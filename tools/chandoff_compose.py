#!/usr/bin/env python3
"""T02 — Bounded Semantic Compose protocol (YZT-54).

Defines and validates the auditable Phase B contract over a T01 PLAN_READY
context_plan. This module never calls a model, never writes Canonical Memory,
never re-runs the T01 Finding Gate, and does not implement the shared Skill.

The executor boundary remains caller_agent_llm_via_skill. Tests inject a
fixture seam; production Skill work is out of scope.

Native API objects are reused, not duplicated:
- input plan: frozen context_plan
- executor payload: frozen semantic_compose_result
- this module's validation envelope is a protocol result for later FINALIZE,
  not a new Native API schema.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

EXECUTOR_MODE = "caller_agent_llm_via_skill"
LLM_CALLED = False
CANONICAL_WRITES = 0
MULTICA_RUNTIME_DEPENDENCIES = 0

SEMANTIC_JOBS = (
    "interpret_task_intent",
    "select_task_applicable_rules_from_candidates",
    "select_decision_relevant_facts_from_candidates",
    "compress_checkpoint_slice",
    "perform_case_scenario_match_when_case_search_already_allowed",
    "phrase_visible_conflicts",
    "produce_minimum_sufficient_context",
)

JOB_FROM_FIELD = (
    ("selected_rule_ids", "select_task_applicable_rules_from_candidates"),
    ("selected_fact_ids", "select_decision_relevant_facts_from_candidates"),
    ("selected_case_ids", "perform_case_scenario_match_when_case_search_already_allowed"),
    ("checkpoint_entry_ids", "compress_checkpoint_slice"),
    ("conflict_ids", "phrase_visible_conflicts"),
)

FIELD_TO_CANDIDATES = (
    ("selected_rule_ids", "rules"),
    ("selected_fact_ids", "facts"),
    ("selected_case_ids", "cases"),
    ("checkpoint_entry_ids", "checkpoint_entries"),
    ("conflict_ids", "conflicts"),
)

FORBIDDEN_ATTEMPTS = (
    "expand_scope",
    "invent_authority",
    "promote_verification",
    "change_project_phase",
    "bypass_case_hard_gate",
    "add_memory_not_in_plan",
    "write_canonical",
    "rerun_finding_gate",
)

ATTEMPT_TO_CODE = {
    "expand_scope": "EXPAND_SCOPE",
    "invent_authority": "INVENT_AUTHORITY",
    "promote_verification": "PROMOTE_VERIFICATION",
    "change_project_phase": "CHANGE_PROJECT_PHASE",
    "bypass_case_hard_gate": "CASE_GATE_OVERRIDE",
    "add_memory_not_in_plan": "ADD_MEMORY_NOT_IN_PLAN",
    "write_canonical": "WRITE_CANONICAL",
    "rerun_finding_gate": "RERUN_FINDING_GATE",
}

MAX_ERRORS = 32
RESULT_SCHEMA = "context-handoff/semantic-compose-result.schema.json"
PLAN_SCHEMA = "context-handoff/context-plan.schema.json"


def frozen_contract_supports_compose() -> dict:
    """Prove frozen T00 can host T02 without a Native API amendment."""
    sem = load_schema_file(RESULT_SCHEMA)
    common = load_schema_file("context-handoff/handoff-common.schema.json")
    plan_s = load_schema_file(PLAN_SCHEMA)
    required = set(sem.get("required") or [])
    jobs = tuple(common["$defs"]["semantic_job"]["enum"])
    report = {
        "frozen_semantic_result_schema_present": (
            sem.get("properties", {}).get("kind", {}).get("const")
            == "semantic_compose_result"
        ),
        "frozen_result_requires_plan_id_and_selected_ids": required.issuperset({
            "plan_id", "selected_rule_ids", "selected_fact_ids", "selected_case_ids",
            "checkpoint_entry_ids", "conflict_ids", "semantic_notes",
        }),
        "frozen_result_forbids_additional_properties": (
            sem.get("additionalProperties") is False
        ),
        "frozen_semantic_jobs_vocabulary": jobs == SEMANTIC_JOBS,
        "frozen_plan_lists_semantic_jobs": "semantic_jobs" in (plan_s.get("required") or []),
        "subset_helper_available": callable(
            getattr(chandoff, "validate_semantic_result", None)
        ),
    }
    report["ok"] = all(report.values())
    return report


def done_criteria() -> dict:
    return {
        "output_schema_enforced": True,
        "selected_ids_subset_of_plan": True,
        "scope_cannot_expand": True,
        "case_gate_cannot_be_overridden": True,
        "canonical_writes": 0,
        "multica_runtime_dependencies": 0,
    }


def _validate(schema_name: str, instance, path: str = "$") -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path=path)


def _error(code: str, path: str, message: str) -> dict:
    return {"code": code, "path": path, "message": message}


def _bound(errors: list) -> list:
    ordered = sorted(
        errors,
        key=lambda e: (e.get("code") or "", e.get("path") or "", e.get("message") or ""),
    )
    return ordered[:MAX_ERRORS]


def _ids(candidates: list) -> set:
    return {c.get("id") for c in (candidates or []) if isinstance(c, dict) and c.get("id")}


def subset_result(plan: dict, **overrides) -> dict:
    """Fixture helper: a schema-valid result whose IDs are a subset of PLAN."""
    cands = plan.get("candidates") or {}
    jobs = set(plan.get("semantic_jobs") or [])
    result = {
        "schema_version": "1.1",
        "kind": "semantic_compose_result",
        "plan_id": plan.get("plan_id"),
        "selected_rule_ids": (
            [r["id"] for r in (cands.get("rules") or []) if r.get("id")]
            if "select_task_applicable_rules_from_candidates" in jobs else []
        ),
        "selected_fact_ids": (
            [f["id"] for f in (cands.get("facts") or []) if f.get("id")]
            if "select_decision_relevant_facts_from_candidates" in jobs else []
        ),
        "selected_case_ids": (
            [c["id"] for c in (cands.get("cases") or []) if c.get("id")]
            if "perform_case_scenario_match_when_case_search_already_allowed" in jobs
            else []
        ),
        "checkpoint_entry_ids": (
            [e["id"] for e in (cands.get("checkpoint_entries") or []) if e.get("id")]
            if "compress_checkpoint_slice" in jobs else []
        ),
        "conflict_ids": (
            [c["id"] for c in (cands.get("conflicts") or []) if c.get("id")]
            if "phrase_visible_conflicts" in jobs else []
        ),
        "semantic_notes": ["bounded fixture selection inside PLAN"],
    }
    if "produce_minimum_sufficient_context" in jobs:
        result["context_summary"] = f"minimum sufficient selection from {plan.get('plan_id')}"
    result.update(overrides)
    return result


class FixtureExecutor:
    """Injected seam used by tests. Never a network or model client."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def __call__(self, plan: dict):
        self.calls += 1
        if callable(self.payload):
            return self.payload(plan)
        return copy.deepcopy(self.payload)


