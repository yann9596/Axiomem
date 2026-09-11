# U12-R0B forward preflight — operator inputs and instructions (YZT-84)

This supersedes the operator path of `adapters/multica/u12-r0bi/OPERATOR_INSTRUCTIONS.md`
for live eligibility. The prior artifact remains in history byte-unchanged.
The adapter still performs no live call by itself: every runner is injected
explicitly. Do not execute any live step from this document until the Lead
separately activates the R0 canary under its existing single-dispatch
ownership.

## 1. What changed

`arm()` and every not-yet-issued `trigger()` now collect and validate fresh
materials immediately before entering the unchanged O2 single-attempt
issuance path:

1. current issue, complete comments, timeline, complete runs, plus a
   post-collection issue re-read;
2. every pinned artifact blob (usable root/reader mandatory) and the current
   authority record;
3. a fresh target-derived `current_request` and the current Finding source;
4. the exact bound note, the issue projection and the complete run listing.

Results are namespaced diagnostic ledger events (`r0b_preflight`) or typed
stops (`r0b_evidence_refused`). They are never reusable authorization.

- Confirmed drift / supersession / non-READY context:
  `REFRESH_REQUIRED` / `R0B_MATERIAL_STALE` (or the retained
  `ISSUE_REVISION_DRIFT` for incidental revision movement).
- Missing / unreadable / ambiguous input:
  `BLOCKED` / `R0B_MATERIAL_UNAVAILABLE` or
  `BLOCKED` / `R0B_PREFLIGHT_INPUT_MISSING`.
- Zero native reruns on every stop; a stop never republishes, refreshes or
  resets attempt state. Lead disposition is required.
- After durable `TRIGGER_ISSUING` / `TRIGGER_AMBIGUOUS` no fresh
  re-eligibility check ever runs; only the accepted O2 read-only
  reconciliation.

## 2. Required per-entrypoint inputs

| Input | Content | Source |
|---|---|---|
| `current_request` | `prepare_handoff_request` built from the **current** target snapshot through the same project mapping and decision-ref selection used for E | rebuild it from the live target issue (`task_ref` `multica://issue/<identifier>`, role `software-engineer`, project `{"project_id": "web-imagegen"}`) |
| `current_findings` | list of current Finding documents | `chandoff_plan.RuntimeFindingStore().load_open()` (the existing runtime Finding source) |
| `authority_evidence` | optional explicit evidence; required only when no `authority_reader` was injected | `python -B tools/u12_r0_binding.py authority-evidence --root <repo-root>` |

`current_request=None`, `current_findings=None` or a missing authority source
is a fail-closed `BLOCKED`; the adapter never substitutes an empty findings
list or a cached READY.

## 3. Concrete invocation

```python
from pathlib import Path
import chandoff_plan as plan
import chandoff_intent as o2
import u12_r0_binding as r0b

root = Path("D:/AI/worktrees/multica-memory-yzt-84-u12r0bi")
store = o2.DurableIntentStore(Path("D:/AI/multica-state/web-imagegen/dispatch/ledger.jsonl"))
factory = r0b.build_r0b_factory(
    store,
    runner=<explicit live runner>,                 # never implicit
    artifact_root=root,                            # pinned-blob reread
    artifact_blob_reader=None,                     # or an explicit reader
    authority_reader=r0b.ReadinessManifestAuthorityReader(root),
)
intent_id = "DI-<exact>"

current_request = build_current_request_from_live_target()   # same builder as E
current_findings = plan.RuntimeFindingStore().load_open()    # existing source

armed = factory.arm(intent_id, actor="<dispatch>",
                    current_request=current_request,
                    current_findings=current_findings)
# ... only after Lead activation: recollect before the trigger
issued = factory.trigger(intent_id, actor="<dispatch>",
                         current_request=current_request,
                         current_findings=current_findings)
```

When no reader is injected, read the CLI evidence and pass it instead:

```python
evidence = json.loads(subprocess.check_output([
    "python", "-B", "tools/u12_r0_binding.py",
    "authority-evidence", "--root", str(root)], text=True))
factory.arm(intent_id, actor="<dispatch>",
            current_request=current_request,
            current_findings=current_findings,
            authority_evidence=evidence)
```

## 4. Compatibility

- `plan_and_arm(intent_id, snapshot)`, `issue_trigger(intent_id, snapshot)`
  and the inherited `resume()` snapshot path **refuse** (`R0BDowngradeRefused`)
  for tagged R0B intents: caller-supplied snapshots (including
  `artifact_ready=True` booleans) cannot bypass the preflight.
- `mark_prepared` / `mark_published` remain refused for tagged intents.
- `recover()` remains instruction-only (`performed=false`): resume
  instructions are never reusable authorization; the resumed `arm`/`trigger`
  entrypoints always recollect the inputs above.
- Old unissued R0B records recorded by the prior adapter stop and require
  Lead disposition (the adapter digest changed; no downgrade).

## 5. Read-only verification

```bash
python -B tools/u12_r0_binding.py self-check
python -B adapters/multica/u12-r0b-forward/authority-reader-example.py
python -B adapters/multica/u12-r0b-forward/reproduce_preflight.py \
    --output adapters/multica/u12-r0b-forward/preflight-reproduction.json
python -B -m unittest discover -s tools/tests -p "test_u12_r0_binding.py"
python -B -m unittest discover -s tools/tests -p "test_u12_strict_receipt.py"
```

## 6. Hard stops

- No live create/assign/publication/trigger from this document; the Lead
  activates live R0 separately and keeps the bounded canary under
  single-dispatch ownership.
- Any `REFRESH_REQUIRED` / `BLOCKED` stop goes to the Lead; there is no
  automatic refresh, republish, retry or route change.
- No production-ledger write, no Canonical write, no 05/06, no O3, no Merge.
