## U12-R0B Publication Recovery Correction — Operator Instructions (v1.1)

These instructions cover the corrected, proof-bearing recovery of the single
`DI-e5e2b856f6b7cd36` publication incident
(`BLOCKED revision 4 → HANDOFF_PUBLISHED revision 5` plus the execution-identity
migration). The Lead disposition, the accepted execution identities and the
current Finding source are mandatory. The operation performs read-only platform
access, at most one bounded evidence event and exactly one ledger commit; it has
**no** note-publish, create, assign, status, rerun, ARM or TRIGGER capability.

### 1. Inputs the Lead must produce

1. **The disposition** (`--decision-file`), schema
   `u12-r0b-publication-recovery-decision/1.1`, generated from the
   authenticated production ledger audit. It must contain:
   - the exact incident objects: `intent_id`, `expected_target_id`,
     `expected_intent_revision` (4), `expected_blocker` (transition seq/digest,
     reason `PUBLICATION_PROVENANCE_INCOMPLETE`, evidence code
     `PUBLICATION_NOTE_NOT_FOUND`, evidence event seq/digest),
     `publication_attempt` (seq/digest/operation_id), `expected_note`
     (comment id/revision/created_at/updated_at/author/source run/parent plus
     the raw R/O digests);
   - `original_create_recovery` (decision/proof/execution-binding digests and
     accepted commit/adapter);
   - **`source_activation`** (v1.1): `parent_issue_id`, the unique Lead-authored
     activation `comment_id`/`author_id`/`author_type` and its
     `comment_content_raw_digest`, the resolution and request
     `*_attachment_id`/`*_raw_digest`, and the exact E `package_id`,
     `envelope_digest` and `task_fingerprint`. The recovery downloads those
     attachments through the authenticated CLI, re-hashes them, re-reads the
     parent activation comment, reconstructs the request from the freshly read
     target body plus the accepted explicit decisions and compares it verbatim
     with the bound E request — a nonexistent, duplicate, edited, wrong-author
     or nonmatching record is a typed refusal.
   - `execution_migration` (new commit/tree, adapter raw+LF, store/note file
     digests, artifact dependency digest);
   - the fixed authority refs, the single-purpose scope, the trigger policy
     and the decision self-digest.

   The approval authority content is verified against adapter-pinned author and
   content digests of the accepted Human approval comment and the Lead boundary
   comment; a fixed reference string or a caller-supplied digest alone is never
   authority.

2. **The accepted execution identities** (`--accepted-execution-file`) must
   equal `decision.execution_migration` verbatim.

3. **The current Finding source** (`--findings-file`) — required; the adapter
   never substitutes an empty list.

### 2. Invocation

```
python -B tools/u12_r0_binding.py recover-blocked-publication \
  --ledger <production-ledger.jsonl> \
  --intent-id DI-e5e2b856f6b7cd36 \
  --decision-file <lead-disposition.json> \
  --accepted-execution-file <accepted-execution.json> \
  --findings-file <current-findings.json> \
  --actor <exact-lead-actor> \
  [--artifact-root <repo-root>] [--authority-root <repo-root>] \
  [--executable multica]
```

The legacy `u12-r0b-publication-recovery-decision/1.0` /
`.../proof/1.0` / `.../migration/1.0` payloads are **not** accepted by the
v1.1 loader (no silent upgrade); the previous `ac1e5b4` adapter refuses every
v1.1 payload. Both fences are required and tested.

### 3. Refusal meanings (state stays BLOCKED)

| Outcome | Meaning |
|---|---|
| `PUBLICATION_PROVENANCE_INCOMPLETE` | the resolved source activation, authority content, note/attempt/blocker identity or shared history does not reproduce; a projection/raw-response mismatch, relabelled shared command, duplicate/nonexistent/edited record or nonmatching attachment is refused |
| `R0B_MATERIAL_STALE` | the fresh target body no longer reproduces the accepted E request, the frozen fingerprint/built_from drifted, or the SELF_CHECK on the reconstructed request is not READY/USE_EXISTING |
| `R0B_PREFLIGHT_INPUT_MISSING` | the Finding source is missing |
| `PUBLICATION_DELTA_NOT_ATTRIBUTABLE` | comments/issue/timeline changed around the publication beyond the single observed note |
| `EVIDENCE_REVISION_MOVING` | the second complete read is not stable |
| `R0B_VALIDATION_REFUSED` (raised) | the disposition itself is edited, old-schema/hash-only, or the accepted execution input contradicts it; nothing is read or committed |

Every refusal is zero-native-effect: no commit, no evidence append beyond the
typed refusal event, no note resend, no trigger.

### 4. Crash / replay semantics

- **Crash before the commit**: the intent stays `BLOCKED revision 4`; the same
  decision can be re-submitted and the complete evidence is re-collected.
- **After the commit**: replaying the operation re-runs the shared pure
  semantic verifier over the committed proof (`replay=True`), returns the
  existing proof and reports `ledger_commits=0`, `platform_writes=0`,
  `notes_sent=0`, `triggers_issued=0`.
- **Restart**: the reducer re-folds the single `publication_recovery_commit_v1`
  record through the same `validate_publication_recovery_commit_record`
  predicate; a tampered proof fails closed as `LedgerCorruptionError`.
- A second competing commit, a different decision on replay, a held lease, a
  CAS/prefix/tail mismatch or a conflicting open logical intent all refuse
  without a commit.

### 5. Evidence hygiene

`publication-recovery-decision-example.json` and
`accepted-execution-example.json` are **local-fixture schema examples**, never
live evidence. Every live value must come from the authenticated production
ledger audit; a missing or mismatching value stops as
`PUBLICATION_RECOVERY_EVIDENCE_INCOMPLETE` and keeps BLOCKED. Recovery stops at
`HANDOFF_PUBLISHED`; arm, trigger and the final Merge remain separate
Lead/Human decisions.
