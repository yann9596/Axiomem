# U08_DIRECT_ASSIGNMENT_FALLBACK_REPORT

- tool: `tools/chandoff_fallback.py` U08/2.2
- focused tests: `tools/tests/test_handoff_fallback.py` (73 tests)
- branch: `yzt-76-u08-direct-assignment-fallback` based on exact U07 commit `a8fcab19488f38d622ff731eb56eb14b39f8adb5`
- status: **staged_simulation_only** — live comment/assignment/mention/status/run execution is not authorized and was not exercised
- upstream runtime files (`tools/chandoff_mention.py`, `tools/chandoff_assignment.py`, `tools/chandoff_dispatch.py`) are byte-unchanged; U08 subclasses and composes their accepted primitives

## Verdict

Ready for Review. The deferred cross-route fallback is implemented as a deterministic, fail-closed protocol over the shared U06/U07 ledger: reconcile the source Mention route from durable + trusted read-only evidence, classify it, then either resume the frozen route, stop forever, replay, or — only from `NO_TRIGGER_PROVEN` with a Lead-owned authorization — close the source transaction as a non-trigger terminal and run exactly one NEW Assignment transaction (one note, one Assignment, one correlated run, target SELF_CHECK READY). Zero live mutations or triggered runs from this producer task; no second trigger mechanism was added.

## Preconditions

- Isolated worktree `multica-memory-yzt-76-u08` created from exact U07 commit `a8fcab19488f38d622ff731eb56eb14b39f8adb5`; memory `main` (`95c434d`), prior U worktrees, Canonical Memory and the product repository are untouched.
- Fresh isolated checkout of exact U07 reproduced the accepted runtime digests (below) and the full suite (615/615) before any U08 change.
- U05/U04/U10 pins reconfirmed from the staged bundles and re-verified by every U08 run through `u05_mapping` (instruction bundle `sha256:a93e146d…`, binding plan `sha256:7f83bfd5…`, U04 SKILL.md `sha256:f369cee4…`, U04 skill bundle `sha256:7f861c32…`, Artifact Contract `sha256:9c2857ae…`).
- Memory/Registry/Role-Profile revisions unchanged (`30b51dea` / `a08e20eb` / `7b3bdf52`).
- `feature-reviewer` resolves to nothing and never aliases; the live display token `05 Feature Reviewer` is refused without alias; the stable 05 UUID resolves only to staged `delivery-reviewer`.
- The U07 byte-state correction is preserved: the six CRLF `skills/multica-context-handoff/**` files are untouched and the pinned raw-byte bundle digest still reproduces (`u05_mapping` passes).
- Read-only evidence shapes captured in `adapters/multica/assignment-fallback/capabilities.json`; run evidence reuses the accepted U06 `issue runs` list-of-objects contract, and any truncation/unreadability/absence is `EVIDENCE_UNTRUSTED`, never zero-run proof.

## Accepted Inputs

