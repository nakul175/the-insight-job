# Batch 1 — workspace access and source setup

Authorised by the user's 20 October 2026 instruction to code stories sequentially in eight batches, update Linear, test each batch, merge to main, run CI and fix issues before proceeding. This is explicit prototype coding/merge authority. It does not assert independent product acceptance or production release approval, and does not activate or weaken the staged APLYD native release gates.

Dependency order: US-0101, US-0102, US-0103, US-0104, US-0105, US-0201, US-0203, US-0206, US-0301, US-0205.

## Implementation

- US-0101: Django server sessions; assigned workspaces; expiry with no action replay; CSRF and POST logout; database-backed Axes login lockout after five failures, one-hour cooloff. Username and direct peer-IP lockout, with both masked in logs. No untrusted forwarding headers. Administrator-operated recovery only; no public signup or emailed reset in this increment.
- US-0102: single-use hashed seven-day invitations bound to the signed-in account email; role and source/system grants; recheck inviter authority at acceptance; immediate audited role changes; preserve at least one administrator. Invitations are generated for user-managed sharing; no messages are sent. Invitation secrets travel in POST bodies rather than server access-log URLs.
- US-0103: one source permission path scopes record reads, derivatives, searches, exports and plain-text file access. System scope protects UI/API; the system-record API intersects system source grants with the viewer's source permissions. Titles/evidence are excluded without access.
- US-0104: membership/source revocation blocks queued execution; workers recheck current membership, role, source and system permissions. Delivered receipts create queued withdrawals only where supported; unsupported withdrawal becomes manual follow-up. No external deletion is falsely claimed.
- US-0105: authorised overview counts with an explicit seven-day content window, matching filtered drill-downs, setup guidance and timestamped refresh failure rather than invented zero counts.
- US-0201: stable draft source identity, canonical URL uniqueness, assigned owner and rejection of private/local/credential-bearing/unsupported URLs.
- US-0203: included/excluded path-prefix preview, same-origin redirects, immutable source versions and pinned job configuration. All DNS answers must be public; connections pin the validated address while retaining TLS hostname/certificate checks. Redirects are revalidated before following.
- US-0206: archive impact shows active runs, enabled schedules and permitted dependent systems. Archive disables schedules and queued work, applies stop/finish policy to existing work, retains permission-protected history and restores to Draft.
- US-0301: public access needs no secret; API/session configurations select a domain/workspace-scoped connection reference. These connectors remain explicitly unsupported/draft until the following access stories implement them. No plaintext credentials are stored in source configuration.
- US-0205: bounded public HTML/text sample (one page, 2 MB, 30-second overall read budget, at most three redirects), format/missing-element/completeness report and no delivery. Empty, blocked or unsupported capture cannot activate. Partial capture needs a documented policy and a new test. Activation binds the exact currently tested source version.

C2 schema/permission change: additive migrations for role, source/version, grants, invitations, minimal content/derivative references, audit, pinned work, schedules and withdrawal intents. Future-story foundation tables do not mean their workflows are implemented. SQLite is local only. PostgreSQL and Docker build are exercised by Product CI. No live customer accounts, source secrets, model calls or paid resources.

## Verification

Focused tests live in `workspaces/tests/test_access.py` and `intelligence/tests/test_batch1.py`. Combined regression run is required before merge, followed by the main-branch CI run. Product CI checks lint/format, migration drift, SQLite and PostgreSQL tests, runtime package audit and Docker build with read-only repository permissions. No deployment job exists.

The original APLYD core and product baseline remain unchanged and hash-pinned. Governance adoption is still draft; no native reviewer trust, QA run IDs, independent acceptance or production certification is fabricated. Merge authority is the explicit user instruction, not an inferred approval from a green check.