def auto_executor() -> FixtureExecutor:
    return FixtureExecutor(lambda plan: subset_result(plan))


def _as_plan_envelope(plan_input) -> dict:
    if not isinstance(plan_input, dict):
        return {"status": None, "plan": None}
    if plan_input.get("kind") == "context_plan":
        return {"status": "PLAN_READY", "plan": plan_input}
    return {
        "status": plan_input.get("status"),
        "plan": plan_input.get("plan"),
    }


def _normalize_proposed(proposed) -> dict:
    empty = {
        "result": None,
        "free_form": None,
        "jobs_executed": None,
        "attempts": [],
        "canonical_writes": 0,
        "finding_gate_rerun": False,
        "scope": None,
        "project_phase": None,
        "invented_rules": [],
        "verification_promotions": [],
    }
    if proposed is None:
        return empty
    if isinstance(proposed, str):
        empty["free_form"] = proposed
        return empty
    if not isinstance(proposed, dict):
        empty["free_form"] = str(proposed)
        return empty
    if proposed.get("kind") == "semantic_compose_result":
        empty["result"] = proposed
        return empty
    out = dict(empty)
    result = proposed.get("result")
    if isinstance(result, dict) and result.get("kind") == "semantic_compose_result":
        out["result"] = result
    elif proposed.get("kind") == "semantic_compose_result":
        out["result"] = proposed
    out["free_form"] = proposed.get("free_form")
    if "jobs_executed" in proposed:
        out["jobs_executed"] = list(proposed.get("jobs_executed") or [])
    attempts = proposed.get("attempts") or []
    out["attempts"] = [a for a in attempts if isinstance(a, str)]
    out["canonical_writes"] = int(proposed.get("canonical_writes") or 0)
    out["finding_gate_rerun"] = bool(proposed.get("finding_gate_rerun"))
    out["scope"] = proposed.get("scope")
    out["project_phase"] = proposed.get("project_phase")
    out["invented_rules"] = list(proposed.get("invented_rules") or [])
    out["verification_promotions"] = list(proposed.get("verification_promotions") or [])
    if out["result"] is None and out["free_form"] is None:
        if any(k in proposed for k in (
            "markdown", "prose", "context_markdown", "body",
        )):
            out["free_form"] = (
                proposed.get("markdown")
                or proposed.get("prose")
                or proposed.get("context_markdown")
                or proposed.get("body")
            )
        elif "selected_rule_ids" not in proposed and "plan_id" not in proposed:
            out["free_form"] = json.dumps(proposed, ensure_ascii=False)
    return out


