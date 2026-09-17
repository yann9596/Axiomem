---
name: adaptive-task-planning
description: Break a confirmed product goal into owned, verifiable work and replan it when new evidence changes value, risk, dependencies, or uncertainty. Use for project planning, task routing, or living-plan updates; not for implementing specialist work.
---

# Adaptive Task Planning

Owner: Engineering Lead.

Use only when a goal or plan needs decomposition, routing, prioritization, or evidence-driven revision. Do not impose a fixed role sequence: involve only the roles needed by current risk and unknowns.

## Inputs

- Confirmed goal, scope, constraints, acceptance criteria, and current project state
- Known dependencies, risks, unknowns, owner boundaries, and available evidence
- Existing issues and active work, when any

## Method

1. Separate verified needs from assumptions. Complexity must be paid for by a current verified need; park speculative infrastructure or distant possibilities.
2. Split only enough for independent ownership and verification. Give each task one outcome, owner, dependencies, acceptance evidence, and a stopping condition.
3. Keep near-term work detailed, mid-term work coarse, and future work as outcomes until evidence justifies refinement.
4. Order by value, risk reduction, dependency unlock, and uncertainty. Parallelize only independent work.
5. Replan only when new evidence changes those factors. Record the evidence and the plan delta; do not silently rewrite scope or specialist facts.

## Output

Return `Project State`, `Current Goal`, `Decision`, `Active Tasks`, `Changes to Plan`, `Risks`, `Human Decision Needed`, and `Memory Disposition`. For every task include owner, dependency, completion evidence, and stop/escalation condition.

## Stop and escalate

Stop when the next executable tasks are unambiguous and safely owned. Respect existing explicit authorization. Ask the Human for scope expansion, spending, deployment, permissions, destructive impact, or final merge only when the specific action is not already authorized. Complete the concrete proposal and applicable checks before asking. If a cross-role disagreement remains after one evidence-based exchange, stop the loop and make the project tradeoff or escalate it.

When woken because a child entered `in_review`, do not stop at acknowledgement. In the same turn, split or promote the next Near Term work, or record why it stays parked. Do not wait for Human Merge or the next chat.

Use parent-handoff-wake for the current project lifecycle. Keep one final issue comment; do not duplicate parent notifications. Read technology-specific capabilities from the current project, not this shared skill.

## Integration and Human decisions

Read the current project's integration policy. After a delivery passes its applicable review, complete the authorized integration, verification and remote readback before dispatching work that depends on it. Give the next task an exact available base commit. Do not treat a local reviewed branch as an integrated release or repeatedly ask permission for already authorized push/PR/development-branch work. Preserve project-specific final-merge and release boundaries.

For Human decisions, apply the Human decision contract in the bound multica-context-handoff shared collaboration contract. It governs every role and project; the planning skill adds no separate approval format.

Apply Memory governance routing in the bound multica-context-handoff shared contract on approved decision/contract changes, material stale-state changes and stage closeout. Do not require a Runtime exception before scheduling ordinary 02 governance. Before the next dependent dispatch, record the concrete disposition and, where needed, a deduplicated 02 task and due boundary. An empty Finding directory does not discharge this check.
