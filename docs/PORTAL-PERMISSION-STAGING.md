# Portal workspace permission staging

## Owner authority

8 November 2026, existing Codex conversation 01a16394-0869-7471-b712-e1c971c4e858:
“Source Almighty ke staging-only work ka exception aur BTF final push to nakul175/backlog approve hai. Ab remaining gaps close karke end to end verify karo; existing users, roles aur data safe rahe, prod abhi mat badalna.”

This is a staging-only owner exception. It does not change draft adoption, appoint missing owners, supply native PM/QA bindings or authorize production.

## Design delta (C2)

The OS-local default-off connector requires explicit issuer/subject/native-user/workspace mappings and an independently allowlisted, currently active native administrator. No email match, account creation, global staff/superuser edit or inferred grant. Self changes are refused.

Portal signs a five-minute, session-bound exact preview; it reserves a durable dispatch before native I/O. Native rechecks authority, locks the workspace, compares the current plan, applies a native role or suspension, and writes an atomic receipt. Unknown results remain held for reconciliation. Mutation receipts cannot be replayed. Native database triggers advance revisions even for direct ORM role updates and preserve revoke epochs across regrant. Exact source/system/export grants are hashed in the plan and remain unchanged.

Revocation uses `Membership.active=false`, preserving the existing membership and all SourceGrant/SystemGrant rows. Current request guards and worker permission checks deny suspended membership; queued/running work is blocked. Existing native manual deletion is unchanged and is not invoked by Portal. Last active administrator and disabled-user protections remain enforced.

Every open workspace page polls a no-store permission status at five seconds and on focus, with a four-second timeout. A change clears stale forms and navigates to the safe workspace landing GET. It never replays a POST or restores an unsaved form. Unverifiable access clears the visible page. Revoked workspace generations require fresh native login after regrant; role changes keep the existing cookie.

## Limits

Only existing, explicitly reviewed workspace memberships and native Administrator/Operator/Reviewer/Viewer roles. Source/system/export editors, native account provisioning and central native sign-in are not delivered by this change. The Portal process bridge is staging SQLite only; PostgreSQL native logic is tested separately. No live connector certification or production writer enablement. Independent Product/QA review and production migration/restore gates remain outstanding.

## Validation and dependency repair

The first Linux CI candidate passed 269 SQLite/browser tests (8 explicit skips) and 269 PostgreSQL tests (10 explicit skips), including the new contention check. The unchanged dependency audit then rejected pypdf 6.21.1 for ten advisories. Pin updated to the minimal patched 6.21.4, verified against https://github.com/advisories/GHSA-cggf-277g-3q54 and https://github.com/py-pdf/pypdf/releases/tag/6.21.4; full CI rerun required. No audit suppression or parser isolation weakening.

## Future rollback restriction

Reverting to code that ignores Membership.active, or removing these columns, can restore suspended access. A future production release must preserve deny/epoch enforcement across rollback and independently prove consistent PostgreSQL backup/restore. This PR does not authorize or certify a production rollback.
