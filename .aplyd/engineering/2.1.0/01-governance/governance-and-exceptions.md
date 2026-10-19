# Governance, applicability and exceptions


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Use this document to adopt, change and govern the standard without treating unfilled templates as approved organisational policy.


## Controls


<a id="eng-gov-001"></a>

### ENG-GOV-001 — Pin the applicable baseline

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Approved repository profile

Record package version, product-document baseline, repository purpose, maturity, relevant components and named owners in `.engineering/repository.json`. Approval must cite a real review record. The profile remains draft until reviewed.


<a id="eng-gov-002"></a>

### ENG-GOV-002 — Resolve conflicting instructions

**Requirement:** MUST · **Review:** human · **Evidence:** Decision or blocking issue

Record conflicts between requirements, design, local rules and this standard. Stop the conflicting or consequential work until its owner resolves it; continue independent safe work. Never resolve a conflict by deleting an upstream requirement.


<a id="eng-gov-003"></a>

### ENG-GOV-003 — Bind exceptions to scope and time

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Exception register and linked decision

Every mandatory-control deviation requires an exception ID, exact control IDs, scope, reason, risk, compensating controls, owner, independent approver, decision reference and expiry/review date. Expired or pending exceptions do not authorise work. N/A requires a genuine applicability explanation.


<a id="eng-gov-004"></a>

### ENG-GOV-004 — Waive the safety floor

**Requirement:** MUST NOT · **Review:** human · **Evidence:** Scope decision and PR

No exception can authorise fabricated evidence, unauthorised access, credential disclosure, undeclared transfer of protected data, concealment of material limitations or an operation outside granted authority. Change or disable the exposed feature instead.


<a id="eng-gov-005"></a>

### ENG-GOV-005 — Make technical debt actionable

**Requirement:** MUST · **Review:** human · **Evidence:** Debt register / Linear issue

Material debt must have its own identifier or tracked issue, affected component, cause, impact, owner, remediation condition and review date. Link a temporary shortcut to that issue where it occurs. An exception and a debt item serve different purposes; neither silently approves the other.


<a id="eng-gov-006"></a>

### ENG-GOV-006 — Protect the policy control plane

**Requirement:** MUST · **Review:** human · **Evidence:** Server setting evidence and independent review

Changes to package pins, command definitions, exception rules, CODEOWNERS, workflows, permissions, deployment authority and agent instructions require designated owner review. An author-editable boolean cannot prove these protections exist.


<a id="eng-gov-007"></a>

### ENG-GOV-007 — Ratchet legacy quality

**Requirement:** SHOULD · **Review:** human · **Evidence:** Adoption inventory and tracked remediation

Record the existing lint/type/dependency baseline before broad adoption. Block newly introduced problems; schedule existing debt separately. Do not mass-disable rules, exclude an entire codebase or mix broad formatting changes with functional work. Active safety-floor violations remain blocking.


<a id="eng-gov-008"></a>

### ENG-GOV-008 — Distinguish document and delivery states

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Traceability and release record

Use separate states for proposed design, implementation, engineering review, merged code, deployed artifact and product acceptance. No status transition is inferred solely from a generated document or successful build.


## Implementation guidance

### Approval routing
For C1, the code owner reviews implementation. For C2, include the affected architecture/API/data/security/AI owner. For C3, include the live-operation owner and required product/data authority. Record actual people or accountable groups in the profile. Do not appoint someone through a template.

### Exception lifecycle
`proposed → approved → expired / revoked / closed`. Only approved, unexpired exceptions can inform a human decision. The supplied audit validates their structure and rejects expiry; it **never automatically bypasses a failing engineering command**. A scanner-specific narrow exclusion needs the same reviewed decision and must remain visible in command evidence.

### Package updates
Publish a changed package with its changelog and checksum manifest. Open a separate adoption PR in each consumer, compare affected control IDs, assess existing exceptions and rerun local engineering checks. Preserve the prior package until rollback and audit needs are satisfied. Do not edit a vendored immutable version in place.
