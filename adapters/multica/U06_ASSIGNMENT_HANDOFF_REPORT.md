# U06_ASSIGNMENT_HANDOFF_REPORT

- tool: `tools/chandoff_assignment.py` U06/2.2
- dispatch: `tools/chandoff_dispatch.py` U06/2.2
- branch: `yzt-74-u06-assignment-handoff` based on U05 `b43b68b5deae6d8636f02293f2abb52ecb5f65e3` (commit recorded at handoff)
- status: **staged_simulation_only** — live issue/comment/assignment execution is not authorized and was not exercised

## Verdict

Ready for Review. Assignment SAFE_DISPATCH rebased onto the accepted U05 V2.2 identity and U04/U10 Artifact runtime. Zero live mutations or triggered runs from this producer task.

## Preconditions

- Isolated worktree from exact U05 commit `b43b68b5deae6d8636f02293f2abb52ecb5f65e3`. Memory `main`, prior U worktrees, Canonical Memory, and the product repository were not modified.
- U05 instruction/binding pins reconfirmed: `instruction_bundle_revision sha256:a93e146d…`, `binding_plan_revision sha256:7f83bfd5…`.
- U04 SKILL.md `sha256:f369cee4…`, skill bundle `sha256:7f861c32…`. U10 Artifact Contract `sha256:9c2857ae…`.
- `feature-reviewer` resolves to nothing. 05 UUID `b6335f8e-…23b5` maps only to staged `delivery-reviewer`.
- Read-only Multica create/update/assign/comment/runs capabilities captured in `adapters/multica/assignment-handoff/capabilities.json`. Tests use only documented allowlists plus the observed issue-runs list-of-objects contract (`id`, `issue_id`, `agent_id`, `status`).

## Accepted Inputs

