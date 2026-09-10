#!/usr/bin/env python3
"""T01 — deterministic prepare_handoff PLAN builder (YZT-47).

Framework-neutral. Never calls a model. Finding Gate is an internal runtime
capability, not a new public Native API. Default processing never writes
Canonical Memory; Canonical-changing dispositions either apply through an
injected mutator or block the PLAN.

FIND-WIMG-HO00-000001 is closed in T03 FINALIZE (package refs use the frozen
evidence-ref grammar). PLAN candidates still carry canonical object ids;
`ref` is omitted unless a grammar-valid URI already exists.

Checkpoint candidate identity is globally namespaced `{checkpoint}:{local_id}`
so a duplicate canonical local id (team vs project) stays independently
addressable. Canonical checkpoint YAML is not rewritten to hide the collision.
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
from cbuild import (  # noqa: E402
    _checkpoint_entries, _negative_dominates, anchor_digest, collect_conflicts,
    resolve_scope,
)
from cdata import checkpoint_docs, load_all_docs, load_role_profile  # noqa: E402
from chandoff import (  # noqa: E402
    canonical_json, checkpoint_candidate_id, compute_built_from,
    fingerprint_from_request, memory_revision,
)
from crole import (  # noqa: E402
    kind_limit, policy_of, posture_entries, relevance_required, role_rank,
    wants_project_slice, wants_team_conflicts, wants_team_posture, wants_team_slice,
)
from cutil import (  # noqa: E402
    RUNTIME, ScopeError, TEAM, object_scope, scope_allows, toks,
)
from schema_mini import Schema, load_schema_file  # noqa: E402
from yaml_mini import parse_yaml  # noqa: E402

LLM_CALLED = False

REF_RE = re.compile(r"^(multica|adr|doc|repo|registry|project|git)://\S+$")
FINDING_ID_RE = re.compile(r"^FIND-[A-Z0-9]{1,12}-[A-Z0-9]{1,12}-[0-9]{6}$")

SEMANTIC_JOBS_ORDER = (
    "interpret_task_intent",
    "select_task_applicable_rules_from_candidates",
    "select_decision_relevant_facts_from_candidates",
    "compress_checkpoint_slice",
    "perform_case_scenario_match_when_case_search_already_allowed",
    "phrase_visible_conflicts",
    "produce_minimum_sufficient_context",
)

ADMITTED_VERIFICATION = {"verified", "partially_verified"}
ACTIVE_STATUS = "active"
ARCHIVED_PHASE = "archived"
SAFE_DISPOSITIONS = {"forget", "pointer", "absorbed_by_existing"}
MATERIAL_INTENTS = {"durable_candidate", "context_challenge"}
BLOCKING_DISPOSITIONS = {
    "create_rule", "create_fact", "create_case", "issue_escalation",
    "carry_to_checkpoint",
}
MATERIAL_TOKEN_TEXT = (
    "scope rule fact architecture ownership compatibility integrity "
    "canonical authority checkpoint constraint mission governance "
    "provider schema contract"
)

CASE_SEARCH_ENABLE = {
    "architecture_design", "major_technical_choice", "complex_cross_repo_change",
    "incident", "performance_problem", "migration", "repeated_problem",
    "explicit_historical_search", "multiple_reasonable_options",
}

class NoCanonicalWriteMutator:
    """Default mutator: apply only non-Canonical dispositions."""

    def apply(self, finding: dict, disposition: str) -> dict:
        if disposition in SAFE_DISPOSITIONS:
            return {"applied": True, "canonical_changed": False, "block_reason": None}
        return {
            "applied": False,
            "canonical_changed": False,
            "block_reason": "canonical_write_not_safe_in_plan_builder",
        }


class MemoryFindingStore:
    """In-memory finding store used by tests and capture proofs."""

    def __init__(self, findings: list | None = None):
        self._items = [copy.deepcopy(f) for f in (findings or [])]

    def load_open(self) -> list:
        return [copy.deepcopy(f) for f in self._items if f.get("status") == "open"]

    def save(self, finding: dict) -> None:
        fid = finding.get("finding_id")
        for i, existing in enumerate(self._items):
            if existing.get("finding_id") == fid:
                self._items[i] = copy.deepcopy(finding)
                return
        self._items.append(copy.deepcopy(finding))

    def mark_processed(self, finding: dict, disposition: str, note: str | None) -> dict:
        updated = copy.deepcopy(finding)
        updated["status"] = "processed"
        updated["disposition"] = disposition
        if note:
            updated["disposition_note"] = note
        self.save(updated)
        return updated


class RuntimeFindingStore:
    """Loads open Findings from the runtime task workspace. Does not wake anyone."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else (RUNTIME / "findings")

    def load_open(self) -> list:
        if not self.root.exists():
            return []
        out = []
        for path in sorted(self.root.glob("FIND-*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            if doc.get("status") == "open":
                out.append(doc)
        return out

    def save(self, finding: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{finding['finding_id']}.json"
        path.write_text(json.dumps(finding, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    def mark_processed(self, finding: dict, disposition: str, note: str | None) -> dict:
        updated = copy.deepcopy(finding)
        updated["status"] = "processed"
        updated["disposition"] = disposition
        if note:
            updated["disposition_note"] = note
        self.save(updated)
        return updated


def compatibility_check() -> dict:
    """Prove the frozen T00 contract can host Finding Gate without amendment."""
    prep = load_schema_file("context-handoff/prepare-handoff-result.schema.json")
    sres = load_schema_file("context-handoff/self-check-result.schema.json")
    preq = load_schema_file("context-handoff/prepare-handoff-request.schema.json")
    common = load_schema_file("context-handoff/handoff-common.schema.json")
    prep_status = set(common["$defs"]["prepare_handoff_status"]["enum"])
    self_status = set(common["$defs"]["self_check_status"]["enum"])
    prep_required = set(prep.get("required") or [])
    preq_required = set(preq.get("required") or [])
    frozen_prepare_ok = (
        prep_status == {"READY", "PARTIAL", "BLOCKED"}
        and "escalation" in prep_required
        and "task_ref" in preq_required
        and "target" in preq_required
        and "task_snapshot" in preq_required
        and "project" in preq_required
    )
    frozen_self_ok = self_status == {"READY", "REFRESH_REQUIRED", "BLOCKED"}
    express_ok = "BLOCKED" in prep_status and "REFRESH_REQUIRED" in self_status
    result = {
        "frozen_prepare_handoff_contract_can_support_finding_gate": bool(frozen_prepare_ok),
        "frozen_self_check_contract_can_support_finding_gate": bool(frozen_self_ok),
        "frozen_result_schema_can_express_blocked_or_refresh": bool(express_ok),
    }
    result["ok"] = all(result.values())
    return result


def _validate(schema_name: str, instance: dict) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def _is_valid_ref(value) -> bool:
    return isinstance(value, str) and bool(REF_RE.match(value))


def _opt_summary(text: str | None) -> dict:
    value = (text or "").strip()
    if not value:
        return {}
    return {"summary": value[:240]}


def _norm_key(value: str) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def load_registry(registry: dict | None = None) -> dict:
    if registry is not None:
        return registry
    return parse_yaml((TEAM / "registry" / "projects.yaml").read_text(encoding="utf-8"))


def _registry_by_id(registry: dict) -> dict:
    return {p["id"]: p for p in registry.get("projects") or []}


def _task_tokens(request: dict) -> set:
    snap = request.get("task_snapshot") or {}
    parts = [
        request.get("task_ref") or "",
        snap.get("title") or "",
        snap.get("description") or "",
        " ".join(snap.get("requirements") or []),
        " ".join(snap.get("acceptance_criteria") or []),
        " ".join(snap.get("relevant_decisions") or []),
        (request.get("project") or {}).get("project_id") or "",
        request.get("purpose") or "",
    ]
    return toks(" ".join(parts))


def _task_associated(finding: dict, task_ref: str) -> bool:
    tid = finding.get("task_id") or ""
    if not tid or not task_ref:
        return False
    if tid == task_ref:
        return True
    if task_ref.endswith("/" + tid):
        return True
    return False


def _finding_as_doc(finding: dict) -> dict:
    return {
        "scope": {
            "type": "project",
            "project_id": finding.get("project_id"),
            "projects": [],
            "task_id": None,
        },
        "project_id": finding.get("project_id"),
    }


def _finding_text(finding: dict) -> str:
    return " ".join([
        finding.get("summary") or "",
        finding.get("detail") or "",
        finding.get("intent") or "",
        finding.get("discovered_by") or "",
    ])


def _is_material(finding: dict, task_tokens: set) -> bool:
    intent = finding.get("intent")
    if intent in MATERIAL_INTENTS:
        return True
    if finding.get("verification") == "conflicted":
        return True
    ft = toks(_finding_text(finding))
    if intent == "task_delivery":
        return False
    return bool(ft & toks(MATERIAL_TOKEN_TEXT))


def _relevant_to_target(finding: dict, request: dict, task_scope: dict, task_tokens: set) -> bool:
    target = request["target"]["role"]
    if finding.get("discovered_by") == target:
        return True
    if finding.get("intent") in MATERIAL_INTENTS:
        return True
    text = _finding_text(finding).lower()
    if target.replace("-", " ") in text or target in text:
        return True
    ft = toks(_finding_text(finding))
    if not (ft & task_tokens):
        return False
    if task_scope["type"] == "project":
        return finding.get("project_id") == task_scope.get("project_id")
    if task_scope["type"] == "cross_project":
        return finding.get("project_id") in (task_scope.get("projects") or [])
    return True


def classify_disposition(finding: dict) -> str:
    if finding.get("disposition"):
        return finding["disposition"]
    intent = finding.get("intent")
    ver = finding.get("verification")
    if ver == "refuted" or intent == "task_delivery":
        return "forget"
    if ver == "conflicted" or intent == "context_challenge":
        return "issue_escalation"
    if intent == "observation":
        return "pointer" if ver in ADMITTED_VERIFICATION else "forget"
    if intent == "durable_candidate":
        if ver not in ADMITTED_VERIFICATION:
            return "issue_escalation"
        return "create_fact"
    return "forget"


def capture_finding(finding: dict, store=None) -> dict:
    """REPORT_FINDING capture only. Never processes, never wakes Context Engineer."""
    store = store or MemoryFindingStore()
    doc = copy.deepcopy(finding)
    doc.setdefault("schema_version", "1.1")
    doc["kind"] = "finding"
    doc["status"] = "open"
    doc.setdefault("verification", "unverified")
    if not FINDING_ID_RE.match(doc.get("finding_id") or ""):
        raise ValueError(f"invalid finding_id: {doc.get('finding_id')!r}")
    store.save(doc)
    return {
        "finding_id": doc["finding_id"],
        "ingest": "immediate",
        "status": "open",
        "context_engineer_run": False,
        "canonical_write": False,
        "process_now": False,
        "wake_context_engineer": False,
        "context_engineer_woken": False,
    }


def finding_gate(request: dict, task_scope: dict, *, findings: list | None = None,
                 store=None, mutator=None, boundary: str = "handoff") -> dict:
    """Internal FINDING_GATE. boundary=handoff for T01; other boundaries stay T02+."""
    mutator = mutator or NoCanonicalWriteMutator()
    store = store or MemoryFindingStore(findings or [])
    open_findings = findings if findings is not None else store.load_open()
    task_ref = request["task_ref"]
    task_tokens = _task_tokens(request)
    relevant, processed, remaining, blocked = [], [], [], []
    canonical_changed = False
    for finding in sorted(open_findings, key=lambda f: f.get("finding_id") or ""):
        if finding.get("status") != "open":
            continue
        if not _task_associated(finding, task_ref):
            continue
        if not scope_allows(_finding_as_doc(finding), task_scope):
            continue
        if not _relevant_to_target(finding, request, task_scope, task_tokens):
            continue
        relevant.append(copy.deepcopy(finding))
        material = _is_material(finding, task_tokens)
        disposition = classify_disposition(finding)
        if not material:
            applied = mutator.apply(finding, disposition if disposition in SAFE_DISPOSITIONS else "forget")
            used = disposition if disposition in SAFE_DISPOSITIONS else "forget"
            if applied.get("applied"):
                processed.append(store.mark_processed(finding, used, "non_material_auto"))
            else:
                remaining.append(copy.deepcopy(finding))
            continue
        applied = mutator.apply(finding, disposition)
        if applied.get("applied"):
            note = "material_auto"
            processed.append(store.mark_processed(finding, disposition, note))
            if applied.get("canonical_changed"):
                canonical_changed = True
            continue
        blocked.append({
            "finding_id": finding.get("finding_id"),
            "reason": applied.get("block_reason") or "unsafe_material_finding",
            "disposition": disposition,
            "verification": finding.get("verification"),
            "intent": finding.get("intent"),
        })
    if blocked:
        first = blocked[0]
        reason = "unresolved_material_finding"
        if first.get("disposition") == "issue_escalation" and first.get("intent") == "context_challenge":
            reason = "evidence_conflict"
        elif first.get("disposition") in {"create_rule", "issue_escalation"}:
            reason = "authority_gap"
        return {
            "status": "BLOCKED",
            "boundary": boundary,
            "relevant_open_findings": relevant,
            "processed_findings": processed,
            "remaining_nonblocking_findings": remaining,
            "blocked_findings": blocked,
            "canonical_changed": canonical_changed,
            "escalation": {"required": True, "reason": reason},
            "context_engineer_woken": False,
        }
    status = "REFRESH_REQUIRED" if canonical_changed else "CLEAR"
    return {
        "status": status,
        "boundary": boundary,
        "relevant_open_findings": relevant,
        "processed_findings": processed,
        "remaining_nonblocking_findings": remaining,
        "blocked_findings": [],
        "canonical_changed": canonical_changed,
        "escalation": {"required": False},
        "context_engineer_woken": False,
    }


def resolve_handoff_scope(request: dict, registry: dict | None = None) -> dict:
    registry = load_registry(registry)
    by_id = _registry_by_id(registry)
    project_id = request["project"]["project_id"]
    options = request.get("options") or {}
    xp = [p for p in (options.get("cross_project_projects") or []) if isinstance(p, str) and p.strip()]
    task_ref = request["task_ref"]
    if xp:
        if len(xp) < 2:
            raise ScopeError(
                "cross_project requires an explicit registered projects list with >= 2 entries")
        for pid in xp:
            if pid not in by_id:
                raise ScopeError(f"project_id {pid!r} not registered")
            if by_id[pid].get("phase") == ARCHIVED_PHASE:
                raise ScopeError(f"archived project {pid!r} excluded")
        scope = resolve_scope(task_ref, "cross_project", xp, None)
    else:
        if project_id not in by_id:
            raise ScopeError(f"project_id {project_id!r} not registered")
        if by_id[project_id].get("phase") == ARCHIVED_PHASE:
            raise ScopeError(f"archived project {project_id!r} excluded")
        scope = resolve_scope(task_ref, "project", [], project_id)
    if scope["type"] != "task":
        scope = dict(scope)
        scope["task_id"] = None
    return scope


def _phase_of(registry: dict, project_id: str | None) -> str | None:
    if not project_id:
        return None
    for p in registry.get("projects") or []:
        if p["id"] == project_id:
            return p.get("phase")
    return None


def _project_archived(registry: dict, project_id: str | None) -> bool:
    return _phase_of(registry, project_id) == ARCHIVED_PHASE


def _allowed_project_ids(task_scope: dict) -> set:
    if task_scope["type"] == "project":
        return {task_scope["project_id"]}
    if task_scope["type"] == "cross_project":
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


def _phase_allows(doc: dict, phase: str | None) -> bool:
    if not phase:
        return True
    appl = ((doc.get("applicability") or {}).get("deterministic") or {})
    phases = appl.get("project_phases") or []
    if not phases:
        return True
    return phase in phases


def _case_search_allowed(request: dict, profile: dict) -> tuple[bool, str | None]:
    options = request.get("options") or {}
    trigger = options.get("case_trigger")
    if isinstance(trigger, str) and trigger.strip():
        return True, trigger.strip()
    purpose = _norm_key(request.get("purpose") or "")
    policy = profile.get("retrieval_policy") or {}
    triggers = {_norm_key(x) for x in (policy.get("case_search_triggers") or []) if isinstance(x, str)}
    if purpose and (purpose in CASE_SEARCH_ENABLE or purpose in triggers):
        return True, purpose
    return False, None


def _scenario_match(case: dict, task_tokens: set) -> bool:
    applied = toks(" ".join(case.get("applicable_when") or []))
    not_applied = toks(" ".join(case.get("not_applicable_when") or []))
    applied_hit = applied & task_tokens
    napp_hit = not_applied & task_tokens
    if not applied_hit:
        return False
    if _negative_dominates(napp_hit, applied_hit):
        return False
    if len(napp_hit) > len(applied_hit):
        return False
    return True


def _as_checkpoint_candidate(entry: dict) -> dict:
    """Copy a checkpoint slice entry and namespace its candidate id."""
    mapped = dict(entry)
    mapped["id"] = checkpoint_candidate_id(mapped.get("checkpoint"), mapped.get("id"))
    return mapped


def inventory_canonical_checkpoint_ids(checkpoints: list | None = None) -> dict:
    """Inventory locally-scoped canonical checkpoint ids across sources.

    Does not rewrite canonical checkpoint content. Duplicate local ids are
    expected (schema is per-checkpoint); PLAN candidate ids must still be
    globally unique after namespacing.
    """
    cps = checkpoints if checkpoints is not None else checkpoint_docs()
    by_local: dict[str, list] = {}
    for cp in cps:
        scope = cp.get("scope") or {}
        if scope.get("type") == "team":
            source = "team"
        else:
            source = scope.get("project_id") or cp.get("checkpoint_level") or "unknown"
        for section in ("confirmed", "open", "conflicts"):
            for entry in cp.get(section) or []:
                local = entry.get("id")
                if not local:
                    continue
                rec = {
                    "checkpoint": source,
                    "section": section,
                    "local_id": local,
                    "candidate_id": checkpoint_candidate_id(source, local),
                }
                by_local.setdefault(local, []).append(rec)
    duplicates = {k: v for k, v in sorted(by_local.items()) if len(v) > 1}
    return {
        "entry_count": sum(len(v) for v in by_local.values()),
        "unique_local_ids": len(by_local),
        "duplicate_local_ids": duplicates,
        "canonical_content_rewritten": False,
        "candidate_last_wins_resolution": False,
    }


def _collect_evidence(docs: list, checkpoint_entries: list) -> list:
    refs = []
    seen = set()
    for doc in docs:
        for key in ("authority_refs", "source_refs"):
            for ref in doc.get(key) or []:
                if _is_valid_ref(ref) and ref not in seen:
                    seen.add(ref)
                    refs.append({"ref": ref})
        for item in doc.get("verified_against") or []:
            if isinstance(item, dict):
                ref = item.get("source_ref")
                if _is_valid_ref(ref) and ref not in seen:
                    seen.add(ref)
                    refs.append({"ref": ref, **({"note": item["note"]} if item.get("note") else {})})
    for entry in checkpoint_entries:
        for ref in entry.get("refs") or []:
            if _is_valid_ref(ref) and ref not in seen:
                seen.add(ref)
                refs.append({"ref": ref})
    refs.sort(key=lambda x: x["ref"])
    return refs


def _semantic_jobs(candidates: dict, case_allowed: bool) -> list:
    jobs = ["interpret_task_intent"]
    if candidates.get("rules"):
        jobs.append("select_task_applicable_rules_from_candidates")
    if candidates.get("facts"):
        jobs.append("select_decision_relevant_facts_from_candidates")
    if candidates.get("checkpoint_entries"):
        jobs.append("compress_checkpoint_slice")
    if case_allowed and candidates.get("cases"):
        jobs.append("perform_case_scenario_match_when_case_search_already_allowed")
    if candidates.get("conflicts"):
        jobs.append("phrase_visible_conflicts")
    jobs.append("produce_minimum_sufficient_context")
    ordered = [j for j in SEMANTIC_JOBS_ORDER if j in jobs]
    return ordered


def _plan_id(request: dict, scope: dict, memory_rev: str) -> str:
    payload = canonical_json({
        "task_ref": request["task_ref"],
        "role": request["target"]["role"],
        "scope": scope,
        "memory_revision": memory_rev,
        "fingerprint": fingerprint_from_request(request),
    })
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    role = request["target"]["role"]
    return f"PLAN-{role}-{digest}"


def _authority_ok(rule: dict, evidence) -> bool:
    report = evaluate_rule_authority(rule, evidence)
    return bool(report.get("valid"))


def prepare_handoff_plan(request: dict, *, findings: list | None = None,
                         store=None, mutator=None, docs: list | None = None,
                         registry: dict | None = None, checkpoints: list | None = None,
                         revision_fn=None) -> dict:
    """Build a schema-valid context_plan or BLOCKED. Never calls a model."""
    compat = compatibility_check()
    if not compat["ok"]:
        return {
            "status": "T00_CONTRACT_AMENDMENT_REQUIRED",
            "plan": None,
            "llm_called": False,
            "scope_pollution": 0,
            "compatibility_check": compat,
            "finding_gate": None,
            "escalation": {"required": True, "reason": "T00_CONTRACT_AMENDMENT_REQUIRED"},
        }
    errors = _validate("context-handoff/prepare-handoff-request.schema.json", request)
    if errors:
        raise ValueError("invalid prepare_handoff_request: " + "; ".join(errors[:8]))

    revision_before = (revision_fn or memory_revision)()
    registry = load_registry(registry)
    try:
        task_scope = resolve_handoff_scope(request, registry)
    except ScopeError as exc:
        return {
            "status": "BLOCKED",
            "plan": None,
            "llm_called": False,
            "scope_pollution": 0,
            "compatibility_check": compat,
            "finding_gate": None,
            "escalation": {"required": True, "reason": str(exc)},
            "memory_revision_before_gate": revision_before,
            "memory_revision_after_gate": revision_before,
        }

    role = request["target"]["role"]
    profile = load_role_profile(role)
    policy = policy_of(profile)
    limit = int((request.get("options") or {}).get("limit") or 8)

    gate_store = store if store is not None else MemoryFindingStore(findings or [])
    if findings is None and store is None:
        gate_store = RuntimeFindingStore()
    gate = finding_gate(
        request, task_scope, findings=findings, store=gate_store,
        mutator=mutator or NoCanonicalWriteMutator(), boundary="handoff",
    )
    if gate["status"] == "BLOCKED":
        return {
            "status": "BLOCKED",
            "plan": None,
            "llm_called": False,
            "scope_pollution": 0,
            "compatibility_check": compat,
            "finding_gate": gate,
            "escalation": gate["escalation"],
            "memory_revision_before_gate": revision_before,
            "memory_revision_after_gate": (revision_fn or memory_revision)(),
            "context_engineer_woken": False,
        }

    revision_after = (revision_fn or memory_revision)()
    all_docs = docs if docs is not None else load_all_docs()
    cps = checkpoints if checkpoints is not None else checkpoint_docs()
    phase = _phase_of(registry, task_scope.get("project_id")) if task_scope["type"] == "project" else None
    task_tokens = _task_tokens(request)
    evidence_bundle, _ev_errors = load_authority_evidence()

    filters = [
        "finding_gate_handoff",
        "strict_scope_filter",
        "lifecycle_active_only",
        "verification_filter",
        "archived_project_exclusion",
        "role_profile_loaded",
        "project_phase_applicability",
        "authority_presence",
        "review_needed_not_silent_authority",
        "case_activation_default_off",
        "context_budget",
    ]

    rules, facts, cases = [], [], []
    conflicts = []
    pollution = 0
    for doc in all_docs:
        if _pollutes(doc, task_scope):
            continue
        if not scope_allows(doc, task_scope):
            continue
        kind = doc.get("_kind")
        if kind == "anchor":
            continue
        pid = object_scope(doc).get("project_id")
        if pid and _project_archived(registry, pid):
            continue
        if kind not in ("rule", "current_fact", "case"):
            continue
        status = doc.get("status", ACTIVE_STATUS)
        if status in {"superseded", "revoked"}:
            continue
        ver = doc.get("verification")
        if status == "review_needed" or ver == "conflicted":
            refs = [r for r in (doc.get("authority_refs") or doc.get("source_refs") or []) if r]
            if not refs:
                refs = [doc.get("id") or "unknown"]
            conflicts.append({
                "id": f"review:{doc.get('id')}",
                "summary": f"{doc.get('id')} is {status}/{ver}; unresolved, not selected as authority",
                "refs": refs,
                "source": kind,
            })
            continue
        if status != ACTIVE_STATUS:
            continue
        if ver not in ADMITTED_VERIFICATION:
            continue
        if not _phase_allows(doc, phase):
            continue
        body = (doc.get("statement") or "") + " " + (doc.get("title") or "")
        if kind == "rule":
            appl = (doc.get("applicability") or {}).get("semantic") or {}
            body += " " + " ".join(appl.get("applicable_when") or [])
        need_rel = relevance_required(policy, kind)
        if need_rel and not (toks(body) & task_tokens):
            continue
        if kind == "rule":
            rules.append(doc)
        elif kind == "current_fact":
            facts.append(doc)
        else:
            cases.append(doc)

    rules.sort(key=lambda d: role_rank(d, role))
    facts.sort(key=lambda d: role_rank(d, role))
    rules = rules[:kind_limit(policy, "rule", limit)]
    facts = facts[:kind_limit(policy, "current_fact", limit)]

    rule_candidates = []
    for doc in rules:
        status = doc.get("status")
        if not _authority_ok(doc, evidence_bundle):
            status = "review_needed"
            refs = [r for r in (doc.get("authority_refs") or []) if r] or [doc["id"]]
            conflicts.append({
                "id": f"authority:{doc['id']}",
                "summary": f"{doc['id']} authority is unresolved; not silent authority selection",
                "refs": refs,
                "source": "rule",
            })
        item = {"id": doc["id"], "modality": doc["modality"], "status": status}
        item.update(_opt_summary(doc.get("title") or doc.get("statement")))
        rule_candidates.append(item)

    fact_candidates = []
    for doc in facts:
        item = {"id": doc["id"], "status": doc.get("status")}
        item.update(_opt_summary(doc.get("title") or doc.get("statement")))
        fact_candidates.append(item)

    case_allowed, case_trigger = _case_search_allowed(request, profile)
    case_candidates = []
    if case_allowed:
        filters = [f if f != "case_activation_default_off" else "case_activation_scenario_match"
                   for f in filters]
        if "case_hard_eligibility_gate" not in filters:
            filters.append("case_hard_eligibility_gate")
        admitted = []
        for case in cases:
            pid = object_scope(case).get("project_id")
            if pid and _project_archived(registry, pid):
                continue
            if case.get("status") != ACTIVE_STATUS:
                continue
            if case.get("verification") not in ADMITTED_VERIFICATION:
                continue
            if not _scenario_match(case, task_tokens):
                continue
            admitted.append(case)
        cap = min(2, kind_limit(policy, "case", 2))
        for case in admitted[:cap]:
            item = {"id": case["id"]}
            item.update(_opt_summary(case.get("title") or case.get("why_it_matters")))
            case_candidates.append(item)

    include_team_conflicts = wants_team_conflicts(policy)
    cp_conflicts, _blockers = collect_conflicts(task_scope, include_team=include_team_conflicts)
    for c in cp_conflicts:
        refs = c.get("refs") or []
        if not refs:
            continue
        conflicts.append({
            "id": c["id"],
            "summary": c.get("summary") or c["id"],
            "refs": refs,
            "source": c.get("source"),
        })

    checkpoint_entries = []
    want_team = task_scope["type"] != "project" or wants_team_posture(policy) or wants_team_slice(policy)
    if want_team:
        for cp in cps:
            if cp.get("scope", {}).get("type") != "team":
                continue
            if wants_team_posture(policy):
                for entry in posture_entries(cp):
                    mapped = dict(entry)
                    mapped["section"] = "confirmed"
                    checkpoint_entries.append(_as_checkpoint_candidate(mapped))
            slice_ok = task_scope["type"] != "project" or wants_team_slice(policy)
            if slice_ok:
                rel = relevance_required(policy, "checkpoint")
                checkpoint_entries.extend(
                    _as_checkpoint_candidate(e) for e in
                    _checkpoint_entries(cp, "team", task_tokens, rel))
    want_project = task_scope["type"] == "project" and wants_project_slice(policy)
    if want_project and task_scope["type"] == "project":
        pcp = next((c for c in cps
                    if c.get("scope", {}).get("type") == "project"
                    and c.get("scope", {}).get("project_id") == task_scope["project_id"]), None)
        if pcp is None:
            from cutil import PROJECTS, doc_at
            pcp = doc_at(PROJECTS / task_scope["project_id"] / "checkpoint.yaml")
        rel = relevance_required(policy, "checkpoint")
        checkpoint_entries.extend(
            _as_checkpoint_candidate(e) for e in
            _checkpoint_entries(pcp, task_scope["project_id"], task_tokens, rel))

    # de-dupe checkpoint entries by (checkpoint, section, namespaced id)
    seen_cp = set()
    unique_cp = []
    for entry in checkpoint_entries:
        key = (entry.get("checkpoint"), entry.get("section"), entry.get("id"))
        if key in seen_cp:
            continue
        seen_cp.add(key)
        unique_cp.append(entry)
    checkpoint_entries = unique_cp
    candidate_ids = [e.get("id") for e in checkpoint_entries if e.get("id")]
    if len(candidate_ids) != len(set(candidate_ids)):
        dupes = sorted({i for i in candidate_ids if candidate_ids.count(i) > 1})
        raise ValueError(
            "ambiguous checkpoint_entry candidate ids after namespacing "
            f"(last-wins is forbidden): {dupes}"
        )

    anchors = []
    if task_scope["type"] == "project":
        pid = task_scope["project_id"]
        digest = anchor_digest(pid)
        item = {"id": f"anchor:{pid}", "ref": f"project://{pid}/project"}
        item.update(_opt_summary(digest.get("mission")))
        anchors.append(item)
    elif task_scope["type"] == "cross_project":
        for pid in task_scope.get("projects") or []:
            digest = anchor_digest(pid)
            item = {"id": f"anchor:{pid}", "ref": f"project://{pid}/project"}
            item.update(_opt_summary(digest.get("mission")))
            anchors.append(item)

    admitted_docs = rules + facts + [
        d for d in all_docs if d.get("_kind") == "case"
        and d.get("id") in {c["id"] for c in case_candidates}
    ]
    evidence = _collect_evidence(admitted_docs, checkpoint_entries)

    # conflict de-dupe
    seen_conf = set()
    unique_conf = []
    for c in conflicts:
        if c["id"] in seen_conf:
            continue
        seen_conf.add(c["id"])
        unique_conf.append(c)
    unique_conf.sort(key=lambda c: c["id"])

    pollution = 0
    for doc in rules + facts:
        if _pollutes(doc, task_scope):
            pollution += 1
    for cand in case_candidates:
        src = next((d for d in cases if d.get("id") == cand["id"]), None)
        if src and _pollutes(src, task_scope):
            pollution += 1
    for fdoc in gate.get("relevant_open_findings") or []:
        if _pollutes(_finding_as_doc(fdoc), task_scope):
            pollution += 1

    candidates = {
        "anchor": anchors,
        "checkpoint_entries": checkpoint_entries,
        "rules": rule_candidates,
        "facts": fact_candidates,
        "cases": case_candidates,
        "evidence": evidence,
        "conflicts": unique_conf,
    }
    plan = {
        "schema_version": "1.1",
        "kind": "context_plan",
        "plan_id": _plan_id(request, task_scope, revision_after),
        "task_ref": request["task_ref"],
        "role": role,
        "scope": task_scope,
        "hard_filters_applied": filters,
        "case_search": {"allowed": bool(case_allowed)},
        "candidates": candidates,
        "semantic_jobs": _semantic_jobs(candidates, case_allowed),
    }
    schema_errors = _validate("context-handoff/context-plan.schema.json", plan)
    if schema_errors:
        raise ValueError("PLAN failed context_plan schema: " + "; ".join(schema_errors[:8]))
    return {
        "status": "PLAN_READY",
        "plan": plan,
        "llm_called": False,
        "scope_pollution": pollution,
        "compatibility_check": compat,
        "finding_gate": gate,
        "escalation": gate.get("escalation") or {"required": False},
        "memory_revision_before_gate": revision_before,
        "memory_revision_after_gate": revision_after,
        "built_from": compute_built_from(request) if revision_fn is None else {
            "task_fingerprint": fingerprint_from_request(request),
            "memory_revision": revision_after,
            "registry_revision": compute_built_from(request)["registry_revision"],
            "role_profile_revision": compute_built_from(request)["role_profile_revision"],
        },
        "context_engineer_woken": False,
        "case_trigger": case_trigger,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="T01 deterministic handoff PLAN")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("compatibility", help="prove frozen T00 can host Finding Gate")
    plan = sub.add_parser("plan", help="build a context_plan from a request JSON file")
    plan.add_argument("--request-file", required=True)
    args = parser.parse_args()
    if args.command == "compatibility":
        report = compatibility_check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2
    request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    result = prepare_handoff_plan(request)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PLAN_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
