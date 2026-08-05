from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from mnemo_memory.connectors.dbt.code_excerpt import DbtLocalCodeExcerptReader
from mnemo_memory.connectors.dbt.project_binding import (
    DbtProjectBinding,
    LocalDbtProjectBindingStore,
)
from mnemo_memory.packages.domain import (
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("00000000-0000-4000-8000-000000000901"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("00000000-0000-4000-8001-000000000901"),
        ProjectId.from_string("00000000-0000-4000-8002-000000000901"),
    )


def configured_reader(tmp_path: Path) -> tuple[Path, DbtLocalCodeExcerptReader]:
    project = tmp_path / "dbt project"
    project.mkdir()
    project.joinpath("dbt_project.yml").write_text("name: excerpt\n")
    store = LocalDbtProjectBindingStore(tmp_path / "data")
    store.set(DbtProjectBinding(project.resolve(), scope()))
    return project, DbtLocalCodeExcerptReader(store, lambda: NOW)


def test_dbt_excerpt_reads_only_requested_bounded_lines_with_evidence(tmp_path: Path) -> None:
    project, reader = configured_reader(tmp_path)
    model = project / "models" / "orders.sql"
    model.parent.mkdir()
    model.write_text("select\n  order_id,\n  amount\nfrom raw.orders\n")

    excerpt = reader.read(scope(), "models/orders.sql", start_line=2, maximum_lines=2)

    assert excerpt is not None
    assert excerpt.content == "  order_id,\n  amount"
    assert (excerpt.start_line, excerpt.end_line) == (2, 3)
    assert excerpt.evidence.observed_at == NOW
    assert excerpt.evidence.location.start_line == 2
    assert str(project) not in repr(excerpt)
    assert "from raw.orders" not in repr(excerpt)


def test_dbt_excerpt_rejects_secrets_unsafe_paths_and_unbounded_files(tmp_path: Path) -> None:
    project, reader = configured_reader(tmp_path)
    models = project / "models"
    models.mkdir()
    models.joinpath("secret.sql").write_text("select 'sk-abcdefghijklmnopqrstuvwxyz'\n")
    outside = tmp_path / "outside.sql"
    outside.write_text("select private_value\n")
    models.joinpath("escape.sql").symlink_to(outside)
    models.joinpath("binary.sql").write_bytes(b"select \xff")
    models.joinpath("large.sql").write_bytes(b"x" * 1_000_001)
    models.joinpath("long-line.sql").write_text("x" * 4_001)
    models.joinpath("not-code.txt").write_text("private text")

    assert reader.read(scope(), "models/secret.sql", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "../outside.sql", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "models/escape.sql", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "models/binary.sql", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "models/large.sql", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "models/long-line.sql", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "models/not-code.txt", start_line=1, maximum_lines=20) is None
    assert reader.read(scope(), "models/secret.sql", start_line=1, maximum_lines=41) is None
