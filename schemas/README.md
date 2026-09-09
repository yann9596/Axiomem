# V1.1 Schemas

JSON Schema (draft 2020-12) documents, one per canonical object type. All V1.1
Canonical YAML files are stored as YAML whose structure validates against the
matching schema here (`tools/context_cli.py validate-canonical` enforces this
by converting YAML → JSON before validation).

| Schema | Canonical target |
|---|---|
| `project-registry.schema.json` | `team-context/registry/projects.yaml` |
| `project-anchor.schema.json` | `project-context/<project-id>/project.yaml` |
| `checkpoint.schema.json` | `team-context/checkpoint.yaml`, `project-context/<project-id>/checkpoint.yaml` |
| `rule.schema.json` | `*/rules/RULE-*.yaml` |
| `fact.schema.json` | `*/facts/FACT-*.yaml` |
| `case.schema.json` | `*/cases/CASE-*.yaml` |
| `finding.schema.json` | `runtime/v1.1/findings/*.json`, `migration/findings/*.json` |
| `role-profile.schema.json` | `team-context/roles/*.yaml` |
| `evidence-ref.schema.json` | shared `$defs` (scope, verification, ref grammar) |
| `context-package.schema.json` | runtime `context build` output only |
| `legacy-inventory.schema.json` | Gate A input: `migration/legacy-inventory.yaml` (optional; missing is fail-closed) |
| `authority-claim-evidence.schema.json` | Gate A input: `migration/authority-evidence.yaml` (claim-support + scope-coverage) |
| `replay-metric-evidence.schema.json` | Gate C input: `migration/replay-evidence/<task_id>.yaml` |
| `replay-expectation-lock.schema.json` | Gate C input: `migration/replay-expectation.lock.yaml` |

## Context Handoff Native API (T00, YZT-46)

`context-handoff/` holds the frozen Native API contract schemas for
`prepare_handoff` / `self_check` (see `context-handoff/README.md` for the
authoritative contract, implementation mapping, task fingerprint contract and
framework-neutral boundary):

| Schema | Contract object |
|---|---|
| `context-handoff/handoff-common.schema.json` | shared $defs (task_snapshot, anchor_digest, built_from, candidates, status enums) |
| `context-handoff/prepare-handoff-request.schema.json` | `prepare_handoff_request` |
| `context-handoff/context-plan.schema.json` | `context_plan` (deterministic PLAN output) |
| `context-handoff/semantic-compose-result.schema.json` | `semantic_compose_result` (bounded semantic phase output) |
| `context-handoff/prepare-handoff-result.schema.json` | `prepare_handoff_result` (package `$ref`s `context-package.schema.json` — no duplicate package schema) |
| `context-handoff/self-check-request.schema.json` | `self_check_request` |
| `context-handoff/self-check-result.schema.json` | `self_check_result` |

`tools/chandoff.py` holds the executable side of the contract (task
fingerprint, built_from content revisions, PLAN-subset validation,
framework-neutral boundary scan); `tools/tests/test_context_handoff_contracts.py`
verifies all of it.

## Shared grammar (from evidence-ref.schema.json)

- **scope**: `team | cross_project | project | task` with deterministic
  validity constraints (project scope requires `project_id`; cross_project
  requires explicit `projects` with ≥2 entries; task requires both).
- **verification**: `verified | partially_verified | unverified | conflicted |
  refuted`. Numeric confidence values are not permitted anywhere in V1.1.
- **ref grammar**: `multica://issue/<ref>` · `multica://project-context/<project>`
  · `adr://<n>` · `doc://<repo>/<path>@<revision>` ·
  `repo://<repo>@<revision>/<path>` · `registry://<project-id>` ·
  `project://<project-id>/checkpoint`.

## Documented deviations from the design document

1. **Registry path**: design doc §4.1 sketches
   `team-context/projects/registry.yaml`; the implementation contract (Spec
   §3.1) says `registry/projects.yaml`. The implementation contract wins
   (design doc = concept authority, Spec = implementation contract), so the
   Registry lives at `team-context/registry/projects.yaml`. Recorded per YZT-39
   Migration Plan v2 §0.2.
2. **Schema file format**: schemas are JSON Schema files (`.schema.json`)
   instead of the design doc's `.schema.yaml` suggestion, so validation tooling
   is unambiguous. Content structure follows the Spec.

## Gate evidence contracts (YZT-42)

These files are **inputs to executable gates**, not Canonical Memory. Context
Engineer supplies the real 29-object inventory, 14-Rule claim-support /
scope-coverage records, replay metric evidence, and a pre-run expectation
lock. Software Engineer owns the checkers. Missing or corrupt evidence is
FAIL; gates must not default `true` / `0` / `100%`.

| Contract | Path | Fail-closed when |
|---|---|---|
| Legacy inventory | `migration/legacy-inventory.yaml` | file missing, schema invalid, or IDs disagree with filesystem |
| Per-object map | `migration/migration-map.yaml` `legacy:` list items | object in inventory has no `legacy` entry (silent drop); section-level prose (e.g. grouped signals) does not count |
| Authority evidence | `migration/authority-evidence.yaml` | file missing, or a Rule `authority_ref` lacks `claim_support.supported` + `scope_coverage.covers` |
| Replay metrics | `migration/replay-evidence/<task_id>.yaml` | file missing; `false_canonical` / `false_forget` / `issue_noise` are then `null`, not 0 |
| Expectation lock | `migration/replay-expectation.lock.yaml` | file missing, or sha256 mismatch vs current expectation files |
