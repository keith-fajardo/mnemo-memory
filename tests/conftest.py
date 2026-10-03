"""Suite-wide guards for the whole test tree."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_background_source_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may start a real background code-map refresh worker (spec 2026-10-03 §7).

    The CLI's starter reads this variable at call time, so it also reaches hooks that tests run
    in a subprocess. Tests of the starter itself remove it and replace ``subprocess.Popen``.
    """
    monkeypatch.setenv("MNEMO_DISABLE_BACKGROUND_SOURCE_REFRESH", "1")
