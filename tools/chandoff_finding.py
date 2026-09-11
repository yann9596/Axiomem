#!/usr/bin/env python3
"""U09 — Runtime Finding / Challenge alignment layer (YZT-77).

Deterministic, fail-closed semantic alignment between the frozen Memory V1.1
Finding / Challenge capabilities and the accepted V2.2 Artifact-aware handoff
lineage (T01 Finding Gate, T04B current-role integration, U04 shared skill,
U05 role instructions, U06-U08 shared transaction ledger).

Scope discipline (U09 issue, YZT-77):

- REPORT_FINDING stays capture-only: the Finding is created `open` with
  `process_now=false`, `wake_context_engineer=false`, `canonical_write=false`.
  Capture never processes, never wakes 02 and never writes Canonical Memory.
- Processing happens only at the four natural boundaries and reuses the T01
  FINDING_GATE / T04B current-role integration verbatim (no policy copy):
    SELF_CHECK        -> current-role relevant open Findings
    PREPARE_HANDOFF   -> target-role relevant open Findings before READY
    TASK_COMPLETION   -> drain of every task-associated open Finding
    CHALLENGE_CONTEXT -> immediate targeted revalidation of exact revisions
- Classification and routing are typed and deterministic. Free text never
  routes work. Local Review/QA/implementation defects stay in their artifact
  unless they carry reusable cognition or a material context change.
- Only unresolved material exceptions produce a Context Engineer escalation
  proposal, and a proposal is never a trigger: it returns to Engineering Lead
  with the exact affected artifact/context, evidence, attempted processing and
  a recommended target. Any later dispatch is a fresh Lead-owned transaction.
- No live mutation/trigger is ever emitted by this layer. The shared
  `chandoff_dispatch.TransactionLedger` is extended with typed records only;
  no second evidence store exists.
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

import cartifact  # noqa: E402
import chandoff  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402
from cutil import now_iso, scope_allows  # noqa: E402

LAYER_VERSION = "U09/2.2"
CAPTURE_SCHEMA = "U09-finding-capture/2.2"
CLASSIFICATION_SCHEMA = "U09-finding-classification/2.2"
PROCESSING_SCHEMA = "U09-finding-processing/2.2"
CHALLENGE_SCHEMA = "U09-challenge-revalidation/2.2"
DRAIN_SCHEMA = "U09-task-drain/2.2"
ESCALATION_SCHEMA = "U09-escalation-proposal/2.2"
AUDIT_SCHEMA = "U09-finding-audit/2.2"

# The six V2.2 logical roles. Display names never resolve: only these exact
# slugs are accepted, and the retired identities are refused without alias.
V22_ROLES = (
    "engineering-lead",
    "context-engineer",
    "solution-architect",
    "software-engineer",
    "delivery-reviewer",
    "qa",
)
RETIRED_ROLES = ("feature-reviewer", "ops-sre")

BOUNDARY_SELF_CHECK = "SELF_CHECK"
BOUNDARY_PREPARE = "PREPARE_HANDOFF"
BOUNDARY_COMPLETION = "TASK_COMPLETION"
BOUNDARY_CHALLENGE = "CHALLENGE_CONTEXT"
BOUNDARIES = (BOUNDARY_SELF_CHECK, BOUNDARY_PREPARE,
              BOUNDARY_COMPLETION, BOUNDARY_CHALLENGE)
NATURAL_BOUNDARIES = (BOUNDARY_SELF_CHECK, BOUNDARY_PREPARE,
                      BOUNDARY_COMPLETION)

STATUS_CLEAR = "CLEAR"
STATUS_REFRESH_REQUIRED = "REFRESH_REQUIRED"
STATUS_BLOCKED = "BLOCKED"
STATUS_READY_REFUSED = "READY_REFUSED"
STATUS_DRAINED = "DRAINED"
STATUS_RESOLVED = "RESOLVED"
STATUS_RETURNED_TO_LEAD = "RETURNED_TO_LEAD"
STATUS_UNRESOLVED_MATERIAL = "UNRESOLVED_MATERIAL"
STATUS_STOPPED_ESCALATED = "STOPPED_ESCALATED"

LEAD_ROLE = "engineering-lead"

CLASS_IMPLEMENTATION = "IMPLEMENTATION_DEFECT"
CLASS_DELIVERY_REVIEW = "DELIVERY_REVIEW_DEFECT"
CLASS_QA = "QA_DEFECT"
CLASS_TASK_COGNITION = "TASK_COGNITION"
CLASS_CONTEXT_CONFLICT = "CONTEXT_AUTHORITY_CONFLICT"
CLASS_PRODUCT_CHALLENGE = "PRODUCT_CONTEXT_CHALLENGE"
CLASS_DESIGN_CHALLENGE = "DESIGN_CHALLENGE"
CLASS_DESIGN_DEVIATION = "DESIGN_DEVIATION"
CLASS_EXTERNAL_INTELLIGENCE = "EXTERNAL_INTELLIGENCE"

CLASSES = (
    CLASS_IMPLEMENTATION,
    CLASS_DELIVERY_REVIEW,
    CLASS_QA,
    CLASS_TASK_COGNITION,
    CLASS_CONTEXT_CONFLICT,
    CLASS_PRODUCT_CHALLENGE,
    CLASS_DESIGN_CHALLENGE,
    CLASS_DESIGN_DEVIATION,
    CLASS_EXTERNAL_INTELLIGENCE,
)

ROUTE_KEEP_IN_ARTIFACT = "keep_in_artifact"
ROUTE_RUNTIME_PROCESSING = "runtime_boundary_processing"
ROUTE_LEAD_DECISION = "lead_decision"
ROUTE_EVIDENCE_ONLY = "evidence_pointer_only"
ROUTES = (ROUTE_KEEP_IN_ARTIFACT, ROUTE_RUNTIME_PROCESSING,
          ROUTE_LEAD_DECISION, ROUTE_EVIDENCE_ONLY)

CLASS_ORIGINS = ("implementation", "delivery_review", "qa", "external")
CLASS_CLAIMS = (None, "actual_not_design", "design_baseline_wrong",
                "product_expectation_conflict")
FINDING_INTENTS = ("observation", "durable_candidate", "context_challenge",
                   "task_delivery", None)
VERIFICATIONS = ("unverified", "partially_verified", "verified",
                 "conflicted", "refuted")

CLASSIFICATION_TABLE = {
    CLASS_IMPLEMENTATION: {
        "classification": CLASS_IMPLEMENTATION,
        "label": "local implementation defect",
        "artifact_home": "implementation",
        "owner_role": "software-engineer",
        "route": ROUTE_KEEP_IN_ARTIFACT,
        "runtime_route": ROUTE_RUNTIME_PROCESSING,
        "lead_decision_target": "software-engineer",
        "runtime_finding": "reusable_cognition or material_context_change",
        "material_default": False,
        "authority_eligible": False,
        "local_artifact": True,
        "direct_trigger": False,
    },
    CLASS_DELIVERY_REVIEW: {
        "classification": CLASS_DELIVERY_REVIEW,
        "label": "Delivery Review finding",
        "artifact_home": "delivery-review",
        "owner_role": "software-engineer",
        "route": ROUTE_KEEP_IN_ARTIFACT,
        "runtime_route": ROUTE_RUNTIME_PROCESSING,
        "lead_decision_target": "software-engineer",
        "runtime_finding": "reusable_cognition or material_context_change",
        "material_default": False,
        "authority_eligible": False,
        "local_artifact": True,
        "direct_trigger": False,
    },
    CLASS_QA: {
        "classification": CLASS_QA,
        "label": "QA finding",
        "artifact_home": "qa",
        "owner_role": "software-engineer",
        "route": ROUTE_KEEP_IN_ARTIFACT,
        "runtime_route": ROUTE_RUNTIME_PROCESSING,
        "lead_decision_target": "software-engineer",
        "runtime_finding": "reusable_cognition or material_context_change",
        "material_default": False,
        "authority_eligible": False,
        "local_artifact": True,
        "direct_trigger": False,
    },
    CLASS_TASK_COGNITION: {
        "classification": CLASS_TASK_COGNITION,
        "label": "task cognition finding",
        "artifact_home": "runtime",
        "owner_role": "context-engineer",
        "route": ROUTE_RUNTIME_PROCESSING,
        "runtime_route": ROUTE_RUNTIME_PROCESSING,
        "lead_decision_target": "context-engineer",
        "runtime_finding": "always",
        "material_default": True,
        "authority_eligible": True,
        "local_artifact": False,
        "direct_trigger": False,
    },
    CLASS_CONTEXT_CONFLICT: {
        "classification": CLASS_CONTEXT_CONFLICT,
        "label": "context/authority/evidence conflict",
        "artifact_home": "runtime",
        "owner_role": "context-engineer",
        "route": ROUTE_RUNTIME_PROCESSING,
        "runtime_route": ROUTE_RUNTIME_PROCESSING,
        "lead_decision_target": "context-engineer",
        "runtime_finding": "always",
        "material_default": True,
        "authority_eligible": True,
        "local_artifact": False,
        "direct_trigger": False,
    },
    CLASS_PRODUCT_CHALLENGE: {
        "classification": CLASS_PRODUCT_CHALLENGE,
        "label": "Product Context challenge",
        "artifact_home": "runtime",
        "owner_role": "context-engineer",
        "route": ROUTE_LEAD_DECISION,
        "runtime_route": ROUTE_LEAD_DECISION,
        "lead_decision_target": "context-engineer",
        "runtime_finding": "always",
        "material_default": True,
        "authority_eligible": True,
        "local_artifact": False,
        "direct_trigger": False,
    },
    CLASS_DESIGN_CHALLENGE: {
        "classification": CLASS_DESIGN_CHALLENGE,
        "label": "Design Baseline challenge",
        "artifact_home": "runtime",
        "owner_role": "solution-architect",
        "route": ROUTE_LEAD_DECISION,
        "runtime_route": ROUTE_LEAD_DECISION,
        "lead_decision_target": "solution-architect",
        "runtime_finding": "always",
        "material_default": True,
        "authority_eligible": True,
        "local_artifact": False,
        "direct_trigger": False,
    },
    CLASS_DESIGN_DEVIATION: {
        "classification": CLASS_DESIGN_DEVIATION,
        "label": "Design Deviation (actual != design)",
        "artifact_home": "runtime",
        "owner_role": "solution-architect",
        "route": ROUTE_LEAD_DECISION,
        "runtime_route": ROUTE_LEAD_DECISION,
        "lead_decision_target": "solution-architect",
        "runtime_finding": "always",
        "material_default": True,
        "authority_eligible": True,
        "local_artifact": False,
        "direct_trigger": False,
    },
    CLASS_EXTERNAL_INTELLIGENCE: {
        "classification": CLASS_EXTERNAL_INTELLIGENCE,
        "label": "external-intelligence Evidence/Finding",
        "artifact_home": "runtime",
        "owner_role": "context-engineer",
        "route": ROUTE_EVIDENCE_ONLY,
        "runtime_route": ROUTE_RUNTIME_PROCESSING,
        "lead_decision_target": "context-engineer",
        "runtime_finding": "reusable_cognition or material_context_change",
        "material_default": False,
        "authority_eligible": False,
        "local_artifact": False,
        "direct_trigger": False,
    },
}

# Finding-schema intent used for each classification so the frozen T01 gate
# keeps owning package readiness materiality without policy duplication.
CLASS_TO_FINDING_INTENT = {
    CLASS_IMPLEMENTATION: "task_delivery",
    CLASS_DELIVERY_REVIEW: "task_delivery",
    CLASS_QA: "task_delivery",
    CLASS_TASK_COGNITION: "durable_candidate",
    CLASS_CONTEXT_CONFLICT: "context_challenge",
    CLASS_PRODUCT_CHALLENGE: "context_challenge",
    CLASS_DESIGN_CHALLENGE: "context_challenge",
    CLASS_DESIGN_DEVIATION: "observation",
    CLASS_EXTERNAL_INTELLIGENCE: "observation",
}

DRAIN_DISPOSITIONS = {
    "RESOLVED_EXISTING": {"frozen": "absorbed_by_existing", "gate_required": False},
    "DEFERRED_GATE": {"frozen": "carry_to_checkpoint", "gate_required": True},
    "ESCALATED_PROPOSAL": {"frozen": "issue_escalation", "gate_required": True},
    "POINTER": {"frozen": "pointer", "gate_required": False},
    "FORGET": {"frozen": "forget", "gate_required": False},
}
KNOWN_GATES = (
    "SELF_CHECK", "PREPARE_HANDOFF", "TASK_COMPLETION", "CHALLENGE_CONTEXT",
    "CHECKPOINT", "LEAD_DECISION", "LEAD_OWNED_SAFE_DISPATCH",
    "PRE_U12_PIN_GATE", "U11_JOINT_REPLAY", "U12_ENABLEMENT",
)

CHALLENGE_REASONS = (
    "evidence_conflict",
    "authority_gap",
    "context_gap",
    "design_baseline_wrong",
    "product_expectation_conflict",
)
LEAD_DECISION_REASONS = {
    "design_baseline_wrong": "solution-architect",
    "product_expectation_conflict": "context-engineer",
}
ESCALATION_REASONS = (
    "authority_gap", "evidence_conflict", "critical_context_review",
    "cross_repo_investigation", "unrecoverable_unknown",
)

MATERIAL_TOKENS = plan.MATERIAL_TOKEN_TEXT

_PLACEHOLDER_VERSION_RE = re.compile(
    r"^(latest|current|head|main|master|tip|approx|latest-.*)$", re.IGNORECASE)
_PACKAGE_ID_RE = re.compile(r"^CTX-[A-Za-z0-9._:-]+-[0-9a-f]{16}$")
_FINDING_ID_RE = plan.FINDING_ID_RE
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class FindingError(Exception):
    """Bounded stop. The failure is recorded, never guessed away."""

    code = "finding_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = str(message)
        self.details = details

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class CaptureError(FindingError):
    code = "capture_invalid"


class DuplicateCaptureConflict(CaptureError):
    code = "duplicate_capture_conflict"


class ClassificationError(FindingError):
    code = "classification_ambiguous"


class RoleError(FindingError):
    code = "role_invalid"


class RetiredIdentityError(RoleError):
    code = "retired_identity_refused"


class BindingError(FindingError):
    code = "binding_invalid"


class ChallengeError(FindingError):
    code = "challenge_invalid"


class DrainError(FindingError):
    code = "drain_invalid"


class RoutingRefused(FindingError):
    code = "routing_refused"


def _digest(obj) -> str:
    return "sha256:" + hashlib.sha256(
        chandoff.canonical_json(obj).encode("utf-8")).hexdigest()


def _is_digest(value) -> bool:
    return isinstance(value, str) and bool(_DIGEST_RE.fullmatch(value))


def _require_role(value, field: str = "role") -> str:
    slug = value.strip().lower().replace("_", "-") if isinstance(value, str) else ""
    if slug in RETIRED_ROLES:
        raise RetiredIdentityError(
            f"{field} {value!r} is a retired identity with no alias; it never "
            "resolves", field=field)
    if slug not in V22_ROLES:
        raise RoleError(
            f"{field} {value!r} is not one of the six V2.2 logical roles",
            field=field)
    return slug


def _require_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FindingError(f"{field} must be a non-blank string", field=field)
    return value.strip()


def _exact_ref(value) -> bool:
    return isinstance(value, str) and bool(plan.REF_RE.match(value))


def _require_refs(refs, field: str, *, minimum: int = 0) -> list:
    if not isinstance(refs, list):
        raise FindingError(f"{field} must be a list", field=field)
    cleaned = sorted({r.strip() for r in refs
                      if isinstance(r, str) and r.strip()})
    if len(cleaned) < minimum:
        raise FindingError(
            f"{field} must carry at least {minimum} exact evidence reference(s)",
            field=field)
    bad = [r for r in cleaned if not _exact_ref(r)]
    if bad:
        raise FindingError(
            f"{field} carries references outside the frozen ref grammar",
            field=field, bad=bad[:4])
    return cleaned


def resolve_scope(scope, *, registry=None, task_ref: str = "multica://task/u09-scope") -> dict:
    """Resolve one finding's affected scope through the existing resolver."""
    if not isinstance(scope, dict):
        raise FindingError("affected_scope must be an object")
    stype = scope.get("type")
    try:
        if stype == "project":
            req = {"project": {"project_id": scope.get("project_id")},
                   "task_ref": task_ref, "options": {}}
            return plan.resolve_handoff_scope(req, registry)
        if stype == "cross_project":
            projects = scope.get("projects") or []
            req = {"project": {"project_id": projects[0] if projects else None},
                   "task_ref": task_ref,
                   "options": {"cross_project_projects": projects}}
            return plan.resolve_handoff_scope(req, registry)
    except Exception as exc:
        raise FindingError(f"affected_scope is not resolvable: {exc}") from exc
    raise FindingError(
        "affected_scope.type must be project or cross_project")


