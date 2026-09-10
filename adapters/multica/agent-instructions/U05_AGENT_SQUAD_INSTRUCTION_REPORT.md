# U05_AGENT_SQUAD_INSTRUCTION_REPORT

- tool: `tools/chandoff_instructions.py` U05/2.2
- baseline: committed read-only live capture (`baseline.json`)
- status: **staged_candidate_not_active** — activation is owned by U12/Human controlled enablement; nothing in this bundle has been applied to the live workspace
- shared Skill consumed by exact U04 digest; policy stays in `skills/multica-context-handoff/`

## Verdict

staged_candidate_not_active; Ready for Review. Isolated branch `yzt-73-u05-agent-squad-instructions` based on U04 `702cb9bb11120c154a216e66c6ed1d3d632d3b49`. Zero live Agent/Squad/skill writes.

## Preconditions

- Isolated worktree from exact U04 commit `702cb9bb`. Memory `main`, U02/U03/U10/U04 worktrees, and the product repo were not modified.
- Live 05 UUID `b6335f8e-8147-45f7-aac0-8079d85423b5` still displays as `05 Feature Reviewer` (old-role baseline, not V2.2 authority).
- U04 SKILL.md digest matches pin `sha256:f369cee4…`. Artifact Contract remains `sha256:9c2857ae…`. Role profile revision unchanged `sha256:7b3bdf52…`.
- Shared skill is absent from the live workspace catalog.

## Actual Baseline

- CLI `v0.4.42`. Engineering Team squad `ee895c79-ca0a-498b-abe9-5af537736a30`, leader 01, six members. 05 squad role label is still `Feature Reviewer`.
- Live 05/06 runs observed: historical `completed` only; no unexpected active/queued 05/06 runs.
- Out-of-scope agent: Mika. Unrelated bindings (parent-handoff-wake, implementation-handoff, etc.) recorded intact.

## Old-Role Inventory

Classified references: `active_to_migrate` 28 (guards and remaining T09/T10 fixtures), `historical_only` 42, `negative_fixture` 27, `invalid_active_semantics` 0. Historical T08 report kept at `adapters/multica/agent-instructions/T08_REPORT.md`. After-state no longer contains `feature-reviewer.md`.

## V2.2 Role Mapping

| logical role | agent UUID | live name | staged name |
| --- | --- | --- | --- |
| engineering-lead | `24f04aba-…a411` | 01 Engineering Lead | 01 Engineering Lead |
| context-engineer | `8bc546ab-…c065` | 02 Context Engineer | 02 Context Engineer |
| solution-architect | `1303827b-…4418` | 03 Solution Architect | 03 Solution Architect |
| software-engineer | `fa7d16a7-…ca47` | 04 Software Engineer | 04 Software Engineer |
| delivery-reviewer | `b6335f8e-…23b5` | 05 Feature Reviewer | 05 Delivery Reviewer |
| qa | `30ce43d4-…4702` | 06 QA | 06 QA |

`feature-reviewer` resolves to nothing (`retired_no_alias`). Adapter + role-profile data reject it. 05 UUID maps only to `delivery-reviewer`.

## Agent Instructions

01–04/06: owner-boundary sentences preserved; Feature Reviewer routes rewritten; common RUN START / BEFORE HANDOFF / ARTIFACT HANDOFF / NEW COGNITION / TASK CLOSE protocol appended; V2.2 role additions appended. 05: full replacement (Delivery Reviewer, exact-artifact lenses, targeted-only fact verification). 06: QA identity retained, Product & Quality Acceptance added. 02 bound to the shared skill but remains exception-only.

## Squad Instructions

After-state (`after/squad.md`) routes only through 01, encodes Option A R0/R1/R2, forbids fan-out, producer→05 auto-trigger, 05→06 auto-trigger, Assignment+mention, and duplicate Lead wake. Staged 05 member presentation: `Delivery Reviewer`.

## Skill/Binding Plan

