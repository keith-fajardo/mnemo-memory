# ADR 0009: Checked-in project procedures are explicit, cited procedural memory

## Status

Accepted for the first local procedural-memory slice.

## Context

A task checkpoint explains a specific handoff; a structural snapshot explains what Mnemo can
prove about a repository. Neither is a durable, reusable procedure such as a reconciliation
runbook or a project's release checklist. Replaying every project note is wasteful and makes an
agent more likely to treat unrelated prose as an instruction.

## Decision

Mnemo selects a procedure only when a caller supplies one or more literal tags. A current,
checked-in Markdown revision is eligible only with this strict scalar frontmatter:

```markdown
---
mnemo_kind: procedure
mnemo_tags: reconciliation, dbt
mnemo_mandatory: true
---
```

- The existing immutable knowledge-document revision is the single durable source of truth. There
  is no second procedure table, copy of the Markdown body, or new migration.
- The registry accepts checked-in repository Markdown only, normalizes bounded literal tags, and
  returns only revisions in the explicitly supplied project scope. Obsidian documents, malformed
  frontmatter, duplicate tags, and untagged documents are ineligible.
- Results are deterministic: mandatory procedures sort before optional procedures, then relative
  path and immutable revision ID. Selection is bounded and only matching tags enter the existing
  `skills_and_procedures` context section.
- A returned procedure is a cited immutable revision and remains `untrusted_evidence`. It cannot
  execute code, inspect an environment, override user/system policy, or outrank verified dbt and
  source facts about current structure. `mnemo_mandatory` expresses a project procedure priority,
  not authority over Mnemo policy.
- Calls do not infer tags from prompts, agent names, source bodies, or task titles. This preserves
  privacy and makes applicability reviewable. A later agent/skill registry may add explicit
  compatibility metadata without weakening the tag contract.

### Automatic client-profile selection

An enabled Codex or Claude Code lifecycle hook knows its client name, but it does **not** receive a
trustworthy arbitrary agent-role name such as `reconciliation-analyst`. To remove the need for an
agent to remember procedure tags while preserving that boundary, a project may define one current
client profile:

```markdown
---
mnemo_kind: agent_profile
mnemo_client: any
mnemo_procedure_tags: reconciliation, dbt
---
```

`mnemo_client` is exactly `codex`, `claude-code`, or `any`. At automatic session start, Mnemo
prefers one exact-client profile over one `any` profile, then uses its literal tags to attach the
matching procedure revisions to the bounded automatic context packet. Two equally applicable
profiles fail closed: Mnemo attaches neither rather than selecting by path or prose. The selected
profile's immutable revision and digest are retained as evidence on the attached procedure items.

## Consequences

Projects can keep a small reusable playbook in ordinary version-controlled Markdown and retrieve
only the relevant revision with exact digest provenance. Editing a procedure or profile uses the
existing knowledge synchronization path, so the old revision remains historical while the new
current revision is selected. This slice deliberately does not execute procedures, discover
arbitrary agent files, identify arbitrary agent roles, provide a separate MCP tool, or infer
workflow applicability with a model.
