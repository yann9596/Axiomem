#!/usr/bin/env python3
"""Gate B — runtime isolation hard acceptance tests (YZT-40 R7).

All checks must pass; any scope leakage is a STOP condition (YZT-22 blocker).
Writes migration/gate-results/gate-b.json.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import ROOT, DB, ScopeError, now_iso  # noqa: E402
from cbuild import resolve_scope, build_package  # noqa: E402
from crole import role_content_differs, content_fingerprint  # noqa: E402

NORMAL_TASK_ID = "b10-probe"
ROLE_PROBE_TASK_ID = "b8-probe"
ROLE_PROBE_DECISION = "project posture provider switch"


def refs_of(pkg: dict) -> list:
    return [r["ref"] for r in pkg["rules"]] + \
        [f["ref"] for f in pkg["current_facts"]] + \
        [c["ref"] for c in pkg["cases"]]


def role_profile_changes_ok(lead: dict, se: dict) -> tuple[bool, str]:
    """Content-level assertion. Echoed role labels are not evidence."""
    changed = role_content_differs(lead, se)
    fp_lead, fp_se = content_fingerprint(lead), content_fingerprint(se)
    detail = (f"lead_team={fp_lead['team_state_slice']} "
              f"se_team={fp_se['team_state_slice']} "
              f"lead_project={fp_lead['project_state_slice']} "
              f"se_project={fp_se['project_state_slice']}")
    return changed, detail


def default_case_count_ok(pkg: dict, expected_task_id: str = NORMAL_TASK_ID) -> tuple[bool, str]:
    tid = (pkg.get("request") or {}).get("task_id")
    n = len(pkg.get("cases") or [])
    searched = bool((pkg.get("assembly_trace") or {}).get("case_search_performed"))
    if tid != expected_task_id:
        return False, (f"checked task_id={tid!r} cases={n} search={searched}; "
                       f"expected normal-task id {expected_task_id!r}")
    ok = n == 0 and not searched
    return ok, f"task={tid} cases={n} search_performed={searched}"


def gate_b() -> dict:
    from index_builder import rebuild
    checks = []

    def record(name, passed, detail):
        checks.append({"test": name, "passed": bool(passed), "detail": detail})

    stats = rebuild()
    record("rebuild_from_zero", stats["objects"] > 0,
           f"{stats['objects']} objects rebuilt after deleting the index directory")

    hashes = {}
    for p in sorted(ROOT.glob("team-context/**/*.yaml")) + \
            sorted(ROOT.glob("project-context/**/*.yaml")):
        hashes[str(p)] = p.read_bytes()
    s = resolve_scope("b2-probe", None, [], "app1")
    before = refs_of(build_package("b2-probe", "software-engineer", s,
                                   decision="implement provider switch"))
    shutil.rmtree(DB.parent, ignore_errors=True)
    stats2 = rebuild()
    after = refs_of(build_package("b2-probe", "software-engineer", s,
                                  decision="implement provider switch"))
    unchanged = all(p.read_bytes() == h for p, h in
                    ((Path(k), v) for k, v in hashes.items()))
    record("derived_index_not_canonical",
           stats2["objects"] > 0 and before == after and unchanged,
           f"index dir deleted -> rebuilt {stats2['objects']} objects from zero; "
           f"retrieval identical ({before == after}); "
           f"{len(hashes)} canonical files byte-identical ({unchanged})")

    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "index/v1.1/", "runtime/v1.1/"],
        capture_output=True, text=True).stdout.strip()
    ignored = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "index/v1.1/memory.db"],
        capture_output=True, text=True).returncode == 0
    record("index_not_git_tracked", tracked == "" and ignored,
           f"git ls-files empty={tracked == ''}; check-ignore={ignored}")

    blocked = False
    try:
        resolve_scope("b4-probe", None, [], None)
    except ScopeError:
        blocked = True
    record("scope_filter_before_search", blocked,
           "unresolved scope raises before any retrieval (D-02)")

    s = resolve_scope("yz-22-probe", None, [], "app1")
    pkg = build_package("yz-22-probe", "software-engineer", s,
                        decision="provider grok gpt chrome skill model core domain")
    leak = [r for r in refs_of(pkg) if "wimg" in r or "web-imagegen" in r]
    record("app1_cannot_retrieve_web_imagegen", not leak,
           "no leakage" if not leak else f"leaked {leak}")

    s2 = resolve_scope("b6-probe", None, [], "web-imagegen")
    pkg2 = build_package("b6-probe", "software-engineer", s2,
                         decision="student evidence attention thread state followup")
    leak2 = [r for r in refs_of(pkg2) if "app1" in r]
    record("web_imagegen_cannot_retrieve_app1", not leak2,
           "no leakage" if not leak2 else f"leaked {leak2}")

    b7_ok = False
    try:
        resolve_scope("b7-probe", "cross_project", [], None)
    except ScopeError:
        b7_ok = True
    record("cross_project_access_without_explicit_condition", b7_ok,
           "implicit cross-project access denied")

    s3 = resolve_scope(ROLE_PROBE_TASK_ID, None, [], "web-imagegen")
    lead = build_package(ROLE_PROBE_TASK_ID, "engineering-lead", s3,
                         decision=ROLE_PROBE_DECISION)
    se = build_package(ROLE_PROBE_TASK_ID, "software-engineer", s3,
                       decision=ROLE_PROBE_DECISION)
    role_ok, role_detail = role_profile_changes_ok(lead, se)
    record("role_profile_changes_context", role_ok, role_detail)

    s4 = resolve_scope("b9-probe", None, [], "app1")
    pkg4 = build_package("b9-probe", "solution-architect", s4, decision="app1 domain design")
    bad_slice = [e["id"] for e in pkg4["project_state_slice"] if e.get("checkpoint") != "app1"]
    record("checkpoint_slice_is_task_relevant", not bad_slice,
           "slice clean" if not bad_slice else f"cross-project entries {bad_slice}")

    pkg5 = build_package(NORMAL_TASK_ID, "software-engineer", s4,
                         decision="small normal implementation task")
    case_ok, case_detail = default_case_count_ok(pkg5, expected_task_id=NORMAL_TASK_ID)
    record("default_case_count_for_normal_task", case_ok, case_detail)

    report = {"gate": "B", "passed": all(c["passed"] for c in checks),
              "checks": checks, "generated_at": now_iso()}
    out = ROOT / "migration" / "gate-results" / "gate-b.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
