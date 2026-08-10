"""Optional Potion routing is explicit, digest-verified, bounded, and local-only at runtime."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mnemo_memory.apps.cli.main import app
from mnemo_memory.connectors.local_embeddings import potion
from mnemo_memory.connectors.local_embeddings.potion import (
    LocalPotionRouterSettingsStore,
    PotionLocalMemoryRouter,
    PotionModelInstaller,
    PotionRouterError,
    verify_potion_model,
)
from mnemo_memory.packages.application.context_routing import CompactMemoryRoute


def test_explicit_potion_setup_verifies_every_file_and_writes_private_opt_in(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    files = {
        "config.json": b"config",
        "model.safetensors": b"weights",
        "tokenizer.json": b"tokenizer",
    }
    monkeypatch.setattr(
        potion,
        "_MODEL_FILES",
        {
            name: (len(payload), hashlib.sha256(payload).hexdigest())
            for name, payload in files.items()
        },
    )
    requested: list[str] = []

    def fetch(url: str, destination: Path, expected_size: int) -> None:
        requested.append(url)
        payload = files[destination.name]
        assert len(payload) == expected_size
        destination.write_bytes(payload)

    installer = PotionModelInstaller(tmp_path)
    settings = installer.install(fetch)

    assert settings.enabled is True
    assert len(requested) == 3
    assert all(potion.POTION_MODEL_REVISION in url for url in requested)
    verify_potion_model(installer.model_directory)
    assert LocalPotionRouterSettingsStore(tmp_path).load().enabled is True
    assert LocalPotionRouterSettingsStore(tmp_path).path.stat().st_mode & 0o777 == 0o600

    (installer.model_directory / "config.json").write_bytes(b"tampered")
    with pytest.raises(PotionRouterError, match="MODEL_INVALID"):
        verify_potion_model(installer.model_directory)


class _FakeStaticModel:
    def __init__(self) -> None:
        self.last_sentences: tuple[str, ...] = ()

    def encode(
        self,
        sentences: Iterable[str],
        *,
        show_progress_bar: bool = False,
        use_multiprocessing: bool = False,
    ) -> tuple[tuple[float, ...], ...]:
        del show_progress_bar, use_multiprocessing
        values = tuple(sentences)
        self.last_sentences = values
        vectors: list[tuple[float, ...]] = []
        for sentence in values:
            lowered = sentence.casefold()
            vectors.append(
                (
                    float(sum(term in lowered for term in ("previous", "resume", "before")) + 1),
                    float(sum(term in lowered for term in ("policy", "docs", "knowledge")) + 1),
                    float(sum(term in lowered for term in ("modules", "callers", "depends")) + 1),
                    float(sum(term in lowered for term in ("poem", "translate", "equation")) + 1),
                )
            )
        return tuple(vectors)


def test_potion_classifier_uses_only_the_bounded_head_tail_view(tmp_path: Path) -> None:
    private_middle = "middle-private-potion-marker"
    prompt = (
        "Which modules depend on this adapter? "
        + "prefix-noise " * 60
        + private_middle
        + " suffix-noise" * 60
        + " Trace all callers."
    )
    model = _FakeStaticModel()
    router = PotionLocalMemoryRouter(tmp_path)
    router._model = model

    decision = router.classify(prompt)

    assert decision.route in set(CompactMemoryRoute)
    assert 0 <= decision.margin <= decision.confidence <= 1
    assert len(model.last_sentences) == 1
    assert len(model.last_sentences[0]) <= 512
    assert private_middle not in model.last_sentences[0]


def test_router_help_states_explicit_setup_and_shadow_scope() -> None:
    runner = CliRunner()
    memory = runner.invoke(app, ["memory", "--help"])
    router = runner.invoke(app, ["memory", "router", "--help"])

    assert memory.exit_code == router.exit_code == 0
    assert "Potion shadow router" in memory.output
    assert "digest-verify" in router.output
    assert "enable" in router.output and "disable" in router.output
