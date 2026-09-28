# Multica Context & Memory System V1.1

This repository is the canonical, auditable Context & Memory store for the
Multica agent team. The active system is **V1.1** (`CUTOVER_SUCCESS` at
R10, YZT-39): canonical knowledge lives in `team-context/` and
`project-context/` as schema-validated YAML; `index/v1.1/memory.db` is a
derived, rebuildable SQLite FTS/metadata index and is never canonical.

The legacy V1.0 layout described at the bottom is retained read-only during
the compatibility/rollback window; it is not the write model.

## Ownership and lifecycle

- Only the Context Engineer normally writes canonical memory.
- New cognition enters as a **Task Finding** (`runtime/v1.1/findings/`,
  lifecycle `open -> processed`, schema `schemas/finding.schema.json`).
  Findings are temporary conversion objects, never a long-term queue; the
  durable outcome of a finding is its disposition (`create_rule`,
  `create_fact`, `create_case`, `carry_to_checkpoint`, `pointer`, `forget`,
  `issue_escalation`, `absorbed_by_existing`).
- There is no `Memory Candidate`, `USER_INSIGHT`, `EXTERNAL_SIGNAL` or
  `Business Chain` core type. User/research input verifies through the V1.1
  Evidence → Finding → Retention pipeline; business-chain semantics map to
  Derived Relation / Current Fact / Case; normative claims need an approved
  `authority_ref` (fail-closed, `tools/cauthority.py`).
- No numeric confidence anywhere; verification is
  `verified | partially_verified | unverified | conflicted | refuted`.
- Never put secrets, credentials, or full chat transcripts in this repository.

## Layout (V1.1, active)

- `team-context/`: team-scope canonical — registry, roles (profiles),
  rules, checkpoint, policies
- `project-context/<project-id>/`: project-scope canonical — anchor
  (`project.yaml`), rules, facts, cases, checkpoint
- `runtime/v1.1/`: task-scoped runtime artifacts (findings, handoff
  packages); gitignored, never canonical
- `schemas/`: V1.1 JSON Schemas (draft 2020-12) + frozen Context Handoff
  Native API contract (`schemas/context-handoff/`); see `schemas/README.md`
  (also documents the retained legacy V1.0 schema files)
- `index/v1.1/`: derived index, rebuildable from canonical at any time
  (`python tools/context_cli.py rebuild-index`)
- `tools/`: V1.1 CLI (`context_cli.py`, `chandoff.py` + T01+ handoff
  modules on the handoff chain); dependency-free
- `migration/`: V1.0 → V1.1 migration evidence, gates, cutover record
- `registry/`, `memory/`, `chains/`, `sources/`: legacy V1.0 (see below)

## Supported Multica execution

Memory Core is framework-neutral; `adapters/multica/` maps platform tasks and role identities. Project-specific HarmonyOS capabilities stay in the product repository. For the current TeachersApp1 handoff lifecycle see [supported execution](adapters/multica/APP1_SUPPORTED_EXECUTION.md); the prior scoped integration evidence is [readiness verification](adapters/multica/APP1_READINESS_VERIFICATION.md). Historical reports are not current release gates. The planning skill source lives in `skills/adaptive-task-planning/SKILL.md`.

## Commands (V1.1)

Run from the repository root with Python 3.10+:

```text
python tools/context_cli.py validate-canonical    # Gate A0/A: schema + authority + legacy accounting
python tools/context_cli.py rebuild-index         # atomically rebuild the derived index
python tools/context_cli.py check-index           # read-only source/index freshness check
python tools/memory_health.py audit --project teachers-app1  # size/hygiene, not truth
python tools/context_cli.py build --task-id ... --role ... --project ...
python tools/context_cli.py gate-b                # Gate B hard acceptance tests
python tools/context_cli.py migrate-replay        # Gate C historical replay
python tools/context_cli.py compat get|retrieve   # old V1 call translation only (writes nothing)
python tools/chandoff.py scan                     # framework-neutral boundary audit (T00)
python tools/cartifact.py revision                # U10 artifact-contract runtime (not Public Context API)
```

The V1.1 CLI never turns findings into canonical objects automatically;
authority and gates fail closed. The Context Handoff Native API
(`prepare_handoff` / `self_check`, frozen T00 contract in
`schemas/context-handoff/README.md`) builds deterministic, role-scoped Task
Context Packages; ordinary READY handoffs never require or wake the Context
Engineer — escalation only on scope ambiguity, evidence conflict, authority
gap or material findings that cannot be safely auto-processed.

`--embedding-query` style semantic-vector retrieval is not implemented:
V1.1 stores `embedding_provider=disabled`; no embedding model has been
selected and embedding must never be claimed as active.

## Memory quality and audit repair

The [2026-09-28 repair](docs/repairs/2026-09-28/implementation.md) separates
current facts from retained history and adds trusted UTF-8 byte budgets at
candidate, assembly, artifact, publication and consumer boundaries. Oversized
information is never silently truncated. Read the implementation report and
[01 application tasks](docs/repairs/2026-09-28/01-external-issues.md) for exact
validation, migration, live-deployment and paused-project boundaries.

Discovery still reads the complete live thread; only unchanged parsing is
cached. Scoped dependencies are shadow diagnostics, not permission to skip
the frozen global freshness checks. `memory_health.py source-change` emits
read-only narrowing proposals; existing Lead/02 Memory Disposition owns action.

## Legacy V1.0 (retained, read-only, cleanup window)

Kept intact since the R10 cutover for rollback (`tag
context-v1.0-pre-v1.1-20260908`) and comparison; deletion is deferred until
the observation window completes with Human acceptance:

- `memory/` — legacy Memory Units (with `confidence`, `importance` fields)
- `chains/` — legacy Memory Chains (`app1-mvp`, `web-imagegen-pilot`,
  `team-governance` chain→scope mapping now lives in `tools/cutil.py`)
- `sources/` — legacy external signals and unpromoted candidates
  (`memory_candidate` records; superseded semantics — candidates now
  converge on Task Findings)
- `registry/projects.yaml` — legacy V1 registry (Multica project UUID ids,
  tag-based isolation); canonical registry is
  `team-context/registry/projects.yaml`
- `index/memory.db` — legacy derived index, no longer rebuilt
- `tools/memory_cli.py` + `ingest|promote|retrieve|get|challenge|verify|rebuild-index.cmd`
  — legacy V1 CLI; only the `compat` translation path of the V1.1 adapter
  (`tools/ccompat.py`) may still be exercised against old calls, and it
  writes nothing.

Legacy objects are fully accounted for in `migration/legacy-inventory.yaml`
(Gate A, 29/29 mapped, zero silent drops) and stay auditable; they are not
sources for new canonical writes.
