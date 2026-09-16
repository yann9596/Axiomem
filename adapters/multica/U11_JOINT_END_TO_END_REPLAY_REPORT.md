# U11_JOINT_END_TO_END_REPLAY_REPORT

- report revision: **v2 (F-07 committed-bundle + needle regeneration, YZT-109)** — the sections below are the U11 delivery text; every number that depends on the regenerated bundle was re-derived at the revision baseline and is listed in `## Revision 2 — F-07 Regeneration Verification`.
  - based_on: `833868092d4b45b8c965f79dbdac6f8ba466c7f5` (branch `yzt-105-f01-f03-isolation-lf`, direct parent `fe27c15e34c61cc27112b065eb4a181c31e4fcc8`, tree `31b8dde4fba518bcb1f77dc1b46bc74dbdd15378`)
  - supersedes: the U11 report bytes previously committed at this same path (report v1, base `49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9`; v1 matrix evidence digest `sha256:5154381f…`, v1 bundle `sha256:ee15c79e…` (17 files) / `sha256:04e0da84…` (18 files incl. capture)). Revision 1 is **not** edited away: it stays byte-intact in Git history and is replayable with `git show <v1-commit>:adapters/multica/U11_JOINT_END_TO_END_REPLAY_REPORT.md`.
  - validated_against: `83386809` in an isolated clone; the read-only source repository `D:/AI/multica-memory` was not modified.
  - regeneration command: `python tools/chandoff_joint.py bundle --out-dir adapters/multica/joint-replay` (exit 0, `generated_at` `2026-09-11T00:00:00Z` preserved).
  - needle policy: only the `final-gate matrix evidence digest` `sha256:14b892c2…` is authoritative. The v1 digest `sha256:5154381f…` and the CRLF-dependent (non-LF-normalized) digest `sha256:747494e1…` are **not** used and must never be written into this report.
- tool: `tools/chandoff_joint.py` (U11/1.0); focused tests `tools/tests/test_joint_replay.py` (72 tests at U11 delivery; 75 tests at the v2 baseline)
- branch: `yzt-80-u11-joint-replay`; base: exact O2 report commit `49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9`
- feature commits: `964f935` (harness, fixtures, evidence bundle) and `e855fa1` (closed-source guard replay + finding-drain coverage)
- status: **simulation_only** — no live issue/comment/assignment/rerun/mention/status/run mutation was created or mutated; no live Delivery Review/QA activation, U12 enablement, production ledger-root selection, O3, platform change, Canonical/product write, or merge was performed

## Verdict

Ready for Review. The exact accepted U03–U10 + U09 + O2 lineage composes into one deterministic, fail-closed joint workflow: R0/R1/R2 topology with exact-artifact binding, the O2 dispatch/reconciliation matrix (including both confirmed YZT-77/YZT-78 captures), U09 Finding/Challenge and drain semantics, and stage/wake idempotence. Every Parent Final Gate counter and every O2 safety gate in the Joint End-to-End Final Gate matrix is **0**, replay integrity is clean (0 failing dispatch/topology/artifact/finding/stage replays), the capability proof passes 6/6 on a caller-supplied absolute shared ledger root, and the predecessor/O2/U09 pins and bundle digests reproduce. No `U11_NOT_READY_FOR_ENABLEMENT` stop was triggered; no blocker remains for the Lead/Human U12 decision.

## Authorization

- Human decision `使用方案C` on parent YZT-66 authorizes **O2 only** and explicitly does not authorize O3, live rollout, Review/QA activation, U12, or merge. This U11 task is simulation-only and performed none of those.
- Parent YZT-66 Authority Order, corrected execution order, SAFE_DISPATCH, Artifact Contract, Review/QA Option A, Finding/Challenge boundaries, Hard Stops, Replay and Final Gate were applied in the stated order (Human decision → parent authority → accepted O2 artifact → immutable lineage → read-only CLI evidence).
- Dispatch of this task followed the accepted O2 SAFE_DISPATCH path: one non-trigger READY `CONTEXT_HANDOFF_RECORD v1` (`CTX-software-engineer-34690dfaf2d1344e`) on YZT-80, then exactly one `issue rerun` returning exactly one correlated 04 run (`01a08e6e-56f8-7822-bac8-d0bf292ff444`); no Assignment, status promotion, Mention, or second trigger was used.
- all role/Review/QA topology in this report is deterministic fixture replay. Live 05 remains the retired Feature Reviewer identity; live 05/06 activation is not authorized.

