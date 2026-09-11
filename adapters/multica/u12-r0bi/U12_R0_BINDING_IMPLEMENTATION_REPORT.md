# U12-R0BI — Forward Lifecycle Binding Adapter Implementation Report

Task: YZT-84 (parent YZT-66) · Owner: 04 Software Engineer · Branch:
`yzt-84-u12-r0b-forward-adapter` · Base: `fcf63534e74b70220421c7b83c5447242d9a124b`
· Design contract: `U12_R0_BINDING_DECISION.md` attachment
`01a08f10-fc8c-778c-a150-2ed54825515a`, SHA256
`sha256:9d248af4bfbac31d138f2713d8ce48294831c84db937ed6ac10613c88987e3b5`
(verified from the Multica CLI before implementation).

Verdict: **IMPLEMENTATION_READY_FOR_LEAD_REVIEW**. Live R0 readiness:
**BLOCKED / LEAD_DECISION_REQUIRED** — this artifact proves a fixture-positive
isolated lifecycle and the deployed CLI evidence shape. It does not prove live
publication-provenance sufficiency, and no live create/assign/publication/rerun
was performed.

## What was added (and what was not touched)

Added, all new files:

| Path | LF SHA256 |
|---|---|
| `tools/u12_r0_binding.py` | `sha256:3773d19fba7814bc0802a32d8e661591bc54ea457237b1e7815132174ef55ceb` |
| `tools/tests/test_u12_r0_binding.py` | `sha256:f663be107b28ba4cd7d6b065fcc020460cf9046159a9475662bd66c0dcf06ddb` |
| `adapters/multica/u12-r0bi/implementation-manifest.json` | evidence |
| `adapters/multica/u12-r0bi/lifecycle-evidence.json` | evidence |
| `adapters/multica/u12-r0bi/U12_R0_BINDING_IMPLEMENTATION_REPORT.md` | this report |
| `adapters/multica/u12-r0bi/OPERATOR_INSTRUCTIONS.md` | executable operator path |

Byte-unchanged (verified by digest and `git diff` vs the base commit): the
strict gate `tools/u12_strict_receipt.py`
(`sha256:f37ed0912ecbdc3944529cffc6d7a28bc9a41d1faba8cd1e284eab6b0ccf4bed`,
U12-P0R/1.2), `tools/chandoff_intent.py`
(`sha256:0544046fa2ca97c12e6c48de574074df9de5715a950ad75a783cf31853037032`),
T06 `tools/chandoff_note.py`, the T05/T06/T01–T04 modules, all frozen T00
schemas, the U12-P0/P0R plans/manifests/reports, and Canonical memory. No
second store, service, scheduler, queue, O3 or merge path was added. The
adapter reuses the same `DurableIntentStore`, the same
`O2-dispatch-intent/1.0` records and the same state vocabulary; all new data is
namespaced under `r0_binding` with `contract_version = U12-R0B/1.0`.

## Design traceability

