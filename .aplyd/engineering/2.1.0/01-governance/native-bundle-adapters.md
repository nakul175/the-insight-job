# Native PM and QA adapters — v2.1 contract

## Ownership and boundary

The **engineering maintainer** owns adapter code, source discovery, the integration proposal, command wiring and upgrades. The **product owner** owns task scope, structured criterion links and the PM team Done checklist. The **QA owner** owns native case/version mappings, result vocabulary, checker arguments and release semantics. Engineering records the actual product/engineering owners from `repository.json`; a named QA owner is supplied in the connection selections. Unassigned roles remain unresolved decisions, never implied approvals.

These utilities implement the native shapes described in the supplied reviews. The actual companion archives were not available while building this revision. Their filenames, fields and CLI may differ. The helper discovers candidates and proposes locators; ambiguity is visible and blocks approval. No case catalogue, product catalogue or native checker is invented.

## 1. PM: select types, then follow ancestry

`native_bindings.pm` identifies one hash-pinned original document and its `/elements` collection. Explicit `types` select stories, acceptance criteria and technical tasks from the mixed array. Other levels remain available for hierarchy resolution but are not treated as criteria. A vision root with `parentId: null` is legal. Duplicate native IDs, dangling parents, parent cycles and paths longer than 256 hops are rejected. A criterion or technical task resolves its story by following native parents, not by requiring the immediate parent to be a story.

All element IDs remain unchanged. A document represents one pinned product baseline; duplicate element IDs from multiple baselines cannot be silently merged. Record versions are retained when a version locator is configured. A default version may be specified only as an explicit reviewed baseline mapping, not guessed from a filename.

### Product-owned typed links

A task may expose a structured array such as `/technical_task/source_criteria_ids`. The selected locator must be approved. The prose field `technical_task.source_criteria` is **never parsed** into authorization.

Where the native task has no typed array, product owns `.product/engineering-links.json`. Its schema is [task-links.schema.json](../06-enforcement/schemas/task-links.schema.json). It holds only `task_id` and `acceptance_criterion_ids`, plus the exact PM input hash, mapping owner, status and approval reference. It does not repeat task title, description, priority, implementation status or delivery status. The helper proposes empty arrays; it does not assign all criteria in a story to every task. A task being implemented stays UNBOUND until its mapping exists and is approved.

The native task ID is the key. Every referenced criterion must belong to the task's resolved story. If both native structured links and the product extension exist, they must agree. A PM source change invalidates the extension's recorded source hash until reviewed. No second product backlog is created.

## 2. QA: join the separate tables and retain revisions

`case-register.csv` owns case identity and case version. `traceability.csv` owns links between cases and criteria. The native adapter joins on **(case ID, case version)** and aggregates distinct criterion relationships. One case may cover several criteria, and one criterion may be covered by several cases. Two legitimate relationships are not duplicate case records.

A repeated identical relation, duplicate case/version row, unknown foreign key or mismatched criterion version is rejected. Criteria versions in QA are compared exactly to the pinned PM baseline; they are not stripped or treated as an unversioned ID. Case versions `1` and `2` may coexist. There is no implicit latest-version selection.

Engineering implementation records carry `qa_case_ids` and **`qa_case_refs: [{"id": "…", "version": "…"}]`**. The two ID sets must agree. Passing evidence for an old case version cannot satisfy a selected new case version. Optional normalization previews preserve case revisions and criterion revisions; their dictionary keys are reversible JSON pairs, not ambiguous string concatenations.

QA run mappings identify run ID, case ID, case version, result and `build_sha`. A run's build must be a full 40- or 64-hex Git revision, not a free-text label or abbreviated hash. Only a passing run for the exact selected case version and engineering commit can support reviewed/merged/deployed implementation status. Explicit non-PASS selected evidence cannot be masked by another PASS. Outcome aliases require a reviewed value map. Native QA remains responsible for test adequacy, evidence authenticity and release rules.

## 3. Connection proposal and normalization commands

Extract the PM and QA archives into separate directories **inside the adopting repository**. This helper does not download, unpack or execute companion bundles. Keep the engineering distribution outside the target repo while using `adopt.py`, or use the installed enforcement core after adoption.

Run the helper with actual paths and actual version labels:

```sh
python /path/to/engineering/06-enforcement/scripts/connect_bundles.py \
  --repo /path/to/repository \
  --pm /path/to/repository/standards/product \
  --qa /path/to/repository/standards/qa \
  --pm-version 4.1 --qa-version 1.2
```

The default is read-only. It reports candidate files, explicit locators, missing decisions and the responsible role. Add `--write-proposal` to write only a new `.aplyd/proposals/binding-<UUID>/` directory. It contains a draft integration manifest, binding-review record, a product-link proposal where needed, and a normalized preview when inputs are sufficiently bound. No installed file or approval is changed.

