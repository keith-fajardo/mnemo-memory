"""Local stdio-only MCP adapter for durable explicit checkpoints."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Annotated, Protocol, cast
from uuid import uuid4

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, SkipValidation

from mnemo_memory.connectors.automatic_memory.checkpoint_evidence import (
    CheckpointFileEvidenceResolver,
)
from mnemo_memory.connectors.automatic_memory.source_observation import (
    CheckpointSourceObserver,
    refresh_registered_project_source,
)
from mnemo_memory.connectors.dbt.code_excerpt import DbtLocalCodeExcerptReader
from mnemo_memory.connectors.dbt.git_state import DbtGitStateObserver
from mnemo_memory.connectors.dbt.manifest import DbtManifestParser
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBindingError,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.connectors.local_embeddings import FastEmbedLocalProvider
from mnemo_memory.packages.application import (
    CheckpointRuntime,
    CheckpointView,
    LocalConfigurationError,
    LocalRuntimeError,
    PersonalSettingsError,
    PersonalSettingsStore,
    build_checkpoint_runtime,
    resolve_local_config,
)
from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    LocalMemoryProjectBindingStore,
)
from mnemo_memory.packages.application.mcp_durable import DurableMcpContextPort
from mnemo_memory.packages.application.mcp_port import McpContextPort
from mnemo_memory.packages.application.unified_context import UnifiedContextService
from mnemo_memory.packages.context_engine import (
    ContextClient,
    UnifiedContextEngine,
    explain_context_packet,
    render_context_packet,
)
from mnemo_memory.packages.domain import ContextPacket, MemoryScope, SourceStateFingerprint
from mnemo_memory.packages.telemetry import (
    AutomaticRouteDiagnosticsMode,
    AutomaticRouteScope,
    CheckpointSaveDiagnosticEvent,
    CheckpointSaveOutcome,
    LocalAutomaticRouteDiagnosticsSettingsStore,
    LocalCheckpointSaveTelemetryStore,
)

SERVER_NAME = "mnemo-local"
SERVER_VERSION = "0.1.0"
_MAX_EXPLAIN_PACKET_BYTES = 131_072
_SOURCE_CONTEXT_KEYS = frozenset(
    {"source_query", "source_impact", "source_changes", "source_overview"}
)


class CheckpointEvidenceLocationInput(BaseModel):
    """Public MCP input shape for an immutable evidence location."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    uri: Annotated[str, Field(min_length=1)]
    start_line: int | None = None
    start_column: int | None = None
    end_line: int | None = None
    end_column: int | None = None


class CheckpointEvidenceReferenceInput(BaseModel):
    """Public MCP input shape for one checkpoint evidence reference."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    evidence_id: Annotated[str, Field(min_length=36, max_length=36)]
    source_id: Annotated[str, Field(min_length=36, max_length=36)]
    source_type: Annotated[str, Field(min_length=1)]
    trust_class: Annotated[str, Field(min_length=1)]
    immutable_source_ref: Annotated[str, Field(min_length=1)]
    content_hash: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    location: CheckpointEvidenceLocationInput
    observed_at: Annotated[str, Field(min_length=1)]
    verification_status: Annotated[str, Field(min_length=1)]


class DbtSelectorInput(BaseModel):
    """Public bounded selector shape; broad inventories default to an aggregate only."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    resource_type: Annotated[str | None, Field(default=None, min_length=1, max_length=256)] = None
    package_name: Annotated[str | None, Field(default=None, min_length=1, max_length=256)] = None
    tag: Annotated[str | None, Field(default=None, min_length=1, max_length=256)] = None
    maximum_nodes: Annotated[
        int,
        Field(
            ge=1,
            le=8,
            description="Maximum node records when include_nodes is true; not an inventory count.",
        ),
    ] = 8
    include_nodes: Annotated[
        bool | None,
        Field(
            default=None,
            description=(
                "Include bounded node records. A broad resource_type-only inventory defaults "
                "to one exact aggregate; package/tag intersections retain node records."
            ),
        ),
    ] = None
    snapshot_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None
    current_content_digest: Annotated[
        str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ] = None
    require_current: bool = False


