#!/usr/bin/env python3
"""Derived retrieval layer builder for V1.1 (YZT-40 R7).

Deletes index/v1.1/memory.db and rebuilds it from zero from the V1.1 canonical
Git documents (Spec §24.1, §39: never copy V1 tables, never let the derived
layer own canonical state). The DB directory is git-ignored.
"""
from __future__ import annotations

import json
import shutil
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


def _yaml(path: Path):
    return parse_yaml(path.read_text(encoding="utf-8"))


def collect():
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
    status = "active"
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


def rebuild() -> dict:
    registry, anchors, rules, facts, cases, checkpoints, roles = collect()
    if DB.parent.exists():
        shutil.rmtree(DB.parent)
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB))
    try:
        conn.executescript(DDL)
        conn.executemany("INSERT INTO settings VALUES(?,?)", [
            ("schema_version", "1.1"),
            ("embedding_provider", "disabled"),
            ("embedding_status", "interface_reserved_model_not_selected")])
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
    finally:
        conn.close()
    return {"objects": len(anchors) + len(rules) + len(facts) + len(cases),
            "checkpoints": len(checkpoints), "role_profiles": len(roles),
            "database": str(DB.relative_to(ROOT)), "embedding_provider": "disabled"}


def main():
    print(json.dumps(rebuild(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
