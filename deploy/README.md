# DigitalOcean staging proposal

Status: NOT PROVISIONED. No costs incurred and no live endpoint exists.

Proposed topology: one App Platform Docker web service and a managed PostgreSQL database, in one approved region. No collection worker, model provider, object store or scheduler is needed for US-0101. Do not deploy SQLite to ephemeral app storage.

`digitalocean.app.yaml.example` is intentionally incomplete. Resolve the account/team, region/residency, resource size and monthly budget first. The app requires a narrowly scoped GitHub integration for this private repo. No integration permissions have been granted by this work.

Before release: complete the gates in `docs/engineering/US-0101.md`, approve the exact Git commit, test against PostgreSQL, build the container, create and store secrets in DigitalOcean, configure the allowed host and CSRF origin, verify the proxy strips/rewrites X-Forwarded-Proto, and arrange database backups. Use a one-off approved migration job (`python manage.py migrate`) before web rollout. Create initial administrators through an interactive secure console; no hardcoded credentials.

Deploy-on-push is false. No GitHub Actions deployment workflow is activated. Run `python manage.py check --deploy` under production settings, then verify login, assigned-only list, expired sessions and forbidden workspace URLs over HTTPS on staging. Check session-cookie flags and no-store headers. Record endpoint, commit, migration and observed checks in Linear before requesting release approval.

Rollback: redeploy the last approved image while retaining the database. The initial additive schema may remain; do not reverse or delete data as a routine rollback. A first release with no prior version is contained by stopping ingress/service while retaining the database for recovery. Deletion is a separate action.

Access observation on 2026-10-19: no connected DigitalOcean plugin or local credential configured; the cloud console returned “Site Unavailable”. No account inventory or pricing quote was obtained. Obtain an account-specific quote before committing spend.
