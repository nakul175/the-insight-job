# Engineering index

This repository adopts the pinned package at `.aplyd/engineering/2.1.0/` after its real profile and owner review are complete.

| Record | Location |
|---|---|
| Repository mapping and owners | `.engineering/repository.json` |
| Reviewed engineering commands | `.engineering/commands.json` |
| Engineering traceability | `.engineering/traceability.json` |
| Exceptions | the shared register selected by `exception_register` |
| Master coding standard | `.aplyd/engineering/2.1.0/CODING_STANDARD.md` |
| Product baseline | Use the actual index/reference recorded in the profile |
| Current design | Use the actual canonical HLD/LLD/ADR bindings in the profile |
| Current work and handovers | Record actual issue/PR and handover references here |

The starter is draft, not an approved product baseline. Populate real paths and owners; preserve existing authoritative product documentation. Keep execution logs in an approved protected location and out of source control by default.

Final R1 prototype implementation evidence: [eight-batch ledger](batch-execution.json), [final batch verification](batch-08.md), and `.engineering/traceability.json` cover all79 mapped canonical stories. Merge/main CI and separate AI review are engineering milestones; native acceptance and production deployment remain unverified. Historical per-story reports retain their authored checkpoints.