| input | pin |
| --- | --- |
| Parent YZT-66 | Authority Order, Engineering Lead ownership, SAFE_DISPATCH, corrected Stage order, Artifact Contract, Option A, Finding/Challenge boundaries, stage-wake idempotence, Hard Stops, Final Gate |
| U07 | `a8fcab19488f38d622ff731eb56eb14b39f8adb5` (mention runtime `sha256:d3bba5b4…`, focused tests `sha256:ba52f615…`) |
| U06 | `f35afdf91133c3ea21432684fa62f34f0eae12c1` (assignment runtime `sha256:2d701541…`, dispatch `sha256:62dbd081…`) |
| U05 | `b43b68b5deae6d8636f02293f2abb52ecb5f65e3` (`instruction_bundle_revision sha256:a93e146d…`, `binding_plan_revision sha256:7f83bfd5…`) |
| U04 | `702cb9bb11120c154a216e66c6ed1d3d632d3b49` (SKILL.md `sha256:f369cee4…`, bundle `sha256:7f861c32…`) |
| U10 | `73f922ea33c2e2867ba51b6843588c5aa4980ff6` (Artifact Contract `sha256:9c2857ae…`) |
| U03 Frozen-T00 | `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (not amended) |

## Historical Inventory

Full classification in `adapters/multica/assignment-fallback/historical-inventory.json`: **retained 8 / adapted 4 / invalid 8 / deferred 1** (21 items; the historical material overall is migration evidence only).

- Retained: existing canonical issue reuse; exact role/UUID binding; one non-trigger `/note` before any trigger; dual unexpected-run precheck; run correlation against a pre-trigger snapshot; target SELF_CHECK READY before consequential work; completed replay idempotence; simulation default with separate live authorization.
- Adapted: non-trigger mutation → the fallback issues **no** create/update at all inside its transaction (read-only binding of the exact issue; audit asserts `issue_create = 0`, `issue_update = 0`); assignee observation → expected-before/expected-after bound by the Lead authorization; T08 identity → U05 `u05_mapping` + V2.2 role policy; injected run evidence → observed issue-runs listing; package-note reuse → idempotent publication of a freshly prepared identical package (old note preserved, zero duplicate publication).
- Invalid: issue creation inside the fallback; timeout/operator-preference route switch; retry/repair of a possibly issued mention; retry or downgrade of an uncertain assignment; reuse of a READY envelope or source package as fallback authorization; mutation of the source transaction/route in place; published-note reuse across transactions as an authorization; operator judgment choosing the classification; U08 activating 05/06.
- Deferred: U09 Finding/Challenge alignment; U11 joint replay (including wiring the live closed-source guard); U12 live enablement and the non-`set` skill-removal path.

## Architecture / State Machine

```text
INIT
→ SOURCE_RECONCILED     (read-only: issue + assignee + issue-runs; ledger classification)
→ SOURCE_CLOSED         (only NO_TRIGGER_PROVEN; non-trigger terminal closure)
→ AUTHORIZATION_BOUND   (Lead cross-route authorization validated against durable digest)
→ SUPERSESSION_BOUND    (new fallback tx linked to source tx/package/note)
→ EXISTING_ISSUE_BOUND  (U06 subclass: read-only bind; zero create/update)
→ TARGET_BOUND          (U05 role/UUID + artifacts + V2.2 policy)
→ RUN_PRECHECK_PREPARE
→ HANDOFF_PREPARED
→ ARTIFACT_READY
→ HANDOFF_PUBLISHED     (reused/issued through the accepted U06 publisher)
→ HANDOFF_READY_CONFIRMED
→ RUN_PRECHECK_TRIGGER
→ ASSIGNMENT_TRIGGERED  (exactly one `issue assign --to-id`; assignee observed before/after)
→ RUN_CORRELATED        (exactly one new intended run)
→ TARGET_SELF_CHECKED
→ COMPLETED
```

Non-fallback decisions terminate the coordinator transaction with a typed status and zero fallback actions: `ROUTE_RESUMABLE` (resume the frozen mention/assignment route via the accepted U06/U07 recovery), `TRIGGERED` (continue read-only correlation/self-check), `TRIGGER_AMBIGUOUS` (stop forever), `COMPLETED` (source replay), plus the stricter `SOURCE_UNKNOWN`, `SOURCE_ROUTE_UNKNOWN`, `SOURCE_ROUTE_UNSUPPORTED`, `EVIDENCE_UNTRUSTED`, `AUTHORIZATION_REQUIRED`, `AUTHORIZATION_INVALID`, `CLOSURE_CONFLICT`, `ASSIGNEE_DRIFT`, `STAGE_WAKE_DUPLICATE`.

Everything flows through one shared `dispatch.TransactionLedger`; U08 adds typed records only: `cross_route_reconciliation`, `cross_route_authorization`, `cross_route_closure`, `cross_route_supersession`, `cross_route_assignee_observation`, `cross_route_fallback_result`. No second evidence store exists.

## Cross-route Decision Table

Full deterministic table in `adapters/multica/assignment-fallback/decision-table.json` (26 rows). Summary:

| boundary | classification | next action | fallback |
| --- | --- | --- | --- |
| source pre-publish (recorded stop, no note) | `NO_TRIGGER_PROVEN` | close + fallback | allowed w/ Lead authorization |
| source pre-publish (crash, no result) | `ROUTE_RESUMABLE` | resume mention route | no |
| source post-publish/pre-READY (recorded stop) | `NO_TRIGGER_PROVEN` | close + fallback; note idempotent | allowed w/ Lead authorization |
| source post-publish/pre-READY (crash) | `ROUTE_RESUMABLE` | resume, reuse note | no |
| MENTION_READY / pre-native-send | `TRIGGER_AMBIGUOUS` | stop forever | never |
| native-send response absent / receipt rejected | `TRIGGER_AMBIGUOUS` | stop forever | never |
| native receipt confirmed / pre-run | `TRIGGERED` | continue correlation read-only | never |
| source run confirmed / pre-SELF_CHECK | `TRIGGERED` | continue read-only | never |
| source completed | `COMPLETED` | replay, zero side effects | never |
| fallback crash pre-publish / post-publish | `CRASH_SIMULATED` | recover the SAME transaction uniquely | n/a |
| assignment response absent (either route) | `ASSIGNMENT_CONFIRMATION_REQUIRED` | stop forever; read-only reconcile | never |
| fallback completed | `COMPLETED` | replay, zero side effects | n/a |
| truncated/unreadable run listing, pending non-read command | `EVIDENCE_UNTRUSTED` | refresh read-only evidence | never |
| active / target / wrong-agent run | `TRIGGER_AMBIGUOUS` (`UNEXPECTED_RUN`) | stop forever | never |
| unknown source / unknown route / route collision | stricter typed stop | escalate | never |
| assignment-route source stopped | `SOURCE_ROUTE_UNSUPPORTED` | escalate; assignment→mention is forbidden | never |

Fallback is permitted **only** from `NO_TRIGGER_PROVEN`: durable zero-trigger evidence (`mention_outcome = 0`, no `mention_ready` authorization, `assignment_trigger = 0`, `trigger_outcome = 0`, `run_outcome/run_correlation = 0`) plus a trusted untruncated run listing with zero active runs and zero runs not provably pre-existing (the accepted `known_run_ids` when present, otherwise zero target-agent runs).

## Role / Assignee Policy

- Only the six U05 logical roles resolve; `feature-reviewer` and the live old display name are rejected without alias; ordinary-path 02, producer→05, and 05→06 are rejected; 02 requires `unresolved_material_exception`; 05 requires Lead caller + `lead_owned_review_routing` + exact Review gate artifacts; 06 requires Lead caller + `lead_owned_qa_routing` + exact QA gate artifacts. `latest`/placeholder versions are forbidden.
- Fallback target and UUID must equal the source reconciliation's target; the authorization cannot retarget a handoff.
- Assignee before and after are bound: `expected_assignee_before` must match the observed assignee exactly; the fallback mutates it to the target intentionally. A non-null prior assignee requires the Lead authorization to name the exact issue, target UUID, prior assignee and reason (`reassignment` block); any other drift (`ASSIGNEE_DRIFT`) and any already-assigned-to-target state block with zero fallback actions for the drift case and no trigger at all for the invalid-authorization case.
- Stage completion wake and any Lead-directed trigger remain mutually exclusive: a fallback to `engineering-lead` with `stage_wake_applies` stops as `STAGE_WAKE_DUPLICATE`; U08 never emits a mention and never manufactures a second Lead run.

## Artifact Gate

- `required_artifacts` are bound through `cartifact.dependency_digest` at reconciliation and re-run through the accepted U06/U04/U10 gates: `artifact_ready_check` before PREPARE, `apply_finalize_gate` after T03, and `evaluate_freshness` after publication and before the trigger. Missing, ambiguous, wrong-consumer, stale, superseded or changed dependencies stop as `PACKAGE_STALE` with zero note and zero trigger.
- Empty declared sets record the empty digest and are ARTIFACT_READY (inherited U06 deviation, so T00-only drills stay exercisable); the R1 artifact path is separately proven end-to-end (Lead → `delivery-reviewer`, `ARTIFACT_READY`, COMPLETED).
- The exact artifact dependency digest is recorded in the reconciliation (`artifact_dependency_digest`) and in the ledger.

## Trigger / Run Evidence

- Publication: exactly one publish operation through the accepted U06 `NoteCli`/`publish_handoff`; pre-publish sources produce one NEW non-trigger `/note`; when the fresh package is byte-identical to an already published source package the publisher is idempotent (zero writes, the old note preserved verbatim). Duplicate publications are rejected by the accepted package-id conflict rules.
- Trigger: exactly one `issue assign --to-id <U05 UUID>` and zero mention/status/update/create triggers; any assign response failure or unconfirmable response maps to `ASSIGNMENT_CONFIRMATION_REQUIRED` and is never retried.
- Run correlation: exactly one new run for the exact issue + target agent against the pre-trigger snapshot; zero / duplicate / wrong-target / pre-existing / ambiguous runs stop as `RUN_CORRELATION_FAILED`.
- Target SELF_CHECK must be READY before consequential work; bounded refresh and BLOCKED/EXHAUSTED stops are inherited from U06.
- Assignee re-observation at before / pre-trigger / post-trigger / after; any mismatch stops.

## Retry / Recovery Matrix

| boundary | unique next action |
| --- | --- |
| completed (source or fallback) | replay; zero note/assignment/mention/status/run |
| source resumable (crash pre-publish / post-publish-pre-READY) | resume the frozen route via U07/U06 recovery; fallback refused |
| fallback crash pre-publish | continue the SAME fallback transaction from the bound issue |
| fallback crash post-publish/pre-assignment | reuse the exact recorded note; never republish from a lost local response |
| assignment possibly issued | `ASSIGNMENT_CONFIRMATION_REQUIRED`; read-only reconciliation, never a second assignment or a mention fallback |
| recorded incomplete fallback | `REPLAY_REFUSED` |
| source closed by another fallback | `CLOSURE_CONFLICT`; one source route has at most one fallback |
| untrusted run evidence | `EVIDENCE_UNTRUSTED` → refresh read-only evidence (`suggested_status RUN_STATE_UNDETERMINED`) |

Representative evidence: `sample-main-path-pre-publish-*`, `sample-main-path-post-publish-*`, `sample-resumable-source-*`, `sample-staged-mention-*`, `sample-authorization-required-*`, `sample-assignment-confirmation-required-*`, `sample-untrusted-evidence-*`, `sample-recovery-pre-publish-*`, `sample-closure-conflict-*`, plus `success-failure-matrix.json` (48 cases).

## Exact Revisions / Digests

- U07 base `a8fcab19488f38d622ff731eb56eb14b39f8adb5`; U07 runtime `tools/chandoff_mention.py` `sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1` (unchanged); U07 focused tests `sha256:ba52f6153de7d7fbad55c625ed4fb17e53c840fbec090c2bb1d4123e743e2a72` (unchanged).
- U06 runtime `tools/chandoff_assignment.py` `sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129` (unchanged); dispatch `tools/chandoff_dispatch.py` `sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa` (unchanged).
- U05 commit `b43b68b5deae6d8636f02293f2abb52ecb5f65e3`; `instruction_bundle_revision sha256:a93e146d6586cf6f474148aeed092032e3a9c871ad3138ee1152d28108b5b98f`; `binding_plan_revision sha256:7f83bfd523e2c0da3cd0dac4568869f3c9937e2d3ae6c0dc66b9853094a27c22`.
- U04 `702cb9bb11120c154a216e66c6ed1d3d632d3b49`; SKILL.md `sha256:f369cee40ada061364d91ef48a9cb7d467039931e922c1db18d85fe1d7e0638a`; skill bundle `sha256:7f861c320c115b328fb45db7356573ae449a5e443ac08b3572a764c79934fce9`.
- U10 `73f922ea33c2e2867ba51b6843588c5aa4980ff6`; Artifact Contract `sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc`.
- U03 Frozen-T00 `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (unchanged).
- Memory `sha256:30b51dea…`; Registry `sha256:a08e20eb…`; Role Profile `sha256:7b3bdf52…` (all unchanged).
- U08 runtime `tools/chandoff_fallback.py` `sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b` (committed blob).
- U08 focused tests `tools/tests/test_handoff_fallback.py` `sha256:37de2a32423ecd7222309d92946ef99ee24a223eedaad63ac91f38cfa6bc71b3` (committed blob).
- U08 authored evidence bundle (19 files under `adapters/multica/assignment-fallback/`) `sha256:810ecfd743ab7cb4d4041dda04d0a1fa768e5695ae72ff1332aacbfcac3f7818`. Method: canonical JSON of sorted `file name → sha256(bytes)`.
- U08 deterministic replay proof: main-path sample result `sample-main-path-pre-publish-result.json` `sha256:8cc3195ea5411d53391772b30867579f733dcba361222cd30432e73569c3732a` with ledger `sample-main-path-pre-publish-ledger.jsonl` `sha256:0062e7566ea5f1b460ae785dd5ec370678957fbefe4ca7b7fd121fa6adf759bf`; a second identical fixture run is byte-identical over the full canonical result (test `test_fallback_result_is_deterministic`), and replaying a recorded fallback re-issues zero commands.
- U08 implementation + evidence commit `c1b4e176b01b007f1f134c687cb053c6b392b31d` (gate results refreshed at that commit); this report is committed on top and does not change any pinned digest.

