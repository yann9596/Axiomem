#!/usr/bin/env python3
"""U06 — Artifact-aware Assignment SAFE_DISPATCH orchestrator (YZT-74).

Rebases the historical T09 assignment route onto the accepted U05 V2.2
identity and the U04/U10 Artifact-aware shared skill. Mention routing stays
with U07; cross-route fallback stays with U08.

Parent sequence:

    NON_TRIGGER_MUTATION create/update
    → exact role/artifact binding
    → zero-unexpected-run check
    → PREPARE_HANDOFF
    → ARTIFACT_READY
    → publish non-trigger /note
    → re-resolve HANDOFF_READY and freshness
    → exactly one Assignment trigger
    → correlate exactly one intended run
    → target SELF_CHECK
    → consequential work

`t08_mapping` / `resolve_target` remain for the T10 mention orchestrator
(02 is never a mention-dispatch target). The Assignment route uses
`u05_mapping` / `resolve_assignment_target` (all six V2.2 roles, no
feature-reviewer alias).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cartifact  # noqa: E402
import chandoff  # noqa: E402
import chandoff_adapter as adapter  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
from chandoff_findings_source import (  # noqa: E402
    FindingsSourceRefusal, construct_simulation_entry,
    gate_effectful_findings, verify_worker_entry,
)
import chandoff_note as note  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402
from chandoff_instructions import (  # noqa: E402
    EXPECTED_AGENT_IDS,
    LIVE_DISPLAY_NAME,
    PINNED_ARTIFACT_CONTRACT,
    PINNED_SKILL_MD,
    RETIRED_ROLES,
    ROLE_BY_SLUG,
    ROLES,
    STAGED_DISPLAY_NAME,
    V22_ROLES,
    digest_skill_dir,
)
from cutil import ROOT, now_iso  # noqa: E402

ORCHESTRATOR_VERSION = "U06/2.2"
BUNDLE_SUBPATH = "adapters/multica/agent-instructions"
MAX_SELF_CHECK_ATTEMPTS = 2
MAX_COMPOSE_ATTEMPTS = 2

PINNED_U05_COMMIT = "b43b68b5deae6d8636f02293f2abb52ecb5f65e3"
PINNED_U04_COMMIT = "702cb9bb11120c154a216e66c6ed1d3d632d3b49"
PINNED_U10_COMMIT = "73f922ea33c2e2867ba51b6843588c5aa4980ff6"
PINNED_INSTRUCTION_BUNDLE = (
    "sha256:a93e146d6586cf6f474148aeed092032e3a9c871ad3138ee1152d28108b5b98f")
PINNED_BINDING_PLAN = (
    "sha256:7f83bfd523e2c0da3cd0dac4568869f3c9937e2d3ae6c0dc66b9853094a27c22")
PINNED_SKILL_BUNDLE = (
    "sha256:7f861c320c115b328fb45db7356573ae449a5e443ac08b3572a764c79934fce9")
# YZT-88 versions the U04 skill bundle forward. Historical pins above are
# preserved; U05 bundle.json is not rewritten. V2 forwards the bundle again
# because Skill/Instructions now require pipeline selfcheck with an explicit
# Findings source binding and forbid bare T06 / unbound context_cli as
# official entries.
YZT88_SKILL_BUNDLE_261DF9A = (
    "sha256:1a2645771e38def5f44dc809bce991ca18ca7c9ef36777611e9b88726c018bc2")
YZT88_SKILL_BUNDLE_D2B6299 = (
    "sha256:f54f0395410cb5ed9596a2e4366dd9375813127391cd4131613942d9f30533ff")
FORWARD_SKILL_BUNDLE = (
    "sha256:967b48443d5e47bdfe55aecacb92ca78fdd8f576e87602201513b6fc4633ecd6")
ACCEPTED_SKILL_BUNDLES = (FORWARD_SKILL_BUNDLE,)

STATES = (
    "INIT", "ISSUE_CREATED", "ISSUE_UPDATED", "TARGET_BOUND",
    "RUN_PRECHECK_PREPARE", "HANDOFF_PREPARED", "ARTIFACT_READY",
    "HANDOFF_PUBLISHED", "HANDOFF_READY_CONFIRMED", "RUN_PRECHECK_TRIGGER",
    "ASSIGNMENT_TRIGGERED", "RUN_CORRELATED", "TARGET_SELF_CHECKED",
    "COMPLETED",
)
PRE_PUBLISH_STATES = {
    "INIT", "ISSUE_CREATED", "ISSUE_UPDATED", "TARGET_BOUND",
    "RUN_PRECHECK_PREPARE", "HANDOFF_PREPARED", "ARTIFACT_READY",
}
POST_PUBLISH_PRE_TRIGGER_STATES = {
    "HANDOFF_PUBLISHED", "HANDOFF_READY_CONFIRMED", "RUN_PRECHECK_TRIGGER",
}

COMPLETED = "COMPLETED"
INVALID_INPUT = "INVALID_INPUT"
REPLAY_REFUSED = "REPLAY_REFUSED"
ROUTING_REQUIRED = "ROUTING_REQUIRED"
CREATE_UNVERIFIED = "CREATE_UNVERIFIED"
CREATE_RESPONSE_INVALID = "CREATE_RESPONSE_INVALID"
CREATE_NOT_UNASSIGNED = "CREATE_NOT_UNASSIGNED"
PREPARE_FAILED = "PREPARE_FAILED"
PREPARE_BLOCKED = "PREPARE_BLOCKED"
PREPARE_PARTIAL_STOPPED = "PREPARE_PARTIAL_STOPPED"
COMPOSE_REJECTED = "COMPOSE_REJECTED"
PUBLISH_FAILED = "PUBLISH_FAILED"
CONFIRMATION_FAILED = "CONFIRMATION_FAILED"
TRIGGER_COMMAND_FAILED = "TRIGGER_COMMAND_FAILED"
TRIGGER_CONFIRMATION_REQUIRED = "TRIGGER_CONFIRMATION_REQUIRED"
SELF_CHECK_BLOCKED = "SELF_CHECK_BLOCKED"
SELF_REFRESH_EXHAUSTED = "SELF_REFRESH_EXHAUSTED"
PACKAGE_STALE = "PACKAGE_STALE"
OLD_05_PACKAGE_REJECTED = "OLD_05_PACKAGE_REJECTED"
UNEXPECTED_RUN = "UNEXPECTED_RUN"
RUN_STATE_UNDETERMINED = "RUN_STATE_UNDETERMINED"
RUN_CORRELATION_FAILED = "RUN_CORRELATION_FAILED"
U05_DIGEST_DRIFT = "U05_DIGEST_DRIFT"
CROSS_ROUTE_REQUIRED = "CROSS_ROUTE_REQUIRED"
V2_2_REBASE_BLOCKED = "V2_2_REBASE_BLOCKED"
CRASH_SIMULATED = "CRASH_SIMULATED"
UPDATE_UNVERIFIED = "UPDATE_UNVERIFIED"
UPDATE_RESPONSE_INVALID = "UPDATE_RESPONSE_INVALID"
UPDATE_NOT_UNASSIGNED = "UPDATE_NOT_UNASSIGNED"

TERMINAL_STATUSES = (
    COMPLETED, INVALID_INPUT, REPLAY_REFUSED, ROUTING_REQUIRED,
    CREATE_UNVERIFIED, CREATE_RESPONSE_INVALID, CREATE_NOT_UNASSIGNED,
    UPDATE_UNVERIFIED, UPDATE_RESPONSE_INVALID, UPDATE_NOT_UNASSIGNED,
    PREPARE_FAILED, PREPARE_BLOCKED, PREPARE_PARTIAL_STOPPED,
    COMPOSE_REJECTED, PUBLISH_FAILED, CONFIRMATION_FAILED,
    TRIGGER_COMMAND_FAILED, TRIGGER_CONFIRMATION_REQUIRED,
    SELF_CHECK_BLOCKED, SELF_REFRESH_EXHAUSTED, PACKAGE_STALE,
    OLD_05_PACKAGE_REJECTED, UNEXPECTED_RUN, RUN_STATE_UNDETERMINED,
    RUN_CORRELATION_FAILED, U05_DIGEST_DRIFT, CROSS_ROUTE_REQUIRED,
    V2_2_REBASE_BLOCKED, CRASH_SIMULATED,
)

UNCERTAINTY = (
    "exactly-once is evidenced only by this transaction's observable ledger; "
    "platform-side atomicity of issue create/assign is not proven",
    "assignment confirmation is marker-based over the deployed response; an "
    "unrecognized-but-successful response fails closed to "
    "TRIGGER_CONFIRMATION_REQUIRED and requires a read-only operator "
    "reconciliation, never a retry",
    "the target SELF_CHECK runs inside this transaction as a Run-start "
    "boundary simulation; platform-level pre-run guarantees do not exist",
    "run correlation uses the observed issue-runs list-of-objects contract "
    "(id, issue_id, agent_id, status); extra fields are ignored; a missing "
    "required field fails closed",
)

GUARANTEES = {
    "live_mutations": 0,
    "mentions": 0,
    "run_triggers_outside_assignment": 0,
    "canonical_writes": 0,
    "llm_calls_in_orchestrator": 0,
    "frozen_schema_changes": 0,
}


class AssignmentError(Exception):
    """Bounded stop with a stable code (routing/bundle failures)."""

    code = "assignment_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = str(message)[:240]
        self.details = {k: (str(v)[:240] if v is not None else None)
                        for k, v in details.items()}

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class RoutingRequiredError(AssignmentError):
    code = "routing_required"


class T08BundleError(AssignmentError):
    code = "t08_bundle_invalid"


class U05BundleError(AssignmentError):
    code = "u05_bundle_invalid"


class CrashSimulated(Exception):
    """Test-only interrupt: ledger has evidence, no transaction_result."""

    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


class _Stop(Exception):
    """Internal: terminate the state machine with a bounded terminal status."""

    def __init__(self, status: str, reason: str, escalation=None, extra=None):
        super().__init__(reason)
        self.status = status
        self.reason = str(reason)[:240]
        self.escalation = escalation
        self.extra = extra


class _Machine:
    """Ordered state transitions, each appended to the ledger."""

    def __init__(self, ledger: dispatch.TransactionLedger, transaction_id: str):
        self.ledger = ledger
        self.transaction_id = transaction_id
        self.state = "INIT"
        self.transitions = ["INIT"]

    def to(self, state: str) -> None:
        if state not in STATES:
            raise _Stop(INVALID_INPUT, f"unknown state {state!r}")
        self.ledger.append({
            "kind": "state_transition",
            "transaction_id": self.transaction_id,
            "from": self.state,
            "to": state,
        })
        self.state = state
        self.transitions.append(state)

    def require(self, expected: str) -> None:
        if self.state != expected:
            raise _Stop(INVALID_INPUT,
                        f"state machine violation: state {self.state!r} "
                        f"but step requires {expected!r}")


def _slug_of(role_spec: str) -> str:
    """Exact V2.2 staged role lookup; retired names never alias."""
    value = role_spec.strip() if isinstance(role_spec, str) else ""
    if value in RETIRED_ROLES:
        raise RoutingRequiredError(
            f"target role {value!r} is retired with no alias "
            f"({RETIRED_ROLES[value]}); refuse without rewrite",
            role_spec=value, retired=RETIRED_ROLES[value])
    if value in LIVE_DISPLAY_NAME.values() and value not in STAGED_DISPLAY_NAME.values():
        raise RoutingRequiredError(
            f"live old-role display name {value!r} is not a V2.2 staged "
            "identity and is never rewritten",
            role_spec=value)
    if value in ROLE_BY_SLUG:
        return value
    for slug, name in ROLES:
        if value == name:
            return slug
    raise RoutingRequiredError(
        f"target role {role_spec!r} is not in the accepted U05 V2.2 role table",
        role_spec=str(role_spec)[:80])


def _role_names() -> dict:
    return {slug: name for slug, name in ROLES}


def t08_mapping(bundle_dir=None) -> dict:
    """Load the frozen T08 baseline identity mapping: role slug -> agent.

    Read-only bundle structural preconditions; drift fails closed. This
    resolves identity ONLY — it never applies instructions, bindings or any
    T13-owned activation.
    """
    bundle = Path(bundle_dir) if bundle_dir is not None \
        else ROOT / BUNDLE_SUBPATH
    try:
        baseline = json.loads((bundle / "baseline.json").read_text(encoding="utf-8"))
        bundle_doc = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        binding_plan = json.loads(
            (bundle / "binding-plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise T08BundleError(
            f"T08 bundle unreadable/malformed: {exc}", bundle=str(bundle)[:160]) from None
    if not isinstance(baseline, dict) or \
            not str(baseline.get("schema_version", "")).startswith("T08-baseline/"):
        raise T08BundleError("baseline.json is not a T08-baseline document",
                             bundle=str(bundle)[:160])
    if not isinstance(bundle_doc, dict) or \
            not str(bundle_doc.get("schema_version", "")).startswith("T08-bundle/"):
        raise T08BundleError("bundle.json is not a T08-bundle document",
                             bundle=str(bundle)[:160])
    agents = baseline.get("agents")
    if not isinstance(agents, list) or not agents:
        raise T08BundleError("T08 baseline carries no agents",
                             bundle=str(bundle)[:160])
    mapping: dict = {}
    seen_ids: set = set()
    for entry in agents:
        if not isinstance(entry, dict):
            raise T08BundleError("T08 baseline agent entry is not an object")
        role, agent_id, name = (entry.get("role"), entry.get("agent_id"),
                                entry.get("agent_name"))
        if not all(isinstance(x, str) and x.strip() for x in (role, agent_id, name)):
            raise T08BundleError("T08 baseline agent entry has blank identity fields",
                                 role=str(role)[:80])
        if role in mapping or agent_id in seen_ids:
            raise T08BundleError(
                "T08 baseline maps a role or agent id more than once; "
                "fail closed", role=str(role)[:80])
        if not dispatch.UUID_RE.match(agent_id):
            raise T08BundleError("T08 baseline agent_id is not a UUID",
                                 role=str(role)[:80])
        expected = {_role_names().get(role), LIVE_DISPLAY_NAME.get(role)}
        expected.discard(None)
        if name not in expected:
            raise T08BundleError(
                "T08 baseline agent name does not match the frozen role table "
                "or the live old-role baseline display name",
                role=str(role)[:80], agent_name=str(name)[:80])
        mapping[role] = {"agent_id": agent_id, "agent_name": name}
        seen_ids.add(agent_id)
    bindings = binding_plan.get("bindings")
    if not isinstance(bindings, list):
        raise T08BundleError("T08 binding plan carries no bindings list")
    bound = {b.get("agent_role") for b in bindings
             if isinstance(b, dict) and isinstance(b.get("agent_role"), str)
             and b.get("agent_role").strip()}
    if not bound:
        raise T08BundleError("T08 binding plan binds no agent roles")
    return {"agents": mapping, "bound_roles": bound, "bundle": str(bundle)}


def resolve_target(role_spec: str, *, bundle_dir=None) -> dict:
    """T10 mention-path resolver. 02 is never a mention-dispatch target.

    Assignment uses `resolve_assignment_target` (all six V2.2 roles).
    """
    slug = _slug_of(role_spec)
    if slug == "context-engineer":
        raise RoutingRequiredError(
            "context-engineer is the T08 exception-path role and is never a "
            "dispatch target; route through Engineering Lead/Squad",
            role=slug)
    mapping = t08_mapping(bundle_dir)
    if slug not in mapping["bound_roles"]:
        raise RoutingRequiredError(
            f"target role {slug!r} is not bound in the T08 binding plan",
            role=slug)
    entry = mapping["agents"].get(slug)
    if entry is None:
        raise RoutingRequiredError(
            f"target role {slug!r} is bound but missing from the T08 "
            "baseline; T08 preconditions drifted — fail closed", role=slug)
    return {
        "role": slug,
        "role_name": ROLE_BY_SLUG[slug],
        "agent_id": entry["agent_id"],
        "agent_name": entry["agent_name"],
        "resolution_source": "T08-baseline:" + mapping["bundle"],
    }


def u05_mapping(bundle_dir=None, *, require_pins: bool | None = None) -> dict:
    """Load the accepted U05 staged role map. Pins required for the default bundle."""
    bundle = Path(bundle_dir) if bundle_dir is not None \
        else ROOT / BUNDLE_SUBPATH
    if require_pins is None:
        require_pins = bundle_dir is None
    try:
        mapping_doc = json.loads(
            (bundle / "role-mapping.json").read_text(encoding="utf-8"))
        binding_plan = json.loads(
            (bundle / "binding-plan.json").read_text(encoding="utf-8"))
        bundle_doc = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
        invalidation = json.loads(
            (bundle / "package-invalidation.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise U05BundleError(
            f"U05 bundle unreadable/malformed: {exc}", bundle=str(bundle)[:160]) from None
    if not isinstance(mapping_doc, dict) or \
            not str(mapping_doc.get("schema_version", "")).startswith("U05-role-mapping/"):
        raise U05BundleError("role-mapping.json is not a U05-role-mapping document",
                             bundle=str(bundle)[:160])
    if mapping_doc.get("feature_reviewer_resolves_to") is not None:
        raise U05BundleError(
            "U05 role map must leave feature-reviewer unresolved",
            bundle=str(bundle)[:160])
    agents_list = mapping_doc.get("agents")
    if not isinstance(agents_list, list) or not agents_list:
        raise U05BundleError("U05 role map carries no agents")
    mapping: dict = {}
    seen_ids: set = set()
    for entry in agents_list:
        if not isinstance(entry, dict):
            raise U05BundleError("U05 role-map agent entry is not an object")
        role = entry.get("logical_role")
        agent_id = entry.get("agent_id")
        staged = entry.get("staged_display_name")
        if role not in V22_ROLES:
            raise U05BundleError("U05 role-map carries a non-V2.2 role",
                                 role=str(role)[:80])
        if not isinstance(agent_id, str) or not dispatch.UUID_RE.match(agent_id):
            raise U05BundleError("U05 role-map agent_id is not a UUID",
                                 role=str(role)[:80])
        if role in mapping or agent_id in seen_ids:
            raise U05BundleError("U05 role-map duplicates a role or agent id",
                                 role=str(role)[:80])
        if staged != STAGED_DISPLAY_NAME.get(role):
            raise U05BundleError(
                "U05 staged display name drifted from the V2.2 role table",
                role=str(role)[:80], staged=str(staged)[:80])
        if entry.get("alias_from_feature_reviewer") is True:
            raise U05BundleError(
                "U05 role-map must not alias feature-reviewer onto a live role",
                role=str(role)[:80])
        mapping[role] = {
            "agent_id": agent_id,
            "agent_name": staged,
            "live_display_name": entry.get("live_display_name"),
        }
        seen_ids.add(agent_id)
    if set(mapping) != set(V22_ROLES):
        raise U05BundleError("U05 role map does not cover all six V2.2 roles")
    for role, expected_id in EXPECTED_AGENT_IDS.items():
        if mapping[role]["agent_id"] != expected_id:
            raise U05BundleError("U05 agent UUID drifted from the pinned roster",
                                 role=role)
    bundle_rev = bundle_doc.get("instruction_bundle_revision")
    plan_rev = binding_plan.get("binding_plan_revision")
    if require_pins:
        if bundle_rev != PINNED_INSTRUCTION_BUNDLE:
            raise U05BundleError(
                "U05 instruction_bundle_revision drifted from the accepted pin",
                expected=PINNED_INSTRUCTION_BUNDLE, found=str(bundle_rev)[:80])
        if plan_rev != PINNED_BINDING_PLAN:
            raise U05BundleError(
                "U05 binding_plan_revision drifted from the accepted pin",
                expected=PINNED_BINDING_PLAN, found=str(plan_rev)[:80])
        if bundle_doc.get("artifact_contract_revision") != PINNED_ARTIFACT_CONTRACT:
            raise U05BundleError(
                "Artifact Contract revision drifted from the accepted U10 pin",
                expected=PINNED_ARTIFACT_CONTRACT)
        skill = digest_skill_dir(ROOT / "skills" / "multica-context-handoff")
        if skill["skill_md_digest"] != PINNED_SKILL_MD:
            raise U05BundleError(
                "U04 SKILL.md digest drifted from the accepted pin",
                expected=PINNED_SKILL_MD, found=skill["skill_md_digest"])
        if skill["bundle_digest"] not in ACCEPTED_SKILL_BUNDLES:
            raise U05BundleError(
                "U04 skill bundle digest drifted from the accepted pin",
                expected=ACCEPTED_SKILL_BUNDLES[0], found=skill["bundle_digest"])
    if invalidation.get("old_05_package_accepted") is True \
            or invalidation.get("feature_reviewer_activation") not in (0, None):
        raise U05BundleError("U05 package-invalidation accepts old 05")
    return {
        "agents": mapping,
        "bound_roles": set(V22_ROLES),
        "bundle": str(bundle),
        "instruction_bundle_revision": bundle_rev,
        "binding_plan_revision": plan_rev,
        "feature_reviewer_resolves_to": None,
        "old_05_package_accepted": False,
    }


def resolve_assignment_target(role_spec: str, *, bundle_dir=None) -> dict:
    """Assignment-route resolver: all six V2.2 roles, no retired alias."""
    slug = _slug_of(role_spec)
    mapping = u05_mapping(bundle_dir)
    entry = mapping["agents"].get(slug)
    if entry is None:
        raise RoutingRequiredError(
            f"target role {slug!r} is missing from the U05 staged role map",
            role=slug)
    return {
        "role": slug,
        "role_name": STAGED_DISPLAY_NAME[slug],
        "agent_id": entry["agent_id"],
        "agent_name": entry["agent_name"],
        "resolution_source": "U05-role-mapping:" + mapping["bundle"],
        "instruction_bundle_revision": mapping["instruction_bundle_revision"],
        "binding_plan_revision": mapping["binding_plan_revision"],
    }


def _validate_spec(spec, caller_role, transaction_id, compose_fn) -> dict:
    """Zero-command input validation. Bounded INVALID_INPUT refusals."""
    try:
        return _validate_spec_fields(spec, caller_role, transaction_id, compose_fn)
    except dispatch.DispatchError as exc:
        raise _Stop(INVALID_INPUT, exc.message,
                    extra={"error": exc.envelope()}) from None


def _validate_spec_fields(spec, caller_role, transaction_id, compose_fn) -> dict:
    if not isinstance(spec, dict):
        raise _Stop(INVALID_INPUT, "spec must be a dict")
    title = dispatch._require_text(spec.get("title"), "spec.title")
    description = spec.get("description")
    if not isinstance(description, str) or not description.strip():
        raise _Stop(INVALID_INPUT, "spec.description must be a non-blank string")
    if len(description) > 100000:
        raise _Stop(INVALID_INPUT, "spec.description exceeds 100000 chars")
    if dispatch.MENTION_RE.search(title) or dispatch.MENTION_RE.search(description):
        raise _Stop(INVALID_INPUT,
                    "spec carries a mention link; the assignment path contains "
                    "no mention of any kind")
    project_id = dispatch._require_text(spec.get("project_id"), "spec.project_id", 64)
    parent = spec.get("parent_issue_id")
    if parent is not None:
        parent = dispatch._require_bare_id(parent, "spec.parent_issue_id")
    purpose = spec.get("purpose")
    purpose = dispatch._require_text(purpose if purpose is not None
                                     else "implementation", "spec.purpose", 120)
    priority = spec.get("priority")
    if priority is not None:
        priority = dispatch._require_text(priority, "spec.priority", 40)
    options = spec.get("options")
    if options is not None and not isinstance(options, dict):
        raise _Stop(INVALID_INPUT, "spec.options must be a dict or None")
    markers = spec.get("decision_markers")
    if markers is not None and not (isinstance(markers, list)
                                    and all(isinstance(m, str) for m in markers)):
        raise _Stop(INVALID_INPUT, "spec.decision_markers must be a list of strings")
    caller_slug = _validate_role_arg(caller_role, "caller_role")
    if not isinstance(transaction_id, str) or not transaction_id.strip() \
            or len(transaction_id) > 80 or any(c.isspace() for c in transaction_id):
        raise _Stop(INVALID_INPUT,
                    "transaction_id must be a non-blank id without whitespace "
                    "(<= 80 chars)")
    if compose_fn is None:
        raise _Stop(INVALID_INPUT,
                    "compose_fn is required: the caller semantic compose step "
                    "must be injected (the orchestrator never calls a model)")
    if not callable(compose_fn):
        raise _Stop(INVALID_INPUT, "compose_fn must be callable")
    existing = spec.get("existing_issue_id")
    if existing is not None:
        existing = dispatch._require_bare_id(existing, "spec.existing_issue_id")
    artifacts = spec.get("required_artifacts")
    if artifacts is None:
        artifacts = []
    if not isinstance(artifacts, list) or not all(isinstance(a, dict) for a in artifacts):
        raise _Stop(INVALID_INPUT, "spec.required_artifacts must be a list of objects")
    review_level = spec.get("review_level")
    if review_level is not None:
        review_level = dispatch._require_text(review_level, "spec.review_level", 8)
    store_file = spec.get("artifact_store_file")
    if store_file is not None and not isinstance(store_file, str):
        raise _Stop(INVALID_INPUT, "spec.artifact_store_file must be a string or None")
    return {
        "title": title, "description": description, "project_id": project_id,
        "parent_issue_id": parent, "purpose": purpose, "priority": priority,
        "options": options, "decision_markers": markers,
        "caller_role": caller_slug,
        "existing_issue_id": existing,
        "required_artifacts": artifacts,
        "review_level": review_level,
        "artifact_store_file": store_file,
    }


def _validate_role_arg(value, field: str) -> str:
    try:
        return _slug_of(value)
    except RoutingRequiredError:
        raise _Stop(INVALID_INPUT,
                    f"{field} {value!r} is not in the accepted U05 V2.2 role table") from None


def _recorded_result(transaction_id: str, ledger) -> dict | None:
    found = None
    for record in ledger.records:
        if record.get("kind") == "transaction_result" \
                and record.get("transaction_id") == transaction_id:
            found = record.get("result")
    return found if isinstance(found, dict) else None


def _tx_records(ledger, transaction_id: str) -> list:
    return [r for r in ledger.records
            if r.get("transaction_id") == transaction_id]


def _escalation(route: str, reason: str, **extra) -> dict:
    out = {"required": True, "route_to": route, "reason": reason}
    out.update(extra)
    return out


def _bounded_reason(exc: Exception) -> dict:
    if hasattr(exc, "envelope"):
        try:
            return exc.envelope()
        except Exception:
            pass
    return {"code": type(exc).__name__, "message": str(exc)[:240]}


class AssignmentHandoff:
    """One assignment-handoff transaction: ordered, ledgered, fail-closed."""

    def __init__(self, *, validated: dict, target_role_spec: str,
                 recorder, ledger, compose_fn: Callable, clock: Callable,
                 bundle_dir=None, finding_store=None, world: dict | None,
                 policy: dict | None, workdir: Path, executable: str,
                 crash_at: str | None = None, resume: dict | None = None,
                 findings_source=None, legacy_fixture: bool | None = None,
                 effects_runner=None):
        self.spec = validated
        self.target_role_spec = target_role_spec
        self.recorder = recorder
        self.ledger = ledger
        self.compose_fn = compose_fn
        self.clock = clock
        self.bundle_dir = bundle_dir
        self.finding_store = finding_store
        self.world = world or {}
        self.policy = policy or {}
        self.executable = executable
        self.crash_at = crash_at
        self.resume = resume or {}
        self.findings_source = findings_source
        self.effects_runner = effects_runner if effects_runner is not None \
            else getattr(recorder, "inner", recorder)
        # Production never infers simulation from class names, fake
        # attributes, or inner wrappers. Tests pass legacy_fixture=True
        # only with construct_simulation_entry wrapping an inert spy.
        self.legacy_fixture = bool(legacy_fixture)
        self._prepare_observation = None
        self._self_check_observation = None
        self._worker_entry_observation = None

        self.dispatch_cli = dispatch.DispatchCli(
            executable, runner=recorder, workdir=workdir)
        self.adapter_cli = adapter.MulticaCli(executable, runner=recorder)
        self.note_cli = note.NoteCli(executable, runner=recorder)

        self.machine = _Machine(ledger, recorder.transaction_id)
        self.canonical: dict | None = None
        self.task_ref: str | None = None
        self.target: dict | None = None
        self.caller_agent_id: str | None = None
        self.envelope: dict | None = None
        self.published_comment_id: str | None = None
        self.confirmed: dict | None = None
        self.trigger_argv: list | None = None
        self.trigger_confirmed = False
        self.self_check_evidence: dict | None = None
        self._last_request: dict | None = None
        self.artifact_binding: dict | None = None
        self.known_run_ids: set = set()
        self.intended_run: dict | None = None
        self.u05_pins: dict | None = None

    def _maybe_crash(self) -> None:
        if self.crash_at and self.machine.state == self.crash_at:
            raise CrashSimulated(self.crash_at)

    def _require_findings_binding(self, what: str) -> None:
        try:
            gate_effectful_findings(
                source=self.findings_source, runner=self.effects_runner,
                require_source=not self.legacy_fixture,
                allow_legacy_fixture=self.legacy_fixture, what=what)
        except FindingsSourceRefusal as exc:
            raise _Stop(
                INVALID_INPUT,
                f"{exc.message} [{exc.code}]",
                extra={"findings_source_refusal": exc.as_dict()}) from None

    def _verify_worker_entry(self, what: str) -> None:
        if self.findings_source is None:
            if self.legacy_fixture:
                return
            raise _Stop(
                INVALID_INPUT,
                "worker entry requires a verified Findings source binding")
        try:
            result = verify_worker_entry(
                self.findings_source,
                task_ref=self.task_ref,
                role=(self.target or {}).get("role"),
                prior_observation=self._self_check_observation
                or self._prepare_observation,
                observer_run_id=self.recorder.transaction_id,
                allow_simulation=self.legacy_fixture)
        except FindingsSourceRefusal as exc:
            raise _Stop(
                PACKAGE_STALE if exc.code == "findings_source_changed"
                else INVALID_INPUT,
                f"{what} refused at worker entry [{exc.code}]: {exc.message}",
                extra={"findings_source_refusal": exc.as_dict()}) from None
        self._worker_entry_observation = result["observation"]

    def run(self) -> dict:
        try:
            self._require_findings_binding("assignment publication or trigger")
            self._record_start()
            if self.resume.get("canonical"):
                self._restore_canonical(self.resume["canonical"])
            else:
                self._step_create_or_update()
            self._maybe_crash()
            self._step_resolve_and_bind()
            self._maybe_crash()
            self._step_run_precheck("prepare")
            self._maybe_crash()
            envelope = self._prepare_pipeline()
            self._step_finalize(envelope)
            self._maybe_crash()
            self._step_artifact_ready()
            self._maybe_crash()
            if self.resume.get("skip_publish"):
                self._reuse_published_note()
            else:
                self._step_publish()
            self._maybe_crash()
            self._step_confirm()
            self._maybe_crash()
            self._step_freshness_before_trigger()
            self._step_run_precheck("trigger")
            self._maybe_crash()
            self._verify_worker_entry("assignment trigger")
            self._step_trigger()
            self._maybe_crash()
            self._step_correlate_run()
            self._maybe_crash()
            self._step_self_check()
        except CrashSimulated:
            raise
        except _Stop as stop:
            return self._finish(stop.status, stop.reason,
                                escalation=stop.escalation, extra=stop.extra)
        except dispatch.DispatchError as exc:
            return self._finish(_dispatch_terminal(exc), exc.message,
                                escalation={"required": True,
                                            "reason": exc.code},
                                extra={"error": exc.envelope()})
        except AssignmentError as exc:
            status = U05_DIGEST_DRIFT if isinstance(exc, U05BundleError) \
                and "drift" in exc.message.lower() else ROUTING_REQUIRED
            return self._finish(status, exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad", exc.code,
                                    **exc.details),
                                extra={"error": exc.envelope()})
        return self._finish(COMPLETED, None)

    # -- steps ------------------------------------------------------------

    def _record_start(self) -> None:
        spec_digest = "sha256:" + hashlib.sha256(
            chandoff.canonical_json({
                "title": self.spec["title"],
                "description": self.spec["description"],
                "project_id": self.spec["project_id"],
                "existing_issue_id": self.spec.get("existing_issue_id"),
                "required_artifacts": self.spec.get("required_artifacts") or [],
            }).encode("utf-8")).hexdigest()
        self.ledger.append({
            "kind": "recovery_context",
            "transaction_id": self.recorder.transaction_id,
            "caller_role": self.spec["caller_role"],
            "target_role_spec": self.target_role_spec,
            "spec_digest": spec_digest,
            "existing_issue_id": self.spec.get("existing_issue_id"),
        })

    def _restore_canonical(self, canonical: dict) -> None:
        self.machine.require("INIT")
        self.canonical = {"id": canonical["id"], "identifier": canonical["identifier"]}
        self.task_ref = adapter.task_ref_of(canonical["identifier"])
        self.ledger.append({
            "kind": "canonical_issue",
            "transaction_id": self.recorder.transaction_id,
            "id": canonical["id"],
            "identifier": canonical["identifier"],
            "restored": True,
        })
        self.machine.to("ISSUE_CREATED")

    def _step_create_or_update(self) -> None:
        self.machine.require("INIT")
        existing = self.spec.get("existing_issue_id")
        if existing:
            self._step_update(existing)
            return
        try:
            data = self.dispatch_cli.create_issue(
                title=self.spec["title"],
                description=self.spec["description"],
                parent_issue_id=self.spec["parent_issue_id"],
                project_id=self.spec["project_id"],
                priority=self.spec.get("priority"),
            )
        except dispatch.ForbiddenCreateArgvError as exc:
            raise _Stop(INVALID_INPUT, exc.message,
                        extra={"error": exc.envelope()}) from None
        except dispatch.CreateCommandFailedError as exc:
            raise _Stop(CREATE_UNVERIFIED,
                        "create command failed; whether the platform created "
                        "an issue is unverified — operator reconciliation, "
                        "never a blind retry",
                        extra={"error": exc.envelope()}) from None
        except dispatch.CreateResponseInvalidError as exc:
            raise _Stop(CREATE_RESPONSE_INVALID, exc.message,
                        extra={"error": exc.envelope()}) from None
        except dispatch.CreateNotUnassignedError as exc:
            raise _Stop(CREATE_NOT_UNASSIGNED, exc.message,
                        extra={"error": exc.envelope()}) from None
        self._set_canonical(data, "ISSUE_CREATED")

    def _step_update(self, existing: str) -> None:
        try:
            data = self.dispatch_cli.update_issue(
                existing,
                title=self.spec["title"],
                description=self.spec["description"],
                parent_issue_id=self.spec["parent_issue_id"],
                project_id=self.spec["project_id"],
                priority=self.spec.get("priority"),
            )
        except dispatch.UpdateArgvInvalidError as exc:
            raise _Stop(INVALID_INPUT, exc.message,
                        extra={"error": exc.envelope()}) from None
        except dispatch.UpdateCommandFailedError as exc:
            raise _Stop(UPDATE_UNVERIFIED,
                        "update command failed; whether the platform applied "
                        "the mutation is unverified — never a blind retry",
                        extra={"error": exc.envelope()}) from None
        except dispatch.UpdateResponseInvalidError as exc:
            raise _Stop(UPDATE_RESPONSE_INVALID, exc.message,
                        extra={"error": exc.envelope()}) from None
        except dispatch.UpdateNotUnassignedError as exc:
            raise _Stop(UPDATE_NOT_UNASSIGNED, exc.message,
                        extra={"error": exc.envelope()}) from None
        self._set_canonical(data, "ISSUE_UPDATED")

    def _set_canonical(self, data: dict, state: str) -> None:
        self.canonical = {"id": data["id"], "identifier": data["identifier"]}
        self.task_ref = adapter.task_ref_of(data["identifier"])
        self.ledger.append({
            "kind": "canonical_issue",
            "transaction_id": self.recorder.transaction_id,
            "id": data["id"],
            "identifier": data["identifier"],
        })
        self.machine.to(state)

    def _step_resolve_and_bind(self) -> None:
        if self.machine.state not in ("ISSUE_CREATED", "ISSUE_UPDATED"):
            raise _Stop(INVALID_INPUT,
                        f"bind reached from state {self.machine.state!r}")
        try:
            self.target = resolve_assignment_target(
                self.target_role_spec, bundle_dir=self.bundle_dir)
            mapping = u05_mapping(self.bundle_dir)
        except (RoutingRequiredError, U05BundleError) as exc:
            status = U05_DIGEST_DRIFT if isinstance(exc, U05BundleError) \
                and "drift" in exc.message.lower() else ROUTING_REQUIRED
            raise _Stop(status, exc.message,
                        escalation=_escalation("engineering-lead-or-squad",
                                               exc.code, **exc.details),
                        extra={"error": exc.envelope()}) from None
        caller_entry = mapping["agents"].get(self.spec["caller_role"])
        if caller_entry is None:
            raise _Stop(ROUTING_REQUIRED,
                        f"caller role {self.spec['caller_role']!r} has no "
                        "U05 staged agent",
                        escalation=_escalation("engineering-lead-or-squad",
                                               "caller_agent_unresolved"),
                        extra={"caller_role": self.spec["caller_role"]})
        self.caller_agent_id = caller_entry["agent_id"]
        self.u05_pins = {
            "instruction_bundle_revision": mapping["instruction_bundle_revision"],
            "binding_plan_revision": mapping["binding_plan_revision"],
            "artifact_contract_revision": PINNED_ARTIFACT_CONTRACT,
            "skill_md_digest": PINNED_SKILL_MD,
        }
        self._bind_artifacts()
        self.ledger.append({
            "kind": "target_binding",
            "transaction_id": self.recorder.transaction_id,
            "role": self.target["role"],
            "agent_id": self.target["agent_id"],
            "artifact_digest": (self.artifact_binding or {}).get("digest"),
        })
        self.machine.to("TARGET_BOUND")

    def _bind_artifacts(self) -> None:
        requirements = list(self.spec.get("required_artifacts") or [])
        digest = cartifact.dependency_digest(requirements) if requirements \
            else cartifact.dependency_digest([])
        self.artifact_binding = {
            "requirements": requirements,
            "digest": digest,
            "review_level": self.spec.get("review_level"),
            "store_file": self.spec.get("artifact_store_file")
            or self.world.get("artifact_store_file"),
        }
        if not requirements:
            self.artifact_binding["status"] = "ARTIFACT_READY"
            self.artifact_binding["empty"] = True
            return
        store_file = self.artifact_binding["store_file"]
        if not store_file:
            raise _Stop(PACKAGE_STALE,
                        "required artifacts declared but no artifact store "
                        "was bound; fail closed as package_stale",
                        extra={"reason": "artifact_store_required"})
        try:
            store = cartifact._store_from_file(store_file)
            ready = cartifact.artifact_ready_check(
                store,
                {
                    "schema_version": "1.0",
                    "kind": "artifact_ready_check_request",
                    "target_role": self.target["role"],
                    "requirements": requirements,
                    **({"review_level": self.spec["review_level"]}
                       if self.spec.get("review_level") else {}),
                })
        except Exception as exc:
            raise _Stop(PACKAGE_STALE, "artifact_ready_check failed closed",
                        extra={"error": _bounded_reason(exc)}) from None
        self.artifact_binding["status"] = ready.get("status")
        self.artifact_binding["failures"] = ready.get("failures") or []
        self.artifact_binding["dependency_digest"] = ready.get("dependency_digest") or digest
        if ready.get("status") != "ARTIFACT_READY" or ready.get("blocks_handoff"):
            raise _Stop(PACKAGE_STALE,
                        "artifact_ready_check is not ARTIFACT_READY; "
                        "package_stale / REFRESH_REQUIRED before trigger",
                        extra={"artifact_ready": {
                            "status": ready.get("status"),
                            "failures": (ready.get("failures") or [])[:8],
                        }})

    def _step_run_precheck(self, phase: str) -> None:
        expected = "TARGET_BOUND" if phase == "prepare" \
            else "HANDOFF_READY_CONFIRMED"
        if phase == "trigger":
            expected = "HANDOFF_READY_CONFIRMED"
        self.machine.require(expected)
        try:
            listing = self.dispatch_cli.list_runs(
                self.canonical["id"], active=True, siblings=True)
        except dispatch.RunStateUndeterminedError as exc:
            raise _Stop(RUN_STATE_UNDETERMINED,
                        "unexpected-run state cannot be determined; "
                        "zero new trigger",
                        extra={"error": exc.envelope()}) from None
        except dispatch.RunsResponseInvalidError as exc:
            raise _Stop(RUN_STATE_UNDETERMINED,
                        "issue runs response is not the documented list "
                        "contract; fail closed",
                        extra={"error": exc.envelope()}) from None
        unexpected = dispatch.unexpected_active_runs(
            listing["runs"], issue_id=self.canonical["id"],
            ignore_ids=self.known_run_ids)
        try:
            history = self.dispatch_cli.list_runs(
                self.canonical["id"], active=False, siblings=False)
        except (dispatch.RunStateUndeterminedError,
                dispatch.RunsResponseInvalidError) as exc:
            raise _Stop(RUN_STATE_UNDETERMINED,
                        "issue run history cannot be determined; "
                        "zero new trigger",
                        extra={"error": exc.envelope()}) from None
        self.ledger.append({
            "kind": "run_precheck",
            "transaction_id": self.recorder.transaction_id,
            "phase": phase,
            "active_count": len(listing["runs"]),
            "unexpected_count": len(unexpected),
        })
        if unexpected:
            raise _Stop(UNEXPECTED_RUN,
                        "unexpected active target/sibling run observed; "
                        "zero new trigger",
                        extra={"unexpected_runs": [
                            {"id": r["id"], "issue_id": r["issue_id"],
                             "agent_id": r["agent_id"], "status": r["status"]}
                            for r in unexpected[:8]]})
        self.known_run_ids.update(r["id"] for r in listing["runs"])
        self.known_run_ids.update(r["id"] for r in history["runs"])
        self.machine.to("RUN_PRECHECK_PREPARE" if phase == "prepare"
                        else "RUN_PRECHECK_TRIGGER")

    def _prepare_pipeline(self) -> dict:
        if self.machine.state not in (
                "RUN_PRECHECK_PREPARE", "ASSIGNMENT_TRIGGERED", "RUN_CORRELATED"):
            raise _Stop(INVALID_INPUT,
                        f"prepare pipeline reached from state "
                        f"{self.machine.state!r}")
        try:
            snap = adapter.build_snapshot_request(
                issue_id=self.canonical["id"],
                target_role=self.target["role"],
                caller_role=self.spec["caller_role"],
                purpose=self.spec["purpose"],
                cli=self.adapter_cli,
                explicit_project_id=self.spec["project_id"],
                decision_markers=self.spec["decision_markers"],
                options=self.spec["options"],
            )
        except Exception as exc:
            raise _Stop(PREPARE_FAILED, "T05 snapshot failed",
                        extra={"error": _bounded_reason(exc)}) from None
        request = snap["request"]
        self._last_request = request
        plan_result = self._plan(request)
        if plan_result.get("status") != "PLAN_READY":
            if plan_result.get("status") == "BLOCKED":
                raise _Stop(PREPARE_BLOCKED, "T01 PLAN blocked",
                            escalation=plan_result.get("escalation")
                            or {"required": True, "reason": "plan_blocked"},
                            extra={"finding_gate": _gate_summary(plan_result)})
            raise _Stop(PREPARE_FAILED,
                        f"T01 returned {plan_result.get('status')!r}",
                        extra={"escalation": plan_result.get("escalation")})
        return self._compose_and_finalize(plan_result["plan"], request)

    def _plan(self, request: dict) -> dict:
        if self.findings_source is not None:
            if self.world.get("findings") is not None or \
                    self.finding_store is not None:
                raise _Stop(
                    INVALID_INPUT,
                    "a verified Findings source binding cannot be combined "
                    "with world.findings or a legacy finding_store; inputs "
                    "are mutually exclusive")
            try:
                result = plan.prepare_handoff_plan(
                    request,
                    findings_source=self.findings_source,
                    source_boundary="PREPARE",
                    source_observer_run_id=self.recorder.transaction_id,
                    docs=self.world.get("docs"),
                    registry=self.world.get("registry"),
                    checkpoints=self.world.get("checkpoints"),
                )
            except FindingsSourceRefusal as exc:
                raise _Stop(
                    PREPARE_BLOCKED,
                    f"the verified Findings source refused the PREPARE read "
                    f"[{exc.code}]: {exc.message}",
                    escalation=_escalation("engineering-lead-or-squad",
                                           "findings_source_unbound",
                                           code=exc.code)) from None
            except Exception as exc:
                raise _Stop(PREPARE_FAILED, "T01 PLAN failed",
                            extra={"error": _bounded_reason(exc)}) from None
            self._prepare_observation = result.get("findings_observation")
            return result
        if not self.legacy_fixture:
            raise _Stop(
                INVALID_INPUT,
                "production assignment requires a verified Findings source "
                "binding; world.findings and finding_store are not "
                "production authority")
        try:
            return plan.prepare_handoff_plan(
                request,
                findings=self.world.get("findings"),
                store=self.finding_store,
                docs=self.world.get("docs"),
                registry=self.world.get("registry"),
                checkpoints=self.world.get("checkpoints"),
            )
        except Exception as exc:
            raise _Stop(PREPARE_FAILED, "T01 PLAN failed",
                        extra={"error": _bounded_reason(exc)}) from None

    def _compose_once(self, plan_obj: dict, request: dict, errors) -> dict:
        try:
            proposed = self.compose_fn(plan_obj, request, errors=errors)
            return compose.compose_semantic(plan_obj, proposed)
        except Exception as exc:
            raise _Stop(PREPARE_FAILED, "T02 compose failed",
                        extra={"error": _bounded_reason(exc)}) from None

    def _compose_and_finalize(self, plan_obj: dict, request: dict) -> dict:
        validation = self._compose_once(plan_obj, request, None)
        if validation.get("status") != "ACCEPTED":
            validation = self._compose_once(
                plan_obj, request, validation.get("errors") or [])
            if validation.get("status") != "ACCEPTED":
                raise _Stop(COMPOSE_REJECTED,
                            "T02 rejected the compose result; the one bounded "
                            "same-PLAN repair is spent",
                            extra={"errors": (validation.get("errors") or [])[:8]})
        try:
            envelope = finalize.finalize_handoff(
                plan_obj, validation, request,
                docs=self.world.get("docs"),
                registry=self.world.get("registry"),
                evidence=self.world.get("evidence"),
                clock=self.clock,
            )
        except Exception as exc:
            raise _Stop(PREPARE_FAILED, "T03 finalize failed",
                        extra={"error": _bounded_reason(exc)}) from None
        status = envelope.get("status")
        if status == "BLOCKED":
            raise _Stop(PREPARE_BLOCKED, "T03 finalize blocked",
                        escalation=envelope.get("escalation")
                        or {"required": True, "reason": "finalize_blocked"})
        if status not in ("READY", "PARTIAL"):
            raise _Stop(PREPARE_FAILED,
                        f"T03 returned unexpected status {status!r}")
        return envelope

    def _step_finalize(self, envelope: dict) -> None:
        self.machine.require("RUN_PRECHECK_PREPARE")
        role = (envelope.get("role")
                or (envelope.get("package") or {}).get("request", {}).get("role"))
        if role == "feature-reviewer" or self.target["role"] == "feature-reviewer":
            raise _Stop(OLD_05_PACKAGE_REJECTED,
                        "old feature-reviewer package cannot READY, assign, "
                        "or rewrite onto delivery-reviewer",
                        extra={"role": role})
        if envelope["status"] == "PARTIAL":
            if not self.policy.get("allow_partial_publication"):
                raise _Stop(PREPARE_PARTIAL_STOPPED,
                            "PARTIAL is never normal-ready; no explicit "
                            "caller policy decision authorized publication",
                            escalation={"required": True,
                                        "route_to": "engineering-lead-or-squad",
                                        "reason": "partial_requires_policy"},
                            extra={"gaps": _gaps(envelope)})
            self.ledger.append({
                "kind": "policy_decision",
                "transaction_id": self.recorder.transaction_id,
                "decision": "publish_partial",
                "authorized_by": self.policy.get("authorized_by"),
                "gaps": _gaps(envelope),
            })
        self.envelope = envelope
        self.machine.to("HANDOFF_PREPARED")

    def _step_artifact_ready(self) -> None:
        self.machine.require("HANDOFF_PREPARED")
        binding = self.artifact_binding or {}
        requirements = binding.get("requirements") or []
        if not requirements:
            self.ledger.append({
                "kind": "artifact_gate",
                "transaction_id": self.recorder.transaction_id,
                "status": "ARTIFACT_READY",
                "digest": binding.get("digest"),
                "empty": True,
            })
            self.machine.to("ARTIFACT_READY")
            return
        store_file = binding.get("store_file")
        try:
            gate = _load_artifact_gate()
            store = cartifact._store_from_file(store_file)
            view = gate.apply_finalize_gate(
                cartifact, self.envelope, store=store,
                requirements=requirements,
                target_role=self.target["role"],
                review_level=binding.get("review_level"))
        except Exception as exc:
            raise _Stop(PACKAGE_STALE, "U04 artifact finalize gate failed",
                        extra={"error": _bounded_reason(exc)}) from None
        self.ledger.append({
            "kind": "artifact_gate",
            "transaction_id": self.recorder.transaction_id,
            "status": view.get("status"),
            "digest": view.get("exported_digest") or binding.get("digest"),
            "merged": bool(view.get("merged")),
        })
        if view.get("status") != "ARTIFACT_READY" or view.get("blocks_handoff"):
            raise _Stop(PACKAGE_STALE,
                        "ARTIFACT_READY check failed after PREPARE_HANDOFF; "
                        "no publish, no trigger",
                        extra={"artifact_ready": {
                            "status": view.get("status"),
                            "failures": (view.get("failures") or [])[:8],
                        }})
        self.machine.to("ARTIFACT_READY")

    def _reuse_published_note(self) -> None:
        self.machine.require("ARTIFACT_READY")
        comment_id = self.resume.get("published_comment_id")
        package_id = self.resume.get("package_id") or (
            self.envelope or {}).get("package_id")
        try:
            resolved = note.resolve_latest_handoff(
                self.canonical["id"], task_ref=self.task_ref,
                target_role=self.target["role"], cli=self.note_cli)
        except Exception as exc:
            raise _Stop(CONFIRMATION_FAILED,
                        "published note could not be re-resolved during "
                        "recovery; never republish from a lost local response",
                        extra={"error": _bounded_reason(exc)}) from None
        if not resolved.get("found"):
            raise _Stop(CONFIRMATION_FAILED,
                        "recovery found no published note to reuse; never "
                        "republish merely because a local response was lost")
        resolved_env = resolved["envelope"]
        resolved_id = (resolved.get("comment") or {}).get("id")
        if comment_id and resolved_id != comment_id:
            raise _Stop(CONFIRMATION_FAILED,
                        "recovery note comment id does not match the "
                        "recorded publication")
        if package_id and resolved_env.get("package_id") != package_id:
            raise _Stop(CONFIRMATION_FAILED,
                        "recovery note package_id does not match the "
                        "recorded publication")
        self.envelope = resolved_env
        self.published_comment_id = resolved_id
        self.ledger.append({
            "kind": "publish_outcome",
            "transaction_id": self.recorder.transaction_id,
            "published": False,
            "idempotent": True,
            "reused": True,
            "comment_id": self.published_comment_id,
            "package_id": resolved_env.get("package_id"),
        })
        self.machine.to("HANDOFF_PUBLISHED")

    def _step_publish(self) -> None:
        self.machine.require("ARTIFACT_READY")
        try:
            result = note.publish_handoff(
                self.envelope,
                issue_id=self.canonical["id"],
                prepared_by=self.caller_agent_id,
                allow_partial=self.envelope["status"] == "PARTIAL",
                clock=self.clock,
                cli=self.note_cli,
            )
        except Exception as exc:
            raise _Stop(PUBLISH_FAILED, "T06 publish failed",
                        extra={"error": _bounded_reason(exc)}) from None
        comment = result.get("comment") or result.get("existing_comment") or {}
        self.published_comment_id = comment.get("id")
        self.ledger.append({
            "kind": "publish_outcome",
            "transaction_id": self.recorder.transaction_id,
            "published": bool(result.get("published")),
            "idempotent": bool(result.get("idempotent")),
            "comment_id": self.published_comment_id,
            "package_id": self.envelope["package_id"],
        })
        self.machine.to("HANDOFF_PUBLISHED")

    def _step_confirm(self, envelope=None) -> dict:
        if envelope is None:
            self.machine.require("HANDOFF_PUBLISHED")
        elif self.machine.state not in ("ASSIGNMENT_TRIGGERED", "RUN_CORRELATED"):
            raise _Stop(INVALID_INPUT,
                        f"refresh confirm reached from {self.machine.state!r}")
        target = envelope if envelope is not None else self.envelope
        try:
            resolved = note.resolve_latest_handoff(
                self.canonical["id"], task_ref=self.task_ref,
                target_role=self.target["role"], cli=self.note_cli)
        except Exception as exc:
            raise _Stop(CONFIRMATION_FAILED,
                        "T06 discovery fail-closed while confirming "
                        "HANDOFF_READY; no assignment",
                        extra={"error": _bounded_reason(exc)}) from None
        if not resolved.get("found"):
            raise _Stop(CONFIRMATION_FAILED,
                        "no valid CONTEXT_HANDOFF record resolvable for this "
                        "task/role; no assignment",
                        extra={"records_seen":
                               (resolved.get("selection") or {}).get("records_seen")})
        resolved_env = resolved["envelope"]
        comment_id = (resolved.get("comment") or {}).get("id")
        problems = []
        if resolved_env.get("package_id") != target["package_id"]:
            problems.append("package_id")
        if envelope is None and comment_id != self.published_comment_id:
            problems.append("comment_id")
        if resolved_env.get("task_ref") != self.task_ref:
            problems.append("task_ref")
        if resolved_env.get("role") != self.target["role"]:
            problems.append("role")
        if problems:
            raise _Stop(CONFIRMATION_FAILED,
                        "the just-published note did not re-resolve as this "
                        "handoff's READY record; mismatch in " +
                        ", ".join(problems) + "; no assignment",
                        extra={"mismatch": problems,
                               "resolved_comment_id": comment_id})
        if envelope is None:
            self.confirmed = resolved_env
            self.machine.to("HANDOFF_READY_CONFIRMED")
        return resolved_env

    def _step_freshness_before_trigger(self) -> None:
        self.machine.require("HANDOFF_READY_CONFIRMED")
        binding = self.artifact_binding or {}
        requirements = binding.get("requirements") or []
        if not requirements:
            return
        store_file = binding.get("store_file")
        try:
            gate = _load_artifact_gate()
            store = cartifact._store_from_file(store_file)
            freshness = gate.evaluate_freshness(
                cartifact, store,
                previous_requirements=requirements,
                previous_digest=binding.get("digest"),
                current_requirements=None,
                target_role=self.target["role"],
                review_level=binding.get("review_level"))
        except Exception as exc:
            raise _Stop(PACKAGE_STALE, "artifact freshness re-check failed",
                        extra={"error": _bounded_reason(exc)}) from None
        self.ledger.append({
            "kind": "artifact_freshness",
            "transaction_id": self.recorder.transaction_id,
            "stale": bool(freshness.get("stale")),
            "previous_digest": freshness.get("previous_digest"),
            "current_digest": freshness.get("current_digest"),
        })
        if freshness.get("stale"):
            raise _Stop(PACKAGE_STALE,
                        "artifact dependencies stale or changed after "
                        "publication; no assignment",
                        extra={"reason": "package_stale"})

    def _step_trigger(self) -> None:
        self.machine.require("RUN_PRECHECK_TRIGGER")
        try:
            outcome = self.dispatch_cli.assign_issue(
                self.canonical["id"], self.target["agent_id"])
        except dispatch.AssignCommandFailedError as exc:
            raise _Stop(TRIGGER_COMMAND_FAILED,
                        "the assignment command failed before confirmation; "
                        "zero assumed target runs; operator reconciliation "
                        "is read-only, never a retry",
                        extra={"error": exc.envelope()}) from None
        except dispatch.AssignResponseInvalidError as exc:
            raise _Stop(TRIGGER_CONFIRMATION_REQUIRED,
                        "assignment response is unconfirmable; fail closed; "
                        "reconciliation is a later read-only operator action, "
                        "never a second trigger",
                        extra={"error": exc.envelope()}) from None
        self.trigger_argv = [r["argv"] for r in _tx_records(
            self.ledger, self.recorder.transaction_id)
            if r.get("kind") == "command"
            and r.get("command_class") == "assignment_trigger"][-1]
        self.trigger_confirmed = True
        self.ledger.append({
            "kind": "trigger_outcome",
            "transaction_id": self.recorder.transaction_id,
            "outcome": outcome["outcome"],
            "issue_id": self.canonical["id"],
            "agent_id": self.target["agent_id"],
        })
        self.machine.to("ASSIGNMENT_TRIGGERED")

    def _step_correlate_run(self) -> None:
        self.machine.require("ASSIGNMENT_TRIGGERED")
        try:
            listing = self.dispatch_cli.list_runs(
                self.canonical["id"], active=False, siblings=False)
            correlated = dispatch.correlate_intended_run(
                listing["runs"], issue_id=self.canonical["id"],
                agent_id=self.target["agent_id"],
                known_ids=self.known_run_ids)
        except (dispatch.RunStateUndeterminedError,
                dispatch.RunsResponseInvalidError) as exc:
            raise _Stop(RUN_CORRELATION_FAILED,
                        "intended run listing is unreadable; fail closed",
                        extra={"error": exc.envelope()}) from None
        except dispatch.RunCorrelationError as exc:
            raise _Stop(RUN_CORRELATION_FAILED, exc.message,
                        extra={"error": exc.envelope()}) from None
        self.intended_run = {
            "id": correlated["run"]["id"],
            "issue_id": correlated["run"]["issue_id"],
            "agent_id": correlated["run"]["agent_id"],
            "status": correlated["run"]["status"],
            "count": 1,
        }
        self.ledger.append({
            "kind": "run_correlation",
            "transaction_id": self.recorder.transaction_id,
            "run_id": self.intended_run["id"],
            "agent_id": self.intended_run["agent_id"],
            "count": 1,
        })
        self.machine.to("RUN_CORRELATED")

    def _step_self_check(self) -> None:
        self.machine.require("RUN_CORRELATED")
        attempts, refreshes = 0, 0
        current_env = self.confirmed
        while True:
            attempts += 1
            outcome = self._run_self_check(current_env)
            status = outcome["status"]
            if status == "READY":
                self.self_check_evidence = {
                    "status": status, "attempts": attempts,
                    "refreshes": refreshes, "reasons": outcome.get("reasons") or [],
                }
                self.machine.to("TARGET_SELF_CHECKED")
                self.machine.to("COMPLETED")
                return
            if status == "BLOCKED":
                raise _Stop(SELF_CHECK_BLOCKED,
                            "target SELF_CHECK blocked; no consequential work",
                            escalation=_escalation(
                                "engineering-lead-or-squad",
                                "self_check_blocked",
                                reasons=outcome.get("reasons") or []),
                            extra={"self_check": {"status": status,
                                                  "attempts": attempts,
                                                  "reasons": outcome.get("reasons")}})
            if attempts >= MAX_SELF_CHECK_ATTEMPTS:
                raise _Stop(SELF_REFRESH_EXHAUSTED,
                            "a second SELF_CHECK still requires refresh; "
                            "consequential work stays stopped",
                            extra={"self_check": {"status": status,
                                                  "attempts": attempts,
                                                  "reasons": outcome.get("reasons")}})
            refreshes += 1
            self.ledger.append({
                "kind": "self_refresh",
                "transaction_id": self.recorder.transaction_id,
                "attempt": attempts,
                "reasons": outcome.get("reasons") or [],
            })
            current_env = self._refresh(current_env)
            self.machine.require("RUN_CORRELATED")

    def _run_self_check(self, envelope: dict) -> dict:
        request = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": self.task_ref,
            "role": self.target["role"],
            "task_snapshot": self._snapshot_for_self_check(),
            "package_ref": envelope["package_id"],
        }
        try:
            if self.findings_source is not None:
                if self.world.get("findings") is not None or \
                        self.finding_store is not None:
                    raise _Stop(
                        INVALID_INPUT,
                        "a verified Findings source binding cannot be "
                        "combined with world.findings or a legacy "
                        "finding_store")
                trace = selfcheck.self_check_with_trace(
                    request, packages=[envelope],
                    registry=self.world.get("registry"),
                    current=self._current(),
                    findings_source=self.findings_source,
                    findings_prior_observation=self._prepare_observation,
                    source_observer_run_id=self.recorder.transaction_id,
                )
                self._self_check_observation = trace.get(
                    "findings_observation")
            elif self.legacy_fixture:
                trace = selfcheck.self_check_with_trace(
                    request, packages=[envelope],
                    registry=self.world.get("registry"),
                    current=self._current(),
                    findings=self.world.get("findings"),
                    finding_store=self.finding_store,
                )
            else:
                raise _Stop(
                    INVALID_INPUT,
                    "production assignment SELF_CHECK requires a verified "
                    "Findings source binding")
        except _Stop:
            raise
        except Exception as exc:
            raise _Stop(PREPARE_FAILED, "T04 self_check failed",
                        extra={"error": _bounded_reason(exc)}) from None
        return trace["result"]

    def _snapshot_for_self_check(self) -> dict:
        return dict((self._last_request or {}).get("task_snapshot") or {})

    def _current(self):
        current = self.world.get("current")
        if callable(current):
            return current()
        if isinstance(current, dict):
            return current
        return selfcheck.current_revisions()

    def _refresh(self, confirmed: dict) -> dict:
        envelope = self._prepare_pipeline()
        if envelope["status"] != "READY":
            raise _Stop(SELF_REFRESH_EXHAUSTED,
                        f"bounded refresh produced {envelope['status']!r}; "
                        "consequential work stays stopped",
                        extra={"gaps": _gaps(envelope)})
        if chandoff.canonical_json(envelope.get("built_from") or {}) == \
                chandoff.canonical_json(confirmed.get("built_from") or {}):
            self.ledger.append({
                "kind": "refresh_publish_skipped",
                "transaction_id": self.recorder.transaction_id,
                "reason": "built_from unchanged; the confirmed record already "
                          "carries this package state",
            })
            self._step_confirm(confirmed)
            return confirmed
        self._step_publish_refresh(envelope)
        self._step_confirm_refresh(envelope)
        return envelope

    def _step_publish_refresh(self, envelope: dict) -> None:
        try:
            result = note.publish_handoff(
                envelope, issue_id=self.canonical["id"],
                prepared_by=self.caller_agent_id, allow_partial=False,
                clock=self.clock, cli=self.note_cli)
        except Exception as exc:
            raise _Stop(PUBLISH_FAILED, "T06 refresh publish failed",
                        extra={"error": _bounded_reason(exc)}) from None
        comment = result.get("comment") or result.get("existing_comment") or {}
        self.ledger.append({
            "kind": "publish_outcome",
            "transaction_id": self.recorder.transaction_id,
            "published": bool(result.get("published")),
            "idempotent": bool(result.get("idempotent")),
            "comment_id": comment.get("id"),
            "package_id": envelope["package_id"],
            "phase": "self_refresh",
        })

    def _step_confirm_refresh(self, envelope: dict) -> None:
        if self.machine.state not in ("ASSIGNMENT_TRIGGERED", "RUN_CORRELATED"):
            raise _Stop(INVALID_INPUT,
                        f"refresh confirm reached from {self.machine.state!r}")
        try:
            resolved = note.resolve_latest_handoff(
                self.canonical["id"], task_ref=self.task_ref,
                target_role=self.target["role"], cli=self.note_cli)
        except Exception as exc:
            raise _Stop(CONFIRMATION_FAILED,
                        "refreshed record fail-closed during discovery",
                        extra={"error": _bounded_reason(exc)}) from None
        if not resolved.get("found"):
            raise _Stop(CONFIRMATION_FAILED,
                        "refreshed handoff record not resolvable; no further "
                        "action",
                        extra={"records_seen":
                               (resolved.get("selection") or {}).get("records_seen")})
        resolved_env = resolved["envelope"]
        if resolved_env.get("package_id") != envelope["package_id"]:
            raise _Stop(CONFIRMATION_FAILED,
                        "refreshed record did not re-resolve as the just-"
                        "published package; no further action",
                        extra={"mismatch": ["package_id"]})

    # -- result -----------------------------------------------------------

    def _finish(self, status: str, reason: str | None,
                escalation=None, extra=None) -> dict:
        tx_records = _tx_records(self.ledger, self.recorder.transaction_id)
        audit = dispatch.audit_ledger(tx_records, self.executable)
        result = {
            "ok": status == COMPLETED,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": self.recorder.transaction_id,
            "mode": "simulation",
            "terminal_status": status,
            "stop_reason": reason,
            "escalation": escalation,
            "issue": dict(self.canonical) if self.canonical else None,
            "task_ref": self.task_ref,
            "caller": {"role": self.spec["caller_role"],
                       "agent_id": self.caller_agent_id},
            "target": dict(self.target) if self.target else None,
            "package": ({"package_id": self.envelope["package_id"],
                         "status": self.envelope["status"]}
                        if self.envelope else None),
            "published_comment_id": self.published_comment_id,
            "handoff_ready": self.confirmed is not None,
            "trigger": {
                "type": "issue_assign",
                "count": audit["assignment_trigger"]["count"],
                "confirmed": self.trigger_confirmed,
                "argv": self.trigger_argv,
            },
            "self_check": self.self_check_evidence or (extra or {}).get("self_check"),
            "intended_run": dict(self.intended_run) if self.intended_run else None,
            "artifacts": ({
                "digest": (self.artifact_binding or {}).get("digest"),
                "status": (self.artifact_binding or {}).get("status"),
                "count": len((self.artifact_binding or {}).get("requirements") or []),
            } if self.artifact_binding else None),
            "pins": dict(self.u05_pins) if self.u05_pins else None,
            "findings_source": ({
                "mode": "bound",
                "source_id": getattr(self.findings_source, "source_id", None),
                "binding_digest": getattr(self.findings_source,
                                         "binding_digest", None),
                "prepare_snapshot_digest":
                    (self._prepare_observation or {}).get("snapshot_digest"),
                "self_check_snapshot_digest":
                    (getattr(self, "_self_check_observation", None) or {})
                    .get("snapshot_digest"),
            } if self.findings_source is not None else None),
            "transitions": self.machine.transitions,
            "compose_fn_source": "injected",
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
            "audit": audit,
        }
        if extra:
            result["extra"] = extra
        self.ledger.append({
            "kind": "transaction_result",
            "transaction_id": self.recorder.transaction_id,
            "terminal_status": status,
            "result": result,
        })
        return result


def _gaps(envelope: dict) -> list:
    package = envelope.get("package") or {}
    gaps = [c.get("id") for c in (package.get("open_conflicts") or [])
            if isinstance(c, dict) and c.get("id")]
    gaps += [g for g in (package.get("blocked_by") or []) if g]
    return gaps


def _gate_summary(plan_result: dict) -> dict:
    gate = plan_result.get("finding_gate") or {}
    return {
        "status": gate.get("status"),
        "blocked_findings": [{"finding_id": f.get("finding_id"),
                              "reason": f.get("reason")}
                             for f in (gate.get("blocked_findings") or [])],
    }


def _dispatch_terminal(exc: dispatch.DispatchError) -> str:
    return {
        "forbidden_create_argv": INVALID_INPUT,
        "create_command_failed": CREATE_UNVERIFIED,
        "create_response_invalid": CREATE_RESPONSE_INVALID,
        "create_response_not_unassigned": CREATE_NOT_UNASSIGNED,
        "forbidden_update_argv": INVALID_INPUT,
        "update_command_failed": UPDATE_UNVERIFIED,
        "update_response_invalid": UPDATE_RESPONSE_INVALID,
        "update_response_not_unassigned": UPDATE_NOT_UNASSIGNED,
        "assignment_argv_invalid": TRIGGER_COMMAND_FAILED,
        "assignment_command_failed": TRIGGER_COMMAND_FAILED,
        "assignment_response_unconfirmable": TRIGGER_CONFIRMATION_REQUIRED,
        "unexpected_run": UNEXPECTED_RUN,
        "run_state_undetermined": RUN_STATE_UNDETERMINED,
        "runs_response_invalid": RUN_STATE_UNDETERMINED,
        "run_correlation_failed": RUN_CORRELATION_FAILED,
    }.get(exc.code, PREPARE_FAILED)


def _load_artifact_gate():
    scripts = ROOT / "skills" / "multica-context-handoff" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import artifact_gate  # noqa: WPS433
    return artifact_gate


def _standalone_result(transaction_id: str, status: str, reason: str | None,
                       ledger: dispatch.TransactionLedger,
                       executable: str, escalation=None, extra=None) -> dict:
    result = {
        "ok": status == COMPLETED,
        "orchestrator": ORCHESTRATOR_VERSION,
        "transaction_id": transaction_id,
        "mode": "simulation",
        "terminal_status": status,
        "stop_reason": reason,
        "escalation": escalation,
        "issue": None,
        "task_ref": None,
        "caller": None,
        "target": None,
        "package": None,
        "published_comment_id": None,
        "handoff_ready": False,
        "trigger": {"type": "issue_assign", "count": 0, "confirmed": False,
                    "argv": None},
        "self_check": None,
        "intended_run": None,
        "artifacts": None,
        "pins": None,
        "transitions": ["INIT"],
        "compose_fn_source": "injected",
        "guarantees": dict(GUARANTEES),
        "uncertainty": list(UNCERTAINTY),
        "audit": dispatch.audit_ledger(_tx_records(ledger, transaction_id),
                                       executable),
    }
    if extra:
        result["extra"] = extra
    ledger.append({
        "kind": "transaction_result",
        "transaction_id": transaction_id,
        "terminal_status": status,
        "result": result,
    })
    return result


def run_assignment_handoff(spec, *, caller_role: str, target_role_spec: str,
                           runner, ledger: dispatch.TransactionLedger,
                           compose_fn: Callable, transaction_id: str,
                           purpose: str = "implementation",
                           policy: dict | None = None,
                           clock: Callable = now_iso,
                           bundle_dir=None, finding_store=None,
                           world: dict | None = None,
                           workdir=None, executable: str = "multica",
                           crash_at: str | None = None,
                           resume: dict | None = None,
                           findings_source=None,
                           legacy_fixture: bool | None = None) -> dict:
    """Run one assignment-handoff transaction (simulation default).

    Idempotency: a recorded COMPLETED result for this transaction_id is
    returned as-is with zero new commands; a recorded incomplete result is
    refused (REPLAY_REFUSED) — the operator reviews the ledger, the caller
    starts a new transaction id after reconciliation.
    """
    if policy is not None and not isinstance(policy, dict):
        return _standalone_result(
            transaction_id, INVALID_INPUT, "policy must be a dict or None",
            ledger, executable)
    try:
        validated = _validate_spec(spec, caller_role, transaction_id, compose_fn)
    except _Stop as stop:
        return _standalone_result(transaction_id, stop.status, stop.reason,
                                  ledger, executable,
                                  escalation=stop.escalation, extra=stop.extra)
    prior = _recorded_result(transaction_id, ledger)
    if prior is not None:
        if prior.get("terminal_status") == COMPLETED:
            replayed = dict(prior)
            replayed["replayed"] = True
            replayed["commands"] = []
            return replayed
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "terminal_status": REPLAY_REFUSED,
            "stop_reason": "a prior incomplete transaction result is recorded "
                           "for this id; review the ledger and reconcile "
                           "read-only before starting a new transaction",
            "prior_terminal_status": prior.get("terminal_status"),
            "audit": dispatch.audit_ledger(
                _tx_records(ledger, transaction_id), executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    workdir = Path(workdir) if workdir is not None else Path.cwd()
    if legacy_fixture is None:
        legacy_fixture = False
    recorder = dispatch.RecordingRunner(runner, ledger, executable=executable,
                                        transaction_id=transaction_id)
    handoff = AssignmentHandoff(
        validated=validated, target_role_spec=target_role_spec,
        recorder=recorder, ledger=ledger, compose_fn=compose_fn, clock=clock,
        bundle_dir=bundle_dir, finding_store=finding_store, world=world,
        policy=policy, workdir=workdir, executable=executable,
        crash_at=crash_at, resume=resume, findings_source=findings_source,
        legacy_fixture=legacy_fixture, effects_runner=runner)
    try:
        return handoff.run()
    except CrashSimulated as crash:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "terminal_status": CRASH_SIMULATED,
            "crash_at": crash.state,
            "stop_reason": f"simulated crash at {crash.state}",
            "audit": dispatch.audit_ledger(
                _tx_records(ledger, transaction_id), executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }


def acceptance_evidence(result: dict, ledger: dispatch.TransactionLedger) -> dict:
    """YAML done-criteria evidence, computed from the observable ledger."""
    tx = result.get("transaction_id")
    tx_records = _tx_records(ledger, tx)
    audit = result.get("audit") or {}
    creates = [r for r in tx_records if r.get("kind") == "command"
               and r.get("command_class") == "issue_create"]
    assigns = [r for r in tx_records if r.get("kind") == "command"
               and r.get("command_class") == "assignment_trigger"]
    confirms = [r for r in tx_records if r.get("kind") == "state_transition"
                and r.get("to") == "HANDOFF_READY_CONFIRMED"]
    self_checks = result.get("self_check") or {}
    zero_run_before = bool(
        creates and audit.get("issue_create", {}).get("unassigned_argv_ok")
        and not audit.get("mention_hits")
        and not audit.get("unexpected_write_classes")
        and all(_assign_after_confirm(a, confirms) for a in assigns))
    return {
        "run_before_handoff": 0 if zero_run_before else 1,
        "intended_run_count_per_handoff": audit.get("assignment_trigger", {}).get("count"),
        "assignment_is_only_trigger": bool(
            not audit.get("mention_hits")
            and not audit.get("unexpected_write_classes")
            and audit.get("assignment_trigger", {}).get("argv_ok", False)),
        "published_handoff_confirmed_before_assignment": bool(
            confirms and all(_assign_after_confirm(a, confirms) for a in assigns)),
        "self_check_ready_before_work": bool(
            result.get("terminal_status") != COMPLETED
            or (self_checks.get("status") == "READY")),
        "blocked_or_unresolved_refresh_stops_work": (
            True if result.get("terminal_status") in (
                COMPLETED, SELF_CHECK_BLOCKED, SELF_REFRESH_EXHAUSTED)
            else None),
        "retry_does_not_duplicate_trigger": bool(
            result.get("replayed") is True and result.get("commands") == []
            or result.get("replayed") is None),
        "ambiguous_trigger_response_fails_closed": bool(
            result.get("terminal_status") != TRIGGER_CONFIRMATION_REQUIRED
            or (result.get("trigger") or {}).get("confirmed") is False),
        "live_mutations": 0 if result.get("mode") == "simulation" else 1,
        "artifact_ready_before_publish_and_trigger": bool(
            result.get("terminal_status") != COMPLETED
            or any(r.get("kind") == "state_transition"
                   and r.get("to") == "ARTIFACT_READY" for r in tx_records)),
        "unexpected_run_precheck": bool(
            result.get("terminal_status") != COMPLETED
            or sum(1 for r in tx_records if r.get("kind") == "run_precheck") >= 2),
        "intended_run_correlated": bool(
            result.get("terminal_status") != COMPLETED
            or (result.get("intended_run") or {}).get("count") == 1),
        "mention_count": len(audit.get("mention_hits") or []),
    }


def _assign_after_confirm(assign_record: dict, confirms: list) -> bool:
    if not confirms:
        return False
    assign_seq = int(assign_record.get("seq") or 0)
    return all(assign_seq > int(c.get("seq") or 0) for c in confirms)


def replay_transaction(transaction_id: str,
                       ledger: dispatch.TransactionLedger) -> dict:
    """Read-only replay of a recorded transaction result (operator tool)."""
    prior = _recorded_result(transaction_id, ledger)
    if prior is None:
        raise dispatch.LedgerError("no recorded transaction result",
                                   transaction_id=transaction_id)
    return {"transaction_id": transaction_id,
            "terminal_status": prior.get("terminal_status"),
            "result": prior}


def reconcile_trigger(issue_id: str, agent_id: str, *, runner,
                      ledger: dispatch.TransactionLedger,
                      executable: str = "multica",
                      transaction_id: str = "reconcile") -> dict:
    """Read-only operator reconciliation after TRIGGER_CONFIRMATION_REQUIRED.

    Issues only the read-only `issue get` and reports what the platform
    observably holds. It never re-issues the assignment.
    """
    recorder = dispatch.RecordingRunner(runner, ledger, executable=executable,
                                        transaction_id=transaction_id)
    cli = dispatch.DispatchCli(executable, runner=recorder, workdir=Path.cwd())
    try:
        issue = cli.issue_get(issue_id)
    except dispatch.DispatchError as exc:
        return {"ok": False, "error": exc.envelope(),
                "note": "reconciliation read failed; state remains unconfirmed"}
    assignee_id = issue.get("assignee_id")
    confirmed = assignee_id == agent_id
    return {"ok": True, "issue_id": issue["id"],
            "identifier": issue["identifier"],
            "observed_assignee_id": assignee_id,
            "target_agent_id": agent_id,
            "trigger_confirmed": confirmed,
            "note": "confirmed" if confirmed else
            "not assigned; the original trigger did not take effect"}


def classify_crash_boundary(ledger: dispatch.TransactionLedger,
                            transaction_id: str) -> dict:
    """Classify a crash from durable ledger evidence. Never guess."""
    records = _tx_records(ledger, transaction_id)
    prior = _recorded_result(transaction_id, ledger)
    commands = [r for r in records if r.get("kind") == "command"]
    assign_cmds = [r for r in commands
                   if r.get("command_class") == "assignment_trigger"]
    publish_cmds = [r for r in commands
                    if r.get("command_class") == "comment_publish"]
    last_state = "INIT"
    for rec in records:
        if rec.get("kind") == "state_transition" and rec.get("to"):
            last_state = rec["to"]
    canonical = None
    for rec in records:
        if rec.get("kind") == "canonical_issue":
            canonical = {"id": rec.get("id"), "identifier": rec.get("identifier")}
    published_comment_id = None
    package_id = None
    for rec in records:
        if rec.get("kind") == "publish_outcome":
            published_comment_id = rec.get("comment_id")
            package_id = rec.get("package_id")
    if prior and prior.get("terminal_status") == COMPLETED:
        return {"boundary": "completed", "next": "replay",
                "last_state": last_state}
    if assign_cmds or last_state in ("ASSIGNMENT_TRIGGERED", "RUN_CORRELATED",
                                     "TARGET_SELF_CHECKED"):
        return {"boundary": "post_trigger_unconfirmed", "next": "stop",
                "status": TRIGGER_CONFIRMATION_REQUIRED,
                "last_state": last_state,
                "reason": "assignment was possibly issued; never retry"}
    if prior is not None:
        return {"boundary": "recorded_incomplete", "next": "stop",
                "status": REPLAY_REFUSED, "last_state": last_state,
                "prior_terminal_status": prior.get("terminal_status")}
    if publish_cmds or last_state in POST_PUBLISH_PRE_TRIGGER_STATES:
        return {"boundary": "post_publish_pre_trigger",
                "next": "reuse_note_if_fresh",
                "canonical": canonical,
                "published_comment_id": published_comment_id,
                "package_id": package_id, "last_state": last_state}
    if canonical or last_state in PRE_PUBLISH_STATES:
        return {"boundary": "pre_publish", "next": "continue_from_issue",
                "canonical": canonical, "last_state": last_state}
    return {"boundary": "ambiguous", "next": "stop",
            "status": V2_2_REBASE_BLOCKED, "last_state": last_state}


def recover_assignment_handoff(spec, *, caller_role: str, target_role_spec: str,
                               runner, ledger: dispatch.TransactionLedger,
                               compose_fn: Callable, transaction_id: str,
                               **kwargs) -> dict:
    """Resume from durable evidence. Unique next action only; never guess."""
    classified = classify_crash_boundary(ledger, transaction_id)
    classified_record = {
        "kind": "recovery_classification",
        "transaction_id": transaction_id,
        "boundary": classified["boundary"],
        "next": classified["next"],
    }
    ledger.append(classified_record)
    if classified["boundary"] == "completed":
        prior = _recorded_result(transaction_id, ledger)
        replayed = dict(prior)
        replayed["replayed"] = True
        replayed["commands"] = []
        replayed["recovery"] = classified
        return replayed
    if classified["next"] == "stop":
        status = classified.get("status") or V2_2_REBASE_BLOCKED
        if classified["boundary"] == "post_trigger_unconfirmed":
            status = TRIGGER_CONFIRMATION_REQUIRED
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "terminal_status": status,
            "stop_reason": classified.get("reason") or (
                "recovery has no unique safe action"),
            "recovery": classified,
            "audit": dispatch.audit_ledger(
                _tx_records(ledger, transaction_id),
                kwargs.get("executable", "multica")),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    resume = {}
    if classified["boundary"] == "pre_publish" and classified.get("canonical"):
        resume["canonical"] = classified["canonical"]
    if classified["boundary"] == "post_publish_pre_trigger":
        if not classified.get("canonical"):
            return {
                "ok": False,
                "orchestrator": ORCHESTRATOR_VERSION,
                "transaction_id": transaction_id,
                "mode": "simulation",
                "terminal_status": CONFIRMATION_FAILED,
                "stop_reason": "published note recovery lacks canonical issue",
                "recovery": classified,
                "guarantees": dict(GUARANTEES),
                "uncertainty": list(UNCERTAINTY),
            }
        resume["canonical"] = classified["canonical"]
        resume["skip_publish"] = True
        resume["published_comment_id"] = classified.get("published_comment_id")
        resume["package_id"] = classified.get("package_id")
    result = run_assignment_handoff(
        spec, caller_role=caller_role, target_role_spec=target_role_spec,
        runner=runner, ledger=ledger, compose_fn=compose_fn,
        transaction_id=transaction_id, resume=resume, **kwargs)
    result = dict(result)
    result["recovery"] = classified
    return result


def _deterministic_compose(plan_obj: dict, request: dict, errors=None) -> dict:
    return compose.subset_result(plan_obj)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="U06 Assignment SAFE_DISPATCH orchestrator (simulation)")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser(
        "run", help="run one handoff transaction with a fixture runner")
    run_p.add_argument("--spec-file", required=True)
    run_p.add_argument("--fixture-runner-file", required=True,
                       help="canned response table JSON for the simulation runner")
    run_p.add_argument("--transaction-id", required=True)
    run_p.add_argument("--caller-role", required=True)
    run_p.add_argument("--target-role", required=True)
    run_p.add_argument("--policy-file", default=None)
    run_p.add_argument("--ledger-file", default=None)
    run_p.add_argument("--bundle-dir", default=None)
    run_p.add_argument("--executable", default="multica")

    val = sub.add_parser("validate-spec", help="zero-command spec validation")
    val.add_argument("--spec-file", required=True)
    val.add_argument("--caller-role", required=True)
    val.add_argument("--transaction-id", required=True)

    role = sub.add_parser("resolve-role", help="U05 staged role resolution")
    role.add_argument("--role-spec", required=True)
    role.add_argument("--bundle-dir", default=None)

    audit_p = sub.add_parser("audit", help="audit a ledger file")
    audit_p.add_argument("--ledger-file", required=True)
    audit_p.add_argument("--executable", default="multica")

    replay_p = sub.add_parser("replay", help="read-only replay of a recorded result")
    replay_p.add_argument("--transaction-id", required=True)
    replay_p.add_argument("--ledger-file", required=True)
    args = parser.parse_args(argv)

    if args.command == "resolve-role":
        try:
            target = resolve_assignment_target(
                args.role_spec, bundle_dir=args.bundle_dir)
        except (RoutingRequiredError, U05BundleError) as exc:
            print(json.dumps({"ok": False, "routing_required": True,
                              "error": exc.envelope()},
                             ensure_ascii=False, indent=2, sort_keys=True))
            return 3
        print(json.dumps({"ok": True, "target": target},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "validate-spec":
        try:
            spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
            _validate_spec(spec, args.caller_role, args.transaction_id,
                           compose_fn=_deterministic_compose)
        except _Stop as stop:
            print(json.dumps({"ok": False, "terminal_status": stop.status,
                              "stop_reason": stop.reason},
                             ensure_ascii=False, indent=2, sort_keys=True))
            return 2
        print(json.dumps({"ok": True}, ensure_ascii=False))
        return 0
    if args.command == "audit":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        print(json.dumps(dispatch.audit_ledger(ledger.records, args.executable),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "replay":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        print(json.dumps(replay_transaction(args.transaction_id, ledger),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    fixture_table = json.loads(
        Path(args.fixture_runner_file).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.policy_file).read_text(encoding="utf-8")) \
        if args.policy_file else None
    ledger = dispatch.TransactionLedger()
    fixture_runner = construct_simulation_entry(
        dispatch.FixtureRunner(fixture_table))
    result = run_assignment_handoff(
        spec, caller_role=args.caller_role, target_role_spec=args.target_role,
        runner=fixture_runner, ledger=ledger,
        compose_fn=_deterministic_compose,
        transaction_id=args.transaction_id,
        policy=policy, bundle_dir=args.bundle_dir, executable=args.executable,
        legacy_fixture=True)
    if args.ledger_file:
        ledger.save(args.ledger_file)
    print(json.dumps({"result": result,
                      "acceptance_evidence": acceptance_evidence(result, ledger)},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
