# YZT-111 implementation v1 — F-07-NEW-001 store-chain LF normalisation

- kind: `yzt111_f07_new_001_implementation`
- revision: **v1** (task `multica://issue/YZT-111`, review_level R1, parent `multica://issue/YZT-97`)
- owner: 04 Software Engineer
- based_on: `a243c6ff440aa0ef8a1a01c224f5aebf79009927` (tree `ab5a0d61f75e7291650dd24c5b4367380df72c95`; parent `833868092d4b45b8c965f79dbdac6f8ba466c7f5`)
- validated_against: YZT-109 bundle `01a0a94d-2fdc-7b68-be90-0f18828a12cd` SHA256 `d70c688d2ed4058dfa56cb12ce5c9194c99463f2106537b443d9e749f235025b`; evidence zip `01a0a94d-31e8-7e54-9c73-deaffa215286` SHA256 `25c684ee5d9ad25e7b3d69caae3e0eae345e77f9ff2c6e88aa567fbc57173891`; YZT-110 R1 APPROVE accepted by 01 on parent thread `01a0a959-b73c-7270-a90d-65b841ffa3cc` revision 1
- companion: `f04-f05-proposal-v1.md`, `evidence-manifest-v1.json`

## 1. Verdict

F-07-NEW-001 is repaired in the generator only. The two `store-chain.json` hash call sites in `tools/chandoff_joint.py` now pass `normalize_lf=True`. Scratch regeneration of all 17 U11 generated files is byte-identical across LF and CRLF checkout of that fixture. The capability-bound matrix evidence digest is unchanged: `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5`. Pins, immutability whitelist, committed joint-replay bytes, and historical reports were **not** refreshed.

## 2. SELF_CHECK (first-work gate)

```text
python D:/AI/multica-memory/skills/multica-context-handoff/scripts/handoff_pipeline.py prepare
  --repo D:/AI/multica-memory --out-dir <scratch>/yzt-111-handoff
  --issue 01a0a95c-86e6-726b-a30a-7900036e8dd1
  --target-role software-engineer --caller-role software-engineer
  --purpose implementation --project-id teachers-app1
  --findings-source-binding-file <issue-attachment yzt111-binding.json>
  --findings-trusted-map-file <issue-attachment yzt111-trusted-map.json>
# exit 0  status PLAN_READY  plan_id PLAN-software-engineer-1dd61cf56be07532

python D:/AI/multica-memory/skills/multica-context-handoff/scripts/handoff_pipeline.py selfcheck
  --repo D:/AI/multica-memory --out-dir <scratch>/yzt-111-selfcheck
  --issue 01a0a95c-86e6-726b-a30a-7900036e8dd1
  --request-from <scratch>/yzt-111-handoff/request.json
  --findings-evidence-file <scratch>/yzt-111-handoff/findings-observation.json
  --findings-source-binding-file <yzt111-binding.json>
  --findings-trusted-map-file <yzt111-trusted-map.json>
# exit 0  status READY  action USE_EXISTING  consequential_work allowed
```

- `task_ref` `multica://issue/YZT-111`; `current_role` `software-engineer`; `package_id` `CTX-software-engineer-e623d20b3f9542d3`
- provenance comment `01a0a95d-937e-763f-8e3a-fda722cc2add`
- `built_from.task_fingerprint` `sha256:93d9e56083d0ed00c8abb3fb319bc2296278a1e31246150efe62e8f06e628262`
- Findings source `teachers-app1-runtime-findings`, snapshot `sha256:06c3e6290af11912d634956acf59c682b29f6c084fd5a1a78c0d4bb4b918dd0a`, 0 records, `binding_digest` `sha256:f4b09beaa436dc081482587fccf278938ddd3406c940946973f7cac7d8deed8d`
- ARTIFACT_READY was **not** claimed; the published package `task_evidence` is empty.

Consequential work started only after READY / USE_EXISTING.

## 3. Exact baseline

Source `D:/AI/multica-memory` was read-only. At start: HEAD `fe27c15e34c61cc27112b065eb4a181c31e4fcc8`, dirty `migration/gate-results/gate-{a,b,c-replay}.json` plus untracked `adapters/multica/project-bindings/teachers-app1/findings-source-binding.json`. Those files were not touched.

`a243c6ff` / tree `ab5a0d61` were **absent** from the source object store. They were loaded from the YZT-109 bundle after verifying SHA256, onto a clone of branch `yzt-105-f01-f03-isolation-lf` that already contained prerequisite `83386809`. Isolated checkout:

- commit `a243c6ff440aa0ef8a1a01c224f5aebf79009927`
- tree `ab5a0d61f75e7291650dd24c5b4367380df72c95`
- parent `833868092d4b45b8c965f79dbdac6f8ba466c7f5`

## 4. Changes

Only two generator call sites plus targeted tests and this task's new reports.

`tools/chandoff_joint.py` (diagnostic sites 211 and 2446 at the exact base):

```diff
-        "source_digest": file_digest(U10_FIXTURES / "store-chain.json"),
+        "source_digest": file_digest(U10_FIXTURES / "store-chain.json", normalize_lf=True),
-        "u10_store_chain_digest": file_digest(U10_FIXTURES / "store-chain.json"),
+        "u10_store_chain_digest": file_digest(U10_FIXTURES / "store-chain.json", normalize_lf=True),
```

