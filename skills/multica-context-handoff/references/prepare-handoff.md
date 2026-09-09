# PREPARE_HANDOFF protocol

Build and (optionally) publish the Role Context Package for one task and one
target role. Run every command with the verified Context repository root as
`--repo`, and keep all artifacts in one scratch out-dir you pass as
`--out-dir` (outside the repository tree).

## Step 1 — Resolve the request inputs

Collect, or obtain from the caller, all of the following before starting. If
any is missing, stop and ask — nothing is guessed:

- target issue (Multica issue id or identifier) and the current trigger-thread
  routing context (parent comment id) when publication is expected;
- **target role** (the role that will consume the package) and **caller role**
  (your current role);
- **purpose** (e.g. `implementation`, `design`, `review`, `qa_verification`);
- **project mapping**: either an explicit Memory registry `project_id`
  (`--project-id`) or a caller-provided map file (`--project-map`, JSON:
  Multica project id → registry project id). A missing or ambiguous mapping
  fails closed in T05; do not invent one from a Multica project UUID.

## Step 2 — Snapshot + PLAN (deterministic)

```bash
python <skill>/scripts/handoff_pipeline.py prepare \
  --repo <root> --out-dir <scratch> \
  --issue <issue-id> --target-role <role> --caller-role <your-role> \
  --purpose <purpose> --project-id <registry-project-id> \
  [--decision-comment <comment-id> ...] [--decision-marker <text> ...] \
  [--issue-file <captured.json>] [--thread-file <comment-id>=<captured.json>]
```

The T05 adapter reads the authoritative issue (online through its read-only
allowlist, or offline from captured `--issue-file/--parent-file/--thread-file`
fixtures) and freezes a `prepare_handoff_request`; requirements, acceptance
criteria, and decisions enter only through explicit frozen markers in the
authoritative content, each with provenance. T01 then builds the PLAN —
including the Finding Gate — without calling a model.

Read the JSON result:

- `ok: true, status: "PLAN_READY"` → continue at Step 3. Note `plan_id`,
  `semantic_jobs`, `candidates`, `case_search_allowed`, and the artifact paths
  (`request.json`, `plan-envelope.json`).
- `ok: false, stage: "plan", blocked: true` → **stop the handoff here.** Do
  not compose, finalize, or publish. Return the deterministic escalation
  reasons (`escalation`, `finding_gate.blocked_findings`) to the caller; the
  out-dir keeps `request.json` and the blocked `plan-envelope.json` as the
  escalation evidence record.
- `ok: false` with `stage: "snapshot"` → the adapter failed closed (missing
  project mapping, CLI contract drift, ...). Fix the caller-supplied inputs
  and retry once with identical semantics; otherwise stop and report the
  bounded error.

## Step 3 — Bounded semantic compose (the only LLM step)

Read `plan-envelope.json`. Execute **only** the PLAN's listed
`semantic_jobs` and select **only** from the PLAN's candidate sets
(`candidates.rules/.facts/.cases/.checkpoint_entries/.conflicts`). Emit a
frozen `semantic_compose_result` — schema:
`schemas/context-handoff/semantic-compose-result.schema.json` in the Context
repository. Required shape: `plan_id` plus the selection id lists
(`selected_rule_ids`, `selected_fact_ids`, `selected_case_ids`,
`checkpoint_entry_ids`, `conflict_ids`) and `semantic_notes` (free text is
annotation only — never a selection channel).

Forbidden, and deterministically rejected: selecting any id not in the PLAN
(`ADD_MEMORY_NOT_IN_PLAN`), claiming a different scope (`EXPAND_SCOPE`),
inventing rule authority (`INVENT_AUTHORITY`), promoting verification
(`PROMOTE_VERIFICATION`), changing project phase, bypassing the Case gate
(`CASE_GATE_OVERRIDE`), writing Canonical Memory, re-running the Finding
Gate. These rejections cannot be repaired by re-snapshotting: a fresh
snapshot of the same inputs yields the same PLAN and the same rejection.
Free text never selects: `semantic_notes` and `context_summary` are
annotation only, and a hidden selection inside them ships nothing. Select
genuinely for every listed job — handing in empty selections for a listed
job is not caught by FINALIZE and only hollows the package; if the PLAN's
candidates genuinely do not cover a job, say so in `semantic_notes` and let
the caller decide.

Write the result to `<scratch>/compose-result.json`, then run validation and
FINALIZE in one deterministic step:

```bash
python <skill>/scripts/handoff_pipeline.py finalize \
  --repo <root> --out-dir <scratch> \
  --plan-file <scratch>/plan-envelope.json \
  --result-file <scratch>/compose-result.json \
  --request-file <scratch>/request.json \
  --repairs-used <0|1>
```

- `ok: true` → read `status` from the FINALIZE result (Step 4). The T02
  envelope (`compose-validation.json`) and the frozen
  `prepare_handoff_result` (`result.json`) are written for you.
- `ok: false, status: "REJECTED", repair_allowed: true` → **at most one
  repair**: regenerate the compose result using only the validator `errors`
  and the same PLAN (fix formatting/subset errors). Then rerun `finalize`
  with `--repairs-used 1`. The repair must not retrieve new memory, add
  candidates, change scope, invent authority, or bypass Case eligibility.
- `ok: false, repair_exhausted: true` → stop the handoff and report; do not
  attempt another repair.

## Step 4 — FINALIZE status (deterministic; never yours to choose)

- **READY** → normal-ready. Publish (Step 5) when publication was part of the
  caller's request, and return a `HANDOFF_READY` result carrying
  `package_id`, `built_from`, and the comment provenance from the publisher.
- **PARTIAL** → never normal-ready. Do **not** start any downstream work.
  Publish only if the caller explicitly authorizes PARTIAL publication (the
  `publish` command refuses without `--allow-partial` plus
  `--authorize-publish`); all gaps (`gaps`, `open_conflicts`) are preserved
  unchanged. Return a review-required result with the gap list.
- **BLOCKED** → never published, never triggered. Return the deterministic
  escalation reason (`escalation.reason`, `gaps`) and stop.

## Step 5 — Publication (T06, authorized, non-trigger)

```bash
python <skill>/scripts/handoff_pipeline.py publish \
  --repo <root> \
  --issue <issue-id> --result-file <scratch>/result.json \
  --prepared-by <caller-agent-or-user-id> \
  [--parent <trigger-comment-id>] [--allow-partial] \
  --authorize-publish
```

Use `--dry-run` first when the caller wants to inspect the rendered `/note`
record. The publisher re-validates the frozen envelope, refuses BLOCKED and
unauthorized PARTIAL before any write, is idempotent for an identical
already-published record, and performs exactly one allowlisted
`issue comment add` write (the temp body file is deleted afterwards). The
returned provenance includes the comment reference and the zero-side-effect
`trace.guarantees`.

This skill never performs the downstream assignment or mention for the
handoff — not on READY, PARTIAL, or BLOCKED. If the caller's workflow expects
one, that decision belongs to a later workflow stage, not here.

## Result contract

Return to the caller one of: `HANDOFF_READY` (with package/comment
provenance), `REVIEW_REQUIRED` (PARTIAL + gaps + optional publication
provenance), `BLOCKED` (reasons + provenance), or a bounded stop (adapter/
compose errors, repair exhausted). Always include the artifacts dir so the
caller can inspect `request.json`, `plan-envelope.json`,
`compose-validation.json`, `result.json`.