## Changes

- Added `tools/chandoff_fallback.py` (U08/2.2): deterministic source-route reconciliation classifier; typed cross-route envelopes (reconciliation, authorization, closure, supersession, assignee observation, fallback result); Lead-owned authorization validator with intentional-reassignment guard; `AssignmentFallbackHandoff` subclassing U06's `AssignmentHandoff` to bind the existing canonical issue read-only (zero create/update) and observe the intentional reassignment; `cross_route_audit_v2` shared-ledger audit extension; `recover_assignment_fallback` and `resume_source_route`; acceptance-evidence computation; simulation-only CLI.
- Added `tools/tests/test_handoff_fallback.py` (73 focused tests): classification, authorization, main paths, ambiguity, fail-closed, audit, recovery, artifact path, pins, CLI.
- Added the evidence bundle under `adapters/multica/assignment-fallback/`.
- Extended `.gitattributes` with `adapters/multica/assignment-fallback/** -text` for byte-stable evidence.
- Refreshed `migration/gate-results/*.json` at this HEAD (only `generated_at` changed in A/C; B unchanged).
- No U06/U07 runtime, schema, skill, instruction, Canonical Memory or product-repository file was modified.

## Tests / Replays

- Focused U08 fallback: **73/73 OK**.
- Focused U07 mention compatibility: **92/92 OK**; focused U06 assignment compatibility: **58/58 OK**.
- Focused U04 artifact readiness: **26/26 OK**; focused U05 instructions: **27/27 OK**.
- Full `tools/tests` (`unittest discover`): **688/688 OK** (615 baseline on exact U07 + 73 new).
- T00/framework boundary scan (`tools/chandoff.py scan`): `{"clean": true, "violations": {}}`; frozen boundary and no-drive-letter checks pass inside the full suite; `test_handoff_u03_revalidation` **14/14 OK** (byte-state/pin checks).
- Gates at this HEAD: A valid (`all_objects_schema_valid`, silent_drop 0, invalid authority 0); B passed 10/10; C passed 6/6 replays.
- Replay proofs: pre-publish source → one NEW note, one assignment, one run, SELF_CHECK READY; post-publish source → identical package publication is idempotent (one note total, zero new), then one assignment and one run; same evidence + authorization produce byte-identical full result artifacts; completed replay and decision replay emit zero new commands and zero new ledger records; resumable sources resume and never fall back; ambiguity matrix never falls back; fallback crash recovery continues uniquely and never repeats publication or trigger.

