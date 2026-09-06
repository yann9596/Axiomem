#!/usr/bin/env python3
"""Dependency-free Team Memory V1 CLI."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "index" / "memory.db"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
KINDS = {"memory_unit", "memory_chain", "memory_candidate", "external_signal", "context_package"}
SCOPES = {"project", "team", "task", "role", "decision"}


class ValidationError(ValueError):
    pass


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: root must be an object")
    return value


def require(d: dict, names: list[str], path: Path) -> None:
    missing = [name for name in names if name not in d]
    if missing:
        raise ValidationError(f"{path}: missing fields: {', '.join(missing)}")


def iso(value: object, field: str, path: Path) -> None:
    if not isinstance(value, str):
        raise ValidationError(f"{path}: {field} must be an ISO date-time string")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{path}: invalid {field}: {value}") from exc


def validate(d: dict, path: Path) -> None:
    require(d, ["schema_version", "kind", "id"], path)
    if d["schema_version"] != "1.0" or d["kind"] not in KINDS or not ID_RE.fullmatch(str(d["id"])):
        raise ValidationError(f"{path}: invalid schema_version, kind, or id")
    kind = d["kind"]
    if kind == "memory_unit":
        req = ["title", "summary", "content", "status", "scope", "importance", "confidence", "tags", "relationships", "sources", "created_at", "updated_at", "verified_at", "verified_by"]
        require(d, req, path)
        if d["status"] not in {"canonical", "superseded", "deprecated"} or d["scope"] not in SCOPES:
            raise ValidationError(f"{path}: invalid status or scope")
        if not isinstance(d["importance"], int) or not 1 <= d["importance"] <= 5 or not isinstance(d["confidence"], (int, float)) or not 0 <= d["confidence"] <= 1:
            raise ValidationError(f"{path}: importance must be 1..5 and confidence 0..1")
        if d["verified_by"] != "Context Engineer" or not d["sources"]:
            raise ValidationError(f"{path}: canonical units need sources and Context Engineer verification")
        for field in ["created_at", "updated_at", "verified_at"]:
            iso(d[field], field, path)
        for rel in d["relationships"]:
            if not isinstance(rel, dict) or rel.get("type") not in {"supports", "conflicts_with", "supersedes", "depends_on", "related_to"} or not rel.get("target_id"):
                raise ValidationError(f"{path}: invalid relationship")
    elif kind == "memory_chain":
        require(d, ["title", "description", "scope", "memory_ids", "tags", "created_at", "updated_at"], path)
        if d["scope"] not in SCOPES or not d["memory_ids"]:
            raise ValidationError(f"{path}: chain needs valid scope and memory_ids")
    elif kind == "memory_candidate":
        require(d, ["proposed_title", "proposed_content", "dedupe_key", "classification", "importance", "confidence", "source_ids", "status", "captured_at"], path)
        if d["status"] not in {"candidate", "rejected", "promoted"} or d["classification"] not in SCOPES:
            raise ValidationError(f"{path}: invalid candidate status or classification")
        iso(d["captured_at"], "captured_at", path)
    elif kind == "external_signal":
        require(d, ["source_type", "locator", "captured_at", "observed_at", "summary", "reliability", "content_hash"], path)
        if d["reliability"] not in {"authoritative", "corroborated", "unverified", "disputed"}:
            raise ValidationError(f"{path}: invalid reliability")
        iso(d["captured_at"], "captured_at", path)
        iso(d["observed_at"], "observed_at", path)
    elif kind == "context_package":
        require(d, ["task_id", "role", "decision", "known_facts", "relevant_memory", "confidence", "conflicts", "missing_context", "memory_candidates", "generated_at"], path)


def json_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.json") if p.is_file())


def cmd_ingest(args: argparse.Namespace) -> int:
    src = Path(args.input).resolve()
    d = load_json(src)
    validate(d, src)
    kind = d["kind"]
    if kind == "memory_unit":
        if args.actor_role != "Context Engineer":
            raise ValidationError("canonical memory writes require --actor-role \"Context Engineer\"")
        target = ROOT / "memory" / f"{d['id']}.json"
    elif kind == "memory_chain":
        if args.actor_role != "Context Engineer":
            raise ValidationError("memory chain writes require --actor-role \"Context Engineer\"")
        target = ROOT / "chains" / f"{d['id']}.json"
    elif kind == "memory_candidate":
        target = ROOT / "sources" / "candidates" / f"{d['id']}.json"
    elif kind == "external_signal":
        target = ROOT / "sources" / f"{d['id']}.json"
    else:
        raise ValidationError("context packages are task outputs, not canonical ingest records")
    print(json.dumps({"valid": True, "kind": kind, "target": str(target.relative_to(ROOT)), "canonicalized": kind == "memory_unit"}, ensure_ascii=False))
    if args.dry_run:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not args.replace:
        raise ValidationError(f"{target}: already exists; use --replace after review")
    shutil.copyfile(src, target)
    if kind in {"memory_unit", "memory_chain"}:
        rebuild()
    return 0


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE memory_units(id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL, scope TEXT NOT NULL, importance INTEGER NOT NULL, confidence REAL NOT NULL, tags_json TEXT NOT NULL, updated_at TEXT NOT NULL, path TEXT NOT NULL);
    CREATE VIRTUAL TABLE memory_fts USING fts5(id UNINDEXED, title, summary, content, tags, tokenize='unicode61');
    CREATE TABLE relationships(source_id TEXT NOT NULL, relation_type TEXT NOT NULL, target_id TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', PRIMARY KEY(source_id, relation_type, target_id));
    CREATE TABLE source_refs(memory_id TEXT NOT NULL, source_id TEXT NOT NULL, PRIMARY KEY(memory_id, source_id));
    CREATE TABLE chains(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, scope TEXT NOT NULL, tags_json TEXT NOT NULL, path TEXT NOT NULL);
    CREATE TABLE chain_members(chain_id TEXT NOT NULL, memory_id TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(chain_id, ordinal));
    """)
    conn.executemany("INSERT INTO settings(key,value) VALUES(?,?)", [("schema_version", "1"), ("embedding_provider", "disabled"), ("embedding_status", "interface_reserved_model_not_selected")])


