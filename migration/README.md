# Migration — V1.0 → V1.1 bypass rebuild (YZT-40)

This directory holds the V1.0 → V1.1 migration artifacts for the
Context & Memory System rebuild (YZT-40, R3–R9). These are migration /
implementation artifacts, not long-term memory.

Status: **R3–R9 executed; awaiting human Cutover GO / NO-GO decision. R10 is
explicitly NOT executed in this issue.**

## Principles honored

- Bypass rebuild, not in-place overwrite: everything V1.1 lives on the
  `context-v1.1-rebuild` branch; `main` (V1.0) stays untouched and recoverable.
- Rollback baseline: tag `context-v1.0-pre-v1.1-20260908` + bundle at
  `D:\AI\context-backups\multica-memory-pre-v1.1-20260908.bundle` (outside the
  Git worktree, F-5).
- Legacy V1.0 layout (`memory/`, `chains/`, `sources/`, `registry/`, old
  `schemas/`, `tools/memory_cli.py`, tracked `index/memory.db`) remains intact
  so the V1 runtime stays operable during the shadow window. Legacy dirs are
  NOT deleted here; deletion happens at Cutover + cleanup.

## Contents

| Path | Purpose |
|---|---|
| `snapshot.yaml` | R0-style snapshot of the source state this migration started from |
| `migration-map.yaml` | every legacy object → target/disposition (Gate A input) |
| `findings/` | legacy candidates / task deliveries processed through the V1.1 finding pipeline |
| `replay/` | R9 replay case definitions (expected vs actual) |
| `gate-results/` | Gate A0 / A / B / C machine-run results |
| `repo-local/web-imagegen/` | F-3 deliverable: V1.1 repo-local context for Web-ImageGen (staged; product-repo landing is a Cutover-window SE write) |

## Documented design-vs-spec deviation

Registry physical path: design doc §4.1 sketches
`team-context/projects/registry.yaml`; the Spec (implementation contract) §3.1
uses `registry/projects.yaml`. Per "design doc = concept authority / Spec =
implementation contract", the Registry lives at
`team-context/registry/projects.yaml`. Recorded in `schemas/README.md`.
