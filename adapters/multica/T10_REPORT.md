# T10 — Mention Handoff Path (YZT-65)

- branch: `yzt-65-mention-handoff-path` @ `40658e2` (verification HEAD; exact base
  `yzt-64-assignment-main-path@93d63bae580ffb6faf8f175c268a2cd7588618a1`; the
  evidence commit on top adds only non-code artifacts)
- owner: 02 Context Engineer; status: **staged, simulation-only — zero live mutations**
- new code: `tools/chandoff_mention.py` (staged mention-handoff orchestrator + CLI),
  `tools/tests/test_handoff_mention.py` (72 focused tests)
- frozen inputs untouched: T00–T08 schemas/modules, T07 Skill, T08 bundle,
  T09 dispatch/ledger/audit primitives, docs §6/§7/§17/§22/§35
- committed fixture evidence: `adapters/multica/mention-handoff/sample-mention-path-ledger.jsonl`
  (staged SA→SE drill: ready phase → execute phase → replay) and
  `sample-mention-path-result.json` (stage flow, final result, replay proof,
  cross-route audit, all thirteen `done:` criteria green)

## Strict order implemented (§35.2)

```text
INIT → ISSUE_BOUND → TARGET_RESOLVED → ROUTE_FROZEN → HANDOFF_PREPARED
     → HANDOFF_PUBLISHED → HANDOFF_READY_CONFIRMED → MENTION_READY
     → MENTION_EVIDENCE_ACCEPTED → TARGET_RUN_CORRELATED
     → TARGET_SELF_CHECKED → COMPLETED
```

The path is **staged**: the ready stage (steps 1–6) terminates at the
`MENTION_READY` envelope with zero trigger; the current agent then emits ONE
real native mention on its own Multica reply surface (§35.4); the execute
stage re-validates everything from the shared ledger and continues
(evidence → run → SELF_CHECK → work gate). `run_mention_handoff(stage="full")`
drives both phases in one transaction with injected evidence.

| step | owned by | guarantee |
| --- | --- | --- |
| 1 bind existing issue | `DispatchCli.issue_get` | read-only; the issue id comes from the caller spec and is never guessed; no issue is ever created (`issues_created: 0`); assignee observed `before` |
| 2 resolve target role | T08 `resolve_target` over the frozen bundle | exact slug/display-name match; role must be bound; `context-engineer` never a target; unknown/stale → `ROUTING_REQUIRED` |
| 3 route freeze | `route_conflicts` precheck + `route_binding` record | one route per transaction/package/handoff; a handoff or package already bound to the assignment route is refused (`ROUTE_CONFLICT`) and vice versa |
| 4 PREPARE_HANDOFF | T05 snapshot (read-only over the existing issue) → T01 → injected compose → T02/T03 | accepted T07 pipeline; BLOCKED → `PREPARE_BLOCKED`; PARTIAL stops by default, continues only with an explicit recorded policy decision with gaps visible |
| 5 publish non-trigger note | T06 `publish_handoff` | exactly one `/note` `issue comment add --content-file`; body scan refuses any mention link (no adapter-constructed mention) |
| 6 confirm HANDOFF_READY | T06 `resolve_latest_handoff` | exact issue/task_ref/role/package_id/comment_id binding before any trigger authorization |
| 7 MENTION_READY | `mention_ready_envelope` | bounded envelope identifies issue, caller agent, target role/agent id, package id, published comment id (+ created_at), transaction id, trigger type `mention`, and the constraints (one native mention, no adapter markdown, no assignment, assignee unchanged, no second mention) |
| 8 native mention evidence | injected `native_mention_evidence`, fail-closed validation | must prove exactly ONE target-agent mention on the exact issue by the expected caller agent on `native_agent_reply` surface, after the confirmed handoff, with the exact `mention://agent/<target-uuid>` link and no assignment mutation |
| 9 run correlation | injected `target_run_evidence` | exactly ONE run, of the target agent, on the same issue, traceable to the single accepted mention comment; zero/duplicate/wrong-target/uncorrelated stop (`RUN_CORRELATION_FAILED`), never a second mention |
| 10 target SELF_CHECK | T04 `self_check_with_trace` on the re-resolved record | READY → work gate; REFRESH_REQUIRED → one bounded refresh (T07 pipeline re-run; publish only when `built_from` changed); second unresolved refresh → `SELF_REFRESH_EXHAUSTED`; BLOCKED → `SELF_CHECK_BLOCKED` |

