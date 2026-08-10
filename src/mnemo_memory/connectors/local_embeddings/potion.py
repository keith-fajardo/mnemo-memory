"""Optional digest-verified Potion adapter for uncertainty-only local shadow routing."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib import import_module
from math import exp, isfinite, sqrt
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import Protocol, cast
from urllib.request import Request, urlopen

from mnemo_memory.packages.application.automatic_memory import (
    AutomaticMemoryBindingError,
    exclusive_local_file_lock,
)
from mnemo_memory.packages.application.context_routing import (
    CompactMemoryRoute,
    CompactMemoryRouteDecision,
    bounded_automatic_context_prompt,
    compact_memory_route_examples,
)

POTION_MODEL_ID = "minishlab/potion-base-8M"
POTION_MODEL_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"
_MODEL_FILES = {
    "config.json": (
        202,
        "2a6ac0e9aaa356a68a5688070db78fc3a464fefe85d2f06a1905ce3718687553",
    ),
    "model.safetensors": (
        30_236_760,
        "f65d0f325faadc1e121c319e2faa41170d3fa07d8c89abd48ca5358d9a223de2",
    ),
    "tokenizer.json": (
        683_666,
        "e67e803f624fb4d67dea1c730d06e1067e1b14d830e2c2202569e3ef0f70bb50",
    ),
}
_SETTINGS_VERSION = 1
_MAXIMUM_SETTINGS_BYTES = 4_096


class PotionRouterError(RuntimeError):
    """Stable local-router setup or inference failure without prompt or path details."""


class _StaticModel(Protocol):
    def encode(
        self,
        sentences: Iterable[str],
        *,
        show_progress_bar: bool = False,
        use_multiprocessing: bool = False,
    ) -> Iterable[Iterable[float]]: ...


@dataclass(frozen=True, slots=True)
class PotionRouterSettings:
    enabled: bool = False
    model_id: str = POTION_MODEL_ID
    revision: str = POTION_MODEL_REVISION

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("Potion router setting is invalid")
        if self.model_id != POTION_MODEL_ID or self.revision != POTION_MODEL_REVISION:
            raise ValueError("Potion router provenance is invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "model_id": self.model_id,
            "revision": self.revision,
        }


class LocalPotionRouterSettingsStore:
    """Private explicit opt-in state; absence means disabled."""

    _name = "potion-router.json"
    _lock_name = ".potion-router.lock"

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.path = self._directory / self._name

    def load(self) -> PotionRouterSettings:
        if not self.path.exists():
            return PotionRouterSettings()
        if self.path.is_symlink() or not self.path.is_file():
            raise PotionRouterError("MNEMO_POTION_SETTINGS_INVALID")
        try:
            if self.path.stat().st_size > _MAXIMUM_SETTINGS_BYTES:
                raise ValueError
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                not isinstance(value, dict)
                or set(value) != {"version", "settings"}
                or value["version"] != _SETTINGS_VERSION
                or not isinstance(value["settings"], dict)
                or set(value["settings"]) != {"enabled", "model_id", "revision"}
            ):
                raise ValueError
            return PotionRouterSettings(**value["settings"])
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise PotionRouterError("MNEMO_POTION_SETTINGS_INVALID") from error

    def save(self, settings: PotionRouterSettings) -> PotionRouterSettings:
        if not isinstance(settings, PotionRouterSettings):
            raise TypeError("Potion router setting is invalid")
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(
            {"version": _SETTINGS_VERSION, "settings": settings.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary_path: Path | None = None
        try:
            with exclusive_local_file_lock(self._directory, self._lock_name):
                if self.path.is_symlink():
                    raise OSError
                with NamedTemporaryFile(
                    "w", encoding="utf-8", dir=self._directory, delete=False
                ) as temporary:
                    temporary.write(payload)
                    temporary.write("\n")
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    temporary_path = Path(temporary.name)
                os.chmod(temporary_path, 0o600)
                os.replace(temporary_path, self.path)
                os.chmod(self.path, 0o600)
        except (AutomaticMemoryBindingError, OSError) as error:
            raise PotionRouterError("MNEMO_POTION_SETTINGS_WRITE_FAILED") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return settings


class PotionModelInstaller:
    """Explicitly acquire the pinned public files and verify every runtime input."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self.model_directory = self._directory / "potion-router-model"

    def install(
        self, fetch: Callable[[str, Path, int], None] | None = None
    ) -> PotionRouterSettings:
        fetcher = fetch or _download
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self._directory, prefix=".potion-download-") as raw_temporary:
            temporary = Path(raw_temporary)
            for filename, (size, digest) in _MODEL_FILES.items():
                destination = temporary / filename
                fetcher(_artifact_url(filename), destination, size)
                _verify_file(destination, size, digest)
            self.model_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.model_directory.is_symlink():
                raise PotionRouterError("MNEMO_POTION_MODEL_INVALID")
            for filename in _MODEL_FILES:
                destination = self.model_directory / filename
                if destination.is_symlink():
                    raise PotionRouterError("MNEMO_POTION_MODEL_INVALID")
                os.replace(temporary / filename, destination)
                os.chmod(destination, 0o600)
        verify_potion_model(self.model_directory)
        return LocalPotionRouterSettingsStore(self._directory).save(PotionRouterSettings(True))


