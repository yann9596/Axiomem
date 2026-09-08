#!/usr/bin/env python3
"""Shared utilities for the V1.1 runtime (YZT-40)."""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yaml_mini import parse_yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "team-context"
PROJECTS = ROOT / "project-context"
SCHEMAS = ROOT / "schemas"
RUNTIME = ROOT / "runtime" / "v1.1"
DB = ROOT / "index" / "v1.1" / "memory.db"

TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[\w-]+", re.UNICODE)
STOP_WORDS = {"the", "a", "an", "of", "for", "to", "and", "or", "in", "on", "is",
              "are", "be", "by", "with", "this", "that", "at", "as", "it", "its"}

CHAIN_SCOPE = {"app1-mvp": ("project", "app1"),
               "web-imagegen-pilot": ("project", "web-imagegen"),
               "team-governance": ("team", None)}


class ScopeError(ValueError):
    pass


def toks(text: str) -> set:
    return {t.lower() for t in TOKEN_RE.findall(text or "")} - STOP_WORDS


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def doc_at(path: Path):
    return parse_yaml(path.read_text(encoding="utf-8"))


def object_scope(doc: dict) -> dict:
    if "scope" in doc:
        return doc["scope"]
    return {"type": "project", "project_id": doc.get("project_id"),
            "projects": [], "task_id": None}


def scope_allows(doc: dict, task_scope: dict) -> bool:
    scope = object_scope(doc)
    t, o = task_scope["type"], scope["type"]
    opid = scope.get("project_id")
    if t == "team":
        return o == "team"
    if t == "cross_project":
        if o in ("team", "cross_project"):
            return True
        return o == "project" and opid in (task_scope.get("projects") or [])
    if o == "team":
        return True
    if o == "project":
        return opid == task_scope["project_id"]
    return False


def entry_relevant(entry: dict, task_tokens: set) -> bool:
    etoks = toks(entry.get("summary", "")) | \
        set(toks(" ".join(entry.get("refs") or [])))
    return bool(etoks & task_tokens)


def project_of(doc: dict) -> str | None:
    s = object_scope(doc)
    return s.get("project_id") if s["type"] == "project" else None
