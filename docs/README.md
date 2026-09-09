# docs — archived design authorities

Verbatim archives of the design documents that govern this repository. The
source of truth for each is the Multica issue attachment it was downloaded
from; these copies exist so the memory repo is self-describing without network
access. Do not edit archived files — supersede by archiving a new version.

| File | Authority | Source |
|---|---|---|
| `multica_context_memory_system_v1.1_design.md` | V1.1 Memory System design (concept authority for the completed rebuild, YZT-39) | YZT-39 attachment, 2026-09-08 |
| `context_handoff_native_api_design_v1.1.md` | Native API design — capability semantics, Core / Semantic Runtime / Adapter boundaries (YZT-46) | YZT-46 attachment, 2026-09-09 |
| `context_handoff_multica_implementation_v1.1.md` | Multica implementation plan — task order T00–T13 and landing constraints (YZT-46) | YZT-46 attachment, 2026-09-09 |

Where the two handoff documents conflict on implementation detail, the Native
API design's framework-neutral semantics win (frozen in
`schemas/context-handoff/README.md`).
