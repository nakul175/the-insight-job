# Code conventions, typing and maintainability


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Use these rules across stacks; the repository profile supplies language-specific conventions and pinned tools.


## Controls


<a id="eng-code-001"></a>

### ENG-CODE-001 — Use consistent automated style

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Formatter/linter configuration and command evidence

Use one agreed formatter and linter configuration per language/workspace. Review changed rule settings as policy changes. Formatting-only changes must be separable from semantic changes. Do not add unexplained file-wide ignore directives.


<a id="eng-code-002"></a>

### ENG-CODE-002 — Name for domain meaning

**Requirement:** MUST · **Review:** human · **Evidence:** Code review

Use established language and repository conventions. Prefer explicit domain units and states, such as `timeout_ms`, `amount_minor` or `PendingApproval`, over ambiguous names. Do not encode secrets or personal identifiers in filenames. Conventional short loop variables are acceptable in narrow scope.


<a id="eng-code-003"></a>

### ENG-CODE-003 — Validate at runtime and preserve type guarantees

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Schemas, type configuration and command evidence

Use strict type checking where the stack supports it. Prefer unknown/validated unions over unrestricted types. Narrow unsafe casts at documented boundaries; prohibit unchecked model/JSON casts as a substitute for validation. Nullable, missing, zero and empty values must retain their distinct product meanings.


<a id="eng-code-004"></a>

### ENG-CODE-004 — Keep failures explicit and actionable

**Requirement:** MUST · **Review:** human · **Evidence:** Error contract and review

Represent expected errors using documented result/exception conventions. Catch only where useful action, translation or logging occurs; never swallow a failure or return synthetic success. Preserve a safe correlation reference without exposing internal stack traces or sensitive payloads to users.


<a id="eng-code-005"></a>

### ENG-CODE-005 — Keep functions cohesive

**Requirement:** SHOULD · **Review:** human · **Evidence:** PR rationale and maintainability review

Functions and modules should have one coherent responsibility. Treat deeply nested flow, duplicated policy and large mixed-purpose files as review signals, not a mandate for arbitrary splitting. A suggested review trigger is roughly 400 non-generated changed lines in one PR; justify larger coherent changes rather than gaming the count.


<a id="eng-code-006"></a>

### ENG-CODE-006 — Make configuration explicit

**Requirement:** MUST · **Review:** human · **Evidence:** Configuration inventory and startup code

Validate required configuration at startup or component activation. Document name, type, unit, safe default, secret classification and owning component. Fail closed for missing security-critical settings; distinguish optional disabled capability from a healthy configured integration.


<a id="eng-code-007"></a>

### ENG-CODE-007 — Handle numerical and temporal semantics deliberately

**Requirement:** MUST · **Review:** human · **Evidence:** Data dictionary and algorithm reference

Use explicit units, UTC timestamps for instants and named time zones for local scheduling. Do not confuse a date-only value with an instant. Use decimal or integer-minor-unit representations for money as required; define rounding/precision for scientific or programme calculations in the approved domain contract.


<a id="eng-code-008"></a>

### ENG-CODE-008 — Keep comments and generated code truthful

**Requirement:** MUST · **Review:** human · **Evidence:** Generator record and code review

Explain non-obvious constraints and decisions, not line-by-line narration. Remove obsolete comments and unreachable debug paths. Generated code must identify generator/version/source and regeneration command; change its source, not the generated result, unless an approved patch is explicitly recorded.


<a id="eng-code-009"></a>

### ENG-CODE-009 — Keep asynchronous state and resources bounded

**Requirement:** MUST · **Review:** human · **Evidence:** Implementation and operational review

Close files/connections, propagate cancellation where appropriate, avoid unbounded queues and uncontrolled parallelism, and define ownership of shared mutable state. Do not introduce a background task whose failures cannot be observed.


<a id="eng-code-010"></a>

### ENG-CODE-010 — Do not fake feature completion

**Requirement:** MUST · **Review:** human · **Evidence:** Feature state, configuration and issue

Do not expose inactive buttons, in-memory-only persistence, simulated inference or mock partner responses as completed production functionality. Mark bounded examples explicitly and isolate them from production paths. A temporary stub must have a tracked owner and must not silently activate in a live environment.


## Implementation guidance

| Concern | TypeScript default | Python default |
|---|---|---|
| Local variables/functions | camelCase | snake_case |
| Types/classes | PascalCase | PascalCase |
| Boolean names | `is/has/can/should` where meaningful | `is_/has_/can_/should_` where meaningful |
| Constants | Follow ecosystem; UPPER_SNAKE_CASE for true shared constants | UPPER_SNAKE_CASE for module constants |
| Error input | `unknown` then narrow | Specific exception class and safe context |
| Public contracts | Explicit types plus runtime schema | Typed models/functions plus runtime validation |

Use an existing established file naming convention, including framework route conventions. Do not rename an entire repository to match this table. Tool recipes live in `06-enforcement/profiles/`; they are not authorisation to install packages or change frameworks.