def rebuild() -> dict:
    memory_docs = [(p, load_json(p)) for p in json_files(ROOT / "memory")]
    chain_docs = [(p, load_json(p)) for p in json_files(ROOT / "chains")]
    for path, doc in memory_docs + chain_docs:
        validate(doc, path)
    ids = {d["id"] for _, d in memory_docs}
    if len(ids) != len(memory_docs):
        raise ValidationError("duplicate Memory Unit id")
    for path, chain in chain_docs:
        missing = [mid for mid in chain["memory_ids"] if mid not in ids]
        if missing:
            raise ValidationError(f"{path}: missing chain members: {', '.join(missing)}")
    DB.parent.mkdir(parents=True, exist_ok=True)
    temp = DB.with_suffix(".db.new")
    if temp.exists():
        temp.unlink()
    conn = sqlite3.connect(temp)
    try:
        create_schema(conn)
        for path, d in memory_docs:
            tags = json.dumps(d["tags"], ensure_ascii=False)
            conn.execute("INSERT INTO memory_units VALUES(?,?,?,?,?,?,?,?,?,?,?)", (d["id"], d["title"], d["summary"], d["content"], d["status"], d["scope"], d["importance"], d["confidence"], tags, d["updated_at"], str(path.relative_to(ROOT))))
            conn.execute("INSERT INTO memory_fts VALUES(?,?,?,?,?)", (d["id"], d["title"], d["summary"], d["content"], " ".join(d["tags"])))
            for rel in d["relationships"]:
                conn.execute("INSERT INTO relationships VALUES(?,?,?,?)", (d["id"], rel["type"], rel["target_id"], rel.get("note", "")))
            for source in d["sources"]:
                conn.execute("INSERT INTO source_refs VALUES(?,?)", (d["id"], source))
        for path, d in chain_docs:
            conn.execute("INSERT INTO chains VALUES(?,?,?,?,?,?)", (d["id"], d["title"], d["description"], d["scope"], json.dumps(d["tags"], ensure_ascii=False), str(path.relative_to(ROOT))))
            conn.executemany("INSERT INTO chain_members VALUES(?,?,?)", [(d["id"], mid, i) for i, mid in enumerate(d["memory_ids"])])
        conn.commit()
    finally:
        conn.close()
    temp.replace(DB)
    return {"memory_units": len(memory_docs), "chains": len(chain_docs), "database": str(DB.relative_to(ROOT)), "embedding_provider": "disabled"}


def cmd_rebuild(_: argparse.Namespace) -> int:
    print(json.dumps(rebuild(), ensure_ascii=False))
    return 0


