# Frontend, accessibility and offline interaction


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Use the approved product experience and design system. Do not treat a visually plausible screen as implemented workflow.


## Controls


<a id="eng-fe-001"></a>

### ENG-FE-001 — Use coherent shared UI patterns

**Requirement:** MUST · **Review:** human · **Evidence:** UI component map and PR

Follow the existing design system and framework conventions. Reuse form, navigation, error and feedback components where meaning is shared. Do not replace a router, CSS system or component library without an architectural decision and migration scope.


<a id="eng-fe-002"></a>

### ENG-FE-002 — Implement all relevant interaction states

**Requirement:** MUST · **Review:** human · **Evidence:** FSD state mapping and implementation

For each changed workflow, implement loading, empty, partial, error, success, denied, stale and interrupted states as applicable. Prevent accidental duplicate submissions; preserve recoverable user work. Disabled operations need a truthful reason, not an apparently functional placeholder.


<a id="eng-fe-003"></a>

### ENG-FE-003 — Keep authorisation and secrets off the client boundary

**Requirement:** MUST · **Review:** human · **Evidence:** Client/server data review

UI checks may improve usability but must not be the only access control. Never ship server credentials or protected data simply because the page hides it. Validate permissions when retrieving or mutating data; remove sensitive cached state on sign-out/tenant switch as required.


<a id="eng-fe-004"></a>

### ENG-FE-004 — Implement accessible interaction

**Requirement:** MUST · **Review:** human · **Evidence:** Design target and accessibility implementation review

Use semantic HTML/native platform semantics, meaningful labels, keyboard operation, visible focus, deliberate focus restoration, status announcements and non-colour-only cues. Default applicable web products to WCAG 2.2 AA as a design target unless a stricter approved requirement applies. Conformance is not asserted by using a component library.


<a id="eng-fe-005"></a>

### ENG-FE-005 — Separate server and local state

**Requirement:** MUST · **Review:** human · **Evidence:** State ownership and implementation

Keep server-source records, optimistic changes, workflow state and ephemeral view state identifiable. Define invalidation and rollback for optimistic updates. Avoid duplicated contradictory caches; prevent a late response from an old tenant/view replacing current state.


<a id="eng-fe-006"></a>

### ENG-FE-006 — Respect supported devices and local contexts

**Requirement:** MUST · **Review:** human · **Evidence:** FSD/design reference

Implement the specified viewport/device/browser range, readable layouts, touch targets, localisation and locale-sensitive display. Do not hardcode user-facing strings or infer language from identity. Document device constraints rather than silently dropping essential workflows.


<a id="eng-fe-007"></a>

### ENG-FE-007 — Make offline changes explicit and recoverable

**Requirement:** MUST · **Review:** human · **Evidence:** Offline contract and data policy

Where offline capability exists, define permitted offline actions, encrypted/local data handling, queued changes, conflict resolution, revocation limits, sync progress and deletion. Do not imply a local draft has reached the server; display pending/failed/synchronised states distinctly.


## Implementation guidance

Accessibility target selection is APLYD policy; detailed WCAG interpretation remains in W3C guidance. This package specifies implementation expectations, not an accessibility certification workflow.

For low-connectivity deployments, budget assets and network requests against the approved user environment. Use lazy loading, image optimisation and cache rules intentionally; never cache protected content across users for apparent performance.


## Reference basis

S02 provides the WCAG 2.2 standard and its conformance model. See the package [source register](../SOURCES.md). These references do not establish product certification.
