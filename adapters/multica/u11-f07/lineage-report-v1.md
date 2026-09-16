# U11 F-07 Regeneration — Lineage Report v1

- kind: `u11_f07_regeneration_lineage_report`
- revision: **v1** (task `multica://issue/YZT-109`, review level R1, parent `multica://issue/YZT-97`)
- owner: 02 Context Engineer
- based_on: `833868092d4b45b8c965f79dbdac6f8ba466c7f5` (branch `yzt-105-f01-f03-isolation-lf`; direct parent `fe27c15e34c61cc27112b065eb4a181c31e4fcc8`; tree `31b8dde4fba518bcb1f77dc1b46bc74dbdd15378`; historical comparison `251034892d3e6b62d17b64b412e0f4a76ad7a555`)
- supersedes: U11 report revision 1 and its committed bundle (base `49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9`; matrix evidence digest `sha256:5154381f…`; bundle `sha256:ee15c79e…` / `sha256:04e0da84…`). Revision 1 is **not** rewritten: its bytes stay readable in Git history.
- validated_against: `83386809`, reproduced in an isolated clone. The read-only source repository `D:/AI/multica-memory` was not modified (HEAD stayed `fe27c15e…`, branch `main`, and all pre-existing dirty/untracked content was preserved).
- machine-readable companion: `adapters/multica/u11-f07/evidence-manifest-v1.json` (every digest below is generated, never hand-typed)
- delivery commit / tree, transferable patch and `git bundle`: reported by the YZT-109 handoff comment (a commit cannot embed the hash of the commit that contains it)

## 1. Verdict

**F-07 done.** The U11 committed bundle was regenerated from the exact input commit in an isolated clone and the report needle was synchronised to the digest that the real generation produces. Two of the four U11 focused failures at the baseline are fixed by this delivery; the other two are pre-existing pin-baseline drift that this task is explicitly forbidden to repair.

- matrix evidence digest, confirmed by real generation: `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5`
- the v1 needle `sha256:5154381f…` and the CRLF-dependent needle `sha256:747494e1bf8c6a479542883e490c6d628c1f5faee622592a597c7f3303e39f11` are **not** used anywhere in the delivered artifact
- new failures introduced: **0**; new findings: **1** (see §7)

## 2. Exact inputs and their local availability

| object | state | note |
| --- | --- | --- |
| `833868092d4b45b8c965f79dbdac6f8ba466c7f5` | present in the local object store | branch head of `yzt-105-f01-f03-isolation-lf`; `cat-file -t` → `commit` |
| `fe27c15e34c61cc27112b065eb4a181c31e4fcc8` | present | verified as the direct Git parent of `83386809` |
| `251034892d3e6b62d17b64b412e0f4a76ad7a555` | present | historical comparison only, never substituted for the input |
| `4854d02e70c4132ef5f3a74a0cf8a3b5f676e199` (F-06) | **absent** | F-06 was accepted by YZT-107 but its commit was never pushed and is not in this object store. F-06 is explicitly out of scope for this task and was **not** silently integrated. |

No temporary directory of another run was searched, no other Run's scratch path was guessed, and the current HEAD was never used as a substitute for the input commit.

## 3. SELF_CHECK gate (first-work collaboration self-check)

```powershell
# prepare — T05 snapshot + T01 PLAN
python D:/AI/multica-memory/skills/multica-context-handoff/scripts/handoff_pipeline.py prepare `
  --repo D:/AI/multica-memory --out-dir <scratch> --issue 01a0a943-069e-7bb6-a65a-10297ef3f433 `
  --target-role context-engineer --caller-role context-engineer --purpose implementation `
  --project-id teachers-app1 `
  --findings-source-binding-file <yzt109-binding.json> --findings-trusted-map-file <yzt109-trusted-map.json>
# exit 0 — status PLAN_READY, plan PLAN-context-engineer-c74a39bae0b800fd

# selfcheck — T06 discovery + T04 SELF_CHECK (same repo/binding/map, this round's request + observation)
python D:/AI/multica-memory/skills/multica-context-handoff/scripts/handoff_pipeline.py selfcheck `
  --repo D:/AI/multica-memory --out-dir <scratch> --issue 01a0a943-069e-7bb6-a65a-10297ef3f433 `
  --request-from <scratch>/request.json --findings-evidence-file <scratch>/findings-observation.json `
  --findings-source-binding-file <yzt109-binding.json> --findings-trusted-map-file <yzt109-trusted-map.json>