## Preconditions

- New isolated worktree `D:\AI\worktrees\multica-memory-yzt-80-u11` created from exact O2 report commit `49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9`; memory `main`, all prior U worktrees, Canonical Memory and `D:\AI\projects\opencode-web-imagegen` were not modified.
- SELF_CHECK before consequential work: package `CTX-software-engineer-34690dfaf2d1344e`, target `software-engineer`, artifact dependency set (ART-WIMG-O2 `@49c48a9`, lineage U03/U04/U05/U06/U07/U08/U09/U10, ISS-WIMG-080, LEAD-O2-ACCEPTANCE), `backlog`, exact assignee, one authorized run — all matched the published READY boundary.
- O2 baseline reproduced on the clean base before any change: full `tools/tests` **892/892 OK** (37.9 s), capability proof **6/6** (`all_passed: true`), T00/framework scan clean, Gates A/B/C pass at HEAD (Gate A authority `invalid_count 0`, Gate B 10/10 tests, Gate C 6/6 replays), predecessor pins/bytes reproduce, and the O2 evidence-bundle digest recomputes to `sha256:3c207e85f195617fe50914e3384dd309e5847d4c6e55f33a96f328bae0924d4d` (16 files).

## Accepted Inputs

| input | pin |
| --- | --- |
| Parent YZT-66 | Authority Order, corrected execution order, SAFE_DISPATCH, Artifact Contract, Review/QA Option A, Finding/Challenge boundaries, Hard Stops, Replay/Final Gate |
| Human decision | `使用方案C` (O2 only) |
| O2 artifact | branch `yzt-79-o2-dispatch-intent`, commits `c24284a` + report `49c48a9`; `O2_DURABLE_DISPATCH_INTENT_REPORT`; bundle digest `sha256:3c207e85…` |
| U09 base | `53f08407007ec081a888f4fb693575c35ae4aeff` (bundle `sha256:d598d2c5…`) |
| U10 fixtures | `tools/fixtures/artifact-contract/store-chain.json` (+ `ready-r0/r1/r2`, `implementation-b`), consumed read-only |
| U05 pins | instruction bundle `sha256:8f0b2b30…`-pinned revision, binding plan `sha256:…`, artifact contract `sha256:9c2857ae…` (reproduced via `chandoff_assignment`) |
| YZT-77 / YZT-78 captures | sanitized fixtures `adapters/multica/dispatch-intent/fixtures/yzt-77.json`, `yzt-78.json` |
| CLI evidence | `multica v0.4.42` (read-only capture `adapters/multica/joint-replay/capture/rerun-receipt-capture.json`) |

## Exact Revisions / Digests

