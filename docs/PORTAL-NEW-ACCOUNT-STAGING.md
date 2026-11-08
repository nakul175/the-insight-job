# Portal new-account and first-login staging

## Owner authority and scope

Existing owner staging exception: docs/PORTAL-PERMISSION-STAGING.md#owner-authority; owner continuation on8Nov requests Portal Create, first login and same-open-session grant/downgrade/revoke. This C2 change implements only Source Almighty's native contract. No production adoption, release, merge or permission enablement is implied. Independent Product/QA gates remain separate.

## Design and trust

Three default-off Source gates separate provisioning, native membership writing and central OIDC. The process connector accepts only a known central subject, reviewed native administrator, exact workspace/organisation and signed current Portal preview. Account creation produces an unusable-password, non-staff/non-superuser Viewer plus explicit PortalIdentity and atomic receipt. No source/system/export grants are created. An existing issuer+subject, deterministic native username or matching email denies creation; email is never an identity-link fallback. Native workspace serialization plus PostgreSQL subject/email advisory fences guard concurrent Portal creations. Existing manual administration is not redesigned.

Portal reserves a durable request before native I/O, validates exact readback, then creates an Accounts link. It publishes central access only after all confirmations. Lost/uncertain results stay held and cannot be blindly retried. Accounts' separate default-deny Source registration requires the completed Portal receipt, active central person/organisation/app assignment, reviewed resource and current contract before issuing tokens or validating sessions. This prevents partial cross-database work from authorizing a login. New Source OIDC registration is intentionally restricted to provisioned identities; existing captured accounts require a separate reviewed rollout.

Native central sign-in uses authorization code+S256 PKCE, five-minute one-use pending state, nonce, pinned issuer/client/callback, fixed RS256/JWKS, audience/azp/at_hash verification, matching userinfo subject/session and current Accounts session status. Trust never comes from email or token header URLs. PyJWT2.15.1 matches the already-used Portal dependency (official API reference: https://pyjwt.readthedocs.io/en/stable/api.html). Strictly loopback HTTP is allowed only in DEBUG; other configured origins require HTTPS. Outbound calls reject redirects, cap JSON64KiB and timeout5s.

Every authenticated native request rechecks current Accounts state and exact PortalIdentity. Native middleware separately rechecks workspace membership/generation and operation-specific source/export/system permissions. Role changes keep the cookie; revocation denies protected requests and the existing poller clears open content. With OIDC gate off, old native login/session behavior stays unchanged. Enabling it requires a separately approved central-session rollout; old password sessions are not silently upgraded.

## Recovery and limitations

No new database schema/backfill; the existing native creation/permission receipt and identity tables are reused. Do not roll back to code ignoring current membership active/generation state. Unknown-person onboarding, additional-workspace adoption, production connector/credential provisioning, shared role definitions and production rollback certification remain out of scope. Staging UI uses the existing SQLite bridge; real PostgreSQL regressions independently validate native database behavior. Existing secrets and live databases are never read by the fixture.

## Evidence

External workspace pack: work/aplyd-central-admin/native-session-e2e-20261108/source-provisioning-acceptance.json. Actual Portal Create, Accounts OIDC, first native login, same-cookie source/export/system grant-downgrade-revoke and workspace role grant-downgrade-revoke pass with synthetic identities. Every pre-existing row across75native and27Portal tables is preserved. Test/CI exact revisions recorded in the pack after publication; no claim that local Mac parser failures are passing. Existing parser resource.setrlimit(RLIMIT_AS,256MiB) raises ValueError on this Mac; the four affected parser regressions require Linux CI without relaxing the limit.
