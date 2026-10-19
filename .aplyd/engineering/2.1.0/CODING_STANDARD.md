# APLYD Coding & Engineering Standard

**ID:** APLYD-ENG-001 · **Version:** 2.1.0 · **Scope:** all adopting software repositories and their human or AI contributors.

## 1. What this standard governs

This is the controlling entry point for a reusable engineering package. It governs how approved product behaviour becomes maintainable code, how engineering decisions are recorded, how changes are reviewed, and how unfinished work remains transferable. The detailed controls in `01-governance/` and `02-architecture/` are normative. Templates, examples and stack recipes are implementation aids, not competing product specifications.

It does **not** define a test strategy, test scenarios, test cases, a QA process, clinical validation, or product acceptance. Existing controls in those areas remain intact. Engineering approval does not imply release approval.

## Cross-bundle authority

The [cross-bundle contract](01-governance/cross-bundle-contract.md) is normative. It defines the canonical task key, hash-bound native adapters, one root router, shared register, conjunctive team Done and required execution/native QA gates. Product and QA details remain in their actual upstream bundles. Tests of the package utilities do not constitute a new product QA standard.

## 2. Normative language and precedence

**MUST / MUST NOT** are mandatory. **SHOULD / SHOULD NOT** require a documented reason when not followed. **MAY** is optional. A file called `AGENTS.md` is an instruction document, not a permission grant or a security boundary.

Applicable legal, contractual, security and data-use obligations remain binding. Within engineering decisions, use the approved product baseline for intended behaviour; this package for engineering constraints; approved HLD/LLD/ADRs for design; and repository conventions for local mechanics. A conflict is an explicit decision to resolve, not an excuse to silently choose the easiest rule. Agent platform instructions and tool permissions are not overridden by repository text. Specific local instructions may tighten this package but cannot quietly weaken mandatory controls.

The repository profile pins this distribution. Do not assume a newer centrally published version has already been adopted. Do not lower a product nonfunctional requirement to accommodate a code limitation.

## 3. Three deployment contexts, one safety floor

| Context | Permitted simplification | Controls that remain mandatory |
|---|---|---|
| Isolated prototype | One concise design record; manual non-production operation; reduced operational automation | No secrets in code; no unauthorised real data; honest simulated behaviour; explicit authority; traceable changes |
| Pilot | Bounded users/data/integrations; approved operational limits | Tenant/access boundaries, recoverability, ownership, privacy, review and exposure controls |
| Production | Full applicable engineering and operational controls | All applicable controls plus independent release prerequisites outside this package |

A public demonstration, sensitive-data prototype, or externally connected pilot does not qualify for reduced controls merely because someone calls it a demo. Evaluate the actual exposure. Record applicability and design depth in the repository profile. Never treat `not_applicable` as a waiver of relevant behaviour.

## 4. Change classes and evidence depth

| Class | Trigger | Minimum engineering records |
|---|---|---|
| C0: editorial | No executable or contractual behaviour changes | Issue/authorisation, focused PR and documentation rationale |
| C1: bounded implementation | Existing approved design; local behaviour change | Requirement/task link, compact implementation plan, PR, command evidence and handover when unfinished |
| C2: material design | API/schema/permission/model/provider/dependency or multi-module change | C1 plus technical design delta, relevant ADR, migration/integration/security implications and owner review |
| C3: consequential operation | Production access, destructive change, sensitive-data transfer, external action or release | C2 as applicable plus explicit operation authority, operator, recovery/containment plan and separate release decision |

A small diff can be C3. File count is not risk. Minor changes can record the plan in the PR instead of creating a separate document.

## 5. Control ownership

Product owner controls meaning, priority and release scope. Engineering owner controls architecture, code conventions and technical exceptions. Security/privacy owner controls security and data-risk decisions. Operations/release owner controls live changes. The implementer supplies evidence; a named accountable reviewer approves it. An AI agent cannot approve its own exception, create fictional approval, or equate a generated review with human authority.

One person may hold several roles in a small team, but authors cannot manufacture independent review. Record any temporary staffing exception explicitly; preserve non-waivable controls. The package supplies role requirements, not invented names or appointments.

## 6. Mandatory operating sequence

Read the upstream baseline and repository instructions. Inspect the actual implementation and working tree. Link the authorised task. Identify risk and prepare the proportionate plan. Implement the smallest coherent change. Run the applicable, reviewed engineering commands. Review the complete diff and affected boundaries. Update only the authoritative documents and traceability records. Submit a PR with evidence and limitations. Obtain required review. Hand over unresolved work. Merge and deploy only within explicit authority.

Use the [agent task protocol](04-agent-instructions/task-execution.md), [workflow](05-workflows/linear-github-workflow.md), [PR checklist](PR_CHECKLIST.md) and [engineering completion definition](01-governance/engineering-definition-of-done.md).

## 7. Evidence and enforcement

`06-enforcement/controls.json` indexes each control's source, evidence and review method. `engctl.py audit` checks repository configuration, required paths, record structure, stage prerequisites and expiry dates. `engctl.py run` executes explicitly configured engineering commands and records their exit states. These utilities do not decide whether code satisfies a business requirement, authenticate an approver, query GitHub settings, or certify application security.

A configured control is not an executed control. A zero exit code from one command is not compliance with the whole standard. Missing tools, missing configuration, stale exceptions, unavailable scanners and aborted commands are not passes. Any check able to change the gate configuration must have protected review outside the author-controlled branch.

## 8. Authoritative document map

| Area | Controlling document |
|---|---|
| Governance and exceptions | [Governance](01-governance/governance-and-exceptions.md) |
| Traceability, agents and review | [Workflow controls](01-governance/engineering-workflow-controls.md) |
| Product-document interface | [Product bridge](01-governance/product-documentation-bridge.md) |
| Security and privacy | [Security](01-governance/security-and-privacy.md) |
| Architecture | [Architecture](02-architecture/architecture-standard.md) |
| Code conventions | [Code](02-architecture/code-conventions.md) |
| API and external exchange | [API](02-architecture/api-integration-standard.md) |
| Persistence and migrations | [Data](02-architecture/database-migration-standard.md) |
| Frontend | [Frontend](02-architecture/frontend-standard.md) |
| Backend and jobs | [Backend](02-architecture/backend-standard.md) |
| AI and model integration | [AI](02-architecture/ai-engineering-standard.md) |
| Operations and performance | [Operations](02-architecture/reliability-operations-standard.md) |
| Dependency and build supply chain | [Supply chain](02-architecture/dependency-supply-chain-standard.md) |

## 9. Adoption boundary

The package is a finished reusable baseline; a target repository still needs real owners, real upstream paths, actual commands, recorded approvals and server-side protection. The starter profile deliberately begins in `draft`. It must not pass enforced configuration review until those inputs are supplied. Installation does not merge work, enable access, purchase tools, create API keys or authorise production actions.
