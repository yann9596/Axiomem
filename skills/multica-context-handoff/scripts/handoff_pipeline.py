#!/usr/bin/env python3
"""Deterministic pipeline driver for the multica-context-handoff skill (T07).

Chains the accepted T00-T06 surfaces so a caller agent never re-implements
the handoff mechanics:

  prepare    T05 snapshot adapter -> T01 PLAN boundary (BLOCKED stops here)
  finalize   T02 compose validation -> T03 FINALIZE (READY / PARTIAL / BLOCKED)
  selfcheck  T06 discovery -> T04 SELF_CHECK (READY / REFRESH_REQUIRED / BLOCKED)
  publish    T06 non-trigger /note publisher (explicit authorization required)

Boundaries this driver keeps:
- It never calls a model. The only semantic step (composing a frozen
  semantic_compose_result over PLAN candidates) belongs to the caller agent.
- It never issues Multica argv itself. Issue reads enter through the T05
  adapter allowlist; the only reachable write is the T06 publisher allowlist
  (one validated `issue comment add`), guarded by explicit authorization.
- It never writes Canonical Memory, never rebuilds any index, never amends a
  frozen schema, never imports/binds workspace skills, never assigns,
  never mentions, never triggers a downstream run.
- The Context repository root is resolved from an explicit --repo or from
  the script's own location inside a repository tree, and is verified before
  use; it is never guessed and never hard-coded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Callable

PIPELINE_VERSION = "T07/1.0"

READY_EXIT, BOUNDED_EXIT, BLOCKED_EXIT, STOP_EXIT = 0, 2, 3, 4

# Every stage output carries the same zero-side-effect guarantees. The caller
# agent's own semantic compose step is the only LLM work in the protocol and
# happens outside this driver.
GUARANTEES = {
    "pipeline_llm_calls": 0,
    "canonical_writes": 0,
    "memory_rebuilds": 0,
    "issue_lifecycle_writes": 0,
    "assignments": 0,
    "mentions": 0,
    "downstream_run_triggers": 0,
    "frozen_schema_changes": 0,
    "workspace_skill_imports": 0,
    "agent_skill_bindings": 0,
}

MARKERS = ("tools/chandoff.py", "schemas/context-handoff/prepare-handoff-request.schema.json")

_MODULES = {}


class PipelineError(Exception):
    """Bounded stop. The failure is reported, never guessed away."""

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = {k: str(v)[:240] for k, v in self.details.items()}
        return out


def _bounded_error(stage: str, exc: Exception) -> dict:
    err = exc.envelope() if hasattr(exc, "envelope") else {
        "code": type(exc).__name__, "message": str(exc)[:240]}
    return {"ok": False, "stage": stage, "error": err,
            "guarantees": dict(GUARANTEES)}


def resolve_repo_root(explicit: str | None) -> Path:
    """Resolve and verify the Context repository root. Fail closed."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    else:
        candidates.append(Path(__file__).resolve().parents[3])
    for root in candidates:
        if all((root / marker).is_file() for marker in MARKERS):
            return root
    if explicit:
        raise PipelineError(
            "repo_root_not_verified",
            "the explicit --repo path does not contain the verified Context "
            "repository markers (tools/chandoff.py, frozen handoff schemas)",
            repo=explicit)
    raise PipelineError(
        "repo_root_unresolved",
        "no verified Context repository found; pass --repo with the "
        "caller/runtime-provided Context repository root")


def _tools(root: Path):
    if _MODULES and _MODULES.get("root") != root:
        raise PipelineError(
            "repo_root_rebind_refused",
            "tools modules are already bound to another repository root in "
            "this process; use one root per process",
            bound=str(_MODULES["root"]), requested=str(root))
    if not _MODULES:
        tools_dir = str(root / "tools")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import chandoff_adapter  # noqa: F401
        import chandoff_compose  # noqa: F401
        import chandoff_finalize  # noqa: F401
        import chandoff_note  # noqa: F401
        import chandoff_plan  # noqa: F401
        import chandoff_selfcheck  # noqa: F401
        _MODULES.update({
            "root": root,
            "adapter": sys.modules["chandoff_adapter"],
            "compose": sys.modules["chandoff_compose"],
            "finalize": sys.modules["chandoff_finalize"],
            "note": sys.modules["chandoff_note"],
            "plan": sys.modules["chandoff_plan"],
            "selfcheck": sys.modules["chandoff_selfcheck"],
        })
    return _MODULES


