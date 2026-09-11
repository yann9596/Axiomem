# U12-R0B Recovery Evidence Correction — Operator Instructions (YZT-84)

These instructions cover the corrected v1.1 recovery path for the exact legacy incident
(`DI-e5e2b856f6b7cd36` / YZT-85 `01a08f80-6afa-7d95-838a-6d510ec8f803`). They are written for the
Lead: 04 delivers the code, schema, fixtures and example; 04 does not issue the production
disposition and does not run live recovery. No command below has been run against production by 04.

**Candidate artifact under acceptance**

| Item | Value |
|---|---|
| Branch / code commit | `yzt-84-u12-r0b-forward-adapter` / `65a017e` |
| Adapter | `tools/u12_r0_binding.py` LF `sha256:40ccf07dd088d6a4077213714deef45c83f46116478ccc462c203ef7f07641fa` |
| Decision schema | `u12-r0b-create-recovery-decision/1.1` |
| Proof schema | `u12-r0b-recovery-proof/1.1` |
| Execution binding | `u12-r0b-execution-binding/1.1` |
| Contract | `U12-R0B/1.1`, `single-terminal-lf/1` (unchanged) |

## Step 0 — accept the exact bytes and the exact commit

The Lead accepts this artifact. The accepted execution commit is the 40-hex commit whose
`tools/u12_r0_binding.py` blob LF digest equals `sha256:40ccf07d...` (code commit `65a017e` at the time
of writing; if the Lead accepts a later commit containing the same verified adapter bytes, that commit's
resolved blob must still reproduce the digest). The operation refuses a missing/short/unaccepted/
unresolvable/mismatched identity; a same-content substitute commit is not accepted.

## Step 1 — audit the real ledger and capture the exact pins

```powershell
python -B tools/u12_r0_binding.py ledger-audit `
  --ledger D:/AI/multica-state/web-imagegen/dispatch/ledger.jsonl `
  --intent-id DI-e5e2b856f6b7cd36
```

The command is read-only (zero CLI commands, zero writes). It prints:

- `shared_history.records[]` — every record in the relevant interval with its `seq`, content digest and
  its retained `classification` / `reason` (expected intent history, uniquely correlated original create
  command/result, recognized reads, or a typed refusal);
- `shared_history.interval`, `audited_prefix` and `original_create` — the full original command/result
  records and the exact prefix;
- `suggested_disposition.ledger_prefix` and `suggested_disposition.original_create_pair` — the pins the
  disposition must carry.

The audit must show exactly one `ISSUE_CREATING`-class (`uniquely-correlated-original-create-command`)
command and its adjacent successful result, and no refused classification. If it reports a refusal, stop:
the real chain does not satisfy the correction contract and `CREATE_AMBIGUOUS` stays.

`ledger_prefix.digest` convention: SHA256 over the exact bytes of the first `length` newline-terminated
ledger lines. This is the same value `ledger-audit` prints; do not substitute a different file hash.

## Step 2 — issue the v1.1 disposition

Copy `recovery-decision-example.json` and replace every placeholder with the audited values:

| Field | Meaning |
|---|---|
| `decision_id`, `approval_ref`, `approved_by`, `approved_at` | your immutable acceptance record |
| `intent_id`, `expected_target_id`, `expected_intent_revision`, `expected_target_revision`, `expected_creator_id` | exact subjects and revisions (creator from the audit/issue evidence) |
| `predecessor_commit`, `predecessor_adapter_digest` | the unchanged known-predecessor pins |
| `design_ref`, `design_digest` | the accepted `U12_R0_CREATE_RECOVERY_DECISION.md` attachment and digest |
| `evidence_decision_ref`, `evidence_decision_digest` | the accepted evidence-correction decision |
| `original_receipt_body_status` | `not_persisted` **only** if no raw create receipt body exists; otherwise `persisted` |
| `receipt_limit_scope` | the exact constant `READ_ONLY_TARGET_IDENTIFICATION_FOR_UNPERSISTED_RECEIPT_BODY` when and only when the status is `not_persisted` |
| `ledger_prefix`, `original_create_pair` | copied verbatim from `ledger-audit` |
| `accepted_execution.commit`, `accepted_execution.adapter_digest` | the accepted repair commit and the adapter LF digest it resolves to |

