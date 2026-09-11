## U12-R0B — Source-Activation and Semantic Proof Correction

**Verdict:** `IMPLEMENTATION_READY_FOR_LEAD_REVIEW`. The two independently
reproduced omissions in the approved publication recovery contract are
corrected on top of the preserved history, with the corrected payloads versioned
`/1.1` and the previous `ac1e5b4` adapter refusing them. Live recovery was
**not** performed: no production-ledger read/write, no note resend, no
create/assign/status/rerun, no ARM/TRIGGER, no YZT-85 mutation, no Canonical
write, no 05/06 activation. Live disposition remains Lead-owned.

### Authority

| Item | Ref | Digest |
|---|---|---|
| Authoritative contract | `attachment/01a08ffa-604f-70c3-a03e-0d166505fa3d` | `sha256:0df4cec9a7cfb26a2b9146806280a2dcae4924f6cdb29190560ab17db32c8997` |
| Human approval | comment `01a09003-f830-79ac-b022-c7aaf2cf4039` on YZT-66 | content pinned (raw digest below) |
| Lead exception boundary | comment `01a08ffb-fb18-7135-bc19-c17297426127` on YZT-66 | content pinned (raw digest below) |
| Accepted source activation | comment `01a08ff4-83c5-7b0c-a075-ec60184a843a` on YZT-66 | Lead-authored, resolved at recovery time |
| Correcting decision | parent YZT-66 comment `01a09048-1fcd-7ca6-ae75-ad07bb5f0ffd` | `CHANGES_REQUIRED` + both counterexample scripts |

The correction implements exactly the required bounded items: real resolution
and verification of the Lead-authored source activation and its attached
resolution/request, actual approval-authority content verification, and one
shared pure semantic proof verifier for the writer and the reducer/restart.
No architecture, scope or trigger change is introduced.

### Base and commits

- Worktree `D:\AI\worktrees\multica-memory-yzt-84-u12r0bi`, branch
  `yzt-84-u12-r0b-forward-adapter`, base `ac1e5b4` (previous adapter LF
  `sha256:37877f694c61ee13a098c2686f7a08cf55bfe40d95772f6e8cbee939e4991b56`,
  preserved as history, not accepted).
- Forward commits:
  - `2665180` `fix`: v1.1 source-activation resolution, approval-authority
    content verification and the shared pure semantic proof verifier
    (writer + reducer + replay).
  - `265d73b` `test`: source-activation and semantic-rehash refusal matrix
    (17 new focused tests, including both Lead counterexamples through the
    helper, writer, reducer and replay paths).
  - evidence commit: this report, operator instructions, examples,
    reproduction script and acceptance matrix; `implementation-manifest.json`
    records the exact `evidence_commit` and the committed-blob LF digests.

### The two independently reproduced omissions and their correction

1. **Source activation was shape-checked only.** The Lead counterexample
   replaced `decision.source_activation` with nonexistent comment/attachment
   ids and a zero resolution digest, recomputed `decision_digest`, and the
   public `recover` returned `PUBLICATION_RECOVERED / HANDOFF_PUBLISHED
   revision 5` (`ledger_commits=1`), while the command trace read only the
   target issue/comments/timeline/runs and never the parent activation or its
   attachments.
   - Corrigendum: `recover_blocked_publication` now resolves the unique
     Lead-authored activation record on the recorded parent through the
     authenticated CLI, requires the actual E package id, full envelope digest,
     task fingerprint, target id and intent id in that record, downloads the
     resolution and request attachments, re-hashes the raw bytes against the
     disposition, verifies the request `relevant_decisions` binding, binds the
     request to the persisted E request and fingerprint/built_from, and
     reconstructs the request from the freshly read target body plus the
     accepted explicit decisions. Missing, duplicate, edited, wrong-author,
     wrong-ref, false-hash, wrong-E/target/intent or stale-body cases are typed
     refusals. The corrected counterexample payload now stops as
     `R0BValidationRefused: decision.source_activation must stay within the
     exact schema` (old shape) or `RECOVERY_REFUSED /
     PUBLICATION_PROVENANCE_INCOMPLETE` with the parent read performed and zero
     downloads for the new-shape nonexistent-id variant. Zero commit, zero
     native write, BLOCKED revision 4 preserved.
2. **The proof was digest-local, not semantic.** The Lead counterexample
   changed `proof.observations.runs` to an unexpected completed run, recomputed
   `observations.digests.runs` and `proof_digest`, and
   `validate_publication_recovery_proof(applying=True)` accepted it because it
   verified local digests but never reconstructed the empty-runs predicate from
   the raw responses.
   - Corrigendum: one shared pure semantic verifier
     (`validate_publication_recovery_semantics`) now runs inside
     `validate_publication_recovery_proof` for the commit writer and the
     reducer/restart replay. It re-derives every normalized observation
     (issue, comments, activities, runs) from the complete persisted raw
     responses, enforces the exact read sequence and raw-response/projection
     correspondence, re-checks the stable full reread, re-evaluates the empty
     full-run predicate, the sole note relation `O == R[:-1]`, the note
     uniqueness/author/thread/envelope/meta linkage, the before/after delta and
     issue/timeline stability, re-verifies the resolved source
     activation/authority/E/request/material relationships and re-derives every
     shared-history classification from the record bytes. Recomputed
     attacker-controlled hashes cannot make invalid evidence valid. The
     corrected counterexample now stops as `R0BValidationRefused: the committed
     runs observations do not correspond to the persisted raw responses; the
     projection is edited or substituted` in the helper, the writer, the
     reducer and the committed replay path, while the untampered record still
     commits once.