def _json_write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _json_read(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _out_dir(args) -> Path:
    return Path(args.out_dir).resolve() if getattr(args, "out_dir", None) \
        else Path(tempfile.mkdtemp(prefix="ctx-handoff-"))


def _thread_specs(specs) -> dict:
    out = {}
    for spec in specs or []:
        comment_id, sep, path = str(spec).partition("=")
        if not sep or not comment_id.strip() or not path.strip():
            raise PipelineError("bad_thread_file_spec",
                                "want COMMENT_ID=PATH", spec=str(spec))
        out[comment_id.strip()] = path.strip()
    return out


def _candidate_counts(plan: dict) -> dict:
    cands = plan.get("candidates") or {}
    return {key: len(cands.get(key) or [])
            for key in ("rules", "facts", "cases", "checkpoint_entries", "conflicts")}


# ---------------------------------------------------------------------------
# PREPARE_HANDOFF: T05 snapshot -> T01 PLAN.
# ---------------------------------------------------------------------------

def run_prepare(args, *, adapter_fn: Callable | None = None,
                plan_fn: Callable | None = None) -> tuple[dict, int]:
    root = resolve_repo_root(args.repo)
    tools = _tools(root)
    out = _out_dir(args)
    try:
        options = json.loads(args.options_json) if args.options_json else None
    except json.JSONDecodeError as exc:
        return {"ok": False, "stage": "snapshot",
                "error": {"code": "options_json_invalid",
                          "message": f"--options-json is not valid JSON: {exc}"},
                "guarantees": dict(GUARANTEES)}, BOUNDED_EXIT
    adapter = adapter_fn or tools["adapter"].build_snapshot_request
    try:
        envelope = adapter(
            issue_id=args.issue,
            target_role=args.target_role,
            caller_role=args.caller_role,
            purpose=args.purpose,
            project_map=_json_read(args.project_map) if args.project_map else None,
            map_source=args.project_map,
            explicit_project_id=args.project_id,
            decision_comment_ids=args.decision_comment,
            decision_markers=args.decision_marker,
            options=options,
            issue_file=args.issue_file,
            parent_file=args.parent_file,
            thread_files=_thread_specs(args.thread_file),
        )
    except Exception as exc:  # bounded AdapterError or CLI contract drift
        return _bounded_error("snapshot", exc), BOUNDED_EXIT
    _json_write(out / "request-envelope.json", envelope)
    request = envelope["request"]
    _json_write(out / "request.json", request)

    plan_fn = plan_fn or tools["plan"].prepare_handoff_plan
    try:
        plan_result = plan_fn(request)
    except Exception as exc:
        return _bounded_error("plan", exc), BOUNDED_EXIT
    _json_write(out / "plan-envelope.json", plan_result)

    if plan_result.get("status") != "PLAN_READY":
        gate = plan_result.get("finding_gate") or {}
        return {
            "ok": False,
            "stage": "plan",
            "status": plan_result.get("status"),
            "task_ref": request["task_ref"],
            "target_role": request["target"]["role"],
            "escalation": plan_result.get("escalation") or {"required": False},
            "blocked": True,
            "finding_gate": {
                "status": gate.get("status"),
                "blocked_findings": [
                    {"finding_id": f.get("finding_id"), "reason": f.get("reason")}
                    for f in (gate.get("blocked_findings") or [])],
            },
            "artifacts": {"dir": str(out)},
            "guarantees": dict(GUARANTEES),
        }, BLOCKED_EXIT

    plan = plan_result["plan"]
    return {
        "ok": True,
        "stage": "plan",
        "status": "PLAN_READY",
        "plan_id": plan.get("plan_id"),
        "task_ref": request["task_ref"],
        "target_role": request["target"]["role"],
        "semantic_jobs": list(plan.get("semantic_jobs") or []),
        "candidates": _candidate_counts(plan),
        "case_search_allowed": bool((plan.get("case_search") or {}).get("allowed")),
        "built_from": plan_result.get("built_from"),
        "escalation": plan_result.get("escalation") or {"required": False},
        "context_engineer_woken": bool(plan_result.get("context_engineer_woken")),
        "artifacts": {
            "dir": str(out),
            "request": str(out / "request.json"),
            "plan_envelope": str(out / "plan-envelope.json"),
        },
        "next": ("compose a frozen semantic_compose_result over the PLAN "
                 "candidates only, then run the finalize command"),
        "guarantees": dict(GUARANTEES),
    }, READY_EXIT


# ---------------------------------------------------------------------------
# Compose validation (T02) + FINALIZE (T03), with the one-repair bound.
# ---------------------------------------------------------------------------

def run_finalize(args, *, compose_fn: Callable | None = None,
                 finalize_fn: Callable | None = None) -> tuple[dict, int]:
    root = resolve_repo_root(args.repo)
    tools = _tools(root)
    out = _out_dir(args)
    plan_input = _json_read(args.plan_file)
    proposed = _json_read(args.result_file)
    request = _json_read(args.request_file)

    compose_fn = compose_fn or tools["compose"].compose_semantic
    validation = compose_fn(plan_input, proposed)
    _json_write(out / "compose-validation.json", validation)

    if validation.get("status") != "ACCEPTED":
        exhausted = int(getattr(args, "repairs_used", 0) or 0) >= 1
        payload = {
            "ok": False,
            "stage": "compose",
            "status": "REJECTED",
            "plan_id": validation.get("plan_id"),
            "errors": validation.get("errors") or [],
            "repair_exhausted": exhausted,
            "repair_allowed": not exhausted,
            "artifacts": {"dir": str(out),
                          "compose_validation": str(out / "compose-validation.json")},
            "guarantees": dict(GUARANTEES),
        }
        if exhausted:
            payload["stop"] = ("the single bounded same-PLAN repair is spent; "
                               "the handoff stops")
        return payload, (STOP_EXIT if exhausted else BOUNDED_EXIT)

    finalize_fn = finalize_fn or tools["finalize"].finalize_handoff
    result = finalize_fn(plan_input, validation, request)
    _json_write(out / "result.json", result)

    status = result.get("status")
    package = result.get("package") or {}
    gaps = sorted(set((package.get("blocked_by") or []) +
                      [c.get("id") for c in (package.get("open_conflicts") or [])
                       if c.get("id")]))
    if status == "READY":
        publish = {"publishable": True,
                   "requires_authorization": "--authorize-publish",
                   "normal_ready": True}
    elif status == "PARTIAL":
        publish = {"publishable": "only_with_explicit_caller_authorization",
                   "requires_authorization": ["--authorize-publish", "--allow-partial"],
                   "normal_ready": False,
                   "gaps_preserved": True}
    else:
        publish = {"publishable": False, "normal_ready": False,
                   "never_published": True}
    return {
        "ok": True,
        "stage": "finalize",
        "status": status,
        "package_id": result.get("package_id"),
        "task_ref": result.get("task_ref"),
        "role": result.get("role"),
        "built_from": result.get("built_from"),
        "gaps": gaps,
        "escalation": result.get("escalation") or {"required": False},
        "publish": publish,
        "artifacts": {
            "dir": str(out),
            "result": str(out / "result.json"),
            "compose_validation": str(out / "compose-validation.json"),
        },
        "guarantees": dict(GUARANTEES),
    }, {"READY": READY_EXIT, "PARTIAL": BOUNDED_EXIT, "BLOCKED": BLOCKED_EXIT}[status]


# ---------------------------------------------------------------------------
# SELF_CHECK: T06 discovery -> T04 deterministic check.
# ---------------------------------------------------------------------------

def _self_check_request(args) -> dict:
    """Build the frozen self_check_request.

    --request-from derives it deterministically from a prepare_handoff_request
    (or a T05 request envelope): same task_ref, snapshot, and the CALLER role
    as current role. --request-file loads a prepared request; --task-ref and
    --role then act as explicit caller overrides. The two sources are mutually
    exclusive; a bare task_ref/role without a snapshot never builds a request.
    """
    if args.request_from and (args.task_ref or args.role):
        raise PipelineError(
            "conflicting_request_sources",
            "use --request-from alone, or --task-ref/--role with --request-file")
    if args.request_from:
        source = _json_read(args.request_from)
        if source.get("kind") == "prepare_handoff_request":
            request = source
        elif isinstance(source.get("request"), dict):
            request = source["request"]
        else:
            raise PipelineError(
                "not_a_prepare_request",
                "--request-from must point to a prepare_handoff_request "
                "(or a T05 request envelope)")
        req = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": request["task_ref"],
            "role": request["caller"]["role"],
            "task_snapshot": request["task_snapshot"],
        }
    elif args.request_file:
        req = _json_read(args.request_file)
        if args.task_ref:
            req["task_ref"] = args.task_ref
        if args.role:
            req["role"] = args.role
    else:
        raise PipelineError(
            "missing_self_check_request",
            "pass --request-file, or --request-from with a prepare_handoff_request")
    if args.package_ref:
        req["package_ref"] = args.package_ref
    return req