class SourceOverviewInput(BaseModel):
    """Strict compact source-graph overview input."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    maximum_files: Annotated[int, Field(ge=1, le=32)] = 12
    maximum_modules: Annotated[int, Field(ge=1, le=32)] = 12
    maximum_declarations: Annotated[int, Field(ge=1, le=64)] = 24
    maximum_components: Annotated[int, Field(ge=1, le=32)] = 12
    maximum_relationships: Annotated[int, Field(ge=1, le=32)] = 12
    snapshot_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None
    current_source_digest: Annotated[
        str | None, Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    ] = None
    require_current: bool = False


class TeamKnowledgeMcpPort(Protocol):
    def list_knowledge_sources(self, request: dict[str, object]) -> dict[str, object]: ...

    def approve_knowledge_source(self, request: dict[str, object]) -> dict[str, object]: ...


class SemanticVerificationMcpPort(Protocol):
    def verify_against_memory(self, request: dict[str, object]) -> dict[str, object]: ...


class _McpContextSession(Protocol):
    port: McpContextPort

    def refresh_source(self) -> None: ...

    def close(self) -> None: ...


class _LocalMcpContextSession:
    """One initialized local runtime retained for the lifetime of an MCP process."""

    def __init__(
        self,
        runtime: CheckpointRuntime,
        port: McpContextPort,
        source_refresher: Callable[[], None],
    ) -> None:
        self.port = port
        self._runtime = runtime
        self._source_refresher = source_refresher

    def refresh_source(self) -> None:
        self._source_refresher()

    def close(self) -> None:
        self._runtime.close()


class _DeferredMcpContextPort:
    """Keep durable composition and source parsing out of the MCP handshake."""

    def __init__(self, session_factory: Callable[[], _McpContextSession]) -> None:
        self._session_factory = session_factory
        self._session: _McpContextSession | None = None
        self._source_refreshed = False
        self._closed = False
        self._lock = Lock()

    def get_context(self, request: dict[str, object]) -> dict[str, object]:
        session = self._initialized_session()
        if any(request.get(name) is not None for name in _SOURCE_CONTEXT_KEYS):
            self._refresh_source_once(session)
        return session.port.get_context(request)

    def list_skills(self, request: dict[str, object]) -> dict[str, object]:
        return self._initialized_session().port.list_skills(request)

    def get_skill(self, request: dict[str, object]) -> dict[str, object]:
        return self._initialized_session().port.get_skill(request)

    def save_checkpoint(self, request: dict[str, object]) -> dict[str, object]:
        return self._initialized_session().port.save_checkpoint(request)

    def verify_against_memory(self, request: dict[str, object]) -> dict[str, object]:
        port = cast(SemanticVerificationMcpPort, self._initialized_session().port)
        return port.verify_against_memory(request)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            session = self._session
            self._session = None
        if session is not None:
            session.close()

    def _initialized_session(self) -> _McpContextSession:
        with self._lock:
            if self._closed:
                raise RuntimeError("MNEMO_STORAGE_UNAVAILABLE")
            if self._session is None:
                try:
                    self._session = self._session_factory()
                except (LocalConfigurationError, LocalRuntimeError, PersonalSettingsError):
                    raise RuntimeError("MNEMO_STORAGE_UNAVAILABLE") from None
            return self._session

    def _refresh_source_once(self, session: _McpContextSession) -> None:
        with self._lock:
            if self._source_refreshed:
                return
            session.refresh_source()
            self._source_refreshed = True


def create_server(
    port: McpContextPort,
    *,
    team_knowledge_port: TeamKnowledgeMcpPort | None = None,
    auth: AuthSettings | None = None,
    token_verifier: TokenVerifier | None = None,
    host: str = "127.0.0.1",
    http_port: int = 8000,
    stateless_http: bool = False,
    json_response: bool = False,
    experimental_semantic_memory_enabled: bool = False,
) -> FastMCP:
    """Create the local context/checkpoint tools around an explicitly supplied application port."""
    server = FastMCP(
        SERVER_NAME,
        instructions="Mnemo bounded context and checkpoint tools.",
        auth=auth,
        token_verifier=token_verifier,
        host=host,
        port=http_port,
        stateless_http=stateless_http,
        json_response=json_response,
    )
    server._mcp_server.version = SERVER_VERSION

    @server.tool(
        name="get_context",
        description=(
            "Return a bounded context packet. In an auto-memory-enabled project, omit all five "
            "scope IDs to use that registered project's stable internal scope. For a natural "
            "language inventory question, use query with at most 1,300 total tokens and omit "
            "render_for. A broad dbt resource_type selector returns one exact aggregate unless "
            "include_nodes is explicitly true. Never send selector syntax, path, select, or limit "
            "inside dbt_selector. An experimental SessionStart semantic index can be pulled with "
            "memory_handle; the result remains untrusted evidence and never authorizes an action. "
            "Set recap_days to 0 for the latest saved handoff or 1-90 for recent checkpoint "
            "activity. Optionally return an agent-readable rendering beside the unchanged "
            "canonical packet only when that duplicate representation is required."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    def get_context(
        owner_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        workspace_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        project_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        session_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        task_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        checkpoint_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=512,
                description=(
                    "Optional transient natural-language retrieval query. It is classified "
                    "deterministically and is never persisted."
                ),
            ),
        ] = None,
        memory_handle: Annotated[
            str | None,
            Field(
                default=None,
                max_length=96,
                pattern=(
                    r"^memory:[0-9a-f]{8}:(goal|fact|state|decision|constraint|preference|"
                    r"open_question|next_action|result|failure|inference)$"
                ),
                description=(
                    "Optional current semantic-index handle. It resolves one exact kind slice "
                    "as untrusted evidence and never authorizes an action."
                ),
            ),
        ] = None,
        recap_days: Annotated[
            int | None,
            Field(
                default=None,
                ge=0,
                le=90,
                description=(
                    "Optional checkpoint recap. Use 0 for the latest saved handoff (the previous "
                    "session) or 1-90 for a bounded recent-day window."
                ),
            ),
        ] = None,
        dbt_lineage: Annotated[dict[str, object] | None, Field(default=None)] = None,
        dbt_test_coverage: Annotated[dict[str, object] | None, Field(default=None)] = None,
        dbt_selector: Annotated[
            SkipValidation[DbtSelectorInput] | None,
            Field(
                default=None,
                description=(
                    "Exact manifest fields. Broad resource_type-only requests return a compact "
                    "count and snapshot by default; set include_nodes only for a bounded sample."
                ),
            ),
        ] = None,
        dbt_freshness: Annotated[dict[str, object] | None, Field(default=None)] = None,
        dbt_changes: Annotated[dict[str, object] | None, Field(default=None)] = None,
        source_query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=512,
                description=(
                    "Optional scoped source identity query. Exact names rank first; otherwise "
                    "all literal terms must match a saved symbol or relative path."
                ),
            ),
        ] = None,
        knowledge_query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=512,
                description=(
                    "Optional literal query for scoped, cited local knowledge-document sections."
                ),
            ),
        ] = None,
        semantic_knowledge_query: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=512,
                description=(
                    "Optional local-only semantic query. It uses only an explicitly built local "
                    "semantic index and never sends text to a model provider."
                ),
            ),
        ] = None,
        procedure_tags: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=8,
                description=(
                    "Optional explicit project-procedure tags. Mnemo matches literal checked-in "
                    "Markdown procedure metadata only; it does not infer tags from a prompt."
                ),
            ),
        ] = None,
        skill_tags: Annotated[
            list[str] | None,
            Field(
                default=None,
                max_length=8,
                description=(
                    "Optional exact applicability tags for current checked-in skills. Requires "
                    "skill_client and cannot be combined with skill_agent_name."
                ),
            ),
        ] = None,
        skill_client: Annotated[
            str | None,
            Field(
                default=None,
                pattern="^(codex|claude-code)$",
                description="Concrete client used for skill compatibility checks.",
            ),
        ] = None,
        skill_agent_name: Annotated[
            str | None,
            Field(
                default=None,
                pattern="^[a-z][a-z0-9_-]{0,63}$",
                description=(
                    "Optional exact checked-in agent name whose declared skill tags are used."
                ),
            ),
        ] = None,
        source_impact: Annotated[dict[str, object] | None, Field(default=None)] = None,
        source_changes: Annotated[dict[str, object] | None, Field(default=None)] = None,
        source_overview: Annotated[
            SkipValidation[SourceOverviewInput] | None,
            Field(
                default=None,
                description=(
                    "Return one compact, provenance-bearing projection of the saved source graph: "
                    "exact counts plus bounded components, files, symbols, and relationships."
                ),
            ),
        ] = None,
        include_lifecycle_events: Annotated[
            bool,
            Field(
                default=False,
                description="Include a bounded evidence-bearing checkpoint lifecycle timeline.",
            ),
        ] = False,
        include_approved_events: Annotated[
            bool,
            Field(
                default=False,
                description=(
                    "Include bounded explicit evidence-backed decisions, failures, and "
                    "tool outcomes."
                ),
            ),
        ] = False,
        render_for: Annotated[
            str | None,
            Field(
                default=None,
                pattern="^(codex|claude-code)$",
                description=(
                    "Optionally return the unchanged canonical packet beside a deterministic "
                    "client-labeled rendering for Codex or Claude Code. This duplicates the "
                    "packet, so omit it for normal MCP retrieval."
                ),
            ),
        ] = None,
        active_task_checkpoint_tokens: Annotated[int, Field(ge=0, le=8_000)] = 600,
        total_tokens: Annotated[
            int,
            Field(
                ge=0,
                le=8_000,
                description=(
                    "Hard packet budget. Use at most 1,300 for normal questions; larger values "
                    "are for explicitly requested deep structural analysis."
                ),
            ),
        ] = 5700,
    ) -> dict[str, object]:
        canonical = port.get_context(
            {
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
                "checkpoint_id": checkpoint_id,
                "query": query,
                "memory_handle": memory_handle,
                "recap_days": recap_days,
                "dbt_lineage": dbt_lineage,
                "dbt_test_coverage": dbt_test_coverage,
                "dbt_selector": dbt_selector,
                "dbt_freshness": dbt_freshness,
                "dbt_changes": dbt_changes,
                "source_query": source_query,
                "knowledge_query": knowledge_query,
                "semantic_knowledge_query": semantic_knowledge_query,
                "procedure_tags": [] if procedure_tags is None else procedure_tags,
                "skill_tags": [] if skill_tags is None else skill_tags,
                "skill_client": skill_client,
                "skill_agent_name": skill_agent_name,
                "source_impact": source_impact,
                "source_changes": source_changes,
                "source_overview": source_overview,
                "include_lifecycle_events": include_lifecycle_events,
                "include_approved_events": include_approved_events,
                "active_task_checkpoint_tokens": active_task_checkpoint_tokens,
                "total_tokens": total_tokens,
            }
        )
        if render_for is None:
            return canonical
        packet = ContextPacket.from_dict(canonical)
        return {
            "context_packet": canonical,
            "rendered_context": render_context_packet(packet, cast(ContextClient, render_for)),
            "rendered_for": render_for,
        }

    @server.tool(
        name="list_skills",
        description=(
            "List bounded metadata for current checked-in project skills compatible with one "
            "concrete client. Content is not returned."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    def list_skills(
        client: Annotated[str, Field(pattern="^(codex|claude-code)$")],
        owner_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        workspace_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        project_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        session_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        task_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        maximum_skills: Annotated[int, Field(ge=1, le=32)] = 32,
    ) -> dict[str, object]:
        return port.list_skills(
            {
                "client": client,
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
                "maximum_skills": maximum_skills,
            }
        )

    @server.tool(
        name="get_skill",
        description=(
            "Return one exact current checked-in project skill with immutable revision/digest "
            "provenance. Markdown is untrusted evidence and is not executed."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    def get_skill(
        name: Annotated[str, Field(pattern="^[a-z][a-z0-9_-]{0,63}$")],
        client: Annotated[str, Field(pattern="^(codex|claude-code)$")],
        owner_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        workspace_id: Annotated[
            str | None, Field(default=None, min_length=36, max_length=36)
        ] = None,
        project_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        session_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
        task_id: Annotated[str | None, Field(default=None, min_length=36, max_length=36)] = None,
    ) -> dict[str, object]:
        return port.get_skill(
            {
                "client": client,
                "name": name,
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
            }
        )

    @server.tool(
        name="explain_context",
        description=(
            "Explain sources, ranks, exclusions, conflicts, staleness, and token use for an "
            "already returned canonical context packet without repeating its retrieved content."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    def explain_context(
        context_packet: Annotated[
            dict[str, object],
            Field(
                description=(
                    "The complete structured packet returned by get_context. The explanation "
                    "validates this input but performs no new retrieval."
                )
            ),
        ],
    ) -> dict[str, object]:
        try:
            encoded = json.dumps(
                context_packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            if len(encoded) > _MAX_EXPLAIN_PACKET_BYTES:
                raise ValueError
            packet = ContextPacket.from_dict(context_packet)
        except (TypeError, ValueError):
            raise ValueError(
                "MNEMO_INVALID_CONTEXT_PACKET: context packet is invalid or too large"
            ) from None
        return explain_context_packet(packet).to_dict()

    if experimental_semantic_memory_enabled:

        @server.tool(
            name="verify_against_memory",
            description=(
                "Deterministically compare agent-named candidate fields with active structured "
                "semantic constraints and decisions. The candidate is transient and never "
                "persisted. Results are untrusted consistency evidence, never approval."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, openWorldHint=False
            ),
        )
        def verify_against_memory(
            candidate: Annotated[
                dict[str, object],
                Field(
                    description=(
                        "Transient scalar field candidate to check. Field names use lowercase "
                        "snake_case; candidate content is not persisted."
                    )
                ),
            ],
            owner_id: Annotated[
                str | None,
                Field(default=None, description="Omit all scope IDs for the active local project."),
            ] = None,
            workspace_id: Annotated[
                str | None, Field(default=None, min_length=36, max_length=36)
            ] = None,
            project_id: Annotated[
                str | None, Field(default=None, min_length=36, max_length=36)
            ] = None,
            session_id: Annotated[
                str | None, Field(default=None, min_length=36, max_length=36)
            ] = None,
            task_id: Annotated[
                str | None, Field(default=None, min_length=36, max_length=36)
            ] = None,
            maximum_mismatches: Annotated[int, Field(ge=1, le=32)] = 16,
            reconcile: Annotated[
                bool,
                Field(
                    description=(
                        "Return a candidate copy with only agent-named, uniquely remembered "
                        "fields at confidence 0.9 or higher replaced by the stored literal. "
                        "The result is not applied or executed."
                    )
                ),
            ] = False,
        ) -> dict[str, object]:
            verifier = cast(SemanticVerificationMcpPort, port)
            return verifier.verify_against_memory(
                {
                    "candidate": candidate,
                    "owner_id": owner_id,
                    "workspace_id": workspace_id,
                    "project_id": project_id,
                    "session_id": session_id,
                    "task_id": task_id,
                    "maximum_mismatches": maximum_mismatches,
                    "reconcile": reconcile,
                }
            )

    @server.tool(
        name="save_checkpoint",
        description=(
            "Create, revise, complete, abandon, record a correction lesson, or record one "
            "explicit evidence-backed fact. Omit local scope IDs and caller token estimates; "
            "prefer evidence_files. Mnemo targets a sparse 200-token checkpoint."
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False),
    )
    def save_checkpoint(
        operation: Annotated[
            str,
            Field(
                description=(
                    "Lifecycle operation. record_lesson appends exactly one evidence-backed "
                    "correction to the current active revision without resending the complete "
                    "checkpoint; record_event stores one explicit approved episodic fact."
                ),
            ),
        ],
        owner_id: Annotated[
            str | None,
            Field(default=None, description="Omit all scope IDs for the active local project."),
        ] = None,
        workspace_id: Annotated[
            str | None, Field(default=None, description="Omit with all other local scope IDs.")
        ] = None,
        project_id: Annotated[
            str | None, Field(default=None, description="Omit with all other local scope IDs.")
        ] = None,
        session_id: Annotated[
            str | None, Field(default=None, description="Omit with all other local scope IDs.")
        ] = None,
        task_id: Annotated[
            str | None, Field(default=None, description="Omit with all other local scope IDs.")
        ] = None,
        task_objective: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=4_000,
                description=(
                    "Required checkpoint content for create, revise, complete, and abandon."
                ),
            ),
        ] = None,
        current_state: Annotated[
            str | None,
            Field(
                default=None,
                min_length=1,
                max_length=4_000,
                description=(
                    "Required checkpoint content for create, revise, complete, and abandon."
                ),
            ),
        ] = None,
        evidence_references: Annotated[
            list[SkipValidation[CheckpointEvidenceReferenceInput]] | None,
            Field(
                default=None,
                min_length=1,
                max_length=64,
                description=(
                    "Lower-level alternative to evidence_files. Lesson evidence IDs must refer "
                    "to these references. A location requires uri; omit all four source-span "
                    "coordinates when unknown, or provide all four together."
                ),
            ),
        ] = None,
        evidence_files: Annotated[
            list[str] | None,
            Field(
                default=None,
                min_length=1,
                max_length=16,
                description=(
                    "Preferred local shorthand: project-relative files that Mnemo hashes and "
                    "converts into evidence IDs. Use this or evidence_references, never both."
                ),
            ),
        ] = None,
        token_estimate: Annotated[
            int | None,
            Field(
                default=None,
                description=(
                    "Deprecated compatibility field. Mnemo ignores caller estimates, computes "
                    "the canonical value locally, and targets a compact 200-token checkpoint."
                ),
            ),
        ] = None,
        checkpoint_id: Annotated[
            str | None,
            Field(
                default=None,
                description="Required for every operation except create.",
            ),
        ] = None,
        expected_revision_id: Annotated[
            str | None,
            Field(
                default=None,
                description="Required for revise, complete, abandon, and record_lesson.",
            ),
        ] = None,
        reason: Annotated[str | None, Field(default=None, max_length=4_000)] = None,
        event_kind: Annotated[
            str | None,
            Field(default=None),
        ] = None,
        event_summary: Annotated[
            str | None, Field(default=None, min_length=1, max_length=1_200)
        ] = None,
        source_event_key: Annotated[
            str | None, Field(default=None, min_length=1, max_length=256)
        ] = None,
        completed_work: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        remaining_work: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        decisions: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        failures: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        lessons: Annotated[
            list[dict[str, object]] | None,
            Field(
                default=None,
                max_length=16,
                description=(
                    "Canonical correction lessons. record_lesson requires exactly one; complete "
                    "content revisions accept up to 16. With evidence_files, omit evidence_ids "
                    "and Mnemo fills them from the resolved files."
                ),
            ),
        ] = None,
        blockers: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        relevant_files: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        relevant_artifacts: Annotated[list[str] | None, Field(default=None, max_length=128)] = None,
        verification_performed: Annotated[
            list[str] | None, Field(default=None, max_length=128)
        ] = None,
    ) -> dict[str, object]:
        return port.save_checkpoint(
            {
                "operation": operation,
                "owner_id": owner_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
                "task_objective": task_objective,
                "current_state": current_state,
                # Nested evidence is validated by the canonical durable boundary so malformed
                # values receive Mnemo's payload-free error instead of Pydantic's value echo.
                "evidence_references": evidence_references,
                "evidence_files": evidence_files,
                "token_estimate": token_estimate,
                "checkpoint_id": checkpoint_id,
                "expected_revision_id": expected_revision_id,
                "reason": reason,
                "event_kind": event_kind,
                "event_summary": event_summary,
                "source_event_key": source_event_key,
                "completed_work": completed_work,
                "remaining_work": remaining_work,
                "decisions": decisions,
                "failures": failures,
                "lessons": lessons,
                "blockers": blockers,
                "relevant_files": relevant_files,
                "relevant_artifacts": relevant_artifacts,
                "verification_performed": verification_performed,
            }
        )

    if team_knowledge_port is not None:

        @server.tool(
            name="list_knowledge_sources",
            description=(
                "List bounded content-free ownership, current revision, and approval status for "
                "team knowledge sources in one exact project scope."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=True, destructiveHint=False, openWorldHint=False
            ),
        )
        def list_knowledge_sources(
            owner_id: Annotated[str, Field(min_length=36, max_length=36)],
            workspace_id: Annotated[str, Field(min_length=36, max_length=36)],
            project_id: Annotated[str, Field(min_length=36, max_length=36)],
            visibility: Annotated[str, Field(pattern="^(owner|workspace|project)$")],
            limit: Annotated[int, Field(ge=1, le=100)] = 100,
        ) -> dict[str, object]:
            return team_knowledge_port.list_knowledge_sources(
                {
                    "owner_id": owner_id,
                    "workspace_id": workspace_id,
                    "project_id": project_id,
                    "visibility": visibility,
                    "limit": limit,
                }
            )

        @server.tool(
            name="approve_knowledge_source",
            description=(
                "Approve one exact current team knowledge source. The authenticated caller must "
                "be a project maintainer, workspace administrator, or workspace owner."
            ),
            annotations=ToolAnnotations(
                readOnlyHint=False, destructiveHint=False, openWorldHint=False
            ),
        )
        def approve_knowledge_source(
            owner_id: Annotated[str, Field(min_length=36, max_length=36)],
            workspace_id: Annotated[str, Field(min_length=36, max_length=36)],
            project_id: Annotated[str, Field(min_length=36, max_length=36)],
            visibility: Annotated[str, Field(pattern="^(owner|workspace|project)$")],
            document_id: Annotated[str, Field(min_length=36, max_length=36)],
            expected_revision_id: Annotated[str, Field(min_length=36, max_length=36)],
            source_action_key: Annotated[str, Field(min_length=1, max_length=256)],
        ) -> dict[str, object]:
            return team_knowledge_port.approve_knowledge_source(
                {
                    "owner_id": owner_id,
                    "workspace_id": workspace_id,
                    "project_id": project_id,
                    "visibility": visibility,
                    "document_id": document_id,
                    "expected_revision_id": expected_revision_id,
                    "source_action_key": source_action_key,
                }
            )

    names = ["get_context", "list_skills", "get_skill", "explain_context", "save_checkpoint"]
    if team_knowledge_port is not None:
        names.extend(("list_knowledge_sources", "approve_knowledge_source"))
    for name in names:
        tool = server._tool_manager._tools[name]
        tool.parameters["additionalProperties"] = False
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)
    return server


def _build_local_mcp_context_session(
    data_directory: Path | None,
    project_directory: Path,
) -> _LocalMcpContextSession:
    runtime = build_checkpoint_runtime(
        resolve_local_config(data_directory), dbt_parser=DbtManifestParser()
    )
    try:
        assert runtime.dbt_manifest_service is not None
        assert runtime.source_structure_repository is not None
        assert runtime.knowledge_document_repository is not None
        source_repository = runtime.source_structure_repository
        binding_store = LocalMemoryProjectBindingStore(runtime.config.data_directory)
        try:
            binding = binding_store.get(project_directory)
        except AutomaticMemoryBindingError:
            binding = None
        observer = CheckpointSourceObserver(
            binding_store,
            source_repository,
            runtime.repository,
            lambda: datetime.now(UTC),
        )
        dbt_bindings = LocalDbtProjectBindingStore(runtime.config.data_directory)
        dbt_state_observer = DbtGitStateObserver()

        def current_dbt_source_state(scope: MemoryScope) -> SourceStateFingerprint | None:
            try:
                dbt_binding = dbt_bindings.get_for_scope(scope)
            except DbtProjectBindingError:
                return None
            if dbt_binding is None:
                return None
            return dbt_state_observer.observe(dbt_binding.project_root)

        # Construction is inert: FastEmbed is imported and model weights are requested only when
        # a caller explicitly sends semantic_knowledge_query after local semantic indexing.
        from mnemo_memory.packages.knowledge import LocalSemanticKnowledgeRetriever
        from mnemo_memory.packages.skills_registry import (
            KnowledgeDocumentProcedureRegistry,
            KnowledgeDocumentSkillRegistry,
        )

        semantic_knowledge = LocalSemanticKnowledgeRetriever(
            runtime.knowledge_document_repository,
            FastEmbedLocalProvider(runtime.config.data_directory / "semantic-model-cache"),
        )
        skill_registry = KnowledgeDocumentSkillRegistry(runtime.knowledge_document_repository)
        settings = PersonalSettingsStore(runtime.config.data_directory).load()

        def after_checkpoint_save(view: CheckpointView) -> None:
            # Source observation was already an optional fail-open callback. Keep it isolated so
            # an unavailable projection does not prevent an enabled semantic checkpoint.
            with suppress(Exception):
                observer.observe(view)
            if settings.experimental_semantic_memory_enabled:
                assert runtime.semantic_memory_service is not None
                runtime.semantic_memory_service.save_checkpoint_view(
                    view,
                    retention_days=settings.episodic_retention_days,
                )

        def observe_checkpoint_save(
            scope: MemoryScope,
            operation: str,
            outcome: str,
            error_code: str | None,
            duration_ms: int,
            token_estimate: int | None,
            compacted: bool | None,
        ) -> None:
            diagnostics = LocalAutomaticRouteDiagnosticsSettingsStore(
                runtime.config.data_directory
            ).load()
            if diagnostics.mode is AutomaticRouteDiagnosticsMode.OFF or (
                diagnostics.mode is AutomaticRouteDiagnosticsMode.SUMMARY and outcome == "success"
            ):
                return
            assert scope.workspace_id is not None
            assert scope.project_id is not None
            assert scope.session_id is not None
            assert scope.task_id is not None
            event = CheckpointSaveDiagnosticEvent(
                uuid4(),
                AutomaticRouteScope(
                    str(scope.owner_id),
                    str(scope.workspace_id),
                    str(scope.project_id),
                    str(scope.session_id),
                    str(scope.task_id),
                    scope.visibility.value,
                ),
                datetime.now(UTC),
                operation,
                CheckpointSaveOutcome(outcome),
                duration_ms,
                error_code,
                token_estimate,
                compacted,
            )
            LocalCheckpointSaveTelemetryStore(
                runtime.config.data_directory,
                retention_days=diagnostics.retention_days,
            ).record(event)

        port = DurableMcpContextPort(
            runtime.checkpoint_service,
            UnifiedContextEngine(
                UnifiedContextService(
                    runtime.checkpoint_service,
                    runtime.dbt_manifest_service,
                    source_repository,
                    runtime.repository,
                    runtime.knowledge_document_repository,
                    semantic_knowledge,
                    KnowledgeDocumentProcedureRegistry(runtime.knowledge_document_repository),
                    DbtLocalCodeExcerptReader(dbt_bindings, lambda: datetime.now(UTC)),
                    skill_registry,
                    (
                        runtime.semantic_memory_service
                        if settings.experimental_semantic_memory_enabled
                        else None
                    ),
                ),
                runtime.repository,
            ),
            after_checkpoint_save,
            None if binding is None else binding.checkpoint_scope,
            current_dbt_source_state,
            skill_registry,
            settings.context_budget,
            settings.approved_event_capture_enabled,
            checkpoint_evidence_resolver=(
                None if binding is None else CheckpointFileEvidenceResolver(binding.project_root)
            ),
            checkpoint_save_observer=observe_checkpoint_save,
            semantic_memory=(
                runtime.semantic_memory_service
                if settings.experimental_semantic_memory_enabled
                else None
            ),
        )

        def refresh_source() -> None:
            if binding is not None:
                refresh_registered_project_source(binding, source_repository)

        return _LocalMcpContextSession(runtime, port, refresh_source)
    except Exception:
        runtime.close()
        raise


def main(data_directory: Path | None = None) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")
    try:
        settings = PersonalSettingsStore(resolve_local_config(data_directory).data_directory).load()
        experimental_semantic_memory_enabled = settings.experimental_semantic_memory_enabled
    except (LocalConfigurationError, PersonalSettingsError):
        # Tool discovery remains storage-inert and fail-open. A later tool call still reports the
        # concrete configuration/storage failure through the deferred runtime boundary.
        experimental_semantic_memory_enabled = False
    deferred_port = _DeferredMcpContextPort(
        lambda: _build_local_mcp_context_session(data_directory, Path.cwd())
    )
    try:
        create_server(
            deferred_port,
            experimental_semantic_memory_enabled=experimental_semantic_memory_enabled,
        ).run(transport="stdio")
    finally:
        deferred_port.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    try:
        main(None if args.data_dir is None else Path(args.data_dir))
    except (LocalConfigurationError, LocalRuntimeError, PersonalSettingsError) as error:
        logging.basicConfig(
            level=logging.ERROR, stream=sys.stderr, format="%(levelname)s %(message)s"
        )
        logging.error("MNEMO_STORAGE_UNAVAILABLE: %s", error)
        raise SystemExit(2) from error
