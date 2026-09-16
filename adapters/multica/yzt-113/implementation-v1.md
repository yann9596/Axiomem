# YZT-113 implementation v1 — D→B→C pin, U11 bundle, and preflight refresh

- kind: `yzt113_pin_refresh_implementation`
- revision: **v1** (task `multica://issue/YZT-113`, review_level R1, parent `multica://issue/YZT-97`)
- owner: 04 Software Engineer
- based_on: `403e7758d9908410e636cc3b923c879cfd620340` (tree `899d12859f322f7e29ff358cc9713dc16306da5d`; parent `a243c6ff440aa0ef8a1a01c224f5aebf79009927`)
- supersedes: YZT-111 implementation v1 / F-04-F-05 proposal v1 for the **executing** pin and committed joint-replay bytes only. Historical frozen evidence is retained.
- validated_against: YZT-112 Review comment `01a0a97b-14fd-71d7-941d-96dd841d2270` revision 1 (implementation APPROVE; proposal CONDITIONAL APPROVAL WITH REQUIRED SEQUENCE); parent thread `01a0a97b-9478-7212-b63b-031d8015f551` revision 1; Lead accept comment `01a0a981-2b09-7254-a2ba-b6d3e2ecd156`
- companion: `pin-refresh-manifest-v1.md`, `evidence-manifest-v1.json`
- inputs: YZT-111 evidence zip `01a0a96d-3d4b-7528-b420-57f5c17e5105` SHA256 `07442e72027c133a8be7209fabba8840f400ca4132939adfd3e19299f1dc90af`; independent bundle `01a0a96d-3c16-7de4-97de-db2b77ddbf60` SHA256 `ea6735516c85586f0b104fd553b3d5bf2bd1e20cf6bc033db4002b9dbbd4f502`; base bundle `01a0a94d-2fdc-7b68-be90-0f18828a12cd` SHA256 `d70c688d2ed4058dfa56cb12ce5c9194c99463f2106537b443d9e749f235025b`

This task executes the approved sequence. It is **not** final quality acceptance.

## 1. Verdict

D→B→C completed in order on an isolated clone. Assignment/mention predecessor pins now match the yzt-88 executing LF digests. Post-D generator regenerated the U11 committed bundle; only `artifact-set.json` and `compatibility-pin-manifest.json` changed. Preflight `PIN_FILES_LF` / `PIN_BUNDLES` / `PIN_MATRIX_FILE` were updated to post-D/post-B measured values. `python -m unittest tools.tests.test_joint_replay -v`: **78 ran, 0 failed, exit 0**. Directed pin test and `verify_pins()` accepted-input match: pass. Capability-bound matrix evidence digest remains `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5`.

This is **not** a full preflight deploy, full `tools/tests` suite, QA gate, or BOOT-02～05 pass.

## 2. SELF_CHECK (first-work gate)

```text
python D:/AI/multica-memory/skills/multica-context-handoff/scripts/handoff_pipeline.py prepare
  --repo D:/AI/multica-memory --out-dir ./scratch-handoff
  --issue 01a0a97e-8c80-7710-bf00-66230718d9e0
  --target-role software-engineer --caller-role software-engineer
  --purpose implementation --project-id teachers-app1
  --findings-source-binding-file ./attachments/binding113.json
  --findings-trusted-map-file ./attachments/trusted-map113.json
  --findings-source-expect-commit fe27c15e34c61cc27112b065eb4a181c31e4fcc8
  --findings-source-expect-adapter-digest sha256:955562de05521c27c5824e3705771a8b1063585f208d3c85c3baffc0d2b913b1
# exit 0  status PLAN_READY  plan_id PLAN-software-engineer-5513a3ddf1377cad

python D:/AI/multica-memory/skills/multica-context-handoff/scripts/handoff_pipeline.py selfcheck
  --repo D:/AI/multica-memory --out-dir ./scratch-selfcheck
  --issue 01a0a97e-8c80-7710-bf00-66230718d9e0
  --request-from ./scratch-handoff/request.json
  --findings-evidence-file ./scratch-handoff/findings-observation.json
  --findings-source-binding-file ./attachments/binding113.json
  --findings-trusted-map-file ./attachments/trusted-map113.json
  --findings-source-expect-commit fe27c15e34c61cc27112b065eb4a181c31e4fcc8
  --findings-source-expect-adapter-digest sha256:955562de05521c27c5824e3705771a8b1063585f208d3c85c3baffc0d2b913b1
# exit 0  status READY  action USE_EXISTING  consequential_work allowed
```

- `task_ref` `multica://issue/YZT-113`; `current_role` `software-engineer`; `package_id` `CTX-software-engineer-c4c38c4ea45eb8d5`
- provenance comment `01a0a97f-a23c-73aa-b218-ca61c3755e78`
- `built_from.task_fingerprint` `sha256:46a616b00b4d924ea8858bc38bf4af2ab5873c1098cf30073da8b5f3d45ccc4c`
- Findings source `teachers-app1-runtime-findings`, snapshot `sha256:06c3e6290af11912d634956acf59c682b29f6c084fd5a1a78c0d4bb4b918dd0a`, 0 records, `binding_digest` `sha256:c6276fcb4233ace80a50f3c29e6e79851776329df147421d3caf5cb9897184d9`
- ARTIFACT_READY was **not** claimed; published package `task_evidence` is empty.

