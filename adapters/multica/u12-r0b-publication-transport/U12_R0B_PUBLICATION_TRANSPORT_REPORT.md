## U12-R0B — Publication Transport Repair and Proof-Bearing O2 Recovery Commit

**Verdict:** `IMPLEMENTATION_READY_FOR_LEAD_REVIEW` — every approved
Validation Focus row passes on the isolated historical-shape fixture, with
execution identities resolved against the real committed HEAD blobs. Live
recovery was **not** performed: no production-ledger read/write, no note
resend, no create/assign/status/rerun, no ARM/TRIGGER, no YZT-85 mutation, no
Canonical write, no 05/06 activation. Live disposition and the production
audit remain Lead-owned.

### Authority

| Item | Ref | Digest |
|---|---|---|
| Authoritative contract | `attachment/01a08ffa-604f-70c3-a03e-0d166505fa3d` | `sha256:0df4cec9a7cfb26a2b9146806280a2dcae4924f6cdb29190560ab17db32c8997` |
| Human approval | comment `01a09003-f830-79ac-b022-c7aaf2cf4039` on YZT-66 | read in full |
| Lead exception boundary | comment `01a08ffb-fb18-7135-bc19-c17297426127` on YZT-66 | read in full |
| Accepted source resolution | `attachment/01a08fec-29a9-75ec-ad1c-0f3ecbb96649` | `sha256:e689432dd0596760831aa184d3d982599836a426b34c678a6ccb2a50be9ce023` |
| Accepted create-recovery design | `attachment/01a08f89-53bc-7bb0-bda2-1bf86c40be7f` | `sha256:1fedade6…` |
| Accepted evidence correction | `attachment/01a08fbb-51cd-7aeb-907f-a547e4e066e9` | `sha256:60f265a8…` |

The exception is implemented exactly as bounded: the only state-machine exit
added is the versioned `publication_recovery_commit_v1` op for this single
publication incident; ordinary `BLOCKED` transitions, the trigger matrix, the
strict receipt gate, frozen T00, Core and the original C/E/proof/history are
unchanged.

### Base and commits

- Base: `da99c112093ea576a449a1f3c8ce355652805a40` (adapter LF
  `sha256:40ccf07dd088d6a4077213714deef45c83f46116478ccc462c203ef7f07641fa`,
  verified before work; worktree clean).
- Local forward commits (base..HEAD):
  - `839e8f7` `feat`: publication transport repair + proof-bearing O2 recovery
    commit writer/reducer + execution migration.
  - `a70dd70`, `2576c64`, `dea0a06`, `5f94d91`, `b5d72b3`, `94d66b6`,
    `25be58e`, `09e1091`: fixture, validator alignment, target baseline,
    seq-ordered latest-field reducer support and preflight pin scope.
- HEAD when the acceptance matrix was generated: see
  `publication-transport-acceptance-matrix.json`
  (`execution_migration_commit`).

### What changed

