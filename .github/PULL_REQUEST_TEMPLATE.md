# Pull request: engineering change record


## Cross-bundle identity and authority
**Canonical technical task ID:**  
**Tracker reference (Linear/GitHub/Jira/local):**  
**Story IDs:**  
**Acceptance-criterion IDs:**  
**QA case IDs:**  
**QA run IDs and exact source revision (or explicit NOT RUN):**  
**Product/QA catalogue versions and hashes:**  
**Primary team Definition of Done / workflow / release-record reference:**  
**Selected shared exception IDs:**  
Engineering-only work records an explicit reason for absent story/criterion/case links. Never create duplicate native tasks or fictional cases/runs. The release section is an evidence attachment to the primary PM release record, not an alternative approval.

<!-- Replace instructional text. Do not mark boxes based on intention. This is an engineering checklist, not a substitute for other existing product gates. -->

## Identity and scope
**Linear/work item:**  
**Change class:** C0 / C1 / C2 / C3  
**Requirement/design references and baseline:**  
**Canonical task key (same task, not a second ENG ID):**  
**Package version:** 2.1.0  
**Authorised outcome and non-goals:**

## What changed and why
Describe the actual change, affected modules and user/operational behaviour. Link the implementation plan and ADR/design delta where needed. Explain unexpected scope and large diffs. Clearly identify simulated or incomplete functionality.

## Impact
| Area | Changed? | Concrete impact / N/A reason / reference |
|---|---|---|
| Architecture and module boundaries | | |
| API/events and existing consumers | | |
| Database/schema/backfill | | |
| Permissions, tenancy and sensitive data | | |
| Frontend states/accessibility/localisation | | |
| Models/prompts/tools/providers | | |
| Dependencies/licences/toolchain | | |
| Configuration/feature flags | | |
| Performance/cost/observability | | |
| Rollout, rollback and irreversible effects | | |

## Engineering evidence
| Command/check | Exact revision and environment | Outcome | Evidence reference / limitation |
|---|---|---|---|
| Formatting | | NOT RUN | |
| Lint/static analysis | | NOT RUN | |
| Types | | NOT RUN | |
| Build/package | | NOT RUN | |
| Dependencies | | NOT RUN | |
| Secrets | | NOT RUN | |
| Actual product tests + fresh nonzero report | | NOT RUN | |
| Native QA lint gate | | NOT RUN | |
| Native QA release gate, where applicable | | NOT RUN | |

Use PASS, FAIL, BLOCKED, NOT RUN or approved N/A accurately. List actual commands, not invented generic scripts. Do not replace a failing command with a no-op or suppress output to force a green gate. Preserve existing independent required checks without redefining them here.

## Review checklist
- [ ] I read current repository instructions and preserved unrelated work.
- [ ] Scope and behaviour match the approved baseline; divergences have explicit decisions.
- [ ] Applicable numbered engineering controls have concrete implementation/review evidence.
- [ ] Required contracts, canonical docs and traceability are updated, or the no-change rationale is recorded.
- [ ] No live secrets/protected data are added; permissions and tenant boundaries are enforced where affected.
- [ ] Dependency/configuration/migration/AI/operational impacts are explicit.
- [ ] Known limitations, debt and exceptions are visible with real owners/references.
- [ ] A handover is present when work is incomplete or another agent must continue.

## Review and authority
**Required accountable reviewers:**  
**Actual independent review references:**  
**Open blocking findings / exceptions:**  
**Merge authority:** not implied by this PR  
**Deployment authority:** not implied by merge  
**Handover / next bounded action:**

## Native revision references (v2.1)

Record the native task ID, resolved story IDs, typed criterion IDs, PM source/catalogue hashes, exact QA case IDs **and versions**, QA run IDs and full Git revision. Use `qa_case_refs: [{"id": "CASE-ID", "version": "CASE-VERSION"}]` alongside the ID list in machine records. Preserve criterion revisions from native QA links. Reference the approved product-owned task-link extension only when the native task lacks a structured list. Unbound prose is not scope approval, and a passing older case revision does not satisfy a selected new revision.
