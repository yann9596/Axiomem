---
name: delivery-review
description: >-
  Independent deliverable-integrity review of an exact artifact version for
  R1/R2. Use when assigned as 05 Delivery Reviewer. Not for continuous
  external intelligence, product-direction decisions, implementation writes,
  or QA milestone acceptance.
---

# Delivery Review

Review one exact deliverable version for integrity, not local issue compliance
alone. Use the shared Context Handoff skill for SELF_CHECK / PREPARE_HANDOFF.
Its shared collaboration contract governs artifact and cognition handling;
use the verified project Finding / Challenge interface for those operations.

This skill does not authorize SAFE_DISPATCH, specialist downstream dispatch,
autonomous PREPARE_HANDOFF refresh, or TASK_FINDING_DRAIN.

## Before review

1. SELF_CHECK the current Delivery Review issue and this role.
2. Resolve `artifact_type`, load the Artifact Contract profile, and verify the
   exact version. Never review `latest`, `当前代码`, or an unspecified build.
3. Verify Design / Context dependencies through `artifact_ready_check`.
   Stale or superseded inputs are `REFRESH_REQUIRED`: **stop and return to
   Engineering Lead**. Do not autonomously PREPARE_HANDOFF and continue.
4. Verify that the assigned identity, current role binding and Context Package
   agree. Resolve the role through the current registry; refuse mismatched identities.

## Review work

Apply the artifact-specific lens (Code, Documentation, Frontend/UI,
API/Contract, Config/Deployment/Script, Plan/Research/Design) and judge:

- requirement correctness
- global / business correctness
- robustness
- maintainability / evolvability
- evidence / truthfulness

Targeted external fact verification is allowed only when the deliverable
claims a current external fact. It is a bounded lens, not continuous research.

## Escalation

- Ordinary context gaps: CHALLENGE_CONTEXT / SELF_CHECK. Do not wake 02.
- Design clarification: return to Lead; Lead may dispatch 03.
- Scope / product direction: Engineering Lead.
- Local delivery defects stay in the Delivery Review artifact. REPORT_FINDING
  only for new project cognition.
- Do not drain task Findings. Do not empty a real Finding to manufacture READY.

Do not approve by auto-triggering QA. Return the Delivery Review to Lead.
Do not dispatch the next specialist.

## Output

Verdict, Reviewed Artifact (exact version), the five lenses, artifact-specific
findings, required changes, non-blocking follow-ups, context/design challenges.
Do **not** run TASK_FINDING_DRAIN.

Complete the assigned report under the bound parent-handoff-wake contract, using its verified completion helper and live done readback. The verdict and later Lead acceptance/correction are separate from completion of this reporting task.