| artifact | pin |
| --- | --- |
| base commit | `49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9` |
| feature commits | `964f935`, `e855fa1` |
| `tools/chandoff_joint.py` | `sha256:2afc229364e6202bef9fae0c5bba264194210d7d1797b5c6cdc39735fc6b6208` (LF-normalized) |
| `tools/tests/test_joint_replay.py` | `sha256:159b413dba25607159339d6a852d0481ba43f10272ef80dd71b0f2ecab4e2a94` (LF-normalized) |
| U11 evidence bundle (17 generated files) | `sha256:50e702dac5aa3f6ed3cb0d47538488939e0eec249bf31ea22809a5b174113b5e` |
| U11 evidence bundle incl. capture (18 files) | `sha256:2047cc98737ea5fd82e7b7b3cb0199afe2480ef41c9bf0eca54a21fa6e1228c9` |
| `final-gate-matrix.json` (file) | `sha256:d8f7c84cc364be3a415878a177881d948f312be78a391138d6cfbbd7ec98f868` |
| final-gate matrix evidence digest | `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5` |
| `closed-source-guard.json` | `sha256:2517d6b19f708d5f13780b489d77668a4adcacf7f5962acf70361a77de2db0bb` |
| `finding-drain.json` | `sha256:dc3c26b2dd63099e9a80965cfefd46f51e049f3bc55f4e454aa45c3782d77a9b` |
| `o2-recovery-matrix.json` | `sha256:df3e2cde7d29c485f5f8ddec78704f673e818c260c09b3c06d3cc33dbd1f2d39` |
| `capability-proof.json` | `sha256:ca57913de6c7a58ce526dd6c7eca20745f1e48471368e4a97a2b2bf3ebaeb411` |
| `side-effect-audit.json` | `sha256:084002452de271abb836b676ada2ca0eca6eb9673ba66f5de3f1ef5b1a1932aa` |
| `replays.json` | `sha256:d18fc0e68f2f7a115a425693bbc48433c99e255c4221a84c388cfb39feca239b` |
| rerun receipt capture | `sha256:b00f5664581592634a8b37899aec23b6d2e7ac41726919a320bef9b842cb24bd` |
| O2 bundle (unchanged) | `sha256:3c207e85f195617fe50914e3384dd309e5847d4c6e55f33a96f328bae0924d4d` |
| U09 bundle (unchanged) | `sha256:d598d2c5db96f3e16eed23627d1a9b8d007412fc21f29a5360f7addc81b005eb` |

Digest method: source files raw bytes with CRLF normalized to LF; bundle method canonical JSON of sorted `file name → sha256(bytes)`.

The five rows above were re-derived at `83386809` (report v2). Their v1 values, kept only as superseded history, are: bundle 17 files `sha256:ee15c79e…`, bundle 18 files `sha256:04e0da84…`, `final-gate-matrix.json` `sha256:a9ecc24c…`, matrix evidence digest `sha256:5154381f…`. The remaining rows are byte-identical to v1 — `closed-source-guard.json`, `finding-drain.json`, `o2-recovery-matrix.json`, `capability-proof.json`, `side-effect-audit.json`, `replays.json`, the capture file, and the O2/U09 bundles were all regenerated and reproduced exactly.

## Joint Topology

| level | route (simulated) | required Artifacts | exact binding | dispatches | result |
| --- | --- | --- | --- | --- | --- |
| R0 | Producer → Lead (stays in the producer task) | minimal `issue_definition` accepted | `ISS-WIMG-R0@1` | 0 review, 0 QA | `validate_routing` ok; review/QA route guards refused; 0 review/QA artifacts created |
| R1 | Producer → Lead → independent Delivery Review → Lead | `implementation` + implied exact review input | `ART-WIMG-031@aaa111` reviewed by `REV-WIMG-008@1` (verdict APPROVE) | exactly 1 review run (`issue_rerun`), 0 QA | ARTIFACT_READY; one correlated review run; envelope valid with exact `reviewed_artifact` |
| R2 | Producer → Lead → Delivery Review → Lead → QA → Lead | + `product_expectation`, `design_baseline`, `implementation`, `delivery_review(APPROVE)` | `PE-WIMG-004@4`, `SOL-WIMG-017@3`, `ART-WIMG-031@aaa111`, `REV-WIMG-008@1` validated by `QA-WIMG-004@1` (PASS) | 1 review run + 1 QA run | all exact baselines present; both dispatches correlated; QA envelope valid |

