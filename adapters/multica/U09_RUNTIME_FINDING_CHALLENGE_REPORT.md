# U09_RUNTIME_FINDING_CHALLENGE_REPORT

- tool: `tools/chandoff_finding.py` U09/2.2
- focused tests: `tools/tests/test_handoff_finding.py` (86 tests)
- branch: `yzt-77-u09-finding-challenge-alignment` based on exact U08 report commit `6f541f715aa07d45f60c7c50e855952b64bcc9af`
- status: **simulation_only** — live comment/assignment/status/run execution is not authorized and was not exercised; no U06/U07/U08 runtime file, schema, skill, instruction, Canonical Memory or product-repository file was modified

## Verdict

Ready for Review. The frozen Memory V1.1 Finding capture/lifecycle and the existing Challenge capability are aligned with the accepted V2.2 Artifact-aware handoff lineage as one deterministic, fail-closed runtime layer: capture is immediate/open and processes nothing; the four natural boundaries reuse the accepted T01 FINDING_GATE and T04B current-role integration verbatim; `CHALLENGE_CONTEXT` is targeted revalidation against exact package/context/artifact/evidence revisions; and every unresolved material exception becomes a typed Lead-addressed proposal — never a direct 02/03/04 trigger. Zero live mutations or triggered runs from this producer task; frozen T00 is not amended and all accepted upstream pins reproduce.

## Preconditions

- Isolated worktree `multica-memory-yzt-77-u09` created from exact U08 report commit `6f541f715aa07d45f60c7c50e855952b64bcc9af`; memory `main` (`95c434d`), prior U worktrees, Canonical Memory and the product repository are untouched.
- Fresh isolated checkout of exact U08 reproduced the accepted runtime digests (below) and the full suite **688/688 OK** before any U09 change.
- Reconfirmed `feature-reviewer` does not resolve and has no alias (its literal is assembled at runtime in the U09 test so the U05 old-role inventory stays byte-stable), the live 05 display name is still `05 Feature Reviewer`, and live 05/06 enablement remains off (`feature_reviewer_activation: 0`, `old_05_package_accepted: false`).
- Pre-change inventory: T01 Finding Gate (`tools/chandoff_plan.py`), T04B current-role integration (`tools/chandoff_selfcheck.py`), V1 challenge signal shape (`tools/memory_cli.py`, `schemas/external-signal.schema.json`), U04 shared skill, U05 role instruction marker set, U06–U08 shared `chandoff_dispatch.TransactionLedger`.
- The accepted U07 byte-state correction is preserved: the six CRLF `skills/multica-context-handoff/**` files are untouched and `u05_mapping()` still reproduces the pinned bundle digest.

## Accepted Inputs