## Byte-state Verification

- U07 runtime/tests, U06 runtime/dispatch blob digests recomputed at this HEAD and equal to the accepted pins (see above).
- The six CRLF `skills/multica-context-handoff/**` files required by the accepted U04 raw-byte bundle pin are untouched; `u05_mapping()` reproduces `sha256:7f861c32…` and every U08 run re-verifies the pins.
- `git status` at the final HEAD is clean; no memory `main`, prior worktree, Canonical Memory, or product-repository change was made.

## Side-Effect Audit

- Producer live issue create/update/comment/mention/assignment/status: 0.
- Adapter-constructed mentions / plain-text triggers: 0. Assignments from this producer: 0.
- Live assignee/status changes: 0. Triggered runs from this producer: 0.
- Canonical writes: 0. Product-repository changes: 0. Frozen T00 amended: false. U05/U12 live Agent/Squad/skill/binding writes: 0. Merges: 0.
- The only platform surface used by U08 is the accepted read-only evidence plus the injected fixture runners; the CLI refuses to construct an implicit live runner.

## Finding Drain

Unaccounted open Findings at completion: **0**. Carried forward explicitly (recorded, none blocking the U08 path):

1. **Package identity is context-derived, not route-derived** (new, recorded as a fallback property): a freshly prepared fallback package for an unchanged context hashes to the same `package_id` as the stopped mention source; the publisher is idempotent and no duplicate note is created. This is fail-closed (no duplication, no reuse as authorization) but a literal "new package id" reading is not achievable without either fabricating task context or amending the accepted U06 pipeline — Lead should decide at the pre-U12 pin gate whether route-bound package identity is required.
2. **U07 latent pin byte-basis inconsistency** (carried from U07): the U04/U05 raw-byte pins correspond to CRLF bytes while the U06 commit stores LF blobs; U07's branch is self-consistent and U08 preserved it. Re-pin vs attribute-strategy decision belongs to the later Finding-alignment gate before U12.
3. **U12 non-`set` skill-removal capability** (unchanged enablement blocker; out of U08 scope).
4. **Closed-source execution guard**: the accepted U07 `run_execute_stage` is byte-pinned and cannot be amended, so "a closed source transaction is never resumed" is enforced by U08's reconciliation/`resume_source_route` and detected by `cross_route_audit_v2` (`closed_source_route_reactivated`), not by U07 itself; U11 joint replay should wire the live guard.

