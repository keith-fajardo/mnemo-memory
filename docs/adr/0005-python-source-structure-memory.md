# ADR 0005: Rebuildable multi-language source-structure memory

## Status

Accepted for the local source-structure slice. The original Python-first decision
was amended when the same immutable graph contract gained offline grammar adapters.

## Context

Task checkpoints preserve *what a person or agent was doing*. dbt manifests preserve
authoritative dbt lineage. Neither alone can answer ordinary repository questions such as
which module defines a function or what a module declares it imports. Repeatedly searching a
large checkout consumes context and time in a fresh agent session.

## Decision

Mnemo will build a separate, immutable, scoped projection of source structure.

- Python uses the standard-library `ast` module. JavaScript/JSX, TypeScript/TSX, Go, Rust, C, C++,
  C#, Java, and PHP use pinned, precompiled Tree-sitter grammar wheels. Every adapter is local and
  offline at parse time: it never imports project modules, executes code, reads shell environment
  values, runs a build, fetches a grammar, or calls a model.
- The projection includes safe relative file identity, module/class/function declarations, import
  declarations, and syntactically explicit calls. An import gains an internal target-symbol link
  only when exactly one module in the same snapshot matches. A call gains a target link
  only for an unambiguous same-module, fully-qualified, or imported-member declaration. JavaScript
  and TypeScript additionally resolve a local default import only when its target file contains one
  named `export default function` or `export default class`, and one exact named local barrel export
  such as `export { member as alias } from "./module"`. A local wildcard barrel can resolve a requested
  non-default member only when exactly one saved local declaration is proven; anonymous defaults,
  ambiguous re-exports, and `export default existingName` require value-flow semantics and remain unresolved. A default
  class member call resolves only for an explicitly declared static method. The current
  adapters resolve simple Python/ES-module aliases plus direct Java class imports, Rust
  `use crate::...` items, exact Go imported-package member calls, and direct `self`/`this` sibling
  method calls only when a unique saved
  declaration exists. Duplicate declarations are left unresolved rather than resolved by source
  ordering. Go imports remain unresolved as module edges because a Go import identifies
  a package directory while this projection identifies files. It excludes source text,
  comments, docstrings, credentials, generated caches, and arbitrary project metadata.
- Each projection is content-addressed and belongs to an explicit Mnemo scope. Paths locate a
  local checkout but never become owner, workspace, or project identity.
- Snapshots are immutable and rebuildable. A later parser/storage service may select the current
  snapshot only with explicit source-state evidence; an active snapshot is not automatically
  current.
- Context retrieval can return only a bounded, relevant subset with provenance and omission
  notices. It supports deterministic dependency/dependent impact candidates through resolved
  internal links, but will not replay a checkout or claim a complete runtime call graph.

## Consequences

This establishes useful static structure without overstating what a syntax tree can prove. Dynamic
imports, aliases that cannot be proven, runtime dispatch, generated code, unresolved cross-file
targets, and a complete call graph remain unsupported until they have explicit, tested contracts. No source is uploaded, and no model
participates in authoritative structural facts. New languages join through a Mnemo-owned adapter;
we do not download or execute arbitrary grammar code from a user's repository.