## §35.4 boundary (adapter never constructs the mention)

- the orchestrator has NO API that produces mention markdown; the only
  mention-shaped string it validates is the evidence's declared link, and
  any `mention://` in an ISSUED argv or in a rendered note body fails the
  path (`audit.mention_hits`, T06 body scan);
- evidence must attest `author_surface: native_agent_reply`; anything else
  is rejected as adapter-constructed/forged; the mention comment must be a
  different comment from the published `/note`;
- plain text `@Name` (zero mention links) and bare handles alongside a link
  (ambiguous) fail closed (`MENTION_EVIDENCE_REJECTED` /
  `MENTION_CONFIRMATION_REQUIRED`); the adapter makes no claim that plain
  text is a native mention.

## Mutual exclusion and exactly-once

- T09's `TransactionLedger`/`RecordingRunner`/`FixtureRunner` are reused
  verbatim — one shared ledger, no parallel ledger. `audit_mention_ledger`
  (minimal compatible extension) fails on any `issue_create`, any
  `issue assign` argv, any mention link in issued argv, more than one
  mention authorization, more than one run outcome, or mixed assignment+
  mention evidence in one transaction;
- `cross_route_audit` scans a whole shared ledger: one transaction carrying
  both trigger types, one package bound to both routes, one handoff
  (issue+task_ref+role) triggered by both, >1 mention per package, or >1
  run per mention — each fails (`ok: false` with bounded conflicts);
- assignee stability: observed `before` (step 1) and re-observed `after` in
  every `_finish` (successful and stopped paths alike); any observable
  mutation flips the transaction to `ASSIGNMENT_MUTATION_DETECTED`
  (`ok: false`) — never repaired silently;
- replay: a recorded final COMPLETED result is returned as-is with zero new
  commands; any recorded incomplete result (ambiguous mention, failed run
  correlation, stop) refuses (`REPLAY_REFUSED`) — reconciliation is a
  read-only operator action, a second mention is never authorized;
