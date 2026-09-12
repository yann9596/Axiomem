# 83-commit classification (main `95c434d` → candidate `58ba5d8`)

Count verified: `git rev-list --count 95c434d..58ba5d8` = **83**. Dirty files on the kept candidate worktree: **0**.

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
- Creating a live 05 agent (Lead/Human bootstrap only).
- Migrating or emptying `D:\AI\multica-memory\runtime\v1.1\findings`.
- Restoring T06 full discovery or automatic assignment as the ordinary start path.

## Integration recommendation

**Integrate the whole 83-commit chain as the Memory candidate**, then add this D1 documentation commit on `yzt-73-d1-deployment-v1`. Do not squash away YZT-88 guards. Do not blind-merge to `main` in D1.

`58ba5d8` is the code start. Any D1 artifact commit is a **new** release candidate SHA and must not be called `58ba5d8`.
