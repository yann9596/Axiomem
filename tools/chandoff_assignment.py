#!/usr/bin/env python3
"""T09 — Assignment Handoff main-path orchestrator (YZT-64).

Implements the frozen strict order (impl doc §34.2) as an auditable,
fail-closed state machine over the accepted T00–T08 boundaries:

    1. create unassigned issue        (no assignee / no mention / no trigger)
    2. canonical issue id             (only from the successful create response)
    3. resolve target role            (constrained to the T08 baseline mapping)
    4. PREPARE_HANDOFF                (T05 snapshot -> T01 PLAN -> caller
                                       semantic compose -> T02/T03 finalize)
    5. publish non-trigger note       (T06, explicit authorization policy)
    6. confirm HANDOFF_READY          (T06 discovery re-resolution)
    7. exactly one assignment trigger (issue assign --to-id; no mention)
    8. target SELF_CHECK              (T04; bounded refresh, then work gate)

Boundary this module keeps:

- every Multica argv flows through `chandoff_dispatch.DispatchCli` /
  `chandoff_note.NoteCli` / `chandoff_adapter.MulticaCli` allowlists and a
  ledger-recording runner; the orchestrator itself never builds a Multica
  argv and never constructs a subprocess runner;
- the frozen T00–T03 schemas and policies are reused verbatim: the
  orchestrator copies no Scope/Authority/Case/Finding/lifecycle/semantic
  policy and only branches on the deterministic statuses those surfaces emit;
- the semantic compose step is INJECTED (`compose_fn`): the orchestrator
  never calls a model; the T02 one-bounded-repair rule is honored by
  re-validating the repaired proposal against the SAME plan;
- PARTIAL stops by default; continuation requires an explicit caller policy
  decision recorded in the ledger with every gap kept visible;
- BLOCKED anywhere stops before publish/assignment with escalation evidence;
- the trigger is `issue assign --to-id <uuid>` exactly once; command
  failure, and non-parseable or marker-less responses, fail closed to
  TRIGGER_COMMAND_FAILED / TRIGGER_CONFIRMATION_REQUIRED without retry —
  reconciliation is a later read-only operator action;
- simulation is the default and the only mode exercised by YZT-64; live
  issue-create/assignment execution requires a separate explicit
  authorization document (T09 never runs that path — T12/T13 own it);
- the result records ids, ordered transitions, issued commands, trigger
  evidence and terminal status from the ledger — never secrets, never
  comment bodies — and states its uncertainty explicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff  # noqa: E402
import chandoff_adapter as adapter  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_note as note  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402
from chandoff_instructions import ROLES, ROLE_BY_SLUG  # noqa: E402
from cutil import ROOT, now_iso  # noqa: E402

ORCHESTRATOR_VERSION = "T09/1.0"
BUNDLE_SUBPATH = "adapters/multica/agent-instructions"
MAX_SELF_CHECK_ATTEMPTS = 2
MAX_COMPOSE_ATTEMPTS = 2

STATES = (
    "INIT", "ISSUE_CREATED", "TARGET_RESOLVED", "HANDOFF_PREPARED",
    "HANDOFF_PUBLISHED", "HANDOFF_READY_CONFIRMED", "ASSIGNMENT_TRIGGERED",
    "TARGET_SELF_CHECKED", "COMPLETED",
)

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

TERMINAL_STATUSES = (
    COMPLETED, INVALID_INPUT, REPLAY_REFUSED, ROUTING_REQUIRED,
    CREATE_UNVERIFIED, CREATE_RESPONSE_INVALID, CREATE_NOT_UNASSIGNED,
    PREPARE_FAILED, PREPARE_BLOCKED, PREPARE_PARTIAL_STOPPED,
    COMPOSE_REJECTED, PUBLISH_FAILED, CONFIRMATION_FAILED,
    TRIGGER_COMMAND_FAILED, TRIGGER_CONFIRMATION_REQUIRED,
    SELF_CHECK_BLOCKED, SELF_REFRESH_EXHAUSTED,
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
    """Exact T08 role-table lookup by slug or display name; nothing guessed."""
    value = role_spec.strip() if isinstance(role_spec, str) else ""
    if value in ROLE_BY_SLUG:
        return value
    for slug, name in ROLES:
        if value == name:
            return slug
    raise RoutingRequiredError(
        f"target role {role_spec!r} is not in the frozen T08 role table",
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
        expected = _role_names().get(role)
        if expected is None or name != expected:
            raise T08BundleError(
                "T08 baseline agent name does not match the frozen role table",
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
    """Resolve the handoff target strictly inside the T08 baseline/role mapping.

    Zero match or ambiguity -> RoutingRequired; the T08 binding plan's
    excluded exception-path role (context-engineer) is never a dispatch
    target; a role missing from a stale/mutated baseline is routing-required,
    never guessed.
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
    return {
        "title": title, "description": description, "project_id": project_id,
        "parent_issue_id": parent, "purpose": purpose, "priority": priority,
        "options": options, "decision_markers": markers,
        "caller_role": caller_slug,
    }


