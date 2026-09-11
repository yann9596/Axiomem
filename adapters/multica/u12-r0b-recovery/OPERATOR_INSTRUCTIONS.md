# U12-R0B Create Recovery — Operator Instructions (YZT-84)

Bounded forward repair of the first live R0 create that stopped at
`CREATE_AMBIGUOUS` because the platform readback lost exactly one terminal LF.
**This document does not authorize the live recovery.** Lead acceptance of the
exact repair bytes and a concrete recovery disposition remain prerequisites.

## 1. What the operation does and does not do

`recover_created_target` binds the one already-created target to the existing
production intent through the existing ledger:

- transition `CREATE_AMBIGUOUS -> TARGET_BOUND` (no new intent, no reset, no
  second create, no field rewrite of the preserved record);
- appends `r0b_recovery_evidence` (the immutable proof) before the transition
  and `r0b_recovery_bound` after it;
- stores an execution fence (`U12-R0B/1.1` contract version + execution
  binding) that the predecessor adapter refuses;
- returns `TARGET_BOUND` plus `next_action = RESUME_OWNERSHIP_NO_START`.

It has no create/assign/comment/rerun capability. Every external command on
this path is a read (`issue children`, `issue get`, `issue comment list`,
`issue timeline`, `issue runs`). A refusal never changes the intent state; it
appends a bounded `r0b_evidence_refused` diagnostic and returns
`outcome = RECOVERY_REFUSED`.

## 2. Recovery disposition schema (exact, self-digested)

The disposition is immutable and self-digested:

```json
{
  "schema": "u12-r0b-create-recovery-decision/1.0",
  "decision_id": "<lead-issued decision id>",
  "disposition": "RECOVER_CREATED_TARGET",
  "scope": "CREATE_AMBIGUOUS",
  "intent_id": "DI-e5e2b856f6b7cd36",
  "expected_target_id": "01a08f80-6afa-7d95-838a-6d510ec8f803",
  "expected_intent_revision": 1,
  "expected_target_revision": 1,
  "expected_creator_id": "24f04aba-7da9-4371-bf89-685d7505a411",
  "predecessor_commit": "b49630b881170f7e6f40ffe61a82687492b99792",
  "predecessor_adapter_digest": "sha256:8a9b75628f1263004ab214c96733d1b775399cdd6ffe5208fdaf4c0ea5abbc05",
  "design_ref": "attachment/01a08f89-53bc-7bb0-bda2-1bf86c40be7f",
  "design_digest": "sha256:1fedade6694d6d15f076a1ecb84aedfa680c93767cd3ec27ac94b594220b101e",
  "approval_ref": "<exact Lead/Human approval reference>",
  "approved_by": "<approving identity>",
  "approved_at": "<RFC3339 timestamp>",
  "decision_digest": "<sha256 of the canonical object without this field>"
}
```

Rules enforced by the adapter:

- every field above is required; unknown fields refuse;
- `predecessor_commit`, `predecessor_adapter_digest`, `design_ref` and
  `design_digest` must equal the exact accepted constants;
- `intent_id` and `expected_target_id` must equal the operation arguments, and
  `expected_intent_revision` / `expected_target_revision` the recorded values;
- `decision_digest` must reproduce the canonical self-digest.

Compute the digest before committing the disposition:

```
python -B tools/u12_r0_binding.py recovery-decision-digest \
  --decision-file <decision.json>
# add the printed "decision_digest" to the file, then verify:
python -B tools/u12_r0_binding.py recovery-decision-digest \
  --decision-file <decision.json> --check
```

## 3. Exact live invocation (Lead only, after acceptance)

Read-only pre-checks (all safe, no writes):

```
python -B tools/u12_r0_binding.py authority-evidence --root <accepted checkout>
python -B tools/u12_r0_binding.py transport-relation \
  --source-file <original-source-body.txt> --observed-file <live-readback.txt>
```

Recovery:

```
python -B tools/u12_r0_binding.py recover-created-target \
  --ledger D:\AI\multica-state\web-imagegen\dispatch\ledger.jsonl \
  --intent-id DI-e5e2b856f6b7cd36 \
  --target 01a08f80-6afa-7d95-838a-6d510ec8f803 \
  --decision-file <accepted-decision.json> \
  --actor <lead-agent-id> \
  --artifact-root <accepted repair checkout root> \
  --execution-commit <accepted repair commit> \
  --executable multica
```

Exit codes: `0` = bound or replayed; `1` = typed refusal (state unchanged);
`2` = structural contract/pin/disposition refusal.

Prerequisites revalidated under the intent lease before any append: full
original chain, original spec/context/authority/artifact digests, exact known
predecessor pin, exactly one durable create attempt, exactly the
`INTENT_RECORDED -> CREATE_AMBIGUOUS` transition, complete parent-child
discovery with exactly one standalone marker/Intent candidate, exact target
id/parent/title/project/priority/creator/revision/backlog/unassigned fields,
empty comments and runs, exactly one creator `created` activity, stable issue
re-read, current pinned artifact bytes and current READY authority, and the
exact directional single-terminal-LF relation between the persisted source and
the live readback.

## 4. Crash / replay semantics

| Crash point | Durable state | Re-entry behaviour |
|---|---|---|
| before `r0b_recovery_evidence` | `CREATE_AMBIGUOUS` | fresh evidence is recollected; a new proof is built |
| after evidence, before transition | `CREATE_AMBIGUOUS` | fresh evidence recollected; a second evidence event is appended, then one binding |
| after transition, before `r0b_recovery_bound` | `TARGET_BOUND` | read-only replay returns the same binding; no second bind/create |
| after acknowledgement | `TARGET_BOUND` | read-only replay; byte-identical ledger |

Concurrent callers serialize on the intent lease; the loser either replays or
gets a typed `lease_held` refusal with zero external calls. A different target
or a different disposition digest refuses as competing proof.

## 5. Stop conditions

Stop and return to Lead (no retry, no improvisation) when: any prerequisite
refuses; the live target moved; comments/runs/timeline show an unexplained
mutation; artifact/authority is stale; the marker identity is missing or
ambiguous; or the on-disk record does not match the accepted predecessor pin.
`CREATE_AMBIGUOUS` is preserved in every refusal.

## 6. After recovery (separate, unchanged lifecycle)

Only after a successful binding:

1. one ownership `issue assign ... --no-start` through
   `assign_ownership_once`;
2. fresh real-target E through `bind_execution_package`;
3. one publication through `publish_handoff_once` +
   `confirm_publication_and_bind` (read-only confirmation on uncertainty);
4. `arm` and then `trigger` — each recollects fresh material/authority/
   Findings and fails closed on drift; exactly one strict-gated rerun;
5. read-only run correlation.

YZT-85's immutable worker verification subject remains `b49630b...`; the
recovered dispatcher bytes are the separately recorded execution identity.
Never claim YZT-85 references the new dispatcher bytes.

## 7. Point-in-time live observation (not authorization)

Read-only inspection at repair time: production ledger readable, 37,860 bytes,
`sha256:5a7e3369fb7544668f1ba45d8db4e01707228977ac45e4d60318677432b09d76`;
intent `DI-e5e2b856f6b7cd36` in `CREATE_AMBIGUOUS` at revision 1 with exactly
one `r0b_create_issuing` event and zero ownership/publication events. Lead must
re-audit the chain and the sole attempt immediately before the live recovery;
this observation is not reusable authorization.
