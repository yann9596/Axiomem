# T09 — Assignment Handoff Main Path (YZT-64)

- branch: `yzt-64-assignment-main-path` @ `d6d1947` (exact base `yzt-63-agent-instruction-contract@2b41e82`)
- owner: 02 Context Engineer; status: **staged, simulation-only — zero live mutations**
- new code: `tools/chandoff_dispatch.py` (dispatch CLI boundary + transaction ledger),
  `tools/chandoff_assignment.py` (state-machine orchestrator + CLI),
  `tools/tests/test_handoff_assignment.py` (43 focused tests)
- frozen inputs untouched: T00–T08 schemas/modules, T07 Skill, T08 bundle, docs §34

## Strict order implemented (§34.2)

```text
INIT → ISSUE_CREATED → TARGET_RESOLVED → HANDOFF_PREPARED → HANDOFF_PUBLISHED
     → HANDOFF_READY_CONFIRMED → ASSIGNMENT_TRIGGERED → TARGET_SELF_CHECKED → COMPLETED
```

| step | owned by | guarantee |
| --- | --- | --- |
| 1 create unassigned issue | `DispatchCli.create_issue` | argv allowlist: `--title` + `--description-file` (UTF-8, cwd, deleted after) only; `--assignee/--assignee-id/--attachment*/inline/stdin/--allow-external-file/--allow-duplicate/--status/--stage` refused; any `mention://` in title/description refuses before a process starts |
| 2 canonical id | create response contract check | id/identifier/title validated; assignee must be null; id is never guessed; non-JSON response → `CREATE_RESPONSE_INVALID`; exit≠0 → `CREATE_UNVERIFIED` (never blind-retried) |
| 3 resolve target role | `resolve_target` over T08 bundle | exact slug/display-name match against the frozen T08 role table; role must be bound in the T08 binding plan; baseline agent identity must exist; `context-engineer` (T08 exception-path role) is never a dispatch target; unknown/stale → `ROUTING_REQUIRED` (Engineering Lead/Squad), no guessed agent |
| 4 PREPARE_HANDOFF | T05 `build_snapshot_request` (CLI mode) → T01 `prepare_handoff_plan` → injected `compose_fn` → T02 `compose_semantic` (one bounded same-PLAN repair) → T03 `finalize_handoff` | the accepted T07 pipeline order over the exact created issue/task/role; orchestrator copies no policy; BLOCKED → `PREPARE_BLOCKED`; PARTIAL stops (`PREPARE_PARTIAL_STOPPED`) unless an explicit caller policy decision is recorded in the ledger with gaps visible; compose double-rejection → `COMPOSE_REJECTED` |
| 5 publish non-trigger note | T06 `publish_handoff` (via `NoteCli`) | exactly one `issue comment add --content-file`; `/note` body; PARTIAL published only under the recorded policy authorization |
| 6 confirm HANDOFF_READY | T06 `resolve_latest_handoff` | the just-published note must re-resolve as the newest valid record with matching package_id + comment id + task_ref + role; any mismatch/stale/newer-foreign-package → `CONFIRMATION_FAILED`, no assignment |
| 7 assignment trigger | `DispatchCli.assign_issue` | the sole run trigger: one `issue assign <id> --to-id <uuid>`; `--no-start/--unassign/--to` refused; exit≠0 → `TRIGGER_COMMAND_FAILED`; non-JSON or marker-less response → `TRIGGER_CONFIRMATION_REQUIRED`; never retried — reconciliation is the read-only `reconcile_trigger` operator action |
| 8 target SELF_CHECK | T04 `self_check_with_trace` on the confirmed envelope | READY → work gate; REFRESH_REQUIRED → one bounded refresh (re-snapshot → re-plan → re-compose → re-finalize; publish only when `built_from` actually changed); second unresolved refresh → `SELF_REFRESH_EXHAUSTED`; BLOCKED → `SELF_CHECK_BLOCKED` |

## Idempotency / ledger

- `TransactionLedger`: deterministic JSONL records (argv + command class + exit code
  + state transitions + outcomes; no comment bodies, no secrets). Every argv flows
  through one `RecordingRunner`, so the ledger is the ordered evidence of the whole path.
- Replay: a recorded `COMPLETED` result for a `transaction_id` is returned as-is with
  **zero new commands**; a recorded incomplete result refuses (`REPLAY_REFUSED`) — no
  blind retry of create/publish/assign.
- Exactly-once is claimed **only** against this observable ledger evidence; platform-side
  create/assign atomicity is explicitly not claimed (recorded in every result's
  `uncertainty` field).