def run_selfcheck(args, *, selfcheck_fn: Callable | None = None,
                  note_cli_factory: Callable | None = None,
                  finding_store=None) -> tuple[dict, int]:
    root = resolve_repo_root(args.repo)
    tools = _tools(root)
    out = _out_dir(args)
    try:
        request = _self_check_request(args)
    except PipelineError as exc:
        return {"ok": False, "stage": "selfcheck", "error": exc.envelope(),
                "guarantees": dict(GUARANTEES)}, BOUNDED_EXIT

    task_ref = request["task_ref"]
    role = request["role"]
    packages, provenance = [], {"mode": None}
    try:
        if args.envelope_file:
            packages = [_json_read(p) for p in args.envelope_file]
            provenance = {"mode": "offline_files",
                          "files": list(args.envelope_file)}
        else:
            note_cli = note_cli_factory() if note_cli_factory \
                else tools["note"].NoteCli(executable=args.executable)
            resolved = tools["note"].resolve_latest_handoff(
                args.issue, task_ref=task_ref, target_role=role, cli=note_cli)
            provenance = {
                "mode": "t06_discovery",
                "found": bool(resolved.get("found")),
                "comment": (resolved.get("comment") or {}),
                "records_seen": ((resolved.get("selection") or {})
                                 .get("records_seen")),
            }
            if resolved.get("found"):
                packages = [resolved["envelope"]]
    except Exception as exc:
        # Discovery fail-closed (e.g. a newer invalid same-target candidate)
        # stops here. Falling back to an older caller-supplied package is
        # never allowed.
        payload = _bounded_error("discovery", exc)
        payload["artifacts"] = {"dir": str(out)}
        return payload, BOUNDED_EXIT

    selfcheck_fn = selfcheck_fn or tools["selfcheck"].self_check_with_trace
    trace = selfcheck_fn(request, packages=packages or None,
                         store_dir=args.store, finding_store=finding_store)
    result = trace["result"]
    _json_write(out / "self-check-request.json", request)
    _json_write(out / "self-check-result.json", result)

    status = result["status"]
    work = {
        "READY": "allowed",
        "REFRESH_REQUIRED": "stopped_until_refreshed_ready",
        "BLOCKED": "stopped_escalation_required",
    }[status]
    payload = {
        "ok": True,
        "stage": "selfcheck",
        "status": status,
        "action": result.get("action"),
        "reasons": result.get("reasons") or [],
        "package_id": (packages[-1].get("package_id") if packages else None),
        "task_ref": task_ref,
        "current_role": role,
        "provenance": provenance,
        "consequential_work": work,
        "context_engineer_woken": bool(trace.get("context_engineer_woken")),
        "scope_pollution_from_findings": trace.get("scope_pollution_from_findings"),
        "artifacts": {
            "dir": str(out),
            "result": str(out / "self-check-result.json"),
        },
        "guarantees": dict(GUARANTEES),
    }
    if status == "REFRESH_REQUIRED":
        payload["refresh"] = {
            "instruction": ("run PREPARE_HANDOFF for the same task and the "
                            "current role; consequential work stays stopped "
                            "until the refreshed result is READY"),
            "task_ref": task_ref,
            "target_role": role,
        }
    return payload, {"READY": READY_EXIT, "REFRESH_REQUIRED": BOUNDED_EXIT,
                     "BLOCKED": BLOCKED_EXIT}[status]


