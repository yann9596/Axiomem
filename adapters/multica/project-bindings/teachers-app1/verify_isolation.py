#!/usr/bin/env python3
"""YZT-98 isolation verification probes for the archived `app1` scope and the
new `teachers-app1` scope.

These are **deterministic internal probes**, not formal Context Handoff gate
results: they call the frozen T01/T03/T04/T05 helpers directly. A formal
PREPARE_HANDOFF / SELF_CHECK additionally requires a production Findings source
binding (see findings-source-plan.md) and is NOT_RUN until that binding is
activated.

Every probe prints name / expectation / observed / verdict so the evidence is
inspectable without trusting prose.

Usage:
  python verify_isolation.py --repo <context-repo-root>
  python verify_isolation.py --repo <root> --dump-package web-imagegen context-engineer
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Pins captured at the YZT-98 baseline commit 251034892d3e6b62d17b64b412e0f4a76ad7a555
# (before the isolation repair). They are used as the "stale package" input.
BASELINE_MEMORY_REVISION = (
    "sha256:30b51dea6d6f2a09b3ec25d283d207f8705d4198206664e33049ef6285138561")
BASELINE_REGISTRY_REVISION = (
    "sha256:a08e20ebea57bf830c605e8c9cc87950bc3781350d1d0c22c01e83e994684edb")
BASELINE_ROLE_PROFILE_REVISION = (
    "sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f")
ARCHIVED_MULTICA_PROJECT_ID = "c671779f-059a-41b7-af11-95636de809ec"
NEW_MULTICA_PROJECT_ID = "7a2195b5-6628-4b02-9fb2-bc3ce161de85"


def _request(project_id: str, task_ref: str, *,
             title: str = "YZT-98 isolation probe",
             description: str = "probe: archived app1 scope must fail closed") -> dict:
    return {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": task_ref,
        "project": {"project_id": project_id},
        "target": {"role": "context-engineer"},
        "purpose": "context_assembly",
        "caller": {"role": "context-engineer"},
        "task_snapshot": {
            "title": title,
            "description": description,
            "requirements": [],
            "acceptance_criteria": [],
            "relevant_decisions": [],
        },
        "options": {},
    }


def _envelope(project_id: str, task_ref: str, *, built_from: dict,
              status: str = "READY") -> dict:
    return {
        "schema_version": "1.1",
        "kind": "prepare_handoff_result",
        "package_id": f"CTX-probe-{project_id}",
        "task_ref": task_ref,
        "role": "context-engineer",
        "status": status,
        "generated_at": "2026-09-16T04:05:00Z",
        "built_from": built_from,
        "escalation": {"required": False},
        "package": {
            "schema_version": "1.1",
            "kind": "context_package",
            "scope": {"type": "project", "project_id": project_id,
                      "projects": [], "task_id": None},
            "request": {"task_id": task_ref, "role": "context-engineer",
                        "project_id": project_id},
            "cases": [], "current_facts": [], "rules": [],
            "open_conflicts": [], "blocked_by": [],
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--dump-package", nargs=2, metavar=("PROJECT", "ROLE"),
                        help="print one deterministic package (for before/after diff)")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / "tools" / "chandoff_plan.py").is_file():
        print(json.dumps({"ok": False, "error": "repo_not_verified",
                          "repo": str(repo)}), file=sys.stderr)
        return 2
    sys.path.insert(0, str(repo / "tools"))

    import chandoff  # noqa: E402
    import chandoff_plan as plan  # noqa: E402
    import chandoff_selfcheck as selfcheck  # noqa: E402
    import cbuild  # noqa: E402

    if args.dump_package:
        project, role = args.dump_package
        pkg = cbuild.build_package(
            f"multica://issue/YZT-98", role,
            {"type": "project", "project_id": project, "projects": [],
             "task_id": None},
            decision="deterministic comparison dump", query="", limit=10)
        pkg.pop("generated_at", None)
        (pkg.get("assembly_trace") or {}).pop("generated_at", None)
        print(json.dumps(pkg, ensure_ascii=False, sort_keys=True, indent=2))
        return 0

    registry = plan.load_registry()
    by_id = {p["id"]: p for p in registry["projects"]}
    probes = []

    def record(name, expected, observed, ok):
        probes.append({"probe": name, "expected": expected,
                       "observed": observed, "pass": bool(ok)})

    # 1. Registry truth.
    record("registry_app1_phase", "archived",
           by_id.get("app1", {}).get("phase"),
           by_id.get("app1", {}).get("phase") == "archived")
    record("registry_teachers_app1_phase", "incubation",
           by_id.get("teachers-app1", {}).get("phase"),
           by_id.get("teachers-app1", {}).get("phase") == "incubation")
    record("registry_teachers_app1_multica_id", NEW_MULTICA_PROJECT_ID,
           by_id.get("teachers-app1", {}).get("multica_project_id"),
           by_id.get("teachers-app1", {}).get("multica_project_id")
           == NEW_MULTICA_PROJECT_ID)

    # 2. Old identity fails closed at PLAN scope resolution.
    try:
        plan.resolve_handoff_scope(_request("app1", "multica://issue/YZT-98"))
        observed, ok = "resolved (no error)", False
    except Exception as exc:  # ScopeError is the expected bounded stop
        observed = f"{type(exc).__name__}: {exc}"
        ok = "archived project 'app1' excluded" in str(exc)
    record("plan_rejects_archived_app1", "ScopeError archived project", observed, ok)

    # 2b. The Multica UUID of the retired project maps to the archived scope,
    #     so the same bounded stop applies instead of a silent re-scope.
    mapped = json.loads(
        (repo / "adapters" / "multica" / "project-map.json").read_text(encoding="utf-8"))
    record("old_multica_uuid_maps_to_archived_scope", "app1",
           mapped.get(ARCHIVED_MULTICA_PROJECT_ID),
           mapped.get(ARCHIVED_MULTICA_PROJECT_ID) == "app1")
    record("new_multica_uuid_maps_to_new_scope", "teachers-app1",
           mapped.get(NEW_MULTICA_PROJECT_ID),
           mapped.get(NEW_MULTICA_PROJECT_ID) == "teachers-app1")

    # 3. New identity resolves.
    try:
        scope = plan.resolve_handoff_scope(
            _request("teachers-app1", "multica://issue/YZT-98"))
        observed, ok = json.dumps(scope, ensure_ascii=False), (
            scope.get("project_id") == "teachers-app1")
    except Exception as exc:
        observed, ok = f"{type(exc).__name__}: {exc}", False
    record("plan_resolves_teachers_app1", "project scope teachers-app1", observed, ok)

    # 4. New-project package carries no old-project object.
    pkg = cbuild.build_package(
        "multica://issue/YZT-98", "context-engineer",
        {"type": "project", "project_id": "teachers-app1", "projects": [],
         "task_id": None},
        decision="isolation probe", query="", limit=10)
    old_refs = [r["ref"] for r in pkg["rules"] + pkg["current_facts"]
                if "APP1-" in r["ref"] and "TAPP1" not in r["ref"]]
    record("teachers_app1_package_has_no_app1_objects", [],
           sorted(old_refs), old_refs == [])
    anchor = pkg["anchor_digest"]
    record("teachers_app1_anchor_is_new_project",
           "Learning Context", (anchor.get("mission") or "")[:60],
           "Learning Context" in (anchor.get("mission") or ""))
    record("teachers_app1_package_excluded_other_projects", "> 0",
           pkg["assembly_trace"]["excluded_counts"].get("other_project"),
           (pkg["assembly_trace"]["excluded_counts"].get("other_project") or 0) > 0)

    # 5. SELF_CHECK rejects a package scoped to the archived project.
    req = {
        "schema_version": "1.1", "kind": "self_check_request",
        "task_ref": "multica://issue/YZT-98", "role": "context-engineer",
        "task_snapshot": _request("teachers-app1", "multica://issue/YZT-98")["task_snapshot"],
    }
    revs = chandoff.compute_built_from(_request("teachers-app1", "multica://issue/YZT-98"))
    res_old = selfcheck.self_check(req, packages=[_envelope(
        "app1", "multica://issue/YZT-98", built_from=revs)])
    record("selfcheck_rejects_archived_scope_package", "scope_mismatch in reasons",
           res_old["reasons"], "scope_mismatch" in res_old["reasons"])
    record("selfcheck_rejects_archived_scope_status", "REFRESH_REQUIRED",
           res_old["status"], res_old["status"] == "REFRESH_REQUIRED")

    # 6. Stale-package counterexample: baseline (pre-repair) revisions.
    stale = {"task_fingerprint": revs["task_fingerprint"],
             "memory_revision": BASELINE_MEMORY_REVISION,
             "registry_revision": BASELINE_REGISTRY_REVISION,
             "role_profile_revision": BASELINE_ROLE_PROFILE_REVISION}
    res_stale = selfcheck.self_check(req, packages=[_envelope(
        "teachers-app1", "multica://issue/YZT-98", built_from=stale)])
    record("selfcheck_stale_package_status", "REFRESH_REQUIRED",
           res_stale["status"], res_stale["status"] == "REFRESH_REQUIRED")
    record("selfcheck_stale_package_reasons",
           "memory_revision_changed + registry_revision_changed",
           res_stale["reasons"],
           "memory_revision_changed" in res_stale["reasons"]
           and "registry_revision_changed" in res_stale["reasons"])
    record("selfcheck_role_profile_untouched", "role_profile_revision_changed absent",
           res_stale["reasons"],
           "role_profile_revision_changed" not in res_stale["reasons"])

    # 7. Role-mismatch counterexample. `package_ref` pins the candidate
    #    explicitly; otherwise discovery matches on (task_ref, role) and a
    #    mismatching pair would surface as `package_missing` instead.
    req_mismatch = dict(req, role="qa",
                        package_ref="CTX-probe-teachers-app1")
    res_role = selfcheck.self_check(req_mismatch, packages=[_envelope(
        "teachers-app1", "multica://issue/YZT-98", built_from=revs)])
    record("selfcheck_role_mismatch", "role_mismatch in reasons",
           res_role["reasons"], "role_mismatch" in res_role["reasons"])

    # 8. Task-changed counterexample (same role, different task_ref).
    req_task = dict(req, task_ref="multica://issue/YZT-97",
                    package_ref="CTX-probe-teachers-app1")
    res_task = selfcheck.self_check(req_task, packages=[_envelope(
        "teachers-app1", "multica://issue/YZT-98", built_from=revs)])
    record("selfcheck_task_changed", "task_changed in reasons",
           res_task["reasons"], "task_changed" in res_task["reasons"])

    # 9. Missing package counterexample.
    res_missing = selfcheck.self_check(req, packages=[])
    record("selfcheck_missing_package", "REFRESH_REQUIRED/package_missing",
           {"status": res_missing["status"], "reasons": res_missing["reasons"]},
           res_missing["status"] == "REFRESH_REQUIRED"
           and "package_missing" in res_missing["reasons"])

    failed = [p for p in probes if not p["pass"]]
    print(json.dumps({
        "kind": "yzt98_isolation_probes",
        "note": "deterministic internal probes, NOT formal handoff gate results",
        "project": "teachers-app1",
        "probes": probes,
        "passed": len(probes) - len(failed),
        "failed": len(failed),
        "ok": not failed,
    }, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
