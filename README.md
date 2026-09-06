# Multica Team Memory V1

This repository is the canonical, auditable memory store for the Web-ImageGen
agent team. JSON files are the source of truth. `index/memory.db` is derived and
may always be deleted and rebuilt.

## Ownership and lifecycle

- Only the Context Engineer normally writes `memory/` or approves a candidate.
- New observations enter `sources/candidates/` as `memory_candidate` records.
- A candidate is never indexed and never becomes a long-term fact automatically.
- After deduplication, source verification, conflict review, classification and
  importance assessment, the Context Engineer creates a `memory_unit` with
  `status: canonical` and records the candidate/source IDs in `sources`.
- Superseded facts stay auditable: set `status: superseded` and link the replacing
  unit with a `supersedes` relationship.
- Never put secrets, credentials, or full chat transcripts in this repository.

## Layout

- `memory/`: canonical Memory Units (source of truth)
- `chains/`: curated ordered Memory Chains
- `sources/`: external signals and unpromoted candidates
- `schemas/`: JSON Schemas and copyable templates
- `index/`: rebuildable SQLite FTS/metadata/relationship index
- `tools/`: dependency-free command line implementation

## Commands

Run commands from the repository root with Python 3.10 or newer:

```text
python tools/memory_cli.py ingest path/to/record.json --actor-role "Context Engineer"
python tools/memory_cli.py verify
python tools/memory_cli.py rebuild-index
python tools/memory_cli.py retrieve "owner boundary" --limit 5
python tools/memory_cli.py retrieve "Context Engineer" --tag governance --relations
python tools/memory_cli.py get "TASK-ID" "Software Engineer" "decide the approach" --chain web-imagegen-pilot
python tools/memory_cli.py promote <candidate-id> --actor-role "Context Engineer" --dry-run
python tools/memory_cli.py challenge <unit-id> --reason "what is disputed" --actor "Solution Architect"
```

`ingest` routes candidates and external signals to `sources/`; only canonical
Memory Units require the exact owner role and enter `memory/`. The command does
not turn candidates into facts. Use `--dry-run` to validate and route without
writing.

Retrieval combines SQLite FTS5 with metadata filters (`--tag`, `--min-importance`,
`--min-confidence`) and optional one-hop relationship expansion (`--relations`).
When FTS query syntax is unsuitable it safely falls back to tokenized matching.

The reserved `--embedding-query` switch intentionally fails with an explanatory
message. V1 stores `embedding_provider=disabled`; no embedding model has been
selected and semantic-vector retrieval must not be claimed as active.

`get` assembles a Context Package (see `schemas/context-package.schema.json`)
from retrieval plus an optional chain: known facts, relevant memory, declared
conflicts and recorded challenges, structural gaps, and pending candidates. It
performs retrieval and formatting only; sufficiency is judged by the Context
Engineer, not automated.

`promote` turns one reviewed candidate in `sources/candidates/` into a canonical
Memory Unit. It requires `--actor-role "Context Engineer"`, never overwrites an
existing unit, marks the candidate `promoted` (audit preserved), rebuilds the
index and verifies. `--dry-run` prints the plan without writing.

`challenge` records a dispute against a canonical unit as a `disputed` external
signal (`source_type: memory_challenge`) under `sources/`, hashing the unit
file's content; the unit itself is never modified or deleted. `get` surfaces
challenges as conflicts for Context Engineer adjudication.

## Review checklist before canonicalization

1. Search existing units for the candidate's `dedupe_key` and core terms.
2. Verify every material claim against the cited source.
3. Classify scope and tags; assign confidence and importance.
4. Record conflicts explicitly; do not silently overwrite an existing fact.
5. Add relationships to relevant units or chains.
6. Create a canonical Memory Unit as Context Engineer, then rebuild and verify.

