# API and integration engineering


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Specify contracts at system boundaries, including independently owned partner systems. A working HTTP response does not establish permission, correctness or interoperability.


## Controls


<a id="eng-api-001"></a>

### ENG-API-001 — Maintain an explicit contract

**Requirement:** MUST · **Review:** human · **Evidence:** Versioned interface contract

Every consumed or exposed API/event has documented request/response schemas, identity, tenant scope, validation, error semantics and versioning. Use OpenAPI for REST where suitable, or the native schema/IDL for another protocol. Keep code and contract changes in the same reviewed change set.


<a id="eng-api-002"></a>

### ENG-API-002 — Bound requests and collections

**Requirement:** MUST · **Review:** human · **Evidence:** API contract and implementation

Define payload/file sizes, pagination bounds, stable ordering, filter/sort allowlists and rate/concurrency limits appropriate to product needs. Avoid unlimited list endpoints. Document cursor expiry and consistency behaviour where relevant.


<a id="eng-api-003"></a>

### ENG-API-003 — Use consistent error semantics

**Requirement:** MUST · **Review:** human · **Evidence:** Error schema and mapping

Use the existing error contract or a reviewed standard such as RFC 9457. Keep HTTP/transport status, stable machine code and user-safe detail consistent. Do not use HTTP success to conceal an operation failure or leak resource existence contrary to access policy.


<a id="eng-api-004"></a>

### ENG-API-004 — Design duplicate and retry behaviour

**Requirement:** MUST · **Review:** human · **Evidence:** Idempotency contract and implementation

For consequential operations, define tenant/actor-scoped idempotency keys, payload binding, in-progress response, replay retention and conflict behaviour. Do not retry non-idempotent partner writes unless the contract supports safe deduplication or reconciliation.


<a id="eng-api-005"></a>

### ENG-API-005 — Authenticate and reconcile webhooks

**Requirement:** MUST · **Review:** human · **Evidence:** Webhook contract and worker design

Verify origin using the documented signature/authentication scheme and current keys. Check replay/time bounds where supported, deduplicate event IDs, handle out-of-order events and record delivery acknowledgement only after the agreed durable handoff.


<a id="eng-api-006"></a>

### ENG-API-006 — Make connector limitations visible

**Requirement:** MUST · **Review:** human · **Evidence:** Integration contract and operational record

Record partner owner, endpoint/version, auth scopes, data rights, field mapping, rate limits, timeout/retry policy, availability limitations and error recovery. Distinguish not-connected, degraded, stale, denied and simulated states. Store credential references, not credentials.


<a id="eng-api-007"></a>

### ENG-API-007 — Control breaking changes

**Requirement:** MUST · **Review:** human · **Evidence:** API change record and owner review

A removed field, narrower validation, changed enumeration or permission rule may break consumers even if the endpoint path is unchanged. Identify consumers and deployment sequence; publish compatibility/deprecation decisions and rollback constraints.


## Implementation guidance

For an independent AI provider exposed through an orchestration product, the contract should bind tenant and user authority to the request, use a versioned input/output schema, record model provenance, define asynchronous status, distinguish screening output from diagnosis, and specify who retains/deletes source data. Do not assume shared databases, shared admin credentials or common organisational permission.

Example safe error shape: `type`, `title`, `status`, a non-sensitive `instance` or correlation reference, and a stable application `code`. Use your established schema consistently; do not retrofit a new error standard during unrelated maintenance.


## Reference basis

S03 documents RFC 9457. The remaining provisions are proposed APLYD engineering policy. See the package [source register](../SOURCES.md). These references do not establish product certification.
