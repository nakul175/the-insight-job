# Reliability, observability and operational change


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Derive targets from approved product needs and environment. Do not invent universal latency, availability or recovery thresholds.


## Controls


<a id="eng-ops-001"></a>

### ENG-OPS-001 — Define meaningful operational signals

**Requirement:** MUST · **Review:** human · **Evidence:** Operational contract and instrumentation

Provide structured logs, metrics and correlation across user request, job and external call. Separate liveness from readiness, define service-level signals and actionable alert ownership. Bound metric-cardinality and avoid sensitive identifiers in labels.


<a id="eng-ops-002"></a>

### ENG-OPS-002 — Bound external failures

**Requirement:** MUST · **Review:** human · **Evidence:** Integration policy and code

Set explicit connect/read/overall timeouts, bounded retries and appropriate backoff. Use circuit breaking, concurrency limits or other containment where the failure pattern warrants it. Prevent retry amplification across layers and never silently retry a consequential non-idempotent write.


<a id="eng-ops-003"></a>

### ENG-OPS-003 — Implement approved resource and performance budgets

**Requirement:** MUST · **Review:** human · **Evidence:** Budget reference and implementation rationale

Record applicable workload, request sizes, concurrency, data growth, cost and latency budgets from the product baseline. Address expensive queries, N+1 calls, repeated inference and unbounded memory. A measured exception needs explicit owner approval; do not lower the target in code documentation.


<a id="eng-ops-004"></a>

### ENG-OPS-004 — Treat caching as part of correctness

**Requirement:** MUST · **Review:** human · **Evidence:** Cache policy and implementation

Define cache keys including security scope, lifetime, invalidation, stale behaviour and stampede protection as relevant. Never use a shared cache to bypass permissions, deletion or contractual freshness. Document whether a fallback is stale data rather than new computation.


<a id="eng-ops-005"></a>

### ENG-OPS-005 — Make deployment attributable

**Requirement:** MUST · **Review:** human · **Evidence:** Release/deployment record

Record source revision, build/artifact digest, dependency/configuration versions, migration state, target environment, operator/approval and deployment outcome. Check the actual running revision before claiming rollout; a GitHub merge or successful build alone is not deployment evidence.


<a id="eng-ops-006"></a>

### ENG-OPS-006 — Define containment and recovery

**Requirement:** MUST · **Review:** human · **Evidence:** Runbook and release record

For operationally critical changes, document feature disablement, rollback/forward recovery, backup authority, recovery points and runbook ownership. Identify which irreversible side effects remain after rollback and how current access/deletion policy is restored.


<a id="eng-ops-007"></a>

### ENG-OPS-007 — Separate environment authority

**Requirement:** MUST · **Review:** human · **Evidence:** Environment policy and approval

Keep development, staging and production identities/data/configuration distinct. No production mutation, public deployment, deletion or migration is implied by a coding task. Use explicit authorised operation scope and protected environments for live work.


## Implementation guidance

A useful failure log includes event name, severity, service/component version, trace/correlation ID, non-sensitive scope reference and stable error class. It should not include a full request body by default. Alerts require an actionable condition, owner and response reference rather than a stream of low-value notifications.

Recovery statements are engineering plans until execution evidence and the separate operational decision exist. This document does not prescribe QA exercises.
