#!/usr/bin/env python3
"""Gate A0 / Gate A — canonical validation for the V1.1 rebuild (YZT-40).

Validates every V1.1 canonical document against its JSON Schema, every Rule's
authority_refs against resolvable authority artifacts, and the legacy
accounting: every V1.0 object must appear in migration/migration-map.yaml
(no silent drops). Results written to migration/gate-results/gate-a.json.
Exit 0 = gate passes; 2 = STOP.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yaml_mini import parse_yaml  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402
from caccount import run_accounting  # noqa: E402
from cauthority import evaluate_all_rules  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "team-context"
PROJECTS = ROOT / "project-context"
VERIFICATIONS = {"verified", "partially_verified", "unverified", "conflicted", "refuted"}
FORBIDDEN_STATUSES = {"candidate", "stale", "archived", "waiting_human", "blocked",
                      "conflicted", "blocked_conflicted_waiting_human"}

# Gate A project-drift alarm. Project ids/phases are DATA (the Registry is the
# only phase authority, F-6); this constant only makes an unexpected project
# appearing or disappearing a hard Gate A error instead of a silent drift.
# `app1` is the archived (YZT-98) predecessor of `teachers-app1`: it must stay
# registered and inspectable, never be silently dropped.
EXPECTED_PROJECT_IDS = ["app1", "teachers-app1", "web-imagegen"]
EXPECTED_ARCHIVED_PROJECT_IDS = ["app1"]
# F-6 lifecycle for a project that is still a live target of work.
LIVE_PHASES = {"incubation", "active_development"}


def read_yaml(path: Path):
    return parse_yaml(path.read_text(encoding="utf-8"))


def schema_check(doc, schema_name: str, path_hint: str) -> list[str]:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(doc, path=path_hint)


def main() -> int:
    errors: list[str] = []

    registry = read_yaml(TEAM / "registry" / "projects.yaml")
    registered_ids: list[str] = []
    if registry.get("kind") != "project_registry":
        errors.append("registry: kind must be project_registry")
    else:
        errors += schema_check(registry, "project-registry.schema.json", "registry")
        registered_ids = sorted(p["id"] for p in registry["projects"])
        if registered_ids != EXPECTED_PROJECT_IDS:
            errors.append(f"registry: unexpected project ids {registered_ids}")
        archived_ids = sorted(p["id"] for p in registry["projects"]
                              if p["phase"] == "archived")
        if archived_ids != EXPECTED_ARCHIVED_PROJECT_IDS:
            errors.append(f"registry: unexpected archived project ids {archived_ids}")
        for p in registry["projects"]:
            if p["phase"] == "archived":
                continue
            if p["phase"] not in LIVE_PHASES:
                errors.append(f"registry/{p['id']}: phase {p['phase']} violates F-6")

    rules, facts, cases = [], [], []
    rules += [read_yaml(p) for p in sorted((TEAM / "rules").glob("RULE-*.yaml"))]
    # Anchors are derived from the filesystem and must match the Registry
    # exactly: an anchor without a registration (or the reverse) is drift, not
    # a new project. Archived projects keep their anchor for traceability.
    anchor_dirs = sorted(p.name for p in PROJECTS.iterdir()
                         if p.is_dir() and (p / "project.yaml").is_file())
    if registered_ids and anchor_dirs != registered_ids:
        errors.append("project-context anchors "
                      f"{anchor_dirs} do not match registered projects "
                      f"{registered_ids}")
    for pid in anchor_dirs:
        a = read_yaml(PROJECTS / pid / "project.yaml")
        if a.get("kind") != "project_anchor":
            errors.append(f"anchor/{pid}: kind must be project_anchor")
            continue
        errors += schema_check(a, "project-anchor.schema.json", f"anchor:{pid}")
        if a["project_id"] != pid:
            errors.append(f"anchor/{pid}: project_id mismatch")
        if not a.get("authority_refs"):
            errors.append(f"anchor/{pid}: anchor without authority_refs")
        base = PROJECTS / pid
        for p in sorted((base / "rules").glob("RULE-*.yaml")):
            rules.append(read_yaml(p))
        for p in sorted((base / "facts").glob("FACT-*.yaml")):
            facts.append(read_yaml(p))
        for p in sorted((base / "cases").glob("CASE-*.yaml")):
            cases.append(read_yaml(p))

    checkpoints = [read_yaml(TEAM / "checkpoint.yaml")] + [
        read_yaml(p) for p in sorted(PROJECTS.glob("*/checkpoint.yaml"))]
    roles = [read_yaml(p) for p in sorted((TEAM / "roles").glob("*.yaml"))]
    findings = [json.loads(p.read_text(encoding="utf-8"))
                for p in sorted((ROOT / "migration" / "findings").glob("FIND-*.json"))]
    rt = ROOT / "runtime" / "v1.1" / "findings"
    runtime_findings = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(rt.glob("FIND-*.json"))] \
        if rt.exists() else []

    for r in rules:
        errors += schema_check(r, "rule.schema.json", f"rule:{r['id']}")
        if r["scope"]["type"] not in ("project", "team"):
            errors.append(f"rule:{r['id']}: illegal rule scope type")
        elif r["scope"]["type"] == "project" and r["scope"].get("project_id") not in registered_ids:
            errors.append(f"rule:{r['id']}: unregistered project_id")
        if r.get("status") in FORBIDDEN_STATUSES:
            errors.append(f"rule:{r['id']}: forbidden status {r.get('status')}")
        if r.get("verification") not in VERIFICATIONS:
            errors.append(f"rule:{r['id']}: illegal verification")
        for key in ("applicable_when", "not_applicable_when"):
            pass

    for fdoc in facts:
        errors += schema_check(fdoc, "fact.schema.json", f"fact:{fdoc.get('id')}")
        if fdoc["scope"]["type"] != "project":
            errors.append(f"fact:{fdoc['id']}: scope must be project")
        elif fdoc["scope"].get("project_id") not in registered_ids:
            errors.append(f"fact:{fdoc['id']}: unregistered project_id")

    for c in cases:
        errors += schema_check(c, "case.schema.json", f"case:{c['id']}")
        for key in ("applicable_when", "not_applicable_when"):
            if not c.get(key):
                errors.append(f"case:{c['id']}: {key} missing (scenario boundary required)")

    for cp in checkpoints:
        errors += schema_check(cp, "checkpoint.schema.json", f"checkpoint:{cp.get('_path')}")
        if cp.get("status"):
            errors.append(f"checkpoint:{cp.get('_path')}: lifecycle status present")
        for sect in ("confirmed", "open", "conflicts"):
            for entry in cp.get(sect, []):
                if not entry.get("refs"):
                    errors.append(f"checkpoint:{cp.get('_path')}:{entry.get('id')}: missing refs")

    for rp in roles:
        errors += schema_check(rp, "role-profile.schema.json", f"role:{rp['role']}")
        extra = set(rp) - {"schema_version", "kind", "role", "responsibilities",
                           "authority_boundary", "retrieval_policy", "_path"}
        if extra:
            errors.append(f"role:{rp['role']}: non-policy fields present (D-03)")

    for fdoc in findings + runtime_findings:
        errors += schema_check(fdoc, "finding.schema.json", f"finding:{fdoc['finding_id']}")
        if fdoc["status"] == "processed" and not fdoc.get("disposition"):
            errors.append(f"finding:{fdoc['finding_id']}: processed without disposition")

    counts = {"anchors": len(anchor_dirs), "rules": len(rules), "facts": len(facts),
              "cases": len(cases), "checkpoints": len(checkpoints),
              "role_profiles": len(roles), "migration_findings": len(findings) + len(runtime_findings)}
    all_objects_schema_valid = not errors
    unresolved_scope = sum(1 for e in errors if "scope" in e.lower())

    authority = evaluate_all_rules(rules)
    errors += authority["errors"]
    accounting = run_accounting()
    errors += accounting.get("errors") or []
    if accounting.get("silent_drop"):
        errors.append(
            "legacy silent_drop="
            f"{accounting['silent_drop']}: {accounting.get('silent_drop_ids')}")
    if not accounting.get("complete"):
        errors.append(
            "legacy accounting incomplete: "
            f"accounted={accounting.get('legacy_objects_accounted_for')}% "
            f"inventory={accounting.get('inventory_count')} "
            f"mapped={accounting.get('mapped_count')}")
    rule_without_valid_authority = authority["invalid_count"]
    accounted = accounting["legacy_objects_accounted_for"]
    silent_drop = accounting["silent_drop"]
    missing = sorted(set((accounting.get("missing_evidence") or []) +
                         (authority.get("missing_evidence") or [])))
    valid = (
        all_objects_schema_valid
        and accounting.get("complete") is True
        and silent_drop == 0
        and unresolved_scope == 0
        and rule_without_valid_authority == 0
        and not missing
        and not accounting.get("fail_closed")
        and not authority.get("fail_closed")
    )
    report = {
        "gate": "A0+A",
        "valid": valid,
        "all_objects_schema_valid": all_objects_schema_valid,
        "legacy_objects_accounted_for": accounted,
        "silent_drop": silent_drop,
        "unresolved_scope": unresolved_scope,
        "rule_without_valid_authority": rule_without_valid_authority,
        "errors": errors,
        "counts": counts,
        "accounting": {
            "inventory_source": accounting.get("inventory_source"),
            "inventory_count": accounting.get("inventory_count"),
            "mapped_count": accounting.get("mapped_count"),
            "silent_drop_ids": accounting.get("silent_drop_ids"),
            "map_only_ids": accounting.get("map_only_ids"),
            "complete": accounting.get("complete"),
        },
        "authority": {
            "rule_count": authority.get("rule_count"),
            "invalid_count": authority.get("invalid_count"),
            "fail_closed": authority.get("fail_closed"),
        },
        "missing_evidence": missing,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out = ROOT / "migration" / "gate-results" / "gate-a.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