def verify_potion_model(model_directory: Path) -> None:
    if (
        not model_directory.is_absolute()
        or model_directory.is_symlink()
        or not model_directory.is_dir()
    ):
        raise PotionRouterError("MNEMO_POTION_MODEL_INVALID")
    try:
        for filename, (size, digest) in _MODEL_FILES.items():
            _verify_file(model_directory / filename, size, digest)
    except (OSError, PotionRouterError) as error:
        raise PotionRouterError("MNEMO_POTION_MODEL_INVALID") from error


class PotionLocalMemoryRouter:
    """Centroid classifier over Mnemo-owned examples; prompts remain transient and local."""

    def __init__(self, data_directory: Path) -> None:
        self._directory = data_directory.expanduser().resolve()
        self._model_directory = self._directory / "potion-router-model"
        self._model: _StaticModel | None = None
        self._centroids: dict[CompactMemoryRoute, tuple[float, ...]] | None = None

    @property
    def model_id(self) -> str:
        return f"model2vec:{POTION_MODEL_ID}@{POTION_MODEL_REVISION}"

    def classify(self, prompt: str) -> CompactMemoryRouteDecision:
        bounded = bounded_automatic_context_prompt(prompt)
        try:
            model = self._get_model()
            if self._centroids is None:
                self._centroids = _build_centroids(model)
            prompt_vectors = tuple(model.encode((bounded,), use_multiprocessing=False))
            if len(prompt_vectors) != 1:
                raise ValueError
            vector = _unit_vector(prompt_vectors[0])
            scores = {
                route: sum(left * right for left, right in zip(vector, centroid, strict=True))
                for route, centroid in self._centroids.items()
            }
            maximum = max(scores.values())
            weights = {route: exp((score - maximum) * 6.0) for route, score in scores.items()}
            denominator = sum(weights.values())
            ordered = sorted(
                ((route, weight / denominator) for route, weight in weights.items()),
                key=lambda item: (-item[1], item[0].value),
            )
            route, confidence = ordered[0]
            return CompactMemoryRouteDecision(route, confidence, confidence - ordered[1][1])
        except PotionRouterError:
            raise
        except Exception as error:
            raise PotionRouterError("MNEMO_POTION_INFERENCE_UNAVAILABLE") from error

    def _get_model(self) -> _StaticModel:
        if self._model is not None:
            return self._model
        settings = LocalPotionRouterSettingsStore(self._directory).load()
        if not settings.enabled:
            raise PotionRouterError("MNEMO_POTION_DISABLED")
        verify_potion_model(self._model_directory)
        try:
            static_model = import_module("model2vec").StaticModel
        except (AttributeError, ImportError) as error:
            raise PotionRouterError("MNEMO_POTION_RUNTIME_NOT_INSTALLED") from error
        try:
            self._model = cast(
                _StaticModel,
                static_model.from_pretrained(
                    self._model_directory,
                    normalize=True,
                    force_download=False,
                ),
            )
        except Exception as error:
            raise PotionRouterError("MNEMO_POTION_MODEL_UNAVAILABLE") from error
        return self._model


def _build_centroids(model: _StaticModel) -> dict[CompactMemoryRoute, tuple[float, ...]]:
    centroids: dict[CompactMemoryRoute, tuple[float, ...]] = {}
    for route, examples in compact_memory_route_examples().items():
        vectors = tuple(model.encode(examples, use_multiprocessing=False))
        if len(vectors) != len(examples) or not vectors:
            raise ValueError
        unit_vectors = tuple(_unit_vector(vector) for vector in vectors)
        dimensions = len(unit_vectors[0])
        if any(len(vector) != dimensions for vector in unit_vectors):
            raise ValueError
        centroids[route] = _unit_vector(
            sum(vector[index] for vector in unit_vectors) / len(unit_vectors)
            for index in range(dimensions)
        )
    return centroids


def _unit_vector(values: Iterable[float]) -> tuple[float, ...]:
    vector = tuple(float(value) for value in values)
    if not vector or any(not isfinite(value) for value in vector):
        raise ValueError
    norm = sqrt(sum(value * value for value in vector))
    if norm <= 0:
        raise ValueError
    return tuple(value / norm for value in vector)


def _artifact_url(filename: str) -> str:
    return f"https://huggingface.co/{POTION_MODEL_ID}/resolve/{POTION_MODEL_REVISION}/{filename}"


def _download(url: str, destination: Path, expected_size: int) -> None:
    request = Request(url, headers={"User-Agent": "mnemo-memory/potion-setup"})
    try:
        with urlopen(request, timeout=30) as response, destination.open("wb") as output:
            observed = 0
            while True:
                chunk = response.read(min(1_048_576, expected_size + 1 - observed))
                if not chunk:
                    break
                observed += len(chunk)
                if observed > expected_size:
                    raise PotionRouterError("MNEMO_POTION_DOWNLOAD_INVALID")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except PotionRouterError:
        raise
    except Exception as error:
        raise PotionRouterError("MNEMO_POTION_DOWNLOAD_FAILED") from error


def _verify_file(path: Path, expected_size: int, expected_digest: str) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != expected_size:
        raise PotionRouterError("MNEMO_POTION_MODEL_INVALID")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1_048_576), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_digest:
        raise PotionRouterError("MNEMO_POTION_MODEL_INVALID")