When discovery is ambiguous, supply `--selections path/to/selections.json`. Recognized selection keys are `pm_document`, `pm_catalogue`, `qa_catalogue`, `qa_cases`, `qa_links`, `qa_runs`, `qa_checker`, `team_dod`, `task_links`, `workflow_ref`, `release_record_ref`, and `qa_owner`. Paths are repository-relative. The template [connection-selections.example.json](../07-starter-kit/connection-selections.example.json) illustrates the keys; its example paths are not claims about the companion archives. Native field locators can be reviewed and adjusted in the proposed manifest.

`team-checklists.json` is sought in the **PM bundle**, not QA. Supply actual argument arrays through `--qa-lint-args` and `--qa-release-args` only after the QA owner has inspected the real CLI. The helper never guesses a `--release` flag. It does not execute even a helper's `--help` command automatically. Gates remain incomplete until the native checker calls and their approval references are recorded.

Product first approves any typed link extension. Engineering then regenerates the proposal so the source hash refers to the approved file, reconciles authority paths and root-agent composition, and records integration approval. Native check commands still need the runner's explicit execution flag and trusted review. To inspect normalized tables from an existing manifest without invoking repository code:

```sh
python /path/to/engineering/06-enforcement/scripts/connect_bundles.py \
  --repo /path/to/repository --preview-manifest .aplyd/integration.json
```

The output is labelled `PREVIEW_NOT_AUTHORITY`. It is not a new editable ledger. Audit reads and normalizes the original pinned inputs again; it never trusts a saved preview as authoritative evidence.

## 4. Existing QA exception CSV

A native QA CSV need not grow missing fields just to be readable. Its reviewed `field_map` maps available headers; `defaults` may supply **only** `domains` and `remediation_ref`, with a separate `mapping_approval_ref`. Approval, status, owner, risk, expiry and control IDs cannot be manufactured through defaults. Missing per-row remediation may use an explicit real shared remediation decision reference, approved for that scope.

For legacy QA rows without domains, the only permitted domain default is `["qa"]`. This does not create engineering waivers. New engineering or cross-domain exceptions need explicit domain columns or a reviewed migration to the canonical register. The raw CSV remains the only selected source; the normalized view is in-memory. An existing native QA checker must still be wired to the selected source under its own supported contract.

`expiry` can map to `expires_on` without truncating its value. An ISO timestamp with a timezone expires at the exact instant. A date-only expiry remains valid through that UTC date. A timezone-less timestamp is rejected rather than interpreted in an agent's local timezone.

## 5. Legacy engineering exception migration

A nonempty `.engineering/exceptions.json` from v1 blocks audit when the selected register has moved and no approved migration receipt accounts for its IDs. Empty old registers do not manufacture a blocker. `migrate_exceptions.py --repo …` proposes a lossless merge into the selected canonical JSON register and a draft `.aplyd/exception-migration.json` receipt. `--write-proposal` writes a new proposal directory only.

The utility retains IDs, status, approvers, approval references and exact expiry values. It adds the original engineering domain. Identical existing records are reused; conflicting IDs stop the merge instead of being renumbered. It does not guess QA CSV meanings or automatically change either package's active registry pointer. Review and install the selected merged register and approved receipt together. Keep the original register frozen at the recorded hash; every old ID must map to an existing selected record. Historical status transitions remain separately governed.

## Normative controls

<a id="eng-xbd-007"></a>
### ENG-XBD-007 — Normalize native hierarchy without inventing scope
**Requirement:** MUST · **Review:** hybrid · **Evidence:** pinned PM source, typed links and adapter result.
Mixed native arrays MUST be filtered by explicit type and resolved through validated ancestry. Unstructured prose MUST NOT be converted into an approved criterion allocation. Product-owned link extensions MUST bind the exact source hash and approval.

<a id="eng-xbd-008"></a>
### ENG-XBD-008 — Preserve QA relation and evidence versions
**Requirement:** MUST · **Review:** hybrid · **Evidence:** native case/link/run sources and versioned engineering references.
Many-to-many QA relations MUST join on case ID and version. Version metadata MUST NOT be discarded. A selected run MUST identify a full Git revision; completion evidence MUST match the selected case version and source revision.

<a id="eng-xbd-009"></a>
### ENG-XBD-009 — Separate proposed connections from approval
**Requirement:** MUST · **Review:** hybrid · **Evidence:** owner-assigned proposal and recorded adoption decision.
Discovery, normalization and proposals MUST NOT activate bindings, generate approvals, execute unreviewed companion code or mark a product Done. Ambiguous inputs and unsupported fields MUST remain explicit decisions.

<a id="eng-xbd-010"></a>
### ENG-XBD-010 — Reconcile legacy exceptions without changing authority
**Requirement:** MUST · **Review:** hybrid · **Evidence:** selected register, exact timestamps and lossless migration receipt.
Exception adapters MUST preserve approval semantics and exact expiry instants. A legacy register MUST NOT be silently orphaned. Migration MUST account for every old ID without inventing approval, expanding domain scope or deleting history.