| input | pin |
| --- | --- |
| Parent YZT-66 | SAFE_DISPATCH, Option A, Artifact Contract, Hard Stops |
| U05 | `b43b68b5deae6d8636f02293f2abb52ecb5f65e3` |
| U04 | `702cb9bb11120c154a216e66c6ed1d3d632d3b49` |
| U10 | `73f922ea33c2e2867ba51b6843588c5aa4980ff6` |
| U03 Frozen-T00 | `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (not amended) |

## Historical Inventory

See `adapters/multica/assignment-handoff/historical-inventory.json`. Fail-closed create/assign/note/replay mechanics retained. T08 identity, 02-never-target (Assignment route), live-05 display-name routing, and “incomplete always refuse” recovery adapted or invalidated per U05/U04/U10. Mention, cross-route fallback, Finding redesign, joint replay, and live enablement deferred to U07/U08/U09/U11/U12.

## Architecture / State Machine

```text
INIT
→ ISSUE_CREATED | ISSUE_UPDATED     (NON_TRIGGER_MUTATION, unassigned)
→ TARGET_BOUND                      (U05 role + UUID + required artifacts + tx)
→ RUN_PRECHECK_PREPARE              (issue runs --active --siblings)
→ HANDOFF_PREPARED                  (T05→T01→injected compose→T02/T03)
→ ARTIFACT_READY                    (U04 apply_finalize_gate / U10 ready-check)
→ HANDOFF_PUBLISHED                 (exactly one /note)
→ HANDOFF_READY_CONFIRMED           (re-resolve package/role/task/comment)
→ RUN_PRECHECK_TRIGGER              (freshness + zero unexpected run)
→ ASSIGNMENT_TRIGGERED              (exactly one issue assign --to-id)
→ RUN_CORRELATED                    (exactly one new intended run)
→ TARGET_SELF_CHECKED               (READY before work; one bounded refresh)
→ COMPLETED
```

`t08_mapping` / `resolve_target` remain for T10 mention (02 still never a mention target). Assignment uses `u05_mapping` / `resolve_assignment_target`.

## Artifact Gate

- Declared `required_artifacts` run `cartifact.artifact_ready_check` before PREPARE and U04 `apply_finalize_gate` after T03.
- Exact dependency digest is stored in the ledger (`artifact_gate` / `artifact_freshness`).
- Missing, wrong version, stale, superseded, wrong-consumer, or changed digest → `PACKAGE_STALE` before publish/trigger.
- Empty declared set records the empty digest and is ARTIFACT_READY (T00-only drills).
- Old `feature-reviewer` packages cannot READY, assign, or rewrite onto `delivery-reviewer`.

## Run Precheck / Correlation

- Before publish and before Assignment: `issue runs --active --siblings --output json`.
- Any active run, truncated listing, non-list JSON, or missing required fields → `UNEXPECTED_RUN` / `RUN_STATE_UNDETERMINED`, zero new trigger.
- After Assignment: snapshot known run ids, require exactly one new run for the target issue + agent. Zero / duplicate / wrong-target → `RUN_CORRELATION_FAILED`.

## Retry / Recovery Matrix

| boundary | unique next action |
| --- | --- |
| completed | replay; zero new create/note/assign/mention/run |
| pre-publish (canonical issue exists) | skip create; continue prepare/publish/trigger |
| post-publish, trigger not issued | reuse published note iff comment/package/role/task still match and remain fresh; never republish because a local response was lost |
| trigger possibly issued | `TRIGGER_CONFIRMATION_REQUIRED`; never retry |
| recorded incomplete terminal | `REPLAY_REFUSED` |
| mention/U08 needed | `CROSS_ROUTE_REQUIRED` (not implemented here) |

Samples: `sample-recovery-pre-publish-result.json`, `sample-recovery-post-publish-result.json`, `sample-recovery-post-trigger-result.json`.

## Exact Revisions / Digests

- U05 commit: `b43b68b5deae6d8636f02293f2abb52ecb5f65e3`
- instruction_bundle_revision: `sha256:a93e146d6586cf6f474148aeed092032e3a9c871ad3138ee1152d28108b5b98f`
- binding_plan_revision: `sha256:7f83bfd523e2c0da3cd0dac4568869f3c9937e2d3ae6c0dc66b9853094a27c22`
- artifact_contract_revision: `sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc`
- U04 skill_md_digest: `sha256:f369cee40ada061364d91ef48a9cb7d467039931e922c1db18d85fe1d7e0638a`
- U04 skill_bundle_digest: `sha256:7f861c320c115b328fb45db7356573ae449a5e443ac08b3572a764c79934fce9`
- role_profile_revision (unchanged): `sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f`
- U06 assignment runtime: `tools/chandoff_assignment.py sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129`
- U06 dispatch runtime: `tools/chandoff_dispatch.py sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa`
- Memory/Registry/Role Profile revisions unchanged

## Changes

- Rebased `tools/chandoff_assignment.py` to U06/2.2: U05 identity, artifact bind, dual run precheck, run correlation, crash recovery.
- Extended `tools/chandoff_dispatch.py`: `issue runs` read contract, non-trigger `issue update --no-start`, audit of update class.
- Kept T10 `t08_mapping` / `resolve_target` behavior (02 never a mention target).
- Added adversarial tests in `tools/tests/test_handoff_assignment.py` (58 focused).
- Evidence under `adapters/multica/assignment-handoff/` and this report.

## Tests / Replays

- Focused U06 assignment: 58/58.
- T10 mention: 72/72.
- Full `tools/tests`: **595/595 OK**.
- T00 scan (`tools/chandoff.py scan`): clean.
- U04 artifact-readiness and U05 instruction tests included in the 595.
- Gate A valid; Gate B passed; Gate C 6/6 PASS. `migration/gate-results/*.json` refreshed at this HEAD.

## Side-Effect Audit

- Live issue create/update/comment/assign/status: 0
- Mentions constructed: 0
- Triggered runs from this producer: 0
- Canonical writes: 0
- Product repo changes: 0
- Frozen T00 amended: false
- U05/U12 live Agent/Squad/skill/binding writes: 0

Read-only probes used: `issue get`, `issue comment list`, `issue runs` (this task, already running), `--help`.

## Finding Drain

Unaccounted open Findings at completion: 0.

## Deviations

- T10 mention still consumes `t08_mapping` / `resolve_target` so U07 is not silently rewritten. Assignment-only identity authority is `u05_mapping`.
- Empty `required_artifacts` is ARTIFACT_READY with the empty digest so T00-only drills and existing T09 cases remain exercisable; main-path-with-artifacts is separately proven.
- U05 `old-role-inventory.json` and `bundle.json` files-map digest were regenerated so the U05 byte-stability test still matches after `test_handoff_assignment.py` line numbers shifted. Counts unchanged (`active_to_migrate` 28, `historical_only` 42, `negative_fixture` 27). `instruction_bundle_revision` and `binding_plan_revision` pins unchanged.
- `.gitattributes` pins U05 skill trees and U06 assignment evidence as `-text` so Windows `core.autocrlf` cannot break U05 skill-bundle pins.
- Assignment confirmation remains marker-based over the deployed assign JSON (`id` or `assignee_id`). Unrecognized success still fails closed to `TRIGGER_CONFIRMATION_REQUIRED`.
- U12 missing non-`set` skill removal path is recorded unchanged as a future enablement blocker.

## Risks

- Observed issue-runs JSON is not a Frozen Public Schema; extra fields are ignored, missing required fields fail closed.
- Platform-side create/assign atomicity is still not claimed (ledger uncertainty).
- Live 05 display name remains `05 Feature Reviewer` until U12; U06 refuses that token and does not alias it.

## Blockers

None for staged Assignment runtime. Live enablement stays with U12. Mention remains U07. Cross-route fallback remains U08. Joint replay remains U11.

## Recommended Next Decision

Lead: accept this staged Assignment runtime as Stage 5 input. Do not enable live 05/06. Next Near Term: U07 Mention Handoff on the same U05 pin, reusing this ledger/run-precheck contract. Keep U12 parked.

## Ready for Review

Yes. Review level R2 foundation runtime only. Formal Delivery Review and QA stay parked until U11/U12.

## Done criteria

| criterion | value |
| --- | --- |
| non_trigger_mutation_first | true |
| exact_role_and_artifacts_bound | true |
| artifact_ready_before_publish_and_trigger | true |
| unexpected_run_precheck | true |
| one_note | true |
| assignment_only_trigger | true |
| intended_run_count | 1 |
| mention_count | 0 |
| self_check_ready_before_work | true |
| pre_publish_safe | true |
| post_publish_pre_trigger_reconciled | true |
| post_trigger_ambiguous_never_retried | true |
| completed_replay_idempotent | true |
| feature_reviewer_resolves | false |
| old_05_package_accepted | false |
| exact_u05_revisions_pinned | true |
| exact_artifact_contract_revision_pinned | true |
| live_mutations_or_triggers | 0 |
| frozen_t00_amended | false |
| canonical_writes | 0 |
| product_repo_changes | 0 |
| unaccounted_open_findings | 0 |
