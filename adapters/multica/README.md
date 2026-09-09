# Multica Adapter (T05 snapshot + T06 note publish/discovery, YZT-58/YZT-59)

Read-only Multica Issue Snapshot Adapter (T05). Converts authoritative Multica
issue data into the frozen, framework-neutral `prepare_handoff_request` input
shape (`schemas/context-handoff/prepare-handoff-request.schema.json`,
T00/YZT-46). Since YZT-59 this directory also documents the T06 non-trigger
`/note` CONTEXT_HANDOFF publisher + discovery layer (below).

Implementation: `tools/chandoff_adapter.py` (this repo keeps all executable
tools under `tools/`, matching the T01-T04 layout). This directory holds the
adapter's configuration and documentation.

## Responsibilities and boundaries

The adapter only does `Multica object -> neutral snapshot`:

- reads the deployed `multica` CLI, read-only commands only:
  `issue get`, `issue comment list --thread`, `version`. Every issued argv is
  checked against a read-only allowlist; write commands are refused.
- requirements / acceptance criteria / decisions are admitted only through
  frozen explicit markers in the authoritative issue body (requirements:
  `requirements`, `required work`, `required changes`, `required tests`,
  `required snapshot mapping`; acceptance: `acceptance criteria`,
  `acceptance evidence`, `done criteria`, `done`) or through caller-selected
  comment ids. Each admitted item carries exact provenance in the trace.
  Ordinary progress comments are never fetched and never reach the
  task fingerprint.
- the current assignee is never mapped: the frozen request contract has no
  assignee field; none is added to Core schemas.

The adapter does NOT: interpret Memory Scope policy, select Canonical
context, call `prepare_handoff`, publish results, rebuild Memory, read the
Memory registry or any canonical tree, call any model, or touch the network.

## Explicit project mapping (fail closed)

A Multica project UUID, a missing project, or a null project is never
automatically a Memory registry `project_id`. The mapping must be explicit:

1. `--project-id <registry-id>` — caller designates the registry project id.
2. `--project-map <file>` — JSON object `"<multica project uuid>": "<registry project id>"`.
   `project-map.json` here is the maintained mapping (values mirror the
   `multica_project_id` fields of the canonical Project Registry
   `team-context/registry/projects.yaml`); update it via the registry, never
   by guessing. Default when the flag is omitted:
   `adapters/multica/project-map.json`.

No stable mapping exists -> bounded error `project_mapping_unresolved`, stop.

## CLI compatibility note (implementation plan §30.4)

- Deployed CLI at capture/verification time: `multica version` → `v0.4.41`
  (go1.26.8, windows/amd64, commit `4aca890a2`, built 2026-09-07).
- Commands used and verified against the deployed help: `issue get <id>
  --output json` (accepts identifier or UUID), `issue comment list <issue>
  --thread <id> --full --output json` (`--full` keeps resolved threads
  verbatim so any selected comment is deterministic), `version --output json`.
- The adapter validates returned issue JSON against its required contract
  field set (`id`, `identifier`, `title`, `description`, `parent_issue_id`,
  `project_id`) and stops bounded with `incompatible_cli_contract` when a
  deployed CLI drifts from it. Malformed JSON (`cli_json_malformed`), command
  failure / permission failure (`cli_command_failed`) and a missing CLI
  (`cli_unavailable`) are also bounded stops. No fallback shapes are guessed.
- Real captured CLI JSON used as offline fixtures lives in
  `tools/fixtures/adapter/` (issue `YZT-58`, parent `YZT-39`, the YZT-56
  report thread on `YZT-39`).

## Usage

```bash
# live, this issue, explicit caller mapping (issue carries no Multica project):
python tools/chandoff_adapter.py build \
  --issue YZT-58 --target-role software-engineer --caller-role engineering-lead \
  --purpose implementation --project-id web-imagegen

# offline, byte-stable reproduction from captured fixtures:
python tools/chandoff_adapter.py build \
  --issue 01a085f5-ac12-7738-a64d-a7d18394708e \
  --target-role software-engineer --caller-role engineering-lead \
  --purpose implementation --project-id web-imagegen \
  --issue-file tools/fixtures/adapter/issue_get_yzt58.json \
  --parent-file tools/fixtures/adapter/issue_get_parent_yzt39.json \
  --thread-file 01a085f1-c4bf-7b6c-af91-3c7160e38c0d=tools/fixtures/adapter/comment_thread_yzt56_report.json \
  --decision-comment 01a085f1-c4bf-7b6c-af91-3c7160e38c0d
```

Output envelope: `{"ok": true, "request": <frozen schema shape>,
"task_fingerprint": "sha256:…", "trace": {provenance, cli argv, guarantees}}`.
Errors print `{"ok": false, "error": {code, message, details}}` and exit 2.

## Out of scope (parked tasks)

The shared Skill (T07), Agent Instructions (T08) and any replay/rollout/
enablement work. The T05 snapshot adapter never publishes and never triggers
agents, mentions, assignments, or new runs.

---

# T06 — Non-trigger `/note` CONTEXT_HANDOFF publisher + discovery (YZT-59)

Implementation: `tools/chandoff_note.py`. Adapter-only state machine on top of
the frozen T00-T03 pipeline: it persists a validated READY/PARTIAL
`prepare_handoff_result` as a `/note` comment record and resolves the latest
valid record for `(task_ref, target_role)`. The record/envelope is Multica
Adapter state, NOT a Native API/Core object: no frozen schema is modified, no
second Task Context Package schema exists, and the `CONTEXT_HANDOFF` marker
appears in no frozen schema (asserted by `frozen_contract_supports_publish`).

## Publish contract

