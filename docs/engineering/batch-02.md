# Batch 2 — source access, collection and parsing

C2 prototype implementation under the user's 20 October 2026 eight-batch instruction. Recovery baseline: `4f7032f8ef4755257627f09ddb22abb5ffaacdcd`; Batch 1 parent: `402673687891ec04210745fa2e1b9ed387265ebf`. Native product/QA bindings remain draft. No acceptance, production release or paid infrastructure is inferred.

## Design and scope

- US-0302 / TASK-0302-BUILD: owner/source/workspace-bound isolated Chromium handoff, encrypted actions, frames and session material, exact account-element verification, bounded login duration and domain-pinned CONNECT proxy. Failed/cancelled handoffs disconnect without starting collection. Single browser-worker ownership uses PostgreSQL advisory locks or a local file lock.
- US-0303 / TASK-0303-BUILD: unavailable access pauses jobs rather than retrying forever. Reconnection rechecks grants and resumes the failed checkpoint; identity changes require explicit confirmation. Capture checks account verification before and after fetching.
- US-0304 / TASK-0304-BUILD: only scoped environment references for API secrets; replacement is tested before publishing the reference. Disconnect deletes retained session/action material and blocks pending jobs. Authentication reflected in captured responses is quarantined and excluded from exports.
- US-0401 / TASK-0401-BUILD: active tested source, current permission, initiating actor, pinned version and workspace request-key uniqueness.
- US-0402 / TASK-0402-BUILD: bounded static pagination and linked attachment discovery; visited/pending/skipped URLs with depth/page/origin restrictions. CAPTCHA is action-required. Dynamic players/media are reported unavailable, never bypassed.
- US-0403 / TASK-0403-BUILD: immutable per-run URL capture bytes, SHA-256, timestamp and missing-elements report; safe attachment download requires current export permission; quarantined content is unavailable through that viewer.
- US-0404 / TASK-0404-BUILD: leased jobs, saved checkpoints, unique originals, bounded retry/backoff and explicit exhaustion. Every state transition after external work checks lease ownership. A stale worker cannot overwrite a new worker's state.
- US-0405 / TASK-0405-BUILD: page/depth/byte/time/attempt limits, no provider calls or spend, in-flight byte/time accounting, cancellation at a safe checkpoint and no downstream delivery dispatch.
- US-0406 / TASK-0406-BUILD: DNS-pinned public same-origin capture, domain-restricted browser traffic, sandboxed Chromium configuration, inert source text, quarantine and audited safety rejection. Parser subprocess receives only document data and an environment without application secrets; it has fixed resource/time/output limits and no model/tool interface.
- US-0501 / TASK-0501-BUILD: HTML headings/tables/sections and PDF pages; explicit encrypted/corrupt/unsupported/empty/partial outcomes retain originals and do not claim full extraction.

Additive migrations 0002 and 0003 introduce handoffs/actions, capture/parse records, connection/source bindings and worker bookkeeping. Apply to a backed-up nonproduction database before starting workers; do not run concurrent migration processes. No existing rows are deleted by these migrations. Historical unbound connections must be reconnected through source setup.

## Runtime requirements

Collection/parser workers require Linux with `resource.RLIMIT_AS` and `RLIMIT_CPU`. On macOS the parser fails closed when the memory cap cannot be applied; it must not silently run without the cap. Use the Linux CI/container for complete parser verification. Parser wall time is seven seconds and input is at most 2 MB. Supported documents are HTML, plain text/CSV and PDFs; archives, Office documents and media are retained/quarantined or reported unavailable.

Configure `TIJ_CONNECTION_KEYS` through the runtime secret store as comma-separated Fernet keys, newest first. Keep old keys while material encrypted with them remains. Never commit keys or account material. `TIJ_SECRET_*` values belong only to the trusted capture/API service, not the browser subprocess. Missing keys prevent login. Browser subprocess environment is restricted; no model/API key is required.

Run `python manage.py run_collections` and `python manage.py browser_handoffs` as separate non-root processes against the same database. The `browser-worker` Docker target installs the pinned Playwright Chromium and OS dependencies; the default `runtime` target serves web/collection. A browser-worker host must support Chromium's sandbox and outbound proxy configuration. Do not disable sandboxing for source login. An unsupported host fails the handoff. Browser fixtures use only synthetic HTML and intentionally launch without sandboxing; this is not evidence of a production host's sandbox configuration. External SSO domains are unsupported by the single-domain connector.

## Verification checkpoint

Local macOS: lint, formatting, migration drift and runtime dependency audit passed. Three added worker-race/budget regression tests passed. The complete 68-test run executed the real Chrome fixture and loopback proxy test; four parser-dependent assertions failed because macOS rejects the required address-space limit. This is a recorded environment limitation, not a passing suite. No tests were skipped in that explicit browser run. Linux Product CI must pass before merge, including SQLite, PostgreSQL, Chromium fixture, dependency audit and both container builds.

All tests use synthetic data. No live source login, DigitalOcean activation, production deployment, native QA run or independent acceptance has occurred. Live source account/MFA interoperability, infrastructure sandbox policy and native release approvals remain separate gates.

Linux PR CI run 39346844861 passed on a9b3d914118f8f99aa777c3a1eff0b6f5f57d1eb: SQLite/Chromium, PostgreSQL, audit and both image builds. Follow-up account-verification/disconnect and parsing-race regressions passed locally; the final candidate requires a fresh CI pass.

## Handover and merge gate

[PR #2](https://github.com/nakul175/the-insight-job/pull/2) contains recovered source plus local corrections. Code revision: `31543f10bc4b507bdc86240a713f9ff4419bfb47`. Five new race/budget regressions pass locally. The final CI run must qualify the current head. All ten mapped Linear issues are In Review; acceptance checkboxes/VERIFY tasks were not marked accepted.

The preserved ENG-GIT-002 requires independent accountable owner review and states that AI/self-review cannot replace it. The repository has no designated owner/review approval. Explicit merge authority is recorded, but no review waiver or staffing exception has been invented. Pending that review and final CI, do not merge or start Batch 3. The user has been asked to identify the reviewer. Once resolved, merge the exact reviewed head, verify main CI, record the merge/run evidence, then proceed in ledger order. No production operation is authorised.
