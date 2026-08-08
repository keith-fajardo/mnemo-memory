"""Reversible, narrowly-owned lifecycle-hook configuration for coding clients."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

ClientName = Literal["codex", "claude-code"]
_HOOK_TIMEOUT_SECONDS = 300


class AutomaticMemoryClientConfigError(ValueError):
    """Safe client-configuration error; do not expose config contents."""


def enable_client_hooks(
    client: ClientName, launcher: Path, home: Path, data_directory: Path
) -> bool:
    """Add only Mnemo-owned hook handlers, preserving unrelated client configuration."""
    path = _config_path(client, home)
    value = _read(path)
    hooks = value.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
    command = _command(launcher, client, data_directory)
    changed = False
    for event, matcher in _hook_events(client):
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
        if _contains(groups, command):
            if _set_owned_timeout(groups, command, _HOOK_TIMEOUT_SECONDS):
                changed = True
            continue
        group: dict[str, object] = {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": _HOOK_TIMEOUT_SECONDS,
                }
            ]
        }
        if matcher is not None:
            group["matcher"] = matcher
        groups.append(group)
        changed = True
    if changed:
        _write(path, value)
    return changed


def disable_client_hooks(
    client: ClientName, launcher: Path, home: Path, data_directory: Path
) -> bool:
    """Remove only exactly-owned commands, leaving every other hook untouched."""
    path = _config_path(client, home)
    if not path.exists():
        return False
    value = _read(path)
    hooks = value.get("hooks")
    if not isinstance(hooks, dict):
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
    command = _command(launcher, client, data_directory)
    changed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
        retained: list[object] = []
        for group in groups:
            updated = _without_owned_command(group, command)
            if updated is None:
                changed = True
                continue
            if updated != group:
                changed = True
            retained.append(updated)
        if retained:
            hooks[event] = retained
        else:
            del hooks[event]
    if changed:
        _write(path, value)
    return changed


def _config_path(client: ClientName, home: Path) -> Path:
    root = home.expanduser().resolve()
    if client == "codex":
        return root / "hooks.json"
    return root / ".claude" / "settings.json"


def client_home(client: ClientName, environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    if client == "codex":
        return Path(env.get("CODEX_HOME", Path.home() / ".codex"))
    return Path(env.get("HOME", str(Path.home())))


def _command(launcher: Path, client: ClientName, data_directory: Path) -> str:
    if not launcher.is_absolute():
        raise AutomaticMemoryClientConfigError("MNEMO_LAUNCHER_NOT_ABSOLUTE")
    if not data_directory.is_absolute():
        raise AutomaticMemoryClientConfigError("MNEMO_DATA_DIRECTORY_NOT_ABSOLUTE")
    return (
        f"{shlex.quote(str(launcher.resolve()))} automatic-memory-hook --client {client} "
        f"--data-dir {shlex.quote(str(data_directory.resolve()))}"
    )


def _hook_events(client: ClientName) -> tuple[tuple[str, str | None], ...]:
    # The broad PostToolUse matcher is required to notice edits and the Mnemo MCP save call.
    # UserPromptSubmit may use a bounded prompt transiently for scoped retrieval; it never stores
    # the prompt in lifecycle state or durable memory.
    # Stop/PreCompact are the client lifecycle boundaries that must receive the checkpoint reminder.
    if client == "codex":
        return (
            ("SessionStart", None),
            ("UserPromptSubmit", None),
            ("PostToolUse", ".*"),
            ("Stop", None),
            ("PreCompact", ".*"),
        )
    return (
        ("SessionStart", None),
        ("UserPromptSubmit", None),
        ("PostToolUse", ".*"),
        ("Stop", None),
        ("PreCompact", None),
    )


def _contains(groups: list[object], command: str) -> bool:
    return any(_has_command(group, command) for group in groups)


def _has_command(group: object, command: str) -> bool:
    if not isinstance(group, dict):
        return False
    handlers = group.get("hooks")
    return isinstance(handlers, list) and any(
        isinstance(handler, dict) and handler.get("command") == command for handler in handlers
    )


def _set_owned_timeout(groups: list[object], command: str, timeout: int) -> bool:
    """Upgrade only Mnemo's matching handler without disturbing client-owned entries."""
    changed = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if not isinstance(handler, dict) or handler.get("command") != command:
                continue
            if handler.get("timeout") != timeout:
                handler["timeout"] = timeout
                changed = True
    return changed


def _without_owned_command(group: object, command: str) -> dict[str, object] | None:
    if not isinstance(group, dict):
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
    handlers = group.get("hooks")
    if not isinstance(handlers, list):
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
    retained = [
        item for item in handlers if not (isinstance(item, dict) and item.get("command") == command)
    ]
    if not retained:
        return None
    result = dict(group)
    result["hooks"] = retained
    return result


def _read(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_UNSAFE")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID") from error
    if not isinstance(value, dict):
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_INVALID")
    return value


def _write(path: Path, value: dict[str, object]) -> None:
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise AutomaticMemoryClientConfigError("MNEMO_MEMORY_HOOK_CONFIG_UNSAFE")
    temporary: Path | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=parent, delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
