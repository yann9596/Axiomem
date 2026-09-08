#!/usr/bin/env python3
"""Gate C — historical replay & shadow validation harness (YZT-40 R9)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import ROOT, toks, now_iso, doc_at  # noqa: E402
from cbuild import resolve_scope, build_package  # noqa: E402
from cmetrics import metric_bundle, verify_expectation_lock  # noqa: E402


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
    metrics = metric_bundle(case["task_id"], refs, pkg["blocked_by"])
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
            "false_canonical": metrics["false_canonical"],
            "false_canonical_refs": metrics["false_canonical_refs"],
            "hidden_unresolved_conflict": len(pkg["open_conflicts"]) > expected.get("expected_conflicts", 0),
            "invalid_rule_authority": any(not r.get("authority_refs") for r in pkg["rules"]),
            "false_activation": false_activation,
            "missing_context": missing,
            "unexpected_context": unexpected,
            "reinvestigation_cost": len(missing),
            "issue_noise": metrics["issue_noise"],
            "issue_noise_refs": metrics["issue_noise_refs"],
            "false_forget": metrics["false_forget"],
            "false_forget_refs": metrics["false_forget_refs"],
            "metric_status": metrics["status"],
            "missing_evidence": metrics["missing_evidence"],
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


def _zero_metric(values) -> bool:
    return all(v == 0 for v in values)


def run_all() -> dict:
    results = []
    for p in sorted((ROOT / "migration" / "replay").glob("*.yaml")):
        results.append(run_replay(p))
    provenance = verify_expectation_lock()
    missing_evidence = list(provenance.get("missing_evidence") or [])
    for r in results:
        missing_evidence.extend(r["result"].get("missing_evidence") or [])
    metric_complete = all(r["result"].get("metric_status") == "computed" for r in results)
    fc_vals = [r["result"]["false_canonical"] for r in results]
    ff_vals = [r["result"]["false_forget"] for r in results]
    noise_vals = [r["result"]["issue_noise"] for r in results]
    passed = (
        bool(results) and
        metric_complete and
        provenance.get("ok") and
        not missing_evidence and
        all(
            not r["result"]["scope_pollution"] and
            r["result"]["false_canonical"] == 0 and
            not r["result"]["false_forget"] and
            not r["result"]["issue_noise"] and
            not r["result"]["hidden_unresolved_conflict"] and
            not r["result"]["invalid_rule_authority"] and
            not r["result"]["missing_context"] and
            not r["result"]["unexpected_context"] and
            not r["result"]["false_activation"] and
            r["result"]["case_search_ok"] and
            r["result"]["conflicts_ok"]
            for r in results))
    report = {"gate": "C", "passed": passed, "replays": results,
              "provenance": provenance,
              "missing_evidence": sorted(set(missing_evidence)),
              "aggregate": {
                  "scope_pollution_total": sum(len(r["result"]["scope_pollution"]) if isinstance(r["result"]["scope_pollution"], list) else (1 if r["result"]["scope_pollution"] else 0) for r in results),
                  "false_canonical": (sum(fc_vals) if metric_complete and _zero_metric(fc_vals) is not None and all(v is not None for v in fc_vals) else None),
                  "false_activation_total":
                      sum(len(r["result"]["false_activation"]) for r in results),
                  "missing_context_total": sum(len(r["result"]["missing_context"]) for r in results),
                  "false_forget": (sum(ff_vals) if metric_complete and all(v is not None for v in ff_vals) else None),
                  "reinvestigation_cost": sum(r["result"]["reinvestigation_cost"] for r in results),
                  "issue_noise": (sum(noise_vals) if metric_complete and all(v is not None for v in noise_vals) else None),
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
