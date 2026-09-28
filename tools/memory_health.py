#!/usr/bin/env python3
"""Read-only lifecycle hygiene and source-change narrowing for Lead/02.

No automatic promotions, writes, deletions, task creation or scheduling.
Report is a disposition proposal, not proof of semantic correctness.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path

from cutil import ROOT
from context_quality import assess, byte_size, load_policy
from yaml_mini import parse_yaml

REPO_REF = re.compile(r"^repo://([^/@]+)(?:@([^/]+))?(?:/(.*))?$")


def canonical_paths(root: Path):
    for base in (root / "team-context", root / "project-context"):
        for path in sorted(base.rglob("*.yaml")):
            yield path


def source_changes(repo: str, changed_paths: list[str], *, root: Path | None = None) -> dict:
    """Narrow by verified_against source repository + path contracts.

    Missing path bindings stay conservative. This does not declare objects
    stale/refuted and does not run semantic revalidation.
    """
    if not repo.strip() or not changed_paths or any(not p or ".." in Path(p).parts for p in changed_paths):
        raise ValueError("repo and nonempty repository-relative changed paths required")
    root = root or ROOT
    affected = []
    for path in canonical_paths(root):
        doc = parse_yaml(path.read_text(encoding="utf-8"))
        if doc.get("kind") not in {"current_fact", "rule", "case", "project_anchor"}:
            continue
        bindings = list(doc.get("verified_against") or [])
        bound_refs = {b.get("source_ref") for b in bindings}
        for ref in list(doc.get("source_refs") or []) + list(doc.get("authority_refs") or []):
            if ref not in bound_refs:
                bindings.append({"source_ref": ref})
        hits = []
        for binding in bindings:
            match = REPO_REF.fullmatch(str(binding.get("source_ref") or ""))
            if not match or match.group(1) != repo:
                continue
            patterns = binding.get("paths") or ([match.group(3)] if match.group(3) else [])
            matches = [p for p in changed_paths if not patterns or any(
                p == pat or p.startswith(pat.rstrip("/") + "/") or fnmatch.fnmatchcase(p, pat)
                for pat in patterns)]
            if matches:
                hits.append({"source_ref": binding["source_ref"], "changed_paths": matches,
                             "reason": "bound_source_change" if patterns else "missing_path_binding_conservative"})
        if hits:
            affected.append({"id": doc.get("id") or doc.get("project_id"), "path": path.relative_to(root).as_posix(),
                             "status": doc.get("status"), "hits": hits,
                             "disposition": "bounded_semantic_revalidation_required", "owner": "context-engineer"})
    return {"schema_version": "source-change-proposal/1", "repo": repo, "changed_paths": changed_paths,
            "affected": affected, "canonical_writes": 0, "agent_triggers": 0,
            "limitation": "Unbound indirect/transitive dependencies and live authority withdrawal require Lead/02 evidence review; no effect is inferred from commit age."}


def health(*, root: Path | None = None, project: str | None = None) -> dict:
    root = root or ROOT
    policy = load_policy(root)
    objects, checkpoints = [], []
    for path in canonical_paths(root):
        doc = parse_yaml(path.read_text(encoding="utf-8"))
        scope = doc.get("scope") or {}
        if project and scope.get("type") != "team" and scope.get("project_id") != project:
            continue
        if doc.get("kind") in {"current_fact", "rule", "case"}:
            # History remains auditable but does not create current-size alarms.
            if doc.get("status") != "active":
                continue
            kind = doc["kind"]
            key = "fact_bytes" if kind == "current_fact" else "rule_bytes" if kind == "rule" else "candidate_source_bytes"
            size = byte_size(doc)
            objects.append({"id": doc["id"], "path": path.relative_to(root).as_posix(),
                            "kind": kind, "canonical_bytes": size,
                            "limit": policy["limits"]["candidate_source_bytes"],
                            "oversized": size > policy["limits"]["candidate_source_bytes"],
                            "statement_bytes": len(str(doc.get("statement") or "").encode("utf-8")),
                            "body_oversized": len(str(doc.get("statement") or "").encode("utf-8")) > policy["limits"][key]})
        elif doc.get("kind") == "checkpoint":
            entries = [{"id": e["id"], "summary": e["summary"], "refs": e["refs"]}
                       for s in ("confirmed", "open", "conflicts") for e in doc.get(s) or []]
            q = assess("plan", {"candidates": {"checkpoint_entries": entries}}, root=root)
            checkpoints.append({"path": path.relative_to(root).as_posix(),
                                "counts": {s: len(doc.get(s) or []) for s in ("confirmed", "open", "conflicts", "next")},
                                "quality": q})
    issues = [o for o in objects if o["oversized"] or o["body_oversized"]]
    return {"schema_version": "memory-health/1", "project": project, "objects": objects,
            "checkpoints": checkpoints, "oversized_objects": issues,
            "ok": not issues and all(c["quality"]["ok"] for c in checkpoints),
            "canonical_writes": 0, "agent_triggers": 0,
            "limitation": "Size/structure only; does not prove facts true, decisions approved, all Findings captured, or index/deployment fresh. Use check-index separately."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("audit"); h.add_argument("--project")
    c = sub.add_parser("source-change"); c.add_argument("--repo", required=True)
    c.add_argument("--path", action="append", required=True)
    args = parser.parse_args()
    try:
        result = health(project=args.project) if args.command == "audit" else source_changes(args.repo, args.path)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