## Failure matrix (all fixture-tested)

| case | terminal | target runs |
| --- | --- | --- |
| mention/invalid spec | `INVALID_INPUT` (zero commands) | 0 |
| unknown role / 02 target / stale T08 bundle | `ROUTING_REQUIRED` | 0 |
| create exit≠0 / malformed / pre-assigned response | `CREATE_UNVERIFIED` / `CREATE_RESPONSE_INVALID` / `CREATE_NOT_UNASSIGNED` | 0 |
| Finding-gate or finalize BLOCKED | `PREPARE_BLOCKED` | 0 |
| PARTIAL default | `PREPARE_PARTIAL_STOPPED` (gaps visible) | 0 |
| PARTIAL with explicit policy | publish + confirm + exactly 1 trigger; self-check still refuses non-READY work → `SELF_REFRESH_EXHAUSTED` | 1 |
| publish fails / note lost / foreign newer package / corrupted record | `PUBLISH_FAILED` / `CONFIRMATION_FAILED` | 0 |
| assign command failure | `TRIGGER_COMMAND_FAILED` (no retry) | 0 assumed |
| ambiguous assign response | `TRIGGER_CONFIRMATION_REQUIRED` (read-only reconciliation) | unconfirmed, no retry |
| SELF_CHECK REFRESH_REQUIRED → refresh recovers | `COMPLETED`, attempts=2, still 1 trigger, no duplicate note | 1 |
| second unresolved refresh / self-check BLOCKED | `SELF_REFRESH_EXHAUSTED` / `SELF_CHECK_BLOCKED` | 1, work stopped |

## Live-state non-activation proof (this task)

- The only runner used by T09 code/tests is injected (`FakeMultica` / `FixtureRunner`);
  no subprocess runner is constructed implicitly, and the live path requires a separate
  explicit authorization document (`authorization_ok`: `authorize_live_mutations=true` +
  `authorized_by` + `authorization_ref` + `scope`) — the gate is tested, the live path is
  **never executed** by YZT-64 (T12/T13 own it).
- Zero live `issue create/comment add/assign/status`, zero mentions, zero run triggers,
  zero skill/agent writes were performed in this task. Read-only probes used: `issue get`,
  `issue comment list`, `--help`, `version`, T08 read-only capture (`agent list`,
  `skill list`, `skill get`).
- Committed fixture evidence: `adapters/multica/assignment-handoff/sample-main-path-ledger.jsonl`
  (full COMPLETED lead→SE drill: 8 commands, ordered) and `sample-main-path-result.json`
  (result + replay proof + acceptance evidence, all nine `done:` criteria green).

## Verification at final HEAD `d6d1947`

| check | result |
| --- | --- |
| full suite (`unittest discover -s tools/tests`) | **449/449 OK** (406 accepted + 43 T09) |
| `python tools/chandoff.py scan` | clean |
| T01/T02/T03/T04/T06 frozen-contract compatibility | all `ok: true` |
| T05 contract (issue/comment JSON validation) | exercised by T09 tests |
| T07 Skill validation | `test_handoff_skill.py` green (in full suite) |
| T08 static audit | ok, 13/13 checks |
| T08 stage-mode live-state verify (fresh read-only capture) | ok, 32/32 checks, 0 drifts |
| Gates A0+A / B / C replay | all pass; `migration/gate-results/*.json` refreshed at this HEAD |

## Deviations / residual risks

1. The assignment confirmation marker set (`id == issue_id` or assignee fields == agent id)
   is a bounded contract over an unobserved deployed response format; an
   unrecognized-but-successful response fails closed to `TRIGGER_CONFIRMATION_REQUIRED`
   (safe, but would need a marker-set extension with live evidence — T12 owns that
   observation).
2. The CLI `run` command is a mechanics drill with a canned `FixtureRunner`; a static
   canned table cannot reproduce a full happy path because package ids embed live
   memory revisions. Full end-to-end drills go through the injected-runner API (tests)
   or recorded ledgers (`replay`).
3. Refresh with an unchanged `built_from` skips re-publication (T06 would refuse a
   same-package-id conflicting body); the already-confirmed record is re-confirmed
   instead. Documented in the ledger as `refresh_publish_skipped`.

## Handoff to T10

- The mention path (T10) must reuse this boundary's ledger/audit and stay a
  **separate, mutually exclusive** trigger route; `audit_ledger` already counts
  `mention_hits` as violations and the T09 path refuses mention-bearing inputs.
- T12 replay should drive `run_assignment_handoff` end-to-end with an injected runner
  first, and only then propose the live authorization artifact for T13.