## Deviations

1. **Zero mutation inside the fallback transaction.** Section B says to reuse U06 primitives for non-trigger mutation; U08 instead binds the exact canonical issue read-only and issues no create/update at all, so no mutation can hide inside the trigger. The audit asserts `issue_create = 0` and `issue_update = 0`. This is stricter than U06's `existing_issue_id` update path (which also fails closed on assigned issues).
2. **`CROSS_ROUTE_REQUIRED` is represented by the typed closure + `cross_route_supersession` records plus the `NO_TRIGGER_PROVEN` classification**, rather than emitting a literal `CROSS_ROUTE_REQUIRED` terminal from the source route; the source route's own terminal recorded by U07 is never reinterpreted.
3. **Post-publish sources reuse an identical published note idempotently** instead of issuing a second, contradictory package; no note is deleted, edited or duplicated (see Finding 1).
4. **`ASSIGNMENT_CONFIRMATION_REQUIRED` (stricter typed equivalent)** replaces U06's `TRIGGER_CONFIRMATION_REQUIRED` on the fallback route for both failed and unconfirmable assignment responses.
5. **Recovery of a fallback crash re-reads closure/supersession/authorization from the ledger** and never re-records them; a crash produces no `transaction_result` so the durable state stays resumable.
6. **The U07 cross-route base audit is refined** for the specific `handoff_triggered_by_both_routes` check: U08 counts actual triggers (`trigger.count > 0`) instead of the mere presence of a stalled mention result, then keeps every other base finding unchanged.
7. Empty `required_artifacts` remains ARTIFACT_READY with the empty digest (inherited U06 deviation).

