# TeachersApp1 supported runtime integration

2026-09-17. Authorized by the user's 2026-09-16 request to complete local remediation, reviewed integration and team configuration before one development-launch confirmation.

## Integrated inputs

- Existing local production history through `fe27c15e34c61cc27112b065eb4a181c31e4fcc8` is retained, not replaced by remote main's older T00 snapshot.
- Reviewed candidate `a06406468a851997db8ba67f8adf98e844daf032` and its ancestors are retained. Its pre-change independent targeted run: 80 tests, 0 failures. This is evidence for that exact candidate, not a claim that all historical pin tests pass on this extended contract.
- YZT-106 F06 patch, reviewed by YZT-107, SHA256 `7afee21d0f943753ac8a4965dc299ecbc198e9d1ed66072ceb104afeb23b220b`, applied to its two U09 derived files. Original review source commit `4854d02e70c4132ef5f3a74a0cf8a3b5f676e199` is preserved as provenance; the combined tree is a new revision.

## Corrections

- Explicit exact `reviewed_artifact` permits design/product artifact review without pretending the producer was software-engineer. Legacy calls retain the implementation requirement; QA retains its full baseline.
- Subject identity is exported in existing task_evidence and checked on freshness. Subject substitution/removal, wrong owner, stale/superseded and unresolved versions are rejected.
- The App1 binding helper uses explicit CLI profile/workspace and checks live project and parent ancestry for each exact task before writing a binding. It does not parse or invent Human authority; the live source comment must authorize the stated scope.
- The supported handoff separates parent publication container from child task identity. Single final comment, single assignment and native stage completion replace the contradictory two-comment completion protocol. See [execution contract](APP1_SUPPORTED_EXECUTION.md).

## Verification

Python 3.14 on Windows, isolated clone, explicit tools/tests import path. `unittest` modules: `test_app1_binding_scope`, `test_review_subject`, `test_artifact_contract`, `test_handoff_artifact_readiness`, `test_handoff_findings_source`, `test_handoff_plan`, `test_handoff_compose`, `test_handoff_selfcheck`.

Result: **212 tests, 0 failures, 0 errors, 1 skipped**. Includes cross-project/parent-cycle rejection, review-subject transport/freshness, legacy review and QA requirements, and Findings source guards. This is supported-path verification, not full historical-suite or U12 deployment acceptance.

Artifact contract revision: `sha256:8da6c9562c4802cd305f5398d66be9dea3ab3067dec1777f3e25d7c02419125e`. Memory, registry and role semantic revisions remain respectively `45cad0a1fb4c91f86bfb402c57e423137ba6f681bd9d6b594f3d0ba68a451693`, `ec7a825740060dbd7dc7aa857128aa2ad587a4a0c4fd4f034b1aa5d594d14401`, `7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f`.

Parent-container discovery was checked using a fresh live YZT-114 task, real binding/authority and fixture parent transport: READY; wrong task rejected. Real publication/assignment/stage evidence is a separate deployment canary and must not be inferred from this fixture.

## Retained limitations

Historical U06–U12 pin/report snapshots are not rewritten to manufacture a full green release. Their automated dispatch path is not enabled by this integration. Mainline's existing dirty gate-result files and local source binding are preserved outside this commit. HarmonyOS, signing and device acceptance are outside these Python test results.