- Consumes ONLY a frozen-schema-valid result: full re-validation against the
  frozen `prepare-handoff-result` + reused `context-package` schemas, package
  integrity re-checks via the existing T03 ref-grammar helpers
  (`package.request.task_id == task_ref`, `request.role == role`, ref
  grammar, status/gap consistency). Duplicated §31.2 display metadata must
  exactly match the validated envelope.
- READY publishable; PARTIAL only with explicit caller authorization
  (`--allow-partial`), gaps kept visible and unchanged; BLOCKED refused
  before any subprocess or write (`publish_refused` /
  `blocked_not_publishable`).
- The rendered body's first line is exactly `/note` (deployed non-trigger
  command), followed by the versioned marker `CONTEXT_HANDOFF_RECORD v1`, a
  single-line `CONTEXT_HANDOFF_META {…}` header (the §31.2 metadata:
  `package_id`, `task_ref`, `target_role`, `status`, `built_from`,
  `prepared_by`, `prepared_at`) and exactly one ```json fenced payload
  `{"record_version": 1, "context_handoff": {…}, "prepare_handoff_result":
  {…}}`. The body scan refuses any `mention://` link and any line other
  than line 1 that looks like a slash command.
- Write allowlist: exactly ONE `issue comment add` argv per publish, always
  `--content-file` (UTF-8, raw bytes — Windows newline translation would
  corrupt the framing), inside the current working directory, temp file
  deleted after the command completes (success or failure). Inline content,
  stdin, attachments and `--allow-external-file` are refused. No issue
  create/update/status/assign/rerun/cancel/metadata/label, no mention, no
  attachment, no comment delete/resolve.
- Optional caller-supplied parent comment id routes the record as a reply;
  never invented, never reused from a stale value.
- Idempotent retry: an already-valid identical record with the same
  `package_id + task_ref + role` returns the existing comment reference
  with zero writes. Any other reuse of the same package id (other task/role,
  different content, or an unverifiable/corrupt record) fails closed
  (`package_id_conflict`).

## Discovery contract

- Complete read: `issue comment list <issue> --full --output json` — the
  deployed CLI's complete-thread enumeration, resolved threads verbatim
  (`--full` disables folding). Verified live on a 41-comment issue
  (17 roots + 24 replies, no cursor). Clipped `--summary` reads,
  `--recent N` windows and folded threads are never used as completeness
  proof; if the deployed CLI reports a pagination cursor on a complete read
  the contract has drifted and discovery stops bounded
  (`incompatible_cli_contract`) instead of reading less than everything.
- A comment is a record candidate iff it contains the exact marker line
  `CONTEXT_HANDOFF_RECORD v1`; validity requires: first line exactly
  `/note`, parseable single-line meta header, exactly one fenced payload
  with exactly the three record keys, `record_version == 1`, meta/payload/
  envelope metadata duplication equality, and full frozen validation of the
  envelope + package. Server `created_at` and comment id are the sole
  ordering/provenance authority; embedded `prepared_at`/`prepared_by` never
  establish recency or trust.
- Records bound to other task/role pairs and ordinary comments are ignored.
  Newest valid bound candidate wins; comment id (lexicographic, descending)
  breaks `created_at` ties. If the NEWEST same-target candidate is
  malformed, schema-invalid, integrity-invalid or package-id-conflicting,
  resolution fails closed (`latest_handoff_invalid`) instead of silently
  exposing an older package. Two valid candidates sharing a package id with
  different content also fail closed.
- Output shape: `{"ok", "found", "envelope", "record", "meta", "comment",
  "selection"}` — the envelope is the unchanged `prepare_handoff_result`
  that T07 can hand into the T04 self-check `packages=` seam. T06 never
  calls self-check.

## Deployed non-trigger proof (controlled, in-scope target YZT-59)

`multica version` → `v0.4.41`. Live evidence captured in
`tools/fixtures/note/` (`proof_evidence.json`, `runs_before_yzt59.json`,
`runs_after_yzt59.json`, `comment_list_yzt59_with_record.json`,
`comment_add_readback_yzt59.json`):

- A genuine pipeline envelope (T05 snapshot of YZT-59 → T01 PLAN_READY →
  T02 ACCEPTED → T03 PARTIAL, `package_id
  CTX-context-engineer-5eaa999d5be8d059`, one real non-blocking canonical
  conflict surfaced) was published with explicit caller authorization.
- `multica issue runs YZT-59 --output json` before and after the single
  publication contain exactly the same one run (the publisher's own run,
  `01a08610-ab8d-75ca-bb24-b316eb1f2b8c`): the `/note` publication created
  no additional Multica run.
- The stored comment round-trips through `resolve` byte-for-byte at the
  envelope level (canonical-JSON identical) with server provenance
  (comment `01a0862a-efb6-7bdc-9317-23089673e36c`, top-level).
- Republish returns the existing comment with zero write commands.
- Deployed normalization note: the server strips the trailing newline of
  the stored body (content otherwise byte-identical; record framing and
  parsing unaffected).

## Usage

```bash
# dry run (validate + render, no CLI call):
python tools/chandoff_note.py publish --issue YZT-59 \
  --result-file result.json --prepared-by <agent-id> [--allow-partial] --dry-run

# single live publish (optional caller-supplied reply routing):
python tools/chandoff_note.py publish --issue YZT-59 \
  --result-file result.json --prepared-by <agent-id> [--allow-partial] \
  [--parent <comment-id>]

# resolve the latest valid record for T07:
python tools/chandoff_note.py resolve --issue YZT-59 \
  --task-ref multica://issue/YZT-59 --role context-engineer

# inspect every record candidate on an issue:
python tools/chandoff_note.py records --issue YZT-59
```
