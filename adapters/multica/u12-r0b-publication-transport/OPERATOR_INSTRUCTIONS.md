## Operator instructions — approved publication recovery

These instructions apply **only** to the single approved exception for the
`DI-e5e2b856f6b7cd36` / YZT-85 publication incident described in
`U12_R0_PUBLICATION_TRANSPORT_DECISION.md` (raw SHA256
`0df4cec9a7cfb26a2b9146806280a2dcae4924f6cdb29190560ab17db32c8997`), approved
by the Human (`01a09003-f830-79ac-b022-c7aaf2cf4039`) and bounded by the Lead
(`01a08ffb-fb18-7135-bc19-c17297426127`). Nothing here authorizes a second
incident, an ordinary `BLOCKED` exit, a note resend, a new intent/E or any
trigger.

The operation never sends a note, creates/assigns/updates anything, reruns or
triggers; platform access is read-only. The only durable writes are the
bounded `r0b_publication_recovery_evidence` event and the single
`publication_recovery_commit_v1` record (one fsync) that moves
`BLOCKED revision 4 → HANDOFF_PUBLISHED revision 5` and binds the
execution-identity migration.

### 1. Prerequisites (all Lead-owned)

1. Accept the exact implementation bytes (adapter / `chandoff_intent.py` /
   `chandoff_note.py` digests) and the local forward commits.
2. Re-audit the production ledger read-only and fill the disposition with the
   **exact** objects: blocking transition seq+digest, blocking evidence event
   seq+digest, the sole publication attempt seq+digest+operation id, the
   expected note metadata and raw R/O digests, the completed create-recovery
   proof/decision/execution-binding digests, the accepted source activation,
   and the accepted execution-migration identities (the new commit/tree and
   the modified dependency file digests).
3. Supply the current Finding source (a JSON array; the adapter never forces
   an empty list — omit it and the operation stops `BLOCKED`).
4. Decide the execution migration commit: it must be the local commit that
   contains the new adapter and dependency bytes. The migration is validated
   against the real committed blobs and the executing files.

Missing or mismatching inputs stop as
`PUBLICATION_RECOVERY_EVIDENCE_INCOMPLETE` (recording the exact gap) and keep
the intent at `BLOCKED`; there is no fallback.

### 2. Decision and accepted-execution files

Both files are JSON objects validated by strict schemas (no extra fields):

- decision schema: `u12-r0b-publication-recovery-decision/1.0`
- accepted execution: exactly `decision.execution_migration`
  (`accepted_commit`, `accepted_tree`, `adapter_raw_digest`,
  `adapter_lf_digest`, `store_file_digest`, `note_file_digest`,
  `artifact_dependency_digest`).
- self-digest: `decision.decision_digest` must equal the canonical digest of
  the decision minus that field. Verify with:

```powershell
python -B tools/u12_r0_binding.py recovery-decision-digest `
  --decision-file <decision.json> --check
```

The shipped `publication-recovery-decision-example.json` and
`accepted-execution-example.json` are generated from the isolated local
historical fixture. They demonstrate the exact schema and are **never** live
evidence: every value must be regenerated from the authenticated production
audit.

### 3. Exact invocation

The ledger path is the Lead-controlled production ledger; this adapter never
hardcodes it.

```powershell
python -B tools/u12_r0_binding.py recover-blocked-publication `
  --ledger <production-ledger.jsonl> `
  --intent-id DI-e5e2b856f6b7cd36 `
  --decision-file <lead-decision.json> `
  --accepted-execution-file <accepted-execution.json> `
  --findings-file <current-findings.json> `
  --actor <lead-actor> `
  --artifact-root <repo-root> `
  --authority-root <repo-root>
```

Read-only pre-checks available before the live call:

```powershell
python -B tools/u12_r0_binding.py validate-intent --ledger <ledger> `
  --intent-id DI-e5e2b856f6b7cd36
python -B tools/u12_r0_binding.py self-check
```

### 4. Outcomes

- `status = HANDOFF_PUBLISHED`, `outcome = PUBLICATION_RECOVERED`,
  `ledger_commits = 1`, `platform_writes = 0`, `notes_sent = 0`,
  `triggers_issued = 0`: the one approved commit is in force. The next wave
  (arm / trigger / stop) is a separate Lead decision.
- `outcome = RECOVERY_REFUSED` with a typed reason: the intent stays
  `BLOCKED` and the refusal is recorded; nothing was sent or triggered.
  Common reasons: `PUBLICATION_PROVENANCE_INCOMPLETE` (missing/mismatched
  incident evidence, duplicate/missing note, moving target),
  `PUBLICATION_DELTA_NOT_ATTRIBUTABLE` (unauthorized comment/timeline/issue
  delta), `EVIDENCE_REVISION_MOVING` (the second complete read drifted),
  `R0B_MATERIAL_STALE` / `R0B_MATERIAL_UNAVAILABLE` (artifact, authority or
  note material), `R0B_PREFLIGHT_INPUT_MISSING` (no Finding source).
- Repeated calls with the same committed decision return
  `replayed = true`, `ledger_commits = 0` and issue nothing else. A
  different decision name after a commit is refused as competing proof.

### 5. Crash semantics

- Crash before the commit (including after the evidence event): the intent
  stays `BLOCKED`; rerun with the same decision — the operation recollects
  the live evidence, tolerates its own prior evidence/refusal records and
  commits exactly once.
- Crash after the commit: the fold replays `HANDOFF_PUBLISHED revision 5`;
  the operation reports the committed proof idempotently with zero new
  commits and zero sends.
- A torn/malformed ledger line, an unexpected tail record, a drifted
  revision/state, another writer's active lease or a conflicting open intent
  on the same logical key refuses with no commit.

### 6. Preservation rules

- Original `adapter_digest`, `execution_binding`, `recovery_proof`,
  `creation_spec`, E, creation counts and every historical event remain
  byte-for-byte unchanged; only the namespaced publication section, the
  post-publication target baseline and the single commit are added.
- The old da99c11 reader refuses the new op (unknown-op fail-closed); roll
  back by stopping the executor, never by editing or removing ledger bytes.
- After recovery, `arm` / `trigger` still run the full fresh preflight,
  the exact O-note recheck (raw method) and the strict receipt gate; recovery
  itself never triggers.
