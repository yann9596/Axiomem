# O2_DURABLE_DISPATCH_INTENT_REPORT

- tool: `tools/chandoff_intent.py` O2/1.0 (+ `tools/o2_store_probe.py` capability probe)
- focused tests: `tools/tests/test_handoff_intent.py` (118 tests)
- branch: `yzt-79-o2-dispatch-intent`, based on exact U09 report commit `53f08407007ec081a888f4fb693575c35ae4aeff`
- status: **simulation_only** — no live issue/comment/assignment/rerun/status/run was created or mutated; no live rollout, formal Delivery Review/QA activation, U11 joint replay, U12 enablement, or merge was performed

## Verdict

Ready for Review. The accepted O2 model from YZT-78 is implemented as a forward-only adapter over the existing shared U06–U09 ledger: a durable append-only `dispatch_intent` transaction type with cross-process enumeration and single-writer CAS/lease, a status/assignee-aware trigger planner that selects exactly one native trigger, intent-first target create/bind, `TRIGGER_ISSUING` persisted before the native call, receipt + exactly-one-run correlation, read-only reconciliation with no automatic retry, a Lead exit invariant and deterministic observability. The confirmed YZT-77 lost-follow-up class and the YZT-77/YZT-78 backlog trigger-selection mismatch are both covered by executable fixtures. No platform-level exactly-once or autonomous liveness is claimed: O2 makes obligations durable and discoverable; it does not promise progress when every reconciler is absent.

## Human Authorization

- Human decision `使用方案C` on parent YZT-66 (comments `01a08e11-8987-7386-bc62-8cc0c145e083`, `01a08e17-eb25-77f5-b52c-ef6911070c2e`) authorizes **O2 only**. It does not authorize O3, live rollout, Review/QA activation, or merge; none of those were performed.
- Parent YZT-66 Authority Order, SAFE_DISPATCH, Artifact Contract, Finding/Challenge boundaries, Hard Stops, corrected stage order and Final Gate were applied in the stated order (Human decision → parent authority → YZT-78 assessment → U09 lineage/pins → current CLI/platform behavior).

## Preconditions

- Isolated worktree `D:\AI\worktrees\multica-memory-yzt-79-o2` created from exact U09 report commit `53f0840`; memory `main` (`95c434d`), prior U worktrees, Canonical Memory and `D:\AI\projects\opencode-web-imagegen` were not modified.
- Fresh isolated checkout of exact U09 reproduced the accepted 774-test baseline (**774/774 OK**, 38.9 s) before any change; exact upstream pins reproduce (guarded by the existing suites and by the O2 repo guard).
- T00/framework boundary scan (`tools/chandoff.py scan`) clean before and after; the new module is a Multica adapter (like `chandoff_dispatch.py`) and is deliberately not part of the framework-neutral scan set.
- Gate A valid, Gate B 10/10, Gate C 6/6 replays re-run at this HEAD (`migration/gate-results/*.json` refreshed).
- Accepted ledger surface inventoried: `chandoff_dispatch.TransactionLedger` is an in-memory list with whole-file `save()`/`load()` JSONL persistence — **no append primitive, no cross-process lock, no CAS/lease, no open-intent enumeration**. Deployed CLI contracts inventoried from live help: `issue assign` (`--to-id`, `--no-start`, `--unassign`), `issue rerun <id> --output json` (no idempotency key), `issue status <key> --no-start`, `issue runs --active/--siblings` (advisory, capped/truncating). `assign --no-start` help cannot state the `backlog` exception; this was treated as a documented platform behavior, not a trigger contract.
- U06 Assignment, U07 Mention, U08 fallback reconciliation, U09 Finding/Challenge integration and their pins are unchanged (byte-asserted).

## Capability Proof

The existing ledger surface alone could not satisfy O2 (process-local records; full-file overwrite; no single-writer claim). The approved O2 boundary allows extending the shared ledger itself, so the capability was **proved before the state machine was implemented**:

