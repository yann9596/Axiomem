# R6 staged contract updates (applied at Cutover window, not before)

Platform-side instructions/skills carry V1.0 semantics until Cutover. Per the
issue contract ("Compatibility Adapter 只能翻译旧调用"), these texts are the
staged V1.1 replacements; installing them is part of R10 (issue: platform
updates land when the workspace owner approves cutover).

## 1. Agent contract change (all seven roles)

```text
GET_CONTEXT (task, role, project)  → context build (V1.1 scope-first package)
REPORT_FINDING (summary, refs)     → finding ingest (runtime artifact)
CHALLENGE_CONTEXT (ref, reason)    → context challenge (target -> review_needed)
PROPOSE_MEMORY                     → DEPRECATED alias of REPORT_FINDING with
                                     intent=durable_candidate
```

V1.1 semantic deltas to communicate at cutover:

- Agents never write canonical memory directly; findings are processed by the
  Context Engineer through the retention pipeline (Spec §19).
- Roles no longer carry dynamic project knowledge in their prompts; project
  knowledge comes from the Context System via Role Context Profiles
  (team-context/roles/*.yaml).
- PROPOSE_MEMORY wording disappears from contracts; only REPORT_FINDING
  remains (deprecated alias kept for the compatibility window only).

## 2. canonical-memory-governance skill (v1.1 content delta)

- Object model: Anchor / Rule / Current Fact / Case / Finding / Checkpoint
  (Pointer and Forget are dispositions, not types).
- A Rule requires a resolvable authority_ref; code patterns, repeated
  behavior, or LLM inference never create authority (D-10).
- verification enum replaces numeric confidence (no pseudo-precision scores).
- Scope-first: nothing is written or retrieved before scope resolution.
- Finding lifecycle: open → processed + disposition
  (create_rule/create_fact/create_case/carry_to_checkpoint/pointer/forget/
  issue_escalation/absorbed_by_existing).
- Rule candidates stay findings/issues until authority exists.

## 3. minimum-context-package skill (v1.1 content delta)

- Package schema 1.1 (anchor_digest, checkpoint slices, rules with authority
  refs, facts with verification, conditional cases with activation_reason,
  open_conflicts, blocked_by, assembly_trace).
- Scope resolved before retrieval (D-02); unknown project stops the build.
- Case search default OFF; enabled only for listed triggers; uncertain
  scenario match → no_match.
- Retrieval trusts the last valid verification (D-22); full revalidation only
  on review_needed / source-change impact / challenge / high risk.

## 4. Workspace Instructions cleanup (platform-managed)

The workspace AGENTS.md sections that currently inline dynamic project
knowledge (memory entry paths, product repo paths, per-project constraints)
move at cutover to: registry (project ids, phases), project anchors (paths,
mission), and repo-local `.ai/context.yaml`. Instructions keep only the
system-level pointer to the context system. Until cutover, the V1-style
pointers in instructions stay authoritative to avoid two truths.
