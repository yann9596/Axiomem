# T08 — Agent Instruction Contract — Static Audit (YZT-63)

- tool: `tools/chandoff_instructions.py` T08/1.0
- baseline: committed read-only live capture (`baseline.json`)
- status: **staged_candidate_not_active** — activation is owned by T13 controlled enablement; nothing in this bundle has been applied to the live workspace
- shared Skill referenced by name only; deterministic policy stays in the accepted T07 Skill (`skills/multica-context-handoff/`)

## §33 Done criteria

| criterion | value |
| --- | --- |
| lead_has_pre_dispatch_gate | true |
| professional_agents_have_self_gate | true |
| professional_agents_have_pre_handoff_gate | true |
| context_engineer_not_normal_path | true |
| existing_role_authority_preserved | true |
| exactly_one_trigger_preserved | true |
| rollback_complete | true |
| live_skill_imports | 0 |
| live_agent_instruction_writes | 0 |
| live_skill_binding_writes | 0 |
| triggered_runs | 0 |

## Static checks

| check | passed | detail |
| --- | --- | --- |
| bundle_matches_deterministic_rebuild | true | candidates/ and after/ regenerate byte-identically from the committed baseline |
| lead_has_pre_dispatch_gate | true | all pre-dispatch markers present |
| professional_agents_have_self_gate | true | SELF_CHECK before consequential work + READY/REFRESH_REQUIRED/BLOCKED routing in 03/04/05/06 |
| professional_agents_have_pre_handoff_gate | true | PREPARE_HANDOFF before dispatch + exactly-one-trigger in 03/04/05/06 |
| context_engineer_not_normal_path | true | 02 carries the exception path only; ordinary READY/REFRESH routing never targets 02 (lead/professional blocks state the boundary) |
| binding_plan_additive_exactly_five | true | additive `add` ops only, exactly 01/03/04/05/06, `set` never used forward; 02 excluded with rationale |
| existing_role_authority_preserved | true | each post-apply text starts with the exact baseline instruction text (pure append, zero loss) |
| new_blocks_transfer_no_authority | true | every block states its non-transfer boundary explicitly |
| no_premature_activation_claim | true | no block claims the gates are live before T09/T13 |
| candidate_deltas_carry_no_trigger_surface | true | no mention link / assign / binding argv inside candidate deltas |
| rollback_complete | true | all six agents restore exact baseline instruction text and binding sets; declarative commands only, no run triggers |
| non_activation_guarantees_all_zero | true | capture trace: zero skill imports, zero instruction writes, zero binding writes, zero assignments/mentions/run triggers |
| stage_state_skill_absent | true | at capture time the skill was absent from the workspace catalog and unbound on all six agents |
| final_live_state_equivalent | true | end-of-task re-read of live agent/skill state verified byte/ID-equivalent to the committed baseline |

## Non-activation guarantees

```json
{
  "live_skill_imports": 0,
  "live_skill_refreshes": 0,
  "live_agent_instruction_writes": 0,
  "live_skill_binding_writes": 0,
  "assignments": 0,
  "mentions": 0,
  "downstream_run_triggers": 0,
  "issue_lifecycle_writes": 0,
  "canonical_writes": 0
}
```

## End-of-task live re-read

- ok: true
- mode: stage
- drift: []

Live agent/skill state was re-read read-only at task end and verified ID-equivalent (agent ids, instruction digests, binding sets, catalog state) to the committed baseline. `updated_at` fields are provenance-only and excluded from equivalence.
