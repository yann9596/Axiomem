# U12_P0R_STRICT_RECEIPT_GATE_REPORT

- task: `YZT-82` — U12-P0R Enforce Approved Receipt Shapes Before R0
- role: 04 Software Engineer (implementation owner)
- branch: `yzt-82-u12p0r-strict-receipt-gate`; base commit `6a6c018c718795aedc677fe61fabfe30fee96217` (exact U12-P0 artifact); repair pre-image commit `afc13fd43c1fa5b468c881f9abc5e174bd0c77bd` (F2 gate revision, rejected as `U12_P0R_CHANGES_REQUIRED`)
- worktree: `D:\AI\worktrees\multica-memory-yzt-82-u12p0r` (existing isolated worktree; original `D:\AI\multica-memory`, the U12-P0 worktree and every prior worktree untouched)
- status: **F4 repair revision.** R0 canary remains uncreated, untriggered and unauthorized. No live issue/comment/assignment/rerun/mention/status/run mutation was issued by this work; `r0_canary_authorized: false` in every artifact.

## Repair Revision F4 (`U12-P0R-F4`)

Lead finding `01a08ed0-a2cc-7625-867a-fc8ec81b021f`: the strict gate decoded receipts with Python's default `json.loads`, whose object builder silently keeps the **last** value for a repeated key. Reproduced before classification:

```json
{"id":"first","id":"second","issue_id":"i","agent_id":"a","status":"queued"}
{"runs":[],"runs":[{"id":"r","issue_id":"i","agent_id":"a","status":"queued"}]}
```

Both previously passed `parse_strict_receipt` (the second was accepted as `runs_wrapper` after the empty list was overwritten), so an ambiguous receipt could reach the O2 parser.

Repair (forward-only, no accepted byte changed):

- `_decode_strict_json()` now decodes every receipt with `object_pairs_hook=_reject_duplicate_keys`. Any JSON object repeating a key — at **every nesting level** (observable fields, the `runs` wrapper, run rows, nested extra-field objects and arrays) and **including same-valued repeats** — raises during decoding, before classification and before the immutable O2 parser is reachable.
- The refusal is typed: reason `duplicate_json_key` → `StrictReceiptRefused` (a `ReceiptAmbiguousError`) → `TRIGGER_AMBIGUOUS` manual stop with zero retry/status-toggle/reroute.
- Strict-gate version/digest: `U12-P0R/1.1`, LF digest `sha256:6d6452a5a17911926503a2eece3725cd58ac22b1ef10d7bb5f292901a207eb49` (superseding the rejected `U12-P0R/1.0` / `sha256:a8280b39…`).
- The three authorized shapes and every previous rejection behavior are unchanged; the refused matrix now carries **28** cases (21 prior + 7 duplicate-key cases, `7/7` refused with **0** O2-parser calls).
- `wiring_proof` adds the static check `duplicate_key_guard_present` (guard function present, `object_pairs_hook` wired, called from `classify_strict_receipt`).

## Authorization

- Human approval of the three U12 choices (production ledger root, bounded rerun receipt contract, R0 → R1 → R2 order): parent YZT-66 comment `01a08e93-0a1a-7074-9dda-912d1237bf56`.
- Lead U12 decision `01a08e89-22ec-7db7-a581-0c62f30db538`: `RERUN_RECEIPT_CONTRACT` accepts **only** a single run object, a one-item run list, or `{runs:[one]}`; ambiguous receipt = manual stop, no retry.
- Lead F2 escalation `01a08ebc-a0e5-7c7f-a150-f917e2cd447d`: `U12-P0-F2 → R0_BLOCKING_CONTRACT_MISMATCH`; `U12-P0-F1 → ACCEPTED_NONBLOCKING_DOCUMENTATION_ERRATA`; `U12-P0-F3 → INFORMATIONAL_BOUNDARY_ACCEPTED`; YZT-81 infra accepted, prior “R0 ready” conclusion not accepted.
- Lead F4 verdict `01a08ed0-a2cc-7625-867a-fc8ec81b021f`: `U12_P0R_CHANGES_REQUIRED` — repair duplicate-key acceptance before any live R0 canary; R0/R1/R2 parked.
- Human execution decision `01a08eea-1c34-7796-904b-3f7da927dea4`: target-local non-trigger READY publication approved; exactly one rerun as the sole trigger.
- Scope honored: same isolated worktree on `afc13fd`, forward-only additions, zero platform writes, zero retries.

