# YZT-73 / deployment-v1-release-mapping — delivery report

Status: **READY_FOR_05_TARGETED_REVIEW** (candidate-level; not a release APPROVE).
Base: `0150df8d1d9ce745826096a5a88fec770b78099f` (D2-reviewed correction-1; bundle `42ca7fce2314bf0522ae81e46025c63d99712366`).
Branch: `yzt-73-d1-deployment-v1` (isolated worktree `D:/AI/worktrees/multica-memory-yzt-73-d1`); this commit SHA is recorded in the YZT-73 delivery comment.
No live apply, no Merge/push, no Core/Canonical revision, no runtime/model/credentials change, no production ledger/Finding access.

## Bound selfcheck (single run, gate for this work)

- One run only, exit 0: `handoff_pipeline.py selfcheck --role software-engineer` + explicit envelope `result.json` + binding/trusted-map/observation + artifact store/requirements + `R1`; observer run `01a095cf-079c-75f9-8ddf-70bb974ba6da`.
- Result: `READY` / `USE_EXISTING`; package `CTX-software-engineer-9cbb0786b379b512`; `ARTIFACT_READY` with dependency `sha256:ca0ef61a…` unchanged; snapshot `sha256:a6737e12…`; binding `sha256:549dc076…`; open Finding `FIND-WIMG-HO00-000001` visible and not task-associated.
- Evidence kept in the run workdir: `selfcheck-release-mapping/` (result + freshness) and `selfcheck-release-mapping-logs/` (argv, stdout/stderr, exit, timestamps).

## Scope executed (adapter / manifest layer only)

1. `adapters/multica/role-bindings/role-bindings.v2.json`
   - `delivery-reviewer` now binds the distinct live agent `edff0eba-eef8-4188-9780-a1402b2b67dd` (`05 Delivery Reviewer`, active), with its resolved skills `delivery-review` `98d6bf60…` + `parent-handoff-wake` `aab482f9…`.
   - Old 05 `b6335f8e-8147-45f7-aac0-8079d85423b5` moved to `historical_identities` as `retired_no_alias`; the old `platform_cutover_note` UUID-reuse statement is removed and replaced by a history-only/no-reuse binding note.
   - `roster_evidence` now points to the new dated live capture `platform-roster-2026-09-12.json`.
2. `adapters/multica/deployment-v1/binding-plan.json`
   - `new_05.agent_id` filled; `ops_pre_d2` recorded as executed + live-read-back (Lead/Human bootstrap); `capability_skills.delivery-review` id resolved; `multica-context-handoff` and `product-quality-gate` stay unresolved with an explicit resolve + read-back requirement (never bind a placeholder).
   - `ops_d3[0]` is the F2 hard prerequisite `capture_before_full_text` (full non-sensitive before text/ID/bindings/Squad + executable rollback material; missing capture ⇒ STOP; drift ⇒ STOP).
   - QA 06: the unbind is replaced by `qa_06_binding_exception_plan` (PLAN ONLY) — a precise 06-only `agent skills set` exception to be granted in the centralized Human release confirmation, computed from the latest full before set, keeping unrelated valid bindings, removing only `milestone-quality-gate`, with drift refusal, read-back and rollback; `agent skills set` stays disabled until then.
3. F1: `after-state-hashes.json` now carries a `_scope` naming bundle `42ca7fc` as historical and the five pin-HEAD docs; `RELEASE_MANIFEST.yaml` lists the five `0150df8` doc digests under `f1_after_state_scope.head_doc_hashes_f1`.
4. `tools/chandoff_instructions.py` and its disabled assignment/fallback/joint paths: **not modified** in this batch (per Lead decision). Static consumer recheck below. No supported manual entry consumes it; if that ever changes, stop and return to Lead.
5. Core/Canonical/six-role Profiles: zero revision (02 conclusion, comment `01a095b9`); PE authority is not covered by this work.

## F2 before-capture (executed this run; D3 must redo it at write time)

Captured live, read-only: `agent get` + `agent skills list` for 01/02/03/04/06, new 05, old 05 (and Mika for roster completeness), `squad get`, and `skill get --with-content` for `delivery-review`, `parent-handoff-wake`, `milestone-quality-gate`. Observed state:

- new 05 `edff0eba` = "05 Delivery Reviewer", idle, skills `delivery-review` + `parent-handoff-wake`;
- old 05 `b6335f8e` = still "05 Feature Reviewer" with the old three skills; not renamed/recycled;
- 06 `30ce43d4` = `milestone-quality-gate` + `parent-handoff-wake`;
- squad `ee895c79` = 6 members including old 05; new 05 not yet a member;
- runtime/model drift recorded as-is: creation-time observation `24277b62 / gpt-5.6-sol / 1` vs current `edc6fd68 / opencode-go/deepseek-v4.1-flash / 3`. Nothing was changed or inferred; D3 captures the actual before state at write time.

