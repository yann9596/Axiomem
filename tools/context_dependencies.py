#!/usr/bin/env python3
"""Scoped dependency manifests for SHADOW evaluation, never a READY authority.

Includes the whole applicable candidate directory, not just selected IDs.
The frozen built_from algorithms and authoritative global checks are unchanged.
External Artifact/Findings freshness remains owned by the existing runtime.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cutil import ROOT
from yaml_mini import parse_yaml

VERSION = "context-dependency-shadow/1"
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]*$")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return "sha256:" + hashlib.sha256(_json(value)).hexdigest()


def _projects(scope: dict) -> set[str]:
    kind = scope.get("type")
    if kind == "team":
        return set()
    if kind == "project":
        result = [scope.get("project_id")]
    elif kind == "cross_project":
        result = scope.get("projects") or []
    else:
        raise ValueError("unresolved shadow scope")
    if not result or any(not isinstance(x, str) or not IDENTIFIER.fullmatch(x) for x in result):
        raise ValueError("invalid shadow project identity")
    return set(result)


def build_manifest(scope: dict, role: str, package: dict | None = None,
                   *, root: Path | None = None) -> dict:
    root = root or ROOT
    projects = _projects(scope)
    if not isinstance(role, str) or not IDENTIFIER.fullmatch(role):
        raise ValueError("invalid shadow role")
    registry_path = root / "team-context/registry/projects.yaml"
    registry = parse_yaml(registry_path.read_text(encoding="utf-8"))
    entries = registry.get("projects") or []
    if not projects.issubset({e.get("id") for e in entries}):
        raise ValueError("shadow project is not registered")
    selected_registry = dict(registry)
    # Global header authorizations stay conservative. Only unrelated project rows
    # are removed; team tasks keep the complete registry.
    selected_registry["projects"] = [e for e in entries
                                    if not projects or e.get("id") in projects]
    files = {root / "team-context/checkpoint.yaml",
             root / f"team-context/roles/{role}.yaml",
             root / "migration/authority-evidence.yaml"}
    files.update((root / "team-context/rules").glob("RULE-*.yaml"))
    files.update(p for p in (root / "team-context/policies").rglob("*") if p.is_file())
    # Policies and registry supply scope/role/phase semantics. All lifecycle
    # states are included conservatively so revocation cannot escape detection.
    from cutil import object_scope, scope_allows
    for pid in sorted(projects):
        base = root / "project-context" / pid
        files.update({base / "project.yaml", base / "checkpoint.yaml"})
    for pattern in ("*/rules/RULE-*.yaml", "*/facts/FACT-*.yaml", "*/cases/CASE-*.yaml"):
        for path in (root / "project-context").glob(pattern):
            doc = parse_yaml(path.read_text(encoding="utf-8"))
            declared = object_scope(doc)
            cross_ok = declared.get("type") != "cross_project" or \
                set(declared.get("projects") or []).issubset(projects)
            if scope_allows(doc, scope) and cross_ok:
                files.add(path)
    rows = []
    for path in sorted(files):
        raw = path.read_bytes()  # missing required files produce incomplete, not READY
        relative = path.relative_to(root).as_posix()
        value = parse_yaml(raw.decode("utf-8")) if path.suffix == ".yaml" else (
            json.loads(raw) if path.suffix == ".json" else raw.decode("utf-8"))
        rows.append({"path": relative, "digest": _digest(value)})
    rows.append({"path": "team-context/registry/projects.yaml#scoped", "digest": _digest(selected_registry)})
    if package is not None:
        dependencies = {k: package.get(k) for k in (
            "scope", "rules", "current_facts", "cases", "team_state_slice",
            "project_state_slice", "open_conflicts", "task_evidence", "source_refs")}
        rows.append({"path": "package:selected-dependencies", "digest": _digest(dependencies)})
    rows.sort(key=lambda row: row["path"])
    payload = {"scope": scope, "role": role, "entries": rows}
    return {"schema_version": VERSION, "shadow_only": True, "complete": True,
            "global_check_unchanged": True, **payload, "digest": _digest(payload)}


def observe(scope: dict, role: str, package: dict | None = None, **kwargs) -> dict:
    try:
        return build_manifest(scope, role, package, **kwargs)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"schema_version": VERSION, "shadow_only": True, "complete": False,
                "global_check_unchanged": True, "reason": str(exc)}


def compare(previous: dict | None, current: dict) -> dict:
    """Diagnostic only; an unchanged subset NEVER overrides global staleness."""
    if not previous or not previous.get("complete") or not current.get("complete") or \
            previous.get("schema_version") != VERSION or current.get("schema_version") != VERSION:
        return {"decision": "conservative_global_required", "shadow_only": True}
    changed = previous.get("digest") != current.get("digest")
    before = {r["path"]: r["digest"] for r in previous["entries"]}
    after = {r["path"]: r["digest"] for r in current["entries"]}
    return {"decision": "scoped_change" if changed else "scoped_unchanged",
            "shadow_only": True, "global_check_unchanged": True,
            "changed_paths": sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))}
