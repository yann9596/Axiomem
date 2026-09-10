# U07_MENTION_HANDOFF_REPORT

- tool: `tools/chandoff_mention.py` U07/2.2
- branch: `yzt-75-u07-mention-handoff` based on U06 `f35afdf91133c3ea21432684fa62f34f0eae12c1` (exact commit recorded at handoff)
- status: **staged_simulation_only** — live comment/mention/assignment/status/run execution is not authorized and was not exercised
- provenance: resumed from the preserved partial worktree after the prior run failed terminally with `agent_error.provider_quota_limit`; no work was discarded or restarted, and no dispatch was duplicated

## Verdict

Ready for Review. The historical T10 mention path is rebased onto the accepted U06 V2.2 identity, Artifact gate, durable ledger, unexpected-run precheck, run-correlation, and crash-recovery contract. The adapter authorizes and validates exactly one native mention but never constructs mention Markdown, never posts the triggering comment, never assigns, and never retries an ambiguous mention. Zero live mutations or triggered runs from this producer task.

## Preconditions

- Isolated worktree `multica-memory-yzt-75-u07` from exact U06 commit `f35afdf91133c3ea21432684fa62f34f0eae12c1`. Memory `main` (`95c434d`) is clean and untouched; prior U worktrees are untouched; Canonical Memory and the product repository were not modified.
- U05/U04/U10 pins reconfirmed from the staged bundles: instruction bundle `sha256:a93e146d…`, binding plan `sha256:7f83bfd5…`, U04 SKILL.md `sha256:f369cee4…`, U04 skill bundle `sha256:7f861c32…`, Artifact Contract `sha256:9c2857ae…`. Memory/Registry/Role-Profile revisions unchanged (`30b51dea` / `a08e20eb` / `7b3bdf52`).
- `feature-reviewer` resolves to nothing and never aliases; the stable 05 UUID maps only to staged `delivery-reviewer`; the live display token `05 Feature Reviewer` is refused without alias.
- Read-only Multica capabilities captured in `adapters/multica/mention-handoff/capabilities.json` (CLI observed v0.4.42). Run evidence reuses the U06-observed `issue runs` array-of-objects contract (`id`, `issue_id`, `agent_id`, `status`); truncation or an unreadable listing is treated as `RUN_STATE_UNDETERMINED`.

## Accepted Inputs