def _scope_project_id(scope: dict) -> str:
    if scope.get("type") == "project":
        return scope.get("project_id")
    projects = sorted(scope.get("projects") or [])
    return projects[0] if projects else ""


def _store_docs(store) -> list:
    """Read every finding doc a T01 store seam can expose (open + processed)."""
    if store is None:
        return []
    items = getattr(store, "_items", None)
    if isinstance(items, list):
        return [copy.deepcopy(x) for x in items]
    root = getattr(store, "root", None)
    if root is not None:
        path = Path(root)
        out = []
        if path.exists():
            for p in sorted(path.glob("FIND-*.json")):
                try:
                    out.append(json.loads(p.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
        return out
    return [copy.deepcopy(f) for f in (store.load_open() or [])]


def finding_ref_digest(finding: dict) -> str:
    return _digest({
        "finding_id": finding.get("finding_id"),
        "status": finding.get("status"),
        "disposition": finding.get("disposition"),
        "summary": finding.get("summary"),
    })


def store_digest(docs) -> str:
    return _digest(sorted(finding_ref_digest(f) for f in docs or []))


def finding_core(*, task_ref: str, reporting_role: str, summary: str,
                 detail, intent, evidence_refs: list,
                 project_id: str) -> dict:
    return {
        "task_ref": task_ref,
        "reporting_role": reporting_role,
        "summary": summary,
        "detail": detail if isinstance(detail, str) else None,
        "intent": intent,
        "evidence_refs": sorted(evidence_refs),
        "project_id": project_id,
    }


def content_digest(core: dict) -> str:
    return _digest(core)


def classify(*, origin, intent=None, claim=None, reusable_cognition: bool = False,
             material_context_change: bool = False, verification: str = "unverified",
             evidence_class: str = "project") -> dict:
    """Deterministic typed classification. Free text never routes here."""
    if origin not in CLASS_ORIGINS + (None,):
        raise ClassificationError(
            f"origin {origin!r} is not a typed classification origin",
            origin=origin)
    if intent not in FINDING_INTENTS:
        raise ClassificationError(
            f"intent {intent!r} is not a frozen Finding intent", intent=intent)
    if claim not in CLASS_CLAIMS:
        raise ClassificationError(
            f"claim {claim!r} is not a typed cognition claim", claim=claim)
    if verification not in VERIFICATIONS:
        raise ClassificationError(
            f"verification {verification!r} is not a frozen verification value",
            verification=verification)
    if evidence_class not in ("project", "external_raw"):
        raise ClassificationError(
            f"evidence_class {evidence_class!r} is not typed",
            evidence_class=evidence_class)
    reusable = bool(reusable_cognition)
    material_change = bool(material_context_change)

    if origin == "external" or evidence_class == "external_raw":
        key = CLASS_EXTERNAL_INTELLIGENCE
    elif claim == "design_baseline_wrong":
        key = CLASS_DESIGN_CHALLENGE
    elif claim == "actual_not_design":
        key = CLASS_DESIGN_DEVIATION
    elif claim == "product_expectation_conflict" or intent == "context_challenge":
        key = CLASS_PRODUCT_CHALLENGE
    elif verification == "conflicted":
        key = CLASS_CONTEXT_CONFLICT
    elif intent == "durable_candidate":
        key = CLASS_TASK_COGNITION
    elif origin == "delivery_review":
        key = CLASS_DELIVERY_REVIEW
    elif origin == "qa":
        key = CLASS_QA
    elif origin == "implementation":
        key = CLASS_IMPLEMENTATION
    else:
        raise ClassificationError(
            "classification is ambiguous: no typed signal selects a class",
            origin=origin, intent=intent, claim=claim)

    row = CLASSIFICATION_TABLE[key]
    local = key in (CLASS_IMPLEMENTATION, CLASS_DELIVERY_REVIEW, CLASS_QA)
    if local:
        runtime = reusable or material_change
        material = material_change
        route = ROUTE_RUNTIME_PROCESSING if runtime else ROUTE_KEEP_IN_ARTIFACT
    elif key == CLASS_EXTERNAL_INTELLIGENCE:
        runtime = reusable or material_change
        material = material_change
        route = ROUTE_EVIDENCE_ONLY
    else:
        runtime = True
        material = True
        route = row["route"]
    authority_eligible = bool(row["authority_eligible"])
    return {
        "schema_version": CLASSIFICATION_SCHEMA,
        "kind": "finding_classification",
        "classification": key,
        "label": row["label"],
        "artifact_home": row["artifact_home"],
        "owner_role": row["owner_role"],
        "route": route,
        "runtime_finding": runtime,
        "material": material,
        "local_artifact": local,
        "authority_eligible": authority_eligible,
        "truth_status": "evidence" if key == CLASS_EXTERNAL_INTELLIGENCE else "project",
        "promotion_allowed": key != CLASS_EXTERNAL_INTELLIGENCE,
        "lead_decision_target": row["lead_decision_target"] if route in (
            ROUTE_LEAD_DECISION, ROUTE_RUNTIME_PROCESSING) else None,
        "direct_trigger": False,
        "context_engineer_wake": False,
        "canonical_write": False,
        "finding_intent": CLASS_TO_FINDING_INTENT[key],
    }


def classification_matrix() -> list:
    rows = []
    for key in CLASSES:
        row = CLASSIFICATION_TABLE[key]
        rows.append({
            "classification": key,
            "label": row["label"],
            "artifact_home": row["artifact_home"],
            "owner_role": row["owner_role"],
            "route": row["route"],
            "runtime_route": row["runtime_route"],
            "runtime_when": row["runtime_finding"],
            "material_default": row["material_default"],
            "authority_eligible": row["authority_eligible"],
            "local_artifact": row["local_artifact"],
            "lead_decision_target": row["lead_decision_target"],
            "direct_trigger": False,
        })
    return rows


def _ledger_append(ledger, transaction_id: str, record: dict):
    if ledger is None:
        return None
    entry = dict(record)
    entry["kind"] = record["kind"]
    entry["transaction_id"] = transaction_id
    entry.setdefault("layer", LAYER_VERSION)
    return ledger.append(entry)


def capture_finding(request: dict, *, store=None, existing=None, clock=None,
                    ledger=None, transaction_id: str = "") -> dict:
    """REPORT_FINDING capture only. Validates, classifies, writes the open
    Finding into the task-workspace store and returns the typed capture
    envelope. Never processes, never wakes 02, never writes Canonical Memory.
    """
    if not isinstance(request, dict):
        raise CaptureError("capture request must be an object")
    if request.get("kind") != "report_finding_request":
        raise CaptureError("kind must be report_finding_request",
                           kind=request.get("kind"))
    try:
        finding_id = _require_text(request.get("finding_id"), "finding_id")
        if not _FINDING_ID_RE.match(finding_id):
            raise CaptureError(f"invalid finding_id: {finding_id!r}")
        task_ref = _require_text(request.get("task_ref"), "task_ref")
        summary = _require_text(request.get("summary"), "summary")
        evidence_refs = _require_refs(request.get("evidence_refs"),
                                      "evidence_refs", minimum=1)
        source_refs = _require_refs(request.get("source_refs") or [],
                                    "source_refs")
    except FindingError as exc:
        if isinstance(exc, CaptureError):
            raise
        raise CaptureError(exc.message, cause=exc.code, **exc.details) from exc
    reporting_role = _require_role(request.get("reporting_role"),
                                   field="reporting_role")
    detail = request.get("detail")
    if detail is not None and not isinstance(detail, str):
        raise CaptureError("detail must be a string or null")
    affected_scope = request.get("affected_scope")
    try:
        resolved_scope = resolve_scope(affected_scope, task_ref=task_ref)
    except FindingError as exc:
        raise CaptureError(exc.message, cause=exc.code, **exc.details) from exc
    project_id = _scope_project_id(resolved_scope)
    classification = classify(
        origin=request.get("origin"),
        intent=request.get("intent"),
        claim=request.get("claim"),
        reusable_cognition=request.get("reusable_cognition", False),
        material_context_change=request.get("material_context_change", False),
        verification=request.get("verification", "unverified"),
        evidence_class=request.get("evidence_class", "project"),
    )
    intent = classification["finding_intent"]
    verification = request.get("verification") or "unverified"
    for flag in ("promote_to_project_truth", "as_authority", "as_rule"):
        if request.get(flag):
            raise CaptureError(
                "REPORT_FINDING is capture-only: raw output never becomes "
                "Authority/Rule/Project Truth at capture", flag=flag)
    if classification["classification"] == CLASS_EXTERNAL_INTELLIGENCE:
        # raw external output is Evidence only: never verified project truth.
        verification = "unverified"
    created_at = _require_text(request.get("created_at"), "created_at") \
        if request.get("created_at") else (clock or now_iso)()
    core = finding_core(
        task_ref=task_ref, reporting_role=reporting_role, summary=summary,
        detail=detail, intent=intent,
        evidence_refs=sorted(set(evidence_refs + source_refs)),
        project_id=project_id)
    digest = content_digest(core)
    doc = {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": finding_id,
        "project_id": project_id,
        "task_id": task_ref,
        "summary": summary,
        "detail": detail,
        "intent": intent,
        "source_refs": sorted(set(evidence_refs + source_refs)),
        "discovered_by": reporting_role,
        "status": "open",
        "verification": verification,
        "created_at": created_at,
    }
    _validate_finding_doc(doc)
    store = store if store is not None else plan.MemoryFindingStore()
    candidates = existing if existing is not None else _store_docs(store)
    for prior in candidates:
        if prior.get("finding_id") != finding_id:
            continue
        prior_core = finding_core(
            task_ref=prior.get("task_id") or "", reporting_role=prior.get("discovered_by") or "",
            summary=prior.get("summary") or "", detail=prior.get("detail"),
            intent=prior.get("intent"), evidence_refs=prior.get("source_refs") or [],
            project_id=prior.get("project_id") or "")
        prior_digest = content_digest(prior_core)
        if prior_digest == digest:
            receipt = {
                "schema_version": CAPTURE_SCHEMA,
                "kind": "finding_capture_result",
                "finding_id": finding_id,
                "ingest": "immediate",
                "status": prior.get("status") or "open",
                "duplicate": "idempotent",
                "writes": 0,
                "classification": classification,
                "content_digest": digest,
                "process_now": False,
                "wake_context_engineer": False,
                "context_engineer_woken": False,
                "canonical_write": False,
                "context_engineer_run": False,
                "direct_trigger": False,
                "ledger_record": None,
            }
            _ledger_append(ledger, transaction_id, {
                "kind": "finding_capture", "finding_id": finding_id,
                "classification": classification["classification"],
                "content_digest": digest, "outcome": "duplicate_idempotent"})
            receipt["ledger_record"] = "duplicate_idempotent"
            return receipt
        raise DuplicateCaptureConflict(
            f"finding {finding_id} already exists with different evidence "
            "(duplicate capture), fail closed",
            finding_id=finding_id, existing_digest=prior_digest,
            incoming_digest=digest)
    store.save(doc)
    receipt = {
        "schema_version": CAPTURE_SCHEMA,
        "kind": "finding_capture_result",
        "finding_id": finding_id,
        "ingest": "immediate",
        "status": "open",
        "duplicate": None,
        "writes": 1,
        "classification": classification,
        "content_digest": digest,
        "process_now": False,
        "wake_context_engineer": False,
        "context_engineer_woken": False,
        "canonical_write": False,
        "context_engineer_run": False,
        "direct_trigger": False,
        "ledger_record": "captured",
    }
    _ledger_append(ledger, transaction_id, {
        "kind": "finding_capture", "finding_id": finding_id,
        "classification": classification["classification"],
        "content_digest": digest, "outcome": "captured"})
    return receipt


def _validate_finding_doc(doc: dict) -> None:
    from schema_mini import Schema, load_schema_file
    schema = load_schema_file("finding.schema.json")
    errors = Schema(schema, schema).validate(doc, path=f"finding:{doc.get('finding_id')}")
    if errors:
        raise CaptureError("finding document is not schema valid: " +
                           "; ".join(errors[:6]), finding_id=doc.get("finding_id"))


def _open_task_findings(task_ref: str, task_scope, *, findings=None,
                        store=None) -> list:
    source = findings if findings is not None else _store_docs(store)
    out = []
    for f in sorted(source, key=lambda x: x.get("finding_id") or ""):
        if f.get("status") != "open":
            continue
        if not plan._task_associated(f, task_ref):
            continue
        if task_scope is not None and not scope_allows(plan._finding_as_doc(f), task_scope):
            continue
        out.append(copy.deepcopy(f))
    return out


def _selection_rows(findings: list) -> list:
    return [{
        "finding_id": f.get("finding_id"),
        "digest": finding_ref_digest(f),
        "verification": f.get("verification"),
        "intent": f.get("intent"),
    } for f in findings]


def _post_prepare_change(prepared: dict, relevant: list) -> bool:
    if not prepared:
        return False
    prepared_digest = prepared.get("selection_digest")
    current_digest = _digest(sorted(row["digest"] for row in _selection_rows(relevant)))
    if prepared_digest and prepared_digest != current_digest:
        return True
    return False


def _proposal(*, task_ref: str, reason_code: str, classification: str | None,
              package_id: str | None, affected_refs: list, evidence_refs: list,
              attempted: dict, recommended_target: str | None,
              boundary: str) -> dict:
    if recommended_target is not None:
        recommended_target = _require_role(recommended_target,
                                           field="recommended_target")
    payload = {
        "task_ref": task_ref,
        "reason_code": reason_code,
        "classification": classification,
        "package_id": package_id,
        "affected_refs": sorted(set(affected_refs or [])),
        "evidence_refs": sorted(set(evidence_refs or [])),
        "attempted": attempted,
        "recommended_target": recommended_target,
        "boundary": boundary,
    }
    proposal_id = "ESC-U09-" + _digest(payload)[7:23]
    return {
        "schema_version": ESCALATION_SCHEMA,
        "kind": "escalation_proposal",
        "proposal_id": proposal_id,
        "addressed_to": LEAD_ROLE,
        "recommended_target": recommended_target,
        "boundary": boundary,
        "reason_code": reason_code,
        "classification": classification,
        "affected": {
            "task_ref": task_ref,
            "package_id": package_id,
            "artifact_or_context_refs": payload["affected_refs"],
        },
        "evidence": {
            "refs": payload["evidence_refs"],
            "digest": _digest({"refs": payload["evidence_refs"],
                               "attempted": attempted}),
        },
        "attempted_processing": attempted,
        "dispatch": {
            "trigger_emitted": False,
            "assignment_emitted": False,
            "status_write_emitted": False,
            "requires_lead_owned_safe_dispatch": True,
        },
        "context_engineer_woken": False,
        "canonical_write": False,
    }


def propose_escalation(*, task_ref: str, reason_code: str, classification=None,
                       package_id=None, affected_refs=None, evidence_refs=None,
                       attempted=None, recommended_target=None,
                       boundary: str = BOUNDARY_CHALLENGE) -> dict:
    """Typed unresolved-material exception proposal addressed to Engineering
    Lead. Never a trigger: dispatch stays Lead-owned."""
    if reason_code not in ESCALATION_REASONS:
        raise FindingError(
            f"escalation reason {reason_code!r} is not a typed unresolved-"
            "material exception", reason_code=reason_code)
    return _proposal(
        task_ref=_require_text(task_ref, "task_ref"), reason_code=reason_code,
        classification=classification, package_id=package_id,
        affected_refs=affected_refs, evidence_refs=evidence_refs,
        attempted=attempted or {}, recommended_target=recommended_target,
        boundary=boundary)


def validate_binding(binding: dict, *, current=None, packages=None,
                     artifact_store=None) -> dict:
    """Exact revision/package/artifact binding. No latest, no display names."""
    if not isinstance(binding, dict):
        raise BindingError("binding must be an object")
    out = {
        "schema_version": "U09-revision-binding/2.2",
        "kind": "revision_binding",
        "package_id": None,
        "task_ref": None,
        "role": None,
        "memory_revision": None,
        "registry_revision": None,
        "role_profile_revision": None,
        "artifact_dependency_digest": None,
        "artifact_requirements": [],
        "artifact_ready": None,
    }
    package_id = binding.get("package_id")
    if package_id is not None:
        if not isinstance(package_id, str) or not _PACKAGE_ID_RE.match(package_id):
            raise BindingError(
                "package_id must be the exact CTX-<role>-<digest> identity; "
                "display names and guessed values fail closed",
                package_id=package_id)
        out["package_id"] = package_id
    if packages is not None and out["package_id"]:
        found = [e for e in packages if e.get("package_id") == out["package_id"]]
        if not found:
            raise BindingError(
                "binding package_id does not resolve in the supplied exact "
                "package set", package_id=out["package_id"])
    task_ref = binding.get("task_ref")
    if task_ref is not None:
        out["task_ref"] = _require_text(task_ref, "binding.task_ref")
    role = binding.get("role")
    if role is not None:
        out["role"] = _require_role(role, field="binding.role")
    for key in ("memory_revision", "registry_revision", "role_profile_revision"):
        value = binding.get(key)
        if value is None:
            continue
        if not _is_digest(value):
            raise BindingError(
                f"binding.{key} must be an exact sha256 revision", field=key)
        if current is not None and current.get(key) != value:
            raise BindingError(
                f"binding.{key} drifted from the current revision; refresh "
                "is required", field=key, bound=value,
                current=current.get(key))
        out[key] = value
    requirements = binding.get("artifact_requirements") or []
    normalized = []
    for req in requirements:
        if not isinstance(req, dict):
            raise BindingError("artifact_requirements rows must be objects")
        if not cartifact.is_exact_version(req.get("version")):
            raise BindingError(
                "artifact requirement version is not exact; latest / current "
                "and free-prose versions fail closed",
                artifact_id=req.get("artifact_id"), version=req.get("version"))
        normalized.append(req)
    out["artifact_requirements"] = cartifact.normalize_requirements(normalized)
    declared_digest = binding.get("artifact_dependency_digest")
    if declared_digest is not None:
        if not _is_digest(declared_digest):
            raise BindingError(
                "artifact_dependency_digest must be an exact sha256 digest")
        if normalized:
            computed = cartifact.dependency_digest(normalized)
            if computed != declared_digest:
                raise BindingError(
                    "artifact dependency digest does not match the exact "
                    "requirements; stale inputs fail closed",
                    declared=declared_digest, computed=computed)
        out["artifact_dependency_digest"] = declared_digest
    elif normalized:
        out["artifact_dependency_digest"] = cartifact.dependency_digest(normalized)
    if artifact_store is not None and normalized:
        for req in normalized:
            resolved = cartifact.resolve_exact(
                artifact_store, req["artifact_id"], req["version"])
            if not resolved.get("ok"):
                raise BindingError(
                    "artifact requirement cannot be resolved at the exact "
                    "version; stale or missing inputs block the affected path",
                    artifact_id=req["artifact_id"], version=req["version"],
                    code=resolved.get("code"))
            status = resolved["envelope"].get("status")
            if status in cartifact.INPUT_BLOCKED_STATUSES:
                raise BindingError(
                    "artifact requirement is stale or superseded; the "
                    "affected review/QA path is blocked",
                    artifact_id=req["artifact_id"], version=req["version"],
                    status=status)
    if binding.get("artifact_ready") is not None:
        out["artifact_ready"] = bool(binding["artifact_ready"])
        if not out["artifact_ready"]:
            raise BindingError(
                "artifact binding is not ARTIFACT_READY; the affected review "
                "path is blocked")
    return out


def process_boundary(boundary: str, request: dict, task_scope, *,
                     prepared: dict | None = None, binding: dict | None = None,
                     current=None, findings=None, store=None, mutator=None,
                     packages=None, artifact_store=None,
                     ledger=None, transaction_id: str = "") -> dict:
    """Process open Findings at one natural boundary.

    SELF_CHECK / PREPARE_HANDOFF reuse the T01 FINDING_GATE (T04B for the
    current-role integration) verbatim. TASK_COMPLETION drains. CHALLENGE_CONTEXT
    performs targeted revalidation. No duplicates, no policy copy.
    """
    if boundary not in BOUNDARIES:
        raise FindingError(f"unknown boundary {boundary!r}", boundary=boundary)
    if boundary == BOUNDARY_COMPLETION:
        raise FindingError(
            "TASK_COMPLETION uses drain_task_findings (explicit dispositions)",
            boundary=boundary)
    if boundary == BOUNDARY_CHALLENGE:
        raise FindingError(
            "CHALLENGE_CONTEXT uses challenge_context (targeted revalidation)",
            boundary=boundary)
    if not isinstance(request, dict) or not request.get("task_ref"):
        raise FindingError("request.task_ref is required")
    bound = validate_binding(binding, current=current, packages=packages,
                             artifact_store=artifact_store) if binding else None
    if binding is not None and bound.get("task_ref") and \
            bound["task_ref"] != request.get("task_ref"):
        raise FindingError(
            "binding task_ref does not match the processing request",
            binding_task_ref=bound["task_ref"], request_task_ref=request.get("task_ref"))
    before = _store_docs(store)
    if boundary == BOUNDARY_SELF_CHECK:
        gate = selfcheck.run_current_role_finding_gate(
            request, task_scope, findings=findings, finding_store=store,
            mutator=mutator)
    else:
        gate = plan.finding_gate(
            request, task_scope, findings=findings,
            store=store if store is not None else plan.MemoryFindingStore(findings or []),
            mutator=mutator or plan.NoCanonicalWriteMutator(),
            boundary="handoff")
    relevant = [copy.deepcopy(f) for f in gate.get("relevant_open_findings") or []]
    selection_digest = _digest(sorted(
        row["digest"] for row in _selection_rows(relevant)))
    status = gate.get("status")
    mapped = {
        "CLEAR": STATUS_CLEAR,
        "REFRESH_REQUIRED": STATUS_REFRESH_REQUIRED,
        "BLOCKED": STATUS_BLOCKED,
    }.get(status)
    reason_codes = []
    if mapped is None:
        raise FindingError(
            f"gate returned an unknown status {status!r}", status=status)
    ready_allowed = mapped == STATUS_CLEAR
    if boundary == BOUNDARY_PREPARE:
        if _post_prepare_change(prepared, relevant):
            mapped = STATUS_REFRESH_REQUIRED
            ready_allowed = False
            reason_codes.append("POST_PREPARE_MATERIAL_CHANGE")
        elif mapped == STATUS_BLOCKED:
            ready_allowed = False
            reason_codes.append("UNRESOLVED_MATERIAL_FINDING")
    if bound is not None and bound.get("artifact_ready") is False:
        mapped = STATUS_REFRESH_REQUIRED
        ready_allowed = False
        reason_codes.append("ARTIFACT_NOT_READY")
    escalation = {"required": False}
    if mapped == STATUS_BLOCKED:
        blocked = gate.get("blocked_findings") or []
        reason = (gate.get("escalation") or {}).get("reason") or "unresolved_material_finding"
        target = None
        if blocked:
            intent = blocked[0].get("intent")
            target = "context-engineer" if intent == "context_challenge" else None
        refs = sorted({r for f in relevant for r in (f.get("source_refs") or [])})
        proposal = _proposal(
            task_ref=request.get("task_ref"),
            reason_code=reason if reason in ESCALATION_REASONS else "critical_context_review",
            classification=CLASS_CONTEXT_CONFLICT,
            package_id=(bound or {}).get("package_id"),
            affected_refs=[f.get("finding_id") for f in relevant if f.get("finding_id")],
            evidence_refs=refs,
            attempted={"boundary": boundary, "gate_status": status,
                       "blocked_findings": [b.get("finding_id") for b in blocked],
                       "reason": reason},
            recommended_target=target,
            boundary=boundary)
        escalation = {"required": True, "reason": reason,
                      "recommended_target": target,
                      "proposal": proposal}
    after = _store_docs(store)
    envelope = {
        "schema_version": PROCESSING_SCHEMA,
        "kind": "finding_processing_result",
        "boundary": boundary,
        "status": mapped,
        "ready_allowed": ready_allowed,
        "reason_codes": reason_codes,
        "selection": _selection_rows(relevant),
        "processed_findings": [f.get("finding_id")
                               for f in gate.get("processed_findings") or []],
        "remaining_nonblocking_findings": [
            f.get("finding_id")
            for f in gate.get("remaining_nonblocking_findings") or []],
        "blocked_findings": [b.get("finding_id")
                             for b in gate.get("blocked_findings") or []],
        "selection_digest": selection_digest,
        "store_digest_before": store_digest(before),
        "store_digest_after": store_digest(after),
        "gate": gate,
        "binding": bound,
        "escalation": escalation,
        "context_engineer_woken": False,
        "canonical_write": False,
        "canonical_changed": bool(gate.get("canonical_changed")),
        "direct_trigger": False,
        "llm_called": False,
    }
    _ledger_append(ledger, transaction_id, {
        "kind": "finding_processing", "boundary": boundary,
        "status": mapped, "ready_allowed": ready_allowed,
        "processed": envelope["processed_findings"],
        "remaining": envelope["remaining_nonblocking_findings"],
        "blocked": envelope["blocked_findings"],
        "selection_digest": selection_digest,
        "escalation_required": bool(escalation.get("required"))})
    return envelope


def drain_task_findings(task_ref: str, task_scope, *, findings=None, store=None,
                        decisions=None, ledger=None,
                        transaction_id: str = "") -> dict:
    """TASK_COMPLETION drain: every task-associated open Finding needs an
    explicit accounted disposition (owner + evidence + gate). A task cannot
    close with an unaccounted open Finding. Deterministic and idempotent.
    """
    task_ref = _require_text(task_ref, "task_ref")
    if not isinstance(decisions, list):
        raise DrainError("decisions must be a list")
    store = store if store is not None else plan.MemoryFindingStore(findings or [])
    before = _store_docs(store)
    open_findings = _open_task_findings(task_ref, task_scope, store=store)
    open_ids = {f.get("finding_id") for f in open_findings}
    processed_by_id = {f.get("finding_id"): f for f in before
                       if f.get("status") == "processed"}
    rows, invalid = [], []
    decided = set()
    for decision in decisions:
        if not isinstance(decision, dict):
            invalid.append({"decision": decision, "reason": "not_an_object"})
            continue
        fid = decision.get("finding_id")
        disposition = decision.get("disposition")
        if fid in decided:
            invalid.append({"finding_id": fid, "reason": "duplicate_decision"})
            continue
        row = DRAIN_DISPOSITIONS.get(disposition)
        if row is None:
            invalid.append({"finding_id": fid, "reason": "unknown_disposition",
                            "disposition": disposition})
            continue
        already = fid in processed_by_id
        if fid not in open_ids and not already:
            invalid.append({"finding_id": fid, "reason": "not_open_task_finding"})
            continue
        if already and processed_by_id[fid].get("disposition") != row["frozen"]:
            invalid.append({"finding_id": fid,
                            "reason": "already_processed_other_disposition"})
            continue
        if row["gate_required"]:
            gate = decision.get("gate")
            if gate not in KNOWN_GATES:
                invalid.append({"finding_id": fid, "reason": "gate_invalid",
                                "gate": gate})
                continue
        owner = decision.get("owner")
        try:
            owner = _require_role(owner, field="owner")
        except FindingError:
            invalid.append({"finding_id": fid, "reason": "owner_invalid",
                            "owner": decision.get("owner")})
            continue
        evidence_ref = decision.get("evidence_ref")
        if not _exact_ref(evidence_ref):
            invalid.append({"finding_id": fid, "reason": "evidence_ref_invalid",
                            "evidence_ref": evidence_ref})
            continue
        decided.add(fid)
        rows.append({
            "finding_id": fid,
            "classification": decision.get("classification"),
            "disposition": disposition,
            "frozen_disposition": row["frozen"],
            "owner": owner,
            "gate": decision.get("gate"),
            "evidence_ref": evidence_ref,
            "note": decision.get("note"),
            "already_processed": already,
        })
    unaccounted = sorted(open_ids - decided)
    if invalid or unaccounted:
        status = STATUS_BLOCKED
        concluded = False
    else:
        status = STATUS_DRAINED
        concluded = True
    writes = 0
    if concluded:
        pending = [row for row in rows if not row["already_processed"]]
        replayed = not pending
        if not replayed:
            by_id = {f.get("finding_id"): f for f in open_findings}
            for row in pending:
                finding = by_id[row["finding_id"]]
                note = f"u09:{row['disposition']}:{row['owner']}:{row['evidence_ref']}"
                store.mark_processed(finding, row["frozen_disposition"], note)
                writes += 1
    else:
        replayed = False
    after = _store_docs(store)
    envelope = {
        "schema_version": DRAIN_SCHEMA,
        "kind": "task_drain_result",
        "drain_id": "DRAIN-U09-" + _digest({
            "task_ref": task_ref, "rows": rows})[7:23],
        "task_ref": task_ref,
        "status": status,
        "concluded": concluded,
        "rows": rows,
        "invalid_decisions": invalid,
        "unaccounted_open_findings": unaccounted,
        "writes": writes,
        "replayed": replayed,
        "store_digest_before": store_digest(before),
        "store_digest_after": store_digest(after),
        "context_engineer_woken": False,
        "canonical_write": False,
        "direct_trigger": False,
        "task_closed_with_open_unaccounted_finding": bool(unaccounted),
    }
    _ledger_append(ledger, transaction_id, {
        "kind": "finding_drain", "task_ref": task_ref, "status": status,
        "accounted": sorted(decided), "unaccounted": unaccounted,
        "replayed": replayed, "writes": writes})
    return envelope


def _challenge_signal(challenge: dict, *, target_ref: str, digest: str,
                      captured_at: str) -> dict:
    """V1-compatible external_signal bridge (`source_type: memory_challenge`)."""
    summary = (challenge.get("summary") or challenge.get("reason_code") or "").strip()
    signal_id = "challenge-u09-" + _digest({
        "task_ref": challenge.get("task_ref"),
        "role": challenge.get("role"),
        "target_ref": target_ref,
        "reason_code": challenge.get("reason_code")})[7:23]
    return {
        "schema_version": "1.0",
        "kind": "external_signal",
        "id": signal_id,
        "source_type": "memory_challenge",
        "locator": target_ref,
        "captured_at": captured_at,
        "observed_at": captured_at,
        "summary": summary or "challenge awaiting adjudication",
        "reliability": "disputed",
        "content_hash": digest,
        "notes": "U09 challenge_context result; no canonical unit was modified",
    }


def challenge_context(challenge: dict, *, task_scope=None, findings=None,
                      store=None, current=None, packages=None, registry=None,
                      artifact_store=None, artifact_state=None,
                      ledger=None, transaction_id: str = "", clock=None) -> dict:
    """CHALLENGE_CONTEXT: immediate targeted revalidation against exact
    package/context/artifact/evidence revisions. Never a direct role hop;
    unresolved material exceptions become Lead-addressed proposals only.
    """
    if not isinstance(challenge, dict) or \
            challenge.get("kind") != "challenge_context_request":
        raise ChallengeError("kind must be challenge_context_request")
    task_ref = _require_text(challenge.get("task_ref"), "task_ref")
    role = _require_role(challenge.get("role"), field="role")
    target = challenge.get("target")
    if not isinstance(target, dict) or not target.get("ref"):
        raise ChallengeError("target.ref is required")
    kind = target.get("kind")
    if kind not in ("package", "context", "artifact", "evidence"):
        raise ChallengeError(
            f"target.kind {kind!r} must be package/context/artifact/evidence")
    ref = _require_text(target.get("ref"), "target.ref")
    reason_code = challenge.get("reason_code")
    if reason_code not in CHALLENGE_REASONS:
        raise ChallengeError(
            f"reason_code {reason_code!r} is not a typed challenge reason",
            reason_code=reason_code)
    exchange_round = challenge.get("exchange_round") or 1
    if not isinstance(exchange_round, int) or exchange_round < 1:
        raise ChallengeError("exchange_round must be a positive integer")
    evidence_refs = _require_refs(challenge.get("evidence_refs") or [],
                                  "evidence_refs", minimum=1)
    binding = challenge.get("binding")
    if not isinstance(binding, dict) or not binding:
        raise ChallengeError(
            "an exact revision binding is required; a challenge without bound "
            "package/context/artifact revisions fails closed")
    # Structural validation only: revision drift is a challenge outcome
    # (REFRESH_REQUIRED), not a malformed request. Exact versions still fail
    # closed before any revalidation happens.
    try:
        bound = validate_binding(binding, current=None, packages=packages)
    except FindingError as exc:
        raise ChallengeError(
            f"challenge binding is invalid: {exc.message}",
            cause=exc.code, **exc.details) from exc
    if bound.get("task_ref") and bound["task_ref"] != task_ref:
        raise ChallengeError(
            "binding task_ref does not match the challenge task_ref")
    if kind == "package":
        if not bound.get("package_id"):
            raise ChallengeError(
                "a package challenge requires binding.package_id")
        if packages is not None and not any(
                e.get("package_id") == bound["package_id"] for e in packages):
            raise ChallengeError(
                "challenge package does not resolve in the exact package set",
                package_id=bound["package_id"])
        if ref != bound["package_id"]:
            raise ChallengeError(
                "target.ref must equal the exact bound package_id",
                target_ref=ref, package_id=bound["package_id"])
    if kind == "artifact":
        if not cartifact.is_exact_version(target.get("version")):
            raise ChallengeError(
                "an artifact challenge requires an exact version; latest / "
                "current fail closed", version=target.get("version"))
    if kind == "evidence":
        if not _is_digest(target.get("digest")):
            raise ChallengeError(
                "an evidence challenge requires target.digest sha256")
    _, target_digest = _target_digest(target, bound)

    revision_reasons = []
    revisions = current if current is not None else selfcheck.current_revisions()
    for key, reason in (
        ("memory_revision", "memory_revision_changed"),
        ("registry_revision", "registry_revision_changed"),
        ("role_profile_revision", "role_profile_revision_changed"),
    ):
        bound_value = bound.get(key)
        if bound_value and revisions.get(key) != bound_value:
            revision_reasons.append(reason)

    status = STATUS_RESOLVED
    reason_codes = list(revision_reasons)
    action = "CONTINUE"
    gate = None
    artifact_blocked = False
    if kind == "artifact":
        state = artifact_state or {}
        if artifact_store is not None:
            resolved = cartifact.resolve_exact(
                artifact_store, target.get("artifact_id") or ref,
                target.get("version"))
            if not resolved.get("ok"):
                artifact_blocked = True
                reason_codes.append(resolved.get("code") or "ARTIFACT_UNRESOLVED")
            elif resolved["envelope"].get("status") in \
                    cartifact.INPUT_BLOCKED_STATUSES:
                artifact_blocked = True
                reason_codes.append("ARTIFACT_STALE")
        elif state:
            if state.get("status") in cartifact.INPUT_BLOCKED_STATUSES:
                artifact_blocked = True
                reason_codes.append("ARTIFACT_STALE")
    if revision_reasons or artifact_blocked:
        status = STATUS_REFRESH_REQUIRED
        action = "REFRESH"
    elif reason_code in ("evidence_conflict", "authority_gap", "context_gap"):
        # targeted revalidation through the accepted T01 / T04B gate only
        if task_scope is not None:
            gate = selfcheck.run_current_role_finding_gate(
                {"task_ref": task_ref, "role": role,
                 "task_snapshot": challenge.get("task_snapshot") or {}},
                task_scope, findings=findings, finding_store=store)
            if gate.get("status") == "BLOCKED":
                status = STATUS_UNRESOLVED_MATERIAL
                action = "ESCALATE_TO_LEAD"
                reason_codes.append("UNRESOLVED_MATERIAL_FINDING")
            elif gate.get("canonical_changed"):
                status = STATUS_REFRESH_REQUIRED
                action = "REFRESH"
                reason_codes.append("memory_revision_changed")
            else:
                status = STATUS_RESOLVED
                action = "CONTINUE"
        else:
            status = STATUS_RESOLVED
            action = "CONTINUE"
    elif reason_code in LEAD_DECISION_REASONS:
        status = STATUS_RETURNED_TO_LEAD
        action = "ESCALATE_TO_LEAD"
        reason_codes.append(reason_code)
    else:
        status = STATUS_UNRESOLVED_MATERIAL
        action = "ESCALATE_TO_LEAD"
        reason_codes.append("UNRESOLVED_MATERIAL")

    if exchange_round >= 2 and status != STATUS_RESOLVED:
        status = STATUS_STOPPED_ESCALATED
        action = "ESCALATE_TO_LEAD"

    proposal = None
    if status in (STATUS_UNRESOLVED_MATERIAL, STATUS_RETURNED_TO_LEAD,
                  STATUS_STOPPED_ESCALATED):
        target_role = LEAD_DECISION_REASONS.get(reason_code)
        if target_role is None and status == STATUS_UNRESOLVED_MATERIAL:
            target_role = "context-engineer"
        proposal = _proposal(
            task_ref=task_ref,
            reason_code=reason_code if reason_code in ESCALATION_REASONS
            else "critical_context_review",
            classification=CLASS_PRODUCT_CHALLENGE if reason_code ==
            "product_expectation_conflict" else
            CLASS_CONTEXT_CONFLICT if reason_code in ("evidence_conflict", "authority_gap")
            else CLASS_DESIGN_CHALLENGE if reason_code == "design_baseline_wrong"
            else None,
            package_id=bound.get("package_id"),
            affected_refs=[ref],
            evidence_refs=evidence_refs,
            attempted={"boundary": BOUNDARY_CHALLENGE, "reason_code": reason_code,
                       "gate_status": (gate or {}).get("status"),
                       "exchange_round": exchange_round},
            recommended_target=target_role,
            boundary=BOUNDARY_CHALLENGE)
    signal = _challenge_signal(
        {"task_ref": task_ref, "role": role, "reason_code": reason_code,
         "summary": challenge.get("summary")},
        target_ref=ref, digest=target_digest,
        captured_at=(clock or now_iso)())
    envelope = {
        "schema_version": CHALLENGE_SCHEMA,
        "kind": "challenge_revalidation_result",
        "challenge_id": "CHAL-U09-" + _digest({
            "task_ref": task_ref, "role": role, "target_ref": ref,
            "reason_code": reason_code, "binding": boundary_safe_binding(bound)})[7:23],
        "task_ref": task_ref,
        "role": role,
        "target": {"kind": kind, "ref": ref,
                   "version": target.get("version"),
                   "digest": target.get("digest")},
        "status": status,
        "action": action,
        "reason_codes": sorted(set(reason_codes)),
        "binding": bound,
        "revalidated_against": {
            "package_id": bound.get("package_id"),
            "memory_revision": revisions.get("memory_revision"),
            "registry_revision": revisions.get("registry_revision"),
            "role_profile_revision": revisions.get("role_profile_revision"),
            "target_digest": target_digest,
        },
        "gate": gate,
        "escalation": ({"required": True, "reason": reason_code,
                        "recommended_target": proposal["recommended_target"],
                        "proposal": proposal} if proposal else {"required": False}),
        "v1_signal": signal,
        "dispatch": {"trigger_emitted": False,
                     "assignment_emitted": False, "status_write_emitted": False,
                     "role_hop": False},
        "context_engineer_woken": False,
        "canonical_write": False,
        "direct_trigger": False,
        "llm_called": False,
    }
    _ledger_append(ledger, transaction_id, {
        "kind": "challenge_revalidation", "challenge_id": envelope["challenge_id"],
        "target_ref": ref, "status": status, "reason_code": reason_code,
        "escalation_required": bool(envelope["escalation"].get("required"))})
    if proposal:
        _ledger_append(ledger, transaction_id, {
            "kind": "escalation_proposal", "proposal_id": proposal["proposal_id"],
            "recommended_target": proposal["recommended_target"],
            "reason_code": proposal["reason_code"],
            "trigger_emitted": False})
    return envelope


def boundary_safe_binding(bound: dict) -> dict:
    return {k: bound.get(k) for k in (
        "package_id", "task_ref", "role", "memory_revision",
        "registry_revision", "role_profile_revision",
        "artifact_dependency_digest")}


def _target_digest(target: dict, bound: dict) -> tuple:
    if target.get("kind") == "package":
        return target.get("kind"), bound.get("package_id") or ""
    if target.get("kind") == "artifact":
        return target.get("kind"), _digest({
            "artifact_id": target.get("artifact_id") or target.get("ref"),
            "version": target.get("version")})
    if target.get("kind") == "evidence":
        return target.get("kind"), target.get("digest") or ""
    return target.get("kind"), _digest({"ref": target.get("ref"),
                                        "binding": boundary_safe_binding(bound)})


def finding_side_effect_audit(records: list) -> dict:
    """Deterministic side-effect audit over the shared ledger records."""
    command_audit = dispatch.audit_ledger(records)
    kinds = {}
    for record in records or []:
        if record.get("kind") == "command":
            continue
        kinds[record.get("kind")] = kinds.get(record.get("kind"), 0) + 1
    triggers = [r for r in records or []
                if any(k in r for k in ("trigger_emitted",)) and r.get("trigger_emitted")]
    return {
        "schema_version": AUDIT_SCHEMA,
        "kind": "finding_side_effect_audit",
        "typed_records": kinds,
        "command_audit": command_audit,
        "live_writes": 0,
        "live_triggers": 0,
        "user_visible_triggers": 0,
        "real_assignments": 0,
        "status_writes": 0,
        "context_engineer_wakes": 0,
        "canonical_writes": 0,
        "product_repo_changes": 0,
        "trigger_records": len(triggers),
        "ok": bool(command_audit.get("ok")) and not triggers,
    }


def u08_finding_dispositions() -> dict:
    """The four U08-recorded findings, classified and gated by U09 with
    explicit typed dispositions. No U06/U07/U08 redesign, no U11/U12 work."""
    rows = [
        {
            "finding_id": "FIND-WIMG-U08-000001",
            "source": "U08 report Finding 1 (context-derived package identity)",
            "classification": CLASS_CONTEXT_CONFLICT,
            "material": True,
            "disposition": "DEFERRED_GATE",
            "gate": "PRE_U12_PIN_GATE",
            "owner": LEAD_ROLE,
            "evidence_ref": "repo://multica-memory@6f541f715aa07d45f60c7c50e855952b64bcc9af/adapters/multica/U08_DIRECT_ASSIGNMENT_FALLBACK_REPORT.md",
            "note": "packet identity coincidence is fail-closed today; the "
                    "route-bound identity decision is a Lead pin-gate decision",
            "redesign_performed": False,
        },
        {
            "finding_id": "FIND-WIMG-U08-000002",
            "source": "U08 report Finding 2 (U07 raw-byte pin basis)",
            "classification": CLASS_CONTEXT_CONFLICT,
            "material": False,
            "disposition": "DEFERRED_GATE",
            "gate": "PRE_U12_PIN_GATE",
            "owner": LEAD_ROLE,
            "evidence_ref": "repo://multica-memory@6f541f715aa07d45f60c7c50e855952b64bcc9af/adapters/multica/U08_DIRECT_ASSIGNMENT_FALLBACK_REPORT.md",
            "note": "U09 must not repin the U07 byte basis; decision belongs to "
                    "the pre-U12 pin gate",
            "redesign_performed": False,
        },
        {
            "finding_id": "FIND-WIMG-U08-000003",
            "source": "U08 report Finding 3 (U12 non-set skill-removal capability)",
            "classification": CLASS_TASK_COGNITION,
            "material": False,
            "disposition": "DEFERRED_GATE",
            "gate": "U12_ENABLEMENT",
            "owner": LEAD_ROLE,
            "evidence_ref": "repo://multica-memory@6f541f715aa07d45f60c7c50e855952b64bcc9af/adapters/multica/U08_DIRECT_ASSIGNMENT_FALLBACK_REPORT.md",
            "note": "enablement blocker stays open; U09 performs no U12 work",
            "redesign_performed": False,
        },
        {
            "finding_id": "FIND-WIMG-U08-000004",
            "source": "U08 report Finding 4 (closed-source execution guard wiring)",
            "classification": CLASS_TASK_COGNITION,
            "material": False,
            "disposition": "DEFERRED_GATE",
            "gate": "U11_JOINT_REPLAY",
            "owner": LEAD_ROLE,
            "evidence_ref": "repo://multica-memory@6f541f715aa07d45f60c7c50e855952b64bcc9af/adapters/multica/U08_DIRECT_ASSIGNMENT_FALLBACK_REPORT.md",
            "note": "U11 joint replay wires the live guard; U09 only classifies "
                    "and gates the recorded property",
            "redesign_performed": False,
        },
    ]
    return {
        "schema_version": "U09-u08-finding-dispositions/2.2",
        "kind": "u08_finding_dispositions",
        "count": len(rows),
        "unaccounted_open_findings": [],
        "rows": rows,
    }


def historical_inventory() -> dict:
    """Historical Finding/Challenge behavior classified for U09."""
    retained = [
        {"item": "REPORT_FINDING capture-only ingest", "decision": "retained"},
        {"item": "Finding.open lifecycle", "decision": "retained"},
        {"item": "T01 internal FINDING_GATE", "decision": "retained"},
        {"item": "T04B current-role integration", "decision": "retained"},
        {"item": "V1 challenge signal shape (`source_type: memory_challenge`)",
         "decision": "retained"},
        {"item": "deterministic dispositions and replay idempotence",
         "decision": "retained"},
        {"item": "task-scoped drain at completion", "decision": "adapted"},
        {"item": "package/context/artifact revision binding", "decision": "adapted"},
        {"item": "escalation to 02 on material exception", "decision": "adapted"},
        {"item": "Grok raw output as project truth", "decision": "invalid"},
        {"item": "ordinary Finding waking 02", "decision": "invalid"},
        {"item": "producer/reviewer/QA direct 02 trigger", "decision": "invalid"},
        {"item": "local Review/QA defect as automatic Runtime Finding",
         "decision": "invalid"},
        {"item": "free-form operator routing of Findings", "decision": "invalid"},
        {"item": "second Finding lifecycle/store", "decision": "invalid"},
        {"item": "U11 joint replay", "decision": "deferred"},
        {"item": "U12 live enablement", "decision": "deferred"},
    ]
    counts = {"retained": 0, "adapted": 0, "invalid": 0, "deferred": 0}
    for row in retained:
        counts[row["decision"]] += 1
    return {
        "schema_version": "U09-historical-inventory/2.2",
        "kind": "finding_challenge_historical_inventory",
        "counts": counts,
        "rows": retained,
    }


def boundary_matrix() -> list:
    return [
        {"boundary": BOUNDARY_SELF_CHECK,
         "selection": "current-role relevant open Findings",
         "engine": "T04B current-role integration over T01 FINDING_GATE",
         "clear": "CLEAR / continue",
         "refresh": "REFRESH_REQUIRED when processing changed Canonical state",
         "blocked": "BLOCKED + Lead-addressed escalation proposal",
         "wakes_context_engineer_by_default": False},
        {"boundary": BOUNDARY_PREPARE,
         "selection": "target-role relevant open Findings",
         "engine": "T01 FINDING_GATE (boundary=handoff)",
         "clear": "CLEAR -> ready_allowed",
         "refresh": "post-prepare material change -> ready refused / refresh",
         "blocked": "READY_REFUSED + Lead-addressed escalation proposal",
         "wakes_context_engineer_by_default": False},
        {"boundary": BOUNDARY_COMPLETION,
         "selection": "every task-associated open Finding",
         "engine": "drain_task_findings with explicit dispositions",
         "clear": "DRAINED only when zero unaccounted open Findings",
         "refresh": "not applicable",
         "blocked": "BLOCKED with unaccounted_open_findings listed",
         "wakes_context_engineer_by_default": False},
        {"boundary": BOUNDARY_CHALLENGE,
         "selection": "one exact package/context/artifact/evidence target",
         "engine": "challenge_context targeted revalidation (+ T04B gate)",
         "clear": "RESOLVED / continue",
         "refresh": "revision drift or stale artifact -> REFRESH_REQUIRED",
         "blocked": "UNRESOLVED_MATERIAL / RETURNED_TO_LEAD / STOPPED_ESCALATED"
                    " -> proposal to Lead only",
         "wakes_context_engineer_by_default": False},
    ]


def success_failure_matrix() -> list:
    return [
        {"case": "capture_valid", "expect": "open, process_now=false, "
         "wake_context_engineer=false, canonical_write=false", "fail_closed": True},
        {"case": "capture_missing_evidence", "expect": "CAPTURE_INVALID", "fail_closed": True},
        {"case": "capture_missing_scope", "expect": "SCOPE_INVALID", "fail_closed": True},
        {"case": "capture_retired_role", "expect": "RETIRED_IDENTITY_REFUSED", "fail_closed": True},
        {"case": "capture_display_name", "expect": "ROLE_NOT_V22", "fail_closed": True},
        {"case": "capture_duplicate_same_content", "expect": "IDEMPOTENT_NO_WRITE", "fail_closed": True},
        {"case": "capture_duplicate_conflict", "expect": "DUPLICATE_CAPTURE_CONFLICT", "fail_closed": True},
        {"case": "classification_impl_local", "expect": "ARTIFACT_ONLY_NO_RUNTIME_FINDING", "fail_closed": True},
        {"case": "classification_impl_cognition", "expect": "ONE_RUNTIME_FINDING", "fail_closed": True},
        {"case": "classification_delivery_review_local", "expect": "ARTIFACT_ONLY", "fail_closed": True},
        {"case": "classification_qa_local", "expect": "ARTIFACT_ONLY", "fail_closed": True},
        {"case": "classification_authority_conflict", "expect": "MATERIAL_RUNTIME_FINDING", "fail_closed": True},
        {"case": "classification_product_challenge", "expect": "LEAD_DECISION_02", "fail_closed": True},
        {"case": "classification_design_challenge", "expect": "LEAD_DECISION_03", "fail_closed": True},
        {"case": "classification_design_deviation", "expect": "DESIGN_DEVIATION_RECORDED", "fail_closed": True},
        {"case": "classification_grok_raw", "expect": "EVIDENCE_ONLY_NOT_TRUTH", "fail_closed": True},
        {"case": "classification_ambiguous", "expect": "CLASSIFICATION_AMBIGUOUS", "fail_closed": True},
        {"case": "boundary_self_check_clear", "expect": "CLEAR_CONTINUE", "fail_closed": True},
        {"case": "boundary_self_check_material", "expect": "ESCALATION_PROPOSAL_NO_WAKE", "fail_closed": True},
        {"case": "boundary_prepare_material_unprocessed", "expect": "READY_REFUSED", "fail_closed": True},
        {"case": "boundary_prepare_post_change", "expect": "REFRESH_REQUIRED", "fail_closed": True},
        {"case": "boundary_completion_unaccounted", "expect": "BLOCKED_DRAIN", "fail_closed": True},
        {"case": "boundary_completion_decided", "expect": "DRAINED", "fail_closed": True},
        {"case": "boundary_completion_replay", "expect": "NO_DUPLICATE_WRITES", "fail_closed": True},
        {"case": "challenge_exact_package", "expect": "RESOLVED_CONTINUE", "fail_closed": True},
        {"case": "challenge_revision_drift", "expect": "REFRESH_REQUIRED", "fail_closed": True},
        {"case": "challenge_missing_binding", "expect": "CHALLENGE_INVALID", "fail_closed": True},
        {"case": "challenge_package_unresolved", "expect": "CHALLENGE_INVALID", "fail_closed": True},
        {"case": "challenge_design_challenge", "expect": "RETURNED_TO_LEAD", "fail_closed": True},
        {"case": "challenge_product_challenge", "expect": "RETURNED_TO_LEAD", "fail_closed": True},
        {"case": "challenge_ordinary_gap", "expect": "NO_02_WAKE", "fail_closed": True},
        {"case": "challenge_unresolved_material", "expect": "PROPOSAL_TO_LEAD", "fail_closed": True},
        {"case": "challenge_second_round", "expect": "STOPPED_ESCALATED", "fail_closed": True},
        {"case": "challenge_stale_artifact", "expect": "REFRESH_REQUIRED_BLOCKED_PATH", "fail_closed": True},
        {"case": "artifact_version_latest", "expect": "VERSION_NOT_EXACT", "fail_closed": True},
        {"case": "artifact_digest_mismatch", "expect": "STALE_INPUTS_REFUSED", "fail_closed": True},
        {"case": "escalation_requires_lead", "expect": "ADDRESSED_TO_LEAD_NO_TRIGGER", "fail_closed": True},
        {"case": "routing_zero_direct_triggers", "expect": "NO_DIRECT_02_03_04_TRIGGER", "fail_closed": True},
        {"case": "grok_promotion_refused", "expect": "AUTHORITY_ELIGIBLE_FALSE", "fail_closed": True},
        {"case": "hidden_material_finding", "expect": "REFUSED_BY_READY_GATE", "fail_closed": True},
        {"case": "replay_idempotent", "expect": "ZERO_DUPLICATE_SIDE_EFFECTS", "fail_closed": True},
    ]


def decision_table() -> list:
    rows = []
    for row in classification_matrix():
        rows.append({
            "classification": row["classification"],
            "route": row["route"],
            "owner_role": row["owner_role"],
            "lead_decision_target": row["lead_decision_target"],
            "requires_lead": bool(row["lead_decision_target"]),
            "direct_trigger": False,
            "wakes_context_engineer": False,
        })
    return rows


def acceptance_evidence() -> dict:
    return {
        "schema_version": "U09-acceptance-evidence/2.2",
        "kind": "u09_acceptance_evidence",
        "capture": {
            "report_finding_is_capture_only": True,
            "process_now_default": False,
            "wake_context_engineer_default": False,
            "canonical_write_default": False,
        },
        "classification": {
            "review_qa_local_defect_is_not_automatic_runtime_finding": True,
            "reusable_cognition_is_runtime_finding": True,
            "grok_raw_truth_to_project": False,
        },
        "boundaries": {
            "self_check_current_role_gate": True,
            "prepare_handoff_target_role_gate": True,
            "task_completion_finding_drain": True,
            "challenge_context_targeted_revalidation": True,
            "handoff_ready_hides_material_finding": False,
        },
        "routing": {
            "ordinary_finding_wakes_02": False,
            "direct_nonlead_02_03_04_trigger": False,
            "unresolved_material_exception_requires_lead": True,
            "product_design_implementation_routes_classified": True,
        },
        "safety": {
            "frozen_t00_amended": False,
            "upstream_pins_preserved": True,
            "live_mutations_or_triggers": 0,
            "canonical_writes": 0,
            "product_repo_changes": 0,
        },
    }


EVIDENCE_CLOCK = "2026-09-11T00:00:00Z"


def _fixture_findings():
    common = {
        "schema_version": "1.1", "kind": "finding", "project_id": "web-imagegen",
        "task_id": "multica://issue/YZT-77", "status": "open",
        "verification": "verified", "created_at": EVIDENCE_CLOCK,
        "discovered_by": "software-engineer",
    }
    return [
        dict(common, finding_id="FIND-WIMG-U09-000001",
             summary="Task cognition: reusable handoff constraint for provider switching",
             intent="durable_candidate",
             source_refs=["repo://multica-memory/docs/context_handoff_native_api_design_v1.1.md"]),
        dict(common, finding_id="FIND-WIMG-U09-000002",
             summary="Local implementation defect: empty state on provider switch",
             intent="task_delivery",
             source_refs=["repo://multica-memory/tools/chandoff_plan.py"]),
        dict(common, finding_id="FIND-WIMG-U09-000003",
             summary="Context authority conflict: two rules disagree on provider scope",
             intent="context_challenge", verification="conflicted",
             source_refs=["repo://multica-memory/team-context/rules/RULE-WIMG-000001.yaml"]),
    ]


def evidence_bundle(out_dir, *, generated_at: str = EVIDENCE_CLOCK) -> dict:
    """Deterministic U09 evidence artifacts (no secrets, no live triggers)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    findings = _fixture_findings()
    store = plan.MemoryFindingStore(findings)
    ledger = dispatch.TransactionLedger()
    capture = capture_finding({
        "kind": "report_finding_request",
        "finding_id": "FIND-WIMG-U09-000010",
        "task_ref": "multica://issue/YZT-77",
        "reporting_role": "delivery-reviewer",
        "summary": "Reusable cognition: review must bind exact artifact versions",
        "detail": "Local review detail stays in the Delivery Review artifact.",
        "intent": "durable_candidate",
        "verification": "verified",
        "evidence_refs": [
            "repo://multica-memory/schemas/artifact-contract/common-envelope.schema.json"],
        "source_refs": ["multica://issue/YZT-77"],
        "affected_scope": {"type": "project", "project_id": "web-imagegen"},
        "origin": "delivery_review",
        "claim": None,
        "reusable_cognition": True,
        "material_context_change": False,
        "created_at": generated_at,
    }, store=store, ledger=ledger, transaction_id="u09-evidence")
    scope = resolve_scope({"type": "project", "project_id": "web-imagegen"})
    local_only = [f for f in findings if f["finding_id"] == "FIND-WIMG-U09-000002"]
    conflict_only = [f for f in findings if f["finding_id"] == "FIND-WIMG-U09-000003"]
    self_gate = process_boundary(
        BOUNDARY_SELF_CHECK,
        {"task_ref": "multica://issue/YZT-77", "role": "software-engineer",
         "task_snapshot": {"title": "U09 evidence self check"}},
        scope, findings=local_only, ledger=ledger, transaction_id="u09-evidence")
    prepare_gate = process_boundary(
        BOUNDARY_PREPARE,
        {"task_ref": "multica://issue/YZT-77",
         "target": {"role": "software-engineer"},
         "task_snapshot": {"title": "U09 evidence prepare"}},
        scope, findings=conflict_only,
        ledger=ledger, transaction_id="u09-evidence")
    drain_store = plan.MemoryFindingStore(findings[:1])
    drain = drain_task_findings(
        "multica://issue/YZT-77", scope, store=drain_store,
        decisions=[{
            "finding_id": "FIND-WIMG-U09-000001",
            "classification": CLASS_TASK_COGNITION,
            "disposition": "DEFERRED_GATE",
            "owner": LEAD_ROLE,
            "gate": "CHECKPOINT",
            "evidence_ref": "repo://multica-memory/team-context/checkpoint.yaml",
        }], ledger=ledger, transaction_id="u09-evidence")
    challenge = challenge_context({
        "kind": "challenge_context_request",
        "task_ref": "multica://issue/YZT-77",
        "role": "delivery-reviewer",
        "target": {"kind": "package", "ref": "CTX-software-engineer-0123456789abcdef"},
        "binding": {
            "package_id": "CTX-software-engineer-0123456789abcdef",
            "task_ref": "multica://issue/YZT-77",
            "role": "software-engineer",
            "memory_revision": chandoff.memory_revision(),
            "registry_revision": chandoff.registry_revision(),
            "role_profile_revision": chandoff.role_profile_revision(),
            "artifact_dependency_digest": cartifact.dependency_digest([]),
        },
        "reason_code": "context_gap",
        "evidence_refs": ["repo://multica-memory/tools/chandoff_finding.py"],
        "exchange_round": 1,
        "summary": "Ordinary context gap revalidation",
    }, task_scope=scope, findings=[],
       current=selfcheck.current_revisions(), ledger=ledger,
       transaction_id="u09-evidence", clock=lambda: generated_at)
    proposal = _proposal(
        task_ref="multica://issue/YZT-77",
        reason_code="critical_context_review",
        classification=CLASS_CONTEXT_CONFLICT,
        package_id="CTX-software-engineer-0123456789abcdef",
        affected_refs=["FIND-WIMG-U09-000002"],
        evidence_refs=["repo://multica-memory/tools/chandoff_finding.py"],
        attempted={"boundary": BOUNDARY_SELF_CHECK, "gate_status": "BLOCKED",
                   "exchange_round": 1},
        recommended_target="context-engineer",
        boundary=BOUNDARY_SELF_CHECK)
    audit = finding_side_effect_audit(ledger.records)
    artifacts = {
        "capabilities.json": {
            "schema_version": "U09-capabilities/2.2",
            "kind": "u09_capabilities",
            "layer": LAYER_VERSION,
            "boundaries": list(BOUNDARIES),
            "classification_count": len(CLASSES),
            "reuses": ["T01 FINDING_GATE", "T04B current-role integration",
                       "V1 memory_challenge signal shape",
                       "U06-U08 shared transaction ledger",
                       "U10 artifact dependency digest"],
            "live_mutations": 0,
        },
        "historical-inventory.json": historical_inventory(),
        "classification-matrix.json": {
            "schema_version": "U09-classification-matrix/2.2",
            "kind": "finding_classification_matrix",
            "rows": classification_matrix(),
        },
        "decision-table.json": {
            "schema_version": "U09-decision-table/2.2",
            "kind": "finding_challenge_decision_table",
            "rows": decision_table(),
        },
        "boundary-matrix.json": {
            "schema_version": "U09-boundary-matrix/2.2",
            "kind": "finding_boundary_matrix",
            "rows": boundary_matrix(),
        },
        "success-failure-matrix.json": {
            "schema_version": "U09-success-failure-matrix/2.2",
            "kind": "finding_success_failure_matrix",
            "rows": success_failure_matrix(),
        },
        "u08-finding-dispositions.json": u08_finding_dispositions(),
        "sample-capture-result.json": capture,
        "sample-boundary-self-check.json": self_gate,
        "sample-boundary-prepare.json": prepare_gate,
        "sample-boundary-completion.json": drain,
        "sample-challenge-resolved.json": challenge,
        "sample-escalation-proposal.json": proposal,
        "acceptance-evidence.json": acceptance_evidence(),
        "side-effect-audit.json": {
            **audit,
            "generated_at": generated_at,
        },
    }
    for name, doc in artifacts.items():
        (out / name).write_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8", newline="\n")
    (out / "sample-ledger.jsonl").write_text(
        ledger.to_jsonl(), encoding="utf-8", newline="\n")
    return {
        "out_dir": str(out),
        "files": sorted(list(artifacts) + ["sample-ledger.jsonl"]),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="U09 Finding/Challenge alignment")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evidence", help="write the deterministic evidence bundle")
    ev.add_argument("--out-dir", default="adapters/multica/finding-challenge")
    ev.add_argument("--generated-at", default=EVIDENCE_CLOCK)
    cap = sub.add_parser("capture", help="capture one REPORT_FINDING request")
    cap.add_argument("--capture-file", required=True)
    cap.add_argument("--store-dir", default=None)
    args = parser.parse_args(argv)
    if args.command == "evidence":
        report = evidence_bundle(args.out_dir, generated_at=args.generated_at)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "capture":
        request = json.loads(Path(args.capture_file).read_text(encoding="utf-8"))
        store = plan.RuntimeFindingStore(args.store_dir) if args.store_dir \
            else plan.MemoryFindingStore()
        result = capture_finding(request, store=store)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
