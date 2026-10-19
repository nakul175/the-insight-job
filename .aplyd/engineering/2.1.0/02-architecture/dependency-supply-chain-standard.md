# Dependencies, build integrity and automation security


**Package:** APLYD-ENG-001 · **Version:** 2.1.0 · **Date:** 11 October 2026

Avoid gratuitous new tooling. Preserve established stacks, use existing approved accounts, and treat build tools as code with privileges.


## Controls


<a id="eng-sup-001"></a>

### ENG-SUP-001 — Justify dependencies and rights

**Requirement:** MUST · **Review:** human · **Evidence:** Dependency decision

Before a substantial new dependency, record purpose, existing alternatives, provenance, maintenance/security posture, licence/redistribution obligations, footprint and owner. For AI, distinguish application code, model weights and dataset rights. Do not claim legal approval from a package licence field alone.


<a id="eng-sup-002"></a>

### ENG-SUP-002 — Pin reproducible inputs

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Lockfiles/toolchain and build evidence

Commit the appropriate lockfiles; record runtime/toolchain versions and use frozen installs where supported. Do not regenerate lockfiles with a different package manager or use unreviewed latest versions. Container/artifact sources need a suitable immutable reference for the deployment policy.


<a id="eng-sup-003"></a>

### ENG-SUP-003 — Treat scanning failures honestly

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Scanner evidence and exception

Configure dependency and secret detection appropriate to the stack. Record tool/advisory snapshot context, exclusions and results. Network errors, missing databases or partial scope are not a clean bill of health. Known-risk suppressions require narrow reviewed rationale, owner and expiry.


<a id="eng-sup-004"></a>

### ENG-SUP-004 — Minimise workflow privileges

**Requirement:** MUST · **Review:** human · **Evidence:** Workflow and runner review

Use read-only CI permissions by default, ephemeral runners for untrusted changes and no production secrets in PR checks. Do not execute untrusted PR code through privileged triggers or self-hosted runners with persistent credentials. Treat action inputs and PR text as untrusted data.


<a id="eng-sup-005"></a>

### ENG-SUP-005 — Pin and review reusable automation

**Requirement:** MUST · **Review:** hybrid · **Evidence:** Workflow/action pin references

Pin remote GitHub Actions to verified full commit SHAs, review updates, disable persisted checkout credentials when unnecessary and document required runner compatibility. Local hooks are convenience; they are not a substitute for protected server-side gates.


<a id="eng-sup-006"></a>

### ENG-SUP-006 — Keep package and command execution explicit

**Requirement:** MUST · **Review:** human · **Evidence:** Adoption/command review

No adoption script may install packages, run application commands, create paid services or execute downloaded scripts implicitly. Review executable command definitions and provision pinned tools before invoking them. Never pipe unverified network content into a shell.


<a id="eng-sup-007"></a>

### ENG-SUP-007 — Maintain deployable component inventory

**Requirement:** SHOULD · **Review:** human · **Evidence:** Release inventory / SBOM

For released products, maintain an inventory or SBOM appropriate to deployment covering direct/transitive libraries, runtime images and model artifacts. Record ownership for upgrades and security response; a list without versions is not a useful deployed inventory.


## Implementation guidance

The included CI workflow is a starter, not a server-side policy deployment. A repository administrator must configure required checks, code-owner review and bypass restrictions and record the actual settings. Some platform features depend on repository/organisation configuration; do not assume installation enables them.

Do not put an author-controlled self-approval flag at the centre of compliance. Review modifications to `.engineering/`, vendored standards and `.github/` as security-sensitive. Organisation-owned required workflows or other trusted enforcement can strengthen the gate; the package does not set them up automatically.


## Reference basis

S05 and S06 describe GitHub branch and workflow controls. S07 and S08 identify the exact action pins used by the starter workflow. See the package [source register](../SOURCES.md). These references do not establish product certification.
