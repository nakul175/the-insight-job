# Backend workflows, jobs and state transitions


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Make business transitions and asynchronous effects durable, bounded and observable.


## Controls


<a id="eng-be-001"></a>

### ENG-BE-001 — Centralise consequential workflow rules

**Requirement:** MUST · **Review:** human · **Evidence:** State machine and implementation

Implement state transitions through a clear application/domain boundary. Validate current state, actor authority, version/concurrency conditions and permitted transition together. Do not allow generic update endpoints to bypass workflow restrictions.


<a id="eng-be-002"></a>

### ENG-BE-002 — Define transaction scope

**Requirement:** MUST · **Review:** human · **Evidence:** LLD transaction contract

Keep transactions as short as practical and avoid holding database locks during remote calls. Specify atomic writes, after-commit effects, failure recovery and external consistency. A successful local write is not proof that the partner accepted the operation.


<a id="eng-be-003"></a>

### ENG-BE-003 — Design jobs for duplicate delivery

**Requirement:** MUST · **Review:** human · **Evidence:** Job contract and implementation

Assume a job may be delivered or executed more than once unless the complete system contract proves otherwise. Include stable identifiers, scoped idempotency, bounded retries with backoff, safe lease/heartbeat behaviour, dead-letter handling and operator recovery.


<a id="eng-be-004"></a>

### ENG-BE-004 — Bound work and propagate cancellation

**Requirement:** MUST · **Review:** human · **Evidence:** Workflow budget and code

Limit batch size, concurrency, execution time and resource use. Define cancellation effects and partial progress. Avoid synchronous blocking work on event loops or user requests when it compromises the documented behaviour.


<a id="eng-be-005"></a>

### ENG-BE-005 — Revalidate authority at delayed execution

**Requirement:** MUST · **Review:** human · **Evidence:** Worker permission path

Workers must check current permissions, data-use grants and target state before a consequential action. Bind approvals to specific action parameters and expire them appropriately. A queued request is not perpetual permission to act.


<a id="eng-be-006"></a>

### ENG-BE-006 — Use service lifecycle controls

**Requirement:** MUST · **Review:** human · **Evidence:** Runtime configuration and runbook

Implement graceful startup/shutdown, dependency cleanup and honest health/readiness states. Do not mark a process ready before critical configuration and dependencies meet the chosen operational contract. Keep optional degraded capabilities visible.


## Implementation guidance

A job record should include ID, tenant/actor context, authorised action, input reference, schema version, status, attempt count, next retry, lease/heartbeat, outcome reference, error category and correlation ID. Retain only necessary data and do not store live credentials in the payload.

Retries need a maximum elapsed budget, not only a maximum count. Poison jobs should become operator-visible rather than consuming capacity indefinitely.
