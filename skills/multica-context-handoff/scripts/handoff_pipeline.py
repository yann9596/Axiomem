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

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import artifact_gate  # noqa: E402

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
        import chandoff_findings_source  # noqa: F401
        import chandoff_note  # noqa: F401
        import chandoff_plan  # noqa: F401
        import chandoff_selfcheck  # noqa: F401
        _MODULES.update({
            "root": root,
            "adapter": sys.modules["chandoff_adapter"],
            "compose": sys.modules["chandoff_compose"],
            "finalize": sys.modules["chandoff_finalize"],
            "findings_source": sys.modules["chandoff_findings_source"],
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


def _artifact_binding(args) -> dict:
    return artifact_gate.binding_from_args(args)


def _artifact_inputs(args, *, target_role: str, package: dict | None = None):
    """Resolve caller-supplied / previously exported artifact requirements.

    Empty or absent declarations skip the gate (legacy T00-T07 path). A
    declared non-empty set without a store fails closed — versions are
    never guessed.
    """
    bind = _artifact_binding(args)
    extracted = artifact_gate.extract_dependency_records(package)
    requirements = None
    review_level = bind["review_level"]
    file_role = None
    if bind["requirements_file"]:
        doc = artifact_gate.load_requirements_doc(bind["requirements_file"])
        requirements = list(doc.get("requirements") or [])
        review_level = review_level or doc.get("review_level")
        file_role = doc.get("target_role")
    elif extracted["requirements"]:
        requirements = list(extracted["requirements"])
    declared = bool(requirements)
    if not declared:
        return None
    if not bind["store_file"]:
        raise PipelineError(
            artifact_gate.STORE_REQUIRED,
            "declared artifact requirements cannot be checked without an "
            "exact envelope store; versions are never guessed",
            requirements_file=bind["requirements_file"] or "")
    return {
        "store_file": bind["store_file"],
        "requirements": requirements,
        "review_level": review_level,
        "target_role": target_role or file_role,
        "previous_digest": extracted.get("digest"),
        "previous_requirements": extracted.get("requirements") or [],
    }


# ---------------------------------------------------------------------------
# Findings source binding: production stages require a verified source
# binding plus the prior boundary observation. There is no implicit source
# flag default and no fabricated empty list.
# ---------------------------------------------------------------------------

def _findings_binding_files(args, stage: str) -> str:
    binding = getattr(args, "findings_source_binding_file", None)
    if not binding:
        raise PipelineError(
            "findings_source_unbound",
            f"{stage} requires --findings-source-binding-file; a missing "
            "Findings binding is refused (never an empty store)")
    if getattr(args, "findings_authority_capture_only", False):
        raise PipelineError(
            "findings_source_unbound",
            f"{stage} refuses capture-only authority; production stages "
            "re-read the live comment through authenticated CLI")
    return binding


def _authority_resolver(args, tools, stage: str):
    cfs = tools["findings_source"]
    cli = getattr(args, "findings_authority_cli", None)
    if cli is None:
        cli = tools["adapter"].MulticaCli(
            executable=getattr(args, "executable", "multica"))
    return cfs.AuthenticatedCommentResolver(cli)


def _load_findings_source(args, tools, *, project_id, stage: str):
    binding = _findings_binding_files(args, stage)
    cfs = tools["findings_source"]
    resolver = _authority_resolver(args, tools, stage)
    try:
        source = cfs.source_from_binding_file(
            binding, resolver=resolver, project_id=project_id,
            expected_commit=getattr(args, "findings_source_expect_commit", None),
            expected_adapter_digest=getattr(
                args, "findings_source_expect_adapter_digest", None))
    except cfs.FindingsSourceRefusal as exc:
        raise PipelineError(exc.code, exc.message, **exc.details) from None
    if not getattr(source, "is_production", False):
        raise PipelineError(
            "findings_source_unbound",
            f"{stage} requires authenticated CLI authority; capture-only "
            "or simulation sources cannot publish or dispatch")
    return source


def _findings_evidence(args, tools, stage: str) -> dict:
    path = getattr(args, "findings_evidence_file", None)
    if not path:
        raise PipelineError(
            "findings_source_unbound",
            f"{stage} requires --findings-evidence-file (the prior boundary "
            "observation); the source join is never guessed")
    cfs = tools["findings_source"]
    try:
        return cfs.observation_from_file(path)
    except cfs.FindingsSourceRefusal as exc:
        raise PipelineError(exc.code, exc.message, **exc.details) from None


def _findings_report(observation: dict | None) -> dict | None:
    if not observation:
        return None
    return {
        "source_id": observation.get("source_id"),
        "boundary": observation.get("boundary"),
        "resolved_root": observation.get("resolved_root"),
        "snapshot_digest": observation.get("snapshot_digest"),
        "total_records": observation.get("total_records"),
        "open_count": observation.get("open_count"),
        "open_ids": list(observation.get("open_ids") or []),
        "simulation": bool(observation.get("simulation")),
        "binding_digest": observation.get("binding_digest"),
    }


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

    try:
        source = _load_findings_source(
            args, tools, project_id=(request.get("project") or {}).get("project_id"),
            stage="prepare")
    except PipelineError as exc:
        payload = _bounded_error("plan", exc)
        payload["status"] = "findings_source_unbound"
        payload["artifacts"] = {"dir": str(out)}
        return payload, BOUNDED_EXIT

    plan_fn = plan_fn or tools["plan"].prepare_handoff_plan
    try:
        plan_result = plan_fn(
            request, findings_source=source,
            source_observer_run_id=getattr(args, "observer_run_id", None))
    except Exception as exc:
        return _bounded_error("plan", exc), BOUNDED_EXIT
    _json_write(out / "plan-envelope.json", plan_result)
    observation = plan_result.get("findings_observation")
    if observation is not None:
        _json_write(out / "findings-observation.json", observation)

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
            "findings_source": _findings_report(observation),
            "findings_source_refusal": plan_result.get("findings_source_refusal"),
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
        "findings_source": _findings_report(observation),
        "artifacts": {
            "dir": str(out),
            "request": str(out / "request.json"),
            "plan_envelope": str(out / "plan-envelope.json"),
            "findings_observation": (str(out / "findings-observation.json")
                                     if observation is not None else None),
        },
        "next": ("compose a frozen semantic_compose_result over the PLAN "
                 "candidates only, then run the finalize command"),
        "guarantees": dict(GUARANTEES),
    }, READY_EXIT


# ---------------------------------------------------------------------------
# Compose validation (T02) + FINALIZE (T03), with the one-repair bound.
# ---------------------------------------------------------------------------

def _digest_doc(doc) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(doc, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _findings_stage_error(stage: str, exc) -> tuple:
    if hasattr(exc, "as_dict"):
        err = exc.as_dict()
        payload = {"ok": False, "stage": stage, "error": err,
                   "findings_source_refusal": err,
                   "guarantees": dict(GUARANTEES)}
        return payload, BOUNDED_EXIT
    payload = _bounded_error(stage, exc)
    if getattr(exc, "code", None) in (
            "findings_source_missing", "findings_source_unreadable",
            "findings_source_invalid", "findings_source_ambiguous",
            "findings_source_unbound", "findings_source_changed"):
        payload["findings_source_refusal"] = exc.envelope()
    return payload, BOUNDED_EXIT


def run_finalize(args, *, compose_fn: Callable | None = None,
                 finalize_fn: Callable | None = None) -> tuple[dict, int]:
    root = resolve_repo_root(args.repo)
    tools = _tools(root)
    out = _out_dir(args)
    plan_input = _json_read(args.plan_file)
    proposed = _json_read(args.result_file)
    request = _json_read(args.request_file)

    try:
        source = _load_findings_source(
            args, tools, project_id=(request.get("project") or {}).get("project_id"),
            stage="finalize")
        evidence = _findings_evidence(args, tools, "finalize")
        pre = source.read(
            boundary="FINALIZE",
            task_ref=request.get("task_ref"),
            role=(request.get("target") or {}).get("role"),
            request_digest=tools["plan"].request_digest(request),
            task_fingerprint=tools["plan"].fingerprint_from_request(request),
            prior_observation=evidence)
    except Exception as exc:  # bounded refusal on the source boundary
        return _findings_stage_error("findings", exc)
    _json_write(out / "findings-observation-finalize.json", pre["observation"])

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

    status = result.get("status")
    artifact_view = None
    try:
        inputs = _artifact_inputs(
            args, target_role=result.get("role") or request.get("target", {}).get("role"),
            package=(result.get("package") or {}))
    except PipelineError as exc:
        _json_write(out / "result.t03.json", result)
        payload = _bounded_error("artifact", exc)
        payload["status"] = status
        payload["t03_status"] = status
        payload["publish"] = {"publishable": False, "normal_ready": False,
                              "blocked_by": "ARTIFACT_NOT_READY"}
        payload["artifacts"] = {"dir": str(out),
                                "compose_validation": str(out / "compose-validation.json")}
        return payload, BOUNDED_EXIT
    if inputs is not None:
        cartifact = artifact_gate.load_cartifact(root)
        store = artifact_gate.load_store(cartifact, inputs["store_file"])
        artifact_view = artifact_gate.apply_finalize_gate(
            cartifact, result, store=store,
            requirements=inputs["requirements"],
            target_role=inputs["target_role"],
            review_level=inputs.get("review_level"))
        _json_write(out / "artifact-ready-check.json", artifact_view)

    _json_write(out / "result.json", result)

    try:
        confirmed = source.read(
            boundary="FINALIZE_CONFIRM",
            task_ref=result.get("task_ref"),
            role=result.get("role"),
            request_digest=tools["plan"].request_digest(request),
            task_fingerprint=(result.get("built_from") or {}).get("task_fingerprint"),
            envelope_digest=_digest_doc(result),
            prior_observation=pre["observation"])
    except Exception as exc:
        payload, code = _findings_stage_error("findings", exc)
        payload["status"] = status
        payload["t03_status"] = status
        payload["publish"] = {"publishable": False, "normal_ready": False,
                              "blocked_by": "FINDINGS_SOURCE_CHANGED"}
        payload["artifacts"] = {"dir": str(out),
                                "result": str(out / "result.json")}
        return payload, code
    _json_write(out / "findings-observation.json", confirmed["observation"])
    findings_block = _findings_report(confirmed["observation"])

    package = result.get("package") or {}
    gaps = sorted(set((package.get("blocked_by") or []) +
                      [c.get("id") for c in (package.get("open_conflicts") or [])
                       if c.get("id")]))
    artifact_blocks = bool(
        artifact_view and artifact_view.get("status") != "ARTIFACT_READY")
    if status == "READY" and not artifact_blocks:
        publish = {"publishable": True,
                   "requires_authorization": "--authorize-publish",
                   "normal_ready": True}
    elif status == "PARTIAL" and not artifact_blocks:
        publish = {"publishable": "only_with_explicit_caller_authorization",
                   "requires_authorization": ["--authorize-publish", "--allow-partial"],
                   "normal_ready": False,
                   "gaps_preserved": True}
    elif artifact_blocks:
        publish = {"publishable": False, "normal_ready": False,
                   "blocked_by": "ARTIFACT_NOT_READY"}
    else:
        publish = {"publishable": False, "normal_ready": False,
                   "never_published": True}
    payload = {
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
        "findings_source": findings_block,
        "artifacts": {
            "dir": str(out),
            "result": str(out / "result.json"),
            "compose_validation": str(out / "compose-validation.json"),
            "findings_observation": str(out / "findings-observation.json"),
        },
        "guarantees": dict(GUARANTEES),
    }
    if artifact_view is not None:
        payload["artifact_status"] = artifact_view.get("status")
        payload["artifact_ready"] = {
            "status": artifact_view.get("status"),
            "blocks_handoff": artifact_view.get("blocks_handoff"),
            "failures": artifact_view.get("failures") or [],
            "dependency_digest": artifact_view.get("dependency_digest")
            or artifact_view.get("exported_digest"),
            "correction_owner": artifact_view.get("correction_owner"),
            "route": artifact_view.get("route"),
            "checks": artifact_view.get("checks") or {},
        }
        payload["artifacts"]["artifact_ready_check"] = str(
            out / "artifact-ready-check.json")
    if artifact_blocks:
        payload["ok"] = False
        return payload, BOUNDED_EXIT
    return payload, {"READY": READY_EXIT, "PARTIAL": BOUNDED_EXIT,
                     "BLOCKED": BLOCKED_EXIT}[status]


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

    if finding_store is not None:
        # Explicit simulation seam: the caller injected an in-memory store and
        # the payload records that mode; the production binding is not read.
        source, evidence = None, None
        findings_mode = "legacy_injected_store"
    else:
        try:
            source = _load_findings_source(args, tools, project_id=None,
                                           stage="selfcheck")
            evidence = _findings_evidence(args, tools, "selfcheck")
        except Exception as exc:
            return _findings_stage_error("findings", exc)
        findings_mode = "bound"

    selfcheck_fn = selfcheck_fn or tools["selfcheck"].self_check_with_trace
    trace = selfcheck_fn(request, packages=packages or None,
                         store_dir=args.store, finding_store=finding_store,
                         findings_source=source,
                         findings_prior_observation=evidence,
                         source_observer_run_id=getattr(args, "observer_run_id",
                                                        None))
    result = trace["result"]
    _json_write(out / "self-check-request.json", request)
    _json_write(out / "self-check-result.json", result)
    if trace.get("findings_observation") is not None:
        _json_write(out / "findings-observation.json",
                    trace["findings_observation"])

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
        "findings_source": _findings_report(trace.get("findings_observation")),
        "findings_source_refusal": trace.get("findings_source_refusal"),
        "findings_source_mode": findings_mode,
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

    candidate = packages[-1] if packages else None
    try:
        inputs = _artifact_inputs(
            args, target_role=role,
            package=(candidate.get("package") if candidate else None))
    except PipelineError as exc:
        overlay = _bounded_error("artifact", exc)
        overlay["status"] = "REFRESH_REQUIRED"
        overlay["action"] = "REFRESH"
        overlay["reasons"] = list(payload.get("reasons") or []) + [
            artifact_gate.PACKAGE_STALE]
        overlay["consequential_work"] = "stopped_until_refreshed_ready"
        overlay["package_id"] = payload.get("package_id")
        overlay["task_ref"] = task_ref
        overlay["current_role"] = role
        overlay["refresh"] = {
            "instruction": ("run PREPARE_HANDOFF for the same task and the "
                            "current role; consequential work stays stopped "
                            "until the refreshed result is READY"),
            "task_ref": task_ref,
            "target_role": role,
        }
        overlay["guarantees"] = dict(GUARANTEES)
        return overlay, BOUNDED_EXIT
    if inputs is not None:
        cartifact = artifact_gate.load_cartifact(root)
        store = artifact_gate.load_store(cartifact, inputs["store_file"])
        current_reqs = inputs["requirements"]
        previous_reqs = inputs.get("previous_requirements") or current_reqs
        previous_digest = inputs.get("previous_digest")
        # Caller-supplied current set wins; otherwise re-resolve live versions.
        supplied = bool(_artifact_binding(args)["requirements_file"])
        freshness = artifact_gate.evaluate_freshness(
            cartifact, store,
            previous_requirements=previous_reqs,
            previous_digest=previous_digest,
            current_requirements=current_reqs if supplied else None,
            target_role=role,
            review_level=inputs.get("review_level"))
        _json_write(out / "artifact-freshness.json", {
            "dependency_changed": freshness["dependency_changed"],
            "previous_digest": freshness["previous_digest"],
            "current_digest": freshness["current_digest"],
            "stale": freshness["stale"],
            "ready_current": freshness["ready_current"],
            "ready_previous": freshness["ready_previous"],
        })
        payload["artifacts"]["artifact_freshness"] = str(
            out / "artifact-freshness.json")
        payload = artifact_gate.overlay_selfcheck(payload, freshness)
        status = payload["status"]

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

    try:
        inputs = _artifact_inputs(
            args, target_role=envelope.get("role"),
            package=(envelope.get("package") or {}))
    except PipelineError as exc:
        payload = _bounded_error("publish", exc)
        payload["error"]["code"] = exc.code
        return payload, BOUNDED_EXIT
    if inputs is not None:
        cartifact = artifact_gate.load_cartifact(root)
        store = artifact_gate.load_store(cartifact, inputs["store_file"])
        supplied = bool(_artifact_binding(args)["requirements_file"])
        freshness = artifact_gate.evaluate_freshness(
            cartifact, store,
            previous_requirements=inputs.get("previous_requirements")
            or inputs["requirements"],
            previous_digest=inputs.get("previous_digest"),
            current_requirements=inputs["requirements"] if supplied else None,
            target_role=inputs["target_role"],
            review_level=inputs.get("review_level"))
        ready = freshness["ready_current"]
        if freshness["stale"] or ready.get("status") != "ARTIFACT_READY":
            return {
                "ok": False, "stage": "publish",
                "error": {
                    "code": "artifact_not_ready",
                    "message": (
                        "publication is refused: artifact readiness failed "
                        "or the accepted dependency set is no longer fresh"
                    ),
                    "artifact_ready": artifact_gate.public_ready_view(ready),
                    "dependency_changed": freshness["dependency_changed"],
                },
                "guarantees": dict(GUARANTEES),
            }, BOUNDED_EXIT

    try:
        source = _load_findings_source(
            args, tools,
            project_id=((envelope.get("package") or {}).get("scope") or {})
            .get("project_id"),
            stage="publish")
        evidence = _findings_evidence(args, tools, "publish")
        pre_publish = source.read(
            boundary="PRE_PUBLISH",
            task_ref=envelope.get("task_ref"), role=envelope.get("role"),
            envelope_digest=_digest_doc(envelope),
            prior_observation=evidence)
    except Exception as exc:
        return _findings_stage_error("findings", exc)
    findings_block = _findings_report(pre_publish["observation"])

    cli = note_cli_factory() if note_cli_factory \
        else tools["note"].NoteCli(executable=args.executable)
    if args.dry_run:
        body, record = tools["note"].render_note_record(
            envelope, prepared_by=args.prepared_by,
            prepared_at=args.prepared_at, allow_partial=args.allow_partial)
        return {
            "ok": True, "stage": "publish", "dry_run": True,
            "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "record": record,
            "findings_source": findings_block,
            "guarantees": dict(GUARANTEES),
        }, READY_EXIT
    try:
        result = tools["note"].publish_handoff(
            envelope, issue_id=args.issue, prepared_by=args.prepared_by,
            parent_comment_id=args.parent, allow_partial=args.allow_partial,
            prepared_at=args.prepared_at, cli=cli)
    except Exception as exc:
        return _bounded_error("publish", exc), BOUNDED_EXIT
    try:
        confirmed = source.read(
            boundary="PUBLICATION_CONFIRMATION",
            task_ref=envelope.get("task_ref"), role=envelope.get("role"),
            envelope_digest=_digest_doc(envelope),
            prior_observation=pre_publish["observation"])
    except Exception as exc:
        return {
            "ok": False,
            "stage": "publish",
            "error": {
                "code": "findings_source_changed_after_send",
                "message": ("the outstanding single publication was sent; the "
                            "post-send Findings confirmation failed. The "
                            "receipt is preserved and no second note is ever "
                            "issued"),
            },
            "publication": result,
            "findings_source_pre_publish": findings_block,
            "findings_source_refusal": (exc.as_dict()
                                        if hasattr(exc, "as_dict")
                                        else str(exc)[:240]),
            "guarantees": dict(GUARANTEES),
        }, BOUNDED_EXIT
    out = dict(result) if isinstance(result, dict) else {"result": result}
    out["findings_source"] = _findings_report(confirmed["observation"])
    return out, READY_EXIT


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _add_repo_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=None,
                   help="explicit Context repository root (verified, never guessed)")


def _add_out_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out-dir", default=None,
                   help="artifact output directory (default: fresh temp dir)")


