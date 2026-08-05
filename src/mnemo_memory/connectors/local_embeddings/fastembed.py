"""Optional FastEmbed adapter that runs vectors on the local machine only."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, cast

from mnemo_memory.packages.knowledge.semantic import LocalEmbeddingError


class _FastEmbedModel(Protocol):
    def passage_embed(self, passages: tuple[str, ...]) -> Iterable[Iterable[float]]: ...

    def query_embed(self, query: str) -> Iterable[Iterable[float]]: ...


class FastEmbedLocalProvider:
    """Lazily initialize one explicitly selected local model and cache it under Mnemo data.

    FastEmbed may download the model weights on this adapter's first use.  Mnemo never invokes the
    adapter during ordinary checkpoint, MCP, or lexical-search operation; a user must explicitly
    invoke semantic indexing.  Document/query text is passed only to the local ONNX runtime.
    """

    def __init__(self, cache_directory: Path, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        if not cache_directory.is_absolute() or not model_name or len(model_name) > 200:
            raise ValueError("MNEMO_SEMANTIC_LOCAL_CONFIGURATION_INVALID")
        self._cache_directory = cache_directory
        self._model_name = model_name
        self._model: _FastEmbedModel | None = None

    @property
    def model_id(self) -> str:
        return "fastembed:" + self._model_name

    def embed_passages(self, passages: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not passages or any(
            not isinstance(item, str) or len(item) > 12_512 for item in passages
        ):
            raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_INPUT_INVALID")
        try:
            model = self._get_model()
            vectors = model.passage_embed(passages)
            return tuple(tuple(float(value) for value in vector) for vector in vectors)
        except LocalEmbeddingError:
            raise
        except Exception as error:
            raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_UNAVAILABLE") from error

    def embed_query(self, query: str) -> tuple[float, ...]:
        if not isinstance(query, str) or not query.strip() or len(query) > 512:
            raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_INPUT_INVALID")
        try:
            model = self._get_model()
            values = tuple(model.query_embed(query))
            if len(values) != 1:
                raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_RESULT_INVALID")
            return tuple(float(value) for value in values[0])
        except LocalEmbeddingError:
            raise
        except Exception as error:
            raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_UNAVAILABLE") from error

    def _get_model(self) -> _FastEmbedModel:
        if self._model is None:
            try:
                from fastembed import TextEmbedding  # type: ignore[import-not-found]
            except ImportError as error:
                raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_RUNTIME_NOT_INSTALLED") from error
            try:
                self._cache_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                self._model = cast(
                    _FastEmbedModel,
                    TextEmbedding(
                        model_name=self._model_name, cache_dir=str(self._cache_directory)
                    ),
                )
            except Exception as error:
                raise LocalEmbeddingError("MNEMO_SEMANTIC_LOCAL_UNAVAILABLE") from error
        assert self._model is not None
        return self._model
