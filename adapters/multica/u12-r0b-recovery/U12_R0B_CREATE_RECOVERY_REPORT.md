# U12-R0B Forward Create Recovery — Implementation Report (YZT-84)

Verdict: **IMPLEMENTATION_READY_FOR_LEAD_REVIEW**. All six validation groups of
the accepted contract pass through the public operations (46/46 fixture rows,
63/63 focused tests). Live R0 recovery remains a separate Lead decision and was
not performed: no live create/assign/comment/rerun, no production-ledger write,
no target mutation, no Canonical write, no 05/06, O3 or Merge action.

## 1. Contract and exact subjects

- Accepted design: `U12_R0_CREATE_RECOVERY_DECISION.md`, attachment
  `01a08f89-53bc-7bb0-bda2-1bf86c40be7f`, raw SHA256
  `1fedade6694d6d15f076a1ecb84aedfa680c93767cd3ec27ac94b594220b101e`
  (downloaded through the Multica CLI, digest verified, read in full).
- Base revision: `b49630b881170f7e6f40ffe61a82687492b99792` on
  `yzt-84-u12-r0b-forward-adapter`, worktree
  `D:/AI/worktrees/multica-memory-yzt-84-u12r0bi` (no other worktree used, no
  user content touched).
- Predecessor adapter: `tools/u12_r0_binding.py` LF SHA256
  `8a9b75628f1263004ab214c96733d1b775399cdd6ffe5208fdaf4c0ea5abbc05`.
- Live subjects: intent `DI-e5e2b856f6b7cd36`, target YZT-85
  `01a08f80-6afa-7d95-838a-6d510ec8f803`, marker `R0BI-f339fa4758aecb34`, C
  `CTX-engineering-lead-61c0c57b464bf1b3`, source run
  `01a08f7c-8fb9-74d5-97ec-7082cd9a4f9e`.

## 2. Changes

`tools/u12_r0_binding.py` (LF
`sha256:ccf2bad8c9ba4e45e3c2ac35c2a3a3754798e45737dbf3c9cf2dd17500281d29`):

1. **Transport profile `single-terminal-lf/1`** — `single_terminal_lf_relation`
   (directional predicate) and `prepare_transport_body` (prospective
   preparation). `validate_creation_spec` now accepts optional
   `source_body` + `transport_profile`, constructs and persists the final
   transport body *before* approval/digest, keeps raw source digests and the
   named transformation as `transport_preparation` provenance, and refuses
   CRLF, repeated terminal LF and any other trailing whitespace instead of
   trimming. `_create_payload` sends the persisted bytes; `_verify_created_issue`
   requires exact transport bytes for profile specs while the legacy exact path
   is unchanged.
2. **Public recovery operation** `recover_created_target(intent_id,
   expected_target_id, recovery_decision, actor, authority_evidence=None,
   execution_commit=None)` plus `validate_recovery_decision` (exact immutable
   self-digested disposition bound to the accepted design, predecessor pins,
   intent/target/revisions/creator). Under the intent lease it revalidates the
   full original record (spec, C, logical key, source run, authority, artifact
   dependency, provenance pin, standalone marker/Intent lines, the one create
   attempt, the one ambiguity transition, the ledger chain), performs complete
   parent-child discovery including the live `unstaged` child (exact standalone
   identity, never marker substring), reads the complete issue/comment/
   timeline/run evidence plus a stable re-read, checks exact target fields,
   empty comments/runs, the single creator `created` activity, current pinned
   artifact bytes and current READY authority, and classifies the body with the
   directional relation. It then appends `r0b_recovery_evidence` (full proof),
   CAS-transitions `CREATE_AMBIGUOUS -> TARGET_BOUND` carrying the proof,
   effective transport digest and fenced execution binding, and appends the
   informational `r0b_recovery_bound` event. Returns `TARGET_BOUND` +
   `RESUME_OWNERSHIP_NO_START`, zero external writes. Replay is read-only.
3. **Compatibility/fence** — `CONTRACT_VERSION` is now `U12-R0B/1.1`; the
   recovered record keeps the original `adapter_digest`, contract version and
   every historical field, while a new namespaced `execution_binding` records
   the effective transport identity, the new execution adapter digest and the
   committed proof/decision references. `validate_intent_record` is
   version-aware: predecessor `U12-R0B/1.0` records stay readable/auditable but
   are not executable under new bytes (`R0BCompatibilityRefused`), and only a
   valid committed proof admits the exact old-to-new transition. A tampered,
   missing or cross-intent proof refuses. The actual predecessor module (loaded
   from `b49630b`) refuses the fenced record at every entrypoint.