- `DurableIntentStore` writes the same JSONL record shape (`kind`/`record_type`/`seq`) that `chandoff_dispatch.TransactionLedger` already reads. Appends take an OS-level sidecar lock (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX), validate the whole ledger, assign a monotonic `seq`, then write + `fsync`. Readers take the same lock, so a torn concurrent read is impossible; a torn record on disk fails closed as `ledger_corruption` with no automatic repair.
- Per-intent leases use `claim/renew/release` records with owner token + expiry; every state transition is a compare-and-set append (`expected_revision`) and requires the caller to hold the active lease. Stale leases are reclaimable only on explicit expiry, with `previous_holder`/`expired_previous` evidence.
- Independent-process proof (`tools/chandoff_intent.py probe`, worker `tools/o2_store_probe.py`, different workdirs, one absolute ledger path) — `adapters/multica/dispatch-intent/capability-proof.json`, all 6 observations passed:
  1. `cross_process_append_enumerate` — append in workdir A, enumerate in workdir B, exactly 1 visible intent;
  2. `single_writer_race` — two reconciler processes claim + issue the same intent: exactly 1 winner, 1 `lease_held` loser, exactly 1 `TRIGGER_ISSUING` transition;
  3. `stale_lease_recovery` — expired claim recovered with `expired_previous: true`;
  4. `cas_conflict` — second transition on a stale expected revision fails `cas_conflict`;
  5. `duplicate_intent_fail_closed` — duplicate append rejected;
  6. `partial_record_fail_closed` — truncated line makes enumeration exit 4 `ledger_corruption`.
- Verdict: the foundation is available within the approved repository/adapter boundary. **No `O2_FOUNDATION_UNAVAILABLE` stop occurred**; O3 was not started and no service/database was built.

## Accepted Inputs

| input | pin |
| --- | --- |
| Parent YZT-66 | Authority Order, SAFE_DISPATCH, Artifact Contract, Finding/Challenge boundaries, Hard Stops, Final Gate |
| Human decision | `使用方案C` (YZT-66 comments `01a08e11-…`, `01a08e17-…`) — O2 only |
| U09 (base) | `53f08407007ec081a888f4fb693575c35ae4aeff` (runtime `sha256:efa29010…`, tests `sha256:12596bea…`, bundle `sha256:d598d2c5…`) |
| YZT-78 assessment | `multica://issue/YZT-78/comment/01a08def-5d47-7ea7-a01c-dc108fdfb587` (rev 9) |
| U08 | `6f541f715aa07d45f60c7c50e855952b64bcc9af` (`sha256:7884cfb6…` / `sha256:37de2a32…`) |
| U07 | `a8fcab19488f38d622ff731eb56eb14b39f8adb5` (`sha256:d3bba5b4…` / `sha256:ba52f615…`) |
| U06 | `f35afdf91133c3ea21432684fa62f34f0eae12c1` (`sha256:2d701541…`, dispatch `sha256:62dbd081…`) |
| U05 | `b43b68b5deae6d8636f02293f2abb52ecb5f65e3` (`sha256:a93e146d…` / `sha256:7f83bfd5…`) |
| U04 | `702cb9bb11120c154a216e66c6ed1d3d632d3b49` (`sha256:f369cee4…` / `sha256:7f861c32…`) |
| U10 | `73f922ea33c2e2867ba51b6843588c5aa4980ff6` (`sha256:9c2857ae…`) |
| U03 Frozen T00 | `2e1959b8b8297c13a3a9e5b5aa341b2154b8dc59` (not amended) |
| CLI evidence | live `issue assign/rerun/status/runs --help` captures; YZT-77/78 assignment + rerun recovery records |

## Historical Compatibility

- No predecessor file was modified or re-pinned: `chandoff_dispatch.py`, `chandoff_assignment.py`, `chandoff_mention.py`, `chandoff_fallback.py`, `chandoff_finding.py` and all U03–U09 evidence remain byte-identical (repo-guard tests recompute the accepted digests).
- Legacy terminal transactions remain readable and unchanged: the intent fold ignores non-intent records; a combined legacy+intent ledger still loads through `chandoff_dispatch.TransactionLedger.load` and the legacy command audit still passes. Absence of an intent on historical work is never inferred or retroactively repaired.
- U09 Finding/Challenge semantics are byte-unchanged (bundle digest `sha256:d598d2c5…` recomputed by the O2 tests).
- Nonterminal preassigned/backlog targets are inventoried **in fixtures only** (`target-inventory.json`: YZT-77, YZT-78, YZT-79). YZT-77/YZT-78 already have correlated recovery runs and are replayed as sanitized evidence; they were not re-dispatched or mutated.
- Rollback: disable creation/reconciliation of the new schema; retain append-only records and correlated run ids; route handoffs back to the accepted U06/U08 commands (`rollback-plan.json`).

## Architecture / State Machine

