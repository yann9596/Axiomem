# Context Handoff Native API Contract (T00, YZT-46)

Status: **FROZEN** — this is the single interface baseline for `prepare_handoff`,
`self_check`, the Semantic Runtime and the Multica Adapter work that follows
(T01+). Authority relations between the two V1.1 handoff design docs:

- `docs/context_handoff_native_api_design_v1.1.md` — capability semantics,
  architecture boundary, Core / Semantic Runtime / Adapter responsibility split.
- `docs/context_handoff_multica_implementation_v1.1.md` — task order, task
  boundaries, Multica landing constraints.

Where they conflict on implementation detail, the framework-neutral semantics of
the Native API design win; Memory Core boundaries are never bent to make a
framework adapter convenient.

---

## 1. CURRENT_IMPLEMENTATION_MAPPING

Determined by reading the actual V1.1 repo state (not from the design docs).
The V1.1 rebuild is complete and cut over (`CUTOVER_SUCCESS_v1.1_active`):
canonical = `team-context/` + `project-context/`; `memory/` is V1 heritage in
its cleanup window; derived `index/v1.1/memory.db` is rebuildable.

| Native API requirement | Existing V1.1 component | Decision |
|---|---|---|
| Task identity | free-string `task_id` in `cbuild.build_package` request | **extend**: Native API freezes opaque `task_ref` (URI); `task_ref` string doubles as `package.request.task_id` so the package schema stays untouched |
| Task snapshot | none (no title/description/requirements normalization existed) | **new**: `task_snapshot` $def, normalized by the Adapter outside Core |
| Scope Resolver | `tools/cbuild.py: resolve_scope` + `cutil.scope_allows` + `evidence-ref.schema.json $defs/scope` | **reuse** unchanged |
| Project Registry | `team-context/registry/projects.yaml` + `project-registry.schema.json` | **reuse** unchanged |
| Project Anchor | `project-context/<pid>/project.yaml` + `project-anchor.schema.json`; digest via `cbuild.anchor_digest` | **reuse** unchanged |
| Team/Project Checkpoint | `team-context/checkpoint.yaml`, `project-context/<pid>/checkpoint.yaml` + `checkpoint.schema.json`; slicing via `cbuild._checkpoint_entries`, `cbuild.collect_conflicts` | **reuse** unchanged |
| Rules / Facts / Cases | `rule|fact|case.schema.json` + `cdata.load_all_docs`, hard filters in `cbuild.build_package` | **reuse** unchanged |
| Role Context Profile | `team-context/roles/*.yaml` + `role-profile.schema.json` + `crole.policy_of` | **reuse** unchanged |
| Role id vocabulary | `project-registry.schema.json $defs/role_id` (hyphenated, e.g. `software-engineer`) | **reuse**: Native API role strings are the V1.1 role_id enum; design-doc underscore spellings are prose only |
| Evidence / Authority | `evidence-ref.schema.json` + `cauthority.evaluate_all_rules` | **reuse** unchanged |
| Task Context Package | `context-package.schema.json` + `cbuild.build_package` | **reuse as FINALIZE base**: `prepare_handoff_result.package` `$ref`s the existing schema; no second package schema exists |
| Finding (report_finding) | `finding.schema.json` + `runtime/v1.1/findings/` | **reuse** — existing capability, not rebuilt in this round |
| Challenge (challenge_context) | V1 `challenge` CLI + `external-signal.schema.json` (`source_type: memory_challenge`) surfacing as package conflicts | **reuse** — existing capability, not rebuilt in this round |
| CLI | `tools/context_cli.py` (`validate-canonical`, `rebuild-index`, `build`, `gate-b`, `migrate-replay`, `compat`) | **extend later** (T01+): handoff subcommands join here; T00 freezes contracts only |
| Schema validation | `tools/schema_mini.py` + `tools/validate_canonical.py` | **reuse** in the handoff contract tests |
| Tests | `tools/tests/` (unittest) | **extend**: `test_context_handoff_contracts.py` |
| Task fingerprint | none existed | **new**: frozen contract + executable helper `tools/chandoff.py` |
| built_from revisions | none existed | **new**: deterministic content revisions (see §4) |

---

## 2. Frozen objects and files

