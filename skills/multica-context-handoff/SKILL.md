---
name: multica-context-handoff
description: >-
  Execute the frozen Context Handoff protocol over the accepted T00–T06
  boundaries in a Context repository: build a deterministic Role Context
  Package before a role starts work (PREPARE_HANDOFF), or verify an existing
  package is still fresh before continuing work (SELF_CHECK). Not for general
  Memory administration, Multica issue management, Agent dispatch, or building
  context without a handoff request.
---

# Multica Context Handoff

Give a target role the minimum sufficient, deterministic Role Context Package
for one task (PREPARE_HANDOFF), or re-verify an already-published package
before consequential work (SELF_CHECK). Every deterministic policy decision —
PLAN, compose validation, final status, freshness, Finding Gate — stays in the
frozen T00–T06 surfaces in the Context repository. This skill orchestrates
them; it never duplicates, reinterprets, or weakens them.

## Choose the action

- **PREPARE_HANDOFF** — a role needs context before starting a task, or
  SELF_CHECK demanded a refresh. Follow [references/prepare-handoff.md](references/prepare-handoff.md).
- **SELF_CHECK** — a role is resuming or continuing work and must confirm its
  latest package is still valid for the current task and role. Follow
  [references/self-check.md](references/self-check.md).

There are no other actions. This skill is not a Memory administration, issue
management, or Agent-dispatch tool.

## Resolve the Context repository

The pipeline needs the Context repository root. Take it from the caller's
explicit path or the runtime-provided workspace resource — never guess, never
default to any built-in path. Verify it before use: `<root>/tools/chandoff.py`
and `<root>/schemas/context-handoff/prepare-handoff-request.schema.json` must
both exist. If resolution or verification fails, stop and ask the caller; do
not search the machine. Pass the verified path as `--repo` to every pipeline
command.

## Invariants (always apply)

1. **Deterministic boundaries decide.** T01 PLAN (including the Finding Gate),
   T02 ACCEPTED/REJECTED, T03 READY/PARTIAL/BLOCKED, and T04
   READY/REFRESH_REQUIRED/BLOCKED are computed by the tools. Never choose,
   override, soften, or re-derive a status. `BLOCKED` anywhere stops the
   workflow immediately.
2. **Semantic work is the only step you perform.** Execute exactly the jobs
   listed in the PLAN's `semantic_jobs`, over exactly the PLAN's candidates,
   and emit the frozen structured `semantic_compose_result` — never free-form
   prose as the sole result. Anything not in the PLAN does not exist for you:
   no added candidates, no scope change, no invented authority, no
   verification promotion, no Case-gate bypass.
3. **One bounded repair.** When T02 rejects a compose result, you may repair
   once, using only the validator diagnostics and the same PLAN. The repair
   cannot retrieve new memory, add candidates, change scope, or bypass Case
   eligibility. A repeated rejection stops the handoff.
4. **PARTIAL is never normal-ready.** No downstream work starts on PARTIAL.
   Publish it only when the caller explicitly authorizes publication, with
   all gaps preserved, and return a review-required result.
5. **BLOCKED is never published and never triggers.** Return the
   deterministic reasons plus available provenance and stop. Record the
   escalation need — but do not mention, assign, dispatch, or start any agent,
   including the Context Engineer. Later workflow stages own routing.
6. **The only Multica write is the T06 publication**, through the pipeline
   `publish` command with explicit `--authorize-publish`. Never issue raw
   Multica commands for handoff purposes, never perform assignment or mention
   (for one handoff, never both — in this skill, neither), never change issue
   status.
7. **No other side effects.** No Canonical Memory, Checkpoint, registry,
   schema, index, issue-lifecycle, Agent-config, or workspace skill-database
   writes; no Memory rebuild; no external LLM service, MCP, HTTP service,
   polling, or Autopilot. Pipeline artifacts go to a scratch out-dir outside
   the repository tree and are runtime state — never committed, never treated
   as Canonical.
8. **Ordinary success never wakes anyone.** READY and USE_EXISTING paths do
   not call, mention, or wake the Context Engineer or any other agent.
9. **Fail closed.** A bounded error, an unverifiable path, a fail-closed
   discovery, or a missing project mapping stops the protocol at that step
   with the exact reason — it never falls back to an older or unverified
   artifact.
10. **Artifact readiness is fail-closed.** When the caller declares required
    artifacts, PREPARE_HANDOFF calls the repository Artifact Contract
    `artifact_ready_check` on those exact versions before a publishable
    normal READY result. `ARTIFACT_NOT_READY` keeps the exact failure rows
    (`artifact/ref/reason_code/reason/correction_owner/route`), stops
    publication and trigger eligibility, and never substitutes `latest` /
    `current`, guesses a version, or wakes another role. `ARTIFACT_READY`
    exports the dependency set only through existing
    `package.task_evidence` and grammar-valid `package.source_refs`.
    SELF_CHECK and the pre-publication boundary re-resolve that set; a
    digest mismatch or newly invalid artifact maps to frozen
    `package_stale` → `REFRESH_REQUIRED`. Artifact Runtime owns
    `ARTIFACT_READY` / `ARTIFACT_NOT_READY`; T00/T04 retain `READY` /
    `REFRESH_REQUIRED` / `BLOCKED`. This skill only orchestrates.
11. **First-work SELF_CHECK is the official collaboration self-check.** Before
    consequential work, run this skill's pipeline `selfcheck` with an explicit
    Findings source binding and the trusted source map. Record the inspectable
    `task_ref`, current `role`, `project_id`, `package_id`, source/package
    digests, and the raw command plus result. This is a collaboration
    protocol, not an unbypassable platform worker gate. Existing trigger
    helpers remain pre-dispatch checks.
12. **Official publication is this pipeline `publish` (and bound assignment
    orchestration).** Direct `chandoff_note.publish_handoff` (T06) remains an
    internal callable and is unsupported for formal dispatch. Do not use
    `tools/context_cli.py self-check` as an official alternative: that route
    does not carry the Findings source binding. Do not recommend a bare T06
    publish or a context_cli self-check without binding.


## Parent-container handoff within current project authorization

The task identity and publication container are distinct. In Lead's current parent issue, publish the finalized child-task envelope as the ONE final non-trigger comment of that run; use the real trigger comment as --parent when available. The envelope task_ref stays the child. Only after confirmed publication does Lead perform one native assignment. No second summary/parent mention and no unverified SAFE_DISPATCH.

For worker SELF_CHECK, build a fresh request from the CHILD issue and its role, but pass the declared PARENT issue to --issue for discovery. Carry exact task binding, trusted map and prior source observation. A parent package for another task is not eligible. Do not change fingerprints to the parent identity.

The producer task is done after its stated deliverable goal is actually met and its single child comment carries exact artifact and validation evidence. Independent review is a separate stage/task. Native stage completion wakes Lead; no additional normal-completion mention. A blocked task stays blocked and can mention Lead in its single child blocking comment. done does not mean merged or milestone accepted.

The single real authorization source can be reused only inside its declared scope. Generate exact per-task binding/map after authenticated project/parent-chain checks; no wildcard task lists or invented authors. New package generation inside unchanged authorization is Lead's responsibility, not another Human-start gate. Keep fail-closed identity/source/freshness checks.

For R1 non-code review, the artifact requirements document may carry explicit reviewed_artifact (artifact_type, artifact_id, exact version), also present as a required dependency. Without it, the legacy implementation requirement remains. Wrong owner, stale/superseded or unmatched subject fails. QA's full R2 baseline remains required. Keep this requirements document at finalize, publish and consumer selfcheck; omission fails closed for a non-code review.