- Lead-mediated at every R1/R2 transition; producer auto-trigger and reviewer auto-trigger are rejected (`AUTO_TRIGGER_FORBIDDEN`, `ROUTING_MISMATCH`); R1 has no QA route; the routing negatives row shows every malformed plan refused with `triggers_created: 0`.
- Retired identity: `runtime_role_vocabulary_proof` ok; retired token does not resolve and has no alias; live/staged display names never resolve; the old 05 package is refused against the new route (`old_role_package_rejected`, `no_alias_rewrite`, `legacy_05_skill_binding_present`); O2/U09 role guards and `validate_binding` refuse the retired identity; role profile absent; `old_05_package_accepted: false`, `feature_reviewer_activation: 0`.
- Superseded implementation or stale package/PE/design/build before trigger returns `REFRESH_REQUIRED` or the exact typed block: `READY_NOTE_MISSING`, `PACKAGE_STALE`, `ARTIFACT_STALE`, artifact-contract `SUPERSEDED`/`STALE`/`VERSION_NOT_EXACT`; no run is created on any of these paths.
- Historical Review/QA verdicts are never edited: superseding the reviewed implementation marks `REV-WIMG-008@1` and `QA-WIMG-004@1` stale with their verdicts (APPROVE/PASS) preserved, `rewritten_verdicts: 0`, and a new attempt is a new envelope.

## Artifact / Revision Replays

- `r1_review_input_ready` / `r2_qa_baselines_ready` / `r0_minimal_ready`: ARTIFACT_READY on exact versions.
- `implementation_superseded`: ARTIFACT_NOT_READY (`SUPERSEDED`); `pe_superseded_blocks_qa` / `design_stale_blocks_qa`: ARTIFACT_NOT_READY; `latest_version_refused`: ARTIFACT_NOT_READY (`VERSION_NOT_EXACT`); `qa_missing_baseline_refused`: ARTIFACT_NOT_READY; `qa_after_changes_required_refused`: ARTIFACT_NOT_READY (`QA_GATE_BLOCKED`).
- `verdict_preservation`: stale + verdict-preserving history, new attempt recorded, 0 rewrites.
- All artifact fixtures carry exact versions, `based_on` / `reviewed_artifact` / `validated_against` provenance and the seven core types (`artifact-set.json`).

## O2 Dispatch / Reconciliation Replays

23 replay cases (`o2-recovery-matrix.json`, all pass), each served by an injected fixture runner with zero live mutation:

- **Consumed captures**: `yzt_77_fixture_replay` (backlog + same assignee → one `issue_rerun`, no binding), `yzt_78_fixture_replay` (backlog + assignee change → `assign --no-start` binding not counted as enqueue, re-read, one `issue_rerun`); both `RUN_CORRELATED`, 1 trigger, audit ok, 0 write replay of the historical Assignment attempts.
- **Matrix cases**: backlog/same-assignee (rerun), backlog/different-assignee (binding + rerun), active/unassigned (Assignment trigger), active/same-assignee (rerun).
- **Crash/recovery**: publish/pre-trigger kill (`POST_PUBLISH_PRE_TRIGGER` → `RESUME_ISSUE`, no duplicate publication), lost trigger response (`TRIGGER_AMBIGUOUS`, later exactly-one-run attach, no reissue), delayed run visibility (`TRIGGER_ISSUING` await → attach), ambiguous create (exactly-one discovery attach; zero/many → `CREATE_AMBIGUOUS` with exactly one create attempt), intent-before-target ordering (create without intent refused; intent record seq < create command seq).
- **Fail-closed anomalies**: duplicate correlated runs, wrong-target run, pre-existing unexpected run, CLI success without a correlated run (not delivery), provider/quota after correlation (`EXECUTION_RECOVERY_RECORDED`, no redispatch, not an orphan), stale lease recovery (`expired_previous`), two-reconciler race (`lease_held` / `lease_not_held`, one winner).
- **Invariants**: `TRIGGER_ISSUING` is persisted before the native call in every triggering path (`issuing_precedes_trigger_command: true`); exactly one selected trigger per transaction; every ledger shows ≤1 trigger, ≤1 ownership binding, ≤1 create; completed replay emits zero note/issue/trigger/run side effects and replays idempotently; legacy ledger prefixes are byte-preserved while new intent records remain readable by both readers; an open intent is visible in observability with its exact next owner/action.
- **Caller-supplied shared root**: `capability_proof` ran over a caller-supplied absolute ledger root in independent processes/workdirs — all 6 observations pass (`cross_process_append_enumerate`, `single_writer_race`, `stale_lease_recovery`, `cas_conflict`, `duplicate_intent_fail_closed`, `partial_record_fail_closed`). No production root was selected or deployed.

