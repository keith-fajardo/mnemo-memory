# Architecture decision records

Architecture decision records capture foundational choices that affect product behavior,
security, privacy, dependencies, data compatibility, or replacement cost.

## Naming and lifecycle

Copy `0000-template.md` to the next four-digit number and a short kebab-case title, for example
`0001-domain-package-layout.md`. Do not reuse numbers.

Statuses are `proposed`, `accepted`, `superseded`, or `rejected`. An accepted ADR is immutable
except for factual corrections and links. A changed decision receives a new ADR that identifies
the superseded record and its migration plan.

An ADR must be accepted before merging a foundational decision, license exception, non-loopback
service exposure, irreversible migration, new canonical format, or cross-package dependency not
already allowed by `AGENTS.md`.

The template deliberately requires security/privacy, token/cost, dependency/licensing, and
reversal analysis so these consequences cannot be silently deferred.
