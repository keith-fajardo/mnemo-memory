# Context-packet schema compatibility

`schemas/context-packet-v1.json` is the canonical wire contract for schema version `1.0`.
`packages/domain/context_packet.py` is its strict, typed executable contract. Both must change in
the same pull request, with representative-fixture and drift tests passing.

Version `1.x` accepts only the exact closed fields defined by the `1.0` schema. A field addition,
removal, renamed enum, or changed semantic requires a new major schema version and an explicit
compatibility adapter outside the domain model. Consumers must reject unknown schema versions and
unknown closed-object fields rather than guess. Packets retain their declared version during
serialization; they are never silently upgraded or downgraded.

The hard budget is part of the packet contract. A later context engine may omit complete items but
must construct a new valid packet and record an omission; it must never truncate content or mutate
the declared token total in place.