- exactly-once is claimed ONLY against observable ledger evidence;
  platform-side mention/run atomicity is explicitly not claimed (recorded
  in every result's `uncertainty`).

## Failure matrix (all fixture-tested)

| case | terminal | mentions / runs |
| --- | --- | --- |
| invalid/smuggled spec (mention link in input) | `INVALID_INPUT` (zero commands) | 0 / 0 |
| unknown role / 02 target / unbound role | `ROUTING_REQUIRED` | 0 / 0 |
| issue read failed / response invalid | `ISSUE_UNVERIFIED` / `ISSUE_RESPONSE_INVALID` | 0 / 0 |
| assignment route already holds the handoff or package | `ROUTE_CONFLICT` | 0 / 0 |
| finding-gate BLOCKED | `PREPARE_BLOCKED` (no publish) | 0 / 0 |
| PARTIAL default / PARTIAL with explicit policy | `PREPARE_PARTIAL_STOPPED` / publish + 1 mention + 1 run, work still stopped (non-READY self-check) | 1 / 1 |
| publish ok but confirmation missing/stale/mismatched | `CONFIRMATION_FAILED` | 0 / 0 |
| evidence missing after native mention | `MENTION_CONFIRMATION_REQUIRED` (no retry authorized) | emitted, unconfirmed |
| plain text `@Name` / fabricated link text / wrong target / wrong author / wrong issue / adapter surface / note-comment-as-mention / precedes publication | `MENTION_EVIDENCE_REJECTED` | 0 runs |
| multiple agent mentions | `MENTION_EVIDENCE_REJECTED` | 0 runs |
| assignment + mention in one route | `MENTION_EVIDENCE_REJECTED` + audit fail | 0 |
| duplicated native receipt | `MENTION_EVIDENCE_REJECTED` (second acceptance refused) | 0 |
| zero / duplicate / wrong-target / uncorrelated / missing run evidence | `RUN_CORRELATION_FAILED` (reconcile, never re-mention) | 1 mention |
| SELF_CHECK REFRESH_REQUIRED → one bounded refresh recovers | `COMPLETED` (still 1 mention, 1 run, no duplicate note) | 1 / 1 |
| second unresolved refresh / SELF_CHECK BLOCKED | `SELF_REFRESH_EXHAUSTED` / `SELF_CHECK_BLOCKED` (work stopped) | 1 / 1 |
| platform mutates assignee during the transaction | `ASSIGNMENT_MUTATION_DETECTED` (fail closed) | — |
| already-confirmed replay | replayed as-is, zero new commands | 0 new |

## Zero-live-activation proof (this task)

- every T10 code path and test uses injected runners (`FakeMultica` /
  `FixtureRunner`); `DispatchCli` refuses to construct any runner, and no
  subprocess/implicit live path exists in `chandoff_mention.py`;
- zero live `comment add`, zero agent mentions, zero assignment/status
  writes, zero run triggers, zero issue creations, zero skill/agent writes,
  zero Canonical writes, zero index rebuilds were performed by YZT-65;
  read-only probes used: `version`, T08 `capture` (`agent list`, `skill
  list`, `skill get` — all read-only), `issue get`/`comment list` only
  inside fixtures;
- the committed evidence's ledger contains only `read` + exactly one
  `comment_publish` command class, `issue_create: 0`,
  `assignment_trigger: 0`, `mention_hits: []`.

## Verification at final HEAD

| check | result |
| --- | --- |
| full suite (`unittest discover -s tools/tests`) | **521/521 OK** (449 accepted T09 + 72 T10) |
| `python tools/chandoff.py scan` | clean |
| T01/T02/T03/T04/T06 frozen-contract compatibility | all `ok: true` (T05 exercised by T10 tests; T07 Skill green in suite) |
| T08 static audit | ok, staged_candidate_not_active, 0 live writes |
| T08 stage-mode live-state verify (fresh read-only capture) | ok, 32/32 checks, 0 drifts |
| Gates A / B / C replay | all pass; `migration/gate-results/*.json` refreshed at this HEAD |
| cross-route audit on the committed evidence | ok (assignment/mention mutual exclusion) |

## Deviations / residual risks

1. Native mention evidence is an INJECTED, bounded contract (ids, counts,
   link form, surface, created_at) — the deployed platform's observable
   reply surface is not yet probed live. Distinguishing one real native
   mention from forged markdown therefore rests on the evidence contract
   plus run correlation, not on a live observation; T12 owns the live
   observation, and a marker-set extension may be needed there (same class
   of caveat as T09's trigger confirmation).
2. The CLI `ready`/`execute` drills use a canned `FixtureRunner` table that
   cannot reproduce a stateful comment round-trip (the confirmation read
   returns no records), so the CLI staged drill ends `CONFIRMATION_FAILED`
   deterministically — the fail-closed boundary the CLI proves; full
   stateful drills go through the injected-runner API (tests) or the
   committed ledger (`replay`). Same limitation T09 documented.
3. The assignment-route "vice versa" refusal lives in T09's boundary
   (mention-bearing inputs are refused there, and the shared cross-route
   audit fails any mixed ledger); a live T09-run integration check for
   mention-bound packages is T12 replay material.
4. Assignee stability is proven against the two observed `issue get`
   responses (start/finish); a concurrent platform-side mutation between
   the reads is not observable in simulation (carried in `uncertainty`).

## Handoff to T11

- T11 (direct-assignment fallback) should reuse this module's evidence
  envelopes and `cross_route_audit`; the mention route must stay mutually
  exclusive with BOTH the T09 main assignment path and T11's fallback
  route, per transaction/package/handoff.
- T12 end-to-end replay should drive: ready stage → (agent native reply)
  → execute stage with OBSERVED evidence, and observe whether the deployed
  platform distinguishes a native mention from plain text (deviation 1).