# exit 0 — status READY, action USE_EXISTING, consequential_work "allowed"
```

- `task_ref` `multica://issue/YZT-109`; `current_role` `context-engineer`; `project_id` `teachers-app1`
- `package_id` `CTX-context-engineer-e16e77db7b363a62` (discovered from `/note` comment `01a0a944-1e89-7af3-91b8-b8f2aaea9ea9`)
- `built_from`: memory `sha256:45cad0a1…`, registry `sha256:ec7a8257…`, role profile `sha256:7b3bdf52…`, task fingerprint `sha256:0323d26a52b6ffbef598770bdebafa4c7de76ee17a6a2d6526ee84941fde1a9f`
- Findings source: `teachers-app1-runtime-findings`, `simulation: false`, snapshot `sha256:06c3e6290af11912d634956acf59c682b29f6c084fd5a1a78c0d4bb4b918dd0a`, 0 records, `binding_digest sha256:26659577…`

Consequential work started only after the platform discovery returned READY.

## 4. Regeneration

```powershell
git clone --branch yzt-105-f01-f03-isolation-lf D:/AI/multica-memory D:/AI/worktrees/multica-memory-yzt-109-f07
# exit 0; HEAD 83386809, tree 31b8dde4…, clean worktree, core.autocrlf=true (delivery checkout mode)

cd D:/AI/worktrees/multica-memory-yzt-109-f07
python tools/chandoff_joint.py bundle --out-dir adapters/multica/joint-replay
# exit 0 — 17 generated files; generated_at preserved at 2026-09-11T00:00:00Z
```

The generator was **not** modified. No pin, whitelist, test assertion, fold record or immutability entry was touched.

## 5. Modified files and digests

| path | sha256 before | sha256 after |
| --- | --- | --- |
| `adapters/multica/joint-replay/final-gate-matrix.json` | `sha256:a9ecc24c0ee1ff8965b534ba3b317b5e1c31e541458bb9fb06d88d59eefe946d` | `sha256:d8f7c84cc364be3a415878a177881d948f312be78a391138d6cfbbd7ec98f868` |
| `adapters/multica/joint-replay/compatibility-pin-manifest.json` | `sha256:49622f257cdf261d5a40786161dba01ea405b3a3b11ccbcbb376f0dbb3fa1646` | `sha256:2db0869edd13ec0902b2eb53e9ecc6e9ff62531691fb0056e13e781ff4e3bf18` |
| `adapters/multica/U11_JOINT_END_TO_END_REPLAY_REPORT.md` (revision 2) | v1 bytes at `83386809` | see the delivery commit |
| `adapters/multica/u11-f07/evidence-manifest-v1.json` | — (new) | `manifest_digest` `sha256:bdb3463ea5ed7e6875c6d7c6854598ee456bd03795e7fa6efceda27ec459090f` |
| `adapters/multica/u11-f07/lineage-report-v1.md` | — (new) | this file |

The other **15** generated files reproduced **byte-for-byte** and were therefore not rewritten: `artifact-bindings.json`, `artifact-set.json`, `capability-proof.json`, `closed-source-guard.json`, `finding-challenge-matrix.json`, `finding-drain.json`, `o2-recovery-matrix.json`, `replays.json`, `rerun-receipt-contract.json`, `residual-decisions.json`, `retired-identity-matrix.json`, `routing-negative-matrix.json`, `side-effect-audit.json`, `stage-wake-matrix.json`, `topology-matrix.json`. `capture/rerun-receipt-capture.json` is a live read-only CLI capture, not a generated file; it stayed byte-identical and was not rewritten.

Bundle digests (`chandoff_joint.bundle_digest`, canonical JSON of the sorted `[relative_path, sha256(bytes)]` list):

| bundle | before | after |
| --- | --- | --- |
| 17 generated files | `sha256:ee15c79e062179d88cc6a71c25feab3560eaa2afe22895165468189bd51770ab` | `sha256:50e702dac5aa3f6ed3cb0d47538488939e0eec249bf31ea22809a5b174113b5e` |
| 18 files incl. capture | `sha256:04e0da8412ba8b024fc18fd06608b59ebe39de76692f7ed85f9c4663d28a29b9` | `sha256:2047cc98737ea5fd82e7b7b3cb0199afe2480ef41c9bf0eca54a21fa6e1228c9` |

