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

## Shared collaboration contract

This section is the single shared definition for the bound team's context, artifact, cognition and Human-decision duties. Role instructions retain professional responsibilities and reference this skill instead of duplicating these rules.

- Resolve the current project, repository, role and capability policy from authoritative task/project inputs. Installed tools are used only when that project enables them for the current role. Do not inherit another project's paths, credentials, technology, scope or temporary authorization. Role identities resolve through the current registry/binding, not historical names or IDs in prompts.
- Before consequential work, run the bound fresh SELF_CHECK with exact task/role, trusted source map, real source binding and prior observation. READY permits the authorized path; stale or BLOCKED stops it. Specialists return the reason to Lead; Lead refreshes within unchanged authorization. Never assert READY or bypass drift. Read-only diagnosis is allowed.
- Cross-run consumer inputs (binding, trusted map, prior observation and referenced artifacts) must remain available after the producer Run exits. Store them in an authorized persistent project directory or attach exact bytes to the platform; do not hand off files in per-Run temporary directories or automatically cleaned worktrees. Record exact paths/versions and verify readability before triggering. A missing declared input stops the consumer; report it to Lead instead of recursively searching other tasks or reconstructing authority from logs.
- Formal artifacts carry an exact version and applicable based_on, supersedes, reviewed_artifact and validated_against relations. Use the actual artifact type and owner. Readable files do not replace Context Handoff. Report commands, results and limitations honestly as PASS, NOT_RUN or BLOCKED.
- Capture new project cognition as a source-backed Finding through the verified project interface. Ordinary capture does not wake Context Engineer or promote a candidate into Canonical. Local delivery defects stay in Review/QA artifacts unless they also establish new project knowledge. Never drain or empty real Findings to manufacture READY; route unresolved cognition through Lead.
- This skill prepares, verifies and publishes Context only. Lead performs authorized issue routing separately; specialists do not dispatch another professional role. Use parent-handoff-wake for one-result completion and native stage/blocked wake behavior. Scope changes or decisions outside existing authority go to the responsible owner or Human; unchanged authorization does not require repeated confirmation.
- Preserve uncommitted/untracked work. Do not silently take over another role's write surface. After one challenge and one owner response, unresolved disagreement goes to Lead; a dispute over Lead authority goes to Human. R0/R1/R2 are project collaboration policy, not Memory Core concepts.

CLI connection scope is explicit when needed: every pipeline stage accepts --executable, --profile and --workspace-id. These configure the existing authenticated read/publication adapters; they do not broaden allowlists or grant dispatch rights.

## Human decision contract — all roles, all projects

This is the common contract for every bound agent, including Lead, Context Engineer, Solution Architect, Software Engineer, Delivery Reviewer and QA. Apply it whenever an agent prepares, escalates, reviews or presents a decision for the user, whether in an issue, chat, review, blocker or approval request. It is not specific to one specialist or product. Keep professional ownership and existing project routing; an escalation does not grant dispatch or decision authority.

Before asking for a decision, finish the authorized investigation and preparation needed to make it concrete and reviewable. Check existing authorization first: do not ask again for an action already approved within unchanged scope. Distinguish an actual user tradeoff from routine technical work the agent can complete itself.

Present the following in plain language, with detail proportional to impact; major decisions must make each item explicit:

1. Background and purpose: what needs deciding and why now.
2. Current state: what is verified, what is unknown, the relevant evidence/version and the part of the work affected. Explain domain terms; use a small example when helpful. Unexplained issue IDs or labels are not explanations.
3. Concrete options: normally two or three feasible choices the user can select, including deferral or keeping the current behavior when viable. Do not invent false alternatives to fill a quota; if only one action is viable, explain why and the consequence of declining it.
4. Consequences: describe differences in user-visible behavior, scope, compatibility/data, effort/cost, risk and reversibility as relevant. Do not dump technical detail without explaining its effect.
5. Recommendation: state the preferred choice, the reason and material uncertainty. Do not replace the recommendation with an open-ended question.
6. Exact decision boundary and pending behavior: name the artifact/version or action being approved, what approval includes, what remains outside it, what must wait without an answer and which independent authorized work continues. Silence is not approval.