| Object | File |
|---|---|
| shared $defs (task_snapshot, anchor_digest, built_from, escalation, candidates, semantic_job vocabulary, status enums) | `handoff-common.schema.json` |
| `prepare_handoff_request` | `prepare-handoff-request.schema.json` |
| `context_plan` | `context-plan.schema.json` |
| `semantic_compose_result` | `semantic-compose-result.schema.json` |
| `prepare_handoff_result` | `prepare-handoff-result.schema.json` |
| `self_check_request` | `self-check-request.schema.json` |
| `self_check_result` | `self-check-result.schema.json` |
| executable contract helpers | `tools/chandoff.py` |
| schema + contract tests | `tools/tests/test_context_handoff_contracts.py` |

The four native capabilities: `prepare_handoff` and `self_check` are frozen
here and implemented in T01–T04; `report_finding` and `challenge_context` are
existing V1.1 capabilities and are not rebuilt.

Architecture boundary (dependency only downward, never back up):

```text
Framework / Multica
        │
Skill / Hook / CLI / future MCP
        ▼
Context Native API
        ▼
Context Semantic Runtime
        ▼
Context & Memory Core
```

## 3. Status semantics (frozen)

`prepare_handoff_result.status` — exactly `READY | PARTIAL | BLOCKED`:

- **READY** — the target role now holds the minimum sufficient context to begin
  the current phase of work. It is a statement about this phase, not a
  permanent certification.
- **PARTIAL** — usable context exists but explicit gaps remain; whether work may
  start is decided by policy/caller based on impact. Gaps must stay visible:
  they live in `package.blocked_by` and `package.open_conflicts`, never hidden.
- **BLOCKED** — missing/conflicting objects, or authority/scope problems make it
  unsafe for the target role to start. The normal downstream handoff must not
  proceed (schema forces `escalation.required = true`).

`self_check_result` — exactly `READY | REFRESH_REQUIRED | BLOCKED`, paired 1:1
with `action: USE_EXISTING | REFRESH | ESCALATE` (schema-enforced in both
directions). `REFRESH_REQUIRED` / `BLOCKED` must carry ≥ 1 reason; standard
reason vocabulary: `package_missing`, `package_stale`, `task_changed`,
`role_mismatch`, `scope_mismatch`, `package_not_ready`,
`memory_revision_changed`, `registry_revision_changed`,
`role_profile_revision_changed`.

## 4. Task fingerprint contract (frozen)

`task_fingerprint` = `sha256:<64 lowercase hex>` over a canonical JSON payload
of exactly seven inputs:

```yaml
fingerprint_inputs:
  - task_ref                 # opaque task identity URI
  - title                    # from task_snapshot
  - description              # from task_snapshot
  - requirements             # from task_snapshot
  - acceptance_criteria      # from task_snapshot
  - explicit_scope           # request.project (framework-neutral scope intent)
  - relevant_human_decisions # task_snapshot.relevant_decisions
```

Normalization (frozen, deterministic):

1. strings: `strip()`; lists: strip each item, drop empties, **sort + dedupe**
   (order and repetition are not task definition);
2. payload = JSON object with exactly the seven keys, serialized
   `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`,
   UTF-8 encoded, SHA-256, hex lowercase, prefixed `sha256:`;
3. **excluded by freeze**: all discussion/comment content, progress notes,
   status chatter, run logs, `caller`, `purpose`, `options`,
   `parent_task_ref` (a relation pointer, not the task definition). Ordinary
   progress comments therefore never invalidate a package.

Executable: `chandoff.task_fingerprint(...)`, `chandoff.fingerprint_payload(...)`
(`tools/chandoff.py`).

`built_from` revisions (same `sha256:` encoding) are content revisions over the
canonical V1.1 trees, disjoint by construction:

- `memory_revision` — parsed content of all anchors, rules, facts, cases and
  checkpoints (`team-context` + `project-context`);
- `registry_revision` — `team-context/registry/projects.yaml`;
- `role_profile_revision` — `team-context/roles/*.yaml`.

Each is a sha256 over the sorted manifest of `(relative path, canonical JSON of
parsed document)` so file formatting, line endings and key order cannot move a
revision. Executable: `chandoff.memory_revision()`, `chandoff.registry_revision()`,
`chandoff.role_profile_revision()`, `chandoff.compute_built_from(...)`.

## 5. Semantic Runtime boundary (frozen, not implemented here)

```text
Deterministic PLAN  (Phase A — pure deterministic, all hard filters)
        ▼
Bounded LLM Semantic Compose  (Phase B — structured semantic_compose_result only)
        ▼
Deterministic FINALIZE  (Phase C — validation, package_id, built_from)
```

