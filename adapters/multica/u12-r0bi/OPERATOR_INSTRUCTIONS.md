# U12-R0BI Operator Instructions — Forward Lifecycle Binding Adapter

Scope: operating `tools/u12_r0_binding.py` for a single tagged R0B intent.
This is pre-enablement tooling. Live R0 activation is an Engineering Lead
decision; these instructions do not authorize it. Every write below goes
through an explicitly injected `multica` runner; the adapter never constructs a
live runner implicitly and never touches the production ledger by itself.

## 0. Preconditions

1. The strict gate and the R0B contract are intact:
   `python tools/u12_r0_binding.py self-check` → `ok: true`.
2. The predecessor pins match:
   `python tools/u12_r0_binding.py artifact-digest` → the entry map contains
   `fcf63534…` / `649c…` values listed in `implementation-manifest.json`.
3. The operator has the exact inputs below; anything missing is a stop.

## 1. Required invocation inputs

| Input | Meaning |
|---|---|
| ledger path | the existing `DurableIntentStore` JSONL (never a second store) |
| `creation_context` | C: real dispatcher `task_ref`, role `engineering-lead`, frozen `result/request/self_check`, `source_task_id` |
| `creation_spec` | approved title/body(bytes), parent, project, logical key, 04 mapping, `backlog`, `publisher_agent_id`, marker, body digest, authority refs+digests, artifact dependency entries+digest |
| `execution_context` | E: real returned `multica://issue/<identifier>`, role `software-engineer`, frozen `result/request/self_check` |
| `publisher_run_id` | the source run that will own the `/note` publication (its platform `source_task_id`); must be known before publishing |
| `prepared_by` / `prepared_at` | T06 metadata; `prepared_at` is fixed and reused by the confirmation |

## 2. Normal forward path (one operator session)

```python
import chandoff_intent as o2
import u12_r0_binding as r0b

store = o2.DurableIntentStore(LEDGER_PATH)
factory = r0b.build_r0b_factory(store, runner=explicit_multica_runner,
                                artifact_blob_reader=git_blob_reader_or_none)

factory.record_creation_intent(creation_context=C, creation_spec=SPEC,
                               authority=AUTHORITY, actor=ACTOR,
                               source_run=SOURCE_RUN, intent_id=INTENT_ID)
factory.create_target_once(INTENT_ID, actor=ACTOR)          # exactly one create
factory.assign_ownership_once(INTENT_ID, actor=ACTOR)       # --no-start only
factory.bind_execution_package(INTENT_ID, execution_context=E, actor=ACTOR)
factory.publish_handoff_once(INTENT_ID, actor=ACTOR,
                             publisher_run_id=PUBLISHER_RUN,
                             prepared_by=LEAD_NAME, prepared_at=FIXED_AT)
factory.arm(INTENT_ID, actor=ACTOR)      # TRIGGER_READY, rerun route only
factory.trigger(INTENT_ID, actor=ACTOR)  # strict receipt gate, one attempt
```

Checks to perform between steps: each call returns `side_effects: 0` on
refusals and a typed `status`; any non-progress status is a stop. Never edit
ledger records by hand.

## 3. Safe resume (after a crash or a lost response)

```python
factory.recover(INTENT_ID, actor=ACTOR)                 # read-only classification
factory.recover(INTENT_ID, actor=ACTOR, allow_create=True)  # only if RESUME_CREATE
```

| Reported action | Meaning | Allowed next call |
|---|---|---|
| `RESUME_CREATE` | durable intent, no create-issuing marker | one `create_target_once` |
| `RESUME_OWNERSHIP` | target bound, no ownership attempt ever recorded | one `assign_ownership_once` |
| `RESUME_PREPARE` | ownership proven | `bind_execution_package` with fresh E |
| `RESUME_PUBLISH` | E bound, no publication attempt | `publish_handoff_once` |
| `RESUME_ARM_AND_TRIGGER` | publication bound | `arm` then `trigger` after a fresh read |
| `RESUME_TRIGGER` | armed, no issuance | `trigger` |
| `READ_ONLY_RECONCILE` | trigger issuing/ambiguous | accepted O2 read-only reconciliation only |
| `LEAD_DISPOSITION` | changed package/artifact or typed stop | stop; Lead decides |

Unknown/ambiguous windows: the call returns a typed stop
(`CREATE_AMBIGUOUS`, `OWNERSHIP_UNPROVEN`, `PUBLICATION_AMBIGUOUS`,
`PUBLICATION_PROVENANCE_INCOMPLETE`, `PUBLICATION_DELTA_NOT_ATTRIBUTABLE`).
None of these is retried, republished or rerouted.

### Uncertain publication specifically

1. A publication attempt is durable before the external call. If the call's
   outcome is unknown, the intent stays `HANDOFF_PREPARED` and any further
   `publish_handoff_once` returns `PUBLICATION_ALREADY_ATTEMPTED`.
2. Run `factory.recover(INTENT_ID, actor=ACTOR)` → it calls
   `confirm_publication_and_bind`, which can only read:
   - exactly one matching note → `HANDOFF_PUBLISHED` at the observed revision;
   - zero/duplicate/edited/incomplete evidence → `BLOCKED`, manual Lead
     investigation (no second T06 call, ever).

## 4. Read-only probes

```text
python tools/u12_r0_binding.py probe-evidence --issue <issue-id>
python tools/u12_r0_binding.py validate-intent --ledger <ledger.jsonl> --intent-id DI-...
python tools/u12_r0_binding.py artifact-digest [--entries-file map.json]
python tools/u12_r0_binding.py self-check
```

`probe-evidence` reports the deployed CLI shape and the exact coverage gaps
(currently `timeline.revision_linkage`), never a PASS claim.

## 5. Forbidden operations (fail closed)

- Never call `mark_prepared` / `mark_published` on a tagged R0B intent (they
  raise `R0BDowngradeRefused`); never patch ledger fields directly.
- Never re-run create/assign/publication/trigger because "state still looks
  early"; only `recover` decides.
- Never supply a fabricated target ID, a placeholder package, or a
  `publisher_run_id`/artifact digest that cannot be verified. A changed
  adapter byte or missing `r0_binding` data is a stop, not a downgrade.
- Never widen the create form beyond the literal `--status backlog`; never use
  assignment/mention/status as a trigger; never retry after uncertainty.