## Preconditions (SELF_CHECK)

- Published target-local READY package `CTX-software-engineer-051a1acd69706393` (`ready`, prepared 2026-09-11T05:25:06Z) self-checked before consequential work: `memory_revision sha256:30b51dea…`, `registry_revision sha256:a08e20eb…`, `role_profile_revision sha256:7b3bdf52…` recomputed from the worktree and **identical** to the package's `built_from`; target role `software-engineer` matches; exactly one new 04 run (`01a08eed-e124…`) for this dispatch; no unexpected trigger, no authority expansion.
- Base `6a6c018`: full `tools/tests` **985/985 OK** (53.4 s) reproduced before any change; `python tools/chandoff.py scan` clean.
- Production ledger re-read read-only before generation: 622 bytes, tip `sha256:c9683898…` — identical to the accepted U12-P0 deployment tip, 0 corrupt records, 0 intent records.
- Accepted predecessors byte-preserved: `tools/chandoff_intent.py` (`sha256:0544046f…`), `tools/o2_store_probe.py` (`sha256:b9cf1d3a…`), `tools/chandoff_joint.py` (`sha256:2afc2293…`), O2 bundle `sha256:3c207e85…`, U11 bundle `sha256:04e0da84…`, final-gate matrix `sha256:a9ecc24c…`, feature commits `964f935` / `e855fa1` / `6439bd4` present; prior U12-P0 plan `sha256:0b88856d…` and manifest `sha256:78f6a456…` reproduce exactly; the rejected `afc13fd` revision's manifest `sha256:8eed5a7b…` and gate `sha256:a8280b39…` are verified from that commit's blobs and recorded as superseded.
- No `.py`/artifact under `adapters/multica/u12-p0/`, no accepted predecessor file and no `chandoff_joint.py` / `u12_preflight.py` byte was modified.

## Strict Receipt Gate (F2 disposition: `RESOLVED_BY_STRICT_RECEIPT_GATE`; F4 disposition: `RESOLVED_BY_STRICT_DUPLICATE_KEY_DECODER`)

Forward-only module `tools/u12_strict_receipt.py` (`U12-P0R/1.1`, LF digest `sha256:6d6452a5a17911926503a2eece3725cd58ac22b1ef10d7bb5f292901a207eb49`), separate from the immutable accepted O2 parser.

Accepted shapes (exactly three):

```text
1. direct run object carrying every observable field (id, issue_id, agent_id, status)
2. one-element list whose sole item is that run object
3. object with `runs` as a one-element list containing that run object
```

Refused before correlation, with zero calls to the O2 parser:

- `{ "run": { ... } }` — even when the nested run is valid (`run_wrapper_unauthorized`);
- mixed wrapper shapes (`run` plus `runs`) and any competing wrapper key on `{runs:[one]}` (the wrapper object must carry exactly the `runs` key);
- empty and multi-item lists, `[1]`, `{runs: []}`, `{runs: [r1, r2]}`, `{runs: <non-list>}`;
- empty object, missing/blank observable fields, non-string observable fields;
- **any repeated JSON object key at any nesting level, same-valued or not** (`duplicate_json_key`), covering duplicate observable fields, a duplicate `runs` wrapper, duplicate nested run-row fields and duplicates inside nested extra-field objects/arrays (F4);
- non-JSON, JSON null/scalars/bool, and non-text input.

Extra fields **inside** the run object are ignored only after the top-level shape has been classified as one of the three authorized forms. Classification is completed before the immutable O2 `parse_run_object()` is invoked and only then as field extraction; a refused receipt never reaches it.

Machine matrix (`strict-receipt-gate-evidence.json`, `sha256:fba5d1a1…`):

