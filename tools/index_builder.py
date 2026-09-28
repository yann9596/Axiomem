#!/usr/bin/env python3
"""Derived retrieval layer builder for V1.1 (YZT-40 R7).

Atomically rebuilds index/v1.1/memory.db from the V1.1 canonical
Git documents (Spec §24.1, §39: never copy V1 tables, never let the derived
layer own canonical state). The DB directory is git-ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yaml_mini import parse_yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "team-context"
PROJECTS = ROOT / "project-context"
DB = ROOT / "index" / "v1.1" / "memory.db"
INDEX_BUILD_VERSION = "20260928.1"

DDL = """
CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE memory_object(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, statement TEXT NOT NULL,
  status TEXT NOT NULL, verification TEXT NOT NULL, path TEXT NOT NULL, updated_at TEXT);
CREATE TABLE memory_scope(
  object_id TEXT PRIMARY KEY, scope_type TEXT NOT NULL, project_id TEXT,
  projects_json TEXT NOT NULL DEFAULT '[]', task_id TEXT);
CREATE TABLE memory_domain(object_id TEXT NOT NULL, domain TEXT NOT NULL, PRIMARY KEY(object_id, domain));
CREATE TABLE memory_repo(object_id TEXT NOT NULL, repo_id TEXT NOT NULL, PRIMARY KEY(object_id, repo_id));
CREATE TABLE memory_role_hint(object_id TEXT NOT NULL, role TEXT NOT NULL, PRIMARY KEY(object_id, role));
CREATE TABLE memory_source_ref(object_id TEXT NOT NULL, source_ref TEXT NOT NULL,
  ref_role TEXT NOT NULL DEFAULT 'source', PRIMARY KEY(object_id, source_ref, ref_role));
CREATE TABLE memory_relation(object_id TEXT NOT NULL, relation_type TEXT NOT NULL,
  target TEXT NOT NULL, note TEXT DEFAULT '', PRIMARY KEY(object_id, relation_type, target));
CREATE TABLE checkpoint_entry(
  checkpoint_id TEXT NOT NULL, section TEXT NOT NULL, entry_id TEXT NOT NULL,
  summary TEXT NOT NULL, refs_json TEXT NOT NULL, project_id TEXT, scope_type TEXT NOT NULL,
  PRIMARY KEY(checkpoint_id, section, entry_id));
CREATE TABLE project_registry_cache(
  project_id TEXT PRIMARY KEY, name TEXT NOT NULL, phase TEXT NOT NULL,
  multica_project_id TEXT, context_repo TEXT NOT NULL, subtree TEXT,
  active_roles_json TEXT NOT NULL, on_demand_roles_json TEXT NOT NULL);
CREATE VIRTUAL TABLE fts_content USING fts5(
  object_id UNINDEXED, title, statement, body, tokenize='unicode61');