| input | pin |
| --- | --- |
| Parent YZT-66 | Authority Order, Finding/Challenge boundaries, SAFE_DISPATCH, Artifact Contract, Option A, Hard Stops, Final Gate |
| U08 | `6f541f715aa07d45f60c7c50e855952b64bcc9af` (runtime `sha256:7884cfb6…`, focused tests `sha256:37de2a32…`, evidence bundle `sha256:810ecfd7…`) |
| U07 | `a8fcab19488f38d622ff731eb56eb14b39f8adb5` (mention runtime `sha256:d3bba5b4…`, focused tests `sha256:ba52f615…`) |
| U06 | `f35afdf91133c3ea21432684fa62f34f0eae12c1` (assignment runtime `sha256:2d701541…`, dispatch `sha256:62dbd081…`) |
| U05 | `b43b68b5deae6d8636f02293f2abb52ecb5f65e3` (`instruction_bundle_revision sha256:a93e146d…`, `binding_plan_revision sha256:7f83bfd5…`) |
| U04 | `702cb9bb11120c154a216e66c6ed1d3d632d3b49` (SKILL.md `sha256:f369cee4…`, bundle `sha256:7f861c32…`) |
| U10 | `73f922ea33c2e2867ba51b6843588c5aa4980ff6` (Artifact Contract `sha256:9c2857ae…`) |
| U03 Frozen-T00 | `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (not amended) |

## Historical Inventory

Full classification in `adapters/multica/finding-challenge/historical-inventory.json`: **retained 6 / adapted 3 / invalid 6 / deferred 2** (17 items; historical Finding/Challenge behavior is migration evidence only).

- Retained: REPORT_FINDING capture-only ingest; the `open`/`processed` Finding lifecycle; the T01 internal FINDING_GATE; the T04B current-role integration; the V1 challenge signal shape (`source_type: memory_challenge`); deterministic dispositions and replay idempotence.
- Adapted: the task-scoped drain now becomes an explicit TASK_COMPLETION boundary with owner/evidence/gate dispositions; package/context/artifact revision binding becomes exact; the 02 escalation becomes a Lead-addressed typed proposal that never triggers.
- Invalid: Grok raw output as project truth; ordinary Finding waking 02; producer/reviewer/QA direct 02 trigger; local Review/QA defect as an automatic Runtime Finding; free-form operator routing; a second Finding lifecycle/store.
- Deferred: U11 joint replay (including the live closed-source guard wiring); U12 live enablement (including the non-`set` skill-removal path).

## Architecture / State Machine

```text
REPORT_FINDING (capture only)
  validate task/role/summary/evidence/scope/provenance
  → classify (typed, deterministic)
  → Finding.open; process_now=false; wake_context_engineer=false; canonical_write=false

natural boundaries (no duplicate policy; T01/T04B reused verbatim)
  SELF_CHECK       current-role T04B gate  → CLEAR | REFRESH_REQUIRED | BLOCKED(+proposal)
  PREPARE_HANDOFF  target-role T01 gate    → ready_allowed, post-prepare change → REFRESH_REQUIRED
  TASK_COMPLETION  drain_task_findings     → DRAINED only with zero unaccounted open Findings
  CHALLENGE_CONTEXT targeted revalidation  → RESOLVED | REFRESH_REQUIRED | UNRESOLVED_MATERIAL
                                            | RETURNED_TO_LEAD | STOPPED_ESCALATED

unresolved material exception
  → EscalationProposal(addressed_to=engineering-lead, recommended_target, exact evidence)
  → Lead-owned decision; any later 02/03/04 dispatch is a fresh SAFE_DISPATCH transaction
