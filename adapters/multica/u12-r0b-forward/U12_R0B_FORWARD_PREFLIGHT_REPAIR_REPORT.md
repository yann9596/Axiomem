# U12-R0B forward preflight repair report (YZT-84)

Verdict: **FORWARD_PREFLIGHT_REPAIR_IMPLEMENTED / READY_FOR_LEAD_REVIEW**.
Live R0 eligibility remains a Lead decision; no live create, assign, publish
or trigger was performed. This repair supersedes the prior live-eligibility
claim of `adapters/multica/u12-r0bi/` for this adapter; the predecessor
evidence bytes and history are retained unchanged.

## Authority and inspected inputs

| Input | Reference | Verified |
|---|---|---|
| Accepted repair contract | `U12_R0_PREFLIGHT_DECISION.md`, attachment `01a08f61-3983-7c39-bdc6-42aa6893c2ee` | raw SHA256 `3acb66e3be54e9c00bf7c8f819b8970175d3a14984e3422f500220ae2106a2a9` (read in full) |
| Unchanged design baseline | `U12_R0_BINDING_DECISION.md`, attachment `01a08f10-fc8c-778c-a150-2ed54825515a` | raw SHA256 `9d248af4bfbac31d138f2713d8ce48294831c84db937ed6ac10613c88987e3b5`; steps 7/8 unchanged |
| Base revision | `6d08621b23965215a35f004a0f60c8dedc3daa0b` | prior adapter LF SHA256 `3773d19fba7814bc0802a32d8e661591bc54ea457237b1e7815132174ef55ceb` reproduced |
| Isolated reproduction | attachment `01a08f61-3aba-74d2-858e-989cae0dc50a`; Lead script `01a08f5b-7851-7291-b86b-c294609ff692` | simulations, not live incidents; re-run under the repaired adapter in `preflight-reproduction.json` |
| Authority pin | `adapters/multica/u12-p0r/readiness-manifest.json` canonical SHA256 `ee6cb9a24580e651edc4c7af2d3b4df723622fa6a8bc3f368c07916b0cea5da0` (`manifest_digest` `64a5c449...`) | current worktree record equals the bound version; self-digest and cross-links reproduce |

## What was wrong and what the repair does (contract clauses 1–8)

The prior adapter returned `artifact_ready=True` in the O2 snapshot from
durable state, derived readiness from saved digests/booleans, re-ran
SELF_CHECK only over the stored request with a forced empty findings list,
and left `_artifact_recheck` as a source-less default success. Arm/trigger
therefore never observed current material. The repair adds one shared
`_preflight_materials` path executed synchronously by `arm()` and by every
not-yet-issued `trigger()`:

1. **Fresh evidence.** Current issue, complete comments (single read, raw
   content kept from the same snapshot), timeline, complete runs and a
   post-collection issue re-read; a moving/incomplete source stops.
2. **Artifacts: integrity AND current subject.** The full bound dependency
   map is rebuilt through the declared `raw`/`LF`/`canonical_json` methods
   with a mandatory reader; unreadable/missing is BLOCKED, changed bytes or
   aggregate is REFRESH_REQUIRED with the exact path. Separately the current
   authority record is re-read from the existing bound path
   (`adapters/multica/u12-p0r/readiness-manifest.json`), compared to the
   bound canonical version, checked for READY/REVOKED/SUPERSEDED disposition,
   self-digest reproduction and its cross-links to the bound strict gate and
   R0 plan. A different current version is never adopted implicitly.
3. **Current semantics/context/role.** A caller-supplied fresh
   target-derived `current_request` must keep the bound `task_ref`, role,
   project mapping and selected decision refs; its frozen fingerprint and
   all `built_from` revisions (task/memory/registry/role profile) are
   compared to bound E; the frozen SELF_CHECK runs on bound E against that
   request and the current Finding source. No parser widening, no revision in
   T00, no silent E rebuild, no forced empty findings.
4. **Current note.** The exact target-local bound note is re-read from the
   fresh complete inventory and must match id, original revision, author
   type/id, source run, thread, body digest and parsed canonical envelope;
   duplicate matching bodies are ambiguous and stop.
