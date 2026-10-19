# Enforcement contract

**Runtime:** Python 3.11+ enforced by the tools. Standard-library-only runtime dependencies. Linux/macOS process groups are required for command execution; read-only audit/adoption are not process-group dependent.

## Tool boundary

`adopt.py` plans first, writes only on `--apply`, preserves existing files, skips repository-owned records on repeat adoption and stages CI. Its default core install includes all normative control documents needed by the audit, not all templates/examples. The original distribution remains the source for omitted documents by hash.

`engctl.py audit` checks version-2 profile/command/extension/register schemas, nonblank actual owners/references, safe paths, installed integrity, command applicability, unique canonical task IDs, stage prerequisites, exceptions and—only in combined mode—native cross-bundle bindings. `integration.py` reads pinned native JSON/CSV records through explicit maps; it is not a QA implementation.

`engctl.py run` requires `--execute-reviewed-commands`. It executes setup, the selected command groups and actual bound QA scripts; saves bounded local logs and structured evidence; and returns nonzero on a failed/blocked required operation. A test category additionally requires a fresh per-run JUnit/counts report with nonzero executed cases and zero reported failures/errors.

## Important non-guarantees

A structurally valid field is not authenticated approval. Hashes detect changed bytes, not the origin or adequacy of those bytes. The audit does not query GitHub/Linear/Jira. Native checker CLI meaning and field mapping require human approval against the actual native bundle. A command or test-report author could fabricate success; independent code review, native QA evidence and trusted CI policy are required. A passing structural audit is not passing application tests. A successful selected CI group is not a successful whole pipeline. No tool sets PM Done, merges or deploys.

## Applicability and exceptions

All seven categories support reasoned N/A with an actual decision reference. Tests cannot be N/A for executable or implementation scope. N/A is not a missing-tool workaround. Commands in an N/A entry are rejected. Exceptions do not automatically suppress command failures. Only the selected shared register is read. The engine validates engineering controls and general approval hygiene; product/QA-specific waivers must pass their native owners/checkers.

## Evidence and reports

The shared root is configured by `evidence_root`, default `.aplyd/evidence`. A UUID-bearing run directory is newly created with mode 0700; tool-written logs/reports use 0600. Arbitrary child commands can still create differently permissioned files and expose their environment: the runner is not a sandbox. Never auto-upload raw logs. Reports record group, phase, task keys, source/config hashes, Git snapshots, argv, exit state, duration and log hashes. Native QA artifacts are referenced, not automatically moved or transformed.

A test command may use `{evidence_dir}` and `{run_id}` in argv. Only explicit token substitution occurs; no shell expansion. `test_report.path` must be within the new run directory. JUnit reports count actual testcase nodes, not an untrusted root summary; zero/all-skipped reports fail. Counts JSON uses exactly executed/failures/errors/skipped. These checks establish nonempty execution reporting, not coverage or clinical/functional validity.

## CI adoption

The staged workflow contains structure, engineering-plus-tests, native QA lint, and native QA release groups. The native release check is required on release branches and tags; unknown merge-queue targets use the stricter release gate. The final aggregate check fails on failed/cancelled/skipped required jobs. Set actual branch patterns and required status checks under protected policy review. Workflow bytes are not server settings. There is no `pull_request_target` or automatic deployment step.

## Schema scope

The bundled JSON validator implements only the keywords used by this distribution, and rejects unsupported schema keywords. Integration maps are validated semantically, including allowed field sets. Native formats outside the explicit JSON/CSV contract stay blocked. Empty starter files are drafts; no inferred catalogue hashes, test commands, owners or approval records are included.

## v2.1 tools and cache behavior

`native_bridge.py` reads mixed PM elements and separate QA case/link/run CSVs. `connect_bundles.py` discovers inputs and proposes bindings; it never runs companion code or applies configuration. `migrate_exceptions.py` proposes a lossless merge and ID receipt for the original v1 engineering register. Both helpers require `--write-proposal` before creating proposal files, use new UUID directories, and leave installed/native records untouched. See [native adapters](../01-governance/native-bundle-adapters.md).

Package integrity ignores Python `.pyc` files under `__pycache__` and known pytest cache files. Every manifest-listed payload is still hashed, including any such path explicitly listed by a manifest. Symlinks and unexpected Python source hidden under a cache directory are rejected. The cache filter is not a general ignored-files escape hatch. Ordinary `pytest` no longer requires `PYTHONDONTWRITEBYTECODE`.