## Finding / Challenge Replays

- Local delivery-review/QA defects stay in their exact review artifact (`keep_in_artifact`, `local_artifact: true`); reusable cognition turns the same origin into a runtime finding (`runtime_boundary_processing`); neither emits a direct trigger.
- `CHALLENGE_CONTEXT` precedes any exceptional 02 handoff: ordinary gaps resolve with `CONTINUE` and no Context Engineer wake; unresolved material findings produce a Lead-addressed escalation proposal with `trigger_emitted: false` and `requires_lead_owned_safe_dispatch: true`; no ordinary READY build calls the Context Engineer (`ordinary_ready_build_calls_context_engineer: 0`).
- `DESIGN_DEVIATION` (`actual_not_design`) and `DESIGN_CHALLENGE` (`design_baseline_wrong`) are distinct Lead-decision classifications; product expectation conflict routes to Lead/02 by decision only.
- External raw signal stays Evidence-only (`evidence_pointer_only`, `authority_eligible: false`, `promotion_allowed: false`, stored `unverified`), so Grok raw output cannot become Project Truth or dispatch 04/05/06.
- Capture is capture-only (`process_now/wake/canonical/direct_trigger: false`) and capture flags cannot promote to project truth/Rule/Authority.
- Task-completion drain: an unaccounted open finding blocks conclusion (`task_closed_with_open_unaccounted_finding` detected), explicit dispositions drain and replay idempotently (`writes: 0` on replay).

## Stage / Wake Replays

- Lead exit invariant: a due target covered by `RUN_CORRELATED` passes; an underspecified parked claim (no dependency / next owner / wake boundary) blocks (`PARKED_TARGET_UNDERSPECIFIED`); a due target without intent blocks (`DUE_TARGET_UNTRACKED`) even when a later Lead action is named.
- Failed parent wake is execution recovery, not redispatch: first failure → `WAKE_ONE`; an active/pending Lead run → `WAKE_REPAIR_REQUIRED`; a second failure → `WAKE_EXHAUSTED`; a delivered wake then `NO_NEW_WAKE` (`duplicate_lead_stage_activation: 0`); wake records never combine with a trigger.
- Closed-source execution guard (U08 Finding 4, deferred to U11): a source transaction closed by a fallback is never resumed (`resume_source_route` → `ROUTE_CONFLICT` before any native call, 0 native calls), and a later source-route reactivation is detected as `closed_source_route_reactivated`; the same ledger without reactivation does not flag it (`closed-source-guard.json`).

## Parent Final Gate Matrix

Machine-readable: `adapters/multica/joint-replay/final-gate-matrix.json` (evidence digest `sha256:14b892c2…`). All 20 parent counters are **0**:

- `feature_reviewer_activation`, `old_role_package_accepted`, `wrong_role_routing`, `normal_path_run_before_ready_handoff`, `duplicate_intended_run`, `duplicate_review_run`, `duplicate_qa_run`, `duplicate_lead_stage_activation`, `scope_pollution`, `invalid_rule_authority`, `hidden_unresolved_conflict`, `stale_or_missing_package_not_detected`, `stale_artifact_triggered`, `stale_baseline_received_qa_pass`, `relevant_finding_hidden`, `task_closed_with_open_unaccounted_finding`, `ordinary_ready_build_calls_context_engineer`, `grok_raw_signal_used_as_project_truth`, `qa_without_required_baseline`, `delivery_review_without_exact_artifact_version`.

All 12 O2 safety gates are **0**: `target_mutation_before_intent`, `underspecified_parked_target`, `backlog_assignment_counted_as_enqueue`, `cli_success_counted_as_delivery`, `duplicate_note`, `duplicate_trigger`, `duplicate_correlated_run`, `ambiguous_trigger_retry`, `provider_failure_counted_as_orphan`, `hidden_open_intent`, `legacy_history_rewritten`, `u09_drift`.