```text
INTENT_RECORDED → TARGET_BOUND → HANDOFF_PREPARED → HANDOFF_PUBLISHED
  → TRIGGER_READY → TRIGGER_ISSUING → RUN_CORRELATED → SELF_CHECKED → COMPLETED

typed stops: PARKED_NOT_DUE | REFRESH_REQUIRED | TRIGGER_AMBIGUOUS
             | CREATE_AMBIGUOUS | BLOCKED | CANCELLED
```

- Intent is persisted **before** any target create/update/assignment/status/comment/trigger. A create always carries the `intent_id` marker in its description, so a lost create response is resolvable by read-only child discovery (exactly one → bind; zero/many → `CREATE_AMBIGUOUS`, never a duplicate create).
- `TRIGGER_ISSUING` is written and fsynced **before** the native call. A missing/untrusted receipt becomes `TRIGGER_AMBIGUOUS` and is never automatically retried or re-routed. A trusted receipt with zero visible runs stays `TRIGGER_ISSUING` (bounded read-only wait); a later exactly-one-run proof attaches the run without another trigger. Duplicate/wrong/ambiguous runs fail closed.
- Provider/quota/cancellation after a run identity exists is recorded as `execution_recovery` against the correlated run; state stays `RUN_CORRELATED` and redispatch is structurally refused.
- Completed/terminal replay emits zero note, issue mutation, assignment, rerun, mention, status write, or new run (and zero reconciliation records).

## Intent Schema

`schema_version: O2-dispatch-intent/1.0`, record ops `recorded|transition|event|lease`; fields (per YZT-78):

```text
intent_id, schema_version, source_task_id, logical_task_key,
issue_id?, parent_issue_id, target_agent_id, target_role,
package_id, artifact_dependency_digest,
expected_issue_revision, expected_status_category, expected_assignee_id,
pretrigger_run_set_digest (+ pretrigger_run_ids at arm time), selected_trigger,
state, trigger_issued_at?, trigger_receipt_digest?, correlated_run_id?,
terminal_reason?, created_at, updated_at, creation_authority, provenance
```

Validation fails closed on unknown V2.2 role, retired identity (no alias), malformed intent/package/digest/grammar, missing authority/provenance, duplicate `intent_id`, and an open intent owning the same `logical_task_key`.

## Trigger Matrix

| precondition (immediately before the one trigger) | selected trigger | ownership binding | proof required |
| --- | --- | --- | --- |
| `backlog` + exact target assigned | `issue rerun` | none | READY note + trusted zero/unexpected-run snapshot + correlated returned run |
| `backlog` + unassigned/different | `issue rerun` | `assign --no-start` first (not a trigger), re-read, then rerun | post-binding assignee read + one correlated run |
| active + unassigned/different | `issue assign` | none | assignee-change receipt + exactly one correlated run |
| active + exact target assigned | `issue rerun` | none | same correlation rule |
| assigned `backlog` promoted to active | **rejected** (`STATUS_PROMOTION_ROUTE_REJECTED`) | — | route not independently proven in O2 |
| any / Mention route (U07) | native mention (own route) | none | native receipt; never combined with Assignment/rerun/status |

Every plan selects exactly one native trigger; `assign --no-start` is classified as `ownership_binding` and never counted as enqueue. A CLI success string is never success: `RUN_CORRELATED` is the only delivery state.

## Reconciler / Recovery

Read-only classification (implemented in `classify_reconciliation`, executed by `reconcile`/`resume`): pre-intent refusal; post-intent/pre-create kill (safe resume create); ambiguous create (read-only discovery attach / inconclusive stop); post-bind/pre-prepare; post-prepare/pre-publish (reuse exact note); post-publish/pre-trigger (`RESUME_ISSUE`, no issuance marker exists); trigger-call lost response (`TRIGGER_AMBIGUOUS`, no reissue); delayed run visibility (`WAIT_READ_ONLY`, later attach); run correlated/pre-SELF_CHECK; provider/quota failure (`EXECUTION_RECOVERY`, no redispatch); parent-wake failure (repair the run; at most one new wake after proving no active/pending Lead run, then exhausted); completed/terminal replay (zero side effects). Only attach actions mutate state automatically; anything without a unique safe action returns the classification plus the exact next owner (`source-run` or `engineering-lead`).

## Lead Exit Invariant

```text
every target created/touched by the source run
  ∈ PARKED_NOT_DUE(dependency + next owner + explicit wake/decision boundary)
  OR intent.state ∈ {RUN_CORRELATED, COMPLETED, REFRESH_REQUIRED,
                     TRIGGER_AMBIGUOUS, BLOCKED, CANCELLED, PARKED_NOT_DUE}
```

