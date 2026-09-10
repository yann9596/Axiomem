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
alone. Consume the shared Context Handoff skill for SELF_CHECK /
PREPARE_HANDOFF / CHALLENGE_CONTEXT / REPORT_FINDING. Do not duplicate those
policies here.

## Before review

1. SELF_CHECK the current Delivery Review issue and this role.
2. Resolve `artifact_type`, load the Artifact Contract profile, and verify the
   exact version. Never review `latest`, `当前代码`, or an unspecified build.
3. Verify Design / Context dependencies through `artifact_ready_check`.
   Stale or superseded inputs are `REFRESH_REQUIRED`.
4. Reject any package, instruction digest, binding set, or role token from
   `feature-reviewer`. There is no alias to this role.

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

Do not approve by auto-triggering QA. Return the Delivery Review to Lead.

## Output

Verdict, Reviewed Artifact (exact version), the five lenses, artifact-specific
findings, required changes, non-blocking follow-ups, context/design challenges.
Then TASK_FINDING_DRAIN.