What actually changed inside the two regenerated JSON files:

1. `final-gate-matrix.json` — `evidence_digest` `sha256:5154381f…` → `sha256:14b892c2…`, and the `invalid_rule_authority` gate-a evidence digest `sha256:bcb848ea…` → `sha256:40bf7227…`. Nothing else in the matrix moved: all 20 parent counters and all 12 O2 safety gates are still `0`, `all_pass` is still `true`.
2. `compatibility-pin-manifest.json` — `all_reproduce: true` → **`false`**, because two predecessor pins no longer reproduce at this baseline (see §7).

## 6. Matrix evidence digest and needle rules

- authoritative value: `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5`, produced by the same call the focused test makes: `final_gate_matrix(capability=run_all()["capability"])`.
- `sha256:5154381f…` — the superseded revision-1 needle. Rejected.
- `sha256:747494e1bf8c6a479542883e490c6d628c1f5faee622592a597c7f3303e39f11` — what the matrix digest becomes if `migration/gate-results/gate-a.json` is hashed **without** LF normalisation on a CRLF checkout (805 CRLF bytes instead of the 769-byte LF blob, gate-a digest `sha256:30099c4c…` instead of `sha256:40bf7227…`). This value was never written into the report; it is recorded here only so a reviewer can recognise it as the wrong needle.
- `sha256:8b49bf70…` — the digest printed by the `matrix` **CLI subcommand**. That path calls `final_gate_matrix()` with no `capability` argument, so `replay_integrity.capability_ok` is `null` instead of `true` and the digest legitimately differs. It is a different call, not a different evidence value; the report needle must use the capability-bound digest.
- The report needle was synchronised from the generation result, not from any literal. No generated byte was hand-written.

### LF/CRLF and time-field comparison rules

- `generated_at` is preserved (`2026-09-11T00:00:00Z`, `CLOCK`) for every generated file; it is a fixed constant, never "now". Byte comparison is therefore time-stable.
- Source digests that the report cites are **LF-normalised** (`file_digest(..., normalize_lf=True)`), matching the report's stated digest method; a CRLF checkout and an LF checkout agree on them.
- The regenerated bundle is not fully line-ending independent: `artifact-set.json` and `compatibility-pin-manifest.json` embed `file_digest(tools/fixtures/artifact-contract/store-chain.json)` **without** normalisation, so those two files differ between a CRLF checkout and an LF checkout (see §7 F-07-NEW-001). All other 15 generated files — including `final-gate-matrix.json` — are byte-identical across both modes, and the matrix evidence digest is identical across both modes.
- Practical comparison rule for a reviewer: compare bundle bytes within the same checkout line-ending mode (the delivered bytes are the `core.autocrlf=true` Windows checkout, matching the original U11 generation environment); compare the matrix evidence digest without any mode caveat.

## 7. Findings

### F-07-NEW-001 — `store-chain.json` digest is not LF-normalised (new, not repaired here)

- `tools/chandoff_joint.py:211` (`artifact_set_manifest`) and `:2446` (`compatibility_manifest`) hash `tools/fixtures/artifact-contract/store-chain.json` with `file_digest(..., normalize_lf=False)`, and that fixture is not pinned `-text` in `.gitattributes`. On a CRLF checkout the digest is `sha256:9c754a93…`; on an LF checkout it is `sha256:d9eb3121…`. Consequence: `artifact-set.json` and `compatibility-pin-manifest.json` are checkout-mode dependent, so "regeneration is byte-identical" holds only within one line-ending mode.
- This is the same defect class as F-03 (gate-a), one fixture further along; it does **not** reach `final-gate-matrix.json` or its evidence digest.
- It was **not** repaired: this task forbids changing the generator, the test assertions, the pins and the immutability whitelist. `correction_owner`: 04 Software Engineer, to be scheduled by 01 together with F-04/F-05.

### F-07-KNOWN-001 — predecessor pin drift for `chandoff_assignment.py` and `chandoff_mention.py` (pre-existing, preserved)