Forward ops are explicit add/remove; `set` is rollback-only. `multica-context-handoff` add planned for all six roles (placeholder until U12 import). 05 removes `external-signal-research` and `feature-correctness-review`, adds `delivery-review`. 06 removes `milestone-quality-gate`, adds `product-quality-gate`. Unrelated bindings preserved. Deployed CLI has `agent skills add|set` only — logical remove is recorded; U12 must not approximate it with replace-all `set`.

## Package Invalidation

Old `feature-reviewer` package → `REFRESH_REQUIRED`, not trigger-eligible, no alias rewrite onto `delivery-reviewer`. Runtime proof: no `feature-reviewer.yaml`; adapter `SchemaViolationError`.

## Changes

- Rebuilt `tools/chandoff_instructions.py` as U05/2.2 staging (capture/build/verify/audit/resolve/invalidate).
- Added `tools/chandoff_instruction_texts.py`, `skills/delivery-review/`, `skills/product-quality-gate/`.
- Regenerated `adapters/multica/agent-instructions/**` V2.2 bundle including squad capture.
- T09 `t08_mapping` accepts live 05 display name vs staged name; mention test for retired 05 fails at resolve (no alias).

## Tests/Replays

- Focused U05: 27/27.
- Full `tools/tests`: **580/580 OK**.
- T00 scan: clean.
- U03 retired-role + U04 artifact-readiness tests included in the 580.
- Gate A valid; Gate B passed; Gate C passed.
- Live verify vs captured baseline: ok, drift [].

## Side-Effect Audit

Stage-mode live instruction/skill/Squad/binding writes and downstream triggers: 0. Canonical writes: 0. Product repo changes: 0. Frozen T00 not amended.

## Finding Drain

Unaccounted open Findings at completion: 0.

## Deviations

- Issue-cited U04 skill integration digest `sha256:1df6a40c…` is not reproducible from U04@702cb9b by the T08 bundle algorithm. SKILL.md digest matches the pin; U05 pins the byte-reproducible bundle digest `sha256:7f861c32…` and records the cited value as unreproducible.
- Baseline/bundle JSON keep the T08 family schema prefix so T09/T10 can load the V2.2 after-state without a Frozen-T00 change. U05 contract is `u05_contract: U05/2.2`.
- 01–04 after-state is not pure-append: Feature Reviewer route sentences are rewritten in place so old 05 semantics cannot remain active.

## Risks

- Deployed CLI has no `agent skills remove`. Logical remove is staged; U12 enablement cannot use replace-all `set` without an explicit Lead/Human exception.
- Live 05 display name and squad member role remain `Feature Reviewer` until U12 rename/`set-role`.
- T09/T10 still consume this bundle; they now resolve `delivery-reviewer` and refuse `feature-reviewer` at the role table.

## Blockers

None for staging. U12 apply is blocked on Human/Lead authorization and on a non-`set` remove path for legacy 05/06 skills.

## Recommended Next Decision

Lead: accept this staged bundle as U11 replay input. Do not enable live 05/06. Keep U12 parked until joint replay. Optional: confirm the unreproducible `1df6a40c…` citation is informational only.

## Ready for Review

Yes. Review level R2 foundation configuration only. This producer issue created zero 05/06 runs.

## Done criteria

| criterion | value |
| --- | --- |
| six_v2_roles_resolve | true |
| feature_reviewer_retired_without_alias | true |
| delivery_reviewer_exact_identity_staged | true |
| qa_identity_retained_product_quality_profile_staged | true |
| all_six_common_handoff_protocol | true |
| artifact_aware_protocol | true |
| owner_boundaries_preserved | true |
| squad_lead_only_routing | true |
| r0_r1_r2_lead_mediated | true |
| exact_u04_skill_digest_pinned | true |
| context_handoff_planned_for_all_six | true |
| legacy_05_skills_removed_in_after_state | true |
| unrelated_bindings_preserved | true |
| replace_all_set_used | false |
| rollback_exact | true |
| old_05_package_accepted | false |
| feature_reviewer_activation | 0 |
| producer_auto_triggers_05 | false |
| delivery_reviewer_auto_triggers_06 | false |
| live_writes_or_triggers | 0 |
| frozen_t00_amended | false |
| canonical_writes | 0 |
| product_repo_changes | 0 |
| unaccounted_open_findings_at_completion | 0 |
| live_skill_imports | 0 |
| live_agent_instruction_writes | 0 |
| live_skill_binding_writes | 0 |
| triggered_runs | 0 |