Replay integrity: dispatch failures 0, topology failures 0, artifact failures 0, finding failures 0, stage failures 0, retired identity ok, routing negatives all refused, closed-source guard ok, finding drain unaccounted 0, capability 6/6.

## Tests

Scope note for this section: the three bullets below describe the **U11 delivery run** on branch `yzt-80-u11-joint-replay` (base `49c48a9`) and are kept verbatim as revision-1 evidence. They are not a claim about the `83386809` baseline; the current targeted results are in `## Revision 2 — F-07 Regeneration Verification`.

- baseline on base commit `49c48a9`: full `tools/tests` **892/892 OK**; focused predecessor suites (O2/U09/U08/U06/U07/U04/U10/U03) 501 OK; capability proof 6/6; T00 scan clean; Gates A/B/C pass; pins/bytes and O2 bundle digest reproduce.
- final: focused `tools.tests.test_joint_replay` **72/72 OK**; full suite **964/964 OK** (892 base + 72 U11); T00/framework scan clean; U11 boundary scan clean (no live-runner/network surface in the harness); zero-secret fixture scan clean; bundle regeneration byte-for-byte deterministic and committed bundle matches regeneration.
- Evidence regeneration: `python tools/chandoff_joint.py bundle --out-dir adapters/multica/joint-replay` reproduces all 17 generated files exactly; `matrix` and `scan` CLI paths are deterministic.

## Side-Effect Audit

`side-effect-audit.json` (digest `sha256:08400245…`): live mutations or triggers **0**; Canonical writes **0**; product-repo changes **0**; predecessor/O2 history rewrites **0**; U09 drift **0**; unaccounted findings **0**; live Review/QA activation **0**; production ledger root selected or deployed **false**; rerun idempotency claimed **false**; merges **0**. All native commands inside the replays were served by injected fixture runners (`live_mutations: 0` on every case); the only live CLI interaction was read-only capture (`version`, `--help`).

## Compatibility

`compatibility-pin-manifest.json` (digest `sha256:2db0869e…` at the v2 baseline; v1 was `sha256:49622f25…`): **3 of 5** predecessor runtime pins still reproduce byte-for-byte (`chandoff_finding.py`, `chandoff_dispatch.py`, `chandoff_fallback.py`). Two do **not**, so the regenerated manifest records `all_reproduce: false`:

- `tools/chandoff_assignment.py`: actual `sha256:acc67d823b7ea4f646f342723e463704196ac815d820ae518ce9c8e119cbcb1c` vs U11-era pin `sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129`
- `tools/chandoff_mention.py`: actual `sha256:6b95dc8a61fc983bc0f9bd716da4bf3dd170f05ecb0639d75c64242cf90f6f19` vs U11-era pin `sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1`

Both files are LF-normalized digests; both drifts come from the post-U11 `yzt-88` Findings-source-binding work (`261df9a`, `d2b6299`, `2efb52f`, `58ba5d8`) that changed the assignment/mention runtimes after the U11 evidence base `49c48a9`. This is a **pin-baseline** condition, not a U11 replay regression: `chandoff_joint.py` and the frozen test assertions were deliberately **not** re-pinned by this task (pin/whitelist updates are F-04/F-05 and stay with the Lead). It is reported, not repaired, and it is the reason this report no longer claims universal predecessor-pin reproduction.

The O2 evidence bundle still hashes to `sha256:3c207e85…` and U09 to `sha256:d598d2c5…`; U05 instruction/binding/artifact-contract pins and `old_05_package_accepted: false` / `feature_reviewer_resolves_to: null` reproduce; the U10 artifact-contract revision and store-chain digest (`sha256:9c754a93…`) are recorded. Only forward-only U11 files were added.

## Deviations