def check_all() -> dict:
    files = json_files(ROOT / "memory") + json_files(ROOT / "chains") + [p for p in json_files(ROOT / "sources") if "candidates" not in p.parts] + json_files(ROOT / "sources" / "candidates")
    docs = []
    for path in files:
        doc = load_json(path)
        validate(doc, path)
        docs.append((path, doc))
    unit_ids = {d["id"] for _, d in docs if d["kind"] == "memory_unit"}
    source_ids = {d["id"] for _, d in docs if d["kind"] in {"external_signal", "memory_candidate"}}
    errors = []
    for path, d in docs:
        if d["kind"] == "memory_unit":
            errors += [f"{path}: missing source {sid}" for sid in d["sources"] if sid not in source_ids]
            errors += [f"{path}: missing relationship target {r['target_id']}" for r in d["relationships"] if r["target_id"] not in unit_ids]
        elif d["kind"] == "memory_chain":
            errors += [f"{path}: missing chain member {mid}" for mid in d["memory_ids"] if mid not in unit_ids]
    if not DB.exists():
        errors.append("index/memory.db is missing")
    else:
        conn = sqlite3.connect(DB)
        try:
            indexed = {r[0] for r in conn.execute("SELECT id FROM memory_units")}
            setting = conn.execute("SELECT value FROM settings WHERE key='embedding_provider'").fetchone()
            if indexed != unit_ids:
                errors.append("index differs from canonical memory; run rebuild-index")
            if not setting or setting[0] != "disabled":
                errors.append("embedding status must remain explicitly disabled")
        finally:
            conn.close()
    if errors:
        raise ValidationError("verification failed:\n- " + "\n- ".join(errors))
    return {"valid": True, "documents": len(docs), "memory_units": len(unit_ids), "sources": len(source_ids), "embedding_provider": "disabled"}


def cmd_verify(_: argparse.Namespace) -> int:
    print(json.dumps(check_all(), ensure_ascii=False))
    return 0


def safe_fts_query(query: str) -> str:
    tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
    return " OR ".join(f'"{token}"' for token in tokens)


def cmd_retrieve(args: argparse.Namespace) -> int:
    if args.embedding_query:
        raise ValidationError("embedding retrieval is disabled: interface reserved, model not selected")
    if not DB.exists():
        raise ValidationError("index/memory.db missing; run rebuild-index")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        where = ["m.importance >= ?", "m.confidence >= ?"]
        params: list[object] = [args.min_importance, args.min_confidence]
        if args.tag:
            where.append("EXISTS (SELECT 1 FROM json_each(m.tags_json) WHERE value = ?)")
            params.append(args.tag)
        if args.scope:
            where.append("m.scope = ?")
            params.append(args.scope)
        fts = safe_fts_query(args.query)
        if fts:
            sql = "SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts JOIN memory_units m ON m.id=memory_fts.id WHERE memory_fts MATCH ? AND " + " AND ".join(where) + " ORDER BY rank, m.importance DESC LIMIT ?"
            rows = conn.execute(sql, [fts, *params, args.limit]).fetchall()
        else:
            rows = conn.execute("SELECT m.*, 0 AS rank FROM memory_units m WHERE " + " AND ".join(where) + " ORDER BY importance DESC LIMIT ?", [*params, args.limit]).fetchall()
        results = []
        for row in rows:
            item = {key: row[key] for key in ["id", "title", "summary", "status", "scope", "importance", "confidence", "updated_at", "path"]}
            item["tags"] = json.loads(row["tags_json"])
            item["sources"] = [r[0] for r in conn.execute("SELECT source_id FROM source_refs WHERE memory_id=?", (row["id"],))]
            if args.relations:
                item["relationships"] = [dict(r) for r in conn.execute("SELECT source_id, relation_type, target_id, note FROM relationships WHERE source_id=? OR target_id=?", (row["id"], row["id"]))]
                item["chains"] = [dict(r) for r in conn.execute("SELECT c.id, c.title, cm.ordinal FROM chain_members cm JOIN chains c ON c.id=cm.chain_id WHERE cm.memory_id=? ORDER BY cm.ordinal", (row["id"],))]
            results.append(item)
        print(json.dumps({"query": args.query, "count": len(results), "retrieval": "sqlite_fts5_metadata", "embedding_provider": "disabled", "results": results}, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Multica Team Memory V1")
    sub = p.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="validate and route a record")
    ingest.add_argument("input")
    ingest.add_argument("--actor-role", default="")
    ingest.add_argument("--replace", action="store_true")
    ingest.add_argument("--dry-run", action="store_true")
    ingest.set_defaults(func=cmd_ingest)
    rebuild_p = sub.add_parser("rebuild-index", help="rebuild SQLite from canonical JSON")
    rebuild_p.set_defaults(func=cmd_rebuild)
    verify_p = sub.add_parser("verify", help="validate records and index parity")
    verify_p.set_defaults(func=cmd_verify)
    retrieve = sub.add_parser("retrieve", help="query FTS, metadata and relationships")
    retrieve.add_argument("query")
    retrieve.add_argument("--tag")
    retrieve.add_argument("--scope", choices=sorted(SCOPES))
    retrieve.add_argument("--min-importance", type=int, default=1)
    retrieve.add_argument("--min-confidence", type=float, default=0.0)
    retrieve.add_argument("--limit", type=int, default=10)
    retrieve.add_argument("--relations", action="store_true")
    retrieve.add_argument("--embedding-query", action="store_true")
    retrieve.set_defaults(func=cmd_retrieve)
    return p


def main() -> int:
    try:
        args = parser().parse_args()
        return args.func(args)
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