Consequential work started only after READY / USE_EXISTING.

## 3. Exact baseline

Source `D:/AI/multica-memory` was read-only. At start: HEAD `fe27c15e34c61cc27112b065eb4a181c31e4fcc8`, dirty `migration/gate-results/gate-{a,b,c-replay}.json` plus untracked `adapters/multica/project-bindings/teachers-app1/findings-source-binding.json`. Those files were not touched. After this work the source HEAD and dirty set are unchanged.

`403e7758` / `a243c6ff` were **absent** from the source object store. Isolated clone of branch `yzt-105-f01-f03-isolation-lf` (`83386809`), then YZT-109 bundle then YZT-111 bundle after SHA256 verification. Isolated checkout before edits:

- commit `403e7758d9908410e636cc3b923c879cfd620340`
- tree `899d12859f322f7e29ff358cc9713dc16306da5d`
- parent `a243c6ff440aa0ef8a1a01c224f5aebf79009927`
- `core.autocrlf=true`

## 4. Changes (D then B then C)

### D

Only `compatibility_manifest` and `PredecessorGuardTests` assignment/mention pin strings. Pinned files `tools/chandoff_assignment.py` / `tools/chandoff_mention.py` unchanged.

Lineage measured: `git log 49c48a9..HEAD` for those two files is exactly `261df9a`, `d2b6299`, `2efb52f`, `58ba5d8`. HEAD blobs equal `58ba5d8`. LF digests equal the approved new pins.

### B

`python tools/chandoff_joint.py bundle --out-dir <scratch>/post-d --generated-at 2026-09-11T00:00:00Z`. 15 generated files byte-identical to committed; capture untouched. Copied only:

- `artifact-set.json`
- `compatibility-pin-manifest.json` (`all_reproduce`: true)

### C

Updated three preflight rows to post-D/post-B measurements. Did not copy `6e28025c…` / `77f92fad…`. Did not update `tools/u12_r0_binding.py`.

Exact old → new values: `pin-refresh-manifest-v1.md`.

Not modified: historical U11/U12 reports, Canonical Memory, production ledger, product repo, Findings, other whitelist rows, F-06.

## 5. Verification

`python -m unittest tools.tests.test_joint_replay -v` in the isolated clone:

| | ran | failures | exit |
| --- | --- | --- | --- |
| after D→B→C | 78 | 0 | 0 |

Three `StoreChainNormalizeLfTests` retained and passing. No tests deleted or assertions lowered.

Directed: `PinVerificationTests.test_real_pins_reproduce_and_discrepancy_recorded` exit 0. `u12_preflight.verify_pins()` `accepted_inputs_all_match=true`. Documented non-blocking `U12-P0-F1` (`o2_report_test_row` mismatch) remains.

LF/CRLF forced fixture checkout: regenerate 17/17 identical; second LF generation identical to the first post-D generation. Content change of `store-chain.json` still moves `artifact-set.json` and `compatibility-pin-manifest.json` digests. Fixture restored.

Capability-bound `final_gate_matrix.evidence_digest` = `sha256:14b892c22a3aa8547f382816d27376d114ef80664361680a969db1456fa079d5`. Unbound CLI `python tools/chandoff_joint.py matrix` has `capability_ok=null` and `evidence_digest` `sha256:8b49bf70436aaa1d6362ec875b40af04560853d8b5d83b75136f695ed5abba88`. That unbound digest is **not** a substitute.

Other blockers (not expanded):

- CLI `python tools/u12_preflight.py pins` raises `NameError: cmd_pins is not defined` (pre-existing missing command function). Deploy-path callable `verify_pins()` is the actual pin check used here.
- Full `tools/tests` suite NOT_RUN.
- Full preflight `deploy` / `verify` NOT_RUN.
- QA two hashes UNRESOLVED, historical logs NOT_AVAILABLE, BOOT-02～05/D01 parked.

## 6. Deviations

None to public contracts. Local choice: invoke `verify_pins()` rather than the broken CLI `pins` subcommand; report that CLI gap without repairing it.

## 7. Context Findings / Memory Candidates

- Post-D `tools/chandoff_joint.py` LF is `sha256:06373790cbf82bb31d94f3860fbcec2f33a58e28962fabcf4418dbf297a8f535`, not the pre-D `6e28025c…`.
- Post-B 18-file joint-replay digest is `sha256:641b756fc49d1f95cf52751b939e36fbe6a40b5e1eb5c7bec5c306fb98e69798`.
- `cmd_pins` is referenced by `u12_preflight.main` but not defined. Out of this task's pin-refresh scope.
- Do not write Canonical Memory from this report.

## 8. Risks

- Reviewers comparing this commit to pre-D scratch candidates `6e28025c` / `77f92fad` / `c1c51f6e` will see different B/C values; that is the required sequence, not drift.
- `core.autocrlf=true` on the delivery clone; pins use LF-normalised file digests where specified.
- Local pin/module pass is not full preflight or QA.

## 9. Ready for Review

Yes, R1 implementation v1 plus pin-refresh-manifest v1. Independent 05 review is owned by 01. This role does not dispatch 05, trigger QA, or merge.
