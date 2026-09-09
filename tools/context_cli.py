#!/usr/bin/env python3
"""Multica Context & Memory System V1.1 CLI (zero-dependency).

Bypass-rebuild runtime for YZT-40 (R3-R9). The V1 CLI (tools/memory_cli.py)
stays untouched and remains production until human Cutover.

  validate-canonical   Gate A0/A: schema + authority + legacy accounting
  rebuild-index        rebuild the derived index from zero
  build                assemble a Task Context Package (scope-first)
  gate-b               Gate B hard acceptance tests (incl. YZT-22 probes)
  migrate-replay       Gate C: historical replay (expected vs actual)
  compat get|retrieve  old-call translation only (writes nothing)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    parser = argparse.ArgumentParser(description="Multica Context & Memory System V1.1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-canonical")
    sub.add_parser("rebuild-index")
    sub.add_parser("gate-b")
    sub.add_parser("migrate-replay")

    compat = sub.add_parser("compat", help="old-call translation only")
    compat_sub = compat.add_subparsers(dest="compat_command", required=True)
    g = compat_sub.add_parser("get")
    g.add_argument("task_id")
    g.add_argument("role")
    g.add_argument("decision")
    g.add_argument("--chain", default="team-governance")
    g.add_argument("--query", default="")
    g.add_argument("--limit", type=int, default=10)
    rt = compat_sub.add_parser("retrieve")
    rt.add_argument("query")
    rt.add_argument("--limit", type=int, default=10)

    build = sub.add_parser("build")
    build.add_argument("--task-id", required=True)
    build.add_argument("--role", required=True)
    build.add_argument("--project")
    build.add_argument("--scope", choices=["project", "team", "cross_project"])
    build.add_argument("--projects", default="")
    build.add_argument("--decision", default="")
    build.add_argument("--query", default="")
    build.add_argument("--case-trigger", default=None)
    build.add_argument("--limit", type=int, default=8)

    ph = sub.add_parser("prepare-handoff-plan",
                        help="T01 deterministic context_plan (no model call)")
    ph.add_argument("--request-file", required=True)

    sc = sub.add_parser("semantic-compose",
                        help="T02 bounded semantic compose validation (no model call)")
    sc.add_argument("--plan-file", required=True)
    sc.add_argument("--result-file", required=True)

    fin = sub.add_parser("prepare-handoff-finalize",
                         help="T03 deterministic FINALIZE (no model call)")
    fin.add_argument("--plan-file", required=True)
    fin.add_argument("--result-file", required=True)
    fin.add_argument("--request-file", required=True)

    args = parser.parse_args()
    try:
        if args.command == "validate-canonical":
            import validate_canonical
            return validate_canonical.main()
        if args.command == "rebuild-index":
            import index_builder
            print(json.dumps(index_builder.rebuild(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "gate-b":
            import cgates
            report = cgates.gate_b()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 2
        if args.command == "migrate-replay":
            import creplay
            report = creplay.run_all()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["passed"] else 2
        if args.command == "build":
            from cbuild import resolve_scope, build_package
            projects = [p.strip() for p in (args.projects or "").split(",") if p.strip()]
            scope = resolve_scope(args.task_id, args.scope or "project",
                                  projects, args.project)
            pkg = build_package(args.task_id, args.role, scope,
                                decision=args.decision, query=args.query,
                                case_trigger=args.case_trigger, limit=args.limit)
            print(json.dumps(pkg, ensure_ascii=False, indent=2))
            return 0
        if args.command == "prepare-handoff-plan":
            import chandoff_plan
            request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
            result = chandoff_plan.prepare_handoff_plan(request)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") == "PLAN_READY" else 2
        if args.command == "semantic-compose":
            import chandoff_compose
            plan_input = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
            proposed = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
            result = chandoff_compose.compose_semantic(plan_input, proposed)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") == "ACCEPTED" else 2
        if args.command == "prepare-handoff-finalize":
            import chandoff_finalize
            plan_input = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
            compose_input = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
            request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
            result = chandoff_finalize.finalize_handoff(plan_input, compose_input, request)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") in {"READY", "PARTIAL", "BLOCKED"} else 2
        if args.command == "compat":
            import ccompat
            if args.compat_command == "get":
                pkg = ccompat.compat_get(args.task_id, args.role, args.decision,
                                         args.chain, args.query, args.limit)
            else:
                pkg = ccompat.compat_retrieve(args.query, args.limit)
            print(json.dumps(pkg, ensure_ascii=False, indent=2))
            return 0
        parser.error(f"unknown command {args.command}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
