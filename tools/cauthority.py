#!/usr/bin/env python3
"""Rule authority resolution + claim-support / scope-coverage (YZT-42).

Resolvability is not sufficient. Each authority_ref must have parseable
claim-support and scope-coverage evidence. Missing evidence is FAIL, never
an implicit PASS.
"""
from __future__ import annotations

import re
from pathlib import Path

from cutil import ROOT, TEAM, PROJECTS, doc_at
from schema_mini import Schema, load_schema_file


EVIDENCE_PATH = ROOT / "migration" / "authority-evidence.yaml"
ISSUE_RE = re.compile(r"^multica://issue/(YZT-\d+|[0-9a-fA-F-]{36})$")
REPO_RE = re.compile(r"^repo://([^@/]+)(?:@([^/]+))?(?:/(.*))?$")
DOC_RE = re.compile(r"^doc://([^/]+)/(.+?)(?:@([^/@]+))?$")
ADR_RE = re.compile(r"^adr://(\d+)$")
PROJECT_CTX_RE = re.compile(r"^multica://project-context/([^/]+)$")
REGISTRY_RE = re.compile(r"^registry://([^/]+)$")

PRODUCT_REPOS = {
    "web-imagegen": Path(r"D:\AI\projects\opencode-web-imagegen"),
    "teachers-app1": Path(r"D:\AI\projects\teachers-app1"),
    "app1": Path(r"D:\AI\projects\teachers-app1"),
}


def _registry_ids() -> set[str]:
    try:
        return {p["id"] for p in doc_at(TEAM / "registry" / "projects.yaml")["projects"]}
    except (OSError, KeyError, TypeError):
        return set()


def adr_file(num: str) -> Path | None:
    d = PRODUCT_REPOS["web-imagegen"] / "docs" / "adr"
    if not d.exists():
        return None
    for p in d.glob("*.md"):
        if p.name.startswith(num):
            return p
    return None


def authority_resolves(ref: str) -> tuple[bool, str]:
    """Return (ok, reason). Never auto-pass unknown URI families."""
    if not ref or not isinstance(ref, str):
        return False, "empty authority_ref"
    m = PROJECT_CTX_RE.fullmatch(ref)
    if m:
        key = m.group(1)
        if key in _registry_ids() and (PROJECTS / key).is_dir():
            return True, f"project-context dir exists: {key}"
        return False, f"project-context {key!r} is not a registered project directory"
    if ISSUE_RE.fullmatch(ref):
        return True, "issue ref matches identifier grammar (artifact support still required)"
    m = ADR_RE.fullmatch(ref)
    if m:
        found = adr_file(m.group(1))
        if found is None:
            return False, f"adr://{m.group(1)} file not found"
        return True, f"adr file {found.name}"
    m = DOC_RE.fullmatch(ref)
    if m:
        repo, rel, _rev = m.group(1), m.group(2), m.group(3)
        base = PRODUCT_REPOS.get(repo)
        if base is None:
            return False, f"doc:// unknown repo {repo!r}"
        target = base / rel
        if not target.exists():
            return False, f"doc:// path missing: {repo}/{rel}"
        return True, "doc path exists"
    m = REGISTRY_RE.fullmatch(ref)
    if m:
        if m.group(1) in _registry_ids():
            return True, "registry project id"
        return False, f"registry://{m.group(1)} not in Project Registry"
    m = REPO_RE.fullmatch(ref)
    if m:
        repo, _rev, rel = m.group(1), m.group(2), m.group(3)
        if repo in ("multica-memory", "context"):
            target = ROOT if not rel else ROOT / rel
            if target.exists():
                return True, "repo path exists in context repo"
            return False, f"repo://{repo} path missing: {rel}"
        base = PRODUCT_REPOS.get(repo)
        if base is None:
            return False, f"repo:// unknown repo {repo!r}"
        target = base if not rel else base / rel
        if target.exists():
            return True, "repo path exists in product repo"
        return False, f"repo://{repo} path missing: {rel}"
    return False, f"unrecognized authority_ref family: {ref}"


def load_authority_evidence(path: Path | None = None) -> tuple[dict | None, list[str]]:
    p = path or EVIDENCE_PATH
    if not p.exists():
        return None, ["migration/authority-evidence.yaml"]
    doc = doc_at(p)
    schema = load_schema_file("authority-claim-evidence.schema.json")
    errors = Schema(schema, schema).validate(doc, path="authority-evidence")
    if errors:
        return None, errors
    return doc, []


def _claims_for(evidence: dict, rule_id: str, ref: str) -> list[dict]:
    return [c for c in (evidence.get("claims") or [])
            if c.get("rule_id") == rule_id and c.get("authority_ref") == ref]


def evaluate_rule_authority(rule: dict, evidence: dict | None) -> dict:
    rid = rule.get("id")
    refs = rule.get("authority_refs") or []
    missing: list[str] = []
    errors: list[str] = []
    resolved = []
    if not refs:
        errors.append(f"rule:{rid}: no authority_refs")
    for ref in refs:
        ok, reason = authority_resolves(ref)
        resolved.append({"ref": ref, "resolves": ok, "reason": reason})
        if not ok:
            errors.append(f"rule:{rid}: authority_ref unresolved: {ref} ({reason})")
        if evidence is None:
            missing.append(f"claim-support:{rid}:{ref}")
            missing.append(f"scope-coverage:{rid}:{ref}")
            continue
        claims = _claims_for(evidence, rid, ref)
        if not claims:
            missing.append(f"claim-support:{rid}:{ref}")
            missing.append(f"scope-coverage:{rid}:{ref}")
            errors.append(f"rule:{rid}: no claim-support/scope-coverage evidence for {ref}")
            continue
        for claim in claims:
            support = claim.get("claim_support") or {}
            coverage = claim.get("scope_coverage") or {}
            if not support.get("supported"):
                errors.append(f"rule:{rid}: claim-support.supported is not true for {ref}")
            if not support.get("excerpt_or_pointer"):
                errors.append(f"rule:{rid}: claim-support missing excerpt_or_pointer for {ref}")
            if support.get("claim_relation") not in ("supports", "qualifies"):
                errors.append(f"rule:{rid}: claim-support.claim_relation invalid for {ref}")
            if not coverage.get("covers"):
                errors.append(f"rule:{rid}: scope-coverage.covers is not true for {ref}")
            if not coverage.get("authority_scope") or not coverage.get("rule_scope"):
                errors.append(f"rule:{rid}: scope-coverage missing authority_scope/rule_scope for {ref}")
    valid = not errors and not missing
    return {
        "rule_id": rid,
        "valid": valid,
        "resolved": resolved,
        "missing": missing,
        "errors": errors,
    }


def evaluate_all_rules(rules: list[dict], evidence_path: Path | None = None) -> dict:
    evidence, ev_errors = load_authority_evidence(evidence_path)
    missing_files = []
    if evidence is None:
        missing_files = [e for e in ev_errors if e.endswith(".yaml") or "authority-evidence" in e]
    reports = [evaluate_rule_authority(r, evidence) for r in rules]
    invalid = [r for r in reports if not r["valid"]]
    missing = []
    for r in reports:
        missing.extend(r["missing"])
    errors = list(ev_errors) if evidence is None and ev_errors else []
    for r in reports:
        errors.extend(r["errors"])
    return {
        "rule_count": len(rules),
        "invalid_count": len(invalid),
        "missing_evidence": sorted(set(missing_files + missing)),
        "errors": errors,
        "reports": reports,
        "fail_closed": evidence is None or len(invalid) > 0,
    }