1. U11 is delivered as a new forward harness (`tools/chandoff_joint.py`) composing existing accepted modules; no predecessor file, pin, schema, Frozen T00 surface, or accepted history was modified.
2. The closed-source "live guard wiring" recorded in U08 Finding 4 is exercised end-to-end in **simulation** (reconciliation refusal + audit detection) rather than by patching the byte-pinned U07 runtime; the guard already lives in U08's resume path, and U11's own authority forbids live runtime changes. Any change to that boundary becomes a U12 decision.
3. The parent self-wake duplication guard is honored by contract: this report is delivered by the child result plus the mandated parent YZT-66 comment; no additional mention is generated inside the harness.
4. `rerun` receipt parsing accepts the three observed shapes; correlation still requires the trusted `issue runs` listing and U11 makes no idempotency claim.
5. The production ledger root remains caller-supplied; the capability proof records `production_root_selected: false`.

## Finding Drain

`finding-drain.json` (digest `sha256:dc3c26b2…`): unaccounted open findings **0**; the four U09-classified deferred findings keep their recorded gates with no redesign performed. `FIND-WIMG-U08-000004` (closed-source execution guard), deferred to `U11_JOINT_REPLAY`, now has joint-replay coverage evidence (`closed-source-guard.json`, digest `sha256:2517d6b1…`). The drain disposition itself remains a Lead-owned decision; U11 neither silently closed a gate nor repaired a finding by changing scope. No new blocking finding was produced by this task.

## Risks

- Production behavior of the shared ledger root (path, ACL, backup/retention, cross-machine lock behavior) is still unproven; the capability proof is one-machine OS-lock evidence.
- `issue rerun` idempotency under a lost response remains undocumented; O2/U11 only prove no automatic retry and read-only reconciliation.
- O2/U11 provide durable obligations and observability, not autonomous liveness: without a reconciler invocation, an obligation stays visible but does not progress.
- Ambient run listings remain advisory/capped (`issue runs --active/--siblings`); correlation trusts the full issue run listing, and truncation is treated as untrusted.
- The live Delivery Reviewer's display name is still the old 05 label until U12; the replay proves it cannot resolve or alias as a role token.

## Blockers

None for U11 acceptance. U12 controlled enablement remains parked pending the Lead/Human decision on the residual decisions below.

## Production Ledger Root Input

`residual-decisions.json` (digest `sha256:95f2ed36…`): `PRODUCTION_LEDGER_ROOT` remains a required U12 input — select and deploy the canonical absolute shared ledger root (path, backup/retention, ACL, cross-machine behavior) before enablement. U11 exercised only a caller-supplied absolute root and deployed nothing (`production_ledger_root_selected_or_deployed: false`).

## Rerun Receipt Contract

`rerun-receipt-contract.json` (digest `sha256:e897702e…`) with read-only capture `capture/rerun-receipt-capture.json` (digest `sha256:b00f5664…`, CLI `v0.4.42`): `issue rerun <id> --output json` has no idempotency key; accepted receipt shapes are exactly one run object, a one-element run list, or `{"runs":[run]}` with observable run contract fields; correlation still requires the trusted `issue runs` listing; a receipt alone never counts as delivery; truncated listings are untrusted; no idempotency claim is made. If the contract cannot be bounded in a future platform version, the correct action remains a typed stop, not a retry.

## Recommended Next Decision

Lead: accept this U11 joint replay as the pre-enablement evidence for the accepted O2 foundation, keep all U03–U10/O2/U09 pins immutable and live 05/06 disabled, drain `FIND-WIMG-U08-000004` with this replay evidence at the appropriate gate, and open the U12 controlled-enablement decision with the two recorded blocking inputs (`PRODUCTION_LEDGER_ROOT`, `RERUN_RECEIPT_CONTRACT`) plus the R0 → R1 → R2 canary order. Formal live Delivery Review/QA activation and U12 remain parked until that Lead/Human decision based on this report.

## Ready for Review

Yes. Review level R2 pre-enablement joint replay, simulation-only. Return the exact replay Artifact (`adapters/multica/joint-replay/`, matrix digest `sha256:14b892c2…`) to Engineering Lead; the R1/R2 live topology and U12 enablement have not been started.

## Revision 2 — F-07 Regeneration Verification