4. **Operator surface** — CLI `recover-created-target`, `prepare-transport`,
   `transport-relation`, `recovery-decision-digest` (+ `--check`); new static
   wiring checks (38/38 true) proving the recovery path is read-only, uses only
   the `TARGET_BOUND` edge and validates the decision/proof.

`tools/tests/test_u12_r0_create_recovery.py` (new, LF
`sha256:1d44a56c41834c2409df0a06128c9a6013255c488277c11e121668364af8a056`):
63 focused tests across the six groups; the predecessor record is produced by
the actual `b49630b` module bytes and the old-executor refusal is tested against
that real code.

`tools/tests/test_u12_r0_binding.py` (LF
`sha256:80e1a02e2dd0e0b701540dfd7971f85880dd905f330c01d92366ebb046aad069`):
one test updated (see Deviations).

`adapters/multica/u12-r0b-recovery/` (new versioned evidence):
`recovery-acceptance-matrix.json` (46/46 rows, all six groups),
`reproduce_create_recovery.py` (executable public-operation fixture),
`OPERATOR_INSTRUCTIONS.md`, `recovery-decision-example.json`, this report and
the manifest. Predecessor evidence under `u12-r0bi/` and `u12-r0b-forward/` is
byte-unchanged.

## 3. Acceptance evidence (all through public operations)

- **Group 1 exact LF recovery**: `CREATE_AMBIGUOUS -> TARGET_BOUND` on the
  exact observed incident shape; recovery window contains 6 read commands and 0
  writes; the historical ledger is an exact byte prefix; original spec, C,
  provenance pin and the sole create are preserved; proof/execution-binding
  digests reproduce; replay is a byte-identical read-only no-op.
- **Group 2 transport**: 10/10 relation cases match the accepted predicate;
  6/6 unsupported sources refuse; prospective creation persists and sends the
  same no-final-LF transport body with raw/LF source digests; the legacy exact
  path still binds; a platform-mutated transport stops at `CREATE_AMBIGUOUS`
  with zero reruns.
- **Group 3 refusals (26)**: wrong target/title/parent/project/priority/
  creator/revision/assignee/status, duplicate and missing identity candidates,
  marker substring only, non-empty runs, extra comment, prior ownership/
  publication/unknown effects, a second create attempt, truncated
  comments/timeline/runs/children, stale artifact, missing artifact reader,
  superseded authority, missing authority source, movement during collection —
  every case returns a typed refusal, keeps `CREATE_AMBIGUOUS` and makes zero
  native writes.
- **Group 4 fences**: bare changed pin refuses before any read; unknown
  version, decision tamper/extra field/wrong design/wrong commit/wrong target
  and revision mismatch refuse; old history audits as `U12-R0B/1.0` while
  execution refuses; the recovered record executes under the new bytes; edited,
  relabeled and cross-intent proofs refuse; the **actual old executor** loaded
  from `b49630b` raises its own `R0BContractError` at `validate`,
  `assign_ownership_once` and `recover` with zero external calls; frozen strict
  gate (`f37ed091…`), `chandoff_intent.py` (`0544046f…`) and readiness manifest
  (`ee6cb9a2…` entry / `64a5c449…` self-digest) reproduce.
- **Group 5 crash/replay/concurrency**: crash before evidence, after evidence
  and after the binding commit behave exactly as specified; a retry after the
  evidence crash recollects; a crashed-before-ack recovery replays with zero
  commands and one binding; repeated recovery is a no-op; competing lease
  refuses before any read; two concurrent recoverers keep exactly one binding
  and one historical create; attempt flags cannot be reset.
- **Group 6 post-recovery lifecycle**: ownership (`--no-start`), E binding,
  one publication, fresh preflight at `arm` and `trigger`, exactly one strict
  rerun → `RUN_CORRELATED`; drift after arm produces `REFRESH_REQUIRED` with
  zero reruns; an ambiguous trigger reconciles read-only without reissue.

Regression runs on this HEAD: focused recovery module 63/63; frozen lifecycle
suite 78/78; `tools/tests` full run 1,176 tests, 1,173 pass, 3 pre-existing
failures (see Risks); strict receipt 40/40; preflight 21/21. The full-suite
failures are outside this diff.

## 4. Deviations