def _infer_jobs(result: dict, plan: dict) -> list:
    jobs = []
    plan_jobs = set(plan.get("semantic_jobs") or [])
    if not isinstance(result, dict):
        return jobs
    for field, job in JOB_FROM_FIELD:
        if result.get(field) and job not in jobs:
            jobs.append(job)
    if result.get("context_summary") and "produce_minimum_sufficient_context" not in jobs:
        jobs.append("produce_minimum_sufficient_context")
    ordered = [j for j in SEMANTIC_JOBS if j in jobs]
    return ordered


def _collect_job_errors(jobs: list, plan: dict) -> list:
    errors = []
    allowed = set(plan.get("semantic_jobs") or [])
    vocab = set(SEMANTIC_JOBS)
    for i, job in enumerate(jobs):
        path = f"$.jobs_executed[{i}]"
        if job not in vocab:
            errors.append(_error(
                "JOB_NOT_IN_VOCABULARY", path,
                f"semantic job {job!r} is outside the frozen vocabulary"))
        elif job not in allowed:
            errors.append(_error(
                "JOB_NOT_IN_PLAN", path,
                f"semantic job {job!r} is not listed in this PLAN"))
    return errors


def _collect_case_errors(result: dict, plan: dict) -> list:
    errors = []
    if not isinstance(result, dict):
        return errors
    selected = list(result.get("selected_case_ids") or [])
    if not selected:
        return errors
    case_search = (plan.get("case_search") or {}).get("allowed")
    admitted = _ids((plan.get("candidates") or {}).get("cases"))
    plan_jobs = set(plan.get("semantic_jobs") or [])
    case_job = "perform_case_scenario_match_when_case_search_already_allowed"
    if not case_search:
        errors.append(_error(
            "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
            "Case selection is forbidden unless T01 already allowed case search"))
    if case_job not in plan_jobs:
        errors.append(_error(
            "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
            "Case selection requires the PLAN case-scenario job"))
    for sid in selected:
        if sid not in admitted:
            errors.append(_error(
                "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
                f"Case {sid!r} did not pass the T01 hard gate"))
    return errors


def _collect_subset_errors(plan: dict, result: dict) -> list:
    errors = []
    raw = chandoff.validate_semantic_result(plan, result)
    for msg in raw:
        if msg.startswith("plan_id mismatch"):
            errors.append(_error("PLAN_ID_MISMATCH", "$.result.plan_id", msg))
            continue
        field = msg.split(":", 1)[0].strip()
        path = f"$.result.{field}"
        errors.append(_error("SELECTED_ID_NOT_IN_PLAN", path, msg))
        errors.append(_error(
            "ADD_MEMORY_NOT_IN_PLAN", path,
            f"{field} introduces memory absent from the PLAN"))
    return errors


def _collect_attempt_errors(attempt: dict, plan: dict) -> list:
    errors = []
    seen = set()

    def add(code, path, message):
        key = (code, path, message)
        if key in seen:
            return
        seen.add(key)
        errors.append(_error(code, path, message))

    for name in attempt.get("attempts") or []:
        code = ATTEMPT_TO_CODE.get(name)
        if code:
            add(code, "$.attempts", f"forbidden attempt {name!r} is rejected")
        else:
            add("JOB_NOT_IN_VOCABULARY", "$.attempts",
                f"unknown attempt {name!r} is rejected")

    if attempt.get("canonical_writes", 0) > 0:
        add("WRITE_CANONICAL", "$.canonical_writes",
            "semantic compose must not write Canonical Memory")
    if attempt.get("finding_gate_rerun"):
        add("RERUN_FINDING_GATE", "$.finding_gate_rerun",
            "semantic compose must not re-run the T01 Finding Gate")
    if attempt.get("invented_rules"):
        add("INVENT_AUTHORITY", "$.invented_rules",
            "semantic compose must not invent Rule authority")
    if attempt.get("verification_promotions"):
        add("PROMOTE_VERIFICATION", "$.verification_promotions",
            "semantic compose must not promote verification")
    if attempt.get("project_phase") is not None:
        add("CHANGE_PROJECT_PHASE", "$.project_phase",
            "semantic compose must not change project phase")
    claimed_scope = attempt.get("scope")
    if claimed_scope is not None and claimed_scope != plan.get("scope"):
        add("EXPAND_SCOPE", "$.scope",
            "semantic compose must not expand or replace PLAN scope")
    return errors


