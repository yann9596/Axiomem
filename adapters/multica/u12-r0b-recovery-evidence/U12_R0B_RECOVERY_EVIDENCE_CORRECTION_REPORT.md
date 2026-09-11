# U12-R0B Recovery Evidence Correction — Implementation Report (YZT-84)

Verdict: **IMPLEMENTATION_READY_FOR_LEAD_REVIEW**. This is the bounded 04 correction of the accepted
`U12_R0_RECOVERY_EVIDENCE_DECISION.md` (attachment `01a08fbb-51cd-7aeb-907f-a547e4e066e9`, raw SHA256
`sha256:60f265a8a446b5329b534bbb183de12d306fc0ea38f1da767ff9bf998ebd4334`, read in full, digest verified).
It fixes the two verified omissions of `29e0bde`: shared command history now enters a conservative
classification/correlation gate, and the committed proof carries complete inline observations with
recomputed predicates. Live R0 recovery stays stopped; no live create/assign/comment/rerun, no
production-ledger write, no YZT-85 mutation, no Canonical/role activation, no 05/06/O3/Merge.

## Subjects

| Subject | Value |
|---|---|
| Branch | `yzt-84-u12-r0b-forward-adapter` |
| Base | `29e0bde95e66de94527b680511289c81329ce04c` (inspected/rejected subject; preserved as history) |
| Code commit | `65a017e` — adapter + both test modules |
| Adapter | `tools/u12_r0_binding.py`, LF `sha256:40ccf07dd088d6a4077213714deef45c83f46116478ccc462c203ef7f07641fa` |
| Existing recovery tests | `tools/tests/test_u12_r0_create_recovery.py`, LF `sha256:a1710c2f59873fc071ba7af98f1ee8f41f69eaa103b6eaec4928435db8c27c7d` (63→63 cases, fixture upgraded) |
| New evidence tests | `tools/tests/test_u12_r0_recovery_evidence.py`, LF `sha256:bbca242004eb4dc36a4d5091bd01fadac8d7ff09448e35565e045060a774bb11` (35 cases) |
| Acceptance matrix | `recovery-evidence-acceptance-matrix.json`, committed-blob LF SHA256 `sha256:597bfd8043654d3b6f0ba173560a91b0f03ca2431a314c507f04bfd677943177`, 33/33 rows |
| Reproduction | `reproduce_recovery_evidence.py`, SHA256 `sha256:a617c2d7117c30581f9eaa02ff763050a0bfdb10d27868d2bf1c90d0bd0a7345` |
| Disposition schema | `u12-r0b-create-recovery-decision/1.1` (old `1.0` refused, no silent upgrade) |
| Proof schema | `u12-r0b-recovery-proof/1.1` (old/hash-only refused) |
| Execution binding schema | `u12-r0b-execution-binding/1.1` |
| Contract / transport | `U12-R0B/1.1`, `single-terminal-lf/1` (unchanged meaning) |
| Predecessor fence | `b49630b881170f7e6f40ffe61a82687492b99792` / `sha256:8a9b75628f1263004ab214c96733d1b775399cdd6ffe5208fdaf4c0ea5abbc05` (unchanged) |

## Changes (maps the accepted decision)

### 1. Conservative shared-history classification and correlation

- The full ledger interval from this intent's original recorded record through the fresh live tip is read as
  raw bytes and classified record by record (`_read_ledger_lines`, `_classify_shared_history`,
  `_classify_record`). Records positively linked to this intent that precede the interval (intent id or
  marker) extend the interval, and a command begun before the interval whose result falls inside it is
  correlated rather than discarded.
- Every record retains a classification and reason: `expected-intent-history`,
  `uniquely-correlated-original-create-command`, `uniquely-correlated-original-create-result`,
  `recognized-read-command`, `recognized-read-result`; everything unresolved refuses.
- The original create command is **never** selected by class label alone: the structured argv must
  reproduce the retained spec — title, `--parent`, `--project`, `--priority`, literal `--status backlog`,
  `--output json`, no assignment/start flag, no unexpected flag/positional — and the `--description-file`
  argument must encode the preserved source body digest (`sha1(body)[:12]`), so a re-used temp path can
  never stand in for the historical input. Exactly one create-issuing event **and** exactly one matching
  command are required.
- Command/result pairing is adjacency-based within the interval with class + transaction agreement; empty
  transaction ids are never paired collectively, and orphan, duplicate, interleaved or class-conflicting
  results refuse. Unknown verbs, malformed argv and class/argv disagreement refuse. Nonzero create results
  refuse regardless of the readback.
- Every relevant write attempt (ownership `--no-start`, assignment, publication/comment, update, status,
  rerun, second create, unknown effect) refuses **regardless of exit code, result presence, current
  assignee or empty live runs**. Native failure is never treated as absence of an effect. (Conservative
  refusal of unrelated interleaving was chosen over building a general correlator, as the decision allows
  for this single-dispatch incident.)