Compute the self-digest (the operation recomputes and refuses a mismatch):

```powershell
python -B tools/u12_r0_binding.py recovery-decision-digest --decision-file <your-decision.json> --check
```

If a consistent original receipt body exists, keep it and pass it with `--receipt-file`; a conflicting or
wrong-target receipt refuses even when the readback looks valid.

## Step 3 — re-audit immediately before the append

Run Step 1 again (and verify the intent is still `CREATE_AMBIGUOUS`, revision 1, unbound). The operation
revalidates the audited prefix byte-for-byte and classifies the fresh tail under the same lease/CAS, so a
shared command appended after the audit refuses before binding — but the re-audit keeps the Lead's own
evidence current. Ownership stays single-dispatch.

## Step 4 — run the one recovery operation

```powershell
python -B tools/u12_r0_binding.py recover-created-target `
  --ledger D:/AI/multica-state/web-imagegen/dispatch/ledger.jsonl `
  --intent-id DI-e5e2b856f6b7cd36 `
  --target 01a08f80-6afa-7d95-838a-6d510ec8f803 `
  --decision-file <your-decision.json> `
  --execution-commit <accepted 40-hex commit> `
  --actor <your operator identity> `
  [--receipt-file <raw create receipt body.json>]
```

- Success: `status = TARGET_BOUND`, `side_effects = 0`,
  `next_action = RESUME_OWNERSHIP_NO_START`, and the new `proof_digest`. The only durable writes are the
  namespaced evidence event and the existing `CREATE_AMBIGUOUS -> TARGET_BOUND` edge.
- Refusal: `outcome = RECOVERY_REFUSED` with a typed `reason`, the state stays `CREATE_AMBIGUOUS`, the
  original history/pins are unchanged and no binding exists. `R0B_CREATE_RECOVERY_SHARED_HISTORY_UNRESOLVED`,
  `R0B_CREATE_RECOVERY_UNKNOWN_EFFECT`, `R0B_CREATE_RECOVERY_ORIGINAL_EVIDENCE_GAP` and
  `R0B_CREATE_RECOVERY_RECEIPT_DISPOSITION_REFUSED` are the correction's safety refusals — investigate the
  ledger, do not retry blindly.

Recovery performs **no** create/assign/comment/rerun. It binds only the same target and returns the next
action; ownership, fresh E/SELF_CHECK, publication, ARM/TRIGGER preflight and the single strict rerun are
separate operations with their existing gates.

## Crash semantics

- Crash before the evidence append: nothing changed; re-run after re-auditing.
- Crash after the evidence append, before the transition: the intent stays `CREATE_AMBIGUOUS`; re-run —
  the prior evidence event is recognised as this operation's own diagnostic and fully recollected, never
  reused as authorization. There is no second create.
- Crash after the transition before the acknowledgement: replay returns the same committed binding after
  revalidating the committed proof; it never creates or binds twice.
- Repeated/concurrent calls serialize under the lease; a competing target or different disposition
  refuses.

## Read-only checks the Lead can run at any time

```powershell
python -B tools/u12_r0_binding.py validate-intent --ledger <ledger> --intent-id DI-e5e2b856f6b7cd36
python -B tools/u12_r0_binding.py self-check
python -B adapters/multica/u12-r0b-recovery-evidence/reproduce_recovery_evidence.py --output <matrix.json>
```

The old `u12-r0b-recovery/`, `u12-r0bi/` and `u12-r0b-forward/` folders remain as historical evidence and
are not part of this invocation path.