- `tools/chandoff_assignment.py`: actual `sha256:acc67d82…` vs U11-era pin `sha256:2d701541…`
- `tools/chandoff_mention.py`: actual `sha256:6b95dc8a…` vs U11-era pin `sha256:d3bba5b4…`
- Cause: the post-U11 `yzt-88` Findings-source-binding work (`261df9a`, `d2b6299`, `2efb52f`, `58ba5d8`) changed both runtimes after the U11 evidence base `49c48a9`. This is why the regenerated manifest honestly records `all_reproduce: false`.
- These were **already failing** at the baseline and remain failing; they were not introduced by this task, were not hidden, and were not "fixed" by re-pinning. `correction_owner`: pin/whitelist baseline approval (F-04/F-05) with the Lead; implementation stays with 04.

## 8. Determinism

| check | result |
| --- | --- |
| two independent regenerations in the same clone, byte comparison of all 17 files | identical |
| regeneration vs the bytes written to `adapters/multica/joint-replay` | identical (17/17) |
| fresh clone of the delivered commit vs committed bundle | **verified**: 17/17 generated files byte-identical; bundle `sha256:50e702da…` (17 files) and `sha256:2047cc98…` (18 files) recomputed; matrix evidence digest recomputed as `sha256:14b892c2…`; `tools.tests.test_joint_replay` re-run 75 tests / 2 failures; `git status` clean after the probes |
| CRLF checkout vs LF checkout | 15/17 identical; `artifact-set.json` and `compatibility-pin-manifest.json` differ — see F-07-NEW-001 |
| `final-gate-matrix.json` across CRLF/LF | identical; evidence digest `sha256:14b892c2…` in both modes |

## 9. Targeted verification, before and after

`python -m unittest tools.tests.test_joint_replay -v` run in the pristine baseline clone and in the delivered clone (raw logs attached to the YZT-109 handoff):

| | ran | failures | exit |
| --- | --- | --- | --- |
| before (pristine `83386809`) | 75 | 4 | 1 |
| after (delivered revision) | 75 | 2 | 1 |

- fixed by this task (2):
  - `BundleTests.test_committed_bundle_regenerates_byte_for_byte` — the committed bundle now matches a fresh regeneration
  - `ReportTests.test_final_gate_matrix_digest_recorded_in_report` — the report now carries `sha256:14b892c2…`
- still failing (2, pre-existing, see F-07-KNOWN-001): `FinalGateTests.test_compatibility_pins_reproduce`, `PredecessorGuardTests.test_predecessor_pins_reproduce`
- new failures: **0**

Note for the reviewer: the YZT-108 review reported "exactly 3 expected failures" for a 12-test subset of this module. Running the **whole** `test_joint_replay` module at the same baseline shows **4** failures; the fourth, `PredecessorGuardTests.test_predecessor_pins_reproduce`, was outside that subset. This is a coverage difference in the earlier count, not a regression, and it is reported here rather than smoothed over.

## 10. Scope, boundaries and NOT_RUN

- Modified only: the two regenerated bundle files, the U11 report revision, and this task's two new `adapters/multica/u11-f07/` artifacts.
- Not modified: `tools/chandoff_joint.py`, `tools/tests/**`, any pin, any whitelist entry, `fold_records`, `u12_p0r.py`, `migration/gate-results/*` bytes, Canonical Memory, the frozen Handoff surfaces, the production ledger, the product repository.
- F-04/F-05 (pins, immutability whitelist) stay with the Lead. F-06 was already accepted by YZT-107 and its commit `4854d02e` was **not** integrated.
- NOT_RUN: full `tools/tests` suite; pin/whitelist refresh; U12 work; live runtime, Review/QA activation; Canonical writes; product-repository work; push; merge.
- Preserved as-is: the global FAILED status, the two QA hash UNRESOLVED items, history logs `NOT_AVAILABLE`, BOOT-02～05 and D01 stay parked.

## 11. Ready for Review

Yes. R1 delivery: exact commit/tree, transferable patch and `git bundle`, this lineage report v1, the machine-readable file-digest manifest, and the raw before/after verification logs. The delivered commit was re-cloned and re-verified independently (§8): committed bundle == fresh regeneration, matrix digest reproduced, targeted tests unchanged at 2 pre-existing failures with 0 new. Independent 05 review is owned by the Lead; this role dispatches nothing downstream.