Task `multica://issue/YZT-109` (F-07, R1). Versioned companion documents: `adapters/multica/u11-f07/lineage-report-v1.md` (narrative lineage report v1) and `adapters/multica/u11-f07/evidence-manifest-v1.json` (machine-readable file-digest manifest, `manifest_digest` `sha256:bdb3463e…`).

- **Input**: `833868092d4b45b8c965f79dbdac6f8ba466c7f5` (tree `31b8dde4fba518bcb1f77dc1b46bc74dbdd15378`), reproduced in an isolated clone. The read-only source repository was not modified, and no other run's scratch directory was searched or guessed.
- **Command**: `python tools/chandoff_joint.py bundle --out-dir adapters/multica/joint-replay` — exit **0**, `generated_at` `2026-09-11T00:00:00Z` preserved. The generator, the tests, the pins and the immutability whitelist were **not** touched.
- **Digest confirmation**: the real generation returns `final_gate_matrix(capability=run_all()["capability"])["evidence_digest"]` = `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5`, matching the declared value. The superseded v1 needle `sha256:5154381f…` and the CRLF-dependent (non-LF-normalised) needle `sha256:747494e1…` are rejected and appear nowhere in the artifact.
- **Modified files**: only `adapters/multica/joint-replay/final-gate-matrix.json` (`sha256:a9ecc24c…` → `sha256:d8f7c84c…`) and `adapters/multica/joint-replay/compatibility-pin-manifest.json` (`sha256:49622f25…` → `sha256:2db0869e…`), plus this report. The other **15** regenerated files reproduced byte-for-byte; the live read-only receipt capture was not rewritten.
- **Bundle**: 17 generated files `sha256:ee15c79e…` → `sha256:50e702da…`; 18 files incl. capture `sha256:04e0da84…` → `sha256:2047cc98…`.
- **Determinism**: two independent regenerations are byte-identical, and regeneration matches the committed bytes 17/17. Across a CRLF and an LF checkout, 15/17 files are byte-identical and `final-gate-matrix.json` — hence the matrix evidence digest — is identical in both modes.
- **Line-ending/time rule**: the delivered bytes are the `core.autocrlf=true` Windows checkout, matching the original U11 generation environment; bundle byte comparison is valid within one line-ending mode. `generated_at` is the fixed `2026-09-11T00:00:00Z` constant, so byte comparison is time-stable. All source digests cited in this report are LF-normalised.
- **Targeted tests** (`python -m unittest tools.tests.test_joint_replay -v`): **before** 75 ran / 4 failures / exit 1 → **after** 75 ran / 2 failures / exit 1. Fixed: `BundleTests.test_committed_bundle_regenerates_byte_for_byte` and `ReportTests.test_final_gate_matrix_digest_recorded_in_report`. New failures: **0**.
- **Still failing (2, pre-existing pin-baseline drift, deliberately not repaired here)**: `FinalGateTests.test_compatibility_pins_reproduce` and `PredecessorGuardTests.test_predecessor_pins_reproduce` — `tools/chandoff_assignment.py` actual `sha256:acc67d82…` vs U11-era pin `sha256:2d701541…`, `tools/chandoff_mention.py` actual `sha256:6b95dc8a…` vs U11-era pin `sha256:d3bba5b4…`. Owner: F-04/F-05 pin-baseline approval with the Lead; implementation with 04.
- **New finding raised (not repaired, out of scope)**: `tools/chandoff_joint.py:211` and `:2446` hash `tools/fixtures/artifact-contract/store-chain.json` without LF normalisation, so `artifact-set.json` and `compatibility-pin-manifest.json` are checkout-mode dependent (`sha256:9c754a93…` on CRLF vs `sha256:d9eb3121…` on LF). It does not reach the final-gate matrix or its evidence digest. `correction_owner`: 04 Software Engineer, to be scheduled by 01 alongside F-04/F-05.
- **NOT_RUN**: full `tools/tests` suite; pin/whitelist refresh; F-06 integration (`4854d02e` is not in this lineage); U12 work; live runtime or Review/QA activation; Canonical writes; product-repository work; push; merge.