| input | pin |
| --- | --- |
| Parent YZT-66 | Authority Order, SAFE_DISPATCH, Option A, corrected stage order, Artifact Contract, stage-wake idempotence, Hard Stops |
| U06 | `f35afdf91133c3ea21432684fa62f34f0eae12c1` (assignment runtime `sha256:2d701541…`, dispatch runtime `sha256:62dbd081…`) |
| U05 | `b43b68b5deae6d8636f02293f2abb52ecb5f65e3` (`instruction_bundle_revision sha256:a93e146d…`, `binding_plan_revision sha256:7f83bfd5…`) |
| U04 | `702cb9bb11120c154a216e66c6ed1d3d632d3b49` (SKILL.md `sha256:f369cee4…`, bundle `sha256:7f861c32…`) |
| U10 | `73f922ea33c2e2867ba51b6843588c5aa4980ff6` (Artifact Contract `sha256:9c2857ae…`) |
| U03 Frozen-T00 | `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (not amended) |

## Historical Inventory

Full classification in `adapters/multica/mention-handoff/historical-inventory.json`: **12 retained / 6 adapted / 2 invalid / 5 deferred**.

- Retained: existing-issue reuse; pre/post assignee observation with fail-closed drift; adapter never constructs a mention or posts the trigger; one non-trigger `/note` then `MENTION_READY` then exactly one native mention; native-receipt provenance; plain-text/fabricated-link fail-closed; Assignment XOR Mention; `MENTION_CONFIRMATION_REQUIRED` for ambiguity with no retry; idempotent completed replay and refused incomplete replay; injected compose (no model in the orchestrator); simulation default; PARTIAL default-stop.
- Adapted to U06/U05/U04/U10: identity via `u05_mapping` (not T08); 02 is exception-only instead of never-a-target; observed `issue runs` listing is the correlation authority (injected T10 evidence is provenance-only); U04 Artifact gate + U10 exact-version bind with freshness recheck; dual unexpected-run precheck; bounded crash continuation for pre-publish and post-publish-pre-mention.
- Invalid: live display-name role lookup including `05 Feature Reviewer`; T08 as current 05 identity authority.
- Deferred: U08 cross-route fallback; U09 Finding/Challenge alignment; U11 joint replay; U12 live enablement and the non-`set` skill-removal path.

## Architecture / State Machine

```text
INIT
→ ISSUE_BOUND                (existing canonical issue read; assignee snapshotted)
→ TARGET_RESOLVED            (U05 role/UUID + exact artifacts + tx bind)
→ ROUTE_FROZEN               (one route per tx/package/handoff)
→ RUN_PRECHECK_PREPARE       (issue runs --active --siblings)
→ HANDOFF_PREPARED           (T05→T01→injected compose→T02/T03)
→ ARTIFACT_READY             (U04 apply_finalize_gate / U10 ready-check)
→ HANDOFF_PUBLISHED          (exactly one non-trigger /note)
→ HANDOFF_READY_CONFIRMED    (re-resolve package/role/task/comment)
→ RUN_PRECHECK_MENTION       (freshness + zero unexpected run)
→ MENTION_READY              (envelope authorizes one native mention; zero trigger)
→ MENTION_EVIDENCE_ACCEPTED  (validated native receipt)
→ TARGET_RUN_CORRELATED      (exactly one new intended run from issue-runs)
→ TARGET_SELF_CHECKED        (READY before consequential work; one bounded refresh)
→ COMPLETED
```

Phase A (`ready`) ends at `MENTION_READY` with zero trigger. Phase B (`execute`) runs only after the current agent emits the native mention on its own reply surface. Every Multica argv flows through the shared U06 `DispatchCli` / `NoteCli` / `AdapterCli` allowlists and one `TransactionLedger`; no route-specific parallel ledger exists.

## Role Policy

- Only the six U05 logical roles resolve (`u05_mapping`); staged display names only; unknown or retired roles → `ROUTING_REQUIRED`.
- 02 is exception-only: ordinary-path mention rejected; explicit `unresolved_material_exception` authorizes without ownership transfer.
- 05 requires Lead caller + `lead_owned_review_routing` + exact gate artifacts (`implementation`, `design_baseline`, `issue_definition`). 06 requires Lead caller + `lead_owned_qa_routing` + exact gate artifacts (`product_expectation`, `design_baseline`, `implementation`, `delivery_review`). Producer→05, 05→06, and producer→06 automatic routing are rejected; `latest`/placeholder versions are forbidden.
- `feature-reviewer` never resolves/READYs/mentions/rewrites; the live `05 Feature Reviewer` token is refused without alias.
- Stage-wake XOR Lead mention: when platform stage completion already wakes (or will wake) Lead, an explicit Lead mention stops as `STAGE_WAKE_DUPLICATE` with zero trigger; a structured Lead mention remains possible only when no stage wake applies and exact READY evidence exists.

## Artifact Gate

- Declared `required_artifacts` run `cartifact.artifact_ready_check` before PREPARE and U04 `apply_finalize_gate` after T03; the exact dependency digest is recorded in the ledger (`artifact_gate` / `artifact_freshness`).
- Freshness is re-evaluated after publication and before `MENTION_READY`. Missing, ambiguous, wrong-consumer, stale, superseded, or changed dependencies stop as `PACKAGE_STALE` / `REFRESH_REQUIRED` with zero trigger.
- An empty declared set records the empty digest and is ARTIFACT_READY so T00-only drills stay exercisable (inherited U06 deviation); main-path-with-artifacts is separately proven by the R1/R2 cases.

## Native Mention Evidence

- `MENTION_READY` is a bounded envelope: kind/schema/transaction, issue id+identifier, caller role+agent, exact target role/name/UUID, package id+status, published comment id+timestamp, `trigger.authorized_mentions = 1`, and constraints including `adapter_constructs_mention_markdown: false` and `second_mention_never_authorized: true`. It contains no mention Markdown.
- Receipt validation accepts only `native_mention_evidence` (U07 schema; T10 schema tolerated as historical provenance) proving: exact transaction/issue, author = expected current agent, `author_surface = native_agent_reply`, a comment id distinct from the published note, `created_at` strictly after publication, exactly one mention entry naming the exact target agent+role with the exact `mention://agent/<target-uuid>` link, no `plain_text_handles` alongside, and no `assignment_mutation`.
- Fail-closed outcomes: plain text, fabricated/untrusted receipt, wrong author/issue/target, zero/multiple mentions, adapter surface, note-as-mention, mutation → `MENTION_EVIDENCE_REJECTED`. Ambiguity (missing `created_at`, handles alongside the link) → `MENTION_CONFIRMATION_REQUIRED`, never retried. A duplicate receipt for the same transaction is rejected; a second mention is never authorized.

