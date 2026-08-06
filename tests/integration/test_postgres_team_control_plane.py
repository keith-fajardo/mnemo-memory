"""Real PostgreSQL contract and RLS parity tests for the optional team adapter."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import cast
from uuid import uuid4

import pg8000.dbapi  # type: ignore[import-untyped]
import pytest

from mnemo_memory.connectors.dbt.manifest import DbtManifestParser, ManifestParseRequest
from mnemo_memory.packages.domain import (
    ApprovedEpisodicEvent,
    ApprovedEpisodicEventGovernance,
    ApprovedEpisodicEventPinAction,
    ApprovedEventGovernanceKind,
    ApprovedEventKind,
    ApprovedEventLifecycleStatus,
    CheckpointAggregate,
    CheckpointContent,
    CheckpointEventKind,
    CheckpointId,
    CheckpointRevision,
    CheckpointRevisionId,
    CheckpointSourceObservation,
    CheckpointStatus,
    CodeEdge,
    CodeEdgeKind,
    CodeFile,
    CodeSnapshot,
    CodeSnapshotId,
    CodeStructureArtifact,
    CodeSymbol,
    CodeSymbolId,
    CodeSymbolKind,
    DbtManifestArtifact,
    DbtNodeId,
    DbtSnapshotId,
    EpisodicCandidateReviewAction,
    EpisodicCandidateReviewDecision,
    EpisodicExportBundle,
    EpisodicExtractionProposal,
    EpisodicMemoryCandidate,
    EpisodicMemoryExpiration,
    EpisodicMemoryGovernanceAction,
    EpisodicMemoryKind,
    EpisodicMemoryRevisionStatus,
    EventId,
    EventOutboxJob,
    EventOutboxTopic,
    EvidenceId,
    EvidenceLocation,
    EvidenceReference,
    EvidenceSourceType,
    KnowledgeDocumentId,
    KnowledgeDocumentRevision,
    KnowledgeDocumentRevisionId,
    KnowledgeDocumentTombstone,
    KnowledgeSectionEmbedding,
    MembershipStatus,
    MemoryScope,
    OwnerId,
    ProjectId,
    ProjectMembership,
    ProjectRole,
    RequestId,
    RetentionPolicyId,
    RetentionSchedule,
    ScopeLevel,
    Sensitivity,
    SessionId,
    SourceId,
    SourceTrustClass,
    TaskActivityActor,
    TaskActivityEvent,
    TaskActivityEventExpiration,
    TaskActivityEventKind,
    TaskId,
    TeamAuditAction,
    TeamAuditEvent,
    TeamProject,
    TeamProjectVisibility,
    TeamWorkspace,
    VerificationStatus,
    Visibility,
    WorkspaceId,
    WorkspaceMembership,
    WorkspaceRole,
    knowledge_section_digest,
)
from mnemo_memory.packages.episodic import (
    EpisodicDeletionService,
    EpisodicExportService,
    EpisodicRetentionService,
    TaskActivityRetentionService,
)
from mnemo_memory.packages.knowledge import KnowledgeDocumentParser, KnowledgeDocumentParseRequest
from mnemo_memory.packages.policy import (
    TeamAuthorizationPolicy,
    TeamAuthorizationRequest,
    TeamOperation,
)
from mnemo_memory.packages.storage import (
    POSTGRES_TEAM_SCHEMA_VERSION,
    CheckpointNotFound,
    EventOutboxLeaseConflict,
    EventOutboxNotFound,
    InvalidCheckpointScope,
    InvalidEpisodicEventScope,
    InvalidEpisodicExportScope,
    InvalidKnowledgeDocumentScope,
    InvalidLifecycleTransition,
    KnowledgeDocumentConflict,
    KnowledgeDocumentNotFound,
    KnowledgeDocumentSecretRejected,
    PostgreSQLApprovedEpisodicEventRepository,
    PostgreSQLCheckpointRepository,
    PostgreSQLConnection,
    PostgreSQLConnectionFactory,
    PostgreSQLEpisodicMemoryRepository,
    PostgreSQLEventOutboxRepository,
    PostgreSQLKnowledgeDocumentRepository,
    PostgreSQLProjectIndexRepository,
    PostgreSQLSourceStructureRepository,
    PostgreSQLTaskActivityEventRepository,
    PostgreSQLTeamControlPlaneRepository,
    PostgreSQLTeamMigrationError,
    PostgreSQLTeamMigrationRunner,
    RevisionConflict,
    TaskActivityEventConflict,
    TaskActivityEventNotFound,
    TaskActivityEventRejected,
    TaskActivityRetentionConflict,
    TaskActivityRetentionNotFound,
    TeamControlPlaneNotFound,
)
from mnemo_memory.packages.storage.contracts import (
    ActiveEpisodicMemoryNotFound,
    ActiveSnapshotConflict,
    ApprovedEpisodicEventConflict,
    ApprovedEpisodicEventNotFound,
    ApprovedEpisodicEventSecretRejected,
    CheckpointSourceObservationConflict,
    CheckpointSourceObservationNotFound,
    EpisodicDeletionConflict,
    EpisodicDeletionNotFound,
    EpisodicExportStorageFailure,
    EpisodicMemoryCandidateConflict,
    EpisodicMemoryCandidateNotFound,
    EpisodicMemoryCandidateRejected,
    EpisodicMemoryExpirationConflict,
    EpisodicMemoryExpirationNotFound,
    EpisodicMemoryGovernanceConflict,
    EpisodicMemoryGovernanceNotFound,
    EpisodicMemoryGovernanceRejected,
    EpisodicMemoryPurgeNotFound,
    EpisodicMemoryReviewConflict,
    EpisodicMemoryReviewNotFound,
    EpisodicMemoryReviewRejected,
    InvalidManifestGraph,
    ManifestNodeNotFound,
    ManifestSnapshotNotFound,
    ProjectIndexStorageFailure,
    SourceIndexStorageFailure,
    SourceSnapshotNotFound,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PostgreSQLHarness:
    host: str
    port: int
    admin_user: str
    database: str
    runtime_role: str

    def connection(
        self, *, database: str | None = None, user: str | None = None
    ) -> PostgreSQLConnection:
        return cast(
            PostgreSQLConnection,
            pg8000.dbapi.connect(
                host=self.host,
                port=self.port,
                database=database or self.database,
                user=user or self.admin_user,
                timeout=5,
            ),
        )

    def admin_factory(self) -> PostgreSQLConnectionFactory:
        return lambda: self.connection()

    def runtime_factory(self) -> PostgreSQLConnectionFactory:
        return lambda: self.connection(user=self.runtime_role)


def _server_settings() -> tuple[str, int, str] | None:
    host = os.environ.get("MNEMO_TEST_POSTGRES_HOST")
    port = os.environ.get("MNEMO_TEST_POSTGRES_PORT")
    user = os.environ.get("MNEMO_TEST_POSTGRES_ADMIN_USER")
    if host is None or port is None or user is None:
        return None
    return host, int(port), user


def _execute_admin(
    host: str,
    port: int,
    user: str,
    sql: str,
    *,
    database: str = "postgres",
) -> None:
    connection = pg8000.dbapi.connect(host=host, port=port, database=database, user=user, timeout=5)
    connection.autocommit = True
    cursor = connection.cursor()
    try:
        cursor.execute(sql)
    finally:
        cursor.close()
        connection.close()


@pytest.fixture(scope="module")
def postgres_harness() -> Iterator[PostgreSQLHarness]:
    settings = _server_settings()
    if settings is None:
        pytest.skip("real PostgreSQL tests require explicit MNEMO_TEST_POSTGRES_* settings")
    host, port, admin_user = settings
    suffix = uuid4().hex[:12]
    database = f"mnemo_test_{suffix}"
    runtime_role = f"mnemo_runtime_{suffix}"
    _execute_admin(
        host,
        port,
        admin_user,
        f'CREATE ROLE "{runtime_role}" LOGIN NOSUPERUSER NOCREATEDB '
        "NOCREATEROLE NOINHERIT NOBYPASSRLS",
    )
    _execute_admin(host, port, admin_user, f'CREATE DATABASE "{database}"')
    harness = PostgreSQLHarness(host, port, admin_user, database, runtime_role)
    runner = PostgreSQLTeamMigrationRunner(harness.admin_factory())
    assert runner.migrate() == POSTGRES_TEAM_SCHEMA_VERSION
    assert runner.migrate() == POSTGRES_TEAM_SCHEMA_VERSION
    runner.provision_runtime_role(runtime_role)
    try:
        yield harness
    finally:
        _execute_admin(
            host,
            port,
            admin_user,
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{database}' AND pid <> pg_backend_pid()",
        )
        _execute_admin(host, port, admin_user, f'DROP DATABASE "{database}"')
        _execute_admin(host, port, admin_user, f'DROP ROLE "{runtime_role}"')


def _audit(
    workspace_id: WorkspaceId,
    actor_id: OwnerId,
    action: TeamAuditAction,
    *,
    project_id: ProjectId | None = None,
    subject_principal_id: OwnerId | None = None,
    request_id: RequestId | None = None,
) -> TeamAuditEvent:
    return TeamAuditEvent.for_mutation(
        request_id=request_id or RequestId.new(),
        workspace_id=workspace_id,
        actor_id=actor_id,
        action=action,
        occurred_at=NOW,
        project_id=project_id,
        subject_principal_id=subject_principal_id,
    )


def _repository(
    harness: PostgreSQLHarness, principal_id: OwnerId, workspace_id: WorkspaceId
) -> PostgreSQLTeamControlPlaneRepository:
    return PostgreSQLTeamControlPlaneRepository(
        harness.runtime_factory(), principal_id=principal_id, workspace_id=workspace_id
    )


def _create_workspace(
    harness: PostgreSQLHarness,
) -> tuple[PostgreSQLTeamControlPlaneRepository, TeamWorkspace, WorkspaceMembership]:
    owner_id, workspace_id = OwnerId.new(), WorkspaceId.new()
    repository = _repository(harness, owner_id, workspace_id)
    workspace = TeamWorkspace(workspace_id, owner_id, NOW)
    owner = WorkspaceMembership(
        workspace_id, owner_id, WorkspaceRole.OWNER, MembershipStatus.ACTIVE
    )
    event = _audit(workspace_id, owner_id, TeamAuditAction.WORKSPACE_CREATED)
    first = repository.create_workspace(workspace, owner, event)
    assert not first.idempotent
    assert repository.create_workspace(workspace, owner, event).idempotent
    return repository, workspace, owner


def _add_workspace_member(
    repository: PostgreSQLTeamControlPlaneRepository,
    workspace: TeamWorkspace,
    principal_id: OwnerId,
    role: WorkspaceRole,
) -> WorkspaceMembership:
    membership = WorkspaceMembership(
        workspace.workspace_id, principal_id, role, MembershipStatus.ACTIVE
    )
    repository.set_workspace_membership(
        membership,
        expected=None,
        audit_event=_audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.WORKSPACE_MEMBERSHIP_CHANGED,
            subject_principal_id=principal_id,
        ),
    )
    return membership


def test_postgres_migration_is_packaged_restrictive_and_rolls_back(
    postgres_harness: PostgreSQLHarness,
) -> None:
    sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0001_team_control_plane.sql")
        .read_text(encoding="utf-8")
    )
    assert sql.count("FORCE ROW LEVEL SECURITY") == 5
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in sql
    assert "BYPASSRLS" not in sql
    assert "GRANT ALL" not in sql
    knowledge_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0002_team_knowledge.sql")
        .read_text(encoding="utf-8")
    )
    assert "CREATE EXTENSION IF NOT EXISTS vector" in knowledge_sql
    assert knowledge_sql.count("FORCE ROW LEVEL SECURITY") == 7
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in knowledge_sql
    checkpoint_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0003_team_checkpoints.sql")
        .read_text(encoding="utf-8")
    )
    assert checkpoint_sql.count("FORCE ROW LEVEL SECURITY") == 3
    assert "DEFERRABLE INITIALLY DEFERRED" in checkpoint_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in checkpoint_sql
    event_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0004_team_task_events_outbox.sql")
        .read_text(encoding="utf-8")
    )
    assert event_sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert "FOR UPDATE SKIP LOCKED" not in event_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in event_sql
    approved_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0005_team_approved_episodic_events.sql")
        .read_text(encoding="utf-8")
    )
    assert approved_sql.count("FORCE ROW LEVEL SECURITY") == 3
    assert "CREATE OR REPLACE FUNCTION mnemo_team.ensure_event_outbox_source()" in approved_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in approved_sql
    candidate_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0006_team_episodic_candidates.sql")
        .read_text(encoding="utf-8")
    )
    assert candidate_sql.count("FORCE ROW LEVEL SECURITY") == 3
    assert "source.retention_json = NEW.retention_json" in candidate_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in candidate_sql
    governance_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0007_team_episodic_governance.sql")
        .read_text(encoding="utf-8")
    )
    assert governance_sql.count("FORCE ROW LEVEL SECURITY") == 1
    assert "UNIQUE (workspace_id, memory_id, expected_revision_id)" in governance_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in governance_sql
    retention_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0008_team_episodic_retention.sql")
        .read_text(encoding="utf-8")
    )
    assert retention_sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert retention_sql.count("episodic payload deletion requires purge") == 1
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in retention_sql
    activity_retention_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0009_team_task_activity_retention.sql")
        .read_text(encoding="utf-8")
    )
    assert activity_retention_sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert activity_retention_sql.count("task activity payload deletion requires purge") == 1
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in activity_retention_sql
    deletion_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0010_team_episodic_deletions.sql")
        .read_text(encoding="utf-8")
    )
    assert deletion_sql.count("FORCE ROW LEVEL SECURITY") == 2
    assert "CREATE OR REPLACE FUNCTION mnemo_team.ensure_episodic_payload_purge()" in deletion_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in deletion_sql
    source_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0011_team_source_structure.sql")
        .read_text(encoding="utf-8")
    )
    assert source_sql.count("FORCE ROW LEVEL SECURITY") == 6
    assert "source snapshot immutable fields cannot change" in source_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in source_sql
    observation_sql = (
        resources.files("mnemo_memory")
        .joinpath(
            "resources",
            "postgres_migrations",
            "0012_team_checkpoint_source_observations.sql",
        )
        .read_text(encoding="utf-8")
    )
    assert observation_sql.count("FORCE ROW LEVEL SECURITY") == 1
    assert "checkpoint source observation revision mismatch" in observation_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in observation_sql
    dbt_manifest_sql = (
        resources.files("mnemo_memory")
        .joinpath("resources", "postgres_migrations", "0013_team_dbt_manifest.sql")
        .read_text(encoding="utf-8")
    )
    assert dbt_manifest_sql.count("FORCE ROW LEVEL SECURITY") == 5
    assert "dbt manifest snapshot immutable fields cannot change" in dbt_manifest_sql
    assert "REVOKE ALL ON ALL TABLES IN SCHEMA mnemo_team FROM PUBLIC" in dbt_manifest_sql

    suffix = uuid4().hex[:12]
    database = f"mnemo_rollback_{suffix}"
    _execute_admin(
        postgres_harness.host,
        postgres_harness.port,
        postgres_harness.admin_user,
        f'CREATE DATABASE "{database}"',
    )

    def factory() -> PostgreSQLConnection:
        return postgres_harness.connection(database=database)

    try:
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=1).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT to_regnamespace('mnemo_team')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
    finally:
        _execute_admin(
            postgres_harness.host,
            postgres_harness.port,
            postgres_harness.admin_user,
            f'DROP DATABASE "{database}"',
        )


def test_team_data_migrations_upgrade_atomically(
    postgres_harness: PostgreSQLHarness,
) -> None:
    suffix = uuid4().hex[:12]
    database = f"mnemo_upgrade_{suffix}"
    _execute_admin(
        postgres_harness.host,
        postgres_harness.port,
        postgres_harness.admin_user,
        f'CREATE DATABASE "{database}"',
    )

    def factory() -> PostgreSQLConnection:
        return postgres_harness.connection(database=database)

    connection = factory()
    cursor = connection.cursor()
    try:
        cursor.execute(
            resources.files("mnemo_memory")
            .joinpath("resources", "postgres_migrations", "0001_team_control_plane.sql")
            .read_text(encoding="utf-8")
        )
        connection.commit()
    finally:
        cursor.close()
        connection.close()
    try:
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=2).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (1,)
            cursor.execute("SELECT to_regclass('mnemo_team.knowledge_document_sources')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0002_team_knowledge.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=3).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (1, 2)
            cursor.execute("SELECT to_regclass('mnemo_team.checkpoint_aggregates')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0003_team_checkpoints.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=4).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (1, 2, 3)
            cursor.execute("SELECT to_regclass('mnemo_team.task_activity_events')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0004_team_task_events_outbox.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=5).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (1, 2, 3, 4)
            cursor.execute("SELECT to_regclass('mnemo_team.approved_episodic_events')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath(
                    "resources", "postgres_migrations", "0005_team_approved_episodic_events.sql"
                )
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=6).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (1, 2, 3, 4, 5)
            cursor.execute("SELECT to_regclass('mnemo_team.episodic_memory_candidates')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0006_team_episodic_candidates.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=7).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (1, 2, 3, 4, 5, 6)
            cursor.execute("SELECT to_regclass('mnemo_team.episodic_memory_governance')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0007_team_episodic_governance.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=8).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (
                1,
                2,
                3,
                4,
                5,
                6,
                7,
            )
            cursor.execute("SELECT to_regclass('mnemo_team.episodic_memory_expirations')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0008_team_episodic_retention.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=9).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
            )
            cursor.execute("SELECT to_regclass('mnemo_team.task_activity_event_expirations')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath(
                    "resources", "postgres_migrations", "0009_team_task_activity_retention.sql"
                )
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=10).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
            )
            cursor.execute("SELECT to_regclass('mnemo_team.episodic_memory_deletions')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0010_team_episodic_deletions.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=11).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == (
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
            )
            cursor.execute("SELECT to_regclass('mnemo_team.source_structure_snapshots')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath("resources", "postgres_migrations", "0011_team_source_structure.sql")
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=12).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == tuple(range(1, 12))
            cursor.execute("SELECT to_regclass('mnemo_team.checkpoint_source_observations')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute(
                resources.files("mnemo_memory")
                .joinpath(
                    "resources",
                    "postgres_migrations",
                    "0012_team_checkpoint_source_observations.sql",
                )
                .read_text(encoding="utf-8")
            )
            connection.commit()
        finally:
            cursor.close()
            connection.close()
        with pytest.raises(PostgreSQLTeamMigrationError, match="injected"):
            PostgreSQLTeamMigrationRunner(factory, fail_migration_at=13).migrate()
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == tuple(range(1, 13))
            cursor.execute("SELECT to_regclass('mnemo_team.dbt_manifest_snapshots')")
            row = cursor.fetchone()
            assert row is not None and row[0] is None
        finally:
            cursor.close()
            connection.close()
        assert PostgreSQLTeamMigrationRunner(factory).migrate() == POSTGRES_TEAM_SCHEMA_VERSION
        connection = factory()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT version FROM mnemo_team.schema_migrations ORDER BY version")
            assert tuple(int(str(row[0])) for row in cursor.fetchall()) == tuple(range(1, 14))
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            assert cursor.fetchone() is not None
        finally:
            cursor.close()
            connection.close()
    finally:
        _execute_admin(
            postgres_harness.host,
            postgres_harness.port,
            postgres_harness.admin_user,
            f'DROP DATABASE "{database}"',
        )


def test_postgres_repository_is_atomic_durable_and_exactly_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    owner_repository, workspace, owner = _create_workspace(postgres_harness)
    admin_id, editor_id, viewer_id = OwnerId.new(), OwnerId.new(), OwnerId.new()
    admin = _add_workspace_member(owner_repository, workspace, admin_id, WorkspaceRole.ADMIN)
    _add_workspace_member(owner_repository, workspace, editor_id, WorkspaceRole.EDITOR)
    _add_workspace_member(owner_repository, workspace, viewer_id, WorkspaceRole.VIEWER)

    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    owner_repository.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    project_member = ProjectMembership(
        workspace.workspace_id,
        project.project_id,
        editor_id,
        ProjectRole.MAINTAINER,
        MembershipStatus.ACTIVE,
    )
    owner_repository.set_project_membership(
        project_member,
        expected=None,
        audit_event=_audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
            project_id=project.project_id,
            subject_principal_id=editor_id,
        ),
    )
    editor_repository = _repository(postgres_harness, editor_id, workspace.workspace_id)
    assert editor_repository.get_project(workspace.workspace_id, project.project_id) == project
    viewer_repository = _repository(postgres_harness, viewer_id, workspace.workspace_id)
    with pytest.raises(TeamControlPlaneNotFound):
        viewer_repository.get_project(workspace.workspace_id, project.project_id)

    transfer = _audit(
        workspace.workspace_id,
        workspace.owner_id,
        TeamAuditAction.WORKSPACE_OWNERSHIP_TRANSFERRED,
        subject_principal_id=admin_id,
    )
    owner_repository.transfer_workspace_ownership(
        workspace.workspace_id,
        expected_owner_id=workspace.owner_id,
        new_owner_id=admin_id,
        audit_event=transfer,
    )
    assert owner_repository.transfer_workspace_ownership(
        workspace.workspace_id,
        expected_owner_id=workspace.owner_id,
        new_owner_id=admin_id,
        audit_event=transfer,
    ).idempotent
    assert owner_repository.get_workspace_membership(
        workspace.workspace_id, owner.principal_id
    ) == replace(owner, role=WorkspaceRole.ADMIN)
    assert owner_repository.get_workspace_membership(workspace.workspace_id, admin_id) == replace(
        admin, role=WorkspaceRole.OWNER
    )
    assert owner_repository.list_audit_events(workspace.workspace_id, limit=3).next_offset == 3

    foreign_repository, foreign_workspace, _ = _create_workspace(postgres_harness)
    with pytest.raises(TeamControlPlaneNotFound):
        owner_repository.get_workspace(foreign_workspace.workspace_id)
    assert foreign_repository.get_workspace(foreign_workspace.workspace_id) == foreign_workspace


def _knowledge_revision(
    scope: MemoryScope,
    path: str,
    content: str,
    *,
    number: int = 1,
    predecessor: KnowledgeDocumentRevisionId | None = None,
    document_id: KnowledgeDocumentId | None = None,
) -> KnowledgeDocumentRevision:
    document = KnowledgeDocumentParser().parse(KnowledgeDocumentParseRequest(scope, path), content)
    if document_id is not None:
        document = replace(document, document_id=document_id)
    return KnowledgeDocumentRevision(
        KnowledgeDocumentRevisionId.new(),
        document,
        number,
        predecessor,
        NOW + timedelta(seconds=number),
    )


def test_postgres_knowledge_is_atomic_scoped_current_only_and_uses_pgvector(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        workspace_id=workspace.workspace_id,
        project_id=project.project_id,
    )
    repository = PostgreSQLKnowledgeDocumentRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    first = _knowledge_revision(
        scope, "docs/decision.md", "# Decision\nUse the deprecated manual process."
    )
    repository.apply_sync(scope, (first,), ())
    second = _knowledge_revision(
        scope,
        "docs/decision.md",
        "# Decision\nUse bounded PostgreSQL context.",
        number=2,
        predecessor=first.revision_id,
        document_id=first.document.document_id,
    )
    repository.apply_sync(scope, (second,), ())

    assert repository.last_sync_at(scope) is not None
    assert repository.get_revision(scope, first.document.document_id, first.revision_id) == first
    assert repository.get_current_revision_by_path(scope, second.document.relative_path) == second
    assert repository.search_current_sections(scope, ("bounded",), 8, 128)[0].revision == second
    assert repository.search_current_sections(scope, ("deprecated",), 8, 128) == ()

    vector = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    embedding = KnowledgeSectionEmbedding(
        scope,
        second.revision_id,
        0,
        "test:pgvector-v1",
        knowledge_section_digest(second.document.sections[0]),
        vector,
    )
    repository.store_section_embeddings(scope, (embedding,))
    assert repository.list_current_section_embeddings(scope, embedding.model_id, 128) == (
        embedding,
    )
    connection = postgres_harness.connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT pg_typeof(embedding)::text FROM mnemo_team.knowledge_section_embeddings"
        )
        row = cursor.fetchone()
        assert row is not None and row[0] == "vector"
    finally:
        connection.rollback()
        cursor.close()
        connection.close()

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLKnowledgeDocumentRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    assert viewer.list_active_documents(scope) == ()
    foreign_scope = replace(scope, project_id=ProjectId.new())
    assert repository.list_active_documents(foreign_scope) == ()
    with pytest.raises(InvalidKnowledgeDocumentScope):
        repository.list_active_documents(
            replace(scope, workspace_id=WorkspaceId.new(), project_id=ProjectId.new())
        )

    tombstone = KnowledgeDocumentTombstone(
        second.document.document_id,
        scope,
        second.document.relative_path,
        second.document.content_digest,
        second.revision_id,
        NOW + timedelta(minutes=1),
    )
    with pytest.raises(KnowledgeDocumentConflict):
        viewer.apply_sync(scope, (), (tombstone,))
    assert repository.get_current_revision(scope, second.document.document_id) == second
    repository.apply_sync(scope, (), (tombstone,))
    with pytest.raises(KnowledgeDocumentNotFound):
        repository.get_current_revision(scope, second.document.document_id)
    connection = postgres_harness.connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT (SELECT count(*) FROM mnemo_team.knowledge_document_revisions), "
            "(SELECT count(*) FROM mnemo_team.knowledge_document_sections), "
            "(SELECT count(*) FROM mnemo_team.knowledge_section_embeddings), "
            "(SELECT count(*) FROM mnemo_team.knowledge_document_tombstones)"
        )
        assert tuple(cursor.fetchone() or ()) == (0, 0, 0, 1)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()

    accepted = _knowledge_revision(scope, "docs/accepted.md", "# Accepted\nSafe content.")
    invalid = _knowledge_revision(
        scope,
        "docs/invalid.md",
        "# Invalid\nWrong predecessor.",
        number=2,
        predecessor=KnowledgeDocumentRevisionId.new(),
    )
    with pytest.raises(KnowledgeDocumentConflict):
        repository.apply_sync(scope, (accepted, invalid), ())
    assert repository.list_active_documents(scope) == ()
    unsafe = _knowledge_revision(
        scope,
        "docs/private.md",
        "# Private\napi_key: 1234567890abcdefghijklmnop",
    )
    with pytest.raises(KnowledgeDocumentSecretRejected):
        repository.apply_sync(scope, (unsafe,), ())
    assert repository.list_active_documents(scope) == ()


def _checkpoint_scope(workspace: TeamWorkspace, project: TeamProject) -> MemoryScope:
    return MemoryScope(
        workspace.owner_id,
        ScopeLevel.TASK,
        Visibility.PROJECT,
        workspace_id=workspace.workspace_id,
        project_id=project.project_id,
        session_id=SessionId.new(),
        task_id=TaskId.new(),
    )


def _checkpoint_evidence(suffix: str) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_AUTHORED,
        f"synthetic://team-checkpoint/{suffix}",
        "sha256:" + suffix[0] * 64,
        EvidenceLocation(f"fixture://team-checkpoint/{suffix}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _checkpoint_content(suffix: str, *, complete: bool = False) -> CheckpointContent:
    return CheckpointContent(
        "exercise PostgreSQL checkpoint parity",
        (f"completed-{suffix}",),
        "complete" if complete else "active",
        () if complete else (f"remaining-{suffix}",),
        (f"decision-{suffix}",),
        (),
        (),
        ("src/example.py",),
        (),
        ("pytest",),
        24,
    )


def _checkpoint_pair(
    scope: MemoryScope, suffix: str
) -> tuple[CheckpointAggregate, CheckpointRevision]:
    checkpoint_id = CheckpointId.new()
    revision = CheckpointRevision(
        CheckpointRevisionId.new(),
        checkpoint_id,
        1,
        None,
        scope,
        _checkpoint_content(suffix),
        CheckpointStatus.ACTIVE,
        (_checkpoint_evidence(suffix),),
        NOW,
    )
    return (
        CheckpointAggregate(
            checkpoint_id,
            scope,
            revision.revision_id,
            1,
            CheckpointStatus.ACTIVE,
            NOW,
            NOW,
        ),
        revision,
    )


def test_postgres_checkpoints_are_atomic_revisioned_and_cross_tenant_safe(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    repository = PostgreSQLCheckpointRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    aggregate, first = _checkpoint_pair(scope, "a")
    repository.create_checkpoint_aggregate(aggregate, first)
    assert repository.get_aggregate(scope, aggregate.checkpoint_id) == aggregate
    assert repository.get_current_revision(scope, aggregate.checkpoint_id) == first
    created_event = repository.list_events(scope).items[0]
    assert created_event.kind is CheckpointEventKind.CREATED
    assert repository.get_event(scope, created_event.event_id) == created_event
    assert repository.append_event(created_event).idempotent

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.checkpoint_revisions', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.checkpoint_lifecycle_events', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, False)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()

    second_evidence = (_checkpoint_evidence("b"),)
    second = repository.append_revision(
        scope,
        aggregate.checkpoint_id,
        first.revision_id,
        _checkpoint_content("b"),
        second_evidence,
        NOW + timedelta(seconds=1),
    )
    assert second.revision_number == 2
    assert second.predecessor_revision_id == first.revision_id
    assert repository.get_revision(scope, aggregate.checkpoint_id, revision_number=1) == first
    assert (
        repository.get_revision(scope, aggregate.checkpoint_id, revision_id=second.revision_id)
        == second
    )

    with pytest.raises(RevisionConflict):
        repository.append_revision(
            scope,
            aggregate.checkpoint_id,
            first.revision_id,
            _checkpoint_content("c"),
            (_checkpoint_evidence("c"),),
            NOW + timedelta(seconds=2),
        )
    assert repository.get_current_revision(scope, aggregate.checkpoint_id) == second
    assert len(repository.list_events(scope).items) == 2

    with pytest.raises(InvalidLifecycleTransition):
        repository.complete_checkpoint(
            scope,
            aggregate.checkpoint_id,
            second.revision_id,
            _checkpoint_content("c"),
            (_checkpoint_evidence("c"),),
            NOW + timedelta(seconds=2),
        )
    completed_content = _checkpoint_content("c", complete=True)
    completed_evidence = (_checkpoint_evidence("c"),)
    completed = repository.complete_checkpoint(
        scope,
        aggregate.checkpoint_id,
        second.revision_id,
        completed_content,
        completed_evidence,
        NOW + timedelta(seconds=3),
    )
    assert completed.status is CheckpointStatus.COMPLETED
    assert (
        repository.complete_checkpoint(
            scope,
            aggregate.checkpoint_id,
            second.revision_id,
            completed_content,
            completed_evidence,
            NOW + timedelta(minutes=1),
        )
        == completed
    )
    with pytest.raises(InvalidLifecycleTransition):
        repository.append_revision(
            scope,
            aggregate.checkpoint_id,
            completed.revision_id,
            _checkpoint_content("d"),
            (_checkpoint_evidence("d"),),
            NOW + timedelta(seconds=4),
        )
    assert repository.select_current_checkpoint(scope) is None
    events = repository.list_events(scope, checkpoint_id=aggregate.checkpoint_id).items
    assert tuple(event.kind for event in events) == (
        CheckpointEventKind.COMPLETED,
        CheckpointEventKind.REVISED,
        CheckpointEventKind.CREATED,
    )
    with pytest.raises(InvalidEpisodicEventScope):
        repository.append_event(replace(created_event, event_id=EventId.new()))

    active_aggregate, active_revision = _checkpoint_pair(scope, "d")
    repository.create_checkpoint_aggregate(active_aggregate, active_revision)
    assert repository.select_current_checkpoint(scope) == active_aggregate

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLCheckpointRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(CheckpointNotFound):
        viewer.get_current_revision(scope, active_aggregate.checkpoint_id)
    hidden_aggregate, hidden_revision = _checkpoint_pair(scope, "e")
    with pytest.raises(RevisionConflict):
        viewer.create_checkpoint_aggregate(hidden_aggregate, hidden_revision)
    assert repository.list_current_checkpoints(scope).items == (active_aggregate,)

    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(CheckpointNotFound):
        repository.get_aggregate(foreign_task, active_aggregate.checkpoint_id)
    with pytest.raises(InvalidCheckpointScope):
        repository.list_current_checkpoints(
            replace(scope, workspace_id=WorkspaceId.new(), project_id=ProjectId.new())
        )
    abandoned_evidence = (_checkpoint_evidence("e"),)
    abandoned_content = _checkpoint_content("e")
    abandoned = repository.abandon_checkpoint(
        scope,
        active_aggregate.checkpoint_id,
        active_revision.revision_id,
        "superseded task",
        abandoned_content,
        abandoned_evidence,
        NOW + timedelta(seconds=5),
    )
    assert abandoned.status is CheckpointStatus.ABANDONED
    assert "superseded task" in abandoned.content.failures
    assert (
        repository.abandon_checkpoint(
            scope,
            active_aggregate.checkpoint_id,
            active_revision.revision_id,
            "superseded task",
            abandoned_content,
            abandoned_evidence,
            NOW + timedelta(minutes=2),
        )
        == abandoned
    )
    assert repository.select_current_checkpoint(scope) is None


def _task_event(
    scope: MemoryScope,
    suffix: str,
    *,
    summary: str | None = None,
    retention: RetentionSchedule | None = None,
) -> TaskActivityEvent:
    return TaskActivityEvent.create(
        scope=scope,
        kind=TaskActivityEventKind.TASK_ACTIVITY,
        actor=TaskActivityActor.USER,
        summary=summary or f"reviewed bounded event {suffix}",
        source_event_key=f"team-task-event-{suffix}",
        sensitivity=Sensitivity.NORMAL,
        retention=retention
        or RetentionSchedule(RetentionPolicyId.new(), True, NOW, NOW, NOW, None, None),
        occurred_at=NOW + timedelta(seconds=10),
        evidence_references=(_checkpoint_evidence(suffix),),
    )


def test_postgres_task_events_and_outbox_are_atomic_leased_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    project_scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        workspace_id=workspace.workspace_id,
        project_id=project.project_id,
    )
    events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    outbox = PostgreSQLEventOutboxRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    event = _task_event(scope, "a")
    assert not events.append_task_activity_event(event).idempotent
    assert events.append_task_activity_event(event).idempotent
    assert events.get_task_activity_event(scope, event.event_id) == event
    assert events.list_task_activity_events(scope).items == (event,)
    with pytest.raises(TaskActivityEventConflict):
        events.append_task_activity_event(replace(event, summary="different canonical payload"))
    with pytest.raises(TaskActivityEventRejected):
        events.append_task_activity_event(
            _task_event(
                scope,
                "b",
                summary="api_key: 1234567890abcdefghijklmnop",
            )
        )
    assert events.list_task_activity_events(scope).items == (event,)

    job = EventOutboxJob.create(
        scope=scope,
        topic=EventOutboxTopic.TASK_ACTIVITY,
        source_event_id=event.event_id,
        event_kind=event.kind.value,
        occurred_at=event.occurred_at,
        created_at=event.occurred_at,
    )
    assert outbox.get_event_job(scope, job.job_id) == job
    assert (
        outbox.get_project_event_job_status(project_scope, now=NOW + timedelta(seconds=11)).pending
        == 1
    )

    claimed = outbox.claim_event_jobs(
        scope,
        worker_id="worker-one",
        now=NOW + timedelta(seconds=11),
        lease_expires_at=NOW + timedelta(minutes=1),
        limit=10,
    )
    assert len(claimed) == 1 and claimed[0].attempt_count == 1
    assert (
        outbox.claim_event_jobs(
            scope,
            worker_id="worker-two",
            now=NOW + timedelta(seconds=12),
            lease_expires_at=NOW + timedelta(minutes=2),
            limit=10,
        )
        == ()
    )
    with pytest.raises(EventOutboxLeaseConflict):
        outbox.complete_event_job(
            scope,
            job.job_id,
            worker_id="worker-two",
            completed_at=NOW + timedelta(seconds=12),
        )
    failed = outbox.retry_event_job(
        scope,
        job.job_id,
        worker_id="worker-one",
        now=NOW + timedelta(seconds=12),
        available_at=NOW + timedelta(seconds=15),
        failure_code="HANDLER_FAILED",
    )
    assert failed.attempt_count == 1 and failed.last_failure_code == "HANDLER_FAILED"
    status = outbox.get_project_event_job_status(project_scope, now=NOW + timedelta(seconds=13))
    assert (status.pending, status.processing, status.failed) == (0, 0, 1)
    assert (
        outbox.requeue_failed_project_event_jobs(
            project_scope, requested_at=NOW + timedelta(seconds=16), limit=100
        )
        == 1
    )
    assert (
        outbox.requeue_failed_project_event_jobs(
            project_scope, requested_at=NOW + timedelta(seconds=16), limit=100
        )
        == 0
    )
    reclaimed = outbox.claim_event_jobs(
        scope,
        worker_id="worker-two",
        now=NOW + timedelta(seconds=16),
        lease_expires_at=NOW + timedelta(minutes=2),
        limit=1,
    )[0]
    assert reclaimed.attempt_count == 2 and reclaimed.last_failure_code is None
    completed = outbox.complete_event_job(
        scope,
        job.job_id,
        worker_id="worker-two",
        completed_at=NOW + timedelta(seconds=17),
    )
    assert completed.completed_at == NOW + timedelta(seconds=17)
    final_status = outbox.get_project_event_job_status(
        project_scope, now=NOW + timedelta(seconds=18)
    )
    assert (final_status.pending, final_status.processing, final_status.failed) == (0, 0, 0)
    restarted = PostgreSQLEventOutboxRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert restarted.get_event_job(scope, job.job_id) == completed

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer_events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    viewer_outbox = PostgreSQLEventOutboxRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(TaskActivityEventNotFound):
        viewer_events.get_task_activity_event(scope, event.event_id)
    with pytest.raises(EventOutboxNotFound):
        viewer_outbox.get_event_job(scope, job.job_id)
    with pytest.raises(TaskActivityEventConflict):
        viewer_events.append_task_activity_event(_task_event(scope, "c"))
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(EventOutboxNotFound):
        outbox.get_event_job(foreign_task, job.job_id)

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.task_activity_events', 'UPDATE'), "
            "has_table_privilege(current_user, 'mnemo_team.event_outbox', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, True)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def _approved_evidence(suffix: str, *, user: bool = False) -> EvidenceReference:
    return EvidenceReference(
        EvidenceId.new(),
        SourceId.new(),
        EvidenceSourceType.USER_CORRECTION if user else EvidenceSourceType.CHECKPOINT,
        SourceTrustClass.USER_CORRECTION if user else SourceTrustClass.USER_AUTHORED,
        f"synthetic://team-approved/{suffix}",
        "sha256:" + "a" * 64,
        EvidenceLocation(f"fixture://team-approved/{suffix}"),
        NOW,
        VerificationStatus.VERIFIED,
    )


def _approved_event(
    scope: MemoryScope,
    suffix: str,
    *,
    summary: str | None = None,
    kind: ApprovedEventKind = ApprovedEventKind.DECISION,
) -> ApprovedEpisodicEvent:
    return ApprovedEpisodicEvent.create(
        scope=scope,
        kind=kind,
        summary=summary or f"retain the verified team fact {suffix}",
        source_event_key=f"team-approved-{suffix}",
        occurred_at=NOW + timedelta(seconds=20),
        evidence_references=(_approved_evidence(suffix),),
    )


def _approved_governance(
    scope: MemoryScope,
    target: ApprovedEpisodicEvent,
    suffix: str,
    *,
    kind: ApprovedEventGovernanceKind,
    replacement: ApprovedEpisodicEvent | None = None,
) -> ApprovedEpisodicEventGovernance:
    return ApprovedEpisodicEventGovernance.create(
        scope=scope,
        kind=kind,
        target_event_id=target.event_id,
        replacement_event_id=None if replacement is None else replacement.event_id,
        reason=f"verified team governance action {suffix}",
        source_action_key=f"team-governance-{suffix}",
        occurred_at=NOW + timedelta(seconds=30),
        evidence_references=(_approved_evidence(f"governance-{suffix}"),),
    )


def test_postgres_approved_events_are_governed_payload_erasing_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    repository = PostgreSQLApprovedEpisodicEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    outbox = PostgreSQLEventOutboxRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )

    original = _approved_event(scope, "a")
    assert not repository.append_approved_event(original).idempotent
    assert repository.append_approved_event(original).idempotent
    assert repository.get_approved_event(scope, original.event_id) == original
    with pytest.raises(ApprovedEpisodicEventConflict):
        repository.append_approved_event(replace(original, summary="conflicting canonical fact"))
    with pytest.raises(ApprovedEpisodicEventSecretRejected):
        repository.append_approved_event(
            _approved_event(scope, "secret", summary="api_key=ABCDEFGHIJKLMNOPQRSTUVWX")
        )

    pin = ApprovedEpisodicEventPinAction.create(
        scope=scope,
        event_id=original.event_id,
        pinned=True,
        source_action_key="team-pin-original",
        occurred_at=NOW + timedelta(seconds=21),
        evidence_references=(_approved_evidence("pin", user=True),),
    )
    assert not repository.set_approved_event_pin(pin).idempotent
    assert repository.set_approved_event_pin(pin).idempotent
    assert repository.get_approved_event_record(scope, original.event_id).pinned

    newer = _approved_event(scope, "b", kind=ApprovedEventKind.FAILURE)
    repository.append_approved_event(newer)
    assert repository.list_approved_events(scope).items == (original, newer)

    replacement = _approved_event(
        scope,
        "corrected",
        summary="retain the corrected verified team fact",
    )
    correction = _approved_governance(
        scope,
        original,
        "correct",
        kind=ApprovedEventGovernanceKind.CORRECTED,
        replacement=replacement,
    )
    corrected = repository.correct_approved_event(replacement, correction)
    assert not corrected.idempotent
    assert corrected.target.status is ApprovedEventLifecycleStatus.CORRECTED
    assert not corrected.target.pinned
    assert corrected.replacement is not None and corrected.replacement.pinned
    assert repository.correct_approved_event(replacement, correction).idempotent
    assert repository.list_approved_events(scope).items == (replacement, newer)

    retraction = _approved_governance(
        scope,
        replacement,
        "retract",
        kind=ApprovedEventGovernanceKind.RETRACTED,
    )
    retracted = repository.retract_approved_event(retraction)
    assert not retracted.idempotent
    assert retracted.target.status is ApprovedEventLifecycleStatus.RETRACTED
    assert retracted.target.event is None and not retracted.target.pinned
    assert repository.retract_approved_event(retraction).idempotent
    with pytest.raises(ApprovedEpisodicEventNotFound):
        repository.get_approved_event(scope, replacement.event_id)
    correction_retry = repository.correct_approved_event(replacement, correction)
    assert correction_retry.idempotent
    assert correction_retry.replacement is not None
    assert correction_retry.replacement.status is ApprovedEventLifecycleStatus.RETRACTED
    assert tuple(item.status for item in repository.list_approved_event_records(scope).items) == (
        ApprovedEventLifecycleStatus.RETRACTED,
        ApprovedEventLifecycleStatus.ACTIVE,
        ApprovedEventLifecycleStatus.CORRECTED,
    )

    for topic, source_id, event_kind, occurred_at in (
        (
            EventOutboxTopic.APPROVED_EPISODIC,
            original.event_id,
            original.kind.value,
            original.occurred_at,
        ),
        (
            EventOutboxTopic.APPROVED_GOVERNANCE,
            correction.action_id,
            correction.kind.value,
            correction.occurred_at,
        ),
        (
            EventOutboxTopic.APPROVED_GOVERNANCE,
            retraction.action_id,
            retraction.kind.value,
            retraction.occurred_at,
        ),
    ):
        job = EventOutboxJob.create(
            scope=scope,
            topic=topic,
            source_event_id=source_id,
            event_kind=event_kind,
            occurred_at=occurred_at,
            created_at=occurred_at,
        )
        assert outbox.get_event_job(scope, job.job_id) == job

    restarted = PostgreSQLApprovedEpisodicEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert (
        restarted.get_approved_event_record(scope, replacement.event_id).status
        is ApprovedEventLifecycleStatus.RETRACTED
    )
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(ApprovedEpisodicEventNotFound):
        repository.get_approved_event_record(foreign_task, original.event_id)

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLApprovedEpisodicEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    assert viewer.list_approved_event_records(scope).items == ()
    with pytest.raises(ApprovedEpisodicEventNotFound):
        viewer.get_approved_event(scope, newer.event_id)
    with pytest.raises(ApprovedEpisodicEventConflict):
        viewer.append_approved_event(_approved_event(scope, "viewer"))

    connection = postgres_harness.admin_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT count(*) FROM mnemo_team.approved_episodic_events "
            "WHERE workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
            (str(workspace.workspace_id), str(replacement.event_id)),
        )
        assert tuple(cursor.fetchone() or ()) == (0,)
        cursor.execute(
            "SELECT count(*) FROM mnemo_team.approved_episodic_event_governance "
            "WHERE workspace_id = CAST(%s AS uuid) AND target_event_id = CAST(%s AS uuid)",
            (str(workspace.workspace_id), str(replacement.event_id)),
        )
        assert tuple(cursor.fetchone() or ()) == (1,)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.approved_episodic_events', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.approved_episodic_event_governance', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.approved_episodic_event_governance', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.approved_episodic_event_pin_actions', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.approved_episodic_event_pin_actions', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, False, False, False, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'contribute', true)",
            (str(workspace.owner_id), str(workspace.workspace_id)),
        )
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute(
                "DELETE FROM mnemo_team.approved_episodic_events WHERE "
                "workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
                (str(workspace.workspace_id), str(newer.event_id)),
            )
    finally:
        connection.rollback()
        cursor.close()
        connection.close()
    assert repository.get_approved_event(scope, newer.event_id) == newer


def _episodic_candidates(
    source: TaskActivityEvent,
    *,
    extractor_version: str = "team-extractor-v1",
    claims: tuple[str, ...] = (
        "retain the verified task decision",
        "remember the bounded task failure",
        "reuse the verified task lesson",
    ),
) -> tuple[EpisodicMemoryCandidate, ...]:
    kinds = (
        EpisodicMemoryKind.DECISION,
        EpisodicMemoryKind.FAILURE,
        EpisodicMemoryKind.LESSON,
    )
    return tuple(
        EpisodicMemoryCandidate.create(
            source_event=source,
            proposal=EpisodicExtractionProposal(
                kinds[index], claim, 0.9 - index * 0.1, Sensitivity.NORMAL
            ),
            proposal_index=index,
            sensitivity=Sensitivity.NORMAL,
            extractor_version=extractor_version,
            provider_id="team-provider",
            model_id="team-model",
            prompt_version="team-prompt-v1",
            created_at=NOW + timedelta(seconds=40),
        )
        for index, claim in enumerate(claims)
    )


def _episodic_review(
    scope: MemoryScope,
    candidate: EpisodicMemoryCandidate,
    suffix: str,
    decision: EpisodicCandidateReviewDecision,
    *,
    reason: str | None = None,
    source_action_key: str | None = None,
) -> EpisodicCandidateReviewAction:
    return EpisodicCandidateReviewAction.create(
        scope=scope,
        candidate_id=candidate.memory_id,
        decision=decision,
        source_action_key=source_action_key or f"team-review-{suffix}",
        reason=reason or f"verified explicit review {suffix}",
        reviewed_at=NOW + timedelta(seconds=50),
        evidence_references=(_approved_evidence(f"review-{suffix}", user=True),),
    )


def test_postgres_episodic_candidates_require_source_and_explicit_review(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    task_events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    source = _task_event(scope, "candidate")
    task_events.append_task_activity_event(source)
    repository = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    candidates = _episodic_candidates(source)
    assert not repository.store_episodic_memory_candidates(candidates).idempotent
    assert repository.store_episodic_memory_candidates(candidates).idempotent
    assert repository.get_episodic_memory_candidate(scope, candidates[0].memory_id) == candidates[0]
    assert repository.list_episodic_memory_candidates(scope).items == tuple(reversed(candidates))
    assert (
        repository.list_episodic_memory_candidates(
            scope, source_event_id=source.event_id, limit=2
        ).next_offset
        == 2
    )

    conflicting = replace(
        candidates[0], memory=replace(candidates[0].memory, claim="changed extracted output")
    )
    with pytest.raises(EpisodicMemoryCandidateConflict):
        repository.store_episodic_memory_candidates((conflicting, *candidates[1:]))
    secret = _episodic_candidates(
        source,
        extractor_version="team-extractor-secret",
        claims=("api_key=ABCDEFGHIJKLMNOPQRSTUVWX",),
    )
    with pytest.raises(EpisodicMemoryCandidateRejected):
        repository.store_episodic_memory_candidates(secret)
    mismatched_retention = RetentionSchedule(
        RetentionPolicyId.new(),
        True,
        NOW,
        NOW,
        NOW,
        None,
        None,
    )
    mismatched_source = replace(source, retention=mismatched_retention)
    mismatched = _episodic_candidates(
        mismatched_source,
        extractor_version="team-extractor-mismatch",
        claims=("safe but authority-mismatched candidate",),
    )
    with pytest.raises(EpisodicMemoryCandidateConflict):
        repository.store_episodic_memory_candidates(mismatched)
    assert len(repository.list_episodic_memory_candidates(scope).items) == 3

    approval = _episodic_review(
        scope, candidates[0], "approve", EpisodicCandidateReviewDecision.APPROVED
    )
    approved = repository.review_episodic_memory_candidate(approval)
    assert not approved.idempotent and approved.active_memory is not None
    assert repository.review_episodic_memory_candidate(approval).idempotent
    assert repository.get_episodic_memory_review(scope, candidates[0].memory_id) == approval
    assert repository.get_active_episodic_memory(scope, candidates[0].memory_id) == (
        approved.active_memory
    )

    rejection = _episodic_review(
        scope, candidates[1], "reject", EpisodicCandidateReviewDecision.REJECTED
    )
    rejected = repository.review_episodic_memory_candidate(rejection)
    assert not rejected.idempotent and rejected.active_memory is None
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        repository.get_active_episodic_memory(scope, candidates[1].memory_id)
    assert repository.list_active_episodic_memories(scope).items == (approved.active_memory,)

    with pytest.raises(EpisodicMemoryReviewConflict):
        repository.review_episodic_memory_candidate(
            _episodic_review(
                scope,
                candidates[0],
                "competing",
                EpisodicCandidateReviewDecision.REJECTED,
            )
        )
    with pytest.raises(EpisodicMemoryReviewConflict):
        repository.review_episodic_memory_candidate(
            _episodic_review(
                scope,
                candidates[2],
                "key-reuse",
                EpisodicCandidateReviewDecision.APPROVED,
                source_action_key=approval.source_action_key,
            )
        )
    with pytest.raises(EpisodicMemoryReviewRejected):
        repository.review_episodic_memory_candidate(
            _episodic_review(
                scope,
                candidates[2],
                "secret",
                EpisodicCandidateReviewDecision.APPROVED,
                reason="access_token=ABCDEFGHIJKLMNOPQRSTUVWX",
            )
        )
    with pytest.raises(EpisodicMemoryReviewNotFound):
        repository.get_episodic_memory_review(scope, candidates[2].memory_id)

    restarted = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert restarted.get_active_episodic_memory(scope, candidates[0].memory_id) == (
        approved.active_memory
    )
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(EpisodicMemoryCandidateNotFound):
        repository.get_episodic_memory_candidate(foreign_task, candidates[0].memory_id)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        repository.get_active_episodic_memory(foreign_task, candidates[0].memory_id)

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    assert viewer.list_episodic_memory_candidates(scope).items == ()
    assert viewer.list_active_episodic_memories(scope).items == ()
    with pytest.raises(EpisodicMemoryCandidateConflict):
        viewer.store_episodic_memory_candidates(
            _episodic_candidates(
                source,
                extractor_version="team-extractor-viewer",
                claims=("viewer cannot stage this candidate",),
            )
        )

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_candidates', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_candidates', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_candidate_reviews', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_candidate_reviews', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.active_episodic_memories', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.active_episodic_memories', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, True, False, True, False, True)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'contribute', true)",
            (str(workspace.owner_id), str(workspace.workspace_id)),
        )
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute(
                "INSERT INTO mnemo_team.active_episodic_memories("
                "workspace_id, project_id, owner_id, visibility, session_id, task_id, "
                "memory_id, approval_action_id, activated_at) VALUES ("
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), %s, "
                "CAST(%s AS uuid), CAST(%s AS uuid), CAST(%s AS uuid), "
                "CAST(%s AS uuid), %s)",
                (
                    str(scope.workspace_id),
                    str(scope.project_id),
                    str(scope.owner_id),
                    scope.visibility.value,
                    str(scope.session_id),
                    str(scope.task_id),
                    str(candidates[1].memory_id),
                    str(rejection.action_id),
                    rejection.reviewed_at,
                ),
            )
    finally:
        connection.rollback()
        cursor.close()
        connection.close()
    assert repository.get_active_episodic_memory(scope, candidates[0].memory_id) == (
        approved.active_memory
    )


def test_postgres_active_episodic_governance_is_optimistic_terminal_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    task_events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    source = _task_event(scope, "7")
    task_events.append_task_activity_event(source)
    repository = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    candidate = _episodic_candidates(
        source,
        extractor_version="team-governance-extractor",
        claims=("retain the initial approved claim",),
    )[0]
    repository.store_episodic_memory_candidates((candidate,))
    approval = _episodic_review(
        scope, candidate, "governance-approve", EpisodicCandidateReviewDecision.APPROVED
    )
    approved = repository.review_episodic_memory_candidate(approval).active_memory
    assert approved is not None
    initial = repository.list_episodic_memory_revisions(scope, candidate.memory_id)
    assert len(initial) == 1 and initial[0].revision_id == approval.action_id
    assert initial[0].status is EpisodicMemoryRevisionStatus.ACTIVE

    correction = EpisodicMemoryGovernanceAction.correct(
        scope=scope,
        memory_id=candidate.memory_id,
        expected_revision_id=initial[0].revision_id,
        source_action_key="team-governance-correct-1",
        reason="the verified user corrected the active claim",
        corrected_claim="retain the corrected approved claim",
        corrected_sensitivity=Sensitivity.NORMAL,
        occurred_at=NOW + timedelta(seconds=60),
        evidence_references=(_approved_evidence("govern-correct-1", user=True),),
    )
    corrected = repository.govern_episodic_memory(correction)
    assert not corrected.idempotent
    assert corrected.current_revision.revision_number == 2
    assert corrected.current_revision.status is EpisodicMemoryRevisionStatus.ACTIVE
    assert corrected.active_memory is not None
    assert corrected.active_memory.memory.claim == "retain the corrected approved claim"
    assert repository.govern_episodic_memory(correction).idempotent
    assert repository.get_episodic_memory_governance(scope, correction.action_id) == correction
    revisions = repository.list_episodic_memory_revisions(scope, candidate.memory_id)
    assert tuple(item.status for item in revisions) == (
        EpisodicMemoryRevisionStatus.SUPERSEDED,
        EpisodicMemoryRevisionStatus.ACTIVE,
    )

    stale = EpisodicMemoryGovernanceAction.correct(
        scope=scope,
        memory_id=candidate.memory_id,
        expected_revision_id=initial[0].revision_id,
        source_action_key="team-governance-stale",
        reason="this stale writer must not fork the chain",
        corrected_claim="stale correction",
        corrected_sensitivity=Sensitivity.NORMAL,
        occurred_at=NOW + timedelta(seconds=61),
        evidence_references=(_approved_evidence("govern-stale", user=True),),
    )
    with pytest.raises(EpisodicMemoryGovernanceConflict):
        repository.govern_episodic_memory(stale)
    secret = EpisodicMemoryGovernanceAction.correct(
        scope=scope,
        memory_id=candidate.memory_id,
        expected_revision_id=correction.action_id,
        source_action_key="team-governance-secret",
        reason="reject secret correction",
        corrected_claim="access_token=ABCDEFGHIJKLMNOPQRSTUVWX",
        corrected_sensitivity=Sensitivity.NORMAL,
        occurred_at=NOW + timedelta(seconds=62),
        evidence_references=(_approved_evidence("govern-secret", user=True),),
    )
    with pytest.raises(EpisodicMemoryGovernanceRejected):
        repository.govern_episodic_memory(secret)

    second = EpisodicMemoryGovernanceAction.correct(
        scope=scope,
        memory_id=candidate.memory_id,
        expected_revision_id=correction.action_id,
        source_action_key="team-governance-correct-2",
        reason="the second verified correction extends the chain",
        corrected_claim="retain the final corrected claim",
        corrected_sensitivity=Sensitivity.NORMAL,
        occurred_at=NOW + timedelta(seconds=63),
        evidence_references=(_approved_evidence("govern-correct-2", user=True),),
    )
    second_result = repository.govern_episodic_memory(second)
    assert second_result.current_revision.revision_number == 3
    conflicting_identity = replace(second, corrected_claim="changed retry payload")
    with pytest.raises(EpisodicMemoryGovernanceConflict):
        repository.govern_episodic_memory(conflicting_identity)

    retraction = EpisodicMemoryGovernanceAction.retract(
        scope=scope,
        memory_id=candidate.memory_id,
        expected_revision_id=second.action_id,
        source_action_key="team-governance-retract",
        reason="the user withdrew the active episodic memory",
        occurred_at=NOW + timedelta(seconds=64),
        evidence_references=(_approved_evidence("govern-retract", user=True),),
    )
    retracted = repository.govern_episodic_memory(retraction)
    assert not retracted.idempotent and retracted.active_memory is None
    assert retracted.current_revision.status is EpisodicMemoryRevisionStatus.RETRACTED
    assert retracted.current_revision.claim is None
    assert repository.govern_episodic_memory(retraction).idempotent
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        repository.get_active_episodic_memory(scope, candidate.memory_id)
    assert repository.list_active_episodic_memories(scope).items == ()
    with pytest.raises(EpisodicMemoryGovernanceConflict):
        repository.govern_episodic_memory(
            EpisodicMemoryGovernanceAction.correct(
                scope=scope,
                memory_id=candidate.memory_id,
                expected_revision_id=retraction.action_id,
                source_action_key="team-governance-after-retraction",
                reason="terminal memory cannot be corrected",
                corrected_claim="must not reactivate",
                corrected_sensitivity=Sensitivity.NORMAL,
                occurred_at=NOW + timedelta(seconds=65),
                evidence_references=(_approved_evidence("govern-terminal", user=True),),
            )
        )

    restarted = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    restarted_revisions = restarted.list_episodic_memory_revisions(scope, candidate.memory_id)
    assert len(restarted_revisions) == 4
    assert restarted_revisions[-1] == retracted.current_revision
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        repository.list_episodic_memory_revisions(foreign_task, candidate.memory_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        repository.get_episodic_memory_governance(foreign_task, correction.action_id)

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        viewer.list_episodic_memory_revisions(scope, candidate.memory_id)

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_governance', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_governance', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, True)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_postgres_episodic_retention_hides_then_purges_payloads_and_is_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    scheduled = NOW + timedelta(minutes=2)
    source = _task_event(
        scope,
        "8",
        retention=RetentionSchedule(RetentionPolicyId.new(), False, NOW, NOW, NOW, None, scheduled),
    )
    events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    events.append_task_activity_event(source)
    repository = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    candidates = _episodic_candidates(
        source,
        extractor_version="team-retention-extractor",
        claims=("expire the approved claim", "expire the inactive claim"),
    )
    repository.store_episodic_memory_candidates(candidates)
    approval = _episodic_review(
        scope, candidates[0], "retention-approve", EpisodicCandidateReviewDecision.APPROVED
    )
    assert repository.review_episodic_memory_candidate(approval).active_memory is not None
    correction = EpisodicMemoryGovernanceAction.correct(
        scope=scope,
        memory_id=candidates[0].memory_id,
        expected_revision_id=approval.action_id,
        source_action_key="team-retention-correct",
        reason="retain one governed payload until its canonical schedule expires",
        corrected_claim="expire the corrected approved claim",
        corrected_sensitivity=Sensitivity.NORMAL,
        occurred_at=NOW + timedelta(minutes=1),
        evidence_references=(_approved_evidence("retention-correct", user=True),),
    )
    repository.govern_episodic_memory(correction)

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(workspace.owner_id), str(workspace.workspace_id), TeamOperation.CONTRIBUTE.value),
        )
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute(
                "DELETE FROM mnemo_team.episodic_memory_candidates "
                "WHERE workspace_id = CAST(%s AS uuid) AND memory_id = CAST(%s AS uuid)",
                (str(workspace.workspace_id), str(candidates[1].memory_id)),
            )
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

    service = EpisodicRetentionService(repository)
    assert service.expire_due(scope, as_of=scheduled - timedelta(seconds=1)).expirations == ()
    targets = repository.list_due_episodic_memory_retention(scope, as_of=scheduled)
    expirations = tuple(EpisodicMemoryExpiration.create(target, scheduled) for target in targets)
    assert len(expirations) == 2
    with pytest.raises(EpisodicMemoryExpirationConflict):
        repository.apply_episodic_memory_expirations(
            (expirations[0], replace(expirations[1], source_event_id=EventId.new()))
        )
    with pytest.raises(EpisodicMemoryExpirationNotFound):
        repository.get_episodic_memory_expiration(scope, candidates[0].memory_id)

    expired = service.expire_due(scope, as_of=scheduled)
    assert expired.expirations == expirations and not expired.idempotent
    assert repository.apply_episodic_memory_expirations(expirations).idempotent
    assert repository.list_due_episodic_memory_retention(scope, as_of=scheduled) == ()
    for candidate in candidates:
        assert repository.get_episodic_memory_expiration(scope, candidate.memory_id) in expirations
        with pytest.raises(EpisodicMemoryCandidateNotFound):
            repository.get_episodic_memory_candidate(scope, candidate.memory_id)
    with pytest.raises(EpisodicMemoryReviewNotFound):
        repository.get_episodic_memory_review(scope, candidates[0].memory_id)
    with pytest.raises(ActiveEpisodicMemoryNotFound):
        repository.get_active_episodic_memory(scope, candidates[0].memory_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        repository.get_episodic_memory_governance(scope, correction.action_id)
    with pytest.raises(EpisodicMemoryGovernanceNotFound):
        repository.list_episodic_memory_revisions(scope, candidates[0].memory_id)
    assert repository.list_episodic_memory_candidates(scope).items == ()
    assert repository.list_active_episodic_memories(scope).items == ()
    assert events.get_task_activity_event(scope, source.event_id) == source

    restarted = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    expected_first = next(item for item in expirations if item.memory_id == candidates[0].memory_id)
    assert (
        restarted.get_episodic_memory_expiration(scope, candidates[0].memory_id) == expected_first
    )
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(EpisodicMemoryExpirationNotFound):
        restarted.get_episodic_memory_expiration(foreign_task, candidates[0].memory_id)
    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(EpisodicMemoryExpirationNotFound):
        viewer.get_episodic_memory_expiration(scope, candidates[0].memory_id)

    purged = service.purge_expired(scope, purged_at=scheduled + timedelta(minutes=1))
    assert len(purged.purges) == 2 and not purged.idempotent
    assert repository.list_unpurged_episodic_memory_expirations(scope) == ()
    assert repository.apply_episodic_memory_purges(purged.purges).idempotent
    with pytest.raises(EpisodicMemoryPurgeNotFound):
        repository.get_episodic_memory_purge(foreign_task, candidates[0].memory_id)
    with pytest.raises(EpisodicMemoryCandidateConflict):
        repository.store_episodic_memory_candidates(candidates)

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_expirations', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_expirations', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_purges', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_purges', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, False, False, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(workspace.owner_id), str(workspace.workspace_id), TeamOperation.READ.value),
        )
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM mnemo_team.episodic_memory_candidates), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_candidate_reviews), "
            "(SELECT COUNT(*) FROM mnemo_team.active_episodic_memories), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_governance), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_expirations), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_purges)"
        )
        assert tuple(int(str(value)) for value in (cursor.fetchone() or ())) == (0, 0, 0, 0, 2, 2)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_postgres_task_activity_retention_waits_for_dependents_then_purges_source(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    scheduled = NOW + timedelta(minutes=3)
    retention = RetentionSchedule(RetentionPolicyId.new(), False, NOW, NOW, NOW, None, scheduled)
    events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    sources = (
        _task_event(scope, "91", retention=retention),
        _task_event(scope, "92", retention=retention),
    )
    for source in sources:
        events.append_task_activity_event(source)
    memories = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    candidate = _episodic_candidates(
        sources[0],
        extractor_version="team-source-retention-extractor",
        claims=("purge this candidate before its source",),
    )[0]
    memories.store_episodic_memory_candidates((candidate,))

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(workspace.owner_id), str(workspace.workspace_id), TeamOperation.CONTRIBUTE.value),
        )
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute(
                "DELETE FROM mnemo_team.task_activity_events WHERE "
                "workspace_id = CAST(%s AS uuid) AND event_id = CAST(%s AS uuid)",
                (str(workspace.workspace_id), str(sources[1].event_id)),
            )
        connection.rollback()
    finally:
        cursor.close()
        connection.close()

    service = TaskActivityRetentionService(events)
    assert service.expire_due(scope, as_of=scheduled - timedelta(seconds=1)).expirations == ()
    targets = events.list_due_task_activity_retention(scope, as_of=scheduled)
    expirations = tuple(TaskActivityEventExpiration.create(target, scheduled) for target in targets)
    assert len(expirations) == 2
    wrong_policy = RetentionPolicyId.new()
    conflicting = TaskActivityEventExpiration(
        TaskActivityEventExpiration.identity(
            expirations[1].event_id, wrong_policy, expirations[1].scheduled_expires_at
        ),
        expirations[1].event_id,
        expirations[1].scope,
        wrong_policy,
        expirations[1].scheduled_expires_at,
        expirations[1].expired_at,
    )
    with pytest.raises(TaskActivityRetentionConflict):
        events.apply_task_activity_expirations((expirations[0], conflicting))
    for source in sources:
        with pytest.raises(TaskActivityRetentionNotFound):
            events.get_task_activity_expiration(scope, source.event_id)

    expired = service.expire_due(scope, as_of=scheduled)
    assert expired.expirations == expirations and not expired.idempotent
    assert events.apply_task_activity_expirations(expirations).idempotent
    assert events.list_task_activity_events(scope).items == ()
    for source in sources:
        with pytest.raises(TaskActivityEventNotFound):
            events.get_task_activity_event(scope, source.event_id)

    restarted = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert restarted.get_task_activity_expiration(scope, sources[0].event_id) in expirations
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(TaskActivityRetentionNotFound):
        restarted.get_task_activity_expiration(foreign_task, sources[0].event_id)
    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(TaskActivityRetentionNotFound):
        viewer.get_task_activity_expiration(scope, sources[0].event_id)

    with pytest.raises(TaskActivityRetentionConflict):
        service.purge_expired(scope, purged_at=scheduled + timedelta(minutes=1))
    for source in sources:
        with pytest.raises(TaskActivityRetentionNotFound):
            events.get_task_activity_purge(scope, source.event_id)

    memory_service = EpisodicRetentionService(memories)
    memory_service.expire_due(scope, as_of=scheduled)
    memory_service.purge_expired(scope, purged_at=scheduled + timedelta(seconds=30))
    purged = service.purge_expired(scope, purged_at=scheduled + timedelta(minutes=1))
    assert len(purged.purges) == 2 and not purged.idempotent
    assert events.apply_task_activity_purges(purged.purges).idempotent
    assert events.list_unpurged_task_activity_expirations(scope) == ()
    with pytest.raises(TaskActivityRetentionNotFound):
        events.get_task_activity_purge(foreign_task, sources[0].event_id)
    for source in sources:
        with pytest.raises(TaskActivityEventConflict):
            events.append_task_activity_event(source)

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.task_activity_event_expirations', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.task_activity_event_expirations', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.task_activity_event_purges', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.task_activity_event_purges', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, False, False, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(workspace.owner_id), str(workspace.workspace_id), TeamOperation.READ.value),
        )
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM mnemo_team.task_activity_events), "
            "(SELECT COUNT(*) FROM mnemo_team.event_outbox WHERE topic = 'task_activity'), "
            "(SELECT COUNT(*) FROM mnemo_team.task_activity_event_expirations), "
            "(SELECT COUNT(*) FROM mnemo_team.task_activity_event_purges), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_expirations), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_purges)"
        )
        assert tuple(int(str(value)) for value in (cursor.fetchone() or ())) == (0, 0, 2, 2, 1, 1)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_postgres_explicit_episodic_deletion_erases_source_dependents_and_is_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    source = _task_event(scope, "10")
    events.append_task_activity_event(source)
    repository = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    candidates = _episodic_candidates(
        source,
        extractor_version="team-deletion-extractor",
        claims=("delete this memory first", "delete this memory with its source"),
    )
    repository.store_episodic_memory_candidates(candidates)
    service = EpisodicDeletionService(repository)
    first = service.delete_memory(
        scope=scope,
        memory_id=candidates[0].memory_id,
        source_event_id=source.event_id,
        source_action_key="team-delete-memory",
        deleted_at=NOW + timedelta(minutes=1),
    )
    assert not first.idempotent
    assert repository.get_episodic_memory_deletion(scope, candidates[0].memory_id) == first.deletion
    assert service.delete_memory(
        scope=scope,
        memory_id=candidates[0].memory_id,
        source_event_id=source.event_id,
        source_action_key="team-delete-memory",
        deleted_at=NOW + timedelta(minutes=1),
    ).idempotent
    with pytest.raises(EpisodicDeletionConflict):
        service.delete_memory(
            scope=scope,
            memory_id=candidates[0].memory_id,
            source_event_id=source.event_id,
            source_action_key="changed-delete-memory",
            deleted_at=NOW + timedelta(minutes=1),
        )
    with pytest.raises(EpisodicMemoryCandidateNotFound):
        repository.get_episodic_memory_candidate(scope, candidates[0].memory_id)

    result = service.delete_task_event(
        scope=scope,
        event_id=source.event_id,
        source_action_key="team-delete-source",
        deleted_at=NOW + timedelta(minutes=2),
    )
    assert not result.idempotent and len(result.dependent_deletions) == 2
    assert first.deletion in result.dependent_deletions
    assert repository.get_task_activity_deletion(scope, source.event_id) == result.deletion
    replay = service.delete_task_event(
        scope=scope,
        event_id=source.event_id,
        source_action_key="team-delete-source",
        deleted_at=NOW + timedelta(minutes=2),
    )
    assert replay.idempotent and replay.dependent_deletions == result.dependent_deletions
    with pytest.raises(TaskActivityEventNotFound):
        events.get_task_activity_event(scope, source.event_id)
    with pytest.raises(TaskActivityEventConflict):
        events.append_task_activity_event(source)
    with pytest.raises(EpisodicMemoryCandidateConflict):
        repository.store_episodic_memory_candidates(candidates)

    restarted = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert restarted.get_task_activity_deletion(scope, source.event_id) == result.deletion
    foreign_task = replace(scope, task_id=TaskId.new())
    with pytest.raises(EpisodicDeletionNotFound):
        restarted.get_task_activity_deletion(foreign_task, source.event_id)
    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(EpisodicDeletionNotFound):
        viewer.get_task_activity_deletion(scope, source.event_id)

    retained_at = NOW + timedelta(minutes=3)
    retained_source = _task_event(
        scope,
        "11",
        retention=RetentionSchedule(
            RetentionPolicyId.new(), False, NOW, NOW, NOW, None, retained_at
        ),
    )
    events.append_task_activity_event(retained_source)
    retained_candidate = _episodic_candidates(
        retained_source,
        extractor_version="team-deletion-after-purge",
        claims=("retain tombstones through explicit deletion",),
    )[0]
    repository.store_episodic_memory_candidates((retained_candidate,))
    EpisodicRetentionService(repository).expire_due(scope, as_of=retained_at)
    EpisodicRetentionService(repository).purge_expired(
        scope, purged_at=retained_at + timedelta(seconds=30)
    )
    TaskActivityRetentionService(events).expire_due(scope, as_of=retained_at)
    TaskActivityRetentionService(events).purge_expired(
        scope, purged_at=retained_at + timedelta(minutes=1)
    )
    retained_deletion = service.delete_task_event(
        scope=scope,
        event_id=retained_source.event_id,
        source_action_key="team-delete-retained-source",
        deleted_at=retained_at + timedelta(minutes=2),
    )
    assert len(retained_deletion.dependent_deletions) == 1
    assert (
        repository.get_episodic_memory_expiration(scope, retained_candidate.memory_id).memory_id
        == retained_candidate.memory_id
    )
    assert (
        events.get_task_activity_expiration(scope, retained_source.event_id).event_id
        == retained_source.event_id
    )

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.task_activity_event_deletions', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.task_activity_event_deletions', 'DELETE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_deletions', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.episodic_memory_deletions', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, False, False, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(workspace.owner_id), str(workspace.workspace_id), TeamOperation.READ.value),
        )
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM mnemo_team.task_activity_events), "
            "(SELECT COUNT(*) FROM mnemo_team.event_outbox WHERE topic = 'task_activity'), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_candidates), "
            "(SELECT COUNT(*) FROM mnemo_team.task_activity_event_deletions), "
            "(SELECT COUNT(*) FROM mnemo_team.episodic_memory_deletions)"
        )
        assert tuple(int(str(value)) for value in (cursor.fetchone() or ())) == (0, 0, 0, 2, 3)
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_postgres_episodic_export_is_complete_stable_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = _checkpoint_scope(workspace, project)
    events = PostgreSQLTaskActivityEventRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    repository = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )

    live_source = _task_event(scope, "export-live")
    events.append_task_activity_event(live_source)
    live_candidates = _episodic_candidates(
        live_source,
        extractor_version="team-export-live",
        claims=("export the approved memory", "export the rejected candidate"),
    )
    repository.store_episodic_memory_candidates(live_candidates)
    approval = _episodic_review(
        scope,
        live_candidates[0],
        "export-approve",
        EpisodicCandidateReviewDecision.APPROVED,
    )
    repository.review_episodic_memory_candidate(approval)
    repository.review_episodic_memory_candidate(
        _episodic_review(
            scope,
            live_candidates[1],
            "export-reject",
            EpisodicCandidateReviewDecision.REJECTED,
        )
    )
    correction = EpisodicMemoryGovernanceAction.correct(
        scope=scope,
        memory_id=live_candidates[0].memory_id,
        expected_revision_id=approval.action_id,
        source_action_key="team-export-correction",
        reason="the verified export claim needs exact corrected wording",
        corrected_claim="export the corrected approved memory",
        corrected_sensitivity=Sensitivity.NORMAL,
        occurred_at=NOW + timedelta(minutes=1),
        evidence_references=(_approved_evidence("export-correction", user=True),),
    )
    repository.govern_episodic_memory(correction)

    expires_at = NOW + timedelta(minutes=2)
    retained_source = _task_event(
        scope,
        "export-retained",
        retention=RetentionSchedule(
            RetentionPolicyId.new(), False, NOW, NOW, NOW, None, expires_at
        ),
    )
    events.append_task_activity_event(retained_source)
    retained_candidate = _episodic_candidates(
        retained_source,
        extractor_version="team-export-retained",
        claims=("export retained lifecycle state",),
    )[0]
    repository.store_episodic_memory_candidates((retained_candidate,))
    EpisodicRetentionService(repository).expire_due(scope, as_of=expires_at)
    EpisodicRetentionService(repository).purge_expired(
        scope, purged_at=expires_at + timedelta(seconds=30)
    )
    TaskActivityRetentionService(events).expire_due(scope, as_of=expires_at)
    TaskActivityRetentionService(events).purge_expired(
        scope, purged_at=expires_at + timedelta(minutes=1)
    )

    deleted_source = _task_event(scope, "export-deleted-source")
    events.append_task_activity_event(deleted_source)
    source_candidate = _episodic_candidates(
        deleted_source,
        extractor_version="team-export-deleted-source",
        claims=("export the source deletion tombstone",),
    )[0]
    repository.store_episodic_memory_candidates((source_candidate,))
    EpisodicDeletionService(repository).delete_task_event(
        scope=scope,
        event_id=deleted_source.event_id,
        source_action_key="team-export-delete-source",
        deleted_at=NOW + timedelta(minutes=4),
    )

    individual_source = _task_event(scope, "export-deleted-memory")
    events.append_task_activity_event(individual_source)
    individual_candidate = _episodic_candidates(
        individual_source,
        extractor_version="team-export-deleted-memory",
        claims=("export the individual deletion tombstone",),
    )[0]
    repository.store_episodic_memory_candidates((individual_candidate,))
    EpisodicDeletionService(repository).delete_memory(
        scope=scope,
        memory_id=individual_candidate.memory_id,
        source_event_id=individual_source.event_id,
        source_action_key="team-export-delete-memory",
        deleted_at=NOW + timedelta(minutes=5),
    )

    exported_at = NOW + timedelta(minutes=6)
    service = EpisodicExportService(repository)
    bundle = service.export(scope, exported_at=exported_at)
    assert EpisodicExportBundle.from_json(bundle.canonical_json()) == bundle
    assert service.export(scope, exported_at=exported_at).canonical_json() == (
        bundle.canonical_json()
    )
    assert (
        service.export(scope, exported_at=exported_at + timedelta(seconds=1)).content_digest
        != bundle.content_digest
    )
    assert tuple(item.event_id for item in bundle.task_events) == tuple(
        sorted((live_source.event_id, individual_source.event_id), key=str)
    )
    assert tuple(item.memory_id for item in bundle.candidates) == tuple(
        sorted((item.memory_id for item in live_candidates), key=str)
    )
    assert len(bundle.reviews) == 2
    assert bundle.governance_actions == (correction,)
    assert tuple(item.revision_number for item in bundle.revisions) == (1, 2)
    assert len(bundle.memory_expirations) == len(bundle.memory_purges) == 1
    assert bundle.memory_expirations[0].memory_id == retained_candidate.memory_id
    assert len(bundle.task_expirations) == len(bundle.task_purges) == 1
    assert bundle.task_expirations[0].event_id == retained_source.event_id
    assert {item.memory_id for item in bundle.memory_deletions} == {
        source_candidate.memory_id,
        individual_candidate.memory_id,
    }
    assert len(bundle.task_deletions) == 1
    assert bundle.task_deletions[0].event_id == deleted_source.event_id

    restarted = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert EpisodicExportService(restarted).export(scope, exported_at=exported_at) == bundle
    foreign_task_bundle = EpisodicExportService(restarted).export(
        replace(scope, task_id=TaskId.new()), exported_at=exported_at
    )
    assert not any(
        (
            foreign_task_bundle.task_events,
            foreign_task_bundle.candidates,
            foreign_task_bundle.memory_expirations,
            foreign_task_bundle.memory_deletions,
            foreign_task_bundle.task_deletions,
        )
    )

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLEpisodicMemoryRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    viewer_bundle = EpisodicExportService(viewer).export(scope, exported_at=exported_at)
    assert not any(
        (
            viewer_bundle.task_events,
            viewer_bundle.candidates,
            viewer_bundle.memory_expirations,
            viewer_bundle.memory_deletions,
            viewer_bundle.task_deletions,
        )
    )
    project_scope = MemoryScope(
        scope.owner_id,
        ScopeLevel.PROJECT,
        scope.visibility,
        scope.workspace_id,
        scope.project_id,
    )
    with pytest.raises(InvalidEpisodicExportScope):
        service.export(project_scope, exported_at=exported_at)

    def unavailable_connection() -> PostgreSQLConnection:
        raise RuntimeError("database unavailable")

    unavailable = PostgreSQLEpisodicMemoryRepository(
        unavailable_connection,
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(EpisodicExportStorageFailure):
        EpisodicExportService(unavailable).export(scope, exported_at=exported_at)


def _source_structure_artifact(scope: MemoryScope, seed: int) -> CodeStructureArtifact:
    snapshot_id = CodeSnapshotId.new()
    module_id = CodeSymbolId.new()
    function_id = CodeSymbolId.new()
    files = (CodeFile(snapshot_id, f"src/module_{seed}.py", "sha256:" + f"{seed:x}" * 64),)
    symbols = (
        CodeSymbol(
            snapshot_id,
            module_id,
            f"src/module_{seed}.py",
            f"src.module_{seed}",
            CodeSymbolKind.MODULE,
            1,
        ),
        CodeSymbol(
            snapshot_id,
            function_id,
            f"src/module_{seed}.py",
            f"src.module_{seed}.reconcile",
            CodeSymbolKind.FUNCTION,
            3,
        ),
    )
    edges = (
        CodeEdge(
            snapshot_id,
            module_id,
            f"src.module_{seed}.reconcile",
            CodeEdgeKind.DEFINES,
            function_id,
        ),
    )
    return CodeStructureArtifact(
        CodeSnapshot(
            snapshot_id,
            scope,
            "sha256:" + f"{seed + 8:x}" * 64,
            len(files),
            len(symbols),
            len(edges),
        ),
        symbols,
        edges,
        files,
    )


def test_postgres_source_structure_is_atomic_searchable_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        workspace.workspace_id,
        project.project_id,
    )
    repository = PostgreSQLSourceStructureRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    first = _source_structure_artifact(scope, 1)
    first_result = repository.store_and_activate(first)
    assert not first_result.idempotent and first_result.snapshot == first.snapshot
    assert repository.store_and_activate(first).idempotent
    assert repository.get_active_snapshot(scope) == first.snapshot
    assert repository.get_snapshot(scope, first.snapshot.snapshot_id) == first.snapshot
    assert repository.iter_files(scope, first.snapshot.snapshot_id) == first.files
    assert (
        repository.get_file(scope, first.snapshot.snapshot_id, first.files[0].relative_path)
        == first.files[0]
    )
    assert repository.iter_symbols(scope, first.snapshot.snapshot_id) == first.symbols
    assert repository.iter_edges(scope, first.snapshot.snapshot_id) == first.edges
    assert repository.find_symbols(scope, first.snapshot.snapshot_id, "reconcile", limit=10) == (
        first.symbols[1],
    )
    assert repository.module_symbols_for_paths(
        scope, first.snapshot.snapshot_id, (first.files[0].relative_path,)
    ) == (first.symbols[0],)
    assert repository.symbols_by_ids(
        scope, first.snapshot.snapshot_id, (first.symbols[1].symbol_id,)
    ) == (first.symbols[1],)
    assert (
        repository.edges_from_symbols(
            scope, first.snapshot.snapshot_id, (first.symbols[0].symbol_id,)
        )
        == first.edges
    )
    assert (
        repository.edges_to_symbols(
            scope, first.snapshot.snapshot_id, (first.symbols[1].symbol_id,)
        )
        == first.edges
    )
    assert repository.last_sync_at(scope) is not None
    assert repository.latest_transition(scope) is None
    assert repository.list_activation_history(scope) == (first.snapshot,)

    second = _source_structure_artifact(scope, 2)
    assert not repository.store_and_activate(second).idempotent
    assert repository.get_active_snapshot(scope) == second.snapshot
    assert repository.latest_transition(scope) == (first.snapshot, second.snapshot)
    assert repository.list_activation_history(scope) == (second.snapshot, first.snapshot)

    conflicting_snapshot = replace(
        second.snapshot,
        source_digest="sha256:" + "c" * 64,
    )
    with pytest.raises(SourceIndexStorageFailure):
        repository.store_and_activate(replace(second, snapshot=conflicting_snapshot))
    assert repository.get_active_snapshot(scope) == second.snapshot

    reactivated = repository.store_and_activate(first)
    assert reactivated.idempotent and reactivated.snapshot == first.snapshot
    assert repository.get_active_snapshot(scope) == first.snapshot
    assert repository.latest_transition(scope) == (second.snapshot, first.snapshot)
    assert repository.list_activation_history(scope) == (
        first.snapshot,
        second.snapshot,
        first.snapshot,
    )

    restarted = PostgreSQLSourceStructureRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert restarted.get_active_snapshot(scope) == first.snapshot
    assert restarted.iter_symbols(scope, first.snapshot.snapshot_id) == first.symbols
    foreign_scope = replace(scope, project_id=ProjectId.new())
    assert restarted.get_active_snapshot(foreign_scope) is None
    with pytest.raises(SourceSnapshotNotFound):
        restarted.get_snapshot(foreign_scope, first.snapshot.snapshot_id)

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLSourceStructureRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    assert viewer.get_active_snapshot(scope) is None
    with pytest.raises(SourceIndexStorageFailure):
        viewer.store_and_activate(_source_structure_artifact(scope, 3))

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_column_privilege(current_user, "
            "'mnemo_team.source_structure_snapshots', 'source_digest', 'UPDATE'), "
            "has_column_privilege(current_user, "
            "'mnemo_team.source_structure_snapshots', 'is_active', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.source_structure_snapshots', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, True, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (
                str(workspace.owner_id),
                str(workspace.workspace_id),
                TeamOperation.CONTRIBUTE.value,
            ),
        )
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute(
                "UPDATE mnemo_team.source_structure_snapshots SET is_active = false WHERE "
                "workspace_id = CAST(%s AS uuid) AND snapshot_id = CAST(%s AS uuid)",
                (str(workspace.workspace_id), str(first.snapshot.snapshot_id)),
            )
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


_DBT_MANIFEST_FIXTURE = Path(__file__).parents[1] / "fixtures" / "dbt" / "manifest-v12.json"


def _dbt_manifest_artifact(scope: MemoryScope, stamp: int = 0) -> DbtManifestArtifact:
    raw = _DBT_MANIFEST_FIXTURE.read_text(encoding="utf-8")
    if stamp:
        raw = raw.replace("customer-stage", f"customer-stage-{stamp}")
    return DbtManifestParser().parse(
        raw,
        ManifestParseRequest(
            scope,
            "fixtures/dbt/manifest-v12.json",
            NOW + timedelta(seconds=stamp),
        ),
    )


def test_postgres_dbt_manifest_is_atomic_queryable_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        workspace.workspace_id,
        project.project_id,
    )
    repository = PostgreSQLProjectIndexRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    first_graph = _dbt_manifest_artifact(scope)
    first_id = DbtSnapshotId.new()
    first = repository.store_and_activate(first_graph, first_id)
    assert not first.idempotent and first.snapshot.is_active
    assert first.snapshot.node_count == len(first_graph.nodes)
    assert first.snapshot.edge_count == len(first_graph.edges)
    replay = repository.store_and_activate(
        first_graph, DbtSnapshotId.new(), expected_active_snapshot_id=first_id
    )
    assert replay.idempotent and replay.snapshot == first.snapshot
    assert repository.latest_transition(scope) is None
    assert repository.last_sync_at(scope) is not None
    assert repository.get_active_snapshot(scope) == first.snapshot
    assert repository.get_snapshot(scope, first_id) == first.snapshot
    assert repository.iter_nodes(scope, first_id) == first_graph.nodes
    assert repository.iter_edges(scope, first_id) == first_graph.edges

    selected = first_graph.nodes[-1]
    assert repository.get_node(scope, first_id, selected.unique_id) == selected
    assert repository.get_nodes(scope, first_id, (selected.unique_id,)) == (selected,)
    assert repository.find_nodes_by_original_file_path(
        scope, first_id, "models/marts/fct_orders.sql"
    ) == tuple(
        node
        for node in first_graph.nodes
        if node.original_file_path == "models/marts/fct_orders.sql"
    )
    child = next(node for node in first_graph.nodes if node.dependency_ids)
    upstream = repository.direct_upstream(scope, first_id, child.unique_id)
    assert upstream
    assert repository.get_upstream_edges(scope, first_id, (child.unique_id,)) == upstream
    parent = upstream[0].parent_id
    downstream = repository.direct_downstream(scope, first_id, parent)
    assert upstream[0] in downstream
    assert repository.get_downstream_edges(scope, first_id, (parent,)) == downstream

    second_graph = _dbt_manifest_artifact(scope, 1)
    second_id = DbtSnapshotId.new()
    second = repository.store_and_activate(
        second_graph, second_id, expected_active_snapshot_id=first_id
    )
    assert not second.idempotent and second.snapshot.is_active
    assert not repository.get_snapshot(scope, first_id).is_active
    assert repository.latest_transition(scope) == (
        repository.get_snapshot(scope, first_id),
        second.snapshot,
    )
    page = repository.list_snapshots(scope, limit=1)
    assert page.items == (second.snapshot,) and page.next_offset == 1
    assert repository.list_snapshots(scope, offset=1, limit=1).items[0].snapshot_id == first_id

    reactivated = repository.store_and_activate(
        first_graph, DbtSnapshotId.new(), expected_active_snapshot_id=second_id
    )
    assert reactivated.idempotent and reactivated.snapshot.snapshot_id == first_id
    assert repository.latest_transition(scope) == (
        repository.get_snapshot(scope, second_id),
        reactivated.snapshot,
    )
    with pytest.raises(ActiveSnapshotConflict):
        repository.store_and_activate(
            _dbt_manifest_artifact(scope, 2),
            DbtSnapshotId.new(),
            expected_active_snapshot_id=second_id,
        )
    with pytest.raises(ActiveSnapshotConflict):
        repository.store_and_activate(
            _dbt_manifest_artifact(scope, 2),
            first_id,
            expected_active_snapshot_id=first_id,
        )
    invalid_edge = replace(first_graph.edges[0], parent_id=DbtNodeId("model.absent"))
    with pytest.raises(InvalidManifestGraph, match="manifest edge endpoint"):
        repository.store_and_activate(
            replace(first_graph, edges=(invalid_edge,)),
            DbtSnapshotId.new(),
            expected_active_snapshot_id=first_id,
        )
    assert repository.get_active_snapshot(scope) == reactivated.snapshot

    restarted = PostgreSQLProjectIndexRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert restarted.get_active_snapshot(scope) == reactivated.snapshot
    assert restarted.iter_nodes(scope, first_id) == first_graph.nodes
    foreign_scope = replace(scope, project_id=ProjectId.new())
    assert restarted.get_active_snapshot(foreign_scope) is None
    assert (
        restarted.find_nodes_by_original_file_path(
            foreign_scope, first_id, "models/marts/fct_orders.sql"
        )
        == ()
    )
    with pytest.raises(ManifestSnapshotNotFound):
        restarted.get_snapshot(foreign_scope, first_id)
    with pytest.raises(ManifestNodeNotFound):
        restarted.get_node(foreign_scope, first_id, selected.unique_id)

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLProjectIndexRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    assert viewer.get_active_snapshot(scope) is None
    with pytest.raises(ProjectIndexStorageFailure, match="project index database operation failed"):
        viewer.store_and_activate(_dbt_manifest_artifact(scope, 3), DbtSnapshotId.new())

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_column_privilege(current_user, "
            "'mnemo_team.dbt_manifest_snapshots', 'content_digest', 'UPDATE'), "
            "has_column_privilege(current_user, "
            "'mnemo_team.dbt_manifest_snapshots', 'is_active', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.dbt_manifest_snapshots', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, True, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (
                str(workspace.owner_id),
                str(workspace.workspace_id),
                TeamOperation.CONTRIBUTE.value,
            ),
        )
        with pytest.raises(pg8000.dbapi.DatabaseError):
            cursor.execute(
                "UPDATE mnemo_team.dbt_manifest_snapshots SET is_active = false WHERE "
                "workspace_id = CAST(%s AS uuid) AND snapshot_id = CAST(%s AS uuid)",
                (str(workspace.workspace_id), str(first_id)),
            )
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_postgres_checkpoint_source_observation_is_immutable_and_scoped(
    postgres_harness: PostgreSQLHarness,
) -> None:
    control, workspace, _ = _create_workspace(postgres_harness)
    project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    control.create_project(
        project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=project.project_id,
        ),
    )
    task_scope = _checkpoint_scope(workspace, project)
    project_scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        workspace.workspace_id,
        project.project_id,
    )
    source = PostgreSQLSourceStructureRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    first_snapshot = _source_structure_artifact(project_scope, 4)
    second_snapshot = _source_structure_artifact(project_scope, 5)
    source.store_and_activate(first_snapshot)
    source.store_and_activate(second_snapshot)

    checkpoints = PostgreSQLCheckpointRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    aggregate, revision = _checkpoint_pair(task_scope, "a-observation")
    checkpoints.create_checkpoint_aggregate(aggregate, revision)
    observation = CheckpointSourceObservation(
        task_scope,
        aggregate.checkpoint_id,
        revision.revision_id,
        first_snapshot.snapshot.snapshot_id,
        NOW + timedelta(minutes=1),
    )
    first = checkpoints.append_checkpoint_source_observation(observation)
    assert not first.idempotent
    assert checkpoints.append_checkpoint_source_observation(observation).idempotent
    assert (
        checkpoints.get_checkpoint_source_observation(
            task_scope, aggregate.checkpoint_id, revision.revision_id
        )
        == observation
    )

    competing = replace(
        observation,
        source_snapshot_id=second_snapshot.snapshot.snapshot_id,
    )
    with pytest.raises(CheckpointSourceObservationConflict):
        checkpoints.append_checkpoint_source_observation(competing)
    with pytest.raises(CheckpointSourceObservationNotFound):
        checkpoints.append_checkpoint_source_observation(
            replace(observation, source_snapshot_id=CodeSnapshotId.new())
        )

    restarted = PostgreSQLCheckpointRepository(
        postgres_harness.runtime_factory(),
        principal_id=workspace.owner_id,
        workspace_id=workspace.workspace_id,
    )
    assert (
        restarted.get_checkpoint_source_observation(
            task_scope, aggregate.checkpoint_id, revision.revision_id
        )
        == observation
    )
    with pytest.raises(CheckpointSourceObservationNotFound):
        restarted.get_checkpoint_source_observation(
            replace(task_scope, task_id=TaskId.new()),
            aggregate.checkpoint_id,
            revision.revision_id,
        )

    viewer_id = OwnerId.new()
    _add_workspace_member(control, workspace, viewer_id, WorkspaceRole.VIEWER)
    viewer = PostgreSQLCheckpointRepository(
        postgres_harness.runtime_factory(),
        principal_id=viewer_id,
        workspace_id=workspace.workspace_id,
    )
    with pytest.raises(CheckpointSourceObservationNotFound):
        viewer.get_checkpoint_source_observation(
            task_scope, aggregate.checkpoint_id, revision.revision_id
        )

    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT has_table_privilege(current_user, "
            "'mnemo_team.checkpoint_source_observations', 'UPDATE'), "
            "has_table_privilege(current_user, "
            "'mnemo_team.checkpoint_source_observations', 'DELETE')"
        )
        assert tuple(cursor.fetchone() or ()) == (False, False)
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (
                str(workspace.owner_id),
                str(workspace.workspace_id),
                TeamOperation.READ.value,
            ),
        )
        cursor.execute("SELECT COUNT(*) FROM mnemo_team.checkpoint_source_observations")
        row = cursor.fetchone()
        assert row is not None and int(str(row[0])) == 1
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def _database_authorized(
    harness: PostgreSQLHarness,
    principal_id: OwnerId,
    workspace_id: WorkspaceId,
    operation: TeamOperation,
    *,
    project_id: ProjectId | None = None,
    item_owner_id: OwnerId | None = None,
    item_visibility: Visibility = Visibility.WORKSPACE,
) -> bool:
    connection = harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', %s, true)",
            (str(principal_id), str(workspace_id), operation.value),
        )
        cursor.execute(
            "SELECT mnemo_team.authorized(CAST(%s AS uuid), CAST(%s AS uuid), "
            "CAST(%s AS uuid), %s)",
            (
                str(workspace_id),
                None if project_id is None else str(project_id),
                None if item_owner_id is None else str(item_owner_id),
                item_visibility.value,
            ),
        )
        row = cursor.fetchone()
        assert row is not None
        return bool(row[0])
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def test_database_authorization_matches_python_policy_and_denies_cross_tenant(
    postgres_harness: PostgreSQLHarness,
) -> None:
    owner_repository, workspace, _ = _create_workspace(postgres_harness)
    policy = TeamAuthorizationPolicy()
    role_memberships: list[WorkspaceMembership] = []
    for role in (WorkspaceRole.ADMIN, WorkspaceRole.EDITOR, WorkspaceRole.VIEWER):
        principal_id = OwnerId.new()
        role_memberships.append(
            _add_workspace_member(owner_repository, workspace, principal_id, role)
        )
    role_memberships.append(
        owner_repository.get_workspace_membership(workspace.workspace_id, workspace.owner_id)
    )
    scope = MemoryScope(
        workspace.owner_id,
        ScopeLevel.WORKSPACE,
        Visibility.WORKSPACE,
        workspace_id=workspace.workspace_id,
    )
    for membership in role_memberships:
        for operation in TeamOperation:
            expected = policy.decide(
                TeamAuthorizationRequest(
                    membership.principal_id,
                    operation,
                    scope,
                    membership,
                )
            ).allowed
            actual = _database_authorized(
                postgres_harness,
                membership.principal_id,
                workspace.workspace_id,
                operation,
            )
            assert actual is expected, (membership.role, operation, actual, expected)

    private_project = TeamProject(
        workspace.workspace_id,
        ProjectId.new(),
        workspace.owner_id,
        TeamProjectVisibility.PRIVATE,
    )
    owner_repository.create_project(
        private_project,
        _audit(
            workspace.workspace_id,
            workspace.owner_id,
            TeamAuditAction.PROJECT_CREATED,
            project_id=private_project.project_id,
        ),
    )
    for project_role in ProjectRole:
        principal_id = OwnerId.new()
        workspace_membership = _add_workspace_member(
            owner_repository, workspace, principal_id, WorkspaceRole.EDITOR
        )
        project_membership = ProjectMembership(
            workspace.workspace_id,
            private_project.project_id,
            principal_id,
            project_role,
            MembershipStatus.ACTIVE,
        )
        owner_repository.set_project_membership(
            project_membership,
            expected=None,
            audit_event=_audit(
                workspace.workspace_id,
                workspace.owner_id,
                TeamAuditAction.PROJECT_MEMBERSHIP_CHANGED,
                project_id=private_project.project_id,
                subject_principal_id=principal_id,
            ),
        )
        project_scope = MemoryScope(
            workspace.owner_id,
            ScopeLevel.PROJECT,
            Visibility.PROJECT,
            workspace_id=workspace.workspace_id,
            project_id=private_project.project_id,
        )
        for operation in TeamOperation:
            expected = policy.decide(
                TeamAuthorizationRequest(
                    principal_id,
                    operation,
                    project_scope,
                    workspace_membership,
                    private_project,
                    project_membership,
                )
            ).allowed
            actual = _database_authorized(
                postgres_harness,
                principal_id,
                workspace.workspace_id,
                operation,
                project_id=private_project.project_id,
                item_visibility=Visibility.PROJECT,
            )
            assert actual is expected, (project_role, operation, actual, expected)

    admin_membership = next(item for item in role_memberships if item.role is WorkspaceRole.ADMIN)
    assert not _database_authorized(
        postgres_harness,
        admin_membership.principal_id,
        workspace.workspace_id,
        TeamOperation.READ,
        item_owner_id=workspace.owner_id,
        item_visibility=Visibility.OWNER,
    )

    foreign_repository, foreign_workspace, _ = _create_workspace(postgres_harness)
    connection = postgres_harness.runtime_factory()()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', %s, true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'read', true)",
            (str(workspace.owner_id), str(foreign_workspace.workspace_id)),
        )
        cursor.execute("SELECT count(*) FROM mnemo_team.workspaces")
        row = cursor.fetchone()
        assert row is not None and row[0] == 0
        connection.rollback()
        cursor.execute("SELECT count(*) FROM mnemo_team.workspaces")
        row = cursor.fetchone()
        assert row is not None and row[0] == 0
        cursor.execute(
            "SELECT set_config('mnemo.principal_id', 'not-a-uuid', true), "
            "set_config('mnemo.workspace_id', %s, true), "
            "set_config('mnemo.operation', 'not-an-operation', true)",
            (str(workspace.workspace_id),),
        )
        cursor.execute("SELECT count(*) FROM mnemo_team.workspaces")
        row = cursor.fetchone()
        assert row is not None and row[0] == 0
    finally:
        connection.rollback()
        cursor.close()
        connection.close()
    assert foreign_repository.get_workspace(foreign_workspace.workspace_id) == foreign_workspace