- The disposition pins the audited ledger prefix (`length` + exact raw digest) and the original
  command/result sequence numbers and digests; the operation recomputes both from the raw ledger and
  refuses on any mismatch. After the evidence append and immediately before the CAS, the prefix is
  revalidated and the fresh tail classified: the only permitted delta is this operation's own evidence
  event, so a shared write that does not change the intent revision can no longer pass on the CAS alone.

### 2. Receipt decision

- A supplied receipt must be an object naming the exact target (and, when present, matching title/parent);
  a conflicting receipt refuses even when the readback looks valid.
- A truly missing raw receipt body is admissible only under an explicit disposition with
  `original_receipt_body_status = not_persisted` and the exact bounded scope
  `READ_ONLY_TARGET_IDENTIFICATION_FOR_UNPERSISTED_RECEIPT_BODY`; the classification is revalidated from
  the ledger, not trusted as a boolean. Missing/nonzero/ambiguous/conflicting command results are never
  waived.
- 04 supplies the schema, fixtures and operator example only; the Lead audits the real prefix/pair and
  issues the disposition (see `OPERATOR_INSTRUCTIONS.md`).

### 3. Audit-reconstructable proof persistence

- `_build_recovery_proof` persists a v1.1 proof with inline observations: the full initial target issue and
  final recheck, complete comments/activities/runs, the complete parent-child discovery listing with its
  declared/collected totals and completeness metadata, every candidate body actually used (with its full
  response and whether it came from the listing or an `issue get`), the exact raw successful read
  responses (argv + stdout text + digest), per-section digests, transport/source/readback byte
  representation and the exact directional relation, the full original command/result records and the
  receipt or its explicit absence, the coverage/limitation note (timeline is not a revision journal), the
  disposition copy and the resolved execution identity.
- Evidence is durably appended before the unchanged `CREATE_AMBIGUOUS -> TARGET_BOUND` CAS; the binding
  carries the proof digest and the evidence event sequence/digest. Every hash and critical predicate is
  recomputed from the inline bodies on commit, restart and executable load; missing, truncated, edited,
  cross-intent or count/body-mismatched evidence refuses, and a committed replay validates its committed
  proof without fetching fresh data as a substitute. Hash-only or v1.0 proofs are preserved for inspection
  but refused for execution.

### 4. Exact execution identity

- `recover_created_target` now requires the full 40-hex accepted execution commit; it must be the
  disposition's `accepted_execution.commit`, resolve to an adapter blob (real Git blob by default, an
  explicitly injected fixture resolver in tests) whose LF digest equals these executing bytes, and match
  the recorded accepted digest. Missing, short, unaccepted, unresolvable, mismatched or fabricated
  identities refuse. A fixture-proposed identity is recorded as `injected-fixture-proposal` and is not a
  live acceptance.

## Validation Focus mapping (all 21 accepted rows)

| Accepted row | Evidence |
|---|---|
| Exact counterexample (empty-tx/no-intent rerun + exit 0, empty runs) | matrix 1.1; test `test_counterexample_rerun_command_and_success_result_refuse` |
| Same rerun no result / nonzero / different class / explicit intent id | matrix 1.2, 1.3 |
| Ownership `--no-start`, assignment, publication, update/status, unknown effect ± result | matrix 1.4, 1.5 |
| Second shared create without second intent event | matrix 1.6 |
| Sole issuing event, missing create command/result | matrix 1.7 |
| Orphan / duplicate / interleaved / class-conflicting result | matrix 1.8 |
| Nonzero create result / conflicting target receipt | matrix 1.7, 2.2 |
| Empty-ID sole original command + receipt available and consistent | matrix 2.1 |
| Raw receipt never persisted + exact bound receipt-limit disposition | matrix 2.4; blocked variant matrix 2.3 |
| Recognized reads + own lease/refusal/prior evidence; failed/partial read blocks | matrix 1.9, 1.11, 3.3 |
| Positively proven disjoint / unresolved foreign write | matrix 1.10 (conservative refusal) |
| Shared write during collection without intent revision change | matrix 3.2, 3.3 |
| Full inline proof / resolvable immutable evidence | matrix 4.1, 4.2 |
| Hash-only / missing blob / edited / truncated / cross-intent / count mismatch | matrix 4.4 |
| Missing/unaccepted commit, digest/blob mismatch, fabricated provenance | matrix 4.5, 4.4 |
| Zero/multiple candidates, partial listing, wrong fields, moving readback | matrix 6.1 (+ 26 refusal tests in the focused module) |
| Exact body or one terminal LF; other whitespace/marker changes | matrix 6.2 (+ transport group in the focused module) |
| Crash before evidence / after evidence before CAS | matrix 5.4, 5.5 |
| Crash after CAS before ack; repeat/concurrent; competing target/disposition | matrix 5.7 (+ concurrency group in the focused module) |
| Old executor, bare pin change, tampered compatibility/proof, flag reset | matrix 5.6, 5.2, 4.4 (+ old-executor module tests) |
| Post-recovery lifecycle, fresh E, drift after ARM, strict ambiguous trigger | matrix 5.3, 5.8 (+ post-recovery group in the focused module) |