`lead_exit_check` returns `ok` plus the exact violations and the two DoD counters. A due target with no intent (or an in-progress state such as `TRIGGER_READY`/`TRIGGER_ISSUING`/`CREATE_AMBIGUOUS`) blocks; a parked claim without all three fields blocks. No daemon, scheduler, autopilot or autonomous wake is added.

## Observability

`observe(store, now)` returns deterministic open-intent rows: `intent_id`, age, state, issue, target role/agent, selected trigger, receipt digest, correlated run id, ambiguity reason, lease holder/expiry/active, last reconciliation (classification/action), exact next owner and next action, plus state counts and a stable digest. Expired leases stay visible as `expired`; terminal intents are excluded from the open list but counted by state.

## Exact Revisions / Digests

| artifact | pin |
| --- | --- |
| base | U09 `53f08407007ec081a888f4fb693575c35ae4aeff` |
| feature commit | `c24284a` |
| `tools/chandoff_intent.py` | `sha256:0544046fa2ca97c12e6c48de574074df9de5715a950ad75a783cf31853037032` |
| `tools/o2_store_probe.py` | `sha256:b9cf1d3ac0cb95bd15c0ac02c29a969874fad430b269fac14421719f6b726922` |
| `tools/tests/test_handoff_intent.py` | `sha256:649580b22055202280a670889e0a8d5ec5b93a51d2a90ce8dc094d268502923d` |
| evidence bundle (16 files, `adapters/multica/dispatch-intent/`) | `sha256:3c207e85f195617fe50914e3384dd309e5847d4c6e55f33a96f328bae0924d4d` |
| `capability-proof.json` | `sha256:c0bb429b5f7ddf88227d15e88d3eb60ede91e41a129bed722d91266f828002c1` |
| Gates at this HEAD | A valid; B 10/10; C passed (6/6 replays) |
| Memory / Registry / Role Profile | `sha256:30b51dea…` / `sha256:a08e20eb…` / `sha256:7b3bdf52…` (unchanged) |

Digest method for source files: raw bytes with CRLF normalized to LF (accepted U08/U09 convention). Bundle method: canonical JSON of sorted `file name → sha256(bytes)`.

## Changes

- Added `tools/chandoff_intent.py` (O2/1.0): durable intent store (append/lock/lease/CAS/fold), trigger planner, orchestrator, reconciler, Lead exit invariant, observability, YZT-77/78 fixtures, matrices and evidence CLI.
- Added `tools/o2_store_probe.py`: capability-probe worker for the cross-process proof.
- Added `tools/tests/test_handoff_intent.py` (118 focused tests).
- Added the deterministic evidence bundle under `adapters/multica/dispatch-intent/`; added its byte-stability entry to `.gitattributes`.
- Refreshed `migration/gate-results/*.json` at this HEAD (`generated_at` and the replay run only).
- No T00 schema, T01/T04B, U04–U10 runtime, Canonical Memory, or product-repository file was modified.

## Tests / Replays

- Focused O2: **118/118 OK**. Full `tools/tests` (`unittest discover`): **892/892 OK** (774 exact-U09 baseline + 118 new). T00 scan clean.
- Capability/replay coverage: cross-process append/enumerate, two-reconciler race, stale lease, CAS conflict, duplicate intent, duplicate logical key, partial/corrupt record (unit + subprocess), lease ownership enforcement, illegal transitions, snapshot exactness, matrix rows (backlog/active × assignee), status-promotion rejection, mention route + route conflicts, intent-first create ordering, ambiguous create (stop + one-target discovery + two-candidate refusal), publication idempotence and duplicate-note refusal, package/assignee/revision drift → refresh, one-trigger enforcement, `TRIGGER_ISSUING`-before-call ordering, assignment success without run ≠ success, delayed visibility, lost response, duplicate/wrong/pre-existing/untrusted runs, provider failure execution recovery, self-check/completion, completed replay zero side effects, parent-wake budget, exit invariant (due untracked, underspecified parked, accepted states), observability fields, legacy ledger compatibility, YZT-77/78 fixture replays, evidence-bundle byte regeneration, repo/pin guards.
- Required-replay mapping: pre-intent kill; post-intent/pre-create kill; ambiguous create; post-prepare; post-publish/pre-trigger; trigger-call lost response; delayed run visibility; two reconcilers racing; backlog preassigned; backlog unassigned; active different assignee; active same assignee; status/assignee drift; stale package/artifact; exactly one returned rerun id; run created then provider quota; failed parent wake; completed replay — all covered by focused tests plus the capability proof.

