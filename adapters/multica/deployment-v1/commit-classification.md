# Commit classification

## Counts (verified this attempt)

| Range | Count | Dirty |
| --- | --- | --- |
| `95c434d..58ba5d8` (code start) | **83** | 0 on kept worktree `multica-memory-yzt-88-u12fsb` |
| `95c434d..32d3118` (historical D1 bundle) | 84 | n/a |
| `95c434d..119812c` (historical D1 pin HEAD) | **85** | 0 on `yzt-73-d1-deployment-v1` before correction-1 |
| `58ba5d8..119812c` | 2 | D1 docs + pin |
| `95c434d..42ca7fc` (correction-1 bundle) | **86** | isolated branch only |

Do not say the D1 branch is 0/83. That count belongs only to `58ba5d8`.

These 83 commits are one continuous Context Handoff / V2.2 stack, not 83 defects. D1 does not cherry-pick them into 83 review tasks. D1 does not Merge Memory main.

## Must-use for the current supervised manual surface

| Range | Why required |
| --- | --- |
| T01–T07 (`8af8735`…`c8f9847`) | PLAN / compose / FINALIZE / SELF_CHECK / adapter / non-trigger `/note` / shared skill |
| T08 staging (`b85f86f`…`2b41e82`) | Historical instruction contract; **not** a live apply input |
| U01–U03 (`6eba026`…`2e1959b`) | Role vocabulary from profiles; Frozen T00 preserved |
| U02 roles (`b45ab3b`…`0a35592`) | Six logical roles; `feature-reviewer` retired with no alias |
| U10 + U04 (`73f922e`, `702cb9b`) | Artifact Contract + skill `artifact_ready_check` |
| U05 staging (`b43b68b`) | Historical after-state text; **do not deploy that bundle as-is** |
| YZT-88 findings binding (`261df9a`…`58ba5d8`) | Explicit Findings source, trusted map, official-path guards used by this worker selfcheck |

## Retain in tree, do not enable this batch

| Range | Why retained / disabled |
| --- | --- |
| U06–U08 (`f35afdf`…`6f541f7`) | Assignment / mention / fallback SAFE_DISPATCH. Keep code. Default Instructions must not call unvalidated auto-dispatch. |
| U09 (`561533c`…`53f0840`) | Finding/Challenge alignment. Keep. No Finding drain-to-empty. |
| U11 (`964f935`…`6439bd4`) | Joint replay harness. Evidence, not a live gate. |
| O2 (`c24284a`…`49c48a9`) | Durable dispatch intent. Not this batch's default start path. |
| U12-P0 / P0R / R0B (`6a6c018`…`c221c6b`) | Production ledger, receipt gate, publication recovery. Keep. Do not open as default or run full discover against production ledger. |

## Out of this batch / would change production routing or data if enabled

- Live six-role/Squad/Skill apply (D3 after Human publish confirmation).
- Memory main Merge.
- Creating a live 05 agent (Lead/Human bootstrap only; pre-D2, not this 04 run).
- Migrating or emptying `D:\AI\multica-memory\runtime\v1.1\findings`.
- Restoring T06 full discovery or automatic assignment as the ordinary start path.

## Integration recommendation

**Integrate the whole 83-commit chain as the Memory candidate**, then keep D1 documentation commits on `yzt-73-d1-deployment-v1`. Do not squash away YZT-88 guards. Do not blind-merge to `main` in D1.

`58ba5d8` is the code start. Historical `119812c` is CHANGES_REQUIRED. The correction-1 commit is a **new** review target and must not be called `58ba5d8` or `119812c`.
