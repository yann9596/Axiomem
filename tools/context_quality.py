#!/usr/bin/env python3
"""Deterministic UTF-8 context budgets; no truncation, authority, or dispatch.

Policy comes only from the context repository, never from request.options.
The report is INTERNAL: frozen public schemas/status vocabularies are unchanged.
Bytes are not model tokens. A failure identifies the source needing curation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from cutil import ROOT

POLICY_PATH = "team-context/policies/context-quality.json"
LIMIT_KEYS = frozenset({
    "plan_bytes", "package_bytes", "envelope_bytes", "transport_bytes",
    "fact_bytes", "rule_bytes", "checkpoint_entry_bytes", "checkpoint_entries",
    "source_refs", "source_refs_bytes", "current_facts_bytes",
    "checkpoint_slices_bytes", "candidate_source_bytes",
})


class QualityPolicyError(ValueError):
    """Missing/invalid trusted policy must not fall back to unlimited budgets."""


class ContextBudgetError(ValueError):
    def __init__(self, report: dict):
        self.report = report
        super().__init__(diagnostic(report))


def encoded(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def byte_size(value: Any) -> int:
    return len(encoded(value))


def load_policy(root: Path | None = None) -> dict:
    path = (root or ROOT) / POLICY_PATH
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QualityPolicyError(f"CONTEXT_QUALITY_POLICY_INVALID: {path}: {exc}") from exc
    if not isinstance(policy, dict) or set(policy) != {"schema_version", "policy_version", "mode", "limits"}:
        raise QualityPolicyError("CONTEXT_QUALITY_POLICY_INVALID: unexpected policy fields")
    if policy["schema_version"] != "context-quality/1" or policy["mode"] not in {"enforce", "observe"}:
        raise QualityPolicyError("CONTEXT_QUALITY_POLICY_INVALID: unsupported version/mode")
    if not isinstance(policy["policy_version"], str) or not policy["policy_version"].strip():
        raise QualityPolicyError("CONTEXT_QUALITY_POLICY_INVALID: policy_version is required")
    limits = policy["limits"]
    if not isinstance(limits, dict) or set(limits) != LIMIT_KEYS:
        raise QualityPolicyError("CONTEXT_QUALITY_POLICY_INVALID: all named limits are required")
    if any(type(v) is not int or v <= 0 for v in limits.values()):
        raise QualityPolicyError("CONTEXT_QUALITY_POLICY_INVALID: limits must be positive integers")
    return policy


def policy_revision(policy: dict) -> str:
    return "sha256:" + hashlib.sha256(encoded(policy)).hexdigest()


def _reference(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("id") or item.get("ref") or "")[:240]


def assess(stage: str, value: Any, *, source_docs: list | None = None,
           root: Path | None = None) -> dict:
    """Measure exact structured bytes. Nothing is mutated or removed.

    `stage` is an internal boundary name, not a new public protocol concept.
    PLAN additionally inspects the admitted source objects before semantic work.
    """
    policy = load_policy(root)
    limits, metrics, violations = policy["limits"], {}, []

    def check(key: str, actual: int, path: str, item: Any = None):
        metrics[key] = max(metrics.get(key, 0), actual)
        if actual > limits[key]:
            violations.append({"limit_key": key, "path": path,
                               "object_ref": _reference(item), "actual": actual,
                               "limit": limits[key],
                               "unit": "count" if key in {"source_refs", "checkpoint_entries"} else "UTF-8 bytes"})

    def checkpoints(entries: list, path: str):
        check("checkpoint_entries", len(entries), path)
        check("checkpoint_slices_bytes", byte_size(entries), path)
        for i, item in enumerate(entries):
            check("checkpoint_entry_bytes", byte_size(item), f"{path}[{i}]", item)

    def package(pkg: dict):
        check("package_bytes", byte_size(pkg), "$.package")
        for field, key in (("rules", "rule_bytes"), ("current_facts", "fact_bytes")):
            for i, item in enumerate(pkg.get(field) or []):
                check(key, byte_size(item), f"$.package.{field}[{i}]", item)
        check("current_facts_bytes", byte_size(pkg.get("current_facts") or []), "$.package.current_facts")
        checkpoints(list(pkg.get("team_state_slice") or []) + list(pkg.get("project_state_slice") or []),
                    "$.package.checkpoint_slices")
        refs = list(pkg.get("source_refs") or [])
        check("source_refs", len(refs), "$.package.source_refs")
        check("source_refs_bytes", byte_size(refs), "$.package.source_refs")
        metrics["duplicate_top_level_refs"] = len(refs) - len(set(encoded(r) for r in refs))

    if stage == "plan":
        check("plan_bytes", byte_size(value), "$.plan")
        candidates = value.get("candidates") or {}
        checkpoints(candidates.get("checkpoint_entries") or [], "$.plan.candidates.checkpoint_entries")
        admitted = {r.get("id") for field in ("rules", "facts", "cases")
                    for r in candidates.get(field) or []}
        for doc in source_docs or []:
            if doc.get("id") in admitted:
                content = {k: v for k, v in doc.items() if not k.startswith("_")}
                check("candidate_source_bytes", byte_size(content), "$.plan.source_objects", doc)
    elif stage == "package":
        package(value)
    elif stage == "envelope":
        check("envelope_bytes", byte_size(value), "$.envelope")
        package(value.get("package") or {})
    elif stage == "transport":
        if not isinstance(value, str):
            raise TypeError("transport must be the actual text sent to the platform")
        check("transport_bytes", len(value.encode("utf-8")), "$.transport")
    else:
        raise ValueError(f"unknown quality boundary {stage!r}")
    return {"schema_version": "context-quality-report/1", "boundary": stage,
            "policy_version": policy["policy_version"], "policy_revision": policy_revision(policy),
            "mode": policy["mode"], "ok": not violations,
            "blocked": bool(violations and policy["mode"] == "enforce"),
            "metrics": metrics, "violations": violations}


def diagnostic(report: dict) -> str:
    rows = [f"{r['limit_key']} {r['object_ref'] or r['path']}: {r['actual']} > {r['limit']} {r['unit']}"
            for r in report.get("violations", [])[:6]]
    return "CONTEXT_BUDGET_OVERFLOW: " + "; ".join(rows)


def require(stage: str, value: Any, **kwargs) -> dict:
    report = assess(stage, value, **kwargs)
    if report["blocked"]:
        raise ContextBudgetError(report)
    return report


def guard_final_envelope(envelope: dict, **kwargs) -> tuple[dict, dict]:
    """Recheck AFTER artifact enrichment, BEFORE publication/integrity sealing.

    Return a copy and an internal report. Never truncate evidence or replace an
    earlier blocking reason. The public result vocabulary/schema is unchanged.
    """
    result = copy.deepcopy(envelope)
    try:
        report = assess("envelope", result, **kwargs)
        reason = diagnostic(report) if report["blocked"] else None
        code = "CONTEXT_BUDGET_OVERFLOW"
    except QualityPolicyError as exc:
        report = {"ok": False, "blocked": True, "reason": str(exc)}
        reason, code = str(exc), "CONTEXT_QUALITY_POLICY_INVALID"
    if report["blocked"]:
        if result.get("status") != "BLOCKED":
            result["status"] = "BLOCKED"
            result["escalation"] = {"required": True, "reason": reason}
        blockers = result["package"]["blocked_by"]
        if code not in blockers:
            blockers.append(code)
    return result, report
