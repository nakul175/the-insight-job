# Architecture and module boundaries


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Preserve the existing product architecture unless an approved change requires otherwise. Design depth follows risk, not document length.


## Controls


<a id="eng-arch-001"></a>

### ENG-ARCH-001 — Map responsibilities before material change

**Requirement:** MUST · **Review:** human · **Evidence:** Design delta and upstream references

For a C2/C3 change, identify affected components, contracts, data owners, trust boundaries and external dependencies in a technical design delta. Link the approved HLD/LLD or equivalent rather than duplicating it.


<a id="eng-arch-002"></a>

### ENG-ARCH-002 — Prefer a modular simple deployment

**Requirement:** SHOULD · **Review:** human · **Evidence:** ADR

Start with the simplest deployable architecture that meets approved requirements. A modular monolith is a default option, not a universal mandate. New services need a concrete isolation, lifecycle, scaling or ownership reason and an operating-cost decision.


<a id="eng-arch-003"></a>

### ENG-ARCH-003 — Separate responsibilities without dogmatic layers

**Requirement:** MUST · **Review:** human · **Evidence:** Module map and code review

Keep presentation, workflow/domain decisions, persistence and external integration responsibilities identifiable. Framework-native route handlers may orchestrate simple use cases, but permissions and consequential business rules must not be scattered across UI components or third-party adapters.


<a id="eng-arch-004"></a>

### ENG-ARCH-004 — Control module dependencies

**Requirement:** MUST · **Review:** human · **Evidence:** Module contracts and import review

Define public module interfaces and allowed dependency direction. Avoid circular dependencies, cross-module internal imports and shared mutable global state. Shared libraries must have clear ownership; tenant-specific customisation must not become unexplained customer forks.


<a id="eng-arch-005"></a>

### ENG-ARCH-005 — Record consequential decisions

**Requirement:** MUST · **Review:** human · **Evidence:** ADR

Create or amend an ADR for persistence, identity, tenancy, hosting region, model/provider, major dependency, messaging, public API or irreversible design changes. Preserve superseded ADRs and their consequences.


<a id="eng-arch-006"></a>

### ENG-ARCH-006 — Plan compatibility and reversibility

**Requirement:** MUST · **Review:** human · **Evidence:** Design delta and release/migration record

Identify current consumers, old workers/clients, stored records and configuration combinations that coexist during rollout. Define the compatibility window, migration sequence and rollback or forward-recovery path. Do not call a destructive data change reversible without a credible recovery design.


## Implementation guidance

A useful module boundary answers four questions: what it owns; what it exposes; what it may call; and what invariants it enforces. A folder called `services` is not an architecture by itself. Use framework-native conventions when they meet these requirements; do not impose six empty layers on a small application.

ADR naming: `ADR-0001-short-title.md`. Allocate IDs from the existing repository sequence. Statuses: proposed, accepted, superseded, rejected. An accepted ADR links the real approval; an agent-generated proposal starts proposed.
