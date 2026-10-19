# Traceability, agent execution and review controls


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

These controls make the workflow and agent protocol enforceable review requirements. They do not claim that local scripts authenticate decisions or remote state.


## Controls


<a id="eng-trace-001"></a>

### ENG-TRACE-001 — Link authorised work to implementation

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Traceability ledger and PR

Each material change links the actual work item, approved requirements or engineering authorisation, baseline, affected design, implementation paths and real branch/PR references as they are created. Keep upstream IDs stable and partial scope explicit.


<a id="eng-trace-002"></a>

### ENG-TRACE-002 — Preserve upstream authority and history

**Requirement:** MUST · **Review:** human · **Evidence:** Canonical docs and review

Keep one canonical BRD/FSD/HLD/LLD or approved equivalent. Update affected documents through review without changing requirement meaning to match incomplete code. Preserve superseded baselines and separately identify implementation commentary.


<a id="eng-trace-003"></a>

### ENG-TRACE-003 — Validate delivery-state transitions

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Ledger plus real decision/execution references

Reviewed states require actual review/revision references; merged states require an observed merge; deployed states require observed artifact/environment evidence. No agent may populate future-stage fields with invented identifiers. The local ledger audit checks prerequisites, not remote truth.


<a id="eng-agent-001"></a>

### ENG-AGENT-001 — Inspect before changing

**Requirement:** MUST · **Review:** human · **Evidence:** Plan and handover

Read applicable instructions/profile, current requirements/design, working tree and relevant implementation. Detect conflicts and drift. Protect other contributors work and identify the task boundary before editing.


<a id="eng-agent-002"></a>

### ENG-AGENT-002 — Plan proportionately and control scope

**Requirement:** MUST · **Review:** human · **Evidence:** Plan, ADR and PR

Use a compact plan for bounded work and an approved design delta for material change. Do not invent features, replace the stack, add paid tools or combine unrelated refactors without the corresponding scope/design/budget authority.


<a id="eng-agent-003"></a>

### ENG-AGENT-003 — Record actual execution and limitations

**Requirement:** MUST · **Review:** human · **Evidence:** Command evidence, PR and handover

Report commands, revisions, results, blocked dependencies, simulated behaviour and deployment state accurately. Do not turn unavailable tools or missing evidence into successful completion.


<a id="eng-agent-004"></a>

### ENG-AGENT-004 — Persist transferable context

**Requirement:** MUST · **Review:** human · **Evidence:** Handover record

At task stop, material blocker or agent switch, record objective/authority, baseline, branch/HEAD/dirty state, real references, implemented scope, decisions, command outcomes, gaps and next action in a durable handover. The receiver re-inspects current state.


<a id="eng-git-001"></a>

### ENG-GIT-001 — Use focused reviewed changes

**Requirement:** MUST · **Review:** human · **Evidence:** PR and change record

Submit material changes through a PR or an explicitly authorised equivalent change process. Link the real work item, use the required checklist and preserve unrelated work. Do not force-push shared history or discard work without explicit authority.


<a id="eng-git-002"></a>

### ENG-GIT-002 — Require accountable independent review

**Requirement:** MUST · **Review:** human · **Evidence:** Actual review and owner decision

Apply designated owner review to the actual change revision. AI/self-review does not replace independent accountable approval. A materially changed revision needs renewed review under the repository policy. Record a genuine staffing exception where permitted, never a fictional reviewer.


<a id="eng-git-003"></a>

### ENG-GIT-003 — Enforce gates outside author-only control

**Requirement:** MUST · **Review:** human · **Evidence:** Actual server settings and review evidence

Protect relevant branches, checks, ownership, policy files and workflow changes using server-side controls appropriate to the repository. Do not claim these settings exist from local files alone. Preserve all applicable independent product gates.


<a id="eng-git-004"></a>

### ENG-GIT-004 — Separate merge and live-operation permission

**Requirement:** MUST · **Review:** human · **Evidence:** Actual authorisation and operation record

A coding request or passing check does not authorise merge, public deployment, live migration, destructive cleanup, permission change or external data transfer. Record and enforce the granted operation scope.


## Implementation guidance

The [Linear workflow](../05-workflows/linear-github-workflow.md), [agent protocol](../04-agent-instructions/task-execution.md) and [PR checklist](../PR_CHECKLIST.md) specify operational steps for these controls. A single coherent record may satisfy several controls when its evidence actually addresses each one.