Ask for the choice after presenting this package. Record the selected option and source decision reference before freezing the affected contract or performing the gated action. An approval applies only to its stated scope; a material change requires renewed assessment. When reviewing another agent's request, correct missing decision context before passing it to the user. For a missing factual input rather than a decision, ask one precise factual clarification instead of fabricating choices.

## Memory governance routing — all roles, all projects

Two distinct paths apply. The deterministic Finding/Context Runtime processes supported inputs at its existing natural boundaries and returns unresolved-material proposals to Lead; it never starts an agent. Separately, Lead must schedule ordinary Context Engineer governance when approved decisions or verified durable changes require curation. The latter does not require a Runtime error or an existing Finding. "Ordinary Findings do not wake 02" prohibits per-record automatic wakeups; it does not prohibit a bounded Lead-owned governance task.

Trigger a Lead memory-disposition check when: (a) a user/authorized owner approves, changes or withdraws a durable principle, shared contract, role boundary, product expectation, project scope or major decision; (b) an accepted delivery/integration, tool deployment or phase transition makes a retained Current Fact, Checkpoint, Anchor or authoritative pointer materially stale; (c) a source/scope/authority conflict or Context Challenge remains unresolved; or (d) accumulated reusable cognition reaches stage/milestone closeout. Routine logs, unchanged commits, local defects without reusable cognition and normal READY results are not governance triggers by themselves.

Every producer names durable changes and exact sources in its existing single delivery, including who approved normative changes, what existing memory may be stale, and what remains a candidate. Use the verified Finding capture interface when applicable; capture remains non-triggering. If capture is unavailable or the event arrived directly from the user/admin, preserve it in the existing issue/decision artifact and report it to Lead. Do not silently drop it, invent a Finding API, or claim an empty Finding directory proves nothing needs governance.

Lead performs the check on receiving these events, accepting a child/review, before dependent dispatch, and before stage/milestone/parent closeout. Include `Memory Disposition` in the existing Execution Decision or formal handoff's task decision: source reference/revision, team/project scope, disposition and reason, exact 02 task if needed, owner, due boundary and any affected downstream work. The allowed outcomes are no durable change (with reason), already governed (with exact evidence), governance scheduled (with one real task), or blocked (with an owner and release condition). This is workflow evidence in existing issues/artifacts, not a new Memory Core type or a second ledger, and does not authorize an extra final comment.

For governance scheduled, reuse or create one bounded unassigned 02 task with source/version inventory, recognized authority, affected canonical scope, stale entries, acceptance checks and a due boundary. Deduplicate by scope plus exact source/decision revision against pending and completed tasks. Batch compatible non-urgent changes. Prepare/publish/read back the exact task context, then use one authorized native assignment when the project's professional slot is available. Do not repeatedly mention, reassign, rerun or wake 02 for the same change. If another specialist is running, reserve the next allowed slot and name the active predecessor; do not leave an unspecified "later" backlog item. Native completion returns to Lead under the existing lifecycle.

Block only consumers that need the affected semantics or authority until governance is accepted. Unrelated authorized work can proceed under the explicit current authoritative baseline. No affected stage/milestone or parent closes while a required governance task is merely queued; waiting must name the task, owner and due boundary. A pending product option remains pending, even if its design was approved for review or implementation of an independent subset.

02 validates scope and evidence, distinguishes Norm from Reality and pending proposals, deduplicates and retains/forgets findings, updates only authorized canonical objects/pointers/checkpoints, preserves supersession and historical evidence, and rebuilds/validates the affected derived index. Reference the versioned source contract instead of copying its full text into memory. Return exact commit/artifact, dispositions, validation and remaining conflicts in one delivery; do not dispatch downstream. Lead applies the appropriate review, integrates accepted changes at a safe task boundary, verifies deployed source/index consistency and downstream fresh SELF_CHECK, then marks the recorded disposition governed. A task existing or a prompt being updated is not governance completion.

This scheduling contract belongs to shared team workflow. It does not add automatic triggers to Memory Core, broaden canonical authority, turn every handoff into a 02 hop, or allow public publishing without the applicable authorization.
