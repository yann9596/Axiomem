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
