"""Helpers for reconstructing historical SQLite schemas in migration tests."""

from __future__ import annotations

import sqlite3


def drop_checkpoint_deletion_schema(connection: sqlite3.Connection) -> None:
    """Remove schema-30 objects before replaying from an older migration ledger."""
    connection.executescript(
        """
        DROP TRIGGER IF EXISTS checkpoint_deletion_prevents_resurrection;
        DROP TRIGGER IF EXISTS checkpoint_aggregate_delete_requires_tombstone;
        DROP TRIGGER IF EXISTS checkpoint_revision_delete_requires_tombstone;
        DROP TRIGGER IF EXISTS checkpoint_event_delete_requires_tombstone;
        DROP TRIGGER IF EXISTS checkpoint_observation_delete_requires_tombstone;
        DROP TABLE IF EXISTS checkpoint_deletions;
        """
    )