5. **Issue and plan.** The fresh nonvolatile issue projection is diffed
   against the published baseline (stored at confirmation), the
   logical-role→exact-agent binding, parent, project and backlog status are
   checked, and the O2 snapshot is built only from validated evidence. The
   separate exact Issue-revision equality stop is retained. Public
   snapshot-taking entrypoints refuse.
6. **Ordering and persistence.** Validation completes immediately before the
   unchanged O2 issuance path; the intent revision is re-checked before
   issuance; the preflight is recorded as an append-only namespaced
   `r0b_preflight` event (diagnostic only, never authorization); the durable
   `TRIGGER_ISSUING` + strict receipt remains the only side-effect path.
7. **Typed failures.** Confirmed change/supersession/non-READY →
   `REFRESH_REQUIRED`/`R0B_MATERIAL_STALE`; missing/unreadable/ambiguous →
   `BLOCKED`/`R0B_MATERIAL_UNAVAILABLE` or
   `BLOCKED`/`R0B_PREFLIGHT_INPUT_MISSING`; zero native reruns before any
   attempt; repeated stops replay with zero calls.
8. **Resume contract.** `recover()` stays instruction-only
   (`performed=false`); a reconstructed factory re-collects everything at
   the resumed entrypoint; durable `TRIGGER_ISSUING`/`AMBIGUOUS` states run
   only read-only reconciliation (`R0B_TRIGGER_ALREADY_ISSUED` on
   re-entry), never a fresh eligibility check.

## Changes

- `tools/u12_r0_binding.py` (LF SHA256 `8a9b75628f1263004ab214c96733d1b775399cdd6ffe5208fdaf4c0ea5abbc05`, was `3773d19f...`):
  new preflight section (`PreflightRefusal`, `rebuild_artifact_dependency`,
  `compare_artifact_dependency`, `preflight_request_check`,
  `preflight_self_check`, `preflight_note_check`, `preflight_issue_check`,
  `ReadinessManifestAuthorityReader`, `validate_authority_evidence`),
  factory `_preflight_materials`/`_record_preflight`/`_refuse_preflight`/
  `_require_intent_unchanged`, rewritten `arm`/`trigger` signatures, refused
  `plan_and_arm`/`issue_trigger` snapshots, fail-closed `_artifact_recheck`,
  one-read `collect_evidence`, stored `post_publication_projection`, extended
  wiring/contract proofs and the read-only `authority-evidence` CLI.
- `tools/tests/test_u12_r0_binding.py` (LF SHA256 `cb187740701f12d681de170454d473e0139bce54783654d16e8a1774ad6740f9`):
  E is now built from the actual target snapshot; the harness counts
  artifact/authority reader invocations; 28 new preflight matrix tests.
- `adapters/multica/u12-r0b-forward/` (new): this report,
  `implementation-manifest.json`, `preflight-acceptance-matrix.json`,
  `preflight-reproduction.json` + `reproduce_preflight.py`,
  `authority-reader-example.py`, `OPERATOR_INSTRUCTIONS.md`.
- Predecessor bytes: `git diff --name-only 6d08621` over all frozen and
  predecessor paths is empty; only the two forward files are modified. The
  prior `u12-r0bi` evidence is untouched (raw digests recorded in the
  manifest).

## Deviations and interpretation notes

1. Public `plan_and_arm`/`issue_trigger` now refuse caller snapshots instead
   of re-validating them; the contract explicitly allowed "refuse external
   issuance/arming or internally perform the same fresh preflight". The
   inherited `resume()` snapshot path is refused the same way.