## Deviations / interpretations (full list)

1. Only the namespaced decision/proof/execution-binding payloads were versioned to `1.1`; `U12-R0B/1.1`
   lifecycle, O2 vocabulary, frozen T00/O2/strict bytes and the `single-terminal-lf/1` profile keep their
   accepted meaning.
2. Unrelated shared commands are refused conservatively instead of being excluded by positive proof; the
   accepted decision explicitly allows this for the current single-dispatch incident.
3. The disposition `ledger_prefix.digest` convention is defined as SHA256 over the exact bytes of the first
   N newline-terminated ledger lines; `ledger-audit` computes it, so Lead-calculated pins are unambiguous.
4. `execution_blob_resolver` is injectable for isolated fixtures (recorded as
   `injected-fixture-proposal`); the default is the real Git blob resolver. This does not itself accept any
   identity for live use.
5. The existing test fixture module was updated to the v1.1 disposition/proof and the mandatory execution
   commit; its accepted refusal/lifecycle coverage is retained (63/63).
6. The previous artifacts under `adapters/multica/u12-r0b-recovery/**`, `u12-r0bi/**` and `u12-r0b-forward/**`
   are byte-preserved as history; their reproduction script is superseded by this folder.

## Tests and evidence

- Combined focused run (recovery + evidence + frozen lifecycle + strict + preflight): **237 tests, 0
  failures, 0 skips** — 63/63, 35/35, 78/78, 40/40, 21/21.
- Adapter `self-check`: wiring proof **45/45 checks true**, contract proof present (adapter digest
  `sha256:40ccf07d...`).
- Executable public-operation matrix: `python -B adapters/multica/u12-r0b-recovery-evidence/reproduce_recovery_evidence.py --output ...`
  → **33/33 rows pass** across shared-history, receipt, prefix/tail, proof/identity, fences and preserved
  target/evidence groups.
- Full `tools/tests`: **1,211 run, 1 failure + 2 errors**, all three in `test_u12_p0r_evidence.py` and all
  the Lead-classified pre-incident genesis drift (frozen bundle expects the 622-byte pre-incident ledger;
  live ledger is the 37,860-byte post-incident tip `sha256:5a7e3369...`). Files outside this diff; the
  frozen bundle was deliberately not rewritten.
- Frozen pins reproduce: strict gate `sha256:f37ed091...`, `chandoff_intent.py` `sha256:0544046f...`,
  readiness manifest entry `sha256:ee6cb9a2...` and self-digest `sha256:64a5c449...`, artifact dependency
  digest `sha256:937521a6...`.
- No production ledger was read or written in this turn; the Lead-attributed production figures
  (12 records, 37,860 bytes, `sha256:5a7e3369...`, seq 5–9 shape, no persisted raw create receipt) are
  carried as recorded, not re-derived.

## Context findings / operator readiness

- All recovery prerequisites now validate in principle against the exact incident shape used by the
  fixture (predecessor record, sole create, empty runs, revision 1, empty transaction ids, no intent id on
  shared records, raw receipt body not persisted).
- The public `ledger-audit` entrypoint prints the per-record classification and the exact prefix/pair pins
  the Lead must bind; `OPERATOR_INSTRUCTIONS.md` documents the full invocation, and
  `recovery-decision-example.json` shows the v1.1 disposition shape with the real subjects and explicit
  placeholders for audit-derived values.
- Remaining Lead-owned steps: accept these exact bytes, re-audit the production ledger immediately before
  any append, issue the v1.1 disposition (including the bounded receipt-limit scope only if the real chain
  is unique and successful), and name the accepted execution commit.

## Risks

- Unknown historical effects remain a hard stop even when the target looks pristine.
- The conservative classifier refuses any unresolved or unrecognised shared record; the single-dispatch
  constraint remains the operational guard because lease/CAS does not exclude arbitrary external writers.
- Inline evidence enlarges the evidence event (about 46 KB for the fixture); this is the decision's
  recommended minimum for the bounded incident and avoids a new evidence-store lifecycle.
- The timeline is still not a complete revision journal (accepted limitation, recorded in the proof).
- Fixture success does not prove production recoverability; the Lead's real audit and disposition decide.

## What this is not

No production ledger append/recovery, no YZT-85 mutation/ownership/publication/trigger, no new
intent/create, no product original-worktree or Canonical write, no specialist dispatch, no 05/06/O3 and no
Merge. Final Merge remains Human.
