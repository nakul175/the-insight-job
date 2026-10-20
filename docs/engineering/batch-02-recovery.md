# Batch 2 recovery handoff — 2026-10-20

This is an explicitly authorised recovery checkpoint for transfer to local Codex. It preserves the existing Batch 2 implementation without restarting stories, merging, deploying, changing Linear states, or asserting acceptance. Recovery itself adds only this handoff. The product implementation is a material C2 change and remains unfinished/unreviewed.

## Starting state and scope

Branch: `batch/02`. Parent: `402673687891ec04210745fa2e1b9ed387265ebf` (Batch 1 merge, PR #1). The recovery commit preserves all 35 modified/untracked implementation files found at recovery start. Ignored credentials, databases, caches and `.runtime/` are excluded.

Batch 2: US-0302, US-0303, US-0304, US-0401, US-0402, US-0403, US-0404, US-0405, US-0406, US-0501. See `docs/product/Planning-View.json`, `docs/product/linear-map.json`, and `docs/engineering/batch-execution.json` for criteria, tracker mapping and batch order. The ledger intentionally leaves Batch 2 in progress.

Existing work includes encrypted/source-bound account connections and browser handoffs, domain-restricted browser egress, collection workers with leases/checkpoints/budgets, retained captures and isolated parsing, migrations, access-controlled UI, and tests. These descriptions identify work to inspect; they are not verification claims.

## Verification during recovery

- Read applicable root AGENTS.md, repository profile, integration manifest, pinned engineering instructions and coding standard.
- `git diff --check`: passed before checkpoint creation.
- Parsed every changed/untracked Python file with Python `ast.parse`: no syntax errors.
- Scanned the 35 implementation files for private-key headers and common GitHub/OpenAI/AWS credential patterns: no matches. This limited pattern scan is not a complete security review.
- All three product baseline SHA-256 pins match their recorded values.
- No application tests, migration checks, browser tests, dependency audit, builds, CI or acceptance gates were run as part of this recovery.

Earlier conversation reported 63 passing tests before the latest lease/account-change edits, then added two tests. That report does not verify this checkpoint. Re-run against the exact recovered revision.

## Outstanding work for the receiving Codex session

1. Re-inspect repository instructions, working tree, current branch and exact revision. Preserve the recovery commit.
2. Review the complete Batch 2 diff, especially lease-loss accounting, account changes during fetch, browser/ORM bridging, source-bound credentials, export/ACL boundaries, egress and parser isolation.
3. Run focused and complete tests, formatting/lint, migration drift and SQLite/PostgreSQL checks; resolve actual failures. Real browser coverage requires a Chromium installation and explicit browser-test configuration. Do not weaken browser sandboxing or Django async protections.
4. Review the new pinned Playwright/PDF dependencies and run the runtime dependency audit. Supply safe runtime configuration documentation for `TIJ_CONNECTION_KEYS` and worker operation; never commit actual keys/tokens.
5. Extend product CI to exercise browser coverage and the intended worker build; document supported deployment/sandbox requirements. No Batch 2 CI result exists from this recovery.
6. Complete the Batch 2 design/security/migration record and update Linear story by story with actual evidence. Prior context indicates only US-0302 was In Progress; inspect Linear rather than assuming current state.
7. Only after the authorised batch test/review workflow, merge Batch 2 and confirm main CI, then continue the remaining six batches in ledger order.

Native integration bindings and owner approvals remain draft/unassigned. Engineering checks do not establish independent product acceptance or production readiness. DigitalOcean access/deployment is not part of this recovery. No model/provider calls or credential provisioning are performed here.

## Local test context (optional, non-portable)

Previous scratch tooling used `/tmp/insight-job-venv/bin/python` and trusted browser fixtures with `/tmp/chromium`:

```
TIJ_BROWSER_TESTS=true PLAYWRIGHT_CHROMIUM_EXECUTABLE=/tmp/chromium /tmp/insight-job-venv/bin/python manage.py test --settings=config.test_settings
```

These scratch paths will not transfer to local Codex. Provision the local environment from the repository instead. No credentials or runtime state are needed to recover the source.
