"""JSON-file store for pending host-agent-takeover markers.

Stores the source_event_key for scopes that require host-agent takeover before
proceeding. This is a local lifecycle marker, not a second task history.
"""

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from mnemo_memory.packages.domain import MemoryScope


class LocalPendingTakeoverStore:
    """Persist pending host-agent-takeover markers by scope.

    Stores a mapping of scope keys to source_event_key strings. When a takeover
    is pending, the source_event_key is stored; when cleared, the entry is removed.
    Uses atomic writes via tempfile + os.replace and rejects symlinked files.
    """

    _name = "automatic-memory-pending-takeover.json"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self._path = self._directory / self._name

    def mark(self, scope: MemoryScope, source_event_key: str) -> None:
        """Mark a scope as having a pending takeover with the given source_event_key."""
        self._set(scope, source_event_key)

    def pending(self, scope: MemoryScope) -> str | None:
        """Return the stored source_event_key for a scope, or None if not pending."""
        return self._read().get(_scope_key(scope))

    def clear(self, scope: MemoryScope) -> None:
        """Clear any pending takeover marker for a scope."""
        try:
            values = self._read()
            key = _scope_key(scope)
            if key in values:
                values.pop(key)
                # The marker is a bounded local reminder, never an activity history.
                if len(values) > 128:
                    values = {}
                self._write(values)
        except OSError:
            return

    def _set(self, scope: MemoryScope, source_event_key: str) -> None:
        try:
            values = self._read()
            key = _scope_key(scope)
            if isinstance(source_event_key, str) and source_event_key:
                values[key] = source_event_key
            # The marker is a bounded local reminder, never an activity history.
            if len(values) > 128:
                values = {key: source_event_key} if isinstance(source_event_key, str) else {}
            self._write(values)
        except OSError:
            return

    def _read(self) -> dict[str, str]:
        if not self._path.exists() or self._path.is_symlink():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            key: item
            for key, item in value.items()
            if isinstance(key, str) and isinstance(item, str)
        }

    def _write(self, values: dict[str, str]) -> None:
        temporary: Path | None = None
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self._path.exists() and self._path.is_symlink():
                return
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self._directory, delete=False
            ) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                json.dump(values, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
        except OSError:
            return
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _scope_key(scope: MemoryScope) -> str:
    """Return a stable non-path local key for one already-enabled project scope."""
    return sha256(
        json.dumps(scope.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
