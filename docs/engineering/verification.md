# US-0101 verification — 19 October 2026

Executed locally with Python 3.12 and the pinned runtime requirements. This evidence is engineering verification, not native QA acceptance or independent approval.

| Check | Observed result |
|---|---|
| Django test suite (`python manage.py test --settings=config.test_settings`) | 14 tests passed; covers all three ACs plus adversarial access/session cases and readiness failure |
| Migration drift (`python manage.py makemigrations --check --dry-run --settings=config.test_settings`) | No changes detected |
| Ruff checks (`ruff check config workspaces manage.py --extend-select E4,E7,E9,F`) | Passed |
| Ruff formatting (`ruff format --check config workspaces manage.py`) | Passed |
| Runtime dependency consistency (`pip check`, before installing audit tools) | No broken requirements |
| Pinned runtime vulnerability audit (`pip-audit -r requirements.txt --no-deps --disable-pip`) | No known vulnerabilities; machine output in dependency-audit.json |
| Chromium desktop flow | Real local sign-in, assigned-only list, selection, workspace page and logout passed |
| Chromium 360px viewport | No horizontal overflow; screenshot inspected |

The first test run found a comparison issue: independently masked CSRF tokens differ per response. The response-equivalence test now normalises only the CSRF token, preserving full comparison of the remaining foreign/unknown response. All access assertions remain enabled. An initial UI run could not reach a server in a separate command sandbox; running the server and browser in one orchestration process completed the flow.

Screenshots are from the real local app with a synthetic account. No source account, customer data, external model or production credential was used.

Not executed: Docker image build, PostgreSQL integration tests, native QA release checker, independent security/accessibility review, DigitalOcean deployment and live endpoint verification. No complete WCAG conformance claim. Login throttling and recovery policy remain public-release blockers. No acceptance criteria are marked independently accepted.
