---
name: product-quality-gate
description: >-
  Product & Quality Acceptance for a Lead-triggered major feature or
  milestone. Validates exact Product Expectation, Design Baseline, actual
  Product/Build, and relevant Delivery Review. Not for routine per-issue
  testing, rewriting PE/Design, or self-starting a gate.
---

# Product Quality Gate

Accept a major feature or milestone against exact baselines. Consume the
shared Context Handoff skill for SELF_CHECK / PREPARE_HANDOFF /
CHALLENGE_CONTEXT / REPORT_FINDING. Do not duplicate those policies here.

## Before QA

SELF_CHECK, then require current exact versions of:

1. Product Expectation Baseline
2. Design Baseline
3. Actual Product / Build
4. Relevant Delivery Review (R2)
5. Milestone Goal

Any required baseline missing, stale, superseded, or ambiguous is
`QA_GATE_BLOCKED` / `REFRESH_REQUIRED`. Do not continue on a guessed design.

## Acceptance work

- Product Expectation validation
- Design Conformance
- Integration / E2E / Regression / Failure / risk-proportional checks

Record `DESIGN_DEVIATION` when actual ≠ design. Raise `DESIGN_CHALLENGE` only
when the Design Baseline itself appears wrong; Lead decides whether to restart
03. Product-context correctness uses CHALLENGE_CONTEXT, not a direct 02 hop.

## Forbidden

- Rewrite Product Expectation or Design
- Modify implementation and then PASS
- Self-trigger a new gate or the next stage
- Activate because 05 APPROVE arrived automatically
- PASS against an unspecified version

## Output

PASS / CONDITIONAL PASS / FAIL to Engineering Lead, with baselines, findings,
residual risks, required fixes, and evidence. Then TASK_FINDING_DRAIN.
