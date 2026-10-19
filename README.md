# The Insight Job

Evidence-backed content intelligence. First increment: **US-0101 / APL-2773 — Sign in and select workspace**.

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

`config.test_settings` uses in-memory SQLite and a deliberately fast test-only hasher. Never deploy that settings module. Production settings require a secret and database URL, enforce HTTPS and secure cookies, and use Django's normal password hashers. The first migration is additive; apply it once as a release job, not concurrently from every web worker.

## Delivery status

Draft implementation, not approved, merged, deployed or accepted. Product and QA approval bindings remain draft. See `docs/engineering/US-0101.md` for scope, invariants and gates. All 79 R1 stories are tracked in Linear with canonical IDs. Sources, extraction, distillation and delivery are future stories.

DigitalOcean deployment is not activated. No paid resources have been created. Confirm account, region, resource budget and release authority before provisioning. Public deployment is also gated on login throttling, password recovery policy, native QA review, dependency/security review and PostgreSQL verification.