```

All layer records are appended as typed entries to the accepted shared `chandoff_dispatch.TransactionLedger` (`finding_capture`, `finding_processing`, `finding_drain`, `challenge_revalidation`, `escalation_proposal`). No second evidence store exists.

## Capture Contract

`capture_finding` reuses T01 `plan.capture_finding` for the task-workspace store write and adds the U09 typed envelope:

- Required: exact `finding_id` grammar, `task_ref`, reporting role (six V2.2 slugs only; display names and retired identities fail closed), non-blank summary, ≥1 grammar-valid evidence reference, resolvable affected scope (registered, not archived; cross-project needs ≥2 explicit projects), deterministic provenance digest.
- Result: `status: open`, `ingest: immediate`, `process_now: false`, `wake_context_engineer: false`, `context_engineer_woken: false`, `canonical_write: false`, `context_engineer_run: false`, `direct_trigger: false`, plus the typed classification.
- The stored Finding document is schema-valid against the frozen `finding.schema.json`; the frozen `intent` field carries the T01-compatible semantics (`task_delivery` for local defects, `durable_candidate` for task cognition, `context_challenge` for challenges/authority conflicts, `observation` for deviations/external intelligence).
- Promotion flags (`promote_to_project_truth`, `as_authority`, `as_rule`) are refused at capture; raw external output is forced `verification: unverified`.
- Duplicate capture with identical content is idempotent (zero writes); the same id with different evidence is `duplicate_capture_conflict` and fails closed.

## Classification Matrix

9 deterministic classes, no free-form routing. Full table in `adapters/multica/finding-challenge/classification-matrix.json`.

| classification | artifact home / owner | route | runtime finding | lead-decision target |
| --- | --- | --- | --- | --- |
| `IMPLEMENTATION_DEFECT` | implementation / 04 | keep in artifact | only with reusable cognition or material context change | 04 (via Lead) |
| `DELIVERY_REVIEW_DEFECT` | delivery-review / producer | keep in artifact | only with reusable cognition or material context change | 04 (via Lead) |
| `QA_DEFECT` | qa / producer | keep in artifact | only with reusable cognition or material context change | 04 (via Lead) |
| `TASK_COGNITION` | runtime / 02 | runtime boundary processing | always | 02 (via Lead) |
| `CONTEXT_AUTHORITY_CONFLICT` | runtime / 02 | runtime boundary processing | always, material | 02 (via Lead) |
| `PRODUCT_CONTEXT_CHALLENGE` | runtime / 02 | lead decision | always, material | 02 |
| `DESIGN_CHALLENGE` | runtime / 03 | lead decision | always, material | 03 |
| `DESIGN_DEVIATION` | runtime / 03 | lead decision | always, material | 03 |
| `EXTERNAL_INTELLIGENCE` | runtime / 02 | evidence/pointer only | only with reusable cognition or material context change | never direct |

Every row has `direct_trigger: false` and `wakes_context_engineer: false`. Grok/raw external output is `authority_eligible: false`, `promotion_allowed: false`, `truth_status: evidence`.

## Natural Boundary Processing

- **SELF_CHECK** — selects only current-role relevant open Findings and calls `chandoff_selfcheck.run_current_role_finding_gate` (T04B). CLEAR → continue; Canonical-changing processing → REFRESH_REQUIRED; material Finding that cannot be safely auto-processed → BLOCKED with a Lead-addressed proposal.
- **PREPARE_HANDOFF** — selects only target-role relevant open Findings and calls T01 `finding_gate(boundary="handoff")`. A material target-context Finding that remains unprocessed refuses READY (`ready_allowed: false`); a material Finding that appears after package preparation (selection digest drift) forces REFRESH_REQUIRED (`POST_PREPARE_MATERIAL_CHANGE`). A Finding in the store is never hidden by READY.
- **TASK_COMPLETION** — `drain_task_findings` requires an explicit disposition row for every task-associated open Finding with `owner` (V2.2 role), `evidence_ref` (frozen ref grammar) and, for deferred/escalated dispositions, a known `gate`. Any unaccounted or invalid row blocks the close (`task_closed_with_open_unaccounted_finding: true`); a complete drain is idempotent (replay writes zero rows and keeps the same store digest).
- **CHALLENGE_CONTEXT** — targeted revalidation only, never a role hop. The result always carries `dispatch.trigger_emitted: false`.

Determinism: the same findings + revisions produce byte-identical envelopes; replay of a processed boundary selects nothing new and re-emits no processing writes.

## Challenge/Revalidation

- A challenge requires a typed `reason_code` (`evidence_conflict`, `authority_gap`, `context_gap`, `design_baseline_wrong`, `product_expectation_conflict`), ≥1 exact evidence reference and an exact revision binding; a missing, ambiguous or non-exact binding (`latest`/guessed version/display name) fails closed before revalidation.
- Exact target kinds: `package` (resolved by exact `package_id`), `artifact` (exact version; stale/superseded status blocks the affected review/QA path as REFRESH_REQUIRED), `context`, `evidence` (digest).
- Revision drift vs the current memory/registry/role-profile revisions → REFRESH_REQUIRED (`*_revision_changed`, frozen T00 vocabulary).
- Ordinary gaps revalidate through the accepted T04B gate: CLEAR → RESOLVED/continue without waking 02; BLOCKED → UNRESOLVED_MATERIAL with a Lead-addressed proposal.
- `design_baseline_wrong` → RETURNED_TO_LEAD (03 decision); `product_expectation_conflict` → RETURNED_TO_LEAD (02 decision); a second unresolved exchange round stops as STOPPED_ESCALATED (one evidence-based exchange, then return the tradeoff to Lead/Human).
- Every result carries a V1-compatible `external_signal` bridge (`source_type: memory_challenge`, reliability `disputed`, content hash of the exact target revision) for reuse by the existing Challenge capability.

## Escalation Policy

- Only typed unresolved material exceptions qualify (`authority_gap`, `evidence_conflict`, `critical_context_review`, `cross_repo_investigation`, `unrecoverable_unknown`); anything else is refused.
- A proposal is addressed to `engineering-lead` with the exact affected task/package/artifact/context refs, evidence refs and digest, attempted processing, a validated `recommended_target` and `requires_lead_owned_safe_dispatch: true`. It emits no trigger, assignment or status write; 02 is never woken by ordinary processing or by a resolved/refresh path.

## Artifact/Revision Binding

- `validate_binding` accepts only exact package ids (`CTX-<role>-<16 hex>`), the six role slugs, `sha256:` revisions and exact artifact versions; `latest`/`current`/free-prose versions, display names and mismatched `artifact_requirements` digests fail closed.
- Artifact requirements are normalized and digested through the accepted U10 `cartifact.dependency_digest`; an artifact store lookup that cannot resolve the exact version, or resolves a `superseded`/`stale`/`draft`/`changes_required` envelope, blocks the affected review/QA path.
- The binding is exported on every processing/challenge envelope together with the selection digest so replays are decided against exact revisions, never against `latest`.

## Ledger/Replay

- Typed records only in the shared U06–U08 `TransactionLedger`; the audit reuses `chandoff_dispatch.audit_ledger` and reports zero command records, zero mutating command classes, zero mentions/assignments/status writes and zero triggers.
- `finding_side_effect_audit` returns `ok: true` for all sample and fixture ledgers (`live_writes: 0`, `live_triggers: 0`, `user_visible_triggers: 0`, `real_assignments: 0`, `canonical_writes: 0`).
- Replay proofs: identical evidence + revisions produce byte-identical processing envelopes; a replayed boundary selects nothing and writes nothing; a replayed drain returns `replayed: true`, `writes: 0` and the same store digest.

## Exact Revisions / Digests

- U09 runtime `tools/chandoff_finding.py` `sha256:efa29010b5a0b07aa76a329c107b903a54f342e8fa88cc3e99a384a121273848` (committed-blob method: raw bytes with CRLF normalized to LF, matching the accepted U08 digest convention).
- U09 focused tests `tools/tests/test_handoff_finding.py` `sha256:12596bea74291aa5e0c0a597775a121a3ea3c7e9e7659263af79b963c4d8f0c4`.
- U09 authored evidence bundle (16 files under `adapters/multica/finding-challenge/`) `sha256:d598d2c5db96f3e16eed23627d1a9b8d007412fc21f29a5360f7addc81b005eb`. Method: canonical JSON of sorted `file name → sha256(bytes)`.
- U08 runtime `sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b`; U08 tests `sha256:37de2a32423ecd7222309d92946ef99ee24a223eedaad63ac91f38cfa6bc71b3` (both reproduce byte-for-byte).
- U07 runtime/tests, U06 runtime/dispatch, U05 `a93e146d…`/`7f83bfd5…`, U04 `f369cee4…`/`7f861c32…`, U10 `9c2857ae…`, U03 `2e1959b8…` — all reproduce unchanged via `u05_mapping()` and the digest checks.
- Memory `sha256:30b51dea…`; Registry `sha256:a08e20eb…`; Role Profile `sha256:7b3bdf52…` (all unchanged).
- Gate results refreshed at this HEAD: `migration/gate-results/gate-a.json` (valid), `gate-b.json` (passed 10/10), `gate-c-replay.json` (passed 6/6).

## Changes

- Added `tools/chandoff_finding.py` (U09/2.2): typed capture + classification; boundary adapters reusing T01/T04B verbatim; task drain; exact-binding validator over U10 `cartifact`; challenge revalidation with V1 signal bridge; Lead-addressed escalation proposal; shared-ledger typed records + side-effect audit; U08 disposition carry; deterministic matrices; simulation-only evidence CLI.
- Added `tools/tests/test_handoff_finding.py` (86 focused tests).
- Added the evidence bundle under `adapters/multica/finding-challenge/` and the `.gitattributes` byte-stability entry.
- Refreshed `migration/gate-results/*.json` at this HEAD (generated_at only).
- No T00 schema, T01/T04B, U04/U05/U06/U07/U08 runtime, Canonical Memory or product-repository file was modified.

## Tests

- Focused U09: **86/86 OK**.
- Focused predecessor suites (U08/U07/U06/U04/U03 revalidation/U05): **290/290 OK**.
- Full `tools/tests` (`unittest discover`): **774/774 OK** (688 baseline on exact U08 + 86 new).
- T00/framework boundary scan (`tools/chandoff.py scan`): `{"clean": true, "violations": {}}`; the new module is additionally scanned with the same
  `chandoff.forbidden_concept_scan` vocabulary.
- Gates at this HEAD: A valid (`all_objects_schema_valid`, silent_drop 0, invalid authority 0); B passed 10/10; C passed 6/6 replays.
- Adversarial coverage: capture immediate/no-process/no-wake/no-Canonical and idempotent/conflicting duplicates; local-vs-cognition classification; every role including retired identities; Review/QA separation; stale revisions; hidden material Finding refused by READY; ordinary-vs-material Context gaps; Product/Design/Design-Deviation/Implementation/Context routing with zero direct triggers; Grok evidence-only with refused promotion; task drain and replay; unauthorized routing; exact bundle regeneration.
- Done criteria: all `capture`/`classification`/`boundaries`/`routing`/`safety` flags in `acceptance-evidence.json` hold (`frozen_t00_amended: false`, upstream pins preserved, live mutations/triggers 0, canonical writes 0, product-repo changes 0, task closed with open unaccounted Finding 0, unaccounted open Findings 0).

## Side-Effect Audit

- Producer live issue create/update/comment/mention/assignment/status: 0 (no live surface is reachable from this module; it imports no process/network library).
- Adapter-constructed triggers / assignments / status writes: 0. Triggered runs from this producer: 0.
- Canonical writes: 0. Product-repository changes: 0. Frozen T00 amended: false. U11 joint replay: not performed. U12 enablement: not performed. Merges: 0.
- The only platform surface used by U09 is the read-only runtime store plus the injected fixture ledger; all sample artifacts are deterministic evidence, contain no secrets and no executable trigger content.

## Finding Drain

Unaccounted open Findings at completion: **0**. All four U08 recorded findings are carried with explicit typed dispositions (below); no new blocking Finding was produced by U09 itself.

## U08 Finding Dispositions

Full record in `adapters/multica/finding-challenge/u08-finding-dispositions.json`; all four rows classify, gate and defer without redesign (`redesign_performed: false`) and drain cleanly:

| finding | classification | disposition | gate | owner |
| --- | --- | --- | --- | --- |
| context-derived package-id coincidence | `CONTEXT_AUTHORITY_CONFLICT` | `DEFERRED_GATE` | `PRE_U12_PIN_GATE` | 01 Lead |
| U07 raw-byte pin basis | `CONTEXT_AUTHORITY_CONFLICT` | `DEFERRED_GATE` | `PRE_U12_PIN_GATE` | 01 Lead |
| U12 non-`set` skill-removal capability | `TASK_COGNITION` | `DEFERRED_GATE` | `U12_ENABLEMENT` | 01 Lead |
| U11 closed-source execution-guard wiring | `TASK_COGNITION` | `DEFERRED_GATE` | `U11_JOINT_REPLAY` | 01 Lead |

## Deviations

1. The U09 layer is a framework-neutral runtime module (no Multica runtime nouns); the only framework-adjacent dependency is the accepted shared `chandoff_dispatch.TransactionLedger`, used as the evidence lineage extension surface.
2. `CHALLENGE_CONTEXT` maps revision drift to REFRESH_REQUIRED rather than raising at binding validation time (structural validation still fails closed first); this keeps the challenge outcome frozen-vocabulary-compatible.
3. The V1 challenge capability is reused through a deterministic `external_signal` bridge rather than re-emitting the V1 CLI; no `memory/` unit is touched and no Canonical write exists.
4. Drain dispositions are U09-typed (`RESOLVED_EXISTING`, `DEFERRED_GATE`, `ESCALATED_PROPOSAL`, `POINTER`, `FORGET`) and map onto the frozen Finding disposition enum; the frozen lifecycle (`open`/`processed`) is unchanged.
5. The U09 test file assembles retired-role literals at runtime so the U05 old-role inventory artifact stays byte-identical (same convention as the accepted U06–U08 tests).

## Risks

- Materiality for non-U09-captured Findings continues to come from the frozen T01 gate; a future change to T01 materiality semantics would change U09 boundary outcomes but cannot silently weaken them (the gate is imported, not copied).
- The escalation vocabulary is typed but the "hard recoverable unknown" classification remains a human/Lead judgment expressed through the proposal payload; U09 only guarantees the container and its zero-trigger property.
- Evidence paths under `adapters/multica/finding-challenge/` are authored artifacts; regeneration is asserted byte-for-byte in the focused tests, so any manual edit will be detected.
- U11/U12 remain parked; the non-`set` skill-removal capability and the live closed-source guard wiring are unchanged blockers for enablement.

## Blockers

None for the staged runtime alignment. Live 05/06 enablement stays with U12; U11 joint replay and the four deferred gates above remain the next decision points.

## Recommended Next Decision

Lead: accept this staged Finding/Challenge alignment as the Stage 5 U09 foundation. Keep live 05/06 disabled and U12 parked. Proceed to U11 joint replay over this lineage, wiring the closed-source guard and exercising capture → boundary → drain end-to-end; decide the package-identity and U07 byte-basis items at the pre-U12 pin gate.

## Ready for Review

Yes. Review level R2 foundation runtime only; formal Delivery Review and QA activation remain parked until U11/U12. No live dispatch, no second trigger mechanism, no Canonical or product write.

## Done criteria

```yaml
capture:
  report_finding_is_capture_only: true
  process_now_default: false
  wake_context_engineer_default: false
  canonical_write_default: false
classification:
  review_qa_local_defect_is_not_automatic_runtime_finding: true
  reusable_cognition_is_runtime_finding: true
  grok_raw_truth_to_project: false
boundaries:
  self_check_current_role_gate: true
  prepare_handoff_target_role_gate: true
  task_completion_finding_drain: true
  challenge_context_targeted_revalidation: true
  handoff_ready_hides_material_finding: false
routing:
  ordinary_finding_wakes_02: false
  direct_nonlead_02_03_04_trigger: false
  unresolved_material_exception_requires_lead: true
  product_design_implementation_routes_classified: true
safety:
  frozen_t00_amended: false
  upstream_u08_u07_u06_pins_preserved: true
  live_mutations_or_triggers: 0
  canonical_writes: 0
  product_repo_changes: 0
  task_closed_with_open_unaccounted_finding: 0
  unaccounted_open_findings: 0
```

## Stop conditions re-check

No stop condition was reached: the frozen T00 contract expresses every required semantic (prepare_handoff/self_check statuses and the internal Finding Gate), the existing Finding lifecycle and the V2.2 authorities are aligned rather than conflicting, classification is deterministic without a new authority, no material Finding can be hidden by READY/close, ordinary processing never wakes 02, escalation never requires a non-Lead trigger, exact Artifact/context/package revisions bind, all accepted pins and byte states reproduce, and no Canonical/product write or live trigger was needed.
