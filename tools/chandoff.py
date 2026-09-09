#!/usr/bin/env python3
"""Context Handoff Native API — executable T00 contract (YZT-46).

Deterministic, framework-neutral helpers frozen by CTX-HO-00. Later handoff
tasks (PLAN, semantic compose finalize, self_check) build on these helpers.
They never call a model and never write canonical memory.

- FINGERPRINT_INPUTS / task_fingerprint / fingerprint_from_request
- memory_revision / registry_revision / role_profile_revision / compute_built_from
- validate_semantic_result (selected IDs must be a subset of PLAN candidates)
- forbidden_concept_scan / scan_handoff_contracts (framework-neutral audit)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import ROOT, TEAM, PROJECTS, doc_at  # noqa: E402
from yaml_mini import parse_yaml  # noqa: E402

# Frozen fingerprint input set (T00 contract §4). Nothing else may enter the
# hash: no discussion content, no progress notes, no caller/options/purpose,
# no parent reference.
FINGERPRINT_INPUTS = (
    "task_ref",
    "title",
    "description",
    "requirements",
    "acceptance_criteria",
    "explicit_scope",
    "relevant_human_decisions",
)

SELF_CHECK_REASONS = (
    "package_missing",
    "package_stale",
    "task_changed",
    "role_mismatch",
    "scope_mismatch",
    "package_not_ready",
    "memory_revision_changed",
    "registry_revision_changed",
    "role_profile_revision_changed",
)

# Dispatch-framework vocabulary that must never appear in the Native API or
# Core schemas. Prose documents are exempt (they name the concepts in order
# to prohibit them). The table below is scan-exempt: it exists to detect the
# vocabulary, not to use it.
# scan-exempt:begin
FORBIDDEN_PATTERNS = (
    ("squad", r"\bsquads?\b"),
    ("mention", r"\bmention"),
    ("assignee", r"\bassignees?\b"),
    ("stage", r"\bstages?\b|\bstaged\b"),
    ("autopilot", r"\bautopilot\b"),
    ("comment_routing", r"comment[\s_-]?routing"),
    ("multica_run", r"\bmultica\s+run\b"),
    ("run_identifier", r"\brun_id\b|\brun_trigger\b|\brun_count\b"),
)
# scan-exempt:end


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _norm_str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _norm_list(value) -> list:
    if not isinstance(value, list):
        return []
    return sorted({x.strip() for x in value if isinstance(x, str) and x.strip()})


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint_payload(task_ref: str, title: str, description: str,
                        requirements: list, acceptance_criteria: list,
                        explicit_scope, relevant_human_decisions: list) -> dict:
    """Build the frozen seven-key payload. Keys outside FINGERPRINT_INPUTS cannot enter."""
    return {
        "task_ref": _norm_str(task_ref),
        "title": _norm_str(title),
        "description": _norm_str(description),
        "requirements": _norm_list(requirements),
        "acceptance_criteria": _norm_list(acceptance_criteria),
        "explicit_scope": explicit_scope if isinstance(explicit_scope, dict) else {},
        "relevant_human_decisions": _norm_list(relevant_human_decisions),
    }


def task_fingerprint(payload: dict) -> str:
    return sha256_text(canonical_json(payload))


def fingerprint_from_request(request: dict) -> str:
    """Fingerprint a prepare_handoff_request-shaped dict using only the frozen inputs."""
    snap = request.get("task_snapshot") or {}
    payload = fingerprint_payload(
        task_ref=request.get("task_ref", ""),
        title=snap.get("title", ""),
        description=snap.get("description", ""),
        requirements=snap.get("requirements") or [],
        acceptance_criteria=snap.get("acceptance_criteria") or [],
        explicit_scope=request.get("project") or {},
        relevant_human_decisions=snap.get("relevant_decisions") or [],
    )
    return task_fingerprint(payload)


def _yaml_doc(path: Path):
    return parse_yaml(path.read_text(encoding="utf-8"))


def _manifest(entries: list) -> str:
    pairs = sorted([[p, c] for p, c in entries])
    return sha256_text(canonical_json(pairs))


def registry_revision() -> str:
    p = TEAM / "registry" / "projects.yaml"
    return _manifest([(p.relative_to(ROOT).as_posix(),
                       canonical_json(_yaml_doc(p)))])


def role_profile_revision() -> str:
    entries = [(p.relative_to(ROOT).as_posix(), canonical_json(_yaml_doc(p)))
               for p in sorted((TEAM / "roles").glob("*.yaml"))]
    return _manifest(entries)


def memory_revision() -> str:
    globs = [
        (PROJECTS, "*/project.yaml"),
        (TEAM / "rules", "RULE-*.yaml"),
        (PROJECTS, "*/rules/RULE-*.yaml"),
        (PROJECTS, "*/facts/FACT-*.yaml"),
        (PROJECTS, "*/cases/CASE-*.yaml"),
        (TEAM, "checkpoint.yaml"),
        (PROJECTS, "*/checkpoint.yaml"),
    ]
    paths: set = set()
    for base, pattern in globs:
        paths.update(base.glob(pattern))
    entries = [(p.relative_to(ROOT).as_posix(), canonical_json(_yaml_doc(p)))
               for p in sorted(paths)]
    return _manifest(entries)


def compute_built_from(request: dict) -> dict:
    return {
        "task_fingerprint": fingerprint_from_request(request),
        "memory_revision": memory_revision(),
        "registry_revision": registry_revision(),
        "role_profile_revision": role_profile_revision(),
    }


def validate_semantic_result(plan: dict, result: dict) -> list:
    """Deterministic subset check: every selected ID must be a PLAN candidate."""
    errors: list = []
    if plan.get("plan_id") != result.get("plan_id"):
        errors.append(f"plan_id mismatch: result {result.get('plan_id')!r} "
                      f"vs plan {plan.get('plan_id')!r}")
    cands = plan.get("candidates") or {}
    for result_key, cand_key in (
        ("selected_rule_ids", "rules"),
        ("selected_fact_ids", "facts"),
        ("selected_case_ids", "cases"),
        ("checkpoint_entry_ids", "checkpoint_entries"),
        ("conflict_ids", "conflicts"),
    ):
        allowed = {c.get("id") for c in (cands.get(cand_key) or [])}
        for sid in result.get(result_key) or []:
            if sid not in allowed:
                errors.append(f"{result_key}: {sid!r} is not a PLAN candidate "
                              f"under candidates.{cand_key}")
    return errors


def forbidden_concept_scan(text: str) -> list:
    found = []
    lowered = text.lower()
    for label, pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, lowered):
            found.append(label)
    return found


def scan_handoff_contracts() -> dict:
    """Scan the frozen handoff schemas and this module for framework vocabulary.

    When scanning this module itself, the marked scan-exempt region (the
    FORBIDDEN_PATTERNS table) is stripped first, so the detector table never
    matches itself.
    """
    targets = sorted((ROOT / "schemas" / "context-handoff").glob("*.schema.json"))
    self_path = Path(__file__).resolve()
    targets.append(self_path)
    plan_path = self_path.with_name("chandoff_plan.py")
    if plan_path.exists():
        targets.append(plan_path)
    compose_path = self_path.with_name("chandoff_compose.py")
    if compose_path.exists():
        targets.append(compose_path)
    finalize_path = self_path.with_name("chandoff_finalize.py")
    if finalize_path.exists():
        targets.append(finalize_path)
    report = {}
    for t in targets:
        text = t.read_text(encoding="utf-8")
        if t == self_path:
            text = re.sub(
                r"# scan-exempt:begin.*?# scan-exempt:end", "", text, flags=re.S)
        found = forbidden_concept_scan(text)
        if found:
            report[t.name] = found
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Context Handoff T00 contract helpers")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="framework-neutral boundary audit")
    rev = sub.add_parser("revisions", help="print frozen content revisions")
    args = parser.parse_args()
    if args.command == "scan":
        report = scan_handoff_contracts()
        print(json.dumps({"clean": not report, "violations": report},
                         ensure_ascii=False, indent=2))
        return 0 if not report else 2
    if args.command == "revisions":
        print(json.dumps({
            "memory_revision": memory_revision(),
            "registry_revision": registry_revision(),
            "role_profile_revision": role_profile_revision(),
        }, ensure_ascii=False, indent=2))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