Rollback material and per-op inverses: `cli-operations.md`; the full raw capture set is retained in the run workdir (attached as delivery evidence). Not stored as hash-only.

## F1 pin-HEAD document digests (0150df8)

| Document | bytes | sha256 |
| --- | --- | --- |
| `D1_REPORT.md` | 2716 | `sha256:d92167fb45661e1ec2554659b638d55691b4dd5e5418ed3737f778aef54c0b76` |
| `RELEASE_MANIFEST.yaml` | 2893 | `sha256:f9a89c6d0cacf9238aa4aee2453eafe370aaa83c76dcd0c2fd562c48f4789390` |
| `commit-classification.md` | 3087 | `sha256:49aa31707aa5647a091fae4e74b8a3c5229e785dc7698fd197564b84676f1a78` |
| `qa-baselines.md` | 1417 | `sha256:1c466cc1f48406582da1536f828585ddd593d00b2ea7f9d5dc0d68eb1fa28bef` |
| `revision-map.md` | 1816 | `sha256:5a65c1ac8482a44c3a150dcec743c2196fee694d144ecc4d226080ce2942aced` |

`RELEASE_MANIFEST.yaml` is itself rewritten at `0150df8` and edited again by this commit; its digest above is the `0150df8` value.

## Static / isolated-synthetic checks (run inside the D1 worktree)

| Check | Command | Result |
| --- | --- | --- |
| Role-binding validator | `python adapters/multica/role-bindings/validate_role_bindings.py` | OK: 6 roles bound once; new 05 in roster; old 05 history-only; 8 UUIDs absent from Core/API |
| Release-mapping validator (new) | `python adapters/multica/deployment-v1/validate_release_mapping.py` | OK: mapping/history/resolved-IDs/F2-step/QA-plan/F1 digests/synthetic compose all hold |
| F1 blob recheck | `git show 0150df8:<doc>` digests vs manifest | all five match (inside validator) |
| `chandoff_instructions` static consumers | `rg -n "chandoff_instructions" <supported modules>` | 0 matches, exit 1 |
| Disabled-chain importers | `rg -n "chandoff_instructions" chandoff_{assignment,joint,fallback,mention,intent}.py` | 5 matches, all disabled auto-dispatch paths |
| Supported → disabled import check | `rg -n "chandoff_(assignment|joint|fallback|mention|intent)" <supported modules>` | 0 matches, exit 1 |
| JSON parse | `json.loads` on role-bindings / binding-plan / after-state-hashes | OK |

No production-readable full discover was run; no live CLI write, no `agent skills set`, no Memory main Merge.

## Affected verification list for 05

1. `role-bindings.v2.json` diff: new 05 active binding, old 05 historical entry, no carry-over reuse text.
2. `binding-plan.json` diff: resolved IDs, F2 first step, resolve+read-back for future imports, QA exception plan-only.
3. `RELEASE_MANIFEST.yaml` diff: attempt, selfcheck block, F1 digests, review target.
4. `after-state-hashes.json` `_scope` (27 items unchanged).
5. New roster capture vs live listing (8 agents incl. both 05s).
6. Unchanged: `tools/chandoff_instructions.py`, `after/*.md`, `skills/*`, `cli-operations.md` semantics.
7. Not run: live apply ops, `agent skills set`, full discover, Memory main Merge.

## Deviations

- The role-binding validator now resolves the roster through `role-bindings.v2.json.roster_evidence` and also checks `historical_identities` (framework adapter layer only). A dated live roster capture `platform-roster-2026-09-12.json` was added; the `2026-09-10` capture is left untouched as history.
- New `validate_release_mapping.py` added for reproducible static + isolated-synthetic checks (stdlib only; optional PyYAML; uses git when available).
- `ops_pre_d2` records a bootstrap that Lead/Human already executed; 04 performed no live change.

## Boundaries / stop conditions

- No live identity/config change, no Merge/push, no D3 execution; QA unbind stays plan-only; `agent skills set` disabled until the explicit 06-only exception.
- If any supported manual entry is later found consuming `tools/chandoff_instructions.py`, stop and return to Lead (do not widen the fix).
- New substantive change requires a new 05 targeted review of the new HEAD; the `0150df8` APPROVE is not extended.

## Next

1. 05 targeted review of this commit (child handoff via Lead on parent YZT-66).
2. Centralized Human release confirmation to include the 06-only precise `agent skills set` exception.
3. D3 remains blocked until those close.