2. SELF_CHECK `BLOCKED`/`ESCALATE` on the preflight is mapped to
   `REFRESH_REQUIRED`/`R0B_MATERIAL_STALE` per clause 7 ("non-READY context →
   REFRESH_REQUIRED"), with the frozen status and reasons preserved in the
   bounded detail; both variants are pre-issuance typed stops with zero
   native calls.
3. `_artifact_recheck` (publication predicate only) now fails closed when no
   reader is configured; the source-less default success described in the
   decision is removed there as well. It is not a second preflight: the
   arm/trigger path validates the full dependency independently.
4. `collect_evidence` now reads the comment inventory once (one consistent
   snapshot) instead of twice.
5. The creation spec must now bind the authoritative readiness manifest path
   with `canonical_json`; this defines the authority source without inventing
   a registry. Consequence: old unissued R0B records stop (the adapter digest
   also changed), exactly as the decision's compatibility clause requires.
6. `post_publication_projection` (full nonvolatile issue projection) is now
   stored at publication confirmation to make the baseline diff exact.
   Records without it fail closed on the projection check.
7. Contract/module version stays `U12-R0B/1.0`; only the adapter bytes/digest
   change, so the downgrade refusal applies automatically.

## Tests and evidence

| Check | Result |
|---|---|
| Focused module `tools/tests/test_u12_r0_binding.py` | **78/78 OK** (50 before, 28 new) |
| Strict receipt regression `test_u12_strict_receipt.py` | **40/40 OK** |
| Full `tools/tests` discovery | **1113/1113 OK** |
| Static wiring proof (`self-check`) | 28/28 checks true; snapshot arming/trigger refused; preflight bound at both entrypoints; findings not forced empty |
| Acceptance matrix | `preflight-acceptance-matrix.json` — all rows PASS, 0 skips |
| Reproduction rows | `preflight-reproduction.json` — unchanged → RUN_CORRELATED, reruns=1; all mutation/supersession/blocking rows → typed stop, reruns=0; artifact reads 7 at arm and 7 at trigger; authority reads 1 and 1 |
| Authority reader example | 7/7 checks true (`authority-reader-example.py`) |
| Production ledger | readable, 622 bytes, tip `sha256:c96838987bd362b263c177ba670e193f2cdcdf02e1694f896667ab57920c3412`, unchanged before/after; the readability test executed (not skipped) |
| Frozen/predessor bytes | unchanged vs `6d08621`; accepted pins reproduce (`f37ed091...` strict gate, `0544046f...` `chandoff_intent.py`, `64a5c449...` readiness manifest) |

Authoritative artifact dependency digest: `sha256:937521a64efecdc762090fbf8d914eabba89fe7a703b7cda896886807af5402f`.

## Context findings (input readiness)

- The concrete authority binding exists and is executable: the bound
  readiness manifest at its accepted version, read fresh by
  `ReadinessManifestAuthorityReader` or produced by
  `python -B tools/u12_r0_binding.py authority-evidence --root <repo>`.
  A bool or cached READY can never satisfy the check.
- The readiness manifest does not enumerate the full bound subject set, so
  "current approved subject is exactly the bound set" is verified by the
  rebuilt per-path dependency map plus the manifest's cross-links (strict
  gate digest, R0 plan/evidence digest); the manifest `manifest_digest`,
  canonical version and disposition cover supersession/revocation. A richer
  authority source that enumerates all subjects would be a separate Lead
  decision, not built here.
- Live readiness still requires: an explicit live runner, the live Finding
  source (`RuntimeFindingStore().load_open()`), a live target-derived
  request builder, and the Lead's R0 activation under single-dispatch
  ownership. The isolated fixtures prove the adapter behavior, not live
  runtime input availability.

## Risks

- No cross-system atomic compare-and-trigger: an external change after the
  final read and before the rerun remains theoretically possible; the
  interval is short, synchronous and stops on any observed movement. The
  Human-approved attribution boundary is unchanged.
- Any stop requires Lead disposition; there is no automatic refresh,
  republish, retry or route change, and no full six-role activation.
- The production-ledger non-write assertion is a before/after byte compare
  in this runtime, not a platform-level isolation guarantee.

## Ready for review

Implementation is complete on branch `yzt-84-u12-r0b-forward-adapter` from
`6d08621` in worktree `D:/AI/worktrees/multica-memory-yzt-84-u12r0bi`
(clean tree at commit time). Recommended next wave: Lead reviews this exact
artifact and decides live R0 activation separately; R1/R2, 05/06, O3 and
Merge stay parked.