def _validate_role_arg(value, field: str) -> str:
    try:
        return _slug_of(value)
    except RoutingRequiredError:
        raise _Stop(INVALID_INPUT,
                    f"{field} {value!r} is not in the frozen T08 role table") from None


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
                 policy: dict | None, workdir: Path, executable: str):
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

    def run(self) -> dict:
        try:
            self._step_create()
            self._step_resolve()
            envelope = self._prepare_pipeline()
            self._step_finalize(envelope)
            self._step_publish()
            self._step_confirm()
            self._step_trigger()
            self._step_self_check()
        except _Stop as stop:
            return self._finish(stop.status, stop.reason,
                                escalation=stop.escalation, extra=stop.extra)
        except dispatch.DispatchError as exc:
            return self._finish(_dispatch_terminal(exc), exc.message,
                                escalation={"required": True,
                                            "reason": exc.code},
                                extra={"error": exc.envelope()})
        except AssignmentError as exc:
            return self._finish(ROUTING_REQUIRED, exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad", exc.code,
                                    **exc.details),
                                extra={"error": exc.envelope()})
        return self._finish(COMPLETED, None)

    # -- steps ------------------------------------------------------------

    def _step_create(self) -> None:
        self.machine.require("INIT")
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
        self.canonical = {"id": data["id"], "identifier": data["identifier"]}
        self.task_ref = adapter.task_ref_of(data["identifier"])
        self.machine.to("ISSUE_CREATED")

    def _step_resolve(self) -> None:
        self.machine.require("ISSUE_CREATED")
        try:
            self.target = resolve_target(self.target_role_spec,
                                         bundle_dir=self.bundle_dir)
        except (RoutingRequiredError, T08BundleError) as exc:
            raise _Stop(ROUTING_REQUIRED, exc.message,
                        escalation=_escalation("engineering-lead-or-squad",
                                               exc.code, **exc.details),
                        extra={"error": exc.envelope()}) from None
        mapping = t08_mapping(self.bundle_dir)
        caller_entry = mapping["agents"].get(self.spec["caller_role"])
        if caller_entry is None:
            raise _Stop(ROUTING_REQUIRED,
                        f"caller role {self.spec['caller_role']!r} has no "
                        "T08 baseline agent; T08 preconditions drifted",
                        escalation=_escalation("engineering-lead-or-squad",
                                               "caller_agent_unresolved"),
                        extra={"caller_role": self.spec["caller_role"]})
        self.caller_agent_id = caller_entry["agent_id"]
        self.machine.to("TARGET_RESOLVED")

    def _prepare_pipeline(self) -> dict:
        if self.machine.state not in ("TARGET_RESOLVED", "ASSIGNMENT_TRIGGERED"):
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
        self.machine.require("TARGET_RESOLVED")
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

    def _step_publish(self) -> None:
        self.machine.require("HANDOFF_PREPARED")
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
        self.machine.require("HANDOFF_PUBLISHED" if envelope is None
                             else "ASSIGNMENT_TRIGGERED")
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

    def _step_trigger(self) -> None:
        self.machine.require("HANDOFF_READY_CONFIRMED")
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

    def _step_self_check(self) -> None:
        self.machine.require("ASSIGNMENT_TRIGGERED")
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
            self.machine.require("ASSIGNMENT_TRIGGERED")

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
            trace = selfcheck.self_check_with_trace(
                request, packages=[envelope],
                registry=self.world.get("registry"),
                current=self._current(),
                findings=self.world.get("findings"),
                finding_store=self.finding_store,
            )
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
        self.machine.require("ASSIGNMENT_TRIGGERED")
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
        "assignment_argv_invalid": TRIGGER_COMMAND_FAILED,
        "assignment_command_failed": TRIGGER_COMMAND_FAILED,
        "assignment_response_unconfirmable": TRIGGER_CONFIRMATION_REQUIRED,
    }.get(exc.code, PREPARE_FAILED)


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
                           workdir=None, executable: str = "multica") -> dict:
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
    recorder = dispatch.RecordingRunner(runner, ledger, executable=executable,
                                        transaction_id=transaction_id)
    handoff = AssignmentHandoff(
        validated=validated, target_role_spec=target_role_spec,
        recorder=recorder, ledger=ledger, compose_fn=compose_fn, clock=clock,
        bundle_dir=bundle_dir, finding_store=finding_store, world=world,
        policy=policy, workdir=workdir, executable=executable)
    return handoff.run()


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


def _deterministic_compose(plan_obj: dict, request: dict, errors=None) -> dict:
    return compose.subset_result(plan_obj)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T09 Assignment Handoff main-path orchestrator (simulation)")
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

    role = sub.add_parser("resolve-role", help="T08-constrained role resolution")
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
            target = resolve_target(args.role_spec, bundle_dir=args.bundle_dir)
        except (RoutingRequiredError, T08BundleError) as exc:
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
    result = run_assignment_handoff(
        spec, caller_role=args.caller_role, target_role_spec=args.target_role,
        runner=dispatch.FixtureRunner(fixture_table), ledger=ledger,
        compose_fn=_deterministic_compose,
        transaction_id=args.transaction_id,
        policy=policy, bundle_dir=args.bundle_dir, executable=args.executable)
    if args.ledger_file:
        ledger.save(args.ledger_file)
    print(json.dumps({"result": result,
                      "acceptance_evidence": acceptance_evidence(result, ledger)},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