## YZT-77 / YZT-78 Fixture Evidence

Sanitized captures (no secrets, no outputs, no executable trigger content) in `fixtures/`; replays in `samples/` and `fixture-replays.json`:

- **YZT-77** (persistent orphan; same-assignee Assignment no-op on `backlog`; then rerun recovery `01a08ddc-7698-7c18-943b-f354ee6ec8fb`): replay selects exactly one `issue_rerun` with no ownership binding, no assignment replay, state `RUN_CORRELATED`, 1 write-class command, audit `ok: true`.
- **YZT-78** (assignee-changing Assignment on `backlog`; then rerun recovery `01a08de0-e0cb-7adc-890e-c87fcadce826`): replay binds ownership with `assign --no-start` (not a trigger), re-reads, then one `issue_rerun`; 2 write-class commands total, 1 trigger, audit `ok: true`.
- Both fixtures replay with a canned runner and mutate nothing live; the historical Assignment attempts are asserted never to be replayed.

## Side-Effect Audit

- Live target/platform mutations or triggers from this task: **0** (only read-only `issue get`/`issue runs`/`issue children`/`--help` captures; producer-run live creates/updates/assignments/status/reruns/mentions: 0).
- Duplicate note / trigger / correlated run: **0 / 0 / 0** (`side_effect_audit` on both sample ledgers: `ok: true`).
- Canonical writes: 0. Product-repository changes: 0. Frozen T00 amended: false. Predecessor history rewrites: 0. U09 pin/byte change: 0. Unaccounted findings: 0.
- U11 joint replay: not performed. U12 enablement: not performed. Merges: 0.

## Finding Drain

Unaccounted open findings at completion: **0**. No new blocking Finding was produced. The four U09-deferred findings remain deferred to their recorded gates (`PRE_U12_PIN_GATE` ×2, `U12_ENABLEMENT`, `U11_JOINT_REPLAY`); O2 changes none of their classifications.

## Deviations

1. O2 is delivered as a **new forward adapter** (`tools/chandoff_intent.py`) instead of editing `chandoff_dispatch.py`: the accepted predecessor files and pins are immutable history, and the shared-ledger JSONL contract is consumed unchanged (an explicit requirement). No second hidden store is created — intent records live in the same shared ledger format and legacy readers accept them.
2. Added `CREATE_AMBIGUOUS` as its own typed stop (exit-blocking) rather than overloading terminal `BLOCKED`, so an exactly-one-target read-only discovery can still attach and bind later.
3. Provider/quota failure is recorded as an `execution_recovery` event while the intent stays `RUN_CORRELATED` (matching "recorded against the correlated run, not by reopening trigger issuance") instead of a dispatch-terminal state.
4. The status-promotion route is explicitly rejected rather than implemented, as the minimum implementation permits.
5. `rerun` receipt parsing accepts a run object, a one-element run list, or `{"runs":[...]}`; the deployed `rerun` output schema is not frozen and cannot be live-verified from this task, so correlation still requires the trusted `issue runs` listing, not the receipt alone.

## Risks

- `issue rerun` idempotency under a lost response remains undocumented; O2 only guarantees it never auto-retries and reconciles read-only.
- The proof demonstrates single-writer/CAS on one machine and filesystem (Windows `msvcrt`/POSIX `flock`, append+fsync). Deployment of the canonical shared ledger root is a rollout decision (U12); a filesystem that does not honor OS locks would need re-proving.
- O2 cannot wake itself: a hard kill with no subsequent reconciler invocation leaves a durable, visible obligation, not progress.
- Ambiguous issuance and parent-wake failures end in manual Lead decisions; O2 makes them typed and auditable but not automatic.
- The evidence bundle's capability proof and fixture replays are simulated; live effectiveness remains U11's joint-replay evidence.

## Blockers

None for O2 acceptance. U11 (joint replay + live closed-source guard wiring) and U12 (enablement) remain parked and were not started.

## Recommended U11 Decision

Lead: accept this O2 foundation, keep U09 and all predecessor pins immutable, and route U11 to jointly replay these fixtures over the real lineage (backlog/same-assignee, backlog/different-assignee, active/unassigned, active/same, ambiguous issuance, delayed visibility, provider failure, parent wake) and to decide the production ledger root and the `rerun` receipt contract. Keep live 05/06 disabled and U12 parked.

## Ready for Review

Yes. Review level R2 foundation prerequisite only; return to Engineering Lead for acceptance. Formal Delivery Review/QA and U11/U12 activation are not started by this issue.
