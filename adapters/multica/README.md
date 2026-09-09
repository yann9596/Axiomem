# Multica Adapter (T05, YZT-58)

Read-only Multica Issue Snapshot Adapter. Converts authoritative Multica issue
data into the frozen, framework-neutral `prepare_handoff_request` input shape
(`schemas/context-handoff/prepare-handoff-request.schema.json`, T00/YZT-46).

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

Package discovery / latest `CONTEXT_HANDOFF` lookup (T06), handoff rendering
and publishing (T06), the shared Skill (T07), Agent Instructions (T08) and
any replay/rollout/enablement work. The adapter never publishes and never
triggers agents, mentions, assignments, or new runs.