# ---------------------------------------------------------------------------
# Publication through the T06 publisher (the only reachable Multica write).
# ---------------------------------------------------------------------------

def run_publish(args, *, note_cli_factory: Callable | None = None) -> tuple[dict, int]:
    root = resolve_repo_root(args.repo)
    tools = _tools(root)
    if not args.authorize_publish:
        return {
            "ok": False, "stage": "publish",
            "error": {"code": "publish_not_authorized",
                      "message": ("publication requires the explicit "
                                  "--authorize-publish flag from the caller")},
            "guarantees": dict(GUARANTEES),
        }, BOUNDED_EXIT
    envelope = _json_read(args.result_file)
    status = envelope.get("status")
    if status == "BLOCKED":
        return {
            "ok": False, "stage": "publish",
            "error": {"code": "blocked_never_publishable",
                      "message": "BLOCKED handoff results are never published"},
            "guarantees": dict(GUARANTEES),
        }, BLOCKED_EXIT
    if status == "PARTIAL" and not args.allow_partial:
        return {
            "ok": False, "stage": "publish",
            "error": {"code": "partial_requires_authorization",
                      "message": ("PARTIAL publication requires --allow-partial "
                                  "with explicit caller authorization; PARTIAL "
                                  "is never normal-ready")},
            "guarantees": dict(GUARANTEES),
        }, BOUNDED_EXIT

    cli = note_cli_factory() if note_cli_factory \
        else tools["note"].NoteCli(executable=args.executable)
    if args.dry_run:
        body, record = tools["note"].render_note_record(
            envelope, prepared_by=args.prepared_by,
            prepared_at=args.prepared_at, allow_partial=args.allow_partial)
        return {
            "ok": True, "stage": "publish", "dry_run": True,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "record": record, "guarantees": dict(GUARANTEES),
        }, READY_EXIT
    try:
        result = tools["note"].publish_handoff(
            envelope, issue_id=args.issue, prepared_by=args.prepared_by,
            parent_comment_id=args.parent, allow_partial=args.allow_partial,
            prepared_at=args.prepared_at, cli=cli)
    except Exception as exc:
        return _bounded_error("publish", exc), BOUNDED_EXIT
    return result, READY_EXIT


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _add_repo_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=None,
                   help="explicit Context repository root (verified, never guessed)")