def _add_artifact_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--artifact-store-file", default=None,
                   help="caller-supplied Artifact Contract envelope store JSON")
    p.add_argument("--artifact-requirements-file", default=None,
                   help="exact required artifact identity/version set JSON")
    p.add_argument("--artifact-review-level", default=None,
                   choices=("R0", "R1", "R2"),
                   help="optional R0/R1/R2 for the Artifact Contract ready-check")


def _add_findings_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--findings-source-binding-file", default=None,
                   help="verified findings-source-binding/1 JSON "
                        "(required on every production stage)")
    p.add_argument("--findings-authority-file", default=None,
                   help="unused local capture (not production authority; "
                        "each stage re-reads the live comment via CLI)")
    p.add_argument("--findings-authority-capture-only", action="store_true",
                   default=False,
                   help="explicitly request capture-only authority "
                        "(refused on every production pipeline stage)")
    p.add_argument("--findings-evidence-file", default=None,
                   help="prior boundary findings-source-observation/1 JSON "
                        "(required for finalize/selfcheck/publish)")
    p.add_argument("--findings-source-expect-commit", default=None,
                   help="optional full commit the binding runtime pin must "
                        "match")
    p.add_argument("--findings-source-expect-adapter-digest", default=None,
                   help="optional sha256 the binding runtime adapter pin "
                        "must match")
    p.add_argument("--observer-run-id", default=None,
                   help="observer run identity recorded in the observation")


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
    _add_artifact_args(prep)
    _add_findings_args(prep)

    fin = sub.add_parser("finalize", help="T02 compose validation + T03 FINALIZE")
    fin.add_argument("--plan-file", required=True)
    fin.add_argument("--result-file", required=True,
                     help="proposed frozen semantic_compose_result")
    fin.add_argument("--request-file", required=True)
    fin.add_argument("--repairs-used", type=int, default=0, choices=(0, 1),
                     help="bounded same-PLAN repairs already spent")
    _add_repo_arg(fin)
    _add_out_arg(fin)
    _add_artifact_args(fin)
    _add_findings_args(fin)

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
    _add_artifact_args(chk)
    _add_findings_args(chk)

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
    _add_artifact_args(pub)
    _add_findings_args(pub)

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
