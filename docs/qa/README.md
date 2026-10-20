# R1 criterion assessment

User authorization: 2026-10-20, assess all 79 R1 stories against all 237 acceptance criteria, repair failures, and update Linear/dashboard. Canonical source remains `docs/product/Planning-View.json`; its original proposed status and IDs are preserved. The four external-delivery extensions remain outside this denominator.

This is an AI-led synthetic prototype assessment by the implementing assistant. It is not independent human QA, native catalogue approval, product acceptance, a staging exercise, or a production release. Repository adoption/integration remain draft; no native QA case/run bindings or accountable acceptance owner are configured. No fictional owner or native case ID is introduced.

`r1-criterion-matrix.json` records each criterion's unchanged expected behavior, explicitly curated probe references, source lines/hashes, and the actual assertions in those probes. It is a cross-reference, not an automatic semantic coverage score or a passing result. Results must be reconciled against an executed exact-revision run. A probe can support several criteria; counts of tests and criteria must never be equated. Conditional paths not exercised must remain explicitly limited.

## Repair and additional observations

AC-0701-02 / APL-3021: on baseline `434458b3ffa8134f89a8c6bfe4d9a6b5234feb6e`, a content-type schema's detail page omitted dependent systems because only the directly owned system was considered. A requirement-led regression reproduced the defect (11 passed / 1 failed after test-fixture corrections). The repair uses the existing lifecycle dependency inventory and filters it through current system permissions before rendering. Restricted dependency names and IDs stay hidden.

Seventeen new service/request probes cover source preview/activation, archive impacts, content lineage, type-schema dependencies, independent routing, relevance review/override, retirement, invalid rules, queue filters, correction evidence, defer constraints, cyclic enrichment, append-only audit, usage timestamps/correlation and unauthorized alert recipients. A new browser journey checks named keyboard-reachable controls across eleven core screens at 360 CSS pixels and preserves a correction after a failed save. A second browser check exercises synthetic password and MFA steps before identity verification; it does not log into a real provider.

Baseline main CI run 39443855515 attempt 2 passed freshly on the unchanged baseline: 220 discovered per database, 213 executed in SQLite/Chromium and 213 in PostgreSQL, seven explicit skips in each. Local unrestricted macOS baseline reproduced four parser failures because RLIMIT_AS is unavailable; Linux CI passed those checks. Initial sandbox loopback restrictions are preserved in diagnostic logs and are not product defects. Parser safety limits were not changed or disabled. Candidate checks and CI must additionally pass before the repair is considered merged.

## Limits requiring explicit reporting

- AC-0803-03 and AC-0902-03: semantic/model paths are not activated in this prototype. Literal relevance and typed deterministic comparisons are exercised, and unsupported semantic operators fail closed. No model/prompt execution, cost, credential use or semantic acceptance is inferred. An authorized provider/model/prompt configuration and approved data policy are prerequisites for exercising those conditional paths.
- AC-1406-03 and AC-1505-03: browser checks cover text status, live-region markup, tab roles, selected state, error alerts and keyboard focus. Actual assistive-technology speech/announcement output requires an enabled screen-reader test session; browser DOM assertions alone do not establish that outcome.
- Hosted authenticated end-to-end acceptance requires a normal authorized QA sign-in/session. Public health/sign-in/static/authentication-boundary smoke is distinct from synthetic local functional assessment.
- The synthetic isolated restore exercise tests the declared prototype policy. It does not verify restoring the droplet's production backup or off-host retention.

## Reproduction

Run the existing Product CI workflow on the candidate revision. Both database suites now use verbosity 2 so exact probe names and skips are retained. SQLite/Chromium exercises UI journeys; PostgreSQL exercises real row-lock contention. The plain PostgreSQL run intentionally skips browser fixtures; the SQLite run intentionally skips PostgreSQL-only concurrency cases. Complementary execution must be verified, never assumed.

Local focused commands:

```sh
python manage.py test intelligence.tests.test_acceptance_audit intelligence.tests.test_batch3 intelligence.tests.test_batch4 --settings=config.test_settings --verbosity 2
TIJ_BROWSER_TESTS=true python manage.py test intelligence.tests.test_browser_engine intelligence.tests.test_acceptance_browser --settings=config.test_settings --verbosity 2
```

Run only with synthetic disposable test databases. Do not point these commands at the hosted application database or weaken parser isolation for macOS. Preserve raw failures, retests and exact revisions separately. Final assessment evidence is published to the existing Linear stories and dashboard; no acceptance checkbox or human QA stage may be inferred from this document.