1. `CONTRACT_VERSION`/`ADAPTER_VERSION` moved to `U12-R0B/1.1`. This is the
   design-mandated execution fence: a recovered record keeps the original
   `U12-R0B/1.0` version in provenance but carries the `1.1` tag the predecessor
   factory rejects. New records recorded by these bytes are `1.1`; a bare
   changed pin still refuses.
2. `recover()` (the read-only classification entrypoint) now loads with
   `executable=False` and, for a predecessor record, returns the instruction
   `PREDECESSOR_CREATE_RECOVERY_REQUIRED` /
   `RECOVER_CREATED_TARGET` instead of attempting the old create path; all
   lifecycle entrypoints refuse predecessor records with
   `R0BCompatibilityRefused` (no automatic migration).
3. Recovery refusals append one bounded `r0b_evidence_refused` diagnostic
   (window `create_recovery`) but never transition the intent; the state stays
   `CREATE_AMBIGUOUS` so Lead can correct material and retry. Structural
   failures (pin, version, decision digest, proof, competing replay) raise typed
   errors instead of returning a result.
4. `ProductionLedgerNonWriteTests` in the predecessor test module no longer
   asserts the stale pre-incident whole-file digest (`c9683898…`, 622 bytes):
   the authorized live R0 create changed the production ledger to
   `5a7e3369…` (37,860 bytes), and the Lead's later live recovery will change it
   again. The test now checks the stable invariant it exists for — the ledger is
   intact append-only JSONL, folds without corruption, keeps exactly one live
   create attempt, and its bytes are unchanged across the isolated run.
5. Records with `transport_preparation` verify freshly-created targets by exact
   transport bytes rather than the historical LF-normalized digest; legacy
   records keep the historical comparison exactly.
6. The `E_RECOVERY_EVIDENCE` event is an allowed prior effect so a crash
   between the evidence append and the transition can be retried; the event is
   never treated as authorization (evidence is always recollected).

## 5. Context findings and operator readiness

- Read-only inspection of the live chain (production ledger, readable in this
  runtime) confirms the exact fixture shape: one `recorded`, one
  `r0b_create_issuing`, one `r0b_evidence_refused`, one
  `INTENT_RECORDED -> CREATE_AMBIGUOUS` transition (reason `CREATE_AMBIGUOUS`),
  revision 1, original pin `8a9b7562…`, body `70bde7ed…` (2992 chars), zero
  ownership/publication events, fresh artifact dependency `937521a6…`
  reproducing exactly from this checkout. All recovery prerequisites therefore
  validate in principle against the real record.
- Operator readiness: the exact CLI invocation, decision schema and crash
  semantics are in `OPERATOR_INSTRUCTIONS.md`; `recovery-decision-example.json`
  is a structural template whose digest must be recomputed after the Lead fills
  in `decision_id`/`approval_ref`/`approved_by`/`approved_at`.
- Remaining live blockers (Lead-owned): accept these exact repair bytes; issue
  the concrete disposition; re-audit the chain/attempt immediately before the
  append; provide the accepted-checkout root and execution commit. No 04-side
  blocker remains.

## 6. Risks

- The live chain/attempt audit remains Lead's precondition; this report and the
  fixture do not substitute for it, and the point-in-time observation in the
  operator instructions is not reusable authorization.
- CLI normalization beyond the observed single terminal LF is unproven; every
  other form is refused (no global trim, no Unicode/interior normalization).
- The timeline exposes no revision-to-event chain; target stability relies on
  the complete evidence set plus the re-read, as previously accepted.
- Full-suite drift: `test_u12_p0r_evidence.py` has 3 failures
  (`test_production_ledger_read_only_integrity`,
  `test_generation_is_deterministic`,
  `test_committed_bundle_regenerates_byte_identical`) because the frozen
  U12-P0R evidence pins the pre-incident 622-byte genesis ledger and the
  authorized live R0 create changed it (37,860 bytes). Those files are outside
  this diff and the fail-closed pins are deliberate accepted evidence; I did
  not modify frozen bytes or accepted-evidence tests. Lead disposition is
  needed if the P0R evidence bundle is to be superseded.

## 7. Memory candidates (Context Engineer)

- The live create transport removed exactly the terminal LF; the accepted
  directional `single-terminal-lf/1` relation is now implemented and fenced.
- The recovered record pattern: original pin/version/spec preserved +
  `execution_binding` + committed `recovery_proof` + `U12-R0B/1.1` fence; the
  predecessor factory refuses it and the new factory audits old history.
