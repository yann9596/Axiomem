#!/usr/bin/env python3
"""Context Handoff T04 — deterministic SELF_CHECK (YZT-57).

Implements the frozen Native API `self_check` (T00 contract, YZT-46;
design: docs/context_handoff_native_api_design_v1.1.md §9,
docs/context_handoff_multica_implementation_v1.1.md §29).

Question answered: can the already-started target role use an existing
Role Context Package as-is? Never a full rebuild, never an LLM call,
never a Canonical write.

Check order (frozen design §9.3 + Approved Runtime Finding Processing
Trigger supplement §7.1 / §19):
  1. find the ONE candidate package (none -> REFRESH_REQUIRED; the Finding
     Gate never runs without a valid package: an open Finding is never used
     as Context);
  2. validate task_ref / role / scope-vs-current-Registry / task_fingerprint;
  3. when (and only when) the package scope is Registry-verified: scan
     task-associated open Findings and run the internal
     FINDING_GATE(boundary=current_role) — the T01 gate is reused verbatim
     (no policy copy, no Scope guessing, no Adapter logic);
  4. compare memory / registry / role_profile revisions (computed AFTER the
     gate so processing-induced Canonical changes surface);
  5. map the final frozen result:
       gate CLEAR + all invariants hold              -> READY / USE_EXISTING
       relevant Finding safely processed AND the
       Canonical/Context revision changed            -> REFRESH_REQUIRED / REFRESH
       material Finding cannot be safely
       auto-processed                                -> BLOCKED / ESCALATE

Reason vocabulary is the frozen T00 set (`chandoff.SELF_CHECK_REASONS`).
Verdict is policy computed from data, never an LLM choice:

- any failed check            -> REFRESH_REQUIRED / REFRESH
- candidate package BLOCKED   -> BLOCKED / ESCALATE (reason package_not_ready)
- gate BLOCKED                -> BLOCKED / ESCALATE (reason package_not_ready;
                                 finding diagnostics stay in the internal
                                 trace, never in the frozen result)
- everything passes           -> READY / USE_EXISTING

Ordinary / non-material / irrelevant / cross-scope Findings never wake the
Context Engineer and never expand Scope: only gate `escalation.required`
signals an exception for the Context Engineer.

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
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chandoff  # noqa: E402
import chandoff_plan as plan  # noqa: E402  (T01 internal Finding Gate, reused)
from chandoff_findings_source import FindingsSourceRefusal  # noqa: E402
from cutil import RUNTIME, TEAM  # noqa: E402
from yaml_mini import parse_yaml  # noqa: E402

LLM_CALLED = False
CANONICAL_WRITES = 0
MULTICA_RUNTIME_DEPENDENCIES = 0

REQUEST_SCHEMA = "context-handoff/self-check-request.schema.json"
RESULT_SCHEMA = "context-handoff/self-check-result.schema.json"

ARCHIVED_PHASE = "archived"
DEFAULT_STORE = RUNTIME / "handoff-packages"

# Supplement §7.1/§19: SELF_CHECK anchors the current-role Finding Gate.
# The boundary is exactly the CURRENT role of this request — never a handoff
# target role (that boundary belongs to prepare_handoff).
GATE_BOUNDARY = "current_role"

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
        # T01 supplement compatibility, reused (never re-derived here): the
        # frozen self_check contract can host FINDING_GATE without amendment.
        "frozen_self_check_contract_can_support_finding_gate":
            plan.compatibility_check().get(
                "frozen_self_check_contract_can_support_finding_gate", False),
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
                  current=None, gate: dict | None = None) -> dict:
    """Deterministic self_check against ONE candidate result envelope.

    gate: already-run FINDING_GATE(boundary=current_role) result, or None
    when no Registry-verified package scope existed to anchor the scan
    (missing package, unverifiable scope). Without a gate result no Finding
    is used as Context and no Scope is guessed.
    """
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

    # Current-role Finding Gate (supplement §7.1/§19), after package
    # validation. Gate BLOCKED means a relevant material Finding cannot be
    # safely auto-processed: stop and escalate. The frozen reason vocabulary
    # stays package-level (`package_not_ready`); finding diagnostics live
    # only in the internal gate trace. Gate processing that changed
    # Canonical state makes the package's built-from revision outdated.
    if gate is not None:
        if gate.get("status") == "BLOCKED":
            reasons.append("package_not_ready")
            return _emit(envelope, "BLOCKED", "ESCALATE", reasons)
        if gate.get("canonical_changed"):
            reasons.append("memory_revision_changed")

    # Revisions are compared against the post-gate CURRENT state so a
    # controlled Canonical-changing disposition surfaces as
    # memory_revision_changed instead of hiding behind a pre-gate snapshot.
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


def _missing_result(reasons: list) -> dict:
    result = {
        "schema_version": "1.1",
        "kind": "self_check_result",
        "status": "REFRESH_REQUIRED",
        "reasons": _sort_reasons(reasons),
        "action": "REFRESH",
    }
    out_errors = _validate(RESULT_SCHEMA, result)
    if out_errors:
        raise RuntimeError("self_check_result schema invalid: " +
                           "; ".join(out_errors[:8]))
    return result


def internal_gate_request(request: dict) -> dict:
    """Framework-neutral FINDING_GATE request built only from frozen inputs.

    The frozen self_check_request carries task_ref, role and task_snapshot;
    the gate's target role is exactly the CURRENT role of this request
    (never a handoff target role). No opaque task_ref parsing, no Scope
    guessing, no Adapter logic: the Scope comes only from the verified
    package scope handed in by the caller.
    """
    return {
        "task_ref": request["task_ref"],
        "target": {"role": request["role"]},
        "task_snapshot": request.get("task_snapshot") or {},
    }


def _gate_store(findings: list | None, finding_store):
    """T01 finding-store seam, reused: injected store wins; a plain findings
    list is wrapped in a MemoryFindingStore; with neither, the default
    runtime store (`runtime/v1.1/findings/`, framework-neutral filesystem)
    is scanned. Never a Multica runtime dependency."""
    if finding_store is not None:
        return finding_store
    if findings is None:
        return plan.RuntimeFindingStore()
    return plan.MemoryFindingStore(findings)


def run_current_role_finding_gate(request: dict, task_scope: dict, *,
                                  findings: list | None = None,
                                  finding_store=None, mutator=None,
                                  findings_source=None,
                                  source_prior_observation=None) -> dict:
    """Internal FINDING_GATE(boundary=current_role) via the T01 gate.

    Reuses T01's task association, strict Scope filter, deterministic
    relevance narrowing, verification/classification/retention policy and
    injected store/mutator seam verbatim. No policy is copied and none is
    weakened. A verified `findings_source` is read freshly at this boundary
    and the gate runs over the detached records (never the live store).
    """
    if findings_source is not None and (findings is not None or
                                        finding_store is not None):
        raise ValueError(
            "conflicting findings inputs: a verified findings_source cannot "
            "be combined with a findings list or a legacy finding_store")
    if findings_source is not None:
        snapshot = findings_source.read(
            boundary=GATE_BOUNDARY,
            task_ref=request.get("task_ref"), role=request.get("role"),
            prior_observation=source_prior_observation)
        return plan.finding_gate(
            internal_gate_request(request), task_scope,
            findings=None, store=plan.MemoryFindingStore(snapshot["records"]),
            mutator=mutator, boundary=GATE_BOUNDARY, diagnostics=True)
    return plan.finding_gate(
        internal_gate_request(request), task_scope,
        findings=findings, store=_gate_store(findings, finding_store),
        mutator=mutator, boundary=GATE_BOUNDARY)


def finding_pollution(gate: dict | None, task_scope: dict | None) -> int:
    """Cross-scope pollution contributed by gate-relevant Findings (must be 0)."""
    if not gate or task_scope is None:
        return 0
    return sum(
        1 for f in (gate.get("relevant_open_findings") or [])
        if plan._pollutes(plan._finding_as_doc(f), task_scope))


def _digest_json(obj) -> str:
    return "sha256:" + hashlib.sha256(
        chandoff.canonical_json(obj).encode("utf-8")).hexdigest()


def _source_refusal_result(envelope: dict, exc: FindingsSourceRefusal) -> tuple:
    """Map a typed source refusal to the frozen vocabulary plus diagnostics.

    Source-invalid/unbound/missing is BLOCKED/ESCALATE (package_not_ready);
    detected drift is REFRESH_REQUIRED/REFRESH with the existing
    `package_stale` reason. The precise internal code stays in the trace.
    """
    drift = exc.code == "findings_source_changed"
    status = "REFRESH_REQUIRED" if drift else "BLOCKED"
    action = "REFRESH" if drift else "ESCALATE"
    reasons = ["package_stale"] if drift else ["package_not_ready"]
    return _emit(envelope, status, action, reasons), drift


def self_check_with_trace(request: dict, *, packages=None, store_dir=None,
                          registry=None, current=None, findings=None,
                          finding_store=None, mutator=None,
                          findings_source=None,
                          findings_prior_observation=None,
                          source_observer_run_id=None) -> dict:
    """self_check plus the non-schema Finding-Gate trace (diagnostics only).

    The frozen public result never carries finding diagnostics; they live
    in the returned trace (internal trace / test evidence only). Sequence
    per supplement §7.1/§19: find current Package -> validate task/role/
    scope/status/fingerprint/revisions -> scan task-associated open
    Findings -> FINDING_GATE(boundary=current_role) -> map final result.

    A verified `findings_source` is read independently at this boundary and
    must join the same task/role/request/envelope; conflicting legacy list
    or store injections are refused instead of silently preferred.
    """
    if findings_source is not None and (findings is not None or
                                        finding_store is not None):
        raise ValueError(
            "conflicting findings inputs: a verified findings_source cannot "
            "be combined with a findings list or a legacy finding_store")
    errors = _validate(REQUEST_SCHEMA, request)
    if errors:
        raise ValueError("invalid self_check_request: " + "; ".join(errors[:8]))
    envelope, missing = resolve_candidate(
        request, packages=packages, store_dir=store_dir)
    if envelope is None:
        # No valid package: the Finding Gate never runs, an open Finding is
        # never used as Context, and nothing is guessed.
        return {
            "result": _missing_result(missing),
            "finding_gate": None,
            "gate_ran": False,
            "verified_scope": None,
            "findings_observation": None,
            "findings_source_refusal": None,
            "context_engineer_woken": False,
            "scope_pollution_from_findings": 0,
        }
    scope = (envelope.get("package") or {}).get("scope") or {}
    verified_scope = None
    if (scope.get("type") in ("project", "cross_project")
            and _registry_ok(scope, _load_registry(registry))):
        verified_scope = scope
    gate = None
    findings_observation = None
    findings_source_refusal = None
    if verified_scope is not None:
        if findings_source is not None:
            try:
                snapshot = findings_source.read(
                    boundary=GATE_BOUNDARY,
                    observer_run_id=source_observer_run_id,
                    task_ref=request.get("task_ref"),
                    role=request.get("role"),
                    request_digest=_digest_json(request),
                    task_fingerprint=current_fingerprint(request, verified_scope),
                    envelope_digest=_digest_json(envelope),
                    prior_observation=findings_prior_observation)
            except FindingsSourceRefusal as exc:
                result, _drift = _source_refusal_result(envelope, exc)
                return {
                    "result": result,
                    "finding_gate": None,
                    "gate_ran": False,
                    "verified_scope": verified_scope,
                    "findings_observation": None,
                    "findings_source_refusal": exc.as_dict(),
                    "context_engineer_woken": False,
                    "scope_pollution_from_findings": 0,
                }
            findings_observation = snapshot["observation"]
            gate = plan.finding_gate(
                internal_gate_request(request), verified_scope,
                findings=None,
                store=plan.MemoryFindingStore(snapshot["records"]),
                mutator=mutator, boundary=GATE_BOUNDARY, diagnostics=True)
        else:
            gate = run_current_role_finding_gate(
                request, verified_scope, findings=findings,
                finding_store=finding_store, mutator=mutator)
    result = check_package(request, envelope, registry=registry,
                           current=current, gate=gate)
    return {
        "result": result,
        "finding_gate": gate,
        "gate_ran": gate is not None,
        "verified_scope": verified_scope,
        "findings_observation": findings_observation,
        "findings_source_refusal": findings_source_refusal,
        "context_engineer_woken": bool((gate or {}).get("context_engineer_woken")),
        "scope_pollution_from_findings": finding_pollution(gate, verified_scope),
    }


def self_check(request: dict, *, packages=None, store_dir=None, registry=None,
               current=None, findings=None, finding_store=None, mutator=None,
               findings_source=None, findings_prior_observation=None,
               source_observer_run_id=None) -> dict:
    """Native API entry: frozen request in, frozen result out. No LLM, no writes."""
    return self_check_with_trace(
        request, packages=packages, store_dir=store_dir, registry=registry,
        current=current, findings=findings, finding_store=finding_store,
        mutator=mutator, findings_source=findings_source,
        findings_prior_observation=findings_prior_observation,
        source_observer_run_id=source_observer_run_id)["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description="T04 deterministic SELF_CHECK")
    sub = parser.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check", help="run self_check from request file")
    chk.add_argument("--request-file", required=True)
    chk.add_argument("--package", action="append", default=[],
                     help="prepare_handoff_result envelope file (repeatable)")
    chk.add_argument("--store", default=None,
                     help="runtime package store dir "
                          "(default runtime/v1.1/handoff-packages)")
    args = parser.parse_args()
    try:
        request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        packages = [json.loads(Path(p).read_text(encoding="utf-8"))
                    for p in args.package]
        result = self_check(request, packages=packages or None,
                            store_dir=args.store)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return {"READY": 0, "REFRESH_REQUIRED": 2, "BLOCKED": 3}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
