# PM–engineering–QA contract

**Version:** 2.1.0 · **Ownership:** product, QA and engineering jointly approve the repository binding.

## 1. One chain, not three backlogs

Vision → strategy → roadmap/release goal → epic → story → acceptance criterion → **canonical technical task** → implementation/PR → QA case/run → team delivery decision → independently authorised release.

The PM native task record owns task identity, scope, relationships and delivery stage. `.engineering/traceability.json` is an implementation extension keyed by that exact `task_id`. It holds code/review/deployment facts and references native QA case/run IDs. It does not allocate another sequential ENG task or copy a second product status. A Linear issue may itself be the canonical task ID when the product catalogue says so; otherwise it is a `tracker_ref`. GitHub, Jira and local UUID-backed work records are also permitted.

The QA native source owns case definitions, execution outcomes and release-check semantics. Engineering does not recreate it or infer coverage from prose. The adapter reads actual source rows on demand; it does not save a normalized duplicate ledger.

## 2. Document roles are many-to-many mappings

| Existing engineering input | PM concepts it elaborates or implements | Preservation rule |
|---|---|---|
| BRD | Vision, strategy, outcomes and release goals | Retain business rules, constraints and requirement IDs; a BRD is not equivalent to a vision statement |
| FSD | Stories, acceptance criteria, business rules and functional workflows | Retain detailed states, fields and cross-cutting behaviour; do not shrink it to story titles |
| HLD | Design notes attached to relevant epics/releases | Preserve system boundaries, security, operations and cross-epic architecture |
| LLD | Design notes and implementation contracts attached to technical tasks | Preserve interfaces, schemas, algorithms, migrations and shared contracts |

Approve these relationships against the **actual product catalogue version and SHA-256**, not this table alone. Hashes bind the reviewed catalogue bytes; they do not establish semantic equivalence or authority. Actual repository document roles remain in `product_docs.bindings` and the native task relationships.

## 3. Binding and verification

`.aplyd/integration.json` is a declarative adapter contract, **not** a claim about either unseen native bundle's schema. It pins product/QA catalogues, native records, rules and checker source files by version and SHA-256. It maps JSON pointers or CSV headers into typed story, criterion, task, case and run references. Native IDs are unchanged. The audit verifies hashes, key existence and relationships; it rejects stale sources, unknown IDs, unrelated cases and mismatched task scope.

Combined mode requires both catalogue pins, the one team checklist, one workflow/release-record pointer, one exception-register path and approved `qa_lint` and `qa_release` commands. Native checker files must be called directly by Python from repository root. Their actual arguments are copied only after inspecting their real source/help. No guessed `--release` flag or replacement `qa_lint.py` is shipped. Unsupported native data shapes remain blocked rather than silently converted.

Local component mode requires an explicit accountable decision reference. It supports authorised engineering/adoption work without pretending the PM/QA chain has been verified. It cannot pass a combined release invocation. Component mode must not be used to relabel an application release as exempt.

## 4. One root router; no unsafe ranking

Merge product, QA and engineering routing into the root `AGENTS.md`. Domain documents are explicit references, not competing root templates. This distribution vendors no extra active `AGENTS.md` under its installed package path. Existing subtree instructions must be reviewed for their actual tool scope; do not delete them automatically.

Product owns intended behaviour. QA owns verification and quality gates. Engineering owns implementation constraints. Legal/security/data-use obligations and actual platform permissions remain binding. A conflict is a blocker routed to the relevant owners, not a simple priority override.

## 5. One team Definition of Done

The product-team checklist is the authoritative delivery checklist. It incorporates both the engineering completion checklist and the native QA checklist. All applicable checks are conjunctive: required engineering evidence **and** required QA evidence **and** required product acceptance/operational decisions. An owner cannot mark Done by referring only to one green job.

| Recorded fact | PM/QA interpretation |
|---|---|
| Planned | No implementation or verification claim |
| Implemented | Code exists; product delivery may still be in progress |
| Engineering-reviewed | Implementation has review; combined mode also requires linked passing QA runs at the recorded revision for implementation rows |
| Merged | Observed version-control event; not a deployment |
| Deployed | Exact artifact observed in a named environment; not product acceptance |
| QA PASS/FAIL/BLOCKED/NOT RUN | Separate result for a stated case, run and revision |
| Accepted / team Done | Actual PM decision under the team checklist; never generated by `engctl.py` |