1. **`tools/u12_r0_binding.py`** (the forward adapter):
   - Versioned profile `publication-single-terminal-lf-v1`:
     `prepare_publication_transport_body` requires the exact renderer ending
     `\n```\n`, builds `T = R[:-1]` verbatim (no strip/rstrip, no line-ending
     normalisation, no BOM handling, no variant guessing), and records separate
     raw R/T identities. `publication_transport_relation` accepts only
     `O == T`.
   - `publish_handoff_once` persists the complete issuing record (profile,
     R/T raw+LF digests, lengths, E identity, package, source run, parent,
     operation id, before evidence) **before** the unique send, sends the
     verified exact T through `chandoff_note.publish_handoff(transport_body=…)`
     (file bytes verified after write, no BOM), verifies the response body
     digest is the transport digest, and binds `publication_binding.body_digest`
     to O with `body_digest_method = raw_utf8` while the original attempt keeps
     R. The legacy exact-body path is byte-for-byte unchanged.
   - `comment_record` retains the raw content digest alongside the existing
     LF-normalised digest; `preflight_note_check` re-checks the exact O bytes
     for raw-bound records and detects R/O duplicate inventories.
   - **`recover_blocked_publication`** (factory-only, public): restricted
     historical inspection under the explicit da99c11 pin
     (`PREDECESSOR_FORWARD_ADAPTER_DIGEST`), complete live revalidation
     (blocking transition/evidence records, the sole attempt, the durable
     attempt meta re-render, the exact `O == R[:-1]` relation, note
     metadata/envelope, before/after delta-exclusion, timeline/runs, a stable
     second complete read, full shared-history classification, artifact /
     authority / fingerprint / SELF_CHECK / source-join material rechecks) and
     then the single atomic commit. It has no note-publish, create, assign,
     status, rerun, ARM or TRIGGER capability.
   - **`publication_recovery_commit_v1`**: a versioned op under the original
     `dispatch_intent` record type/schema. The writer (under the OS ledger
     lock: effective lease holder, revision/state CAS, audited prefix digest,
     exact tail digest list, conflicting open logical intent) and the reducer
     share one complete validation
     (`validate_publication_recovery_commit_record` → proof + migration).
     It applies exactly `BLOCKED revision 4 → HANDOFF_PUBLISHED revision 5`
     plus the `publication_execution_migration`. Crash before commit leaves
     BLOCKED and re-collects; after commit the reducer replays idempotently.
   - **Execution-identity migration**: binds the complete old da99c11
     create-recovery identity (execution binding/proof/decision digests,
     predecessor commit/adapter), the incident blocker/attempt, the audited
     shared prefix, and the new commit/tree/adapter raw+LF plus the modified
     `chandoff_intent.py` / `chandoff_note.py` file digests resolved from the
     committed blobs **and** the executing files. Normal
     load/validate/arm/trigger/recover validate the chain; before the commit
     only the restricted inspection can read the old bytes. A changed
     adapter hash with rewritten store/note bytes, a cross-intent copy or a
     hash-only proof refuse.
   - Wiring/contract proofs extended; CLI
     `recover-blocked-publication` added.

2. **`tools/chandoff_intent.py`** (limited O2 store/fold increment): an
   extension-op registry, `append()`/`fold_records()` dispatch with
   fail-closed unknown-op handling (an old reader refuses the new op as ledger
   corruption), and `_latest_field` now considers extension-commit fields by
   ledger sequence (byte-identical when no extension commit exists).

3. **`tools/chandoff_note.py`**: `publish_handoff(..., transport_body=…)`
   re-verifies the exact renderer relation, byte-verifies the written
   content-file, refuses a BOM, sends exactly one comment, and reports both
   the rendered and sent digests. The legacy call is unchanged.

4. **`tools/u12_preflight.py`**: the accepted artifact pin for
   `tools/chandoff_intent.py` is verified from the pinned commit blob; the
   executing bytes are bound by the migration (explicit approved-exception
   note), instead of re-asserting the frozen working file.

### Validation Focus mapping

All 16 rows of the approved contract are exercised through public operations;
the standalone matrix
(`adapters/multica/u12-r0b-publication-transport/publication-transport-acceptance-matrix.json`,
13 aggregated rows) maps 1:1 onto them:

| Validation Focus row | Matrix row | Focused tests |
|---|---|---|
| real observation shape R/O | `1_real_observation_shape` | `HistoricalShapeTests::test_fixture_is_the_exact_real_incident_shape`, `test_old_predicate_refused_the_incident_before_recovery` |
| future send T / O==T / one comment_add | `3_future_transport_o_equals_t` | `FutureTransportTests` (4) |
| zero / duplicate / R+R-minus-one notes | `5_note_candidate_refusals` | `RecoveryRefusalTests` note rows |
| whitespace / CRLF / BOM / Unicode raw relation | `4_raw_relation_refusal_matrix` | `TransportRelationTests` (5) |
| wrong E / source / author / type / thread / timestamp | `5_note_candidate_refusals`, `6_delta_and_material_refusals` | `RecoveryRefusalTests` rows |
| projection drift / timeline / baseline / runs / second read | `6_delta_and_material_refusals` | `RecoveryRefusalTests` rows |
| exact old recovered da99c11 chain → one rev5 commit | `2_exact_recovered_da99c11_chain` | `HistoricalShapeTests` (6) |
| new loader without migration / old loader on new op | `7_loader_fences` | `MigrationFenceTests` (2) |
| old proof edits / copied migration / hash-only / changed bytes | `8_migration_and_proof_tamper_refusals` | `MigrationFenceTests` (6) |
| ordinary BLOCKED exit / wrong reason/revision/attempt / duplicate commit | `9_writer_reducer_refusals` | `HistoricalShapeTests::test_ordinary_blocked_exit_stays_refused`, `MigrationFenceTests::test_competing_second_commit_refuses_in_writer_and_reducer` |
| lease / CAS / shared-tail command at same revision / conflicts | `10_lease_tail_conflict_refusals` | `CrashReplayTests` (3) |
| crash before/after commit / restart / replay / bad line | `11_crash_replay_corruption` | `CrashReplayTests` (4) |
| post-recovery arm/trigger/restart, O-bound note, strict gate | `12_post_recovery_lifecycle`, `13_post_recovery_note_drift` | `PostRecoveryLifecycleTests` (3) |

### Tests and evidence

- New focused module `tools/tests/test_u12_r0_publication_recovery.py`:
  **45/45 OK** (historical shape, transport, refusals, migration fences,
  crash/replay, lease/tail/conflict, post-recovery lifecycle).
- Full `tools/tests`: **1256 run / 3 failures-or-errors** (2 errors + 1
  failure), all in `test_u12_p0r_evidence.py` and all the pre-classified
  pre-incident P0R genesis pin drift (accepted 622-byte genesis / 37,860-byte-era
  pins vs the live 1,814,836-byte production ledger tip
  `sha256:4c7d431b…`, which is the Lead's own publication-observation digest).
  The frozen P0R bundle is deliberately **not** rewritten; no false global
  green is claimed.
- Adapter `self-check` wiring proof: all checks true (extended with the
  publication transport/recovery invariants).
- Acceptance matrix: **13/13 rows pass** through public operations on the
  real historical shape and real committed identities.
- The predecessor folders `u12-r0bi/`, `u12-r0b-forward/`, `u12-r0b-recovery/`,
  `u12-r0b-recovery-evidence/` and the frozen P0R bundle are byte-preserved.

### Deviations / interpretation

- The bounded O2 store/fold increment is implemented as a process-local
  extension-op registry in `chandoff_intent.py` instead of a hard-coded op
  list, so the core keeps no publication vocabulary while old readers still
  fail closed on the unknown op.
- `_latest_field` consults extension-commit fields by seq; without an
  extension commit the behaviour is identical to the previous transition-only
  lookup (existing suites unchanged).
- `u12_preflight.py` verifies the accepted artifact pin for
  `tools/chandoff_intent.py` from the pinned commit blob and records the
  executing digest separately; this is the documented approved-exception
  scope, not a pin rewrite.
- No live production seq/digest values are fabricated: the decision schema
  requires the Lead to fill the exact audited production objects, and the
  examples shipped here are explicitly local-fixture schema examples.

### Operator readiness

`OPERATOR_INSTRUCTIONS.md` gives the exact Lead inputs, the full decision
schema, the exact CLI invocation, refusal meanings and crash semantics.
`publication-recovery-decision-example.json` /
`accepted-execution-example.json` are schema examples generated from the local
fixture (never live evidence).

### Risks / remaining blockers (Lead-owned)

- The production audit (exact blocker transition/evidence seq+digest, attempt
  event seq+digest, note metadata, original create-recovery digests) is the
  Lead's precondition; a missing or mismatching value stops as
  `PUBLICATION_RECOVERY_EVIDENCE_INCOMPLETE` and keeps BLOCKED.
- The snapshot/listener window is not transactional; the commit re-validates
  the audited prefix, the exact tail and the logical intent under the OS lock,
  and arm/trigger still run their own fresh preflight.
- The live ledger's trailing shape must match the classifier (one create pair,
  one ownership pair, at most one publication pair, exactly one issuing and
  one response event, no bound/triggered event); any deviation is a typed
  refusal, never a silent normalisation.
- Recovery stops at `HANDOFF_PUBLISHED`; arm, trigger and the final Merge
  remain separate Lead/Human decisions.