| YZT-83 element | Implementation |
|---|---|
| Creation context C (real dispatcher task, `engineering-lead`, frozen READY + SELF_CHECK) | `record_creation_intent` → `r0b_creation_context` → `validate_context` (frozen envelope validation + fingerprint recomputation + READY/USE_EXISTING) |
| Approved creation specification + artifact dependency digest + authority pins | `validate_creation_spec` (marker embedded in body, exact LF body digest, authority refs + digests, artifact digest reproduces from the entry map) |
| Intent-before-create; C as initial `package_id`, retained immutably | `record_creation_intent` records C + `creation_spec` + `artifact_dependency` in the intent before any target call |
| Exact unassigned backlog creation; one new create form | `R0BBoundary.create_backlog_issue` (literal `--status backlog` only; no assignee/mention; refusal list keeps every other forbidden form) |
| Disruption-safe create; marker discovery | `create_target_once` + `_recover_create` (children discovery, marker+title+parent+zero-run match, exactly-one binding) |
| Durable no-start ownership attempt | `assign_ownership_once` (fsynced `r0b_ownership_issuing` under lease before the call; read-only prove/recover; never repeats an uncertain assign) |
| Execution context E for the real returned target | `r0b_execution_context` + `bind_execution_package` (E task_ref == real identifier, role/profile/artifact match, target binding drift checks, one CAS `HANDOFF_PREPARED` transition carrying E and the actual post-setup revision/status/assignee) |
| Intent-before-publication; no retry after uncertainty | `publish_handoff_once` (renders the exact body first; appends and fsyncs `r0b_publication_issuing` with the full before-evidence boundary under the lease; T06 once; failure keeps `HANDOFF_PREPARED` and only allows read-only confirmation) |
| Publication predicate (6 conditions) | `evaluate_publication_predicate` + `_confirm_locked` (single attempt; exact note identity/author/source run/thread/original revision/bytes/canonical E; full before/after issue projection; complete comment inventory and activity-set delta; zero runs; stable re-read; artifact + all `built_from` revisions recomputed; SELF_CHECK re-run) |
| Atomic post-publication revision binding | The exact observed revision, status and assignee are written in the same `HANDOFF_PREPARED → HANDOFF_PUBLISHED` CAS transition as the proof; no arithmetic, no later patch |
| Strict-only rerun factory | `R0BForwardFactory` subclasses `strict.CanaryOrchestrator`; `R0BBoundary` does not override `rerun_issue`; `Trigger` revalidates and delegates to the strict path; assignment/mention/status triggers are refused |
| Recovery matrix | `recover` classifies every window read-only; only the pre-attempt (`allow_create`) and the no-attempt ownership case are actionable; publication confirmation reuses the durable before-evidence and never republishes |
| No downgrade / no manual field patches | `validate_intent_record` on every entry (contract version + adapter-byte pin); `mark_prepared`/`mark_published` refuse tagged intents; no direct store-field edits anywhere |

## Entrypoints and invocation inputs

Factory: `u12_r0_binding.build_r0b_factory(store, runner=..., note_runner=None,
executable="multica", clock=None, workdir=None, ttl_seconds=300,
artifact_blob_reader=None, artifact_root=None)` — an explicit runner is
mandatory; there is no implicit live runner. Public operations, in order:

```text
record_creation_intent(creation_context=..., creation_spec=..., authority=...,
                       actor=..., source_run=None, intent_id=None)
create_target_once(intent_id, actor=...)
assign_ownership_once(intent_id, actor=...)
bind_execution_package(intent_id, execution_context=..., actor=...)
publish_handoff_once(intent_id, actor=..., publisher_run_id=...,
                     prepared_by=..., prepared_at=..., parent_comment_id=None)
confirm_publication_and_bind(intent_id, actor=...)   # recovery only
arm(intent_id, actor=...)                            # strict-only rerun gate
trigger(intent_id, actor=...)                        # strict receipt only
recover(intent_id, actor=..., allow_create=False)    # read-only classification
validate(intent_id)                                  # contract/adapter-pin check
probe_evidence_capability(issue_id)                  # read-only CLI coverage
build_context_package(request)                       # real T01→T04 package
```

Read-only CLI: `python tools/u12_r0_binding.py self-check`,
`artifact-digest`, `probe-evidence --issue <id>`, `validate-intent --ledger
<path> --intent-id <id>`. The full operator sequence, required inputs and safe
resume rules are in `OPERATOR_INSTRUCTIONS.md`.

## Verification

All commands run from `tools/` in the YZT-84 worktree; no live Multica write
and no production-ledger access occurred.

| Command | Result |
|---|---|
| `python -B -m unittest tests.test_u12_r0_binding -v` | **50/50 OK** (positive lifecycle, unchanged-revision case, rejection matrix, crash/lease recovery, predicate unit matrix, recovery matrix, immutable bytes, non-write) |
| `python -B -m unittest discover -s tests -p test_u12_strict_receipt.py` | **40/40 OK** (focused strict regression, bytes unchanged) |
| `python -B -m unittest discover -s tests -p "test_*.py"` | **1085/1085 OK** at this HEAD |
| `python -B tools/u12_r0_binding.py self-check` | wiring proof `ok: true` (17/17 checks) |
| `python -B tools/u12_r0_binding.py artifact-digest` | reproduces every accepted pin; artifact dependency digest `sha256:937521a64efecdc762090fbf8d914eabba89fe7a703b7cda896886807af5402f` |

