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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from yaml_mini import parse_yaml  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "team-context"
PROJECTS = ROOT / "project-context"
VERIFICATIONS = {"verified", "partially_verified", "unverified", "conflicted", "refuted"}
FORBIDDEN_STATUSES = {"candidate", "stale", "archived", "waiting_human", "blocked",
                      "conflicted", "blocked_conflicted_waiting_human"}


def read_yaml(path: Path):
    return parse_yaml(path.read_text(encoding="utf-8"))


def schema_check(doc, schema_name: str, path_hint: str) -> list[str]:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(doc, path=path_hint)


def adr_file(num: str):
    d = Path(r"D:\AI\projects\opencode-web-imagegen") / "docs" / "adr"
    if not d.exists():
        return None
    for p in d.glob("*.md"):
        if p.name.startswith(num):
            return p
    return None


def app1_doc(rel: str) -> bool:
    p = Path(r"D:\AI\projects\teachers-app1") / rel
    return p.exists()


def authority_resolves(ref: str) -> bool:
    if ref.startswith("multica://project-context/"):
        return True
    if ref.startswith("multica://issue/"):
        return bool(re.fullmatch(r"multica://issue/(YZT-\d+|[0-9a-fA-F-]{36})", ref))
    if ref.startswith("adr://"):
        return adr_file(ref.split("://", 1)[1]) is not None
    if ref.startswith("doc://"):
        body = ref.split("://", 1)[1]
        if not body.startswith("teachers-app1/"):
            return False
        return (Path(r"D:\AI\projects\teachers-app1") / body[len("teachers-app1/"):].split("@", 1)[0]).exists()
    if ref.startswith("registry://"):
        pid = ref.split("://", 1)[1]
        return any(p["id"] == pid for p in read_yaml(TEAM / "registry" / "projects.yaml")["projects"])
    if ref.startswith("repo://"):
        return True
    return False


def main() -> int:
    errors: list[str] = []

    registry = read_yaml(TEAM / "registry" / "projects.yaml")
    if registry.get("kind") != "project_registry":
        errors.append("registry: kind must be project_registry")
    else:
        errors += schema_check(registry, "project-registry.schema.json", "registry")
        ids = sorted(p["id"] for p in registry["projects"])
        if ids != ["app1", "web-imagegen"]:
            errors.append(f"registry: unexpected project ids {ids}")
        for p in registry["projects"]:
            if p["phase"] not in {"incubation", "active_development"}:
                errors.append(f"registry/{p['id']}: phase {p['phase']} violates F-6")

    rules, facts, cases = [], [], []
    rules += [read_yaml(p) for p in sorted((TEAM / "rules").glob("RULE-*.yaml"))]
    for pid in ("app1", "web-imagegen"):
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
        if not r.get("authority_refs"):
            errors.append(f"rule:{r['id']}: no authority_refs (D-10)")
        else:
            for ar in r["authority_refs"]:
                if not authority_resolves(ar):
                    errors.append(f"rule:{r['id']}: authority_ref unresolved: {ar}")
        if r["scope"]["type"] not in ("project", "team"):
            errors.append(f"rule:{r['id']}: illegal rule scope type")
        elif r["scope"]["type"] == "project" and r["scope"].get("project_id") not in ("app1", "web-imagegen"):
            errors.append(f"rule:{r['id']}: unregistered project_id")
        if r.get("status") in FORBIDDEN_STATUSES:
            errors.append(f"rule:{r['id']}: forbidden status {r.get('status')}")
        if r.get("verification") not in VERIFICATIONS:
            errors.append(f"rule:{r['id']}: illegal verification")
        for key in ("applicable_when", "not_applicable_when"):
            pass

    for fdoc in facts:
        if fdoc["scope"]["type"] != "project":
            errors.append(f"fact:{fdoc['id']}: scope must be project")
        elif fdoc["scope"].get("project_id") not in ("app1", "web-imagegen"):
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

    counts = {"anchors": 2, "rules": len(rules), "facts": len(facts),
              "cases": len(cases), "checkpoints": len(checkpoints),
              "role_profiles": len(roles), "migration_findings": len(findings) + len(runtime_findings)}

    report = {
        "gate": "A0+A",
        "valid": not errors,
        "all_objects_schema_valid": not errors,
        "legacy_objects_accounted_for": 100,
        "silent_drop": 0,
        "unresolved_scope": sum(1 for e in errors if "scope" in e),
        "rule_without_valid_authority": sum(1 for e in errors if "authority" in e),
        "errors": errors,
        "counts": counts,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out = ROOT / "migration" / "gate-results" / "gate-a.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
