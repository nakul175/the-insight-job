# Database, data integrity and migrations


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Treat data changes as lifecycle changes, not only schema edits. Use the existing LLD/data dictionary as the authoritative contract.


## Controls


<a id="eng-data-001"></a>

### ENG-DATA-001 — Enforce critical invariants

**Requirement:** MUST · **Review:** human · **Evidence:** Schema and invariant mapping

Use database constraints and transactions for enforceable uniqueness, referential integrity and critical state invariants. Include tenant keys where required. Document any invariant that cannot be enforced in storage and the concurrency mechanism that protects it.


<a id="eng-data-002"></a>

### ENG-DATA-002 — Version all schema changes

**Requirement:** MUST · **Review:** human · **Evidence:** Migration IDs and applied-state record

Use the established migration system with ordered, immutable applied migrations. Do not edit an already applied production migration or rely on unrecorded console changes. Reconcile emergency manual changes into version control promptly through the incident/change process.


<a id="eng-data-003"></a>

### ENG-DATA-003 — Plan safe migration sequencing

**Requirement:** MUST · **Review:** human · **Evidence:** Migration plan and approved rollout

For risky changes, document expand/backfill/switch/contract stages, compatibility with old application versions, locks, expected runtime, batching, checkpoints and stop conditions. Do not make a column mandatory before old writers and existing rows are compatible.


<a id="eng-data-004"></a>

### ENG-DATA-004 — Define recovery without false rollback promises

**Requirement:** MUST · **Review:** human · **Evidence:** Migration/release record

Identify data-loss risk, backup/snapshot authority, restore point, forward repair and the point after which simple application rollback is unsafe. A down migration that drops new data is not a complete recovery plan. Destructive execution requires explicit operational approval.


<a id="eng-data-005"></a>

### ENG-DATA-005 — Handle concurrency and durable side effects

**Requirement:** MUST · **Review:** human · **Evidence:** LLD transaction/event contract

Choose transactions, optimistic version checks, locks or another justified strategy for concurrent changes. Define isolation assumptions and conflict responses. When data change and external message must be coordinated, use a durable outbox/inbox or equivalent rather than an unprotected dual write.


<a id="eng-data-006"></a>

### ENG-DATA-006 — Prevent uncontrolled data access and growth

**Requirement:** MUST · **Review:** human · **Evidence:** Data/query design and retention record

Use reviewed indexes, bounded query patterns, tenant-scoped access and pagination. Avoid N+1 queries and unbounded table loads. Define lifecycle ownership for blobs, temporary records, exports and orphan cleanup.


<a id="eng-data-007"></a>

### ENG-DATA-007 — Preserve deletion and access state through restore

**Requirement:** MUST · **Review:** human · **Evidence:** Recovery and data lifecycle design

Specify deletion propagation across primary storage, derived indexes, caches, exports and model retrieval. Define how current revocations/deletion instructions are reconciled after a restore; restoring a backup must not quietly revive access or forbidden retention.


## Implementation guidance

Backfills must be resumable and safe to repeat, with a deterministic selection rule and recorded progress. Keep public API changes, migration versions and artifact versions linked. Production data must not be copied into developer laptops or prompts as a convenient debugging shortcut.

Prefer additive deployment first. Use a separate approved change for eventual deletion of old fields/tables after the compatibility window. A database engine change requires an explicit ADR and compatibility assessment, not a dependency swap disguised as refactoring.