## 6. One exception authority and one evidence index

The repository selects a single shared exception register. The starter offers `.governance/exceptions.json`; an existing QA CSV may remain canonical using an explicit reviewed field/status map. The checker reads that selected source and writes no shadow copy. Do not install a second writable register merely for engineering. Existing historical registers remain archived read-only until reconciled; never delete decision history.

Each exception retains a globally unique/native ID, domains, control IDs, scope, risk, mitigation, owner, independent approver, real decision reference and expiry. Engineering validates its own controls/non-waivable floor. Native product/QA checkers must validate their own control vocabularies and gate-specific waiver rules. A mapping does not make an exception effective and does not auto-convert command failure to success. Inapplicability is a reasoned applicability decision, not a waived security duty.

The shared evidence root defaults to `.aplyd/evidence`. Runs have UUID-bearing directories and link canonical task IDs, source revision, configuration hashes and native QA commands. Native QA evidence may remain at its existing authoritative location and be referenced; do not duplicate raw evidence or rewrite historical IDs. Logs are protected locally and never uploaded automatically. A shared root does not replace the native QA evidence schema or prove that its outputs are authentic.

## 7. Enforced integration controls

<a id="eng-xbd-001"></a>
### ENG-XBD-001 — Pin and validate native bundle bindings
**Requirement:** MUST · **Review:** hybrid · **Evidence:** approved integration manifest and structural audit.
Combined mode MUST use actual catalogue/file hashes, native field mappings and native checker commands. Unknown or unavailable bindings MUST block combined gates.

<a id="eng-xbd-002"></a>
### ENG-XBD-002 — Reuse canonical task and typed QA references
**Requirement:** MUST · **Review:** hybrid · **Evidence:** native sources and engineering extension.
Implementation rows MUST reuse the native technical-task ID and typed story, criterion, case and run IDs. New local records MUST use collision-resistant IDs; historical source IDs MUST NOT be renumbered.

<a id="eng-xbd-003"></a>
### ENG-XBD-003 — Keep one root composition and one team Done
**Requirement:** MUST · **Review:** human · **Evidence:** reviewed root router and team checklist.
The team checklist MUST require applicable product, QA and engineering obligations. Neither product scope nor agent routing may waive mandatory safety controls.

<a id="eng-xbd-004"></a>
### ENG-XBD-004 — Require real tests and native QA gates
**Requirement:** MUST · **Review:** hybrid · **Evidence:** current run report and native checker results.
Executable implementation MUST run an actual test command with a fresh report and a nonzero executed count. Combined changes MUST run the native QA structural checker; release scope MUST additionally run the native QA release check. Missing commands/reports, all-skipped cases and failures MUST NOT count as passes. Report counts do not prove test adequacy.

<a id="eng-xbd-005"></a>
### ENG-XBD-005 — Use one selected exception register
**Requirement:** MUST · **Review:** hybrid · **Evidence:** shared source and domain-specific decisions.
Engineering MUST read the selected shared source without maintaining a second authoritative approval register. Non-waivable controls and independent approval MUST remain effective across adapters.

<a id="eng-xbd-006"></a>
### ENG-XBD-006 — Preserve repository-owned configuration
**Requirement:** MUST · **Review:** automated · **Evidence:** adoption plan and regression suite.
Reinstallation MUST preserve repository-owned records and MUST NOT propose blank starter records over configured data. Version migration MUST be an explicitly reviewed change.

## v2.1 native-shape extension

The [native adapter contract](native-bundle-adapters.md) is normative for mixed PM arrays and relational/versioned QA CSVs. It defines product-owned typed link extensions, versioned case references, bounded read-only connection proposals, exact timestamp semantics and mandatory v1 exception reconciliation. Flat `entities` and `native_bindings` are mutually exclusive. Catalogue pins, approved native checker calls, one team Done and the existing safety floor remain mandatory.
