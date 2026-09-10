# Artifact Contract Runtime (U10 / YZT-71)

Framework-neutral **runtime** surface for cross-role artifact identity,
exact version, readiness, dependency export, invalidation, and Option A
R0/R1/R2 routing contracts.

This is **not**:

- a Memory Core type or Canonical lifecycle
- a Frozen T00 Public Schema amendment
- a Public Context API (`prepare_handoff` / `self_check` stay unchanged)
- a dispatch database, issue/run trigger, or Review/QA skill

Executable: `tools/cartifact.py`. U04/U05 consume
`u04-u05-input-contract.json` plus the schemas and catalog in this
directory. Dependency sets export into existing T00-compatible
`task_evidence` / `source_refs` without new package fields.

Authority: V1.1 implementation boundaries; Frozen T00; V2.2 Corrected
§30–§31 / §33–§34; runtime supplement §§3–8, 12–14 Option A, 21–23,
30–31, 36/S2, 37; U03 base `2e1959b`.
