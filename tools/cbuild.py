#!/usr/bin/env python3
"""Task Context Package assembler for V1.1 (YZT-40 R7). Scope-first, deterministic."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import (TEAM, PROJECTS, ScopeError, toks, now_iso, doc_at,
                   object_scope, scope_allows, entry_relevant, project_of)  # noqa: E402
from cdata import load_all_docs, checkpoint_docs, load_role_profile  # noqa: E402


def _negative_dominates(napp_hit: set, applied_hit: set) -> bool:
    return len(napp_hit) > len(applied_hit)


def resolve_scope(task_id: str, scope_arg: str | None, projects: list,
                  project_id: str | None) -> dict:
    reg_ids = [p["id"] for p in doc_at(TEAM / "registry" / "projects.yaml")["projects"]]
    if scope_arg == "team":
        return {"type": "team", "project_id": None, "projects": [], "task_id": task_id}
    if scope_arg == "cross_project":
        if len(projects) < 2 or any(x not in reg_ids for x in projects):
            raise ScopeError(
                "cross_project requires an explicit registered projects list with >= 2 entries")
        return {"type": "cross_project", "project_id": None,
                "projects": projects, "task_id": task_id}
    if not project_id:
        raise ScopeError("scope unresolved: project_id required before retrieval (D-02)")
    if project_id not in reg_ids:
        raise ScopeError(f"project_id {project_id!r} not registered")
    return {"type": "project", "project_id": project_id, "projects": [],
            "task_id": task_id}


def anchor_digest(project_id: str) -> dict:
    a = doc_at(PROJECTS / project_id / "project.yaml")
    gov = a["governance"]
    return {"mission": gov["mission"],
            "target_users": gov.get("target_users", []),
            "relevant_scope": "; ".join(gov.get("scope", [])),
            "relevant_constraints": gov.get("hard_constraints", []),
            "non_goals": gov.get("non_goals", [])}


def collect_conflicts(task_scope: dict) -> tuple:
    conflicts, blockers = [], []
    for cp in checkpoint_docs():
        st = cp["scope"]["type"]
        if task_scope["type"] == "project" and st == "team":
            continue
        if task_scope["type"] == "project" and st == "project" and \
                cp["scope"].get("project_id") != task_scope["project_id"]:
            continue
        if task_scope["type"] == "cross_project" and st == "project" and \
                cp["scope"].get("project_id") not in (task_scope.get("projects") or []):
            continue
        for section in ("conflicts", "open"):
            for entry in cp.get(section, []) or []:
                if section == "conflicts":
                    conflicts.append({
                        "source": f"checkpoint:{cp['scope'].get('project_id') or 'team'}",
                        "id": entry["id"], "summary": entry["summary"],
                        "refs": entry.get("refs", [])})
                for ref in entry.get("refs", []):
                    if ref.startswith("multica://issue/") and ref not in blockers:
                        blockers.append(ref)
    return conflicts, blockers


def build_package(task_id: str, role: str, task_scope: dict, decision: str = "",
                  query: str = "", case_trigger: str | None = None,
                  limit: int = 8) -> dict:
    excluded = {"other_project": 0, "not_active": 0}
    trace = {"scope_resolved": True, "case_search_performed": False,
             "case_search_trigger": None, "role_policy_applied": role,
             "filters_applied": ["strict_scope_filter", "lifecycle_active_only",
                                 "case_activation_default_off", "context_budget"],
             "excluded_counts": excluded,
             "generated_at": now_iso()}
    reg = doc_at(TEAM / "registry" / "projects.yaml")["projects"]
    phase = None
    if task_scope["type"] == "project":
        phase = next(p["phase"] for p in reg if p["id"] == task_scope["project_id"])
    digest = None
    if task_scope["type"] == "project":
        digest = anchor_digest(task_scope["project_id"])
    elif task_scope["type"] == "cross_project":
        digest = {"mission": "cross-project: " + "; ".join(
            anchor_digest(pid)["mission"].strip() for pid in task_scope["projects"]),
            "target_users": [], "relevant_scope": "explicit projects: " +
            ", ".join(task_scope["projects"]),
            "relevant_constraints": [], "non_goals": [],
            "projects": task_scope["projects"]}
    text = " ".join([task_id, decision or "", query or "",
                     task_scope.get("project_id") or ""])
    task_tokens = toks(text)

    rules, facts, cases = [], [], []
    for doc in load_all_docs():
        if not scope_allows(doc, task_scope):
            excluded["other_project"] += 1
            continue
        if doc.get("status", "active") != "active":
            excluded["not_active"] += 1
            continue
        body = doc.get("statement", "") + " " + doc.get("title", "")
        if doc["_kind"] == "rule":
            appl = doc.get("applicability", {}).get("semantic", {}) or {}
            body += " " + " ".join(appl.get("applicable_when") or [])
        if toks(body) & task_tokens:
            {"rule": rules, "current_fact": facts, "case": cases}[doc["_kind"]].append(doc)
    rules = rules[:limit]
    facts = facts[:limit]

    activated, activation_reasons = [], {}
    if case_trigger:
        trace["case_search_performed"] = True
        trace["case_search_trigger"] = case_trigger
        trace["filters_applied"].append("case_activation_scenario_match")
        for c in cases:
            applied = toks(" ".join(c.get("applicable_when") or []))
            not_applied = toks(" ".join(c.get("not_applicable_when") or []))
            applied_hit = applied & task_tokens
            napp_hit = not_applied & task_tokens
            # conservative bounded check (design §17.6): a negative phrase kills
            # activation only when negative evidence dominates positive evidence.
            if applied_hit and (len(napp_hit) <= len(applied_hit)) \
                    and not _negative_dominates(napp_hit, applied_hit):
                activated.append(c)
                activation_reasons[c["id"]] = (
                    f"scenario_match(trigger={case_trigger}, "
                    f"tokens={sorted(applied_hit)})")
        activated = activated[:2]

    conflicts, blockers = collect_conflicts(task_scope)
    team_state = []
    if task_scope["type"] != "project":
        for cp in checkpoint_docs():
            if cp["scope"]["type"] != "team":
                continue
            for section in ("confirmed", "open", "conflicts"):
                for entry in cp.get(section, []) or []:
                    if section == "confirmed" or entry_relevant(entry, task_tokens):
                        team_state.append({"checkpoint": "team", "section": section,
                                           "id": entry["id"], "summary": entry["summary"],
                                           "refs": entry.get("refs", [])})
    project_state = []
    if task_scope["type"] == "project":
        pcp = doc_at(PROJECTS / task_scope["project_id"] / "checkpoint.yaml")
        for section in ("confirmed", "open", "conflicts"):
            for entry in pcp.get(section, []) or []:
                if section == "confirmed" or entry_relevant(entry, task_tokens):
                    project_state.append({"checkpoint": task_scope["project_id"],
                                          "section": section, "id": entry["id"],
                                          "summary": entry["summary"],
                                          "refs": entry.get("refs", [])})
    src_refs = []
    for d in rules + facts:
        src_refs += [f"{d['id']}: {r}" for r in (d.get("source_refs") or [])]
    pointers = []
    if task_scope["type"] == "project":
        pointers.append(f"project-context/{task_scope['project_id']}")
        pointers.append(f".ai/context.yaml (lives in the {task_scope['project_id']} product repo)")
    return {
        "schema_version": "1.1", "kind": "context_package",
        "request": {"task_id": task_id, "role": role,
                    "project_id": task_scope.get("project_id")},
        "scope": task_scope, "project_phase": phase,
        "anchor_digest": digest or {},
        "team_state_slice": team_state, "project_state_slice": project_state,
        "rules": [{"ref": f"rule:{d['id']}", "statement": d["statement"],
                   "modality": d["modality"], "status": d["status"],
                   "verification": d["verification"],
                   "authority_refs": d.get("authority_refs", [])} for d in rules],
        "current_facts": [{"ref": f"fact:{d['id']}", "statement": d["statement"],
                           "status": d["status"], "verification": d["verification"],
                           "source_refs": d.get("source_refs", [])} for d in facts],
        "cases": [{"ref": f"case:{d['id']}",
                   "activation_reason": activation_reasons[d["id"]],
                   "why_it_matters": d.get("why_it_matters", "")} for d in activated],
        "open_conflicts": conflicts, "blocked_by": blockers,
        "task_evidence": [], "source_refs": src_refs,
        "repo_local_pointers": pointers,
        "assembly_trace": trace, "generated_at": trace["generated_at"],
    }
