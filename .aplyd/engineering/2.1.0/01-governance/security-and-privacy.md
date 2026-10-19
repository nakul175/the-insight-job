# Security and privacy engineering


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Apply controls to actual data and trust boundaries, including demos, exports, background workers, search and AI retrieval. This is an implementation baseline, not a security certification or jurisdiction-specific legal opinion.


## Controls


<a id="eng-sec-001"></a>

### ENG-SEC-001 — Enforce identity and authority at every boundary

**Requirement:** MUST · **Review:** human · **Evidence:** Access model, service/worker code and review

Authenticate with established mechanisms and enforce authorisation on the server for every protected operation and resource. Derive tenant context from authenticated grants, not a client-supplied tenant identifier alone. Recheck permissions when queued work executes or protected output is delivered.


<a id="eng-sec-002"></a>

### ENG-SEC-002 — Preserve tenant isolation throughout the system

**Requirement:** MUST · **Review:** human · **Evidence:** Trust-boundary design and code review

Carry authorised tenant scope into database access, caches, object keys, job queues, search indexes, exports, metrics access and retrieval contexts. Include tenant scope in cache/idempotency keys. Privileged cross-tenant operations require a separate explicit permission and audit path.


<a id="eng-sec-003"></a>

### ENG-SEC-003 — Keep secrets outside source and diagnostics

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Secret configuration, scanner evidence and incident reference

Use approved secret injection; do not put live credentials in repositories, frontend bundles, URLs, prompts or logs. Examples contain names and empty values only. If a secret is exposed, stop its use and follow revocation/rotation and incident handling; deleting a line is not sufficient.


<a id="eng-sec-004"></a>

### ENG-SEC-004 — Validate untrusted inputs and encode outputs

**Requirement:** MUST · **Review:** human · **Evidence:** Boundary contracts and implementation

Validate shape, type, size, range and permitted values at ingress. Use parameterised database operations and destination-appropriate output encoding. Do not construct shell commands, SQL identifiers or rendered HTML from raw user/model text. Map allowed dynamic choices explicitly.


<a id="eng-sec-005"></a>

### ENG-SEC-005 — Constrain outbound connections and uploads

**Requirement:** MUST · **Review:** human · **Evidence:** Integration and upload design

Use destination allowlists or equivalent egress policy for user/model-selected URLs; handle redirects and private-network resolution consistently. Validate upload limits, actual media type, archive expansion and object access. Keep uploads outside executable paths; malware processing and quarantine follow the product threat model.


<a id="eng-sec-006"></a>

### ENG-SEC-006 — Protect sessions and cryptographic operations

**Requirement:** MUST · **Review:** human · **Evidence:** Identity design and configuration

Use maintained identity/cryptographic libraries, secure transport and approved key custody. Specify cookie scope, secure/HTTP-only flags, CSRF controls where cookies authenticate actions, session expiry, rotation and revocation. Do not equate CORS with access control or disable certificate validation to make an integration work.


<a id="eng-sec-007"></a>

### ENG-SEC-007 — Minimise and govern sensitive data

**Requirement:** MUST · **Review:** human · **Evidence:** Data-flow/retention record and permission reference

Document necessary fields, permitted purpose, retention, residency, deletion, backup treatment and recipients. Use synthetic data by default in development and demos. Transmit real protected data only under recorded authority and an approved destination policy, including AI services. Do not infer permission from technical access.


<a id="eng-sec-008"></a>

### ENG-SEC-008 — Keep audit evidence separate from operational noise

**Requirement:** MUST · **Review:** human · **Evidence:** Audit contract and implementation

Record actor, authorised scope, action, target, time, outcome and correlation reference for consequential changes. Protect audit access and integrity; avoid raw sensitive payloads. If audit delivery is part of the operation invariant, use a durable design and define failure handling instead of silently losing the event.


<a id="eng-sec-009"></a>

### ENG-SEC-009 — Review changed attack surfaces

**Requirement:** MUST · **Review:** human · **Evidence:** Technical design delta and security review

Changes to authentication, roles, data exposure, imports, file rendering, remote execution, webhooks or model tools require an updated threat assessment and affected owner review. Record the mitigation in the actual implementation, not just a checklist assertion.


<a id="eng-sec-010"></a>

### ENG-SEC-010 — Make administrative access bounded

**Requirement:** MUST · **Review:** human · **Evidence:** Admin design and deployment configuration

Use least privilege, short-lived or bounded elevation where supported, accountable operators and explicit scope. Do not add a hidden universal admin account, bypass header, debug endpoint or hardcoded credential for convenience.


## Implementation guidance

Security review follows data flow, not just route handlers: browser → API → job → storage → export → external system. A denied screen does not establish denied API access. Encryption does not substitute for permissions, retention or safe keys.

Do not copy whole jurisdiction-specific regulations into code policy. The product's qualified compliance owner supplies applicable obligations and their versions; engineering links implementation to that approved mapping.


## Reference basis

S01 (OWASP ASVS) provides a reference framework; the specific controls above are APLYD policy choices. S06 informs untrusted automation handling. See the package [source register](../SOURCES.md). These references do not establish product certification.