def _report(*, status: str, plan_id, result, errors: list, jobs_executed: list) -> dict:
    bounded = _bound(errors)
    accepted = status == "ACCEPTED" and not bounded
    return {
        "schema_version": "1.1",
        "kind": "semantic_compose_validation",
        "status": "ACCEPTED" if accepted else "REJECTED",
        "plan_id": plan_id,
        "result": result if accepted else None,
        "errors": bounded,
        "jobs_executed": list(jobs_executed or []),
        "executor_mode": EXECUTOR_MODE,
        "llm_called": False,
        "canonical_writes": 0,
        "scope_expanded": any(e["code"] == "EXPAND_SCOPE" for e in bounded),
        "case_gate_overridden": any(e["code"] == "CASE_GATE_OVERRIDE" for e in bounded),
    }


def compose_semantic(plan_input, proposed=None, *, executor=None) -> dict:
    """Validate a proposed semantic_compose_result against a PLAN_READY plan.

    `executor`, when provided, is an injected seam: callable(plan) -> proposed.
    It is not invoked unless the input is PLAN_READY.
    """
    compat = frozen_contract_supports_compose()
    if not compat["ok"]:
        return _report(
            status="REJECTED", plan_id=None, result=None,
            errors=[_error(
                "T00_CONTRACT_AMENDMENT_REQUIRED", "$",
                "frozen T00 cannot host bounded semantic compose")],
            jobs_executed=[],
        )

    envelope = _as_plan_envelope(plan_input)
    plan = envelope.get("plan")
    if envelope.get("status") != "PLAN_READY" or not isinstance(plan, dict):
        return _report(
            status="REJECTED",
            plan_id=(plan or {}).get("plan_id") if isinstance(plan, dict) else None,
            result=None,
            errors=[_error(
                "T01_NOT_PLAN_READY", "$.status",
                "semantic compose requires a T01 PLAN_READY plan; "
                f"got {envelope.get('status')!r}")],
            jobs_executed=[],
        )

    plan_id = plan.get("plan_id")
    if proposed is None and executor is not None:
        proposed = executor(plan)

    if proposed is None:
        return _report(
            status="REJECTED", plan_id=plan_id, result=None,
            errors=[_error(
                "MISSING_COMPOSE_ATTEMPT", "$",
                "no structured semantic_compose_result was supplied")],
            jobs_executed=[],
        )

    attempt = _normalize_proposed(proposed)
    errors: list = []
    result = attempt.get("result")

    if result is None and attempt.get("free_form"):
        errors.append(_error(
            "FREE_FORM_ONLY", "$",
            "free-form prose is forbidden as the sole compose result"))
        errors.extend(_collect_attempt_errors(attempt, plan))
        return _report(
            status="REJECTED", plan_id=plan_id, result=None,
            errors=errors, jobs_executed=attempt.get("jobs_executed") or [],
        )
    if result is None:
        errors.append(_error(
            "MISSING_COMPOSE_ATTEMPT", "$",
            "compose attempt is not a structured semantic_compose_result"))
        errors.extend(_collect_attempt_errors(attempt, plan))
        return _report(
            status="REJECTED", plan_id=plan_id, result=None,
            errors=errors, jobs_executed=attempt.get("jobs_executed") or [],
        )

    for msg in _validate(RESULT_SCHEMA, result, path="$.result"):
        errors.append(_error("SCHEMA_INVALID", "$.result", msg))

    jobs = attempt.get("jobs_executed")
    if jobs is None:
        jobs = _infer_jobs(result, plan)
    errors.extend(_collect_job_errors(jobs, plan))
    errors.extend(_collect_subset_errors(plan, result))
    errors.extend(_collect_case_errors(result, plan))
    errors.extend(_collect_attempt_errors(attempt, plan))

    return _report(
        status="ACCEPTED", plan_id=plan_id, result=result,
        errors=errors, jobs_executed=jobs,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="T02 bounded semantic compose")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compatibility", help="prove frozen T00 can host T02")
    val = sub.add_parser("validate", help="validate a compose attempt against a PLAN")
    val.add_argument("--plan-file", required=True)
    val.add_argument("--result-file", required=True)
    args = parser.parse_args()
    if args.command == "compatibility":
        report = frozen_contract_supports_compose()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    plan_input = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    proposed = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
    result = compose_semantic(plan_input, proposed)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ACCEPTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
