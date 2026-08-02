"""Real, isolated cross-client MCP transport for the Issue 11A checkpoint fixture."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import cast

import pytest

from scripts.run_cross_client_benchmark import run

pytestmark = pytest.mark.skipif(
    shutil.which("codex") is None or shutil.which("claude") is None,
    reason="codex or claude executable is unavailable",
)


def test_exact_registered_launchers_resume_the_same_evidenced_checkpoint(tmp_path: Path) -> None:
    result = run(tmp_path / "Cross Client Δ With Spaces")
    assert result["passed"] is True
    assert result["client_versions"] == {
        "codex": "codex-cli 0.145.0",
        "claude_code": "2.1.220 (Claude Code)",
    }
    assert result["registration_scope"] == "user"
    assert result["no_model_call"] is True
    assert result["cross_scope_non_disclosure"] is True
    assert result["client_configurations_preserved"] is True
    assert all(cast(dict[str, bool], result["failure_degradation"]).values())
    no_memory = cast(dict[str, object], result["no_memory"])
    assert cast(dict[str, object], no_memory["quality"])["required_fact_recall"] == 0.0
    for condition in ("codex_to_claude", "claude_to_codex", "alternating_revision"):
        value = cast(dict[str, object], result[condition])
        assert value["passed"] is True
        assert cast(dict[str, object], value["quality"])["required_fact_recall"] == 1.0
        assert cast(dict[str, object], value["quality"])["provenance_coverage"] == 1.0
        assert cast(int, value["context_packet_tokens"]) <= 600
        assert cast(int, value["full_transcript_tokens"]) > cast(
            int, value["context_packet_tokens"]
        )
    alternating = cast(dict[str, object], result["alternating_revision"])
    assert alternating["revision_number"] == 2
    assert alternating["stable_identity"] is True
    assert alternating["distinct_revision_identity"] is True
    assert all(
        "/" not in digest for digest in cast(dict[str, str], result["launcher_digests"]).values()
    )
