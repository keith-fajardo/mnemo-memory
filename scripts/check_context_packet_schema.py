"""Validate the canonical context-packet schema against its typed v1 contract."""

from __future__ import annotations

import json
import sys
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/context-packet-v1-minimal.json"
sys.path.insert(0, str(ROOT))

from mnemo_memory.packages.domain import (  # noqa: E402
    ContentRepresentation,
    ContextItemType,
    ContextPacket,
    OmissionReason,
    PacketSchemaVersion,
    ValidityState,
)


def main() -> int:
    schema = json.loads(
        resources.files("mnemo_memory")
        .joinpath("resources", "schemas", "context-packet-v1.json")
        .read_text(encoding="utf-8")
    )
    fixture = json.loads(FIXTURE_PATH.read_text())
    packet = ContextPacket.from_dict(fixture)
    model_fields = set(packet.to_dict())
    if schema["$schema"] != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("context packet schema must use JSON Schema draft 2020-12")
    if schema["additionalProperties"] is not False:
        raise ValueError("context packet schema must reject unknown top-level fields")
    if set(schema["required"]) != model_fields or set(schema["properties"]) != model_fields:
        raise ValueError("context packet schema fields drift from the typed contract")
    if schema["properties"]["schema_version"]["const"] != PacketSchemaVersion.V1.value:
        raise ValueError("context packet schema version drifts from the typed contract")
    omission_values = schema["$defs"]["omission"]["properties"]["reason"]["enum"]
    item_values = schema["$defs"]["item"]["properties"]["item_type"]["enum"]
    validity_values = schema["$defs"]["item"]["properties"]["validity"]["enum"]
    if omission_values != [reason.value for reason in OmissionReason]:
        raise ValueError("context packet omission reasons drift from the typed contract")
    if item_values != [item_type.value for item_type in ContextItemType]:
        raise ValueError("context packet item types drift from the typed contract")
    if validity_values != [validity.value for validity in ValidityState]:
        raise ValueError("context packet validity values drift from the typed contract")
    if (
        schema["$defs"]["item"]["properties"]["content_representation"]["const"]
        != ContentRepresentation.UNTRUSTED_EVIDENCE.value
    ):
        raise ValueError("context packet content representation drifts from the typed contract")
    print("Context packet JSON Schema and representative fixture validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
