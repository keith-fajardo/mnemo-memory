from __future__ import annotations

from mnemo_memory.packages.domain import (
    KnowledgeDocumentId,
    KnowledgeDocumentRevisionId,
    MemoryScope,
    OwnerId,
    ProjectId,
    ScopeLevel,
    Visibility,
    WorkspaceId,
)
from mnemo_memory.packages.knowledge import (
    KnowledgeDocumentParser,
    KnowledgeDocumentParseRequest,
    KnowledgeSyncActionKind,
    KnowledgeSyncPlanner,
    KnownKnowledgeDocument,
)


def scope() -> MemoryScope:
    return MemoryScope(
        OwnerId.from_string("11111111-1111-4111-8111-111111111111"),
        ScopeLevel.PROJECT,
        Visibility.PROJECT,
        WorkspaceId.from_string("22222222-2222-4222-8222-222222222222"),
        ProjectId.from_string("33333333-3333-4333-8333-333333333333"),
    )


def document(path: str, text: str):  # type: ignore[no-untyped-def]
    return KnowledgeDocumentParser().parse(KnowledgeDocumentParseRequest(scope(), path), text)


def known(identifier: str, path: str, text: str) -> KnownKnowledgeDocument:
    parsed = document(path, text)
    return KnownKnowledgeDocument(
        KnowledgeDocumentId.from_string(identifier),
        scope(),
        path,
        parsed.content_digest,
        KnowledgeDocumentRevisionId.from_string(identifier),
        1,
    )


def test_sync_plan_is_deterministic_and_preserves_ids_for_revisions_and_renames() -> None:
    unchanged = known("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "notes/a.md", "# A\nSame")
    revised = known("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "notes/b.md", "# B\nOld")
    renamed = known("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "notes/old.md", "# Move\nSame")
    deleted = known("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "notes/deleted.md", "# Deleted")
    discovered = (
        document("notes/a.md", "# A\nSame"),
        document("notes/b.md", "# B\nNew"),
        document("notes/new.md", "# Move\nSame"),
        document("notes/added.md", "# Added"),
    )

    plan = KnowledgeSyncPlanner().plan(scope(), (unchanged, revised, renamed, deleted), discovered)

    assert [
        (item.kind, item.relative_path, item.previous_relative_path) for item in plan.actions
    ] == [
        (KnowledgeSyncActionKind.UNCHANGED, "notes/a.md", None),
        (KnowledgeSyncActionKind.ADDED, "notes/added.md", None),
        (KnowledgeSyncActionKind.REVISED, "notes/b.md", None),
        (KnowledgeSyncActionKind.TOMBSTONED, "notes/deleted.md", None),
        (KnowledgeSyncActionKind.RENAMED, "notes/new.md", "notes/old.md"),
    ]
    action_by_path = {item.relative_path: item for item in plan.actions}
    assert action_by_path["notes/b.md"].document_id == revised.document_id
    assert action_by_path["notes/new.md"].document_id == renamed.document_id
    assert action_by_path["notes/deleted.md"].document is None
    assert plan.changed is True


def test_sync_does_not_guess_a_rename_for_duplicate_or_copied_document_bytes() -> None:
    first = known("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "one.md", "# Same")
    second = known("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "two.md", "# Same")

    plan = KnowledgeSyncPlanner().plan(
        scope(), (first, second), (document("three.md", "# Same"), document("four.md", "# Same"))
    )

    assert {item.kind for item in plan.actions} == {
        KnowledgeSyncActionKind.ADDED,
        KnowledgeSyncActionKind.TOMBSTONED,
    }
    assert not any(item.kind is KnowledgeSyncActionKind.RENAMED for item in plan.actions)


def test_sync_rejects_scope_mismatch_and_duplicate_paths() -> None:
    duplicate = document("note.md", "# first")
    duplicate_again = document("note.md", "# second")
    try:
        KnowledgeSyncPlanner().plan(scope(), (), (duplicate, duplicate_again))
    except ValueError as error:
        assert str(error) == "knowledge synchronization paths must be unique"
    else:
        raise AssertionError("expected duplicate path rejection")
