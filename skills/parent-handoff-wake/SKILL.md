---
name: parent-handoff-wake
description: Complete an assigned deliverable task with one child result, confirmed done status and native stage handoff; escalate incomplete work without duplicate notifications.
---

# Parent Handoff Wake

Read the assigned task's acceptance goal and explicit project lifecycle. This
completion contract applies to Context Engineer, Solution Architect, Software
Engineer, Delivery Reviewer and QA. Lead uses it when closing an assigned
deliverable; an ongoing coordination parent stays active until its own goal
is met.

## Complete the assigned goal

When the stated goal is met, post ONE final result on the assigned child with
the exact artifact/commit revision, validation and limitations. Then **set the
child to `done` and read the live issue back to confirm `done` before ending
the run**. A result comment or successful tool invocation alone is insufficient.
Use [scripts/complete_task.py](scripts/complete_task.py) for the state transition
and readback. Supply the actual task, parent, project, assignee and existing
final comment; acknowledge the met goal and record revision/verification.

Review and QA reports are deliverables too: a complete `REJECT`/`FAIL` report
can complete its assigned reporting task. The verdict remains unchanged;
Lead routes required corrections separately. `done` does not mean approved,
governed, integrated, merged, released or milestone accepted. Waiting for any
of those later actions does **not** put a completed deliverable in `in_review`.

Native completion of an explicit stage wakes Lead. Do not add a parent comment,
mention, assignment or another final child comment on ordinary completion.
Lead reads the actual stage/run and returns an execution decision on wake.

## Stop honestly

If the assigned goal is unmet, preserve the project's honest active/blocked
state and put the owner, release condition and one structured Lead mention
in the single child blocking result. Do not mark missing inputs or an unfinished
report `done`. Use `in_review` only when the authoritative task/project explicitly
requires that lifecycle; then use its documented return route, not an assumed
stage-completion wake.

If the final result already exists, reuse its ID; do not repost it. After an
uncertain status mutation, read the issue before doing anything else. Confirmed
`done` is idempotent. If readback cannot confirm completion, stop with the saved
receipt and exact issue state; do not blindly retry or claim completion.
Specialists never dispatch the next specialist.

The completion helper changes only one explicitly authorized child status. It
does not evaluate professional correctness, approve a verdict, refresh Context,
write Canonical Memory or bypass the current role's required fresh SELF_CHECK.
