# Revision map — do not collapse these SHAs

Old D1 reported `main...HEAD` as 0/83 while HEAD `119812c` was **0 dirty / 85 ahead**. Use this table.

| Label | Object | `git rev-list --count 95c434d..<object>` | Role |
| --- | --- | --- | --- |
| Memory main | `95c434d894c2a25d0b1f435c403d84950996cbfa` | 0 | Merge target. Not this batch. |
| Code start / YZT-88 candidate | `58ba5d8d92fa82b07330e5aacd4e8b9fe1a9bbc8` | **83** | Kept worktree `D:/AI/worktrees/multica-memory-yzt-88-u12fsb`. Package/selfcheck runtime. **Not** the D1 review target. Dirty files: 0. |
| Historical D1 bundle | `32d311896d8ebfcded704b5f30086c5d38e5c76d` | 84 | First D1 docs commit. CHANGES_REQUIRED. |
| Historical D1 pin HEAD | `119812c85d47bf086c2987f6e4bafd80d47ef0a4` | **85** | Pin of `32d3118`. History, **not** a deployable baseline. |
| This correction bundle | filled after commit as `correction_bundle` | 86 expected | Content of correction-1. D2 reviews this SHA (or its pin if a SHA-fill commit follows). |
| Evidence (not a Memory commit) | bound selfcheck observer `01a09521-7050-7b5e-b2e9-5aa218a020c7` | n/a | Package `CTX-software-engineer-ca95e5f1cce82f65`. Snapshot `a6737e12…`. Binding `9af94753…`. |

`58ba5d8..119812c` = **2** commits (bundle + pin). `95c434d..119812c` = **85**, not 83.

## How to name SHAs in review

- **Bundle commit**: the commit that contains `adapters/multica/deployment-v1/` correction-1 files.
- **Pin / branch HEAD**: current `yzt-73-d1-deployment-v1` HEAD. If a follow-up pin only fills SHAs into `RELEASE_MANIFEST.yaml`, say so; do not call the pin `58ba5d8`.
- **Final reviewed commit**: the exact SHA D2 writes in `Reviewed Artifact`. Must not be `58ba5d8` or `119812c`.
- **Actual main diff**: `git rev-list --left-right --count 95c434d...<reviewed_sha>` plus `git status --short` on that worktree.

Original candidate worktree at `58ba5d8` stays clean and is not rewritten.