### Version bumps and fences

| Payload | Previous | Corrected |
|---|---|---|
| publication recovery decision | `u12-r0b-publication-recovery-decision/1.0` | `/1.1` |
| publication recovery proof | `u12-r0b-publication-recovery-proof/1.0` | `/1.1` |
| publication execution migration | `u12-r0b-publication-execution-migration/1.0` | `/1.1` |

The old `ac1e5b4` reader refuses the v1.1 decision, proof and commit record; the
new loader refuses the frozen v1.0 payloads. No silent upgrade and no rollback
readability.

### Validation

- **Focused module** `tools/tests/test_u12_r0_publication_recovery.py`:
  **62/62 OK** (45 preserved + 17 new correction tests, including both Lead
  counterexamples through the helper/writer/reducer/replay paths, the semantic
  rehash matrix — nonempty runs, raw-response/projection mismatch, a removed
  note rehashed in both the raw responses and the projection, issue/timeline
  rehash, shared create/ownership commands relabelled as reads with a
  recomputed `classification_digest` — the source activation/authority refusal
  matrix and the old/new reader fences).
- **Frozen focused suites**: lifecycle 78/78, create recovery 63/63, recovery
  evidence 35/35, note 84/84, intent 118/118, strict receipt 40/40, preflight
  21/21 — all OK.
- **Full `tools/tests`**: **1,273 run / 3 failures-or-errors** (2 errors + 1
  failure), all in `test_u12_p0r_evidence.py` and all the pre-classified
  pre-incident P0R genesis pin drift (accepted 622-byte genesis/pins vs the live
  1,814,836-byte production ledger tip `sha256:4c7d431b…`). The frozen P0R
  bundle is deliberately not rewritten; no false global green.
- **Acceptance matrix**: `reproduce_publication_recovery_correction.py` →
  **10/10 rows pass** through public operations on isolated ledgers: the valid
  exact historical chain still recovers once (and replays with zero new
  commits), both Lead counterexamples in their corrected form, the source
  activation/authority/stale-request refusals, the four-path rehashed-runs
  refusal, the semantic rehash matrix, the proof-content fences and the
  old-reader/v1.0 fences.
- **Adapter `self-check`**: wiring proof all true, extended with
  `publication_semantic_proof_recheck` and
  `publication_source_activation_resolved`.

### Preservation / scope

- `git diff --name-only ac1e5b4..HEAD` lists only `tools/u12_r0_binding.py`,
  `tools/tests/test_u12_r0_binding.py`, `tools/tests/test_u12_r0_create_recovery.py`,
  `tools/tests/test_u12_r0_publication_recovery.py` and the new folder
  `adapters/multica/u12-r0b-publication-recovery-correction/`.
- The prior evidence folders `u12-r0bi/`, `u12-r0b-forward/`,
  `u12-r0b-recovery/`, `u12-r0b-recovery-evidence/`,
  `u12-r0b-publication-transport/` and the frozen `u12-p0r/` bundle are
  byte-preserved; the reserved `b49630b` worker snapshot and the original
  product worktree are untouched.
- No production ledger was opened, no platform write was issued and no note was
  resent. All new tests and the matrix run on temporary JSONL ledgers with the
  real b49630b/da99c11 module bytes loaded from the accepted commits.

### Deviations / interpretation

- The v1.1 proofs persist the resolved activation comment projection plus the
  full resolution/request texts and the authority comment projections inline so
  the reducer can re-verify everything without a live read. This follows the
  approved "complete persisted evidence" requirement; no frozen package field is
  invented and no note suffix or E replacement is introduced.
- The target request is reconstructed by a pure helper that mirrors the accepted
  `build_snapshot_request` derivation for the exact accepted mapping (title,
  description, requirements, acceptance criteria from the fresh target;
  accepted explicit decisions and caller inputs from the accepted request),
  because the reducer may not issue live CLI calls. The live path additionally
  runs the frozen SELF_CHECK on the reconstructed request with the current
  Finding source.
- The decision v1.1 adds the exact activation/attachment/E identity fields; the
  former three-field `source_activation` shape is refused as an old/hash-only
  disposition.

### Operator readiness and remaining blockers (Lead-owned)

`OPERATOR_INSTRUCTIONS.md` gives the v1.1 decision schema, the exact
`recover-blocked-publication` invocation, refusal meanings and crash/replay
semantics; `publication-recovery-decision-example.json` and
`accepted-execution-example.json` are local-fixture schema examples (never live
evidence). The Lead still owns the production audit, the live disposition
(exact blocker/attempt/note/create-recovery/activation objects) and the
acceptance of these exact bytes; a missing or mismatching value stops as
`PUBLICATION_RECOVERY_EVIDENCE_INCOMPLETE` and keeps BLOCKED. Recovery stops at
`HANDOFF_PUBLISHED`; arm/trigger, 05/06, O3 and the final Human Merge remain
parked/separate decisions.
