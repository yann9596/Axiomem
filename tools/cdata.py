#!/usr/bin/env python3
"""Canonical data loaders for the V1.1 runtime (YZT-40)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import TEAM, PROJECTS, doc_at  # noqa: E402


def load_all_docs() -> list:
    docs = []
    for p in sorted(PROJECTS.glob("*/project.yaml")):
        d = doc_at(p)
        d["_kind"] = "anchor"
        docs.append(d)
    for p in sorted(TEAM.glob("rules/RULE-*.yaml")) + \
            sorted(PROJECTS.glob("*/rules/RULE-*.yaml")):
        d = doc_at(p)
        d["_kind"] = "rule"
        docs.append(d)
    for p in sorted(PROJECTS.glob("*/facts/FACT-*.yaml")):
        d = doc_at(p)
        d["_kind"] = "current_fact"
        docs.append(d)
    for p in sorted(PROJECTS.glob("*/cases/CASE-*.yaml")):
        d = doc_at(p)
        d["_kind"] = "case"
        docs.append(d)
    return docs


def checkpoint_docs() -> list:
    return [doc_at(TEAM / "checkpoint.yaml")] + \
        [doc_at(p) for p in sorted(PROJECTS.glob("*/checkpoint.yaml"))]


def load_role_profile(role: str) -> dict:
    p = TEAM / "roles" / f"{role}.yaml"
    if not p.exists():
        raise ValueError(f"role profile missing: {role}")
    return doc_at(p)