## Assignee Stability

- The existing issue is read (never created) and the assignee snapshotted before any work; every finish path re-observes the assignee read-only and reports `assignee.before/after/unchanged`.
- Drift → typed `ASSIGNMENT_MUTATION_DETECTED` stop on success and failure paths alike. `issue assign` argv is refused (`ROUTE_CONFLICT`), and the ledger audit reports zero assignment triggers and zero issue creates.

## Run Precheck / Correlation

- `issue runs --active --siblings --output json` before publish and again before `MENTION_READY`. Any active/queued/dispatched/running/waiting run, truncated listing, non-list JSON, or missing required fields stops as `UNEXPECTED_RUN` / `RUN_STATE_UNDETERMINED` with zero trigger.
- After the accepted receipt, the observed issue-runs listing is correlated through U06 `correlate_intended_run` against pre-mention known ids: exactly one new run for the exact issue + target agent is required. Zero / duplicate / wrong-target / pre-existing / uncorrelated runs → `RUN_CORRELATION_FAILED`. Injected evidence is a provenance cross-check only and cannot override the listing.
- Target `SELF_CHECK` must be READY before consequential work; a single bounded same-task refresh may republish only when `built_from` changed (matching fresh note reuse otherwise); a second unresolved refresh or BLOCKED stops.

## Route / Stage Idempotence

- Route freeze + `route_conflicts` + whole-ledger `cross_route_audit` (shared U06 ledger): the same transaction/package/handoff can never carry both Assignment and Mention; a package/handoff already bound to the assignment route refuses a mention and vice versa; no replay can switch routes silently.
- Stage completion and explicit Lead mention are mutually exclusive and deduplicated by a deterministic no-trigger terminal decision (`STAGE_WAKE_DUPLICATE`).
- Completed replay emits zero note/MENTION_READY/mention/assignment/run. `execute` without a recorded staged READY is refused; a recorded incomplete execute is `REPLAY_REFUSED`; a possibly-issued mention is never retried.
- Cross-route fallback (direct assignment after mention uncertainty) is not implemented; U07 emits the bounded `CROSS_ROUTE_REQUIRED` recommendation only (U08).

## Retry / Recovery Matrix

| boundary | unique next action |
| --- | --- |
| completed | replay; zero new note/mention/assignment/run |
| pre-publish (issue bound) | continue from the bound issue; prepare/publish/READY |
| post-publish, mention not issued | reuse the published note iff comment/package/role/task still match and remain fresh; await the native mention; never republish because a local response was lost |
| post-mention/pre-confirmation | `MENTION_CONFIRMATION_REQUIRED`; reconcile read-only; never retry |
| post-confirmation/pre-run-correlation | `RUN_CORRELATION_FAILED` with read-only continuation guidance; a second mention is never recommended |
| recorded incomplete | `REPLAY_REFUSED` |
| cross-route needed | `CROSS_ROUTE_REQUIRED` recommendation only (U08) |

Representative evidence: `sample-main-path-*`, `sample-stale-artifact-*`, `sample-unexpected-run-*`, `sample-plain-text-*`, `sample-stage-wake-*`, `sample-recovery-pre-publish-*`, `sample-recovery-post-publish-*`, `sample-recovery-post-mention-*`, plus `success-failure-matrix.json` (33 cases).

## Exact Revisions / Digests

