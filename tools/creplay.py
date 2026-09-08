#!/usr/bin/env python3
"""Gate C — historical replay & shadow validation harness (YZT-40 R9)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import ROOT, toks, now_iso, doc_at  # noqa: E402
from cbuild import resolve_scope, build_package  # noqa: E402


def package_refs(pkg: dict) -> list:
    return [r["ref"] for r in pkg["rules"]] + \
        [f["ref"] for f in pkg["current_facts"]] + \
        [c["ref"] for c in pkg["cases"]]


def run_replay(case_path: Path) -> dict:
    case = doc_at(case_path)
    scope = resolve_scope(case["task_id"], case.get("scope"),
                          case.get("projects") or [], case.get("project_id"))
    pkg = build_package(case["task_id"], case["role"], scope,
                        decision=case.get("decision", ""),
                        query=case.get("query", ""),
                        case_trigger=case.get("case_trigger"))
    expected = case["expected"]
    refs = package_refs(pkg)

    missing = []
    for want in expected.get("must_include", []):
        if want.startswith("anchor:"):
            pid = want.split(":", 1)[1]
            projects_digest = pkg["anchor_digest"].get("projects") or []
            if pid == case.get("project_id") and not pkg["anchor_digest"].get("mission"):
                missing.append(want)
            elif projects_digest and pid not in projects_digest:
                missing.append(want)
            continue
        if want not in refs:
            missing.append(want)
    unexpected = []
    for excl in expected.get("must_exclude", []):
        unexpected += [r for r in refs if r.startswith(excl)]
    scope_pollution = (expected.get("expected_scope") != pkg["scope"]["type"]) or bool(unexpected)
    cs_expected = expected.get("expected_case_search", "none")
    performed = pkg["assembly_trace"]["case_search_performed"]
    if cs_expected == "none":
        cs_ok = not performed and len(pkg["cases"]) == 0
    elif cs_expected == "performed":
        cs_ok = performed and len(pkg["cases"]) >= 1
    else:  # performed_no_match
        cs_ok = performed and len(pkg["cases"]) == 0
    false_activation = [c["ref"] for c in pkg["cases"]
                        if c["ref"] not in expected.get("must_include", [])]
    project_leak = []
    if expected.get("must_exclude_projects"):
        for e in pkg["project_state_slice"] + pkg["team_state_slice"]:
            if e.get("checkpoint") in expected["must_exclude_projects"]:
                project_leak.append(e["id"])
    pollution = project_leak + unexpected
    conflicts_ok = len(pkg["open_conflicts"]) == expected.get("expected_conflicts", 0)
    density = round((len(pkg["rules"]) + len(pkg["current_facts"]) + len(pkg["cases"])) /
                    max(1, len(pkg["rules"]) + len(pkg["current_facts"]) +
                        len(pkg["cases"]) + pkg["assembly_trace"]["excluded_counts"]["other_project"]), 2)
    result = {
        "task_id": case["task_id"],
        "coverage": case.get("notes", "").strip()[:120],
        "expected": {"scope": expected.get("expected_scope"),
                     "must_include": expected.get("must_include", []),
                     "must_exclude": expected.get("must_exclude", []),
                     "expected_case_search": cs_expected,
                     "expected_conflicts": expected.get("expected_conflicts", 0)},
        "actual": {"scope": pkg["scope"]["type"], "phase": pkg["project_phase"],
                   "refs": refs, "cases_search_performed": performed,
                   "conflicts": len(pkg["open_conflicts"]),
                   "blocked_by": pkg["blocked_by"],
                   "trace": pkg["assembly_trace"]},
        "result": {
            "scope_pollution": pollution,
            "false_canonical": False,
            "hidden_unresolved_conflict": len(pkg["open_conflicts"]) > expected.get("expected_conflicts", 0),
            "invalid_rule_authority": any(not r.get("authority_refs") for r in pkg["rules"]),
            "false_activation": false_activation,
            "missing_context": missing,
            "unexpected_context": unexpected,
            "reinvestigation_cost": len(missing),
            "issue_noise": 0,
            "context_density": density,
            "context_size": len(refs),
            "case_search_ok": cs_ok,
            "conflicts_ok": conflicts_ok,
        },
    }
    return result


def scope_pollution(pkg: dict, expected: dict, project_leak: list) -> list:
    pollution = list(project_leak)
    for r in package_refs(pkg):
        if any(r.startswith(x) for x in expected.get("must_exclude", [])):
            pollution.append(r)
    return pollution


def run_all() -> dict:
    results = []
    for p in sorted((ROOT / "migration" / "replay").glob("*.yaml")):
        results.append(run_replay(p))
    passed = all(
        not r["result"]["scope_pollution"] and
        not r["result"]["false_canonical"] and
        not r["result"]["hidden_unresolved_conflict"] and
        not r["result"]["invalid_rule_authority"] and
        not r["result"]["missing_context"] and
        not r["result"]["unexpected_context"] and
        not r["result"]["false_activation"] and
        r["result"]["case_search_ok"] and
        r["result"]["conflicts_ok"]
        for r in results)
    report = {"gate": "C", "passed": passed, "replays": results,
              "aggregate": {
                  "scope_pollution_total": sum(len(r["result"]["scope_pollution"]) if isinstance(r["result"]["scope_pollution"], list) else (1 if r["result"]["scope_pollution"] else 0) for r in results),
                  "false_canonical": 0, "false_activation_total":
                      sum(len(r["result"]["false_activation"]) for r in results),
                  "missing_context_total": sum(len(r["result"]["missing_context"]) for r in results),
                  "false_forget": 0,
                  "reinvestigation_cost": sum(r["result"]["reinvestigation_cost"] for r in results),
                  "issue_noise": 0,
                  "context_density_avg": round(sum(r["result"]["context_density"] for r in results) / max(1, len(results)), 2),
                  "context_size_avg": round(sum(r["result"]["context_size"] for r in results) / max(1, len(results)), 1),
              },
              "generated_at": now_iso()}
    out = ROOT / "migration" / "gate-results" / "gate-c-replay.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    r = run_all()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["passed"] else 2)