"""


def _yaml(path: Path, data: bytes | None = None):
    # Paths are derived provenance, never trusted from a YAML field.
    doc = parse_yaml((data if data is not None else path.read_bytes()).decode("utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"canonical document must be a mapping: {path}")
    doc["_path"] = path.relative_to(ROOT).as_posix()
    return doc


def _source_paths() -> list[Path]:
    paths = {TEAM / "registry" / "projects.yaml", TEAM / "checkpoint.yaml"}
    for base, pattern in (
        (PROJECTS, "*/project.yaml"), (TEAM / "rules", "RULE-*.yaml"),
        (PROJECTS, "*/rules/RULE-*.yaml"), (PROJECTS, "*/facts/FACT-*.yaml"),
        (PROJECTS, "*/cases/CASE-*.yaml"), (PROJECTS, "*/checkpoint.yaml"),
        (TEAM / "roles", "*.yaml"),
    ):
        paths.update(base.glob(pattern))
    return sorted(paths)


def _snapshot() -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in _source_paths()}


def _revision(snapshot: dict[Path, bytes]) -> str:
    # Index-specific raw-byte revision, NOT the Public Handoff memory_revision.
    manifest = [[p.relative_to(ROOT).as_posix(), hashlib.sha256(data).hexdigest()]
                for p, data in sorted(snapshot.items())]
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def collect(*, loader=None):
    _yaml = loader or globals()["_yaml"]
    registry = _yaml(TEAM / "registry" / "projects.yaml")
    anchors = [_yaml(p) for p in sorted(PROJECTS.glob("*/project.yaml"))]
    rule_paths = sorted((TEAM / "rules").glob("RULE-*.yaml")) \
        + sorted(PROJECTS.glob("*/rules/RULE-*.yaml"))
    rules = [_yaml(p) for p in rule_paths]
    facts = [_yaml(p) for p in sorted(PROJECTS.glob("*/facts/FACT-*.yaml"))]
    cases = [_yaml(p) for p in sorted(PROJECTS.glob("*/cases/CASE-*.yaml"))]
    checkpoints = ([_yaml(TEAM / "checkpoint.yaml")]
                   + [_yaml(p) for p in sorted(PROJECTS.glob("*/checkpoint.yaml"))])
    roles = [_yaml(p) for p in sorted((TEAM / "roles").glob("*.yaml"))]
    return registry, anchors, rules, facts, cases, checkpoints, roles


def _insert(conn, doc, kind):
    scope = _scope_of(doc)
    oid = _oid(doc)
    title = doc.get("title", "") if kind != "anchor" else doc["project_id"]
    statement = doc.get("statement", "") if kind != "anchor" \
        else doc.get("governance", {}).get("mission", "")
    # Anchor lifecycle is the registry phase; do not invent object statuses.
    # Rule/Fact/Case lifecycle must faithfully project Canonical, not default active.
    status = "active" if kind == "anchor" else doc.get("status")
    if not isinstance(status, str) or not status.strip():
        raise ValueError(f"missing canonical lifecycle status: {oid}")
    verification = "verified" if kind == "anchor" else doc.get("verification", "unverified")
    conn.execute("INSERT INTO memory_object VALUES(?,?,?,?,?,?,?,?)", (
        oid, kind, title, statement, status, verification,
        doc.get("_path", ""), doc.get("updated_at", "")))
    conn.execute("INSERT INTO memory_scope VALUES(?,?,?,?,?)", (
        oid, scope["type"], scope.get("project_id"),
        json.dumps(scope.get("projects") or [], ensure_ascii=False),
        scope.get("task_id")))
    for dom in doc.get("domains") or []:
        conn.execute("INSERT OR IGNORE INTO memory_domain VALUES(?,?)", (oid, dom))
    for repo in doc.get("repositories") or []:
        rid = repo["repo_id"] if isinstance(repo, dict) else repo
        conn.execute("INSERT OR IGNORE INTO memory_repo VALUES(?,?)", (oid, rid))
    for role in doc.get("relevant_roles") or []:
        conn.execute("INSERT OR IGNORE INTO memory_role_hint VALUES(?,?)", (oid, role))
    for ref in doc.get("source_refs") or []:
        conn.execute("INSERT OR IGNORE INTO memory_source_ref VALUES(?,?,'source')", (oid, ref))
    for ref in doc.get("authority_refs") or []:
        conn.execute("INSERT OR IGNORE INTO memory_source_ref VALUES(?,?,'authority')", (oid, ref))
    rels = doc.get("relations") or {}
    for rtype in ("exception_of", "supersedes", "conflicts_with", "drift_of",
                  "norm_reality_conflict"):
        val = rels.get(rtype)
        if not val:
            continue
        for tgt in (val if isinstance(val, list) else [val]):
            conn.execute("INSERT OR IGNORE INTO memory_relation VALUES(?,?,?,'')",
                         (oid, rtype, tgt))
    if kind == "anchor":
        body = json.dumps({"governance": doc.get("governance", {}),
                           "context": doc.get("context", {})}, ensure_ascii=False)
    else:
        body = "\n".join(str(doc.get(k, "")) for k in (
            "context", "problem_or_trigger", "action_or_behavior", "outcome",
            "why_it_matters", "verification_note"))
    conn.execute("INSERT INTO fts_content VALUES(?,?,?,?)",
                 (oid, title, statement, body))


def _oid(doc):
    return doc["id"] if "id" in doc else f"anchor:{doc['project_id']}"


def _scope_of(doc):
    if "scope" in doc:
        return doc["scope"]
    return {"type": "project", "project_id": doc["project_id"],
            "projects": [], "task_id": None}


def _write_database(path: Path, snapshot: dict[Path, bytes]) -> dict:
    data = collect(loader=lambda p: _yaml(p, snapshot[p]))
    registry, anchors, rules, facts, cases, checkpoints, roles = data
    revision = _revision(snapshot)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(DDL)
        conn.executemany("INSERT INTO settings VALUES(?,?)", [
            ("schema_version", "1.1"),
            ("embedding_provider", "disabled"),
            ("embedding_status", "interface_reserved_model_not_selected"),
            ("index_build_version", INDEX_BUILD_VERSION),
            ("canonical_revision", revision),
            ("canonical_revision_algorithm", "relative-path-raw-sha256-v1"),
            ("built_at", datetime.now(timezone.utc).isoformat()),
            ("canonical_file_count", str(len(snapshot))),
        ])
        for p in registry["projects"]:
            conn.execute("INSERT INTO project_registry_cache VALUES(?,?,?,?,?,?,?,?)", (
                p["id"], p["name"], p["phase"], p.get("multica_project_id"),
                p["context_repo"]["repo"], p["context_repo"].get("subtree") or "",
                json.dumps(p.get("active_roles", []), ensure_ascii=False),
                json.dumps(p.get("on_demand_roles", []), ensure_ascii=False)))
        for doc in anchors:
            _insert(conn, doc, "anchor")
        for doc in rules:
            _insert(conn, doc, "rule")
        for doc in facts:
            _insert(conn, doc, "current_fact")
        for doc in cases:
            _insert(conn, doc, "case")
        for cp in checkpoints:
            cp_id = f"checkpoint:{cp['scope'].get('project_id') or 'team'}"
            scope = cp["scope"]
            for section in ("confirmed", "open", "conflicts"):
                for entry in cp.get(section, []) or []:
                    conn.execute(
                        "INSERT OR IGNORE INTO checkpoint_entry VALUES(?,?,?,?,?,?,?)",
                        (cp_id, section, entry["id"], entry["summary"],
                         json.dumps(entry.get("refs", []), ensure_ascii=False),
                         scope.get("project_id"), scope["type"]))
        conn.commit()
        if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("index integrity check failed")
    finally:
        conn.close()
    return {"objects": len(anchors) + len(rules) + len(facts) + len(cases),
            "checkpoints": len(checkpoints), "role_profiles": len(roles),
            "database": str(DB.relative_to(ROOT)), "embedding_provider": "disabled",
            "canonical_revision": revision, "index_build_version": INDEX_BUILD_VERSION}


def rebuild() -> dict:
    """Build beside the old DB, then replace only that file after validation.

    A canonical deployment must remain quiescent through this operation and its
    acceptance check. The content recheck detects drift; it is not a lock on
    other programs editing Canonical. A stale rebuild lock is never auto-deleted.
    """
    DB.parent.mkdir(parents=True, exist_ok=True)
    lock = DB.with_name(DB.name + ".rebuild.lock")
    # Exclusive-create serializes cooperating builders without platform packages.
    fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    temp = None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        if any(Path(str(DB) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("index has SQLite sidecars; quiesce writers before rebuild")
        snapshot = _snapshot()
        temp_fd, temp_name = tempfile.mkstemp(prefix=".memory-", suffix=".db", dir=DB.parent)
        os.close(temp_fd)
        temp = Path(temp_name)
        report = _write_database(temp, snapshot)
        with temp.open("r+b") as stream:
            os.fsync(stream.fileno())
        if _snapshot() != snapshot:
            raise RuntimeError("canonical changed during rebuild; old index retained")
        if any(Path(str(DB) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise RuntimeError("SQLite writer appeared during rebuild; old index retained")
        # On Windows an open handle can prevent replace; failure preserves old DB.
        os.replace(temp, DB)
        return report
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def check() -> dict:
    """Read-only freshness/integrity check; no implicit rebuild or DB creation.

    This verifies provenance and SQLite structural integrity, not the truth of
    the Canonical facts or every index row's semantic equivalence.
    """
    if not DB.is_file():
        return {"ok": False, "reason": "index_missing"}
    try:
        before = _snapshot()
        conn = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            settings = dict(conn.execute("SELECT key, value FROM settings"))
            integrity = conn.execute("PRAGMA quick_check").fetchone() == ("ok",)
        finally:
            conn.close()
        current = _revision(before)
        if _snapshot() != before:
            return {"ok": False, "reason": "canonical_changed_during_check"}
        if not integrity:
            return {"ok": False, "reason": "index_integrity_failed"}
        if settings.get("index_build_version") != INDEX_BUILD_VERSION:
            return {"ok": False, "reason": "index_builder_version_mismatch"}
        if (settings.get("schema_version") != "1.1" or
                settings.get("canonical_revision_algorithm") != "relative-path-raw-sha256-v1"):
            return {"ok": False, "reason": "index_metadata_incompatible"}
        if settings.get("canonical_revision") != current:
            return {"ok": False, "reason": "index_stale",
                    "canonical_revision": current,
                    "indexed_revision": settings.get("canonical_revision")}
        return {"ok": True, "reason": "index_current", "canonical_revision": current,
                "index_build_version": INDEX_BUILD_VERSION}
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"ok": False, "reason": "index_check_failed", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild or check the derived V1.1 index")
    parser.add_argument("command", nargs="?", choices=("rebuild", "check"), default="rebuild")
    args = parser.parse_args()
    if args.command == "check":
        report = check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    print(json.dumps(rebuild(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