- U06 base: `f35afdf91133c3ea21432684fa62f34f0eae12c1`; U06 assignment runtime `sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129`; U06 dispatch runtime `sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa` (both unchanged).
- U05 commit `b43b68b5deae6d8636f02293f2abb52ecb5f65e3`; `instruction_bundle_revision sha256:a93e146d6586cf6f474148aeed092032e3a9c871ad3138ee1152d28108b5b98f`; `binding_plan_revision sha256:7f83bfd523e2c0da3cd0dac4568869f3c9937e2d3ae6c0dc66b9853094a27c22`.
- U04 `702cb9bb11120c154a216e66c6ed1d3d632d3b49`; SKILL.md `sha256:f369cee40ada061364d91ef48a9cb7d467039931e922c1db18d85fe1d7e0638a`; skill bundle `sha256:7f861c320c115b328fb45db7356573ae449a5e443ac08b3572a764c79934fce9`.
- U10 `73f922ea33c2e2867ba51b6843588c5aa4980ff6`; Artifact Contract `sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc`.
- U03 Frozen-T00 `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (unchanged).
- Memory `sha256:30b51dea6d6f2a09b3ec25d283d207f8705d4198206664e33049ef6285138561`; Registry `sha256:a08e20ebea57bf830c605e8c9cc87950bc3781350d1d0c22c01e83e994684edb`; Role Profile `sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f` (unchanged).
- U07 runtime `tools/chandoff_mention.py` `sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1` (committed blob).
- U07 focused tests `tools/tests/test_handoff_mention.py` `sha256:ba52f6153de7d7fbad55c625ed4fb17e53c840fbec090c2bb1d4123e743e2a72` (committed blob).
- U07 authored evidence bundle (19 files under `adapters/multica/mention-handoff/`, excluding the two historical T10 `sample-mention-path-*` blobs): `sha256:119236895a51722db04862f7e7133f6dc592a164e1e59fa23da8275adf9802de`. Method: canonical JSON of sorted `file name → sha256(bytes)`.
- Gate results refreshed at this HEAD: `migration/gate-results/gate-a.json`, `gate-b.json`, `gate-c-replay.json`.

## Changes

- Rebased `tools/chandoff_mention.py` to U07/2.2: U05 identity resolution, V2.2 role policy, U04/U10 artifact bind + freshness, dual run precheck, observed issue-runs correlation, native-receipt validation, assignee stability, cross-route audit extension, bounded crash recovery.
- Rewrote `tools/tests/test_handoff_mention.py` for U07/2.2 (92 focused tests; 2 added this run for the remaining crash-boundary classifications).
- Added evidence package under `adapters/multica/mention-handoff/`: `capabilities.json`, `historical-inventory.json`, `success-failure-matrix.json`, and 16 representative ledger/result samples.
- Extended `.gitattributes` with `adapters/multica/mention-handoff/** -text` for byte-stable evidence.
- Regenerated `adapters/multica/agent-instructions/old-role-inventory.json` and the `bundle.json` files-map entry after the test-file content change (counts: `active_to_migrate` 28, `historical_only` 41, `negative_fixture` 27; U05 `instruction_bundle_revision`/`binding_plan_revision` unchanged).
- Refreshed `migration/gate-results/*.json` at this HEAD.
- Restored the exact byte state (CRLF) of the six `skills/multica-context-handoff/**` files required by the accepted U04 raw-byte bundle pin — see Deviations.

## Tests / Replays

- Focused U07 mention: **92/92 OK**.
- Focused U06 assignment compatibility: **58/58 OK**.
- Focused U04 artifact readiness: **26/26 OK**; focused U05 instructions: **27/27 OK**.
- Full `tools/tests` (`unittest discover`): **615/615 OK**.
- T00/framework boundary scan (`tools/chandoff.py scan`): `{"clean": true, "violations": {}}`; frozen boundary scan and no-drive-letter tests pass inside the suite.
- Gates: A valid (`all_objects_schema_valid`, silent_drop 0, invalid authority 0); B passed 10/10 checks; C passed 6/6 replays.
- Replay proofs: completed replay idempotent (zero new commands); incomplete replay refused; pre-publish and post-publish recovery continue uniquely; possibly-issued mention never retried; all four crash boundaries classified deterministically.
- Main path proof: existing issue reused, assignee unchanged, one non-trigger note and READY precede one native mention, one correlated intended run and SELF_CHECK READY precede work; zero Assignment/create/status triggers; no adapter-built mention Markdown.

## Side-Effect Audit

- Producer live issue create/update/comment/mention/assignment/status: 0.
- Adapter-constructed mentions / plain-text triggers: 0.
- Assignment commands: 0. Assignee changes: 0.
- Triggered runs from this producer: 0 (the only platform probes are the read-only `issue get` / `issue runs` shape observation and `--help`, recorded in `capabilities.json`; no live mutation was performed).
- Canonical writes: 0. Product repository changes: 0. Frozen T00 amended: false. U05/U12 live Agent/Squad/skill/binding writes: 0. Merges: 0.

## Finding Drain

Unaccounted open Findings at completion: 0 (the U05 audit's `unaccounted_open_findings_at_completion` = 0 and no invalid active old-role semantics remain; enforced by the passing audit tests). One latent contract inconsistency found this run is recorded under Risks as a Finding candidate rather than written, since Canonical/Finding writes are outside the Software Engineer's ownership.

## Deviations

1. **Six U04 skill-bundle files restored to CRLF byte state.** The accepted U04 bundle pin `sha256:7f861c32…` is computed over raw file bytes (`digest_skill_dir` hashes each file's bytes). At U06 `f35afdf` the committed blobs for these files are LF, so a checkout yields LF and `u05_mapping` fails closed (`found sha256:e207f7a9…`, 84 test failures). With the CRLF bytes the pin reproduces exactly (`sha256:7f861c32…`) and the full suite passes. The semantic diff is empty (`git diff --ignore-cr-at-eol` clean); no pin or revision changed; this makes the branch self-consistent on a fresh checkout.
2. Two crash-boundary tests added beyond the preserved 90 (`post_confirmation_pre_run_correlation`, `post_mention_pre_confirmation`) so every classified boundary has direct evidence; focused U07 is now 92.
3. `old-role-inventory.json` regenerated with `historical_only` 41 (was 42) because the rewrite removed one stale `"feature-reviewer": …` key in the test caller/target maps; counts otherwise unchanged, U05 revisions unaffected.
4. Empty `required_artifacts` remains ARTIFACT_READY with the empty digest (inherited U06 deviation) so T00-only drills and existing cases remain exercisable.
5. Cross-route fallback remains a recommendation only; no Assignment fallback is performed after mention uncertainty (U08).
6. `.gitattributes` `-text` for mention evidence keeps the samples byte-stable for the digests above.

## Risks

- **Latent pin byte-basis inconsistency (Finding candidate):** the accepted U04/U05 raw-byte pins correspond to CRLF bytes while the U06 commit stores LF blobs; a fresh checkout of U06 alone fails `u05_mapping`. U07's branch is self-consistent, but the underlying contract should be re-pinned or the attribute strategy revisited by Lead before U12. No change was made beyond the U07 branch.
- The observed `issue runs` response is an operational evidence surface, not a frozen public schema; malformed/truncated/uncorrelatable state still fails closed.
- Cross-command platform atomicity is not claimed; durable reconciliation is required, and a possibly-issued mention is never retried.
- Live 05 display name remains `05 Feature Reviewer` until U12; U07 refuses that token and never aliases it.
- Sampling/native-mention provenance still relies on the bounded injected receipt contract plus issue-runs correlation; U12 owns live observation.
- The prior run's provider-quota failure means this completion ran on a different runtime; no evidence was lost, but the staged samples were produced by the preserved run's tooling before the final commit and were not regenerated byte-for-byte this run (the runtime and tests were re-verified unchanged in this commit).

## Blockers

None for the staged Mention runtime. Live enablement stays U12 and remains blocked on the non-`set` skill-removal path. U08/U09/U11 stay parked. Live 05/06, skill imports, and Agent/Squad writes remain disabled.

## Recommended Next Decision

Lead: accept this staged Mention SAFE_DISPATCH as the Stage 5 mention-route foundation. Keep live 05/06 disabled and U12 parked. Proceed to U08 (Direct Assignment fallback/recovery) on this branch lineage, reusing the shared ledger, cross-route audit, and run-correlation contract. Consider recording the pin byte-basis inconsistency as a Finding and deciding (re-pin vs attribute strategy) before U12.

## Ready for Review

Yes. Review level R2 foundation runtime only; formal Delivery Review and QA activation remain parked until U11/U12. Resumed from the preserved partial worktree; no work was discarded, no route was duplicated, and no second mention was created.

## Done criteria

| criterion | value |
| --- | --- |
| existing_issue_reused | true |
| exact_role_and_artifacts_bound | true |
| handoff_ready_before_mention | true |
| unexpected_run_precheck | true |
| mention_is_only_trigger | true |
| intended_mention_count | 1 |
| intended_run_count | 1 |
| assignment_or_status_used | false |
| assignee_unchanged | true |
| self_check_ready_before_work | true |
| adapter_constructs_mention_markdown | false |
| native_receipt_exact | true |
| assignment_plus_mention_rejected | true |
| stage_wake_plus_lead_mention_rejected | true |
| ambiguous_mention_never_retried | true |
| completed_replay_idempotent | true |
| feature_reviewer_resolves | false |
| old_05_package_accepted | false |
| exact_u06_u05_artifact_pins | true |
| live_mutations_or_triggers | 0 |
| frozen_t00_amended | false |
| canonical_writes | 0 |
| product_repo_changes | 0 |
| unaccounted_open_findings | 0 |