def _add_out_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out-dir", default=None,
                   help="artifact output directory (default: fresh temp dir)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="multica-context-handoff deterministic pipeline driver")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="T05 snapshot + T01 PLAN")
    prep.add_argument("--issue", required=True)
    prep.add_argument("--target-role", required=True)
    prep.add_argument("--caller-role", required=True)
    prep.add_argument("--purpose", required=True)
    prep.add_argument("--project-id", default=None)
    prep.add_argument("--project-map", default=None)
    prep.add_argument("--decision-comment", action="append", default=None)
    prep.add_argument("--decision-marker", action="append", default=None)
    prep.add_argument("--options-json", default=None)
    prep.add_argument("--issue-file", default=None,
                      help="offline captured issue get JSON (no CLI calls)")
    prep.add_argument("--parent-file", default=None)
    prep.add_argument("--thread-file", action="append", default=None,
                      metavar="COMMENT_ID=PATH")
    prep.add_argument("--executable", default="multica")
    _add_repo_arg(prep)
    _add_out_arg(prep)

    fin = sub.add_parser("finalize", help="T02 compose validation + T03 FINALIZE")
    fin.add_argument("--plan-file", required=True)
    fin.add_argument("--result-file", required=True,
                     help="proposed frozen semantic_compose_result")
    fin.add_argument("--request-file", required=True)
    fin.add_argument("--repairs-used", type=int, default=0, choices=(0, 1),
                     help="bounded same-PLAN repairs already spent")
    _add_repo_arg(fin)
    _add_out_arg(fin)

    chk = sub.add_parser("selfcheck", help="T06 discovery + T04 SELF_CHECK")
    chk.add_argument("--issue", default=None)
    chk.add_argument("--task-ref", default=None)
    chk.add_argument("--role", default=None)
    chk.add_argument("--request-file", default=None)
    chk.add_argument("--request-from", default=None,
                     help="derive the request from a prepare_handoff_request")
    chk.add_argument("--package-ref", default=None)
    chk.add_argument("--envelope-file", action="append", default=[],
                     help="offline prepare_handoff_result envelope (repeatable)")
    chk.add_argument("--store", default=None)
    chk.add_argument("--executable", default="multica")
    _add_repo_arg(chk)
    _add_out_arg(chk)

    pub = sub.add_parser("publish", help="T06 /note publication (authorized)")
    pub.add_argument("--issue", required=True)
    pub.add_argument("--result-file", required=True)
    pub.add_argument("--prepared-by", required=True)
    pub.add_argument("--prepared-at", default=None)
    pub.add_argument("--parent", default=None)
    pub.add_argument("--allow-partial", action="store_true")
    pub.add_argument("--dry-run", action="store_true")
    pub.add_argument("--executable", default="multica")
    pub.add_argument("--authorize-publish", action="store_true",
                     help="explicit caller authorization for this publication")
    _add_repo_arg(pub)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "prepare":
        payload, code = run_prepare(args)
    elif args.command == "finalize":
        payload, code = run_finalize(args)
    elif args.command == "selfcheck":
        payload, code = run_selfcheck(args)
    else:
        payload, code = run_publish(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
