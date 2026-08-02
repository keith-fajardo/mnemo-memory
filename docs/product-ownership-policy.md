# Product ownership and clean-room policy

## Purpose

Mnemo is an independently owned implementation of a local-first unified context platform. This
policy protects that ownership while allowing properly licensed, replaceable infrastructure.
It applies to source code, prompts, schemas, data formats, migrations, tests, fixtures,
documentation, visual assets, build tooling, generated artifacts, and contributions.

## Originality standard

Mnemo-specific work must be either:

1. created originally for Mnemo by a contributor who can grant the project the right to use it;
   or
2. incorporated under a reviewed license with its origin, version, author, and applicable
   notices recorded before inclusion.

Clean-room work is based on Mnemo's product contract, threat model, ADRs, evaluation workflows,
public standards, and public capability-level documentation. Similar product behavior is not a
license to copy an implementation.

Contributors must not copy, translate, port, reproduce, or adapt source code, prompts, schemas,
migrations, tests, documentation, internal formats, UI assets, or other source artifacts from
TencentDB Agent Memory or another competing memory product. A competing product must not be
installed, imported, executed, queried, benchmarked as a required comparator, or used as a
runtime, build-time, development, or test dependency.

Reverse engineering, decompilation, source inspection, and source-to-source transformation of a
competitor for use in Mnemo are prohibited. Public behavior may inform a capability statement
only; the resulting design must be independently justified and documented.

## Allowed third-party infrastructure

Standard infrastructure libraries, language runtimes, protocol SDKs, database drivers, parsers,
and developer tools are allowed when all of the following are true:

- the dependency solves a nondifferentiating infrastructure problem;
- its exact direct and transitive versions are locked;
- its source, authorship or maintainer, license, purpose, and replacement boundary are recorded;
- the license is approved and required notices can be honored;
- its maintenance and security posture are acceptable for the intended use;
- Mnemo-specific policy remains behind a Mnemo-owned interface; and
- it is not a competing memory product or a disguised distribution of one.

Mnemo does not delegate authorization, consent, retention, deletion, source authority, or the
application of canonical mutations to a model or third-party product.

## Contributor provenance

Each contribution must be attributable to a named contributor through version control. By
submitting a change, the contributor attests that:

- the contribution is original or its third-party portions are identified and properly licensed;
- it was not copied or derived from prohibited competing-product artifacts;
- all new dependencies and borrowed assets are disclosed in the dependency register or an ADR;
- generated assistance was reviewed by the contributor for originality, licensing, security,
  privacy, and correctness; and
- the contributor has the right to submit the work under the repository's applicable terms.

Use a Developer Certificate of Origin-style `Signed-off-by: Name <email>` trailer once the
repository's commit policy is enabled. Until then, pull-request or review records must contain
the same attestation. False or incomplete provenance blocks acceptance.

If provenance becomes uncertain after acceptance, quarantine the affected artifact, prevent new
distribution, preserve investigation evidence without redistributing suspect content, and
replace it with an independently implemented or approved alternative.

## Dependency approval process

Before a dependency or CI Action is added or upgraded, the proposer must:

1. explain why standard-library or existing dependencies are insufficient;
2. identify the direct package and complete transitive change from the lockfile;
3. record every package's pinned version, source URL, license, author, owner, purpose,
   direct/transitive status, replacement boundary, and approval status in
   `docs/dependency-register.toml`;
4. confirm the package is not a competing product and does not import prohibited artifacts;
5. assess maintenance, release provenance, known vulnerabilities, install scripts, network
   behavior, data access, and telemetry;
6. confirm license compatibility and any attribution or source-disclosure obligations;
7. run clean installation, composition, license, provenance, test, and relevant security checks;
   and
8. obtain maintainer approval before merging the manifest or lockfile change.

Permissive licenses such as MIT, BSD, Apache-2.0, PSF-2.0, and ISC may be approved through normal
review. Copyleft, source-available, noncommercial, field-of-use, custom, unknown, or conflicting
licenses require a licensing ADR and explicit maintainer approval. An unapproved or unknown
license fails CI.

CI Actions are dependencies. Use exact reviewed releases and prefer immutable commit pins when
the repository adopts that policy. Installation scripts must not run unless their behavior and
necessity have been reviewed.

## Dependency register ownership

The dependency register is the canonical approval record. Lockfiles are the canonical resolved
version and integrity record. Both must agree. The owning maintainer listed in the register is
responsible for upgrades, advisories, replacement planning, and license changes; ownership does
not imply authorship of the third-party package.

Removing a dependency requires removing unused configuration, lockfile entries, notices, and
register entries in the same bounded change. Historic decisions remain in ADRs and version
control.

## Review and enforcement

Automated checks validate register completeness, exact lockfile parity, approved licenses, CI
Action parity, toolchain pins, and prohibited dependency names. Reviewers additionally inspect
the source and intent of Mnemo-specific changes because automation cannot prove originality.

Suspected violations stop release and merge activity for the affected change. Maintainers decide
remediation with legal or security advice when needed. The preferred remedy is removal and clean
reimplementation from Mnemo's contracts, not attempted cosmetic rewriting of suspect material.
