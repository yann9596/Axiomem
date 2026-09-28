#!/usr/bin/env python3
"""T03 — deterministic prepare_handoff FINALIZE (YZT-55).

Pure Phase C boundary: consume a T01 PLAN_READY plan plus an ACCEPTED T02
semantic_compose_result, re-verify every hard invariant, assemble the existing
V1.1 Task Context Package, and emit a frozen prepare_handoff_result.

Never calls a model. Never writes Canonical Memory or Checkpoints. Never
re-runs the T01 Finding Gate or T02 semantic selection. Free-text
context_summary / semantic_notes are never a selection channel.

FIND-WIMG-HO00-000001 is closed here: package rules[].ref / current_facts[].ref
/ cases[].ref use only the frozen evidence-ref ref_string grammar. The V1.1
cbuild.build_package path is left unchanged so Gate C historical replay
expectations stay valid.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cauthority import evaluate_rule_authority, load_authority_evidence  # noqa: E402
from cbuild import anchor_digest  # noqa: E402
from cdata import load_all_docs, load_role_profile  # noqa: E402
from chandoff import (  # noqa: E402
    canonical_json, compute_built_from, split_checkpoint_candidate_id,
    validate_semantic_result,
)
from crole import kind_limit, policy_of  # noqa: E402
from cutil import TEAM, now_iso, object_scope, scope_allows  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402
from yaml_mini import parse_yaml  # noqa: E402

LLM_CALLED = False
CANONICAL_WRITES = 0
MULTICA_RUNTIME_DEPENDENCIES = 0

REF_RE = re.compile(r"^(multica|adr|doc|repo|registry|project|git)://\S+$")
PSEUDO_SCHEME_RE = re.compile(r"^(rule|fact|case):", re.I)

ADMITTED_VERIFICATION = {"verified", "partially_verified"}
ACTIVE_STATUS = "active"
ILLEGAL_RULE_STATUS = {"superseded", "revoked"}
ILLEGAL_FACT_STATUS = {"superseded"}

PACKAGE_SCHEMA = "context-package.schema.json"
RESULT_SCHEMA = "context-handoff/prepare-handoff-result.schema.json"
PLAN_SCHEMA = "context-handoff/context-plan.schema.json"
COMPOSE_SCHEMA = "context-handoff/semantic-compose-result.schema.json"
REQUEST_SCHEMA = "context-handoff/prepare-handoff-request.schema.json"

MAX_ERRORS = 32
FIELD_TO_CANDIDATES = (
    ("selected_rule_ids", "rules"),
    ("selected_fact_ids", "facts"),
    ("selected_case_ids", "cases"),
    ("checkpoint_entry_ids", "checkpoint_entries"),
    ("conflict_ids", "conflicts"),
)

FINDING_HO00_000001 = {
    "finding_id": "FIND-WIMG-HO00-000001",
    "status": "processed",
    "disposition": "absorbed_by_existing",
    "disposition_note": (
        "T03 FINALIZE package assembler emits grammar-valid "
        "repo://multica-memory/<canonical-path> refs for rules/facts/cases. "
        "Frozen evidence-ref ref_string grammar is unchanged. "
        "cbuild.build_package historical refs stay as-is for Gate C replay."
    ),
    "canonical_write": False,
    "llm_called": False,
}


def frozen_contract_supports_finalize() -> dict:
    """Prove frozen T00 can host T03 without a Native API amendment."""
    result = load_schema_file(RESULT_SCHEMA)
    common = load_schema_file("context-handoff/handoff-common.schema.json")
    pkg = load_schema_file(PACKAGE_SCHEMA)
    evidence = load_schema_file("evidence-ref.schema.json")
    required = set(result.get("required") or [])
    status = tuple(common["$defs"]["prepare_handoff_status"]["enum"])
    built = set(common["$defs"]["built_from"]["required"])
    ref_pattern = evidence["$defs"]["ref_string"]["pattern"]
    report = {
        "frozen_prepare_handoff_result_schema_present": (
            result.get("properties", {}).get("kind", {}).get("const")
            == "prepare_handoff_result"
        ),
        "frozen_status_enum_exact": status == ("READY", "PARTIAL", "BLOCKED"),
        "frozen_package_reuses_context_package_schema": (
            "context-package.schema.json"
            in (result.get("properties", {}).get("package", {}).get("$ref") or "")
        ),
        "frozen_built_from_required": built == {
            "task_fingerprint", "memory_revision", "registry_revision",
            "role_profile_revision",
        },
        "frozen_result_requires_gate_fields": required.issuperset({
            "status", "package_id", "task_ref", "role", "built_from",
            "package", "escalation",
        }),
        "frozen_ref_grammar_unchanged": (
            ref_pattern == r"^(multica|adr|doc|repo|registry|project|git)://\S+$"
        ),
        "frozen_package_refs_use_ref_string": (
            pkg["properties"]["rules"]["items"]["properties"]["ref"].get("$ref", "")
            .endswith("ref_string")
        ),
        "subset_helper_available": callable(
            getattr(sys.modules.get("chandoff"), "validate_semantic_result", None)
            or validate_semantic_result
        ),
    }
    report["ok"] = all(report.values())
    return report


def done_criteria() -> dict:
    return {
        "invalid_rule_authority": 0,
        "hidden_conflict": 0,
        "package_schema_valid": True,
        "result_schema_valid": True,
        "task_and_role_bound": True,
        "built_from_present": True,
        "package_ref_grammar_valid": True,
        "pseudo_scheme_refs": 0,
        "final_gate_deterministic": True,
        "canonical_writes": 0,
        "llm_calls": 0,
    }


def finding_ho00_000001_closure() -> dict:
    """Processed/disposition record. Does not write Canonical Memory."""
    return copy.deepcopy(FINDING_HO00_000001)


def canonical_object_ref(doc: dict) -> str:
    """Traceable package ref accepted by the frozen ref_string grammar.

    Never emits rule:/fact:/case: pseudo-schemes. Points at the canonical
    file in this memory repo so the object is uniquely locatable.
    """
    existing = doc.get("ref")
    if isinstance(existing, str) and REF_RE.match(existing) and not PSEUDO_SCHEME_RE.match(existing):
        return existing
    oid = doc.get("id") or "unknown"
    kind = doc.get("_kind") or doc.get("kind") or ""
    scope = object_scope(doc)
    if kind == "rule" and scope.get("type") == "team":
        rel = f"team-context/rules/{oid}.yaml"
    elif kind == "rule":
        rel = f"project-context/{scope.get('project_id') or 'unknown'}/rules/{oid}.yaml"
    elif kind in ("current_fact", "fact"):
        rel = f"project-context/{scope.get('project_id') or 'unknown'}/facts/{oid}.yaml"
    elif kind == "case":
        rel = f"project-context/{scope.get('project_id') or 'unknown'}/cases/{oid}.yaml"
    else:
        rel = f"memory/{oid}"
    return f"repo://multica-memory/{rel}"


def package_refs(pkg: dict) -> list:
    return (
        [r.get("ref") for r in (pkg.get("rules") or [])]
        + [f.get("ref") for f in (pkg.get("current_facts") or [])]
        + [c.get("ref") for c in (pkg.get("cases") or [])]
    )


def pseudo_scheme_refs(pkg: dict) -> list:
    return [r for r in package_refs(pkg) if isinstance(r, str) and PSEUDO_SCHEME_RE.match(r)]


def grammar_invalid_refs(pkg: dict) -> list:
    return [r for r in package_refs(pkg) if not (isinstance(r, str) and REF_RE.match(r))]


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


def _index_docs(docs: list) -> dict:
    out = {}
    for doc in docs or []:
        oid = doc.get("id")
        if oid:
            out[oid] = doc
    return out


def _as_plan_envelope(plan_input) -> dict:
    if not isinstance(plan_input, dict):
        return {"status": None, "plan": None}
    if plan_input.get("kind") == "context_plan":
        return {"status": "PLAN_READY", "plan": plan_input}
    return {
        "status": plan_input.get("status"),
        "plan": plan_input.get("plan"),
    }


def _as_compose_envelope(compose_input) -> dict:
    if not isinstance(compose_input, dict):
        return {"status": None, "result": None, "plan_id": None}
    if compose_input.get("kind") == "semantic_compose_result":
        return {"status": None, "result": compose_input, "plan_id": compose_input.get("plan_id")}
    return {
        "status": compose_input.get("status"),
        "result": compose_input.get("result"),
        "plan_id": compose_input.get("plan_id"),
    }


def _load_registry(registry: dict | None) -> dict:
    if registry is not None:
        return registry
    return parse_yaml((TEAM / "registry" / "projects.yaml").read_text(encoding="utf-8"))


def _phase_of(registry: dict, project_id: str | None) -> str | None:
    if not project_id:
        return None
    for p in registry.get("projects") or []:
        if p["id"] == project_id:
            return p.get("phase")
    return None


def _allowed_project_ids(task_scope: dict) -> set:
    if task_scope.get("type") == "project":
        return {task_scope.get("project_id")}
    if task_scope.get("type") == "cross_project":
        return set(task_scope.get("projects") or [])
    return set()


def _pollutes(doc: dict, task_scope: dict) -> bool:
    scope = object_scope(doc)
    if scope.get("type") == "team":
        return False
    allowed = _allowed_project_ids(task_scope)
    if scope.get("type") == "project":
        return scope.get("project_id") not in allowed
    if scope.get("type") == "cross_project":
        return not set(scope.get("projects") or []).issubset(allowed)
    return True


def _binding_errors(request: dict, plan: dict, result: dict) -> list:
    errors = []
    if request.get("task_ref") != plan.get("task_ref"):
        errors.append(_error(
            "TASK_REF_MISMATCH", "$.request.task_ref",
            f"request task_ref {request.get('task_ref')!r} != plan {plan.get('task_ref')!r}"))
    if result.get("plan_id") != plan.get("plan_id"):
        errors.append(_error(
            "PLAN_ID_MISMATCH", "$.result.plan_id",
            f"compose plan_id {result.get('plan_id')!r} != plan {plan.get('plan_id')!r}"))
    role = (request.get("target") or {}).get("role")
    if role != plan.get("role"):
        errors.append(_error(
            "ROLE_MISMATCH", "$.request.target.role",
            f"request role {role!r} != plan {plan.get('role')!r}"))
    pid = (request.get("project") or {}).get("project_id")
    ps = plan.get("scope") or {}
    if ps.get("type") == "project" and pid != ps.get("project_id"):
        errors.append(_error(
            "SCOPE_MISMATCH", "$.request.project.project_id",
            f"request project {pid!r} != plan scope project {ps.get('project_id')!r}"))
    elif ps.get("type") == "cross_project" and pid not in (ps.get("projects") or []):
        errors.append(_error(
            "SCOPE_MISMATCH", "$.request.project.project_id",
            f"request project {pid!r} is not in plan cross-project allow-list"))
    return errors


def _subset_errors(plan: dict, result: dict) -> list:
    errors = []
    raw = validate_semantic_result(plan, result)
    for msg in raw:
        if msg.startswith("plan_id mismatch"):
            errors.append(_error("PLAN_ID_MISMATCH", "$.result.plan_id", msg))
            continue
        field = msg.split(":", 1)[0].strip()
        path = f"$.result.{field}"
        errors.append(_error("SELECTED_ID_NOT_IN_PLAN", path, msg))
    return errors


def _case_errors(plan: dict, result: dict, by_id: dict) -> list:
    errors = []
    selected = list(result.get("selected_case_ids") or [])
    if not selected:
        return errors
    allowed = bool((plan.get("case_search") or {}).get("allowed"))
    admitted = _ids((plan.get("candidates") or {}).get("cases"))
    jobs = set(plan.get("semantic_jobs") or [])
    case_job = "perform_case_scenario_match_when_case_search_already_allowed"
    if not allowed:
        errors.append(_error(
            "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
            "Case selection is forbidden unless T01 already allowed case search"))
    if case_job not in jobs:
        errors.append(_error(
            "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
            "Case selection requires the PLAN case-scenario job"))
    for sid in selected:
        if sid not in admitted:
            errors.append(_error(
                "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
                f"Case {sid!r} did not pass the T01 hard gate"))
        doc = by_id.get(sid)
        if doc is not None and doc.get("status") != ACTIVE_STATUS:
            errors.append(_error(
                "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
                f"Case {sid!r} is not in a legal lifecycle state"))
        if doc is not None and doc.get("verification") not in ADMITTED_VERIFICATION:
            errors.append(_error(
                "CASE_GATE_OVERRIDE", "$.result.selected_case_ids",
                f"Case {sid!r} is not in a legal verification state"))
    return errors


def _hidden_conflict_errors(plan: dict, result: dict) -> list:
    plan_conflicts = _ids((plan.get("candidates") or {}).get("conflicts"))
    selected = set(result.get("conflict_ids") or [])
    hidden = sorted(plan_conflicts - selected)
    extra = sorted(selected - plan_conflicts)
    errors = []
    if hidden:
        errors.append(_error(
            "HIDDEN_CONFLICT", "$.result.conflict_ids",
            "PLAN conflicts omitted from compose selection: " + ", ".join(hidden)))
    for sid in extra:
        errors.append(_error(
            "SELECTED_ID_NOT_IN_PLAN", "$.result.conflict_ids",
            f"conflict_ids: {sid!r} is not a PLAN candidate under candidates.conflicts"))
    return errors


def _object_state_errors(plan: dict, result: dict, by_id: dict, evidence) -> list:
    errors = []
    task_scope = plan.get("scope") or {}
    admitted_rules = _ids((plan.get("candidates") or {}).get("rules"))
    admitted_facts = _ids((plan.get("candidates") or {}).get("facts"))
    admitted_cases = _ids((plan.get("candidates") or {}).get("cases"))
    for sid in result.get("selected_rule_ids") or []:
        if sid not in admitted_rules:
            continue
        doc = by_id.get(sid)
        path = "$.result.selected_rule_ids"
        if doc is None:
            errors.append(_error("OBJECT_UNAVAILABLE", path, f"Rule {sid!r} is not loadable"))
            continue
        if _pollutes(doc, task_scope) or not scope_allows(doc, task_scope):
            errors.append(_error(
                "CROSS_SCOPE", path,
                f"Rule {sid!r} is outside PLAN scope"))
        status = doc.get("status", ACTIVE_STATUS)
        if status != ACTIVE_STATUS or status in ILLEGAL_RULE_STATUS:
            errors.append(_error(
                "ILLEGAL_LIFECYCLE", path,
                f"Rule {sid!r} has illegal lifecycle status {status!r}"))
        ver = doc.get("verification")
        if ver not in ADMITTED_VERIFICATION:
            errors.append(_error(
                "ILLEGAL_VERIFICATION", path,
                f"Rule {sid!r} has illegal verification {ver!r}"))
        report = evaluate_rule_authority(doc, evidence)
        if not report.get("valid"):
            errors.append(_error(
                "INVALID_RULE_AUTHORITY", path,
                f"Rule {sid!r} authority is invalid"))
    for sid in result.get("selected_fact_ids") or []:
        if sid not in admitted_facts:
            continue
        doc = by_id.get(sid)
        path = "$.result.selected_fact_ids"
        if doc is None:
            errors.append(_error("OBJECT_UNAVAILABLE", path, f"Fact {sid!r} is not loadable"))
            continue
        if _pollutes(doc, task_scope) or not scope_allows(doc, task_scope):
            errors.append(_error(
                "CROSS_SCOPE", path,
                f"Fact {sid!r} is outside PLAN scope"))
        status = doc.get("status", ACTIVE_STATUS)
        if status != ACTIVE_STATUS or status in ILLEGAL_FACT_STATUS:
            errors.append(_error(
                "ILLEGAL_LIFECYCLE", path,
                f"Fact {sid!r} has illegal lifecycle status {status!r}"))
        ver = doc.get("verification")
        if ver not in ADMITTED_VERIFICATION:
            errors.append(_error(
                "ILLEGAL_VERIFICATION", path,
                f"Fact {sid!r} has illegal verification {ver!r}"))
    for sid in result.get("selected_case_ids") or []:
        if sid not in admitted_cases:
            continue
        doc = by_id.get(sid)
        path = "$.result.selected_case_ids"
        if doc is None:
            continue
        if _pollutes(doc, task_scope) or not scope_allows(doc, task_scope):
            errors.append(_error(
                "CROSS_SCOPE", path,
                f"Case {sid!r} is outside PLAN scope"))
    return errors


def _budget_errors(request: dict, plan: dict, result: dict, role: str) -> list:
    errors = []
    options = request.get("options") or {}
    if "limit" in options and options.get("limit") is not None:
        limit = int(options.get("limit"))
    else:
        limit = 8
    profile = load_role_profile(role)
    policy = policy_of(profile)
    checks = (
        ("selected_rule_ids", "rule"),
        ("selected_fact_ids", "current_fact"),
        ("selected_case_ids", "case"),
    )
    for field, kind in checks:
        cap = kind_limit(policy, kind, limit)
        if kind == "case":
            cap = min(2, cap)
        selected = list(result.get(field) or [])
        if len(selected) > cap:
            errors.append(_error(
                "CONTEXT_BUDGET_OVERFLOW", f"$.result.{field}",
                f"{field} selected {len(selected)} exceeds budget {cap}"))
    return errors


def _notes_are_not_selection(result: dict) -> None:
    """Free-text is ignored. Intentionally not parsed for object IDs."""
    _ = result.get("context_summary")
    _ = result.get("semantic_notes")


def _package_id(plan: dict, result: dict, built_from: dict, role: str, task_ref: str) -> str:
    payload = canonical_json({
        "task_ref": task_ref,
        "role": role,
        "plan_id": (plan or {}).get("plan_id"),
        "selected_rule_ids": sorted(result.get("selected_rule_ids") or []) if result else [],
        "selected_fact_ids": sorted(result.get("selected_fact_ids") or []) if result else [],
        "selected_case_ids": sorted(result.get("selected_case_ids") or []) if result else [],
        "checkpoint_entry_ids": sorted(result.get("checkpoint_entry_ids") or []) if result else [],
        "conflict_ids": sorted(result.get("conflict_ids") or []) if result else [],
        "built_from": built_from,
    })
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    safe_role = re.sub(r"[^A-Za-z0-9._:-]+", "-", role or "role").strip("-") or "role"
    return f"CTX-{safe_role}-{digest}"


def _anchor_digest_for(scope: dict) -> dict:
    if scope.get("type") == "project" and scope.get("project_id"):
        return anchor_digest(scope["project_id"])
    if scope.get("type") == "cross_project":
        projects = scope.get("projects") or []
        return {
            "mission": "cross-project: " + "; ".join(
                anchor_digest(pid)["mission"].strip() for pid in projects),
            "target_users": [],
            "relevant_scope": "explicit projects: " + ", ".join(projects),
            "relevant_constraints": [],
            "non_goals": [],
            "projects": projects,
        }
    return {}


def _minimal_package(request: dict, plan: dict | None, *, clock, blocked_by: list,
                     conflicts: list | None = None) -> dict:
    scope = (plan or {}).get("scope") or {
        "type": "project",
        "project_id": (request.get("project") or {}).get("project_id"),
        "projects": [],
        "task_id": None,
    }
    role = (plan or {}).get("role") or (request.get("target") or {}).get("role")
    task_ref = (plan or {}).get("task_ref") or request.get("task_ref")
    generated = clock()
    return {
        "schema_version": "1.1",
        "kind": "context_package",
        "request": {
            "task_id": task_ref,
            "role": role,
            "project_id": scope.get("project_id"),
        },
        "scope": scope,
        "project_phase": None,
        "anchor_digest": {},
        "team_state_slice": [],
        "project_state_slice": [],
        "rules": [],
        "current_facts": [],
        "cases": [],
        "open_conflicts": list(conflicts or []),
        "blocked_by": list(blocked_by),
        "task_evidence": [],
        "source_refs": [],
        "assembly_trace": {
            "scope_resolved": bool(plan),
            "case_search_performed": False,
            "case_search_trigger": None,
            "role_policy_applied": role,
            "filters_applied": ["finalize_hard_invariant_recheck"],
            "excluded_counts": {},
            "generated_at": generated,
        },
        "generated_at": generated,
    }


def _checkpoint_index(entries: list) -> dict:
    """Index PLAN checkpoint candidates by globally unique id.

    Last-wins dict compression is forbidden: a duplicate candidate id is an
    implementation collision, not a selection. Canonical local ids may still
    collide across checkpoint sources; those must already be namespaced.
    """
    by_id = {}
    collisions = []
    for entry in entries or []:
        cid = entry.get("id")
        if not cid:
            continue
        if cid in by_id:
            collisions.append(cid)
        by_id[cid] = entry
    if collisions:
        raise ValueError(
            "ambiguous checkpoint_entry candidate ids (last-wins is forbidden): "
            + ", ".join(sorted(set(collisions)))
        )
    for cid, entry in by_id.items():
        source, _local = split_checkpoint_candidate_id(cid)
        if entry.get("checkpoint") and entry.get("checkpoint") != source:
            raise ValueError(
                f"checkpoint candidate id {cid!r} does not match "
                f"entry.checkpoint {entry.get('checkpoint')!r}"
            )
    return by_id


def _activation_reason(plan: dict, case_id: str, doc: dict) -> str:
    for item in (plan.get("candidates") or {}).get("cases") or []:
        if item.get("id") == case_id:
            summary = (item.get("summary") or "").strip()
            if summary:
                return f"plan_admitted:{summary[:200]}"
    why = (doc.get("why_it_matters") or "").strip()
    if why:
        return f"plan_admitted:{why[:200]}"
    return "plan_admitted_scenario_match"


def _assemble_package(request: dict, plan: dict, result: dict, by_id: dict,
                      registry: dict, *, clock) -> dict:
    scope = plan["scope"]
    role = plan["role"]
    task_ref = plan["task_ref"]
    generated = clock()
    phase = _phase_of(registry, scope.get("project_id")) if scope.get("type") == "project" else None
    digest = _anchor_digest_for(scope)

    cp_by_id = _checkpoint_index(
        (plan.get("candidates") or {}).get("checkpoint_entries") or [])
    team_state, project_state = [], []
    for cid in result.get("checkpoint_entry_ids") or []:
        entry = cp_by_id.get(cid)
        if not entry:
            continue
        if entry.get("checkpoint") == "team":
            team_state.append(copy.deepcopy(entry))
        else:
            project_state.append(copy.deepcopy(entry))

    rules = []
    src_refs = []
    for sid in result.get("selected_rule_ids") or []:
        doc = by_id.get(sid)
        if doc is None:
            continue
        item = {
            "ref": canonical_object_ref(doc),
            "statement": doc.get("statement") or "",
            "modality": doc.get("modality"),
            "status": doc.get("status"),
            "verification": doc.get("verification"),
            "authority_refs": list(doc.get("authority_refs") or []),
        }
        rules.append(item)
        for ref in doc.get("source_refs") or []:
            if REF_RE.match(str(ref)):
                src_refs.append(f"{sid}: {ref}")

    facts = []
    for sid in result.get("selected_fact_ids") or []:
        doc = by_id.get(sid)
        if doc is None:
            continue
        item = {
            "ref": canonical_object_ref(doc),
            "statement": doc.get("statement") or "",
            "status": doc.get("status"),
            "verification": doc.get("verification"),
            "source_refs": [r for r in (doc.get("source_refs") or []) if REF_RE.match(str(r))],
        }
        facts.append(item)
        for ref in doc.get("source_refs") or []:
            if REF_RE.match(str(ref)):
                src_refs.append(f"{sid}: {ref}")

    cases = []
    for sid in result.get("selected_case_ids") or []:
        doc = by_id.get(sid)
        if doc is None:
            continue
        cases.append({
            "ref": canonical_object_ref(doc),
            "activation_reason": _activation_reason(plan, sid, doc),
            "why_it_matters": doc.get("why_it_matters") or "",
        })

    conf_by_id = {c["id"]: c for c in (plan.get("candidates") or {}).get("conflicts") or []
                  if c.get("id")}
    conflicts = []
    blocked_by = []
    for cid in result.get("conflict_ids") or []:
        src = conf_by_id.get(cid)
        if not src:
            continue
        conflicts.append(copy.deepcopy(src))
        for ref in src.get("refs") or []:
            if isinstance(ref, str) and ref.startswith("multica://issue/") and ref not in blocked_by:
                blocked_by.append(ref)

    seen = set()
    src_refs = [r for r in src_refs if not (r in seen or seen.add(r))]

    pointers = []
    if scope.get("type") == "project" and scope.get("project_id"):
        pointers.append(f"project-context/{scope['project_id']}")
        if scope["project_id"] != "teachers-app1":
            pointers.append(
                f".ai/context.yaml (lives in the {scope['project_id']} product repo)")
    elif scope.get("type") == "cross_project":
        for pid in scope.get("projects") or []:
            pointers.append(f"project-context/{pid}")

    case_allowed = bool((plan.get("case_search") or {}).get("allowed"))
    case_performed = case_allowed and bool(result.get("selected_case_ids"))
    filters = [
        "finalize_hard_invariant_recheck",
        "plan_id_task_ref_role_scope_bind",
        "selected_id_subset_of_plan",
        "rule_authority_recheck",
        "lifecycle_verification_recheck",
        "case_eligibility_recheck",
        "conflict_not_hidden",
        "cross_scope_admission_recheck",
        "context_budget",
        "package_ref_grammar",
    ]
    return {
        "schema_version": "1.1",
        "kind": "context_package",
        "request": {
            "task_id": task_ref,
            "role": role,
            "project_id": scope.get("project_id"),
        },
        "scope": scope,
        "project_phase": phase,
        "anchor_digest": digest or {},
        "team_state_slice": team_state,
        "project_state_slice": project_state,
        "rules": rules,
        "current_facts": facts,
        "cases": cases,
        "open_conflicts": conflicts,
        "blocked_by": blocked_by,
        "task_evidence": [],
        "source_refs": src_refs,
        "repo_local_pointers": pointers,
        "assembly_trace": {
            "scope_resolved": True,
            "case_search_performed": case_performed,
            "case_search_trigger": None,
            "role_policy_applied": role,
            "filters_applied": filters,
            "excluded_counts": {},
            "generated_at": generated,
        },
        "generated_at": generated,
    }


def _gate_status(errors: list, package: dict) -> tuple[str, dict]:
    if errors:
        first = errors[0]
        return "BLOCKED", {
            "required": True,
            "reason": first.get("code") or "finalize_blocked",
        }
    if package.get("open_conflicts") or package.get("blocked_by"):
        return "PARTIAL", {"required": False}
    return "READY", {"required": False}


def _codes(errors: list) -> list:
    return [e["code"] for e in errors]


def finalize_handoff(plan_input, compose_input, request: dict, *,
                     docs: list | None = None, registry: dict | None = None,
                     evidence=None, clock=None) -> dict:
    """Deterministic FINALIZE. Gate status is policy, never an LLM choice."""
    clock = clock or now_iso
    compat = frozen_contract_supports_finalize()
    if not compat["ok"]:
        raise RuntimeError("T00_CONTRACT_AMENDMENT_REQUIRED: frozen T00 cannot host FINALIZE")

    req_errors = _validate(REQUEST_SCHEMA, request)
    if req_errors:
        raise ValueError("invalid prepare_handoff_request: " + "; ".join(req_errors[:8]))

    built_from = compute_built_from(request)
    registry = _load_registry(registry)
    all_docs = docs if docs is not None else load_all_docs()
    by_id = _index_docs(all_docs)
    if evidence is None:
        evidence, _ev_errs = load_authority_evidence()

    errors: list = []
    plan_env = _as_plan_envelope(plan_input)
    plan = plan_env.get("plan") if isinstance(plan_env.get("plan"), dict) else None
    if plan_env.get("status") != "PLAN_READY" or plan is None:
        errors.append(_error(
            "T01_NOT_PLAN_READY", "$.plan.status",
            f"FINALIZE requires a T01 PLAN_READY plan; got {plan_env.get('status')!r}"))
        return _emit(
            request, None, None, built_from, errors, clock=clock, registry=registry,
        )

    compose_env = _as_compose_envelope(compose_input)
    result = compose_env.get("result") if isinstance(compose_env.get("result"), dict) else None
    if compose_env.get("status") != "ACCEPTED" or result is None:
        errors.append(_error(
            "T02_NOT_ACCEPTED", "$.compose.status",
            "FINALIZE accepts only a T02 validation envelope with status ACCEPTED; "
            f"got {compose_env.get('status')!r}"))
        return _emit(
            request, plan, None, built_from, errors, clock=clock, registry=registry,
        )

    for msg in _validate(PLAN_SCHEMA, plan, path="$.plan"):
        errors.append(_error("PLAN_SCHEMA_INVALID", "$.plan", msg))
    for msg in _validate(COMPOSE_SCHEMA, result, path="$.result"):
        errors.append(_error("COMPOSE_SCHEMA_INVALID", "$.result", msg))

    _notes_are_not_selection(result)
    errors.extend(_binding_errors(request, plan, result))
    errors.extend(_subset_errors(plan, result))
    errors.extend(_hidden_conflict_errors(plan, result))
    errors.extend(_case_errors(plan, result, by_id))
    errors.extend(_object_state_errors(plan, result, by_id, evidence))
    errors.extend(_budget_errors(request, plan, result, plan.get("role") or request["target"]["role"]))

    return _emit(
        request, plan, result, built_from, errors, clock=clock, registry=registry,
        by_id=by_id,
    )


def _emit(request, plan, result, built_from, errors, *, clock, registry, by_id=None) -> dict:
    bounded = _bound(errors)
    role = (plan or {}).get("role") or (request.get("target") or {}).get("role")
    task_ref = (plan or {}).get("task_ref") or request.get("task_ref")
    generated = clock()
    if plan is not None and result is not None and by_id is not None and not any(
            e["code"] in {
                "T01_NOT_PLAN_READY", "T02_NOT_ACCEPTED", "PLAN_ID_MISMATCH",
                "TASK_REF_MISMATCH", "ROLE_MISMATCH", "SCOPE_MISMATCH",
            } for e in bounded):
        package = _assemble_package(
            request, plan, result, by_id, registry, clock=clock)
    else:
        package = _minimal_package(
            request, plan, clock=clock,
            blocked_by=_codes(bounded),
        )

    import context_quality
    try:
        quality = context_quality.assess("package", package)
        if quality["blocked"]:
            bounded = _bound(bounded + [_error("CONTEXT_BUDGET_OVERFLOW", "$.package",
                                             context_quality.diagnostic(quality))])
    except context_quality.QualityPolicyError as exc:
        bounded = _bound(bounded + [_error("CONTEXT_QUALITY_POLICY_INVALID", "$.package", str(exc))])

    if bounded:
        # Hard failures never keep illegal objects in a READY/PARTIAL package.
        # Rebuild a valid minimal package that still carries visible blockers.
        visible_conflicts = package.get("open_conflicts") or []
        if any(e["code"] == "HIDDEN_CONFLICT" for e in bounded):
            visible_conflicts = list((plan or {}).get("candidates", {}).get("conflicts") or [])
        package = _minimal_package(
            request, plan, clock=clock,
            blocked_by=sorted(set(_codes(bounded) + list(package.get("blocked_by") or []))),
            conflicts=visible_conflicts if any(e["code"] == "HIDDEN_CONFLICT" for e in bounded) else [],
        )
        if plan is not None:
            package["project_phase"] = _phase_of(registry, (plan.get("scope") or {}).get("project_id"))
            package["anchor_digest"] = _anchor_digest_for(plan.get("scope") or {}) or {}

    pkg_errs = _validate(PACKAGE_SCHEMA, package)
    if pkg_errs:
        bounded = _bound(bounded + [_error(
            "PACKAGE_SCHEMA_INVALID", "$.package", msg) for msg in pkg_errs[:8]])
        package = _minimal_package(
            request, plan, clock=clock,
            blocked_by=sorted(set(_codes(bounded))),
        )
        # Repair any residual schema issue on the fallback package.
        pkg_errs = _validate(PACKAGE_SCHEMA, package)
        if pkg_errs:
            # Last-resort: empty legal package with only required keys.
            package["blocked_by"] = sorted(set(list(package.get("blocked_by") or []) + ["PACKAGE_SCHEMA_INVALID"]))

    # Grammar re-check: never emit pseudo-scheme refs.
    bad_refs = pseudo_scheme_refs(package) + grammar_invalid_refs(package)
    if bad_refs:
        bounded = _bound(bounded + [_error(
            "PACKAGE_REF_GRAMMAR_INVALID", "$.package",
            f"illegal package ref {bad_refs[0]!r}")])
        for group in ("rules", "current_facts", "cases"):
            package[group] = []
        package["blocked_by"] = sorted(set(list(package.get("blocked_by") or []) + ["PACKAGE_REF_GRAMMAR_INVALID"]))

    status, escalation = _gate_status(bounded, package)
    if status == "PARTIAL" and package.get("open_conflicts") and not package.get("blocked_by"):
        package["blocked_by"] = [
            f"open_conflict:{c.get('id')}" for c in package["open_conflicts"] if c.get("id")
        ]
    out = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_result",
        "status": status,
        "package_id": _package_id(plan or {}, result or {}, built_from, role, task_ref),
        "task_ref": task_ref,
        "role": role,
        "built_from": built_from,
        "package": package,
        "escalation": escalation,
        "generated_at": generated,
    }
    result_errs = _validate(RESULT_SCHEMA, out)
    if result_errs:
        # Keep the frozen shape valid even if a check was incomplete.
        package = _minimal_package(
            request, plan, clock=clock,
            blocked_by=sorted(set(_codes(bounded) + ["RESULT_SCHEMA_INVALID"])),
        )
        out["status"] = "BLOCKED"
        out["escalation"] = {"required": True, "reason": "RESULT_SCHEMA_INVALID"}
        out["package"] = package
        out["package_id"] = _package_id(plan or {}, result or {}, built_from, role, task_ref)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="T03 deterministic handoff FINALIZE")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compatibility", help="prove frozen T00 can host T03")
    fin = sub.add_parser("finalize", help="assemble prepare_handoff_result")
    fin.add_argument("--plan-file", required=True)
    fin.add_argument("--result-file", required=True)
    fin.add_argument("--request-file", required=True)
    args = parser.parse_args()
    if args.command == "compatibility":
        report = frozen_contract_supports_finalize()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    plan_input = json.loads(Path(args.plan_file).read_text(encoding="utf-8"))
    compose_input = json.loads(Path(args.result_file).read_text(encoding="utf-8"))
    request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    result = finalize_handoff(plan_input, compose_input, request)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"READY", "PARTIAL", "BLOCKED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