The predecessor pin/regression evidence (Lead-attributed 1035-test/full-pin
matrix) is **not claimed as independently rerun**; the 1085-test run is this
HEAD's own result. What was independently reproduced here: the strict gate
40/40, the strict gate LF digest, the `chandoff_intent.py` LF digest, the
readiness-manifest canonical digest `sha256:64a5c449…`, and the U11/O2 bundle
file digests used by the artifact dependency digest.

Immutable-byte comparison: `git diff --name-only
fcf63534e74b70220421c7b83c5447242d9a124b` lists only the new files above; no
predecessor byte changed. Production-ledger non-write attestation: the ledger
at `D:\AI\multica-state\web-imagegen\dispatch\ledger.jsonl` stayed at
622 bytes / `sha256:c96838987bd362b263c177ba670e193f2cdcdf02e1694f896667ab57920c3412`
across the run (checked before/after by `ProductionLedgerNonWriteTests`); the
adapter contains no reference to that path and all tests use temp ledgers.

## Live evidence capability (read-only probe)

`probe-evidence` against a real issue (YZT-82) confirms the deployed CLI
(v0.4.42) supplies: exact `issue.revision`, full non-compact comments with
`source_task_id`, `author_id/type`, per-comment `revision`, `created_at` /
`updated_at`, complete comment inventory without a pagination cursor,
activity records with actor/action/timestamp, and non-truncated run listings.

Exact gap: the timeline exposes **no issue-revision chain** — there is no
platform-provided causal link from a revision delta to an event. Revision
attribution in this adapter is therefore delta-exclusion based (complete comment
inventory + activity set + full issue projection must show exactly the
authorized note and nothing else). An invisible concurrent write cannot be
excluded by the adapter lease; the predicate refuses any interval it cannot
attribute and returns `PUBLICATION_PROVENANCE_INCOMPLETE` (mapped to a typed
`BLOCKED`) rather than PASS. This boundary matches YZT-83's evidence-sufficiency
section and keeps the live R0 decision with the Lead under single-dispatch
ownership of the bounded target. A live publication interval has not been
observed end-to-end; fixture-positive success is not live evidence sufficiency.

## Deviations from the design sketch

- None in external behavior, state vocabulary, schemas or accepted bytes.
  Implementation-local choices: namespaced event names (`r0b_*`), attempt
  `operation_id` format, LF-normalized body digests, and the adapter-byte pin
  in `r0_binding.adapter_digest` (a missing/different pin stops; never a
  downgrade). The typed refusal for insufficient publication coverage is
  `BLOCKED / PUBLICATION_PROVENANCE_INCOMPLETE`, as designed.

## Residual risks

- Platform writes are not transactionally locked by the filesystem lease;
  readback narrows but does not eliminate a concurrent edit after the last
  read. Unattributable intervals stop.
- A handled uncertain publication deliberately sacrifices liveness (no retry);
  a later explicitly invoked operator run can only confirm read-only.
- `CanaryOrchestrator` itself is not modified, so factory-only execution of
  tagged intents is enforced by contract validation, refusal of the plain
  transition API, static wiring proof and operator instructions — not by a
  platform-level hard boundary.
- Live R0, R1/R2, 05/06, O3 and merge remain parked.

## Handoff

Returned to Lead; no automatic 05/06 gate. Artifacts: adapter + tests commit on
`yzt-84-u12-r0b-forward-adapter` off `fcf6353` (clean worktree), the manifest,
the lifecycle evidence and the operator instructions above. Recommended next
wave: Lead reviews this artifact, decides whether the delta-exclusion
attribution boundary is sufficient for the already-approved U12 first canary,
and only then separately activates live R0.
