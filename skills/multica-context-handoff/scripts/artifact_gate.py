#!/usr/bin/env python3
"""Private T07 seam: Artifact Contract readiness for the shared handoff skill.

Imports U10 cartifact callables from the verified Context repository. Does
not duplicate catalog, resolver, digest, readiness, or routing logic, and
is not a Public Context API. Artifact Runtime owns ARTIFACT_READY /
ARTIFACT_NOT_READY; Frozen T00/T04 retain READY / REFRESH_REQUIRED /
BLOCKED. This module only orchestrates the check, exports through existing
package.task_evidence / package.source_refs, and maps a dependency-set
mismatch onto the frozen reason package_stale.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

EVIDENCE_DEP = "artifact_dependency"
EVIDENCE_SET = "artifact_dependency_set"
PACKAGE_STALE = "package_stale"
STORE_REQUIRED = "artifact_store_required"
REQUIREMENTS_REQUIRED = "artifact_requirements_required"
GATE_SCHEMA_INVALID = "artifact_package_schema_invalid"

REF_SCHEMES = ("multica", "adr", "doc", "repo", "registry", "project", "git")


def load_cartifact(root: Path):
    """Import the repository's U10 cartifact module. One truth store."""
    tools_dir = str(Path(root) / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import cartifact
    return cartifact


def _json_read(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def binding_from_args(args) -> dict:
    return {
        "store_file": getattr(args, "artifact_store_file", None) or None,
        "requirements_file": getattr(args, "artifact_requirements_file", None) or None,
        "review_level": getattr(args, "artifact_review_level", None) or None,
    }


def load_requirements_doc(path: str) -> dict:
    """Accept a ready-check request, a {requirements: [...]} object, or a list."""
    raw = _json_read(path)
    if isinstance(raw, list):
        return {"requirements": raw}
    if not isinstance(raw, dict):
        raise ValueError("artifact requirements file must be an object or list")
    if isinstance(raw.get("requirements"), list):
        return {
            "requirements": raw["requirements"],
            "review_level": raw.get("review_level"),
            "target_role": raw.get("target_role"),
        }
    if raw.get("kind") == EVIDENCE_SET:
        return {"requirements": list(raw.get("dependencies") or [])}
    raise ValueError(
        "artifact requirements file must carry a requirements array "
        "(or a dependency-set dependencies array)")


def load_store(cartifact, path: str):
    return cartifact._store_from_file(path)


def build_ready_request(target_role: str, requirements: list,
                        review_level: str | None = None) -> dict:
    req = {
        "schema_version": "1.0",
        "kind": "artifact_ready_check_request",
        "target_role": target_role,
        "requirements": list(requirements or []),
    }
    if review_level:
        req["review_level"] = review_level
    return req


def extract_dependency_records(package: dict | None) -> dict:
    """Read previously exported T00 task_evidence rows. No new public field."""
    deps = []
    digest = None
    for row in (package or {}).get("task_evidence") or []:
        if not isinstance(row, dict):
            continue
        kind = row.get("kind")
        if kind == EVIDENCE_DEP:
            rec = {
                "artifact_type": row.get("artifact_type") or "",
                "artifact_id": row.get("artifact_id") or "",
                "version": row.get("version") if row.get("version") is not None else "",
                "required": bool(row.get("required", True)),
            }
            if row.get("ref"):
                rec["ref"] = row["ref"]
            deps.append(rec)
        elif kind == EVIDENCE_SET:
            digest = row.get("dependency_digest") or digest
    return {"requirements": deps, "digest": digest}


def _strip_artifact_evidence(package: dict) -> None:
    package["task_evidence"] = [
        row for row in (package.get("task_evidence") or [])
        if not (isinstance(row, dict) and row.get("kind") in {EVIDENCE_DEP, EVIDENCE_SET})
    ]


def _is_grammar_ref(value) -> bool:
    if not isinstance(value, str) or " " in value:
        return False
    return any(value.startswith(scheme + "://" ) for scheme in REF_SCHEMES)


def merge_export(package: dict, exported: dict) -> dict:
    """Carry dependency records only through existing T00 surfaces."""
    _strip_artifact_evidence(package)
    package["task_evidence"] = (
        list(package.get("task_evidence") or []) + list(exported.get("task_evidence") or [])
    )
    existing = list(package.get("source_refs") or [])
    seen = set(existing)
    for ref in exported.get("source_refs") or []:
        if _is_grammar_ref(ref) and ref not in seen:
            existing.append(ref)
            seen.add(ref)
    package["source_refs"] = existing
    return package


def validate_package(package: dict) -> list:
    from schema_mini import Schema, load_schema_file
    schema = load_schema_file("context-package.schema.json")
    return Schema(schema, schema).validate(package, path="$")


def validate_result_envelope(envelope: dict) -> list:
    from schema_mini import Schema, load_schema_file
    schema = load_schema_file("context-handoff/prepare-handoff-result.schema.json")
    return Schema(schema, schema).validate(envelope, path="$")


def resolve_current_requirements(cartifact, store, previous: list) -> list:
    """Re-resolve each prior identity to the unique live version when possible."""
    current = []
    for rec in cartifact.normalize_requirements(previous):
        row = dict(rec)
        auth = cartifact.authoritative_version(
            store, rec["artifact_id"], rec.get("artifact_type") or None)
        if auth.get("ok") and auth.get("version"):
            row["version"] = auth["version"]
        current.append(row)
    return cartifact.normalize_requirements(current)


def public_ready_view(ready: dict) -> dict:
    """Exact failure rows plus status. No reinterpretation."""
    failures = list(ready.get("failures") or [])
    first = failures[0] if failures else {}
    return {
        "status": ready.get("status"),
        "blocks_handoff": bool(ready.get("blocks_handoff")),
        "checks": ready.get("checks") or {},
        "failures": failures,
        "dependency_digest": ready.get("dependency_digest"),
        "correction_owner": first.get("correction_owner"),
        "route": first.get("route"),
    }


def evaluate_ready(cartifact, store, target_role: str, requirements: list,
                   review_level: str | None = None) -> tuple[dict, dict]:
    request = build_ready_request(target_role, requirements, review_level)
    ready = cartifact.artifact_ready_check(store, request)
    exported = cartifact.export_t00_surfaces(store, requirements)
    return ready, exported


def evaluate_freshness(cartifact, store, *, previous_requirements: list,
                       previous_digest: str | None, current_requirements: list | None,
                       target_role: str, review_level: str | None = None) -> dict:
    """Compare the accepted set to the current exact set. Fail closed."""
    previous = cartifact.normalize_requirements(previous_requirements or [])
    if current_requirements is not None:
        current = cartifact.normalize_requirements(current_requirements)
    else:
        current = resolve_current_requirements(cartifact, store, previous)
    prev_digest = previous_digest or (
        cartifact.dependency_digest(previous) if previous else None)
    changed = False
    if prev_digest:
        changed = cartifact.dependency_changed(prev_digest, current)
    ready_current, exported = evaluate_ready(
        cartifact, store, target_role, current, review_level)
    ready_previous = ready_current
    if previous and cartifact.dependency_digest(previous) != cartifact.dependency_digest(current):
        changed = True
        ready_previous, _ignored = evaluate_ready(
            cartifact, store, target_role, previous, review_level)
    elif previous:
        ready_previous, _ignored = evaluate_ready(
            cartifact, store, target_role, previous, review_level)
    not_ready = (
        ready_current.get("status") != "ARTIFACT_READY"
        or ready_previous.get("status") != "ARTIFACT_READY"
    )
    stale = bool(changed or not_ready)
    return {
        "dependency_changed": bool(changed),
        "previous_digest": prev_digest,
        "current_digest": cartifact.dependency_digest(current),
        "ready_current": ready_current,
        "ready_previous": ready_previous,
        "exported": exported,
        "stale": stale,
        "reason": PACKAGE_STALE if stale else None,
        "current_requirements": current,
        "previous_requirements": previous,
    }


def apply_finalize_gate(cartifact, envelope: dict, *, store, requirements: list,
                        target_role: str, review_level: str | None = None) -> dict:
    """Run ARTIFACT_READY_CHECK after T03. Never rewrite T03 status."""
    t03 = envelope.get("status")
    ready, exported = evaluate_ready(
        cartifact, store, target_role, requirements, review_level)
    view = public_ready_view(ready)
    view["t03_status"] = t03
    view["exported_digest"] = exported.get("dependency_digest")
    view["schema_errors"] = []
    if t03 == "BLOCKED":
        view["merged"] = False
        return view
    if ready.get("status") == "ARTIFACT_READY":
        package = envelope.get("package")
        if isinstance(package, dict):
            merge_export(package, exported)
            pkg_errs = validate_package(package)
            env_errs = validate_result_envelope(envelope)
            view["schema_errors"] = pkg_errs + env_errs
            view["merged"] = not view["schema_errors"]
            if view["schema_errors"]:
                # Roll back the T00 surfaces rather than ship an invalid package.
                _strip_artifact_evidence(package)
                view["status"] = "ARTIFACT_NOT_READY"
                view["blocks_handoff"] = True
                view["failures"] = [{
                    "artifact_id": "",
                    "artifact_type": "",
                    "version": "",
                    "ref": "",
                    "reason_code": GATE_SCHEMA_INVALID,
                    "reason": view["schema_errors"][0],
                    "correction_owner": "engineering-lead",
                    "route": "lead_replan",
                }]
        else:
            view["merged"] = False
    else:
        view["merged"] = False
    return view


def overlay_selfcheck(payload: dict, freshness: dict) -> dict:
    """Map dependency mismatch / invalidation to frozen package_stale.

    T04 status is not rewritten when it is already BLOCKED. READY and
    REFRESH_REQUIRED become (or stay) REFRESH_REQUIRED with reason
    package_stale. Consequential work is not eligible until refresh.
    """
    payload = copy.deepcopy(payload)
    payload["artifact_ready"] = public_ready_view(freshness["ready_current"])
    payload["artifact_freshness"] = {
        "dependency_changed": freshness["dependency_changed"],
        "previous_digest": freshness["previous_digest"],
        "current_digest": freshness["current_digest"],
        "stale": freshness["stale"],
    }
    if not freshness["stale"]:
        return payload
    if payload.get("status") == "BLOCKED":
        reasons = list(payload.get("reasons") or [])
        if PACKAGE_STALE not in reasons:
            reasons.append(PACKAGE_STALE)
        payload["reasons"] = reasons
        return payload
    reasons = list(payload.get("reasons") or [])
    if PACKAGE_STALE not in reasons:
        reasons.append(PACKAGE_STALE)
    payload["status"] = "REFRESH_REQUIRED"
    payload["action"] = "REFRESH"
    payload["reasons"] = reasons
    payload["consequential_work"] = "stopped_until_refreshed_ready"
    payload.setdefault("refresh", {
        "instruction": (
            "run PREPARE_HANDOFF for the same task and the current role; "
            "consequential work stays stopped until the refreshed result "
            "is READY"
        ),
        "task_ref": payload.get("task_ref"),
        "target_role": payload.get("current_role"),
    })
    return payload