## Risks

- The observed `issue runs` response is an operational evidence surface, not a frozen public schema; malformed/truncated/unreadable state still fails closed (`EVIDENCE_UNTRUSTED`), and zero-run proof is conservative: any run for the target agent that is not provably pre-existing blocks the fallback.
- Platform-side create/assign atomicity is still not claimed; an uncertain assignment is never retried.
- The reconciliation is a point-in-time read; concurrent platform mutations between reconciliation and trigger are caught by re-observation (assignee) and the dual run prechecks, not by a distributed lock.
- Live 05 display name remains `05 Feature Reviewer` until U12; U08 refuses that token and never aliases it.
- A fresh package hash coincidence (Finding 1) is stable by construction; any future change to U06 package identity semantics will change fallback artifacts.

## Blockers

None for the staged fallback runtime. Live enablement stays with U12 (blocked on the non-`set` skill-removal path); U09/U11 remain parked; formal Delivery Review and QA activation are not started.

## Recommended Next Decision

Lead: accept this staged Direct Assignment fallback/recovery as the Stage 5 U08 foundation. Keep live 05/06 disabled and U12 parked. Proceed to U09 (Finding/Challenge alignment) on this branch lineage; then U11 joint replay should cover the closed-source guard, the fallback main paths, and the ambiguity matrix end-to-end. Decide the package-identity question (Finding 1) and the U07 pin byte-basis question (Finding 2) at the pre-U12 pin gate.

## Ready for Review

Yes. Review level R2 foundation runtime only; formal Delivery Review and QA activation remain parked until U11/U12. No U06/U07 runtime file was modified; no live dispatch, no second trigger mechanism, no Canonical or product write.

## Done criteria

| criterion | value |
| --- | --- |
| source_route_reconciled_from_trusted_evidence | true |
| source_transaction_closed_before_fallback | true |
| source_trigger_count | 0 |
| source_run_count | 0 |
| fallback_transaction_is_new_and_linked | true |
| route_mutation_or_package_reuse | false |
| exact_role_and_artifacts_bound | true |
| intentional_reassignment_authorized | true |
| handoff_ready_before_assignment | true |
| unexpected_run_precheck | true |
| assignment_is_only_trigger | true |
| intended_assignment_count | 1 |
| intended_run_count | 1 |
| self_check_ready_before_work | true |
| resumable_route_prefers_resume | true |
| mention_ambiguity_never_falls_back | true |
| assignment_ambiguity_never_retries_or_switches | true |
| completed_replay_idempotent | true |
| exact_u07_u06_u05_artifact_pins | true |
| u07_byte_state_preserved | true |
| feature_reviewer_resolves | false |
| old_05_package_accepted | false |
| live_mutations_or_triggers | 0 |
| frozen_t00_amended | false |
| canonical_writes | 0 |
| product_repo_changes | 0 |
| unaccounted_open_findings | 0 |
