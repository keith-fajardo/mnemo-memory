"""Typed hook modes and the replay overrides seam, without the typed step's imports.

They live apart from ``typed_decision_hook`` (which re-exports both) so a replay child can build
its overrides before the timed hook call and still leave the typed step, with ``asyncio``, to be
imported cold inside the hook's mode read, as in a real hook process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mnemo_memory.packages.domain import TypedDecisionMode

if TYPE_CHECKING:
    from mnemo_memory.apps.cli.typed_decision_hook import DecisionObserver, GuardFactory


@dataclass(frozen=True, slots=True)
class TypedHookModes:
    """The four hook decision modes; all ``off`` means today's path, byte for byte."""

    front_door: TypedDecisionMode = TypedDecisionMode.OFF
    relevance: TypedDecisionMode = TypedDecisionMode.OFF
    tier_hint: TypedDecisionMode = TypedDecisionMode.OFF
    skill: TypedDecisionMode = TypedDecisionMode.OFF

    def __post_init__(self) -> None:
        if any(not isinstance(mode, TypedDecisionMode) for mode in self._modes()):
            raise TypeError("typed hook modes are invalid")

    def _modes(self) -> tuple[TypedDecisionMode, ...]:
        return (self.front_door, self.relevance, self.tier_hint, self.skill)

    @property
    def any_on(self) -> bool:
        return any(mode is not TypedDecisionMode.OFF for mode in self._modes())


@dataclass(frozen=True, slots=True)
class TypedHookOverrides:
    """Replay-only seam: a synthetic-source guard factory and fixed modes (spec §3).

    The ``automatic-memory-hook`` command never builds one, so the real hook always uses the
    runtime guard and the locked settings. ``observer`` lets the replay score decisions.
    """

    guard_factory: GuardFactory
    modes: TypedHookModes
    observer: DecisionObserver | None = None
