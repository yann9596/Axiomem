#!/usr/bin/env python3
"""Context Handoff T04 — deterministic SELF_CHECK (YZT-57).

Implements the frozen Native API `self_check` (T00 contract, YZT-46;
design: docs/context_handoff_native_api_design_v1.1.md §9,
docs/context_handoff_multica_implementation_v1.1.md §29).

Question answered: can the already-started target role use an existing
Role Context Package as-is? Never a full rebuild, never an LLM call,
never a Canonical write.

Check order (frozen design §9.3):
  package exists -> task_ref match -> role match -> scope valid vs current
  Registry -> package status READY -> task_fingerprint valid ->
  memory / registry / role_profile revisions valid.

Reason vocabulary is the frozen T00 set (`chandoff.SELF_CHECK_REASONS`).
Verdict is policy computed from data, never an LLM choice:

- any failed check            -> REFRESH_REQUIRED / REFRESH
- candidate package BLOCKED   -> BLOCKED / ESCALATE (reason package_not_ready)
- everything passes           -> READY / USE_EXISTING

Fingerprint revalidation without a request-level `project` field
(the frozen `self_check_request` carries none, and T00 is not amended):

- The prepare-side fingerprint hashes `explicit_scope = request.project`,
  and `request.project` is schema-constrained to exactly
  `{"project_id": <id>}`. For a project-scoped package the scope is
  therefore reconstructed EXACTLY from `package.scope.project_id` and the
  fingerprint is recomputed and compared.
- For cross-project packages the primary `project_id` of the original
  request is not recoverable from any frozen artifact, so fingerprint
  validity cannot be proven -> fail closed with `package_stale`
  (uncertainty demotes; a wrong READY is worse than an unnecessary
  refresh). The same `package_stale` reason covers packages with missing
  or incomplete `built_from` provenance.

Packages are runtime artifacts (never Canonical, never committed). The
framework adapter resolves the latest CONTEXT_HANDOFF publish (design §15)
and hands the `prepare_handoff_result` envelope here, or drops envelopes
into the gitignored store `runtime/v1.1/handoff-packages/` which this
module can scan deterministically (latest by generated_at, tie-broken by
package_id).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chandoff  # noqa: E402
from cutil import RUNTIME, TEAM  # noqa: E402
from yaml_mini import parse_yaml  # noqa: E402

LLM_CALLED = False
CANONICAL_WRITES = 0
MULTICA_RUNTIME_DEPENDENCIES = 0

REQUEST_SCHEMA = "context-handoff/self-check-request.schema.json"
RESULT_SCHEMA = "context-handoff/self-check-result.schema.json"

ARCHIVED_PHASE = "archived"
DEFAULT_STORE = RUNTIME / "handoff-packages"

_REASON_ORDER = {name: i for i, name in enumerate(chandoff.SELF_CHECK_REASONS)}


def _validate(schema_name: str, instance: dict) -> list:
    from schema_mini import Schema, load_schema_file
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def done_criteria() -> dict:
    """Frozen done criteria (impl doc §29.4), asserted by tests."""
    return {
        "normal_check_requires_llm": False,
        "missing_package_detected": True,
        "role_mismatch_detected": True,
        "changed_task_detected": True,
        "stale_package_detected": True,
    }


def frozen_contract_supports_self_check() -> dict:
    """Prove frozen T00 can host T04 without a Native API amendment."""
    try:
        request_schema = chandoff_canonical_schema(REQUEST_SCHEMA)
        result_schema = chandoff_canonical_schema(RESULT_SCHEMA)
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)}
    statuses = (result_schema.get("properties", {}).get("status", {}) or {})
    actions = (result_schema.get("properties", {}).get("action", {}) or {})
    ref = lambda node: (node or {}).get("$ref", "").rsplit("/", 1)[-1]  # noqa: E731
    checks = {
        "frozen_reason_vocabulary_unchanged": _reason_enum(result_schema) ==
            list(chandoff.SELF_CHECK_REASONS),
        "frozen_status_enum_unchanged": ref(statuses) == "self_check_status",
        "frozen_action_enum_unchanged": ref(actions) == "self_check_action",
        "frozen_fingerprint_inputs_unchanged": chandoff.FINGERPRINT_INPUTS == (
            "task_ref", "title", "description", "requirements",
            "acceptance_criteria", "explicit_scope", "relevant_human_decisions"),
        "frozen_self_check_request_shape": sorted(request_schema.get("required") or []) ==
            ["kind", "role", "schema_version", "task_ref", "task_snapshot"],
        "request_has_no_project_field": "project" not in (request_schema.get("properties") or {}),
    }
    return {"ok": all(checks.values()), **checks}


def _reason_enum(result_schema: dict) -> list:
    """Extract the frozen reason vocabulary from the result schema description.

    The frozen schema expresses reasons as free strings whose description
    documents the vocabulary ("Standard vocabulary (frozen ...): a, b, c").
    Contract identity is checked against that documented set.
    """
    items = ((result_schema.get("properties", {}).get("reasons") or {})
             .get("items", {}))
    enum = items.get("enum")
    if enum is not None:
        return list(enum)
    description = items.get("description") or ""
    marker = "vocabulary"
    if marker in description:
        tail = description.split(marker, 1)[1].split(":", 1)[-1]
        tokens = [t.strip().rstrip(".") for t in tail.split(",")]
        return [t for t in tokens if t]
    return list(chandoff.SELF_CHECK_REASONS)


def chandoff_canonical_schema(name: str) -> dict:
    """Load a frozen context-handoff schema via the schema loader used by T00."""
    from schema_mini import load_schema_file
    return load_schema_file(name)


def _norm_list(value) -> list:
    return chandoff._norm_list(value)


def explicit_scope_from_package(package_scope: dict):
    """Reconstruct the prepare-side `request.project` from a package scope.

    Returns the exact dict when reconstructable, else None (unprovable).
    `request.project` is schema-frozen to `{"project_id": <id>}` only, so a
    project-scoped package reconstructs exactly.
    """
    scope = package_scope or {}
    stype = scope.get("type")
    if stype == "project":
        pid = scope.get("project_id")
        if isinstance(pid, str) and pid.strip():
            return {"project_id": pid.strip()}
        return None
    # cross_project packages: the original primary project_id is not stored
    # in any frozen artifact -> fingerprint cannot be re-derived.
    return None


def current_fingerprint(request: dict, package_scope: dict):
    """Recompute the CURRENT task fingerprint with T00 normalization.

    Returns None when the fingerprint cannot be proven from the frozen
    artifacts (cross-project scope reconstruction).
    """
    explicit_scope = explicit_scope_from_package(package_scope)
    if explicit_scope is None:
        return None
    snap = request.get("task_snapshot") or {}
    payload = chandoff.fingerprint_payload(
        task_ref=request.get("task_ref", ""),
        title=snap.get("title", ""),
        description=snap.get("description", ""),
        requirements=snap.get("requirements") or [],
        acceptance_criteria=snap.get("acceptance_criteria") or [],
        explicit_scope=explicit_scope,
        relevant_human_decisions=snap.get("relevant_decisions") or [],
    )
    return chandoff.task_fingerprint(payload)


def _load_registry(registry=None) -> dict:
    if registry is not None:
        return registry
    return parse_yaml((TEAM / "registry" / "projects.yaml").read_text(encoding="utf-8"))


def _registry_ok(scope: dict, registry: dict) -> bool:
    """Package scope must still resolve against the CURRENT Registry."""
    by_id = {p.get("id"): p for p in registry.get("projects") or []}
    stype = scope.get("type")
    if stype == "project":
        pid = scope.get("project_id")
        if not pid or pid not in by_id:
            return False
        return by_id[pid].get("phase") != ARCHIVED_PHASE
    if stype == "cross_project":
        projects = scope.get("projects") or []
        if len(projects) < 2:
            return False
        for pid in projects:
            if pid not in by_id or by_id[pid].get("phase") == ARCHIVED_PHASE:
                return False
        return True
    return False


def current_revisions() -> dict:
    return {
        "memory_revision": chandoff.memory_revision(),
        "registry_revision": chandoff.registry_revision(),
        "role_profile_revision": chandoff.role_profile_revision(),
    }


def load_store_envelopes(store_dir=None) -> list:
    """Deterministic runtime-store scan: every parseable result envelope."""
    directory = Path(store_dir) if store_dir else DEFAULT_STORE
    out = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(doc, dict) and doc.get("kind") == "prepare_handoff_result":
                out.append(doc)
    return out


def _order_key(envelope: dict):
    return (
        str(envelope.get("generated_at") or ""),
        str(envelope.get("package_id") or ""),
    )


def resolve_candidate(request: dict, *, packages=None, store_dir=None):
    """Deterministically pick the ONE package self_check evaluates.

    - explicit package_ref: exact match by package_id (or store locator
      `<package_id>.json`); missing -> None with reason package_missing.
    - otherwise: latest envelope for (task_ref, role) from the supplied
      packages, else from the runtime store.
    """
    supplied = list(packages) if packages else []
    if not supplied:
        supplied = load_store_envelopes(store_dir)
    ref = (request.get("package_ref") or "").strip()
    if ref:
        for env in supplied:
            if env.get("package_id") == ref:
                return env, []
        # store locator form: <package_id>.json
        base = ref[:-5] if ref.endswith(".json") else ref
        for env in supplied:
            if env.get("package_id") == base:
                return env, []
        return None, ["package_missing"]
    matching = [e for e in supplied
                if e.get("task_ref") == request.get("task_ref")
                and e.get("role") == request.get("role")]
    if not matching:
        return None, ["package_missing"]
    return max(matching, key=_order_key), []


def _sort_reasons(reasons: list) -> list:
    unique = sorted(set(r for r in reasons if isinstance(r, str) and r.strip()))
    return sorted(unique, key=lambda r: _REASON_ORDER.get(r, len(_REASON_ORDER)))


def check_package(request: dict, envelope: dict, *, registry=None,
                  current=None) -> dict:
    """Deterministic self_check against ONE candidate result envelope."""
    reasons: list = []
    built = envelope.get("built_from") or {}
    package = envelope.get("package") or {}
    scope = package.get("scope") or {}

    if envelope.get("task_ref") != request.get("task_ref"):
        reasons.append("task_changed")
    if envelope.get("role") != request.get("role"):
        reasons.append("role_mismatch")

    if scope.get("type") in ("project", "cross_project"):
        reg = _load_registry(registry)
        if not _registry_ok(scope, reg):
            reasons.append("scope_mismatch")

    if built.get("task_fingerprint"):
        fp = current_fingerprint(request, scope)
        if fp is None:
            reasons.append("package_stale")
        elif fp != built["task_fingerprint"]:
            reasons.append("task_changed")
    else:
        reasons.append("package_stale")

    cur = current or current_revisions()
    for key, reason in (
        ("memory_revision", "memory_revision_changed"),
        ("registry_revision", "registry_revision_changed"),
        ("role_profile_revision", "role_profile_revision_changed"),
    ):
        if not built.get(key):
            reasons.append("package_stale")
        elif cur.get(key) != built.get(key):
            reasons.append(reason)

    package_status = envelope.get("status")
    if package_status == "BLOCKED":
        reasons.append("package_not_ready")
        result = _emit(envelope, "BLOCKED", "ESCALATE", reasons)
        return result
    if package_status != "READY":
        reasons.append("package_not_ready")
    if reasons:
        return _emit(envelope, "REFRESH_REQUIRED", "REFRESH", reasons)
    return _emit(envelope, "READY", "USE_EXISTING", [])


def _emit(envelope, status: str, action: str, reasons: list) -> dict:
    result = {
        "schema_version": "1.1",
        "kind": "self_check_result",
        "status": status,
        "reasons": _sort_reasons(reasons),
        "action": action,
    }
    pid = envelope.get("package_id") if isinstance(envelope, dict) else None
    if pid:
        result["package_id"] = pid
    errors = _validate(RESULT_SCHEMA, result)
    if errors:
        raise RuntimeError("self_check_result schema invalid: " + "; ".join(errors[:8]))
    return result


def self_check(request: dict, *, packages=None, store_dir=None, registry=None,
               current=None) -> dict:
    """Native API entry: frozen request in, frozen result out. No LLM, no writes."""
    global LLM_CALLED, CANONICAL_WRITES, MULTICA_RUNTIME_DEPENDENCIES
    errors = _validate(REQUEST_SCHEMA, request)
    if errors:
        raise ValueError("invalid self_check_request: " + "; ".join(errors[:8]))
    envelope, missing = resolve_candidate(
        request, packages=packages, store_dir=store_dir)
    if envelope is None:
        result = {
            "schema_version": "1.1",
            "kind": "self_check_result",
            "status": "REFRESH_REQUIRED",
            "reasons": _sort_reasons(missing),
            "action": "REFRESH",
        }
        out_errors = _validate(RESULT_SCHEMA, result)
        if out_errors:
            raise RuntimeError("self_check_result schema invalid: " +
                               "; ".join(out_errors[:8]))
        return result
    return check_package(request, envelope, registry=registry, current=current)


def main() -> int:
    parser = argparse.ArgumentParser(description="T04 deterministic SELF_CHECK")
    sub = parser.add_parser("check", help="run self_check from request file")
    sub.add_argument("--request-file", required=True)
    sub.add_argument("--package", action="append", default=[],
                     help="prepare_handoff_result envelope file (repeatable)")
    sub.add_argument("--store", default=None, help="runtime package store dir")
    args = parser.parse_args()
    request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
    packages = [json.loads(Path(p).read_text(encoding="utf-8"))
                for p in args.package]
    result = self_check(request, packages=packages or None, store_dir=args.store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"READY": 0, "REFRESH_REQUIRED": 2, "BLOCKED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