`tools/tests/test_joint_replay.py`: added `StoreChainNormalizeLfTests` (three tests) covering both call-site needles, runtime manifest digest equality with `normalize_lf=True`, LF/CRLF same-content equality, and real content change still moving the digest. `generated_at` is unused by these hashes; CLOCK `2026-09-11T00:00:00Z` is preserved on bundle writes.

Not modified: committed `adapters/multica/joint-replay/**`, U11/U12 historical reports, pins, whitelist, fixture bytes, Canonical Memory, production ledger, product repo, Findings.

## 5. Scratch 17-file LF/CRLF regeneration

Command (isolated clone, after the generator patch; output only under scratch):

```text
python tools/chandoff_joint.py bundle --out-dir <scratch>/gen-lf
# and the same after rewriting store-chain.json to CRLF into <scratch>/gen-crlf
# generated_at 2026-09-11T00:00:00Z preserved; store-chain.json restored afterwards
```

Result: **17/17 SAME** across LF and CRLF; a second LF generation was byte-identical. No remaining F-07-NEW-001 DIFF. Fixture meaning unchanged: JSON parsed identically; only the embedded source digest moved from the CRLF raw hash `sha256:9c754a93b11150b5a8952928493476a661123b901563c11ce82145658fcb981a` to the LF-normalised hash `sha256:d9eb3121755d262155039972e3336f14b007d20b6aa0b8fe3d123516989d8154`.

Capability-bound matrix:

```text
final_gate_matrix(capability=run_all()["capability"])
→ sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5
```

Unbound `matrix` CLI path remains `sha256:8b49bf70436aaa1d6362ec875b40af04560853d8b5d83b75136f695ed5abba88` (`capability_ok` is `null`). That other digest is not a substitute.

`final-gate-matrix.json` bytes stayed `sha256:d8f7c84cc364be3a415878a177881d948f312be78a391138d6cfbbd7ec98f868` (same as YZT-109). Only `artifact-set.json` and `compatibility-pin-manifest.json` would change if the committed bundle were regenerated; that regeneration was **not** written.

## 6. Tests (measured, not inherited)

`python -m unittest tools.tests.test_joint_replay -v` in the isolated clone:

| | ran | failures | exit |
| --- | --- | --- | --- |
| before (`a243c6ff`, pristine) | 75 | 2 | 1 |
| after (this implementation) | 78 | 3 | 1 |

Before failures (pre-existing F-07-KNOWN-001):

- `FinalGateTests.test_compatibility_pins_reproduce`
- `PredecessorGuardTests.test_predecessor_pins_reproduce` (`tools/chandoff_assignment.py` actual `sha256:acc67d82…` vs pin `sha256:2d701541…`)

After failures:

- `BundleTests.test_committed_bundle_regenerates_byte_for_byte` — **new, expected**. Committed `artifact-set.json` still embeds `sha256:9c754a93…`; fresh generation embeds `sha256:d9eb3121…`. Not repaired: committed bundle is historical F-07 evidence.
- `FinalGateTests.test_compatibility_pins_reproduce` — same pre-existing pin drift
- `PredecessorGuardTests.test_predecessor_pins_reproduce` — same pre-existing pin drift

New tests: 3/3 pass. Fixed by this task: 0 of the pre-existing 2. New unexpected failures: 0.

Additional pin observation (not in the required module; not greenwashed): `tools.tests.test_u12_preflight.PinVerificationTests.test_real_pins_reproduce_and_discrepancy_recorded` fails. `file:tools/chandoff_joint.py` actual LF `sha256:6e28025cae9cc6e33fff24bc93bfe5d591d7e283771389c267a110da8aee8d1d` vs U11 pin `sha256:2afc229364e6202bef9fae0c5bba264194210d7d1797b5c6cdc39735fc6b6208`. The joint-replay bundle pin `sha256:04e0da84…` vs committed `sha256:2047cc98…` and matrix pin `sha256:a9ecc24c…` vs `sha256:d8f7c84c…` are YZT-109/F-07 leftovers, not introduced here.

## 7. Deviations

None to public contracts. Local choice: keep committed joint-replay bytes frozen and list the byte-for-byte test failure instead of regenerating evidence in this wave.

## 8. Context Findings / Memory Candidates

- F-07-NEW-001 is generator-fixed; remaining mismatch is committed historical bytes vs new generation.
- assignment/mention post-U11 drift source is **verified**, not UNRESOLVED: `261df9a2cd0c036def29572f2cbf273bdf33cb70`, `d2b6299abd924aa55f97756488a04209a04d0158`, `2efb52fc429fbcf05777064efc080b91625697d8`, `58ba5d8d92fa82b07330e5aacd4e8b9fe1a9bbc8` (yzt-88 Findings-source-binding work after U11 base `49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9`).
- Do not write Canonical Memory from this report.

## 9. Risks

- Reviewers comparing committed joint-replay to a fresh generation on this commit will see two JSON files differ until F-04/F-05 regenerates them.
- `core.autocrlf=true` on the delivery clone; pins use LF-normalised file digests of `chandoff_joint.py`.
- Full `tools/tests` suite NOT_RUN.

## 10. Ready for Review

Yes, R1 implementation v1 plus F-04/F-05 proposal v1. Independent 05 review is owned by 01. This role does not dispatch 05, refresh pins, or merge.
