"""Strict provider boundary for optional episodic-candidate extraction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from mnemo_memory.packages.domain import (
    EpisodicExtractionProposal,
    EpisodicExtractionRequest,
    EpisodicMemoryKind,
    MemoryScope,
    ModelBudgetDenied,
    ModelBudgetReservation,
    ModelBudgetReservationPort,
    ModelTaskType,
    Sensitivity,
)

_MAX_CANDIDATES = 4
_MAX_METADATA_LENGTH = 128


class RawEpisodicExtractionProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def generate(self, request: EpisodicExtractionRequest) -> object: ...


class EpisodicExtractionGatewayError(RuntimeError):
    """Payload-free model boundary failure with a stable diagnostic code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SchemaBoundEpisodicExtractionGateway:
    """Accept only Mnemo's closed proposal schema and retry malformed output once."""

    def __init__(
        self,
        provider: RawEpisodicExtractionProvider,
        *,
        provider_id: str,
        model_id: str,
        extractor_version: str,
        prompt_version: str,
        budget: ModelBudgetReservationPort,
        reservation: ModelBudgetReservation,
    ) -> None:
        self._provider = provider
        self._provider_id = _metadata(provider_id, "provider_id")
        self._model_id = _metadata(model_id, "model_id")
        self._extractor_version = _metadata(extractor_version, "extractor_version")
        self._prompt_version = _metadata(prompt_version, "prompt_version")
        self._budget = budget
        if not isinstance(reservation, ModelBudgetReservation):
            raise TypeError("episodic extraction model reservation is invalid")
        self._reservation = reservation

    @property
    def extractor_version(self) -> str:
        return self._extractor_version

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    def extract(
        self, scope: MemoryScope, request: EpisodicExtractionRequest
    ) -> tuple[EpisodicExtractionProposal, ...]:
        if not isinstance(scope, MemoryScope) or not isinstance(request, EpisodicExtractionRequest):
            raise TypeError("episodic extraction request is invalid")
        if scope.workspace_id is None:
            raise EpisodicExtractionGatewayError("MNEMO_MODEL_BUDGET_SCOPE_REQUIRED")
        try:
            metadata_matches = (
                self._provider.provider_id == self.provider_id
                and self._provider.model_id == self.model_id
            )
        except Exception as error:
            raise EpisodicExtractionGatewayError(
                "MNEMO_EPISODIC_PROVIDER_METADATA_MISMATCH"
            ) from error
        if not metadata_matches:
            raise EpisodicExtractionGatewayError("MNEMO_EPISODIC_PROVIDER_METADATA_MISMATCH")
        for attempt in range(2):
            try:
                self._budget.reserve(
                    scope.workspace_id,
                    ModelTaskType.EPISODIC_CANDIDATE_EXTRACTION,
                    self._reservation,
                )
            except ModelBudgetDenied as error:
                raise EpisodicExtractionGatewayError("MNEMO_MODEL_BUDGET_EXCEEDED") from error
            except Exception as error:
                raise EpisodicExtractionGatewayError("MNEMO_MODEL_BUDGET_UNAVAILABLE") from error
            try:
                raw = self._provider.generate(request)
            except Exception as error:
                raise EpisodicExtractionGatewayError("MNEMO_EPISODIC_PROVIDER_FAILURE") from error
            try:
                return parse_episodic_output(raw, request.max_candidates)
            except (TypeError, ValueError) as error:
                if attempt == 1:
                    raise EpisodicExtractionGatewayError("MNEMO_EPISODIC_INVALID_OUTPUT") from error
        raise AssertionError("episodic extraction retry loop did not terminate")


def _metadata(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_METADATA_LENGTH:
        raise ValueError(f"episodic extraction {name} is invalid")
    return value


def parse_episodic_output(raw: object, max_candidates: int) -> tuple[EpisodicExtractionProposal, ...]:
    if not isinstance(raw, Mapping) or set(raw) != {"candidates"}:
        raise ValueError("episodic extraction output fields are invalid")
    values = raw["candidates"]
    if not isinstance(values, list) or len(values) > min(max_candidates, _MAX_CANDIDATES):
        raise ValueError("episodic extraction candidate count is invalid")
    proposals: list[EpisodicExtractionProposal] = []
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {
            "kind",
            "claim",
            "confidence",
            "sensitivity",
        }:
            raise ValueError("episodic extraction proposal fields are invalid")
        kind = value["kind"]
        claim = value["claim"]
        confidence = value["confidence"]
        sensitivity = value["sensitivity"]
        if (
            not isinstance(kind, str)
            or not isinstance(claim, str)
            or not isinstance(sensitivity, str)
        ):
            raise TypeError("episodic extraction proposal text fields are invalid")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise TypeError("episodic extraction proposal confidence is invalid")
        proposals.append(
            EpisodicExtractionProposal(
                EpisodicMemoryKind(kind), claim, float(confidence), Sensitivity(sensitivity)
            )
        )
    return tuple(proposals)


_parse_output = parse_episodic_output  # backward-compatible internal alias
