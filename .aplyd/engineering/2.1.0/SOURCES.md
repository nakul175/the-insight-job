# Source register and authorship boundary

**Prepared:** 11 October 2026. The numbered requirements, default governance choices, templates and utilities are newly authored APLYD package proposals for adoption. External references inform specific mechanisms; they do not establish certification, legal approval or current configuration in a user repository. No third-party specification is reproduced wholesale.

## Primary public references

| ID | Reference | Use and limits |
|---|---|---|
| S01 | [OWASP ASVS project](https://owasp.org/projects/asvs) | Reference security-control framework; not an ASVS compliance claim |
| S02 | [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) | Accessible implementation target; conformance requires its own evidence |
| S03 | [RFC 9457](https://www.rfc-editor.org/info/rfc9457/) | Optional standard error representation for HTTP APIs |
| S04 | [OWASP LLM01:2025 Prompt Injection](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) | Untrusted prompt/retrieved-content risk; does not imply complete mitigation |
| S05 | [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches) | Server-side review/check controls; no user settings inspected or changed |
| S06 | [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use) and [privileged PR trigger guidance](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target) | Pinning, permissions and untrusted workflow boundaries |
| S07 | [actions/checkout v6.1.0 commit](https://github.com/actions/checkout/commit/d23441a48e516b6c34aea4fa41551a30e30af803) | Verified full commit reference used in the workflow; not a claim of latest version |
| S08 | [actions/setup-python v6.3.0 commit](https://github.com/actions/setup-python/commit/ece7cb06caefa5fff74198d8649806c4678c61a1) | Verified full commit reference used in the workflow; not a claim of latest version |
| S09 | [Official Codex AGENTS.md guide](https://developers.openai.com/codex/guides/agents-md) | Tool-specific loading; redirects to the official ChatGPT Learn documentation |
| S10 | [Linear GitHub integration](https://linear.app/docs/github) | Source for integration concepts; no current workspace mapping asserted |
| S11 | [Ruff formatter documentation](https://docs.astral.sh/ruff/formatter/) | Reference formatter command syntax, not a mandatory tool choice |
| S12 | [Gitleaks official repository](https://github.com/gitleaks/gitleaks) | Reference secret-scanner command syntax and scope |
| S13 | [pip-audit official repository](https://github.com/pypa/pip-audit) | Reference dependency-audit command syntax and limitations |

The workflow pins were checked against official release/commit pages during preparation. Revalidate compatibility and security on adoption/update; a verified origin is not proof of vulnerability-free code. Use repository-controlled tool/runtime versions, not assumptions about the latest release.

## User-provided product-document context

Accessible Library excerpts were consulted, not redistributed:

**P01 — Impact-Management-Platform-BRD-v1.1.docx**, 27 September 2026, section 37. It separates business requirements, functional behaviour, architecture and implementation contracts; preserves original requirement meaning and traceability.

**P02 — Impact-Management-LLD-v1.1.docx**, 27 September 2026, introduction/release interpretation. It distinguishes proposed design, current implementation and unresolved delivery status rather than treating document completeness as implemented capability.

**P03 — Global_Health_AI_Sandbox_FSD.docx**, version 1.0, 24 September 2026, sections 12.4–12.7. It retains upstream nonfunctional requirements, separates HLD/LLD responsibilities and keeps external rights/authorisations independent from technical implementation.

These sources support the package's upstream-document bridge. The exact standalone organisation-wide Product Documentation Standard was not located. Its identifier, content and version are therefore not invented. Each adopting repository must record its actual approved product-document baseline. No private repository, Linear workspace or live deployment was changed during preparation.

## v2 revision provenance

The v1 ZIP was inspected directly; its original package verifier returned INTEGRITY_PASS for 82 payload files and 96 controls. User-provided Grok/Claude review text supplied the integration concerns. This revision did not locate the actual native PM/QA archives and makes no compatibility claim for them. Synthetic files exercise only the new binding protocol.

The official Codex AGENTS guide and GitHub Actions secure-use guide (S09/S06) were read on 11 October 2026 for instruction scope and CI trust boundaries. Existing action pins are retained from v1; they are not represented as newly validated release versions in this revision. Changes to native bundle hashes or checker arguments require inspection and approval of those actual files.

## v2.1 provenance correction and scope

The public reference register above is inherited from earlier editions. This adapter revision did not re-fetch those pages or re-verify action pins online; it retains their existing bytes. The earlier statement that the organization-wide product standard was not located describes the original source limitation, not a refusal to implement the newly reported native shapes.

For v2.1, the actual engineering v2.0.0 ZIP was inspected. The supplied Grok/Claude review describes PM's mixed `elements` array and prose-only technical-task criteria, QA's case/trace/run tables and its timestamp-based exception CSV. Those reported shapes are implemented and reproduced in labelled fixtures. The actual PM v4.1/QA v1.2 archives were not obtained. No catalogue hashes, native status definitions or checker CLI claims are made for them. Exact source-archive provenance is recorded in SOURCE_PROVENANCE.json.