## Exact revisions / digests

- instruction_bundle_revision: `sha256:a93e146d6586cf6f474148aeed092032e3a9c871ad3138ee1152d28108b5b98f`
- binding_plan_revision: `sha256:7f83bfd523e2c0da3cd0dac4568869f3c9937e2d3ae6c0dc66b9853094a27c22`
- artifact_contract_revision: `sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc`
- U04 skill_md_digest: `sha256:f369cee40ada061364d91ef48a9cb7d467039931e922c1db18d85fe1d7e0638a`
- U04 skill_bundle_digest: `sha256:7f861c320c115b328fb45db7356573ae449a5e443ac08b3572a764c79934fce9`
- role_profile_revision (unchanged): `sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f`

## Static checks

| check | passed | detail |
| --- | --- | --- |
| bundle_matches_deterministic_rebuild | true | candidates/ and after/ regenerate byte-identically from the committed baseline |
| six_v2_roles_resolve | true | six logical roles resolve; feature-reviewer resolves to nothing |
| delivery_reviewer_exact_identity_staged | true | 05 UUID maps only to delivery-reviewer in the staged after-state |
| lead_has_pre_dispatch_gate | true | all pre-dispatch markers present |
| professional_agents_have_self_gate | true | SELF_CHECK + artifact protocol in 03/04/05/06 |
| context_engineer_not_normal_path | true | 02 carries the exception path only |
| delivery_reviewer_full_replacement | true | 05 after-state is a full replacement with required lenses |
| qa_product_quality_profile_staged | true | 06 retains QA identity and gains Product & Quality Acceptance |
| squad_lead_only_routing | true | squad routes only through 01 and encodes Option A |
| context_handoff_planned_for_all_six | true | handoff skill add planned for all six roles |
| never_replace_all_set | true | forward ops are explicit add/remove; set is rollback-only |
| legacy_05_skills_removed_in_after_state | true | external-signal-research and feature-correctness-review removed |
| existing_role_authority_preserved | true | 01-04/06 keep owner-boundary sentences; 05 is a full replacement |
| new_blocks_transfer_no_authority | true | every after-state states its non-transfer boundary |
| no_premature_activation_claim | true | no block claims the gates are live before U12 |
| candidate_deltas_carry_no_trigger_surface | true | no assign/binding argv; mention:// only the Lead wake |
| old_05_package_rejected | true | old feature-reviewer package cannot READY/trigger new 05 |
| rollback_complete | true | all six agents plus squad restore exact baseline bytes |
| pinned_u04_skill_digest | true | U04 SKILL.md and Artifact Contract revisions pinned |
| non_activation_guarantees_all_zero | true | capture trace: zero live writes and triggers |
| unaccounted_open_findings_at_completion | true | no invalid active old-role semantics remain in the staging surface |
| final_live_state_equivalent | true | end-of-task re-read of live agent/skill/squad state verified equivalent to the committed baseline |

## Non-activation guarantees

```json
{
  "live_skill_imports": 0,
  "live_skill_refreshes": 0,
  "live_agent_instruction_writes": 0,
  "live_skill_binding_writes": 0,
  "live_squad_writes": 0,
  "assignments": 0,
  "mentions": 0,
  "downstream_run_triggers": 0,
  "issue_lifecycle_writes": 0,
  "canonical_writes": 0,
  "product_repo_changes": 0,
  "frozen_t00_amended": 0
}
```

## End-of-task live re-read

- ok: true
- mode: stage
- drift: []