- Phase B output must be structured (`semantic_compose_result`); a free prose
  context as the sole result is forbidden. Every selected ID must be a subset of
  the PLAN candidates — verified by `chandoff.validate_semantic_result` and
  re-verified deterministically in FINALIZE (T03).
- Phase B may only perform the `semantic_jobs` listed in the PLAN (frozen
  vocabulary in `handoff-common.schema.json`). It may never expand scope,
  invent authority, promote verification, change project phase, bypass the case
  hard gate, add objects outside the PLAN, or write canonical memory.
- Current executor: `mode: caller_llm_via_skill` (the calling agent's own model,
  driven by the shared skill). Future: `mode: replaceable`. T00 builds **no**
  dedicated LLM service, HTTP API, MCP server, or model routing — only the
  interface that lets a later backend be swapped without changing the Native API.

## 6. Framework-neutral boundary (frozen)

Forbidden in Native API schemas and Core schemas (scan-enforced):
dispatch-framework nouns — squads, @-trigger keywords, ownership labels
(assignee), run identifiers, stage concepts, comment routing, autopilot.

Allowed: opaque external references only, e.g. `task_ref: multica://issue/YZT-46`
— Core never parses the framework semantics behind a `task_ref`.

Audit command (also wired into the tests):

```bash
python tools/chandoff.py scan   # scans schemas/context-handoff/*.json + tools/chandoff.py
```

Prose documents (READMEs, `docs/`) are exempt by design: they must be able to
*name* the forbidden concepts in order to prohibit them.

## 7. Extension policy

- New fields: additive only, `additionalProperties: false` everywhere, new
  fields optional.
- Enum changes (statuses, actions, semantic_jobs, reason vocabulary) or input
  changes to the fingerprint contract require a contract version bump and are
  not done inside T01+ silently.
- `context-package.schema.json` remains the one and only package schema; the
  handoff result references it.

## 8. Known gap inherited from V1.1 (FIND-WIMG-HO00-000001)

**Closed in T03 FINALIZE** (`disposition: absorbed_by_existing`, no Canonical
write). `chandoff_finalize` emits package `rules[].ref` / `current_facts[].ref` /
`cases[].ref` as grammar-valid `repo://multica-memory/<canonical-path>` pointers.
The frozen `evidence-ref` `ref_string` grammar is unchanged. A real built
package is schema-validated against both `context-package.schema.json` and
`prepare-handoff-result.schema.json`.

`cbuild.build_package` still emits historical `rule:<id>` / `fact:<id>` /
`case:<id>` prefixes so Gate C replay expectation locks remain valid. PLAN
candidate identity is unaffected: candidates carry canonical `id`s; `ref` is
optional and only used for real URI targets.

---

## 9. T04 SELF_CHECK implementation notes (YZT-57; additive, no frozen field changes)

	ools/chandoff_selfcheck.py implements the frozen self_check_request /
self_check_result contract with no schema change:

- Resolution: explicit package_ref matches package_id (or store locator
  <package_id>.json) and is never silently swapped; without a ref, the
  latest envelope for (task_ref, role) is taken from caller-supplied
  packages or the gitignored store untime/v1.1/handoff-packages/.
- ole_mismatch / 	ask_changed are reported when the caller names a
  package by ref whose role/task differ; without a ref, a wrong role/task
  simply resolves to package_missing.
- Fingerprint revalidation reconstructs xplicit_scope from the candidate
  package scope: project packages reconstruct exactly
  ({"project_id": ...} == frozen equest.project shape); cross-project
  packages cannot reconstruct the original primary project id, so their
  fingerprint validity is unprovable and fails closed as package_stale
  (uncertainty demotes; a wrong READY is worse than an unnecessary refresh).
- package_stale also covers missing/incomplete uilt_from provenance.
- PARTIAL packages -> REFRESH_REQUIRED + package_not_ready (the caller
  may still choose to proceed per the frozen PARTIAL semantics);
  BLOCKED packages -> BLOCKED / ESCALATE + package_not_ready.
- Scope validity: the package scope must still resolve against the CURRENT
  Registry (registered, not archived) or scope_mismatch is reported.
- Reasons are deduplicated and emitted in the frozen vocabulary order.
- CLI: context_cli.py self-check --request-file ... [--package ...]...
  exits 0 READY / 2 REFRESH_REQUIRED / 3 BLOCKED.
- Ordinary progress chatter and parent_task_ref never enter the
  fingerprint (frozen seven inputs), so they never invalidate a package.
