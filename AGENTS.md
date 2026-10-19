# APLYD combined repository router

**Router contract:** v2 · This file is the single root composition point for an adopting repository.

Read `.engineering/repository.json` first. In combined mode, read its `integration_manifest` and verify its pinned sources before implementing product scope. Read only the task-relevant native product records, QA rules and engineering sections. If editing this distribution itself rather than an adopting repo, read `CODING_STANDARD.md`, `REVIEW_DISPOSITION.md` and `08-package-tests/README.md`; no product-repository profile is implied.

| Decision | Authoritative source |
|---|---|
| Product meaning, priorities, stories, criteria and canonical task ID | Approved native product baseline selected in the integration manifest |
| Required verification, case/run semantics and release quality gates | Approved native QA baseline and its actual checker invocations |
| Architecture, code, technical change discipline and safe tool execution | Pinned `CODING_STANDARD.md` and `04-agent-instructions/engineering-agent-rules.md` |
| Team Done and delivery stage | One product-team checklist referenced by `team_dod_ref`; engineering and QA are required subordinate checklists |
| Exceptions | One register selected by `exception_register`; approvals route by domain, not by the implementer's preference |
| Local module mechanics | Applicable repository rules that do not weaken the above obligations |

These are separate authorities, not `product > QA > engineering` override levels. Approved scope does not waive security, privacy, QA gates or tool permissions. Tool/platform instructions and actual granted permissions remain binding. Other agent loaders can have their own scope rules; inspect the applicable local instruction chain rather than assuming every AGENTS.md in every folder loads.

**Stop rule:** stop the affected action and record a blocker when required authority, a native binding, a required gate, scope or an instruction conflict is unresolved. Continue only independent authorised work. Never resolve a conflict by silently selecting a weaker control, modifying approvals, fabricating IDs or changing a failing command into a no-op.

Use the canonical product technical-task key; tracker references are links, not a second task. Reuse native story, criterion, case and run IDs. Use UUIDs only for genuinely new locally owned records; never renumber an external bundle. Keep proposed, implemented, reviewed, merged, deployed and accepted separate.

Inspect the working tree before editing. Preserve others' changes. Classify C0–C3 and plan proportionately. Execute only reviewed commands with explicit authority. Missing tools, zero executed tests, missing native QA checks and absent reports are not passes. A green engineering job alone is not Done. Do not merge, deploy, create paid resources or transfer protected data merely because a coding task was assigned. Finish with actual revision, evidence, limits and persistent handover.

## Native bindings in v2.1

Use the engineering-owned connection helper for the reported PM/QA shapes. Product owns any missing typed task-criterion links; never infer them from `source_criteria` prose. Preserve case/criterion versions and full Git revisions. Proposals and normalized previews are not approvals or authoritative ledgers. Stop for unresolved owner decisions, mismatched versions or a nonempty unmigrated v1 exception register. See `01-governance/native-bundle-adapters.md` in the distribution or its hash-pinned installed core.