```yaml
authorized_receipt_shapes_accepted: 3/3
authorized_cases_accepted: 4/4      # direct object incl. extra fields, one-item list, {runs:[one]}
run_wrapper_shape_rejected: true
all_unauthorized_or_ambiguous_shapes_rejected: true
refused_cases: 28
duplicate_key_cases_rejected: 7/7   # incl. same-valued and nested extra-field objects
duplicate_keys_including_same_value_rejected: true
duplicate_key_parser_calls_on_rejection: 0
permissive_parser_invoked_on_refusal: 0
permissive_parser_invoked_only_after_classification: true
```

## R0 Issuing Path Binding (`r0_path_uses_strict_gate_only: true`)

- `StrictReceiptBoundary.rerun_issue` is the **only** receipt entrypoint of the R0 issuing path: it runs the frozen `issue rerun <target> --output json` argv, parses the raw stdout exclusively through `parse_strict_receipt`, and carries no fallback. `StrictReceiptBoundary.assign_trigger` refuses, so no alternative receipt route exists in the canary path.
- `CanaryOrchestrator` replaces the permissive `O2DispatchBoundary` on the orchestrator before any dispatch can occur. `TRIGGER_ISSUING` (transition) is still persisted and fsynced before the native call.
- Static wiring proof (`wiring_proof.ok: true`): exactly one `parse_run_object` call site in the module, inside `parse_strict_receipt`, after the refusal raise; `classify_precedes_o2_parser: true`; `duplicate_key_guard_present: true`; `rerun_issue_never_calls_permissive_parser: true`; `permissive_boundary_method_overridden: true`; `canary_orchestrator_replaces_boundary: true`; `permissive_entrypoint_reachable_in_r0_path: false`; `bypass_or_fallback: false`.
- Dynamic proof (focused tests): with the O2 parser instrumented, every refused case — including all 7 duplicate-key cases and the Lead's two exact reproductions — raises `StrictReceiptRefused` with **zero** parser calls; each authorized shape produces exactly one call after classification. End-to-end through `CanaryOrchestrator`, a `{run:{…}}` or duplicate-key receipt becomes the typed stop `TRIGGER_AMBIGUOUS` (reason `TRIGGER_AMBIGUOUS`, `side_effects: 0`) with exactly one trigger issued and a replay that issues nothing; an authorized receipt correlates exactly one run.
- Updated machine-readable plan `adapters/multica/u12-p0r/proposed-r0-canary-plan.json` (`sha256:ce8be5fe…`) binds the module, version, digest, entrypoint and duplicate-key policy above and supersedes only the prior plan's receipt entrypoint (`supersedes.digest sha256:0b88856d…`, verified).

## Correlation and Attribution Boundary (F3 disposition: `BOUND_BY_ROUTE_PROVENANCE`)

- `TRIGGER_ISSUING` write-ahead and exactly-one trusted untruncated `issue runs` listing correlation are unchanged.
- The selected trigger route is persisted from the issuing transaction/ledger: `dispatch_intent TRIGGER_ISSUING transition fields.selected_trigger` and the `trigger_receipt` event `data.selected_trigger` (both `issue_rerun`; asserted by test).
- `run_attribution_text_used_for_route: false`. U12-P0 captured a live run whose platform attribution says `issue_assignment`/`delegation` while the route was the single `issue rerun`; the canary therefore derives route only from its own ledger and correlates by identity diff against the trusted listing. A fixture run carrying misleading attribution text still correlates by identity alone.
- Any receipt/listing ambiguity remains the typed `TRIGGER_AMBIGUOUS` manual stop: zero retry, zero re-route, zero status toggle.

## Forward Errata (F1 disposition: `ACCEPTED_FORWARD_ERRATA`)

`adapters/multica/u12-p0r/O2_REPORT_DIGEST_ERRATA.json` (`sha256:14025c80…`) records machine-readably:

- O2 report value `sha256:649580b2…` for `tools/tests/test_handoff_intent.py` is a documentation defect, not byte drift;
- the committed blob is identical at `c24284a`, `49c48a9`, `6439bd4`, `6a6c018` (all four LF digests recomputed as `sha256:78fb99d4…`);
- the correct LF digest for future verification is `sha256:78fb99d40ad22ffcf45a087314901e26df5c1d502b038404728c4f1672283665`;
- the O2 report and every predecessor commit remain byte-unchanged (`o2_report_edited: false`, `history_rewritten: false`).

## Supersession of the Prior Readiness Conclusion

- New manifest `adapters/multica/u12-p0r/readiness-manifest.json` (canonical digest `sha256:95a2be772aa837dd7507503a84d5eaefc4e43057f216663484d4fd703f9f496e`, file `sha256:190d77fb…`) declares `supersedes.path = adapters/multica/u12-p0/production-root-manifest.json`, `supersedes.digest sha256:78f6a456…` (`verified_match: true`), and supersedes **only** the prior `receipt_contract_bounded` / R0 readiness conclusion.
- The rejected `U12-P0R/1.0` readiness revision is recorded under `supersedes.prior_u12_p0r_revision` (`commit afc13fd…`, manifest `sha256:8eed5a7b…`, gate `sha256:a8280b39…`, both `verified_*_match: true` from that commit's blobs) and is superseded **only** as a readiness conclusion; its bytes remain in history.
- Production ledger deployment, ACL, backup/restore, capability 6/6, path validation and U11/O2 pins are retained as accepted history; the old artifacts remain byte-intact.

## Changes

- Added `tools/u12_strict_receipt.py` — strict gate (`classify_strict_receipt`, `parse_strict_receipt`, `StrictReceiptBoundary`, `CanaryOrchestrator`, `gate_descriptor`, `wiring_proof`) with the F4 duplicate-key strict decoder.
- Added `tools/u12_p0r.py` — forward evidence generator with `generate` / `verify` / `check` subcommands (no platform call, no production write).
- Added focused tests `tools/tests/test_u12_strict_receipt.py` (30) and `tools/tests/test_u12_p0r_evidence.py` (10, incl. byte-for-byte committed-bundle regeneration).
- Added the deterministic evidence bundle `adapters/multica/u12-p0r/` (6 files) and the `.gitattributes` byte-stability entry.

**No accepted predecessor byte, parser, schema, report, product repository, Canonical Memory or O3/daemon component was modified.** The F4 repair continues forward from `afc13fd`; the only files under `adapters/multica/u12-p0/` are untouched at base.

## Tests

| suite / check | result |
| --- | --- |
| base `6a6c018` full `tools/tests` before change | 985/985 OK (53.4 s) |
| focused `tools.tests.test_u12_strict_receipt` | **30/30 OK** (20 F2 + 10 F4 duplicate-key/E2E) |
| focused `tools.tests.test_u12_p0r_evidence` | 10/10 OK |
| final HEAD full `tools/tests` | **1025/1025 OK** (54.0 s) |
| T00 `python tools/chandoff.py scan` | clean |
| accepted pin/bundle/matrix rechecks (`tools/u12_p0r.py verify`) | `ok: true`; `accepted_inputs_all_match: true` |
| production-ledger read-only integrity | tip `sha256:c9683898…` unchanged, 622 bytes, 1 record, 0 corrupt, 0 intents |
| deterministic artifact regeneration (`tools/u12_p0r.py check`) | all 6 files byte-identical (`all_match: true`) |
| strict matrix | 3/3 authorized shapes; 28/28 unauthorized/ambiguous cases refused (incl. 7/7 duplicate-key); 0 parser calls on refusal |

Reviewer re-verification (read-only): `python tools/u12_p0r.py verify` and `python tools/u12_p0r.py check --evidence-dir adapters/multica/u12-p0r`.

## Side-Effect Audit

| counter | value |
| --- | --- |
| live R0 runs created / triggers issued | 0 |
| live issue/comment/assignment/rerun/mention/status mutations | 0 |
| live 05/06 or R1/R2 activation | 0 |
| Canonical Memory writes | 0 |
| product repository changes | 0 |
| production ledger writes | 0 (read-only integrity only) |
| accepted predecessor/history rewrites | 0 |
| O3 / daemon / scheduler / autonomous wake added | false |
| merges | 0 |

All new writes are confined to the isolated worktree: the two tool modules, the two test modules, the 6-file evidence bundle, the report and the `.gitattributes` entry.

## Finding Dispositions

| finding | disposition |
| --- | --- |
| `U12-P0-F1` (O2 report digest row) | `ACCEPTED_FORWARD_ERRATA` — machine-readable errata added; history unchanged |
| `U12-P0-F2` (permissive `{run:{…}}` acceptance) | `RESOLVED_BY_STRICT_RECEIPT_GATE` — strict gate is the only R0 receipt entrypoint; no bypass/fallback |
| `U12-P0-F3` (attribution ≠ route) | `BOUND_BY_ROUTE_PROVENANCE` — route persisted from issuing ledger; attribution text never used |
| `U12-P0R-F4` (duplicate JSON keys last-wins) | `RESOLVED_BY_STRICT_DUPLICATE_KEY_DECODER` — repeated keys at every nesting level, same-valued included, refused during decoding with zero O2-parser calls |

New observations (informational, outside the R0 path):

- `U12-P0R-OBS-01`: the accepted `tools/u12_preflight.py` CLI `pins` subcommand calls an undefined `cmd_pins` (`NameError`). It is not on the R0 issuing path and pin verification is provided by `tools/u12_p0r.py verify`; the accepted byte was deliberately **not** repaired here because any accepted-artifact change is outside this task's authority. Recorded for a future forward repair.

## Risks

- `issue rerun` still has no platform idempotency key; the strict gate narrows the receipt contract but cannot remove lost-response risk — the trusted untruncated listing + no-retry rule remains the boundary.
- The strict wrapper rule (competing keys fail closed) is intentionally stricter than any historical behavior; a future platform response carrying extra wrapper keys would stop as `TRIGGER_AMBIGUOUS` rather than be parsed. That is the approved fail-closed direction.
- Duplicate-key rejection is intentionally stricter than every historical consumer: any live receipt repeating a key (even an informational one inside an ignored extra field) stops as `TRIGGER_AMBIGUOUS`. That is the approved fail-closed direction for an ambiguous receipt.
- Regeneration determinism is asserted against the current read-only production state (tip `sha256:c9683898…`); once the canary writes an intent, byte-comparison of the ledger evidence file is expected to differ by design.
- The wiring proof is static AST + instrumented-parser evidence over the fixed module digest; it proves the shipped bytes of `tools/u12_strict_receipt.py`, not future edits.

## Blockers

None for acceptance. R0 activation remains a Lead-only decision from this artifact.

## Verdict

`U12_P0R_READY_FOR_LEAD_REVIEW` — the live R0 issuing path now accepts exactly the three Human-approved receipt shapes through a forward-only strict gate bound to an exact file digest, refuses `{run:{…}}`, every other unauthorized/ambiguous shape and every duplicate-key receipt (all nesting levels, same-valued included) before the immutable O2 parser, persists the trigger route from the issuing ledger, records F1 as forward-only errata, and leaves every accepted pin, byte and ledger tip unchanged. `r0_canary_authorized: false`; R1/R2, 05/06 and Merge remain untouched.

## Recommended Lead Decision

Accept this F4-repaired artifact and, if approved, authorize the first live R0 canary per `adapters/multica/u12-p0r/proposed-r0-canary-plan.json` (one non-trigger READY handoff, `TRIGGER_ISSUING` persisted before the sole `issue rerun`, strict-gate receipt, exactly-one-run correlation against the trusted untruncated listing, ambiguity = manual stop/no retry).

## Ready for Review

Yes — R2 safety repair before controlled live canary, returned to Engineering Lead only. Evidence bundle: `adapters/multica/u12-p0r/` (manifest digest `sha256:95a2be772aa837dd7507503a84d5eaefc4e43057f216663484d4fd703f9f496e`).
