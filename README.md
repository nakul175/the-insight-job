# Source Almighty

Evidence-backed content intelligence, formerly **The Insight Job**. The user selected the Source Almighty name and crooked-halo document mascot on20October2026. Historical product baselines, canonical story IDs, repository identity and deployment address remain stable.

[Linear project](https://linear.app/aplyd-sandbox/project/the-insight-job-ef0144c22162)

## Run locally

Python 3.12+ recommended. Create a virtual environment and install `requirements.txt`.
Set `DJANGO_DEBUG=true` and a unique `DJANGO_SECRET_KEY` in your shell. Environment files are not loaded automatically.

```sh
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Use `/admin/` to create a workspace and explicitly assign memberships. Even staff users need a membership to enter a workspace through the product UI. There are no seeded passwords, public signup or simulated authentication.

```sh
python manage.py test --settings=config.test_settings
python manage.py makemigrations --check --dry-run --settings=config.test_settings
```

`config.test_settings` uses SQLite (a disposable file database for explicit browser suites) and a deliberately fast test-only hasher. Never deploy that settings module. Production settings require a secret and database URL, enforce HTTPS and secure cookies, and use Django's normal password hashers. The first migration is additive; apply it once as a release job, not concurrently from every web worker.

## Delivery status

All79original mapped R1 stories are implemented and merged in eight batches. PR11 mainCI39406731586 passed SQLite/PostgreSQL tests, audit and both builds at sourcea040812388e703e6acfc0e4f12d641ddb0503a6c. A sign-in-protected public prototype is hosted on the existing APLYD droplet at https://insightjob.aplyd.org/ under explicit user-approved prototype exceptions. Native QA/product acceptance and formal production release remain unverified; see the batch ledger and engineering evidence.

The Source Almighty visual rebrand is a subsequent user-requested presentation change; its own review, CI and deployment evidence are recorded separately. Four external-delivery stories APL-2938–APL-2941 remain Low-priority Backlog, outside the79-story denominator; they are not implemented. Current delivery remains the local sandbox contract.

## External delivery extension (TIJ-01–04)

The four additional stories APL-2938–APL-2941 are separate from79R1. Administrator-only external delivery is available through each system’s destinations page. Configure an inactive operator-approved receiver, explicitly activate, then queue and send a pinned package. Uncertain operations permit receipt lookup only; no automatic POST retries. Receiver application/deletion evidence stays separate from acknowledgement. See [receiver contract and provisioning](docs/engineering/external-delivery.md). No receiver is configured or activated by installation.

Primary prototype hostname: https://sourcealmighty.aplyd.org/ (existing insightjob.aplyd.org retained during transition). Deployment and independent product acceptance remain separate from engineering completion.
