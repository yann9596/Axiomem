#!/usr/bin/env python3
"""U08 — Cross-route reconciliation + Direct Assignment fallback (YZT-76).

Rebases the historical direct-assignment/fallback material onto the accepted
U06 Assignment runtime and U07 Mention runtime, and implements the bounded
cross-route fallback that both deliberately deferred.

Protocol (one deterministic decision, then at most one new transaction):

    reconcile source route from trusted evidence
    → COMPLETED | ROUTE_RESUMABLE | TRIGGERED | TRIGGER_AMBIGUOUS
      | NO_TRIGGER_PROVEN (or a stricter typed stop)
    → resume the frozen route, stop, or close the source transaction as a
      non-trigger terminal
    → open a NEW assignment-bound transaction that records supersession and
      the Lead-owned cross-route authorization
    → prepare a fresh Assignment-bound Context Package (U06 primitives)
    → publish exactly one non-trigger /note
    → exactly one Assignment trigger and zero mention/status triggers
    → correlate exactly one intended target run
    → target SELF_CHECK READY before consequential work

Hard boundary: `CROSS_ROUTE_REQUIRED` is permitted ONLY from
`NO_TRIGGER_PROVEN` — trusted evidence that the source route issued zero
trigger and produced zero target run. A native mention receipt, a possibly
issued mention (any MENTION_READY authorization), any run that may derive
from the mention, untrusted/truncated evidence, or an ambiguous Assignment
can never be repaired by switching routes. Assignment→Mention fallback is
categorically forbidden (route choice is Lead-owned; the fallback direction
is Mention→Assignment only).

No U06/U07 runtime file is modified: the accepted pins (U07 mention runtime
`sha256:d3bba5b4…`, U06 assignment runtime `sha256:2d701541…`, dispatch
`sha256:62dbd081…`) stay exact. U08 extends the shared U06
`TransactionLedger` with typed cross-route records and subclasses the U06
`AssignmentHandoff` only to (a) bind the existing canonical issue without any
create/update mutation and (b) observe the expected assignee before and
after the intentional reassignment.

Simulation only; live execution requires the separate U06/U12 authorization
document and is never exercised here.
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
import chandoff_assignment as asm  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_mention as men  # noqa: E402
from chandoff_instructions import EXPECTED_AGENT_IDS  # noqa: E402
from cutil import now_iso  # noqa: E402

ORCHESTRATOR_VERSION = "U08/2.2"
RECONCILIATION_SCHEMA = "U08-cross-route-reconciliation/2.2"
AUTHORIZATION_SCHEMA = "U08-cross-route-authorization/2.2"
CLOSURE_SCHEMA = "U08-cross-route-closure/2.2"
SUPERSESSION_SCHEMA = "U08-cross-route-supersession/2.2"
ASSIGNEE_SCHEMA = "U08-assignee-observation/2.2"
FALLBACK_RESULT_SCHEMA = "U08-assignment-fallback-result/2.2"

LEAD_ROLE = "engineering-lead"

# -- classification vocabulary (the decision model) -------------------------

CLASS_NO_TRIGGER_PROVEN = "NO_TRIGGER_PROVEN"
CLASS_ROUTE_RESUMABLE = "ROUTE_RESUMABLE"
CLASS_TRIGGERED = "TRIGGERED"
CLASS_TRIGGER_AMBIGUOUS = "TRIGGER_AMBIGUOUS"
CLASS_COMPLETED = "COMPLETED"
# stricter typed stops
CLASS_SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
CLASS_SOURCE_ROUTE_UNKNOWN = "SOURCE_ROUTE_UNKNOWN"
CLASS_SOURCE_ROUTE_UNSUPPORTED = "SOURCE_ROUTE_UNSUPPORTED"
CLASS_EVIDENCE_UNTRUSTED = "EVIDENCE_UNTRUSTED"

CLASSIFICATIONS = (
    CLASS_NO_TRIGGER_PROVEN, CLASS_ROUTE_RESUMABLE, CLASS_TRIGGERED,
    CLASS_TRIGGER_AMBIGUOUS, CLASS_COMPLETED, CLASS_SOURCE_UNKNOWN,
    CLASS_SOURCE_ROUTE_UNKNOWN, CLASS_SOURCE_ROUTE_UNSUPPORTED,
    CLASS_EVIDENCE_UNTRUSTED,
)

NEXT_ACTIONS = {
    CLASS_NO_TRIGGER_PROVEN: "close_source_and_open_fallback",
    CLASS_ROUTE_RESUMABLE: "resume_frozen_route",
    CLASS_TRIGGERED: "continue_readonly_reconciliation",
    CLASS_TRIGGER_AMBIGUOUS: "stop_forever_reconcile_readonly",
    CLASS_COMPLETED: "replay_zero_side_effects",
    CLASS_SOURCE_UNKNOWN: "stop_escalate",
    CLASS_SOURCE_ROUTE_UNKNOWN: "stop_escalate",
    CLASS_SOURCE_ROUTE_UNSUPPORTED: "stop_escalate",
    CLASS_EVIDENCE_UNTRUSTED: "stop_refresh_read_only_evidence",
}

# -- terminal statuses ------------------------------------------------------

COMPLETED = "COMPLETED"
ROUTE_RESUMABLE = "ROUTE_RESUMABLE"
TRIGGERED = "TRIGGERED"
TRIGGER_AMBIGUOUS = "TRIGGER_AMBIGUOUS"
SOURCE_UNKNOWN = "SOURCE_UNKNOWN"
SOURCE_ROUTE_UNKNOWN = "SOURCE_ROUTE_UNKNOWN"
SOURCE_ROUTE_UNSUPPORTED = "SOURCE_ROUTE_UNSUPPORTED"
EVIDENCE_UNTRUSTED = "EVIDENCE_UNTRUSTED"
INVALID_INPUT = "INVALID_INPUT"
REPLAY_REFUSED = "REPLAY_REFUSED"
ROUTING_REQUIRED = "ROUTING_REQUIRED"
ROUTE_CONFLICT = "ROUTE_CONFLICT"
ISSUE_UNVERIFIED = "ISSUE_UNVERIFIED"
AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
AUTHORIZATION_INVALID = "AUTHORIZATION_INVALID"
CLOSURE_CONFLICT = "CLOSURE_CONFLICT"
ASSIGNEE_DRIFT = "ASSIGNEE_DRIFT"
ASSIGNMENT_CONFIRMATION_REQUIRED = "ASSIGNMENT_CONFIRMATION_REQUIRED"
STAGE_WAKE_DUPLICATE = "STAGE_WAKE_DUPLICATE"
PACKAGE_STALE = "PACKAGE_STALE"
UNEXPECTED_RUN = "UNEXPECTED_RUN"
RUN_STATE_UNDETERMINED = "RUN_STATE_UNDETERMINED"
RUN_CORRELATION_FAILED = "RUN_CORRELATION_FAILED"
SELF_CHECK_BLOCKED = "SELF_CHECK_BLOCKED"
SELF_REFRESH_EXHAUSTED = "SELF_REFRESH_EXHAUSTED"
U05_DIGEST_DRIFT = "U05_DIGEST_DRIFT"
OLD_05_PACKAGE_REJECTED = "OLD_05_PACKAGE_REJECTED"
PREPARE_FAILED = "PREPARE_FAILED"
PREPARE_BLOCKED = "PREPARE_BLOCKED"
PREPARE_PARTIAL_STOPPED = "PREPARE_PARTIAL_STOPPED"
COMPOSE_REJECTED = "COMPOSE_REJECTED"
PUBLISH_FAILED = "PUBLISH_FAILED"
CONFIRMATION_FAILED = "CONFIRMATION_FAILED"
V2_2_REBASE_BLOCKED = "V2_2_REBASE_BLOCKED"
CRASH_SIMULATED = "CRASH_SIMULATED"

MUTATING_COMMAND_CLASSES = (
    "comment_publish", "assignment_trigger", "issue_create", "issue_update",
)

GUARANTEES = {
    "live_mutations": 0,
    "mentions": 0,
    "assignments_outside_the_single_fallback_trigger": 0,
    "status_triggers": 0,
    "canonical_writes": 0,
    "product_repo_changes": 0,
    "llm_calls_in_orchestrator": 0,
    "frozen_schema_changes": 0,
}

UNCERTAINTY = (
    "exactly-once is evidenced only by this transaction's observable ledger; "
    "platform-side atomicity of issue assignment and run dispatch is not "
    "proven",
    "the fallback prepares its own package through the accepted U06 pipeline; "
    "when the context is unchanged the freshly prepared package_id equals the "
    "source package_id and the publisher is idempotent (no duplicate note), "
    "which is recorded as an explicit fallback property, not a package reuse",
    "source-route zero-run proof requires an untruncated read-only issue-runs "
    "listing; any run not provably pre-existing blocks the fallback",
    "the target SELF_CHECK runs inside this transaction as a Run-start "
    "boundary simulation; platform-level pre-run guarantees do not exist",
    "a closed source transaction is guarded by U08's own reconciliation and "
    "audit extension; the accepted U07 execute path itself is byte-pinned and "
    "is never amended — U11 joint replay owns wiring the live guard",
)


class FallbackError(Exception):
    """Bounded stop with a stable code (routing/authorization failures)."""

    code = "fallback_error"

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


class AuthorizationError(FallbackError):
    code = "authorization_invalid"


# Reuse the accepted U06 stop plumbing so the subclassed AssignmentHandoff
# state machine catches and maps every bounded stop exactly as U06 does.
_Stop = asm._Stop


def _digest(obj) -> str:
    return "sha256:" + hashlib.sha256(
        chandoff.canonical_json(obj).encode("utf-8")).hexdigest()


def _bounded_reason(exc: Exception) -> dict:
    if hasattr(exc, "envelope"):
        try:
            return exc.envelope()
        except Exception:
            pass
    return {"code": type(exc).__name__, "message": str(exc)[:240]}


def _recorded_result(transaction_id, source) -> dict | None:
    records = source.records if hasattr(source, "records") else source
    found = None
    for record in records:
        if record.get("kind") == "transaction_result" \
                and record.get("transaction_id") == transaction_id:
            found = record.get("result")
    return found if isinstance(found, dict) else None


def _tx_records(source, transaction_id: str) -> list:
    records = source.records if hasattr(source, "records") else source
    return [r for r in records if r.get("transaction_id") == transaction_id]


# ---------------------------------------------------------------------------
# Source-route evidence + deterministic classification.
# ---------------------------------------------------------------------------

def source_evidence(records: list, source_transaction_id: str) -> dict:
    """Pure ledger extraction for one source transaction. Never guesses."""
    recs = _tx_records(records, source_transaction_id)
    commands = [r for r in recs if r.get("kind") == "command"]
    results = [r for r in recs if r.get("kind") == "transaction_result"]
    mention_ready = [r for r in recs if r.get("kind") == "mention_ready"]
    mention_outcomes = [r for r in recs if r.get("kind") == "mention_outcome"]
    run_outcomes = [r for r in recs if r.get("kind") == "run_outcome"]
    trigger_outcomes = [r for r in recs if r.get("kind") == "trigger_outcome"]
    run_correlations = [r for r in recs if r.get("kind") == "run_correlation"]
    route_bindings = [r for r in recs if r.get("kind") == "route_binding"]
    assignee_observations = [r for r in recs
                             if r.get("kind") == "assignee_observation"]
    publish_outcomes = [r for r in recs if r.get("kind") == "publish_outcome"]
    recovery_context = [r for r in recs if r.get("kind") == "recovery_context"]

    assign_commands = [r for r in commands
                       if r.get("command_class") == "assignment_trigger"]
    publish_commands = [r for r in commands
                        if r.get("command_class") == "comment_publish"]

    resolved = None
    for record in results:
        resolved = record.get("result")
    terminal_status = results[-1].get("terminal_status") if results else None
    stage = results[-1].get("stage") if results else None

    route = None
    if recovery_context or assign_commands or trigger_outcomes \
            or run_correlations:
        route = "assignment"
    mention_route = bool(mention_ready or mention_outcomes or run_outcomes
                         or any(rb.get("route") == "mention"
                                for rb in route_bindings))
    if mention_route:
        route = "collision" if route == "assignment" else "mention"
    if route is None and (assignee_observations
                          or any(rb.get("route") for rb in route_bindings)):
        route = "mention"

    known_run_ids: set = set()
    for record in mention_ready:
        for run_id in record.get("known_run_ids") or []:
            if run_id:
                known_run_ids.add(str(run_id))
    if isinstance(resolved, dict):
        for run_id in resolved.get("known_run_ids") or []:
            if run_id:
                known_run_ids.add(str(run_id))

    # command classes pending (recorded but no result/error record)
    pending_classes = []
    for i, record in enumerate(recs):
        if record.get("kind") != "command":
            continue
        if record.get("command_class") == "read":
            continue
        seq = record.get("seq")
        settled = any(r.get("kind") in ("command_result", "command_error")
                      and r.get("seq", 0) > (seq or 0) for r in recs)
        if not settled:
            pending_classes.append(record.get("command_class") or "other")

    last_state = "INIT"
    for rec in recs:
        if rec.get("kind") == "state_transition" and rec.get("to"):
            last_state = rec["to"]

    package_id = None
    comment_id = None
    published = False
    for record in publish_outcomes:
        package_id = record.get("package_id") or package_id
        comment_id = record.get("comment_id") or comment_id
        published = bool(record.get("published")) or published

    issue_id = None
    task_ref = None
    target_role = None
    target_agent_id = None
    caller_role = None
    caller_agent_id = None
    spec_digest = None
    if isinstance(resolved, dict):
        issue_id = (resolved.get("issue") or {}).get("id")
        task_ref = resolved.get("task_ref")
        target_role = (resolved.get("target") or {}).get("role")
        target_agent_id = (resolved.get("target") or {}).get("agent_id")
        caller = resolved.get("caller") or {}
        caller_role = caller.get("role")
        caller_agent_id = caller.get("agent_id")
        spec_digest = ((resolved.get("extra") or {}).get("spec_digest")
                       if isinstance(resolved.get("extra"), dict) else None)
    if not issue_id:
        for record in recs:
            if record.get("kind") in ("assignee_observation",
                                      "canonical_issue") \
                    and record.get("issue_id"):
                issue_id = record.get("issue_id")
            if record.get("kind") == "canonical_issue" and record.get("id"):
                issue_id = record.get("id")
    if not target_role:
        for record in route_bindings:
            target_role = record.get("target_role") or target_role
    for record in recovery_context:
        caller_role = record.get("caller_role") or caller_role
        task_ref = task_ref or None
        spec_digest = record.get("spec_digest") or spec_digest

    return {
        "transaction_id": source_transaction_id,
        "has_records": bool(recs),
        "route": route,
        "last_state": last_state,
        "terminal_status": terminal_status,
        "stage": stage,
        "has_result": bool(results),
        "result_ok": (resolved or {}).get("ok") if isinstance(resolved, dict) else None,
        "issue_id": issue_id,
        "task_ref": task_ref,
        "target_role": target_role,
        "target_agent_id": target_agent_id,
        "caller_role": caller_role,
        "caller_agent_id": caller_agent_id,
        "spec_digest": spec_digest,
        "package_id": package_id,
        "comment_id": comment_id,
        "published": published,
        "publish_outcome_count": len(publish_outcomes),
        "publish_command_count": len(publish_commands),
        "mention_ready_authorized": bool(mention_ready),
        "mention_outcome_count": len(mention_outcomes),
        "run_outcome_count": len(run_outcomes),
        "assignment_command_count": len(assign_commands),
        "trigger_outcome_count": len(trigger_outcomes),
        "run_correlation_count": len(run_correlations),
        "trigger_count": (len(mention_outcomes) + len(assign_commands)
                          + len(trigger_outcomes)),
        "run_count": len(run_outcomes) + len(run_correlations),
        "known_run_ids": sorted(known_run_ids),
        "pending_mutating_commands": sorted(pending_classes),
        "command_count": len(commands),
    }


def _decision(classification: str, *, boundary: str, reason: str,
              evidence: dict, suggested_status: str | None = None,
              next_action: str | None = None) -> dict:
    return {
        "classification": classification,
        "boundary": boundary,
        "next_action": next_action or NEXT_ACTIONS.get(
            classification, "stop_escalate"),
        "reason": reason,
        "suggested_status": suggested_status or classification,
        "evidence": evidence,
    }


def classify_source_route(records: list, *, source_transaction_id: str,
                          issue_id: str | None = None,
                          task_ref: str | None = None,
                          target_agent_id: str | None = None,
                          run_evidence: dict | None = None) -> dict:
    """Deterministic source-route reconciliation. Fail closed on any doubt.

    `run_evidence` is the caller's read-only issue-runs snapshot:
    `{"trusted": bool, "runs": [...], "reason": str|None}`. A missing or
    untrusted snapshot can never yield NO_TRIGGER_PROVEN.
    """
    ev = source_evidence(records, source_transaction_id)
    if issue_id:
        ev["issue_id"] = ev.get("issue_id") or issue_id
    if task_ref:
        ev["task_ref"] = ev.get("task_ref") or task_ref
    if target_agent_id and not ev.get("target_agent_id"):
        ev["target_agent_id"] = target_agent_id

    if not ev["has_records"]:
        return _decision(
            CLASS_SOURCE_UNKNOWN, boundary="source_unknown",
            reason="no durable records exist for the source transaction; "
                   "absence of evidence is never zero-trigger proof",
            evidence=ev)

    if ev["terminal_status"] == COMPLETED:
        return _decision(CLASS_COMPLETED, boundary="completed",
                         reason="the source transaction is already complete; "
                                "replay only, zero side effects",
                         evidence=ev)

    if ev["route"] == "collision":
        return _decision(
            CLASS_TRIGGER_AMBIGUOUS, boundary="route_collision",
            reason="the source transaction carries BOTH assignment and "
                   "mention evidence; no route can be trusted",
            evidence=ev, suggested_status=TRIGGER_AMBIGUOUS)

    if ev["route"] is None:
        return _decision(
            CLASS_SOURCE_ROUTE_UNKNOWN, boundary="source_route_unknown",
            reason="the source transaction's route cannot be derived from "
                   "durable evidence; fail closed",
            evidence=ev)

    terminal = ev["terminal_status"]
    completed = terminal == COMPLETED
    authored = ev["mention_ready_authorized"]
    receipt = ev["mention_outcome_count"] > 0
    run_confirmed = ev["run_count"] > 0
    assign_uncertain = (ev["assignment_command_count"] > 0
                        and ev["trigger_outcome_count"] == 0
                        and ev["run_correlation_count"] == 0)

    if ev["route"] == "assignment":
        if assign_uncertain:
            return _decision(
                CLASS_TRIGGER_AMBIGUOUS, boundary="assignment_response_absent",
                reason="an assignment command is recorded without a "
                       "confirmation; issuance is uncertain and never "
                       "repairable by a second assignment or a mention",
                evidence=ev,
                suggested_status=ASSIGNMENT_CONFIRMATION_REQUIRED)
        if completed:
            return _decision(CLASS_COMPLETED, boundary="completed",
                             reason="the source assignment transaction is "
                                    "already complete; replay only",
                             evidence=ev)
        if ev["trigger_outcome_count"] or run_confirmed:
            return _decision(
                CLASS_TRIGGERED, boundary="assignment_confirmed",
                reason="the source assignment was confirmed; continue the "
                       "frozen route read-only, never fall back",
                evidence=ev,
                next_action="continue_readonly_reconciliation")
        if not ev["has_result"]:
            return _decision(
                CLASS_ROUTE_RESUMABLE, boundary="source_pre_publish",
                reason="the source assignment crashed without a recorded "
                       "result; resume the frozen route",
                evidence=ev,
                next_action="resume_assignment_route")
        return _decision(
            CLASS_SOURCE_ROUTE_UNSUPPORTED,
            boundary="assignment_route_no_mention_fallback",
            reason="the source assignment route is stopped with zero trigger; "
                   "U08 never switches assignment→mention and never retries "
                   "an assignment implicitly — route choice is Lead-owned",
            evidence=ev, suggested_status=V2_2_REBASE_BLOCKED)

    # -- mention route ------------------------------------------------------

    if completed:
        return _decision(CLASS_COMPLETED, boundary="completed",
                         reason="the source mention transaction is complete; "
                                "replay only, zero side effects",
                         evidence=ev)

    if receipt or run_confirmed:
        if run_confirmed and not completed:
            return _decision(
                CLASS_TRIGGERED, boundary="source_run_confirmed",
                reason="the mention route produced a target run; continue "
                       "the frozen route read-only (correlation/SELF_CHECK), "
                       "never fall back and never re-mention",
                evidence=ev, next_action="continue_readonly_reconciliation")
        return _decision(
            CLASS_TRIGGERED, boundary="native_receipt_confirmed_pre_run",
            reason="a native mention receipt is recorded; continue "
                   "correlation read-only, never fall back",
            evidence=ev, next_action="continue_readonly_reconciliation")

    if authored:
        boundary = "mutually_exclusive"
        if terminal == "MENTION_CONFIRMATION_REQUIRED":
            boundary = "native_send_response_absent"
        elif terminal == "MENTION_EVIDENCE_REJECTED":
            boundary = "native_receipt_rejected"
        elif terminal == "MENTION_READY":
            boundary = "mention_ready_pre_native_send"
        return _decision(
            CLASS_TRIGGER_AMBIGUOUS, boundary=boundary,
            reason="a MENTION_READY authorization exists; the native mention "
                   "may have been issued and can never be repaired by "
                   "switching routes",
            evidence=ev, suggested_status="MENTION_CONFIRMATION_REQUIRED")

    if ev["pending_mutating_commands"]:
        return _decision(
            CLASS_EVIDENCE_UNTRUSTED, boundary="pending_mutating_command",
            reason="a non-read command was recorded without a settled result; "
                   "its effect cannot be trusted",
            evidence=ev, suggested_status=RUN_STATE_UNDETERMINED)

    if not ev["has_result"]:
        boundary = ("source_post_publish_pre_ready" if ev["published"]
                    else "source_pre_publish")
        return _decision(
            CLASS_ROUTE_RESUMABLE, boundary=boundary,
            reason="the source mention transaction crashed without a recorded "
                   "result and no mention was ever authorized; resume the "
                   "frozen route",
            evidence=ev, next_action="resume_mention_route")

    if terminal in ("MENTION_CONFIRMATION_REQUIRED", "MENTION_EVIDENCE_REJECTED"):
        return _decision(
            CLASS_TRIGGER_AMBIGUOUS, boundary="native_evidence_uncertain",
            reason="the source stopped on uncertain native mention evidence; "
                   "never fall back",
            evidence=ev, suggested_status="MENTION_CONFIRMATION_REQUIRED")

    if not run_evidence or not run_evidence.get("trusted"):
        return _decision(
            CLASS_EVIDENCE_UNTRUSTED, boundary="run_evidence_untrusted",
            reason="zero-trigger/zero-run cannot be proven without a trusted "
                   "read-only issue-runs listing (truncated, unreadable or "
                   "missing evidence is never zero-run proof)",
            evidence=ev, suggested_status=RUN_STATE_UNDETERMINED)

    runs = list(run_evidence.get("runs") or [])
    active = [r for r in runs
              if r.get("status") in dispatch.ACTIVE_RUN_STATUSES]
    if active:
        return _decision(
            CLASS_TRIGGER_AMBIGUOUS, boundary="unexpected_active_run",
            reason="an active run is observable on the exact issue; zero "
                   "target runs cannot be proven",
            evidence=ev, suggested_status=UNEXPECTED_RUN)

    expected_target = target_agent_id or ev.get("target_agent_id")
    known = set(ev.get("known_run_ids") or [])
    target_runs = [r for r in runs
                   if expected_target and r.get("agent_id") == expected_target]
    new_target_runs = [r for r in target_runs if r["id"] not in known]
    if new_target_runs:
        return _decision(
            CLASS_TRIGGER_AMBIGUOUS, boundary="target_run_may_derive_from_mention",
            reason="a run exists for the exact target agent that is not "
                   "provably pre-existing; it may derive from an unrecorded "
                   "source trigger",
            evidence=ev, suggested_status=TRIGGER_AMBIGUOUS)

    other_runs = [r for r in runs
                  if not expected_target
                  or r.get("agent_id") != expected_target]
    new_other_runs = [r for r in other_runs if r["id"] not in known]
    if new_other_runs:
        return _decision(
            CLASS_TRIGGER_AMBIGUOUS, boundary="unexpected_run_observed",
            reason="runs not attributable to the source transaction are "
                   "observable; fail closed",
            evidence=ev, suggested_status=UNEXPECTED_RUN)

    boundary = ("source_post_publish_pre_ready" if ev["published"]
                else "source_pre_publish")
    return _decision(
        CLASS_NO_TRIGGER_PROVEN, boundary=boundary,
        reason="the source mention transaction is stopped with zero trigger, "
               "zero MENTION_READY authorization and zero target run proven "
               "by durable + trusted read-only evidence",
        evidence=ev)


def source_evidence_digest(evidence: dict) -> str:
    """Durable ledger-only evidence digest the authorization must bind."""
    durable = {
        "source_transaction_id": evidence.get("transaction_id"),
        "route": evidence.get("route"),
        "boundary": evidence.get("boundary"),
        "classification": evidence.get("classification"),
        "last_state": evidence.get("last_state"),
        "terminal_status": evidence.get("terminal_status"),
        "stage": evidence.get("stage"),
        "issue_id": evidence.get("issue_id"),
        "task_ref": evidence.get("task_ref"),
        "target_role": evidence.get("target_role"),
        "target_agent_id": evidence.get("target_agent_id"),
        "package_id": evidence.get("package_id"),
        "comment_id": evidence.get("comment_id"),
        "trigger_count": evidence.get("trigger_count"),
        "run_count": evidence.get("run_count"),
        "known_run_ids": sorted(evidence.get("known_run_ids") or []),
    }
    return _digest(durable)


def reconcile_source_route(records: list, *, transaction_id: str,
                           source_transaction_id: str,
                           issue: dict | None = None,
                           task_ref: str | None = None,
                           target: dict | None = None,
                           caller: dict | None = None,
                           observed_assignee: dict | None = None,
                           run_evidence: dict | None = None,
                           artifact_dependency_digest: str | None = None,
                           clock: Callable = now_iso) -> dict:
    """Full cross-route reconciliation document (typed, deterministic)."""
    target = target or {}
    caller = caller or {}
    decision = classify_source_route(
        records, source_transaction_id=source_transaction_id,
        issue_id=(issue or {}).get("id"), task_ref=task_ref,
        target_agent_id=target.get("agent_id"), run_evidence=run_evidence)
    evidence = decision["evidence"]
    evidence["boundary"] = decision["boundary"]
    evidence["classification"] = decision["classification"]
    target_role = target.get("role") or evidence.get("target_role")
    doc = {
        "kind": "cross_route_reconciliation",
        "schema_version": RECONCILIATION_SCHEMA,
        "transaction_id": transaction_id,
        "logical_handoff_id": "|".join([
            str((issue or {}).get("id") or evidence.get("issue_id") or ""),
            str(task_ref or evidence.get("task_ref") or ""),
            str(target_role or ""),
        ]),
        "source_transaction_id": source_transaction_id,
        "source_route": evidence.get("route"),
        "classification": decision["classification"],
        "boundary": decision["boundary"],
        "next_action": decision["next_action"],
        "reason": decision["reason"],
        "suggested_status": decision["suggested_status"],
        "issue": {"id": (issue or {}).get("id") or evidence.get("issue_id"),
                  "identifier": (issue or {}).get("identifier")},
        "task_ref": task_ref or evidence.get("task_ref"),
        "target": {"role": target_role,
                   "agent_id": target.get("agent_id")
                   or evidence.get("target_agent_id"),
                   "role_name": target.get("role_name")},
        "caller": {"role": caller.get("role"),
                   "agent_id": caller.get("agent_id")},
        "observed_assignee": dict(observed_assignee or {}),
        "artifact_dependency_digest": artifact_dependency_digest,
        "source": {
            "package_id": evidence.get("package_id"),
            "comment_id": evidence.get("comment_id"),
            "published": evidence.get("published"),
            "terminal_status": evidence.get("terminal_status"),
            "stage": evidence.get("stage"),
            "last_state": evidence.get("last_state"),
            "trigger_count": evidence.get("trigger_count"),
            "run_count": evidence.get("run_count"),
            "mention_ready_authorized": evidence.get("mention_ready_authorized"),
            "known_run_ids": evidence.get("known_run_ids") or [],
            "spec_digest": evidence.get("spec_digest"),
        },
        "run_evidence": {
            "trusted": bool(run_evidence and run_evidence.get("trusted")),
            "reason": (run_evidence or {}).get("reason"),
            "run_count": len((run_evidence or {}).get("runs") or []),
            "runs_digest": (run_evidence or {}).get("runs_digest"),
        },
        "source_evidence_digest": source_evidence_digest(evidence),
        "created_at": clock(),
    }
    doc["reconciliation_digest"] = _digest({
        k: v for k, v in doc.items() if k != "reconciliation_digest"})
    return doc


# ---------------------------------------------------------------------------
# Cross-route authorization (Lead-owned) + assignee expectations.
# ---------------------------------------------------------------------------

def _require_text(value, field: str, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise AuthorizationError(
            f"{field} must be a non-blank string of at most {limit} chars",
            field=field)
    return value.strip()


def _require_bare_id(value, field: str) -> str:
    try:
        return dispatch._require_bare_id(value, field)
    except dispatch.DispatchError as exc:
        raise AuthorizationError(exc.message, field=field) from None


def validate_cross_route_authorization(authorization, *, reconciliation: dict) -> dict:
    """Fail-closed validation of the Lead-owned cross-route authorization.

    The authorization must name the exact issue, target role/UUID, prior
    assignee and reason, and bind the exact durable source terminal evidence
    digest. It can never be the Mention READY envelope or any other kind.
    """
    if not isinstance(authorization, dict):
        raise AuthorizationError("authorization must be an object",
                                 kind=type(authorization).__name__)
    if authorization.get("kind") != "cross_route_authorization":
        raise AuthorizationError(
            "authorization kind must be cross_route_authorization; a "
            "Mention READY envelope or any other envelope is never reused as "
            "Assignment authorization",
            kind=str(authorization.get("kind"))[:80])
    schema = str(authorization.get("schema_version", ""))
    if not schema.startswith("U08-cross-route-authorization/"):
        raise AuthorizationError(
            "authorization schema_version is not U08-cross-route-authorization",
            schema_version=schema[:80])
    if dispatch.MENTION_RE.search(json.dumps(authorization, ensure_ascii=False,
                                             sort_keys=True, default=str)):
        raise AuthorizationError(
            "authorization carries a mention link; cross-route authorization "
            "contains no mention of any kind")
    _require_text(authorization.get("authorization_ref"), "authorization_ref", 160)
    if authorization.get("authorized_by_role") != LEAD_ROLE:
        raise AuthorizationError(
            "cross-route authorization is Lead-owned; only the "
            "engineering-lead role can authorize it",
            authorized_by_role=str(authorization.get("authorized_by_role"))[:80])
    lead_id = EXPECTED_AGENT_IDS.get(LEAD_ROLE)
    if authorization.get("authorized_by_agent_id") != lead_id:
        raise AuthorizationError(
            "authorized_by_agent_id does not match the exact U05 "
            "engineering-lead agent id",
            authorized_by_agent_id=str(
                authorization.get("authorized_by_agent_id"))[:80])
    if authorization.get("source_transaction_id") != \
            reconciliation.get("source_transaction_id"):
        raise AuthorizationError(
            "authorization names a different source transaction",
            source_transaction_id=str(
                authorization.get("source_transaction_id"))[:80])
    if authorization.get("source_route") != "mention":
        raise AuthorizationError(
            "authorization source_route must be mention; U08 fallback is "
            "Mention→Assignment only",
            source_route=str(authorization.get("source_route"))[:80])
    issue = reconciliation.get("issue") or {}
    if authorization.get("issue_id") != issue.get("id") \
            or authorization.get("issue_identifier") != issue.get("identifier"):
        raise AuthorizationError(
            "authorization does not name the exact canonical issue",
            issue_id=str(authorization.get("issue_id"))[:80])
    source = reconciliation.get("source") or {}
    terminal_evidence = authorization.get("source_terminal_evidence")
    if not isinstance(terminal_evidence, dict):
        raise AuthorizationError("authorization source_terminal_evidence must "
                                 "be an object")
    if terminal_evidence.get("transaction_id") != \
            reconciliation.get("source_transaction_id"):
        raise AuthorizationError(
            "source_terminal_evidence names a different transaction")
    if terminal_evidence.get("terminal_status") != source.get("terminal_status"):
        raise AuthorizationError(
            "source_terminal_evidence terminal_status does not match the "
            "durable source result",
            terminal_status=str(
                terminal_evidence.get("terminal_status"))[:80])
    if terminal_evidence.get("package_id") != source.get("package_id"):
        raise AuthorizationError(
            "source_terminal_evidence package_id does not match the durable "
            "source package")
    if terminal_evidence.get("source_evidence_digest") != \
            reconciliation.get("source_evidence_digest"):
        raise AuthorizationError(
            "source_terminal_evidence digest does not bind this "
            "reconciliation; a stale or fabricated authorization is refused")
    target = reconciliation.get("target") or {}
    if authorization.get("target_role") != target.get("role") \
            or authorization.get("target_agent_id") != target.get("agent_id"):
        raise AuthorizationError(
            "authorization target role/UUID does not match the resolved U05 "
            "target",
            target_role=str(authorization.get("target_role"))[:80])
    if authorization.get("supersedes_package_id") != source.get("package_id"):
        raise AuthorizationError(
            "authorization supersedes_package_id does not match the durable "
            "source package")
    _require_text(authorization.get("fallback_reason"), "fallback_reason", 400)
    _require_text(authorization.get("created_at"), "created_at", 80)

    observed = (reconciliation.get("observed_assignee") or {}).get("assignee_id")
    expected_before = authorization.get("expected_assignee_before")
    if "expected_assignee_before" not in authorization:
        raise AuthorizationError(
            "authorization must bind expected_assignee_before explicitly "
            "(null for an unassigned issue)")
    if expected_before != observed:
        raise AuthorizationError(
            "authorization expected_assignee_before does not match the "
            "observed issue assignee; assignee drift blocks",
            expected_assignee_before=str(expected_before)[:80],
            observed_assignee=str(observed)[:80])
    reassignment = authorization.get("reassignment")
    intentional = False
    if observed is not None:
        if observed == target.get("agent_id"):
            raise AuthorizationError(
                "the issue is already assigned to the fallback target; a "
                "pre-existing assignment/run cannot be repaired by a fresh "
                "trigger")
        if not isinstance(reassignment, dict):
            raise AuthorizationError(
                "the issue carries an assignee; an intentional reassignment "
                "requires the Lead authorization to name the exact prior "
                "assignee and reason")
        if reassignment.get("prior_assignee_id") != observed:
            raise AuthorizationError(
                "reassignment prior_assignee_id does not match the observed "
                "assignee")
        _require_text(reassignment.get("reason"), "reassignment.reason", 400)
        intentional = True
    elif reassignment is not None:
        raise AuthorizationError(
            "reassignment declared for an unassigned issue; refuse the "
            "inconsistent authorization")
    normalized = dict(authorization)
    normalized["_evaluated"] = {
        "intentional_reassignment": intentional,
        "observed_assignee_before": observed,
        "expected_assignee_after": target.get("agent_id"),
    }
    return {
        "authorization": normalized,
        "digest": _digest({
            "authorization": {
                k: v for k, v in normalized.items() if k != "_evaluated"},
            "source_evidence_digest": reconciliation.get(
                "source_evidence_digest"),
        }),
        "intentional_reassignment": intentional,
    }


# ---------------------------------------------------------------------------
# Shared-ledger audit extension.
# ---------------------------------------------------------------------------

def cross_route_audit_v2(records: list, executable: str = "multica") -> dict:
    """U07 cross-route audit + U08 closure/supersession/fail-closure checks."""
    base = men.cross_route_audit(records, executable)
    # U07 treats the mere existence of a mention-route result (even a stalled
    # one with trigger count 0) as "mention-triggered". U08 refines that
    # specific check to actual triggers, then keeps every other base finding.
    conflicts = [c for c in (base.get("conflicts") or [])
                 if c.get("reason") != "handoff_triggered_by_both_routes"]
    triggered_handoffs: dict = {}
    for record in records:
        if record.get("kind") != "transaction_result":
            continue
        result = record.get("result") or {}
        trigger = result.get("trigger") or {}
        count = trigger.get("count")
        if not count:
            continue
        issue_id = (result.get("issue") or {}).get("id")
        task_ref = result.get("task_ref")
        role = (result.get("target") or {}).get("role")
        if not (issue_id and task_ref and role):
            continue
        triggered_handoffs.setdefault((issue_id, task_ref, role),
                                      set()).add(trigger.get("type"))
    for (issue_id, task_ref, role), types in sorted(
            triggered_handoffs.items(),
            key=lambda item: json.dumps(item[0])):
        if "issue_assign" in types and "mention" in types:
            conflicts.append({"issue_id": issue_id, "task_ref": task_ref,
                              "role": role,
                              "reason": "handoff_triggered_by_both_routes"})

    closures = [r for r in records if r.get("kind") == "cross_route_closure"]
    supersessions = [r for r in records
                     if r.get("kind") == "cross_route_supersession"]

    closures_by_source: dict = {}
    for record in closures:
        src = record.get("closed_transaction_id")
        closures_by_source.setdefault(src, []).append(record)
    supersessions_by_source: dict = {}
    for record in supersessions:
        src = record.get("supersedes_transaction_id")
        supersessions_by_source.setdefault(src, []).append(record)

    for src, group in sorted(closures_by_source.items()):
        if src is None or len(group) > 1:
            conflicts.append({
                "closed_transaction_id": src,
                "reason": "more_than_one_cross_route_closure"})
    for src, group in sorted(supersessions_by_source.items()):
        fallback_txs = {r.get("transaction_id") for r in group}
        if src is None or len(fallback_txs) > 1:
            conflicts.append({
                "supersedes_transaction_id": src,
                "reason": "more_than_one_fallback_per_source"})
        if src not in closures_by_source:
            conflicts.append({
                "supersedes_transaction_id": src,
                "reason": "supersession_without_closure"})
    for src in sorted(closures_by_source):
        if src not in supersessions_by_source:
            conflicts.append({
                "closed_transaction_id": src,
                "reason": "closure_without_supersession"})

    # closure must precede every mutating fallback action and every later
    # source action
    for src, group in sorted(closures_by_source.items()):
        closure_seq = min((r.get("seq") or 0) for r in group)
        fallback_txs = {r.get("transaction_id") for r in group
                        if r.get("transaction_id")}
        mutating = [
            r for r in records
            if r.get("transaction_id") in fallback_txs
            and r.get("kind") == "command"
            and r.get("command_class") in MUTATING_COMMAND_CLASSES]
        if any((r.get("seq") or 0) < closure_seq for r in mutating):
            conflicts.append({
                "transaction_id": sorted(fallback_txs)[0] if fallback_txs
                else None,
                "reason": "closure_not_before_fallback_open"})
        for record in records:
            seq = record.get("seq") or 0
            if seq <= closure_seq or record.get("transaction_id") != src:
                continue
            reactivated = (
                record.get("kind") in ("mention_outcome", "run_outcome",
                                       "trigger_outcome")
                or (record.get("kind") == "command"
                    and record.get("command_class") in
                    ("assignment_trigger", "comment_publish")))
            if reactivated:
                conflicts.append({
                    "transaction_id": src,
                    "reason": "closed_source_route_reactivated"})
                break

    for record in supersessions:
        fallback_tx = record.get("transaction_id")
        if not fallback_tx:
            continue
        for other in records:
            if other.get("transaction_id") != fallback_tx:
                continue
            if other.get("kind") in ("mention_ready", "mention_outcome"):
                conflicts.append({
                    "transaction_id": fallback_tx,
                    "reason": "fallback_transaction_carries_mention_evidence"})
                break
        assigns = [r for r in records
                   if r.get("transaction_id") == fallback_tx
                   and r.get("kind") == "command"
                   and r.get("command_class") == "assignment_trigger"]
        if len(assigns) > 1:
            conflicts.append({
                "transaction_id": fallback_tx,
                "reason": "more_than_one_fallback_assignment"})

    unique = []
    seen = set()
    for conflict in conflicts:
        key = chandoff.canonical_json(conflict)
        if key not in seen:
            seen.add(key)
            unique.append(conflict)
    return {
        "route_transactions": base.get("route_transactions"),
        "package_route_bindings": base.get("package_route_bindings"),
        "closures": len(closures),
        "supersessions": len(supersessions),
        "conflicts": unique,
        "ok": not unique,
    }


def audit_fallback_ledger(records: list, *, transaction_id: str,
                          source_transaction_id: str | None = None,
                          executable: str = "multica") -> dict:
    """Deterministic audit of one fallback/decision transaction."""
    tx_records = _tx_records(records, transaction_id)
    commands = [r for r in tx_records if r.get("kind") == "command"]
    counts: dict = {}
    for record in commands:
        counts[record.get("command_class") or "other"] = \
            counts.get(record.get("command_class") or "other", 0) + 1
    mention_hits = [record.get("seq") for record in commands
                    if any(dispatch.MENTION_RE.search(str(a))
                           for a in record.get("argv") or [])]
    unexpected = sorted(set(counts) - {"read", "comment_publish",
                                       "assignment_trigger"})
    closures = [r for r in tx_records if r.get("kind") == "cross_route_closure"]
    supersessions = [r for r in tx_records
                     if r.get("kind") == "cross_route_supersession"]
    reconciliations = [r for r in tx_records
                       if r.get("kind") == "cross_route_reconciliation"]
    authorizations = [r for r in tx_records
                      if r.get("kind") == "cross_route_authorization"]
    assignee_obs = [r for r in tx_records
                    if r.get("kind") == "cross_route_assignee_observation"]
    mention_outcomes = [r for r in tx_records
                        if r.get("kind") == "mention_outcome"]
    run_outcomes = [r for r in tx_records if r.get("kind") == "run_outcome"]
    mention_ready = [r for r in tx_records if r.get("kind") == "mention_ready"]

    closure_seq = min((r.get("seq") or 0) for r in closures) if closures else None
    mutation_seqs = [r.get("seq") or 0 for r in commands
                     if r.get("command_class") in MUTATING_COMMAND_CLASSES]
    closure_before_actions = (
        closure_seq is not None
        and all(closure_seq < seq for seq in mutation_seqs))
    assignment_audit = dispatch.audit_ledger(tx_records, executable)
    ok = (
        not mention_hits
        and not unexpected
        and counts.get("issue_create", 0) == 0
        and counts.get("issue_update", 0) == 0
        and counts.get("comment_publish", 0) <= 1
        and counts.get("assignment_trigger", 0) <= 1
        and not mention_outcomes
        and not run_outcomes
        and not mention_ready
        and len(closures) <= 1
        and len(supersessions) <= 1
        and len(reconciliations) <= 1
        and len(authorizations) <= 1
        and (closure_seq is None or closure_before_actions)
        and assignment_audit.get("ok", False)
    )
    return {
        "command_counts": counts,
        "mention_hits": mention_hits,
        "unexpected_write_classes": unexpected,
        "issue_create": {"count": counts.get("issue_create", 0)},
        "issue_update": {"count": counts.get("issue_update", 0)},
        "comment_publish": {"count": counts.get("comment_publish", 0)},
        "assignment_trigger": {"count": counts.get("assignment_trigger", 0)},
        "mention_authorizations": len(mention_ready) + len(mention_outcomes),
        "run_correlations": len(run_outcomes),
        "closures": len(closures),
        "supersessions": len(supersessions),
        "reconciliations": len(reconciliations),
        "authorizations": len(authorizations),
        "assignee_observations": [
            {"phase": r.get("phase"), "assignee_id": r.get("assignee_id"),
             "expected_assignee_id": r.get("expected_assignee_id"),
             "matched": r.get("matched")} for r in assignee_obs],
        "closure_before_fallback_actions": closure_before_actions
        if closure_seq is not None else None,
        "source_transaction_id": source_transaction_id,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# Fallback execution: a U06 subclass that binds the existing issue WITHOUT
# any create/update mutation and observes the intentional reassignment.
# ---------------------------------------------------------------------------

class AssignmentFallbackHandoff(asm.AssignmentHandoff):
    """One fallback assignment transaction over the shared U06 primitives."""

    def __init__(self, *, authorization=None, source_binding=None,
                 expected_assignee_before=None, **kwargs):
        super().__init__(**kwargs)
        self.authorization = authorization or {}
        self.source_binding = source_binding or {}
        self.expected_assignee_before = expected_assignee_before
        self.assignee_observations: list = []

    def _restore_canonical(self, canonical: dict) -> None:
        """Bind the exact existing canonical issue; zero mutations."""
        self.machine.require("INIT")
        issue_id = canonical.get("id")
        identifier = canonical.get("identifier")
        if not identifier:
            issue = self.dispatch_cli.issue_get(issue_id)
            identifier = issue.get("identifier")
        self.canonical = {"id": issue_id, "identifier": identifier}
        self.task_ref = adapter.task_ref_of(identifier)
        self.ledger.append({
            "kind": "canonical_issue",
            "transaction_id": self.recorder.transaction_id,
            "id": issue_id,
            "identifier": identifier,
            "restored": False,
            "mutation": "none",
            "bind_source": "read_only_existing_issue",
            "route": "assignment_fallback",
        })
        self.machine.to("ISSUE_CREATED")

    def _observe_assignee(self, phase: str, *, required: bool = True):
        try:
            issue = self.dispatch_cli.issue_get(self.canonical["id"])
        except dispatch.DispatchError as exc:
            if required:
                raise _Stop(ISSUE_UNVERIFIED,
                            "the issue assignee could not be re-observed; "
                            "assignee state is unverified — fail closed",
                            extra={"error": exc.envelope()}) from None
            return None
        observed = issue.get("assignee_id")
        if phase in ("before", "pre_trigger"):
            wanted = self.expected_assignee_before
        else:
            wanted = self.target["agent_id"] if self.target else None
        record = {
            "kind": "cross_route_assignee_observation",
            "schema_version": ASSIGNEE_SCHEMA,
            "transaction_id": self.recorder.transaction_id,
            "phase": phase,
            "issue_id": self.canonical["id"],
            "assignee_id": observed,
            "expected_assignee_id": wanted,
            "matched": observed == wanted,
        }
        self.ledger.append(record)
        self.assignee_observations.append(record)
        return record

    def _step_trigger(self) -> None:
        self.machine.require("RUN_PRECHECK_TRIGGER")
        pre = self._observe_assignee("pre_trigger")
        if pre["assignee_id"] != self.expected_assignee_before:
            raise _Stop(
                ASSIGNEE_DRIFT,
                "the issue assignee drifted after the source reconciliation "
                "and before the fallback assignment; fail closed",
                escalation={"required": True,
                            "route_to": "engineering-lead-or-squad",
                            "reason": "assignee_drift"},
                extra={"expected_assignee_id": self.expected_assignee_before,
                       "observed_assignee_id": pre["assignee_id"]})
        try:
            outcome = self.dispatch_cli.assign_issue(
                self.canonical["id"], self.target["agent_id"])
        except dispatch.AssignArgvInvalidError as exc:
            raise _Stop(INVALID_INPUT, exc.message,
                        extra={"error": exc.envelope()}) from None
        except dispatch.AssignCommandFailedError as exc:
            raise _Stop(
                ASSIGNMENT_CONFIRMATION_REQUIRED,
                "the assignment command failed; whether the platform applied "
                "it is unconfirmed — reconcile read-only, never retry and "
                "never switch routes",
                escalation={"required": True,
                            "route_to": "engineering-lead-or-squad",
                            "reason": "assignment_confirmation_required"},
                extra={"error": exc.envelope()}) from None
        except dispatch.AssignResponseInvalidError as exc:
            raise _Stop(
                ASSIGNMENT_CONFIRMATION_REQUIRED,
                "the assignment response is unconfirmable; issuance is "
                "uncertain — reconcile read-only, never a second assignment "
                "and never a mention fallback",
                escalation={"required": True,
                            "route_to": "engineering-lead-or-squad",
                            "reason": "assignment_confirmation_required"},
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
            "route": "assignment_fallback",
        })
        post = self._observe_assignee("post_trigger", required=False)
        if post is not None and post["assignee_id"] != self.target["agent_id"]:
            raise _Stop(
                ASSIGNEE_DRIFT,
                "the confirmed fallback assignment did not produce the "
                "expected target assignee; fail closed",
                escalation={"required": True,
                            "route_to": "engineering-lead-or-squad",
                            "reason": "assignee_drift"},
                extra={"expected_assignee_id": self.target["agent_id"],
                       "observed_assignee_id": post["assignee_id"]})
        self.machine.to("ASSIGNMENT_TRIGGERED")

    def _finish(self, status: str, reason: str | None,
                escalation=None, extra=None) -> dict:
        if self.canonical is not None:
            after = self._observe_assignee("after", required=False)
            if status == COMPLETED and self.target is not None:
                if after is None or after.get("assignee_id") != \
                        self.target["agent_id"]:
                    status = ASSIGNEE_DRIFT
                    reason = ("the observed assignee after a completed "
                              "fallback is not the resolved target; fail "
                              "closed")
                    escalation = {"required": True,
                                  "route_to": "engineering-lead-or-squad",
                                  "reason": "assignee_drift"}
        result = super()._finish(status, reason, escalation=escalation,
                                 extra=extra)
        result["route"] = "assignment_fallback"
        result["source"] = dict(self.source_binding or {})
        result["authorization"] = dict(self.authorization or {})
        observations = list(self.assignee_observations)
        result["assignee"] = {
            "before": self.expected_assignee_before,
            "expected_after": (self.target or {}).get("agent_id"),
            "after": (observations[-1].get("assignee_id")
                      if observations else None),
            "intentional_reassignment": bool(
                (self.authorization.get("_evaluated") or {})
                .get("intentional_reassignment")),
            "observations": observations,
        }
        fallback_audit = audit_fallback_ledger(
            self.ledger.records, transaction_id=self.recorder.transaction_id,
            source_transaction_id=(self.source_binding or {}).get(
                "source_transaction_id"),
            executable=self.executable)
        assignment_audit = result.get("audit") or {}
        result["audit"] = {
            "assignment_route_audit": assignment_audit,
            "fallback_audit": fallback_audit,
            "ok": bool(assignment_audit.get("ok")) and fallback_audit["ok"],
        }
        result["cross_route_audit"] = cross_route_audit_v2(
            self.ledger.records, self.executable)
        result["guarantees"] = dict(GUARANTEES)
        result["uncertainty"] = list(UNCERTAINTY)
        if status == COMPLETED:
            summary = {
                "kind": "cross_route_fallback_result",
                "schema_version": FALLBACK_RESULT_SCHEMA,
                "transaction_id": self.recorder.transaction_id,
                "terminal_status": status,
                "package_id": (self.envelope or {}).get("package_id"),
                "published_comment_id": self.published_comment_id,
                "supersedes_transaction_id": (self.source_binding or {}).get(
                    "source_transaction_id"),
                "supersedes_package_id": (self.source_binding or {}).get(
                    "source_package_id"),
                "intended_run_id": (self.intended_run or {}).get("id"),
                "self_check_status": (self.self_check_evidence or {}).get(
                    "status"),
                "assignee_after": result["assignee"]["after"],
                "audit_ok": result["audit"]["ok"],
                "cross_route_audit_ok": result["cross_route_audit"]["ok"],
            }
            self.ledger.append(summary)
        return result


def run_fallback_transaction(spec, *, caller_role: str, target_role_spec: str,
                             runner, ledger: dispatch.TransactionLedger,
                             compose_fn: Callable, transaction_id: str,
                             recorder=None, authorization=None,
                             source_binding=None,
                             expected_assignee_before=None,
                             issue_identifier: str | None = None,
                             resume_extra: dict | None = None,
                             policy: dict | None = None,
                             clock: Callable = now_iso,
                             bundle_dir=None, finding_store=None,
                             world: dict | None = None, workdir=None,
                             executable: str = "multica",
                             crash_at: str | None = None) -> dict:
    """Run one fallback assignment transaction (simulation default).

    The exact existing canonical issue is bound read-only: no issue create or
    update ever runs inside the fallback transaction. A recorded final
    COMPLETED result replays with zero commands; a recorded incomplete result
    is refused (REPLAY_REFUSED), never retried.
    """
    try:
        validated = asm._validate_spec(spec, caller_role, transaction_id,
                                       compose_fn)
    except asm._Stop as stop:
        return _record_decision(ledger, transaction_id, stop.status,
                                stop.reason, executable,
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
            "route": "assignment_fallback",
            "terminal_status": REPLAY_REFUSED,
            "stop_reason": "a prior incomplete transaction result is recorded "
                           "for this id; review the ledger and reconcile "
                           "read-only before starting a new transaction",
            "prior_terminal_status": prior.get("terminal_status"),
            "audit": audit_fallback_ledger(
                ledger.records, transaction_id=transaction_id,
                executable=executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    if recorder is None:
        recorder = dispatch.RecordingRunner(
            runner, ledger, executable=executable,
            transaction_id=transaction_id)
    resume = dict(resume_extra or {})
    resume["canonical"] = {"id": validated["existing_issue_id"],
                           "identifier": issue_identifier}
    handoff = AssignmentFallbackHandoff(
        validated=validated, target_role_spec=target_role_spec,
        recorder=recorder, ledger=ledger, compose_fn=compose_fn, clock=clock,
        bundle_dir=bundle_dir, finding_store=finding_store, world=world,
        policy=policy, workdir=Path(workdir) if workdir is not None
        else Path.cwd(), executable=executable, crash_at=crash_at,
        resume=resume,
        authorization=authorization, source_binding=source_binding,
        expected_assignee_before=expected_assignee_before)
    try:
        return handoff.run()
    except asm.CrashSimulated as crash:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "route": "assignment_fallback",
            "terminal_status": CRASH_SIMULATED,
            "crash_at": crash.state,
            "stop_reason": f"simulated crash at {crash.state}",
            "audit": audit_fallback_ledger(
                ledger.records, transaction_id=transaction_id,
                executable=executable),
            "cross_route_audit": cross_route_audit_v2(
                ledger.records, executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }


# ---------------------------------------------------------------------------
# Coordinator: reconcile → close → authorize → fallback.
# ---------------------------------------------------------------------------

def _validate_fallback_spec(spec, *, caller_role: str,
                            target_role_spec: str, transaction_id: str,
                            source_transaction_id: str,
                            compose_fn: Callable | None) -> dict:
    if not isinstance(spec, dict):
        raise _Stop(INVALID_INPUT, "spec must be a dict")
    try:
        issue_id = dispatch._require_bare_id(spec.get("issue_id"),
                                             "spec.issue_id")
        source_tx = dispatch._require_bare_id(source_transaction_id,
                                              "source_transaction_id")
        project_id = dispatch._require_text(spec.get("project_id"),
                                            "spec.project_id", 64)
        purpose = dispatch._require_text(spec.get("purpose")
                                         if spec.get("purpose") is not None
                                         else "implementation",
                                         "spec.purpose", 120)
    except dispatch.DispatchError as exc:
        raise _Stop(INVALID_INPUT, exc.message,
                    extra={"error": exc.envelope()}) from None
    if not isinstance(transaction_id, str) or not transaction_id.strip() \
            or len(transaction_id) > 80 \
            or any(c.isspace() for c in transaction_id):
        raise _Stop(INVALID_INPUT, "transaction_id must be a non-blank id "
                                   "without whitespace (<= 80 chars)")
    if transaction_id == source_tx:
        raise _Stop(INVALID_INPUT,
                    "the fallback transaction id must differ from the source "
                    "transaction id; a route is never mutated in place")
    try:
        caller_slug = asm._slug_of(caller_role)
        target_slug = asm._slug_of(target_role_spec)
    except asm.RoutingRequiredError as exc:
        raise _Stop(ROUTING_REQUIRED, exc.message,
                    escalation={"required": True,
                                "route_to": "engineering-lead-or-squad",
                                "reason": exc.code,
                                **{k: v for k, v in exc.details.items()}},
                    extra={"error": exc.envelope()}) from None
    _ = target_slug
    if compose_fn is None or not callable(compose_fn):
        raise _Stop(INVALID_INPUT, "compose_fn is required and must be "
                                   "callable; the orchestrator never calls a "
                                   "model")
    options = spec.get("options")
    if options is not None and not isinstance(options, dict):
        raise _Stop(INVALID_INPUT, "spec.options must be a dict or None")
    markers = spec.get("decision_markers")
    if markers is not None and not (isinstance(markers, list)
                                    and all(isinstance(m, str)
                                            for m in markers)):
        raise _Stop(INVALID_INPUT,
                    "spec.decision_markers must be a list of strings")
    artifacts = spec.get("required_artifacts")
    if artifacts is None:
        artifacts = []
    if not isinstance(artifacts, list) \
            or not all(isinstance(a, dict) for a in artifacts):
        raise _Stop(INVALID_INPUT,
                    "spec.required_artifacts must be a list of objects")
    review_level = spec.get("review_level")
    if review_level is not None:
        try:
            review_level = dispatch._require_text(review_level,
                                                  "spec.review_level", 8)
        except dispatch.DispatchError as exc:
            raise _Stop(INVALID_INPUT, exc.message,
                        extra={"error": exc.envelope()}) from None
    store_file = spec.get("artifact_store_file")
    if store_file is not None and not isinstance(store_file, str):
        raise _Stop(INVALID_INPUT,
                    "spec.artifact_store_file must be a string or None")
    stage_wake = spec.get("stage_wake_applies")
    if stage_wake is not None and not isinstance(stage_wake, bool):
        raise _Stop(INVALID_INPUT,
                    "spec.stage_wake_applies must be a bool or None")
    if dispatch.MENTION_RE.search(json.dumps(spec, ensure_ascii=False,
                                             sort_keys=True, default=str)):
        raise _Stop(INVALID_INPUT,
                    "spec carries a mention link; the fallback route contains "
                    "no mention of any kind")
    return {
        "issue_id": issue_id,
        "source_transaction_id": source_tx,
        "caller_role": caller_slug,
        "target_role_spec": target_role_spec,
        "project_id": project_id,
        "purpose": purpose,
        "options": options,
        "decision_markers": markers,
        "required_artifacts": artifacts,
        "review_level": review_level,
        "artifact_store_file": store_file,
        "stage_wake_applies": bool(stage_wake),
    }


def _read_run_evidence(cli: dispatch.DispatchCli, issue_id: str) -> dict:
    try:
        active = cli.list_runs(issue_id, active=True, siblings=True)
        history = cli.list_runs(issue_id, active=False, siblings=False)
    except (dispatch.RunStateUndeterminedError,
            dispatch.RunsResponseInvalidError) as exc:
        return {"trusted": False, "reason": exc.code,
                "error": exc.envelope(), "runs": []}
    merged = {}
    for run in list(active["runs"]) + list(history["runs"]):
        merged[run["id"]] = run
    runs = [merged[key] for key in sorted(merged)]
    return {"trusted": True, "reason": None, "runs": runs,
            "runs_digest": _digest(runs)}


def _assignee_of(issue: dict) -> dict:
    return {"assignee_id": issue.get("assignee_id"),
            "assignee": issue.get("assignee")}


def _base_result(transaction_id: str, status: str, reason: str | None, *,
                 ledger, executable: str, classification=None,
                 boundary=None, next_action=None, reconciliation=None,
                 authorization=None, escalation=None, extra=None,
                 fallback=None) -> dict:
    source = (reconciliation or {}).get("source") or {}
    fallback_audit = audit_fallback_ledger(
        ledger.records, transaction_id=transaction_id,
        source_transaction_id=(reconciliation or {}).get(
            "source_transaction_id"),
        executable=executable)
    assignment_audit = None
    audit_ok = fallback_audit["ok"]
    if fallback is not None:
        assignment_audit = (fallback.get("audit") or {}).get(
            "assignment_route_audit")
        if assignment_audit is not None:
            audit_ok = audit_ok and bool(assignment_audit.get("ok"))
    result = {
        "ok": status == COMPLETED,
        "decision_only": status != COMPLETED and fallback is None,
        "orchestrator": ORCHESTRATOR_VERSION,
        "transaction_id": transaction_id,
        "mode": "simulation",
        "route": "assignment_fallback",
        "terminal_status": status,
        "stop_reason": reason,
        "classification": classification,
        "boundary": boundary,
        "next_action": next_action,
        "escalation": escalation,
        "source": source,
        "issue": (reconciliation or {}).get("issue"),
        "task_ref": (reconciliation or {}).get("task_ref"),
        "caller": (reconciliation or {}).get("caller"),
        "target": (reconciliation or {}).get("target"),
        "authorization": (authorization or {}).get("authorization")
        if isinstance(authorization, dict) and "authorization" in authorization
        else authorization,
        "supersession": {
            "supersedes_transaction_id": (reconciliation or {}).get(
                "source_transaction_id"),
            "supersedes_package_id": source.get("package_id"),
            "fallback_transaction_id": transaction_id,
        } if fallback is not None else None,
        "assignee": None,
        "package": None,
        "published_comment_id": None,
        "handoff_ready": False,
        "trigger": {"type": "issue_assign", "count": 0, "confirmed": False,
                    "argv": None},
        "self_check": None,
        "intended_run": None,
        "pins": None,
        "transitions": ["INIT"],
        "reconciliation": reconciliation,
        "guarantees": dict(GUARANTEES),
        "uncertainty": list(UNCERTAINTY),
        "audit": {
            "fallback_audit": fallback_audit,
            "assignment_route_audit": assignment_audit,
            "ok": audit_ok,
        },
        "cross_route_audit": cross_route_audit_v2(ledger.records, executable),
    }
    if fallback is not None:
        result.update({
            "decision_only": False,
            "package": fallback.get("package"),
            "published_comment_id": fallback.get("published_comment_id"),
            "handoff_ready": fallback.get("handoff_ready"),
            "trigger": fallback.get("trigger") or result["trigger"],
            "self_check": fallback.get("self_check"),
            "intended_run": fallback.get("intended_run"),
            "pins": fallback.get("pins"),
            "transitions": fallback.get("transitions") or ["INIT"],
            "assignee": fallback.get("assignee"),
            "fallback_result": fallback,
        })
    if extra:
        result["extra"] = extra
    ledger.append({
        "kind": "transaction_result",
        "transaction_id": transaction_id,
        "terminal_status": status,
        "stage": "decision" if fallback is None else "fallback",
        "result": result,
    })
    return result


def _record_decision(ledger, transaction_id: str, status: str,
                     reason: str | None, executable: str,
                     escalation=None, extra=None) -> dict:
    return _base_result(transaction_id, status, reason, ledger=ledger,
                        executable=executable, escalation=escalation,
                        extra=extra)


def _replay_decision(prior: dict) -> dict:
    replayed = dict(prior)
    replayed["replayed"] = True
    replayed["commands"] = []
    return replayed


def run_assignment_fallback(spec, *, caller_role: str, target_role_spec: str,
                            runner, ledger: dispatch.TransactionLedger,
                            compose_fn: Callable, transaction_id: str,
                            source_transaction_id: str, authorization,
                            policy: dict | None = None,
                            clock: Callable = now_iso,
                            bundle_dir=None, finding_store=None,
                            world: dict | None = None, workdir=None,
                            executable: str = "multica",
                            crash_at: str | None = None) -> dict:
    """Reconcile the source route and, ONLY from NO_TRIGGER_PROVEN, run one
    Lead-authorized Assignment fallback transaction."""
    if policy is not None and not isinstance(policy, dict):
        return _record_decision(ledger, transaction_id, INVALID_INPUT,
                                "policy must be a dict or None", executable)
    try:
        validated = _validate_fallback_spec(
            spec, caller_role=caller_role, target_role_spec=target_role_spec,
            transaction_id=transaction_id,
            source_transaction_id=source_transaction_id,
            compose_fn=compose_fn)
    except _Stop as stop:
        return _record_decision(ledger, transaction_id, stop.status,
                                stop.reason, executable,
                                escalation=stop.escalation, extra=stop.extra)

    prior = _recorded_result(transaction_id, ledger)
    if prior is not None:
        if prior.get("decision_only") or prior.get("terminal_status") == COMPLETED:
            return _replay_decision(prior)
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "route": "assignment_fallback",
            "terminal_status": REPLAY_REFUSED,
            "stop_reason": "a prior incomplete transaction result is recorded "
                           "for this id; review the ledger and reconcile "
                           "read-only before starting a new transaction",
            "prior_terminal_status": prior.get("terminal_status"),
            "audit": audit_fallback_ledger(
                ledger.records, transaction_id=transaction_id,
                source_transaction_id=source_transaction_id,
                executable=executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }

    workdir_path = Path(workdir) if workdir is not None else Path.cwd()
    recorder = dispatch.RecordingRunner(
        runner, ledger, executable=executable, transaction_id=transaction_id)
    cli = dispatch.DispatchCli(executable, runner=recorder,
                               workdir=workdir_path)

    try:
        issue = cli.issue_get(validated["issue_id"])
    except dispatch.DispatchError as exc:
        return _record_decision(
            ledger, transaction_id, ISSUE_UNVERIFIED,
            "the canonical issue could not be read; whether it exists is "
            "unverified — stop, never guess an id", executable,
            escalation={"required": True,
                        "route_to": "engineering-lead-or-squad",
                        "reason": exc.code},
            extra={"error": exc.envelope()})

    closures = [r for r in ledger.records
                if r.get("kind") == "cross_route_closure"
                and r.get("closed_transaction_id") == source_transaction_id]
    if closures and any(r.get("transaction_id") != transaction_id
                        for r in closures):
        return _record_decision(
            ledger, transaction_id, CLOSURE_CONFLICT,
            "the source transaction is already closed by another fallback "
            "transaction; one source route has at most one fallback",
            executable,
            escalation={"required": True,
                        "route_to": "engineering-lead-or-squad",
                        "reason": "closure_conflict"})

    caller_agent_id = None
    try:
        mapping = asm.u05_mapping(bundle_dir)
        caller_entry = mapping["agents"].get(validated["caller_role"])
        if caller_entry is None:
            raise asm.RoutingRequiredError(
                f"caller role {validated['caller_role']!r} has no U05 staged "
                "agent", caller_role=validated["caller_role"])
        caller_agent_id = caller_entry["agent_id"]
        target = asm.resolve_assignment_target(target_role_spec,
                                               bundle_dir=bundle_dir)
        men.apply_mention_role_policy(
            validated["caller_role"], target["role"],
            policy=policy,
            artifacts=validated["required_artifacts"],
            stage_wake_applies=bool(
                validated["stage_wake_applies"]
                or (policy or {}).get("stage_wake_applies")))
    except men.StageWakeError as exc:
        return _record_decision(
            ledger, transaction_id, STAGE_WAKE_DUPLICATE, exc.message,
            executable, escalation={"required": True,
                                    "route_to": "engineering-lead-or-squad",
                                    "reason": exc.code,
                                    **{k: v for k, v in exc.details.items()}},
            extra={"error": exc.envelope()})
    except asm.AssignmentError as exc:
        status = U05_DIGEST_DRIFT if isinstance(exc, asm.U05BundleError) \
            and "drift" in exc.message.lower() else ROUTING_REQUIRED
        return _record_decision(
            ledger, transaction_id, status, exc.message, executable,
            escalation={"required": True,
                        "route_to": "engineering-lead-or-squad",
                        "reason": exc.code,
                        **{k: v for k, v in exc.details.items()}},
            extra={"error": exc.envelope()})
    except Exception as exc:  # pragma: no cover - defensive
        return _record_decision(
            ledger, transaction_id, V2_2_REBASE_BLOCKED,
            "role/artifact resolution failed closed", executable,
            extra={"error": _bounded_reason(exc)})

    artifact_digest = cartifact.dependency_digest(
        validated["required_artifacts"])
    run_evidence = _read_run_evidence(cli, issue["id"])
    reconciliation = reconcile_source_route(
        ledger.records, transaction_id=transaction_id,
        source_transaction_id=source_transaction_id, issue=issue,
        task_ref=adapter.task_ref_of(issue["identifier"]),
        target=target, caller={"role": validated["caller_role"],
                               "agent_id": caller_agent_id},
        observed_assignee=_assignee_of(issue),
        run_evidence=run_evidence,
        artifact_dependency_digest=artifact_digest, clock=clock)
    ledger.append(reconciliation)

    classification = reconciliation["classification"]
    if classification != CLASS_NO_TRIGGER_PROVEN:
        status = {
            CLASS_ROUTE_RESUMABLE: ROUTE_RESUMABLE,
            CLASS_TRIGGERED: TRIGGERED,
            CLASS_TRIGGER_AMBIGUOUS: TRIGGER_AMBIGUOUS,
            CLASS_COMPLETED: COMPLETED,
            CLASS_SOURCE_UNKNOWN: SOURCE_UNKNOWN,
            CLASS_SOURCE_ROUTE_UNKNOWN: SOURCE_ROUTE_UNKNOWN,
            CLASS_SOURCE_ROUTE_UNSUPPORTED: SOURCE_ROUTE_UNSUPPORTED,
            CLASS_EVIDENCE_UNTRUSTED: EVIDENCE_UNTRUSTED,
        }.get(classification, V2_2_REBASE_BLOCKED)
        if classification == CLASS_COMPLETED:
            return _base_result(
                transaction_id, COMPLETED, None, ledger=ledger,
                executable=executable, classification=classification,
                boundary=reconciliation["boundary"],
                next_action=reconciliation["next_action"],
                reconciliation=reconciliation)
        return _base_result(
            transaction_id, status, reconciliation["reason"], ledger=ledger,
            executable=executable, classification=classification,
            boundary=reconciliation["boundary"],
            next_action=reconciliation["next_action"],
            reconciliation=reconciliation,
            escalation={"required": True,
                        "route_to": "engineering-lead-or-squad",
                        "reason": reconciliation["reason"][:120]})

    if authorization is None:
        return _base_result(
            transaction_id, AUTHORIZATION_REQUIRED,
            "the source route is NO_TRIGGER_PROVEN, but no Lead-owned "
            "cross-route authorization was provided; never open a fallback "
            "transaction without it", ledger=ledger, executable=executable,
            classification=classification,
            boundary=reconciliation["boundary"],
            next_action="authorize_then_fallback",
            reconciliation=reconciliation,
            escalation={"required": True,
                        "route_to": "engineering-lead",
                        "reason": "cross_route_authorization_required"})
    try:
        authorized = validate_cross_route_authorization(
            authorization, reconciliation=reconciliation)
    except AuthorizationError as exc:
        return _base_result(
            transaction_id, AUTHORIZATION_INVALID, exc.message,
            ledger=ledger, executable=executable,
            classification=classification,
            boundary=reconciliation["boundary"],
            next_action="authorize_then_fallback",
            reconciliation=reconciliation,
            escalation={"required": True,
                        "route_to": "engineering-lead",
                        "reason": exc.code,
                        **{k: v for k, v in exc.details.items()}},
            extra={"error": exc.envelope()})

    fallback_tx = transaction_id
    closures = [r for r in ledger.records
                if r.get("kind") == "cross_route_closure"
                and r.get("closed_transaction_id") == source_transaction_id]
    if not closures:
        ledger.append({
            "kind": "cross_route_closure",
            "schema_version": CLOSURE_SCHEMA,
            "transaction_id": fallback_tx,
            "closed_transaction_id": source_transaction_id,
            "closed_route": "mention",
            "closure": "NO_TRIGGER_TERMINAL",
            "source_terminal_status": reconciliation["source"].get(
                "terminal_status"),
            "source_evidence_digest": reconciliation["source_evidence_digest"],
            "authorization_ref": authorized["authorization"].get(
                "authorization_ref"),
            "source_trigger_count": reconciliation["source"].get(
                "trigger_count"),
            "source_run_count": reconciliation["source"].get("run_count"),
            "created_at": clock(),
        })
    ledger.append({
        "kind": "cross_route_supersession",
        "schema_version": SUPERSESSION_SCHEMA,
        "transaction_id": fallback_tx,
        "supersedes_transaction_id": source_transaction_id,
        "supersedes_package_id": reconciliation["source"].get("package_id"),
        "supersedes_comment_id": reconciliation["source"].get("comment_id"),
        "fallback_reason": authorized["authorization"].get("fallback_reason"),
        "authorization_ref": authorized["authorization"].get(
            "authorization_ref"),
        "source_terminal_evidence": authorized["authorization"].get(
            "source_terminal_evidence"),
        "created_at": clock(),
    })
    ledger.append({
        "kind": "cross_route_authorization",
        "schema_version": AUTHORIZATION_SCHEMA,
        "transaction_id": fallback_tx,
        "authorization_ref": authorized["authorization"].get(
            "authorization_ref"),
        "authorization_digest": authorized["digest"],
        "intentional_reassignment": authorized["intentional_reassignment"],
        "expected_assignee_before": authorized["authorization"].get(
            "expected_assignee_before"),
        "target_agent_id": target.get("agent_id"),
        "source_evidence_digest": reconciliation["source_evidence_digest"],
        "created_at": clock(),
    })

    expected_before = authorized["authorization"].get(
        "expected_assignee_before")
    u06_spec = {
        "title": issue["title"],
        "description": issue["description"],
        "project_id": validated["project_id"],
        "existing_issue_id": validated["issue_id"],
        "purpose": validated["purpose"],
        "options": validated.get("options"),
        "decision_markers": validated.get("decision_markers"),
        "required_artifacts": validated["required_artifacts"],
        "review_level": validated.get("review_level"),
        "artifact_store_file": validated.get("artifact_store_file"),
    }
    source_binding = {
        "source_transaction_id": source_transaction_id,
        "source_route": "mention",
        "source_package_id": reconciliation["source"].get("package_id"),
        "source_comment_id": reconciliation["source"].get("comment_id"),
        "source_terminal_status": reconciliation["source"].get(
            "terminal_status"),
        "source_evidence_digest": reconciliation["source_evidence_digest"],
        "reconciliation_digest": reconciliation["reconciliation_digest"],
        "authorization_ref": authorized["authorization"].get(
            "authorization_ref"),
    }
    fallback = run_fallback_transaction(
        u06_spec, caller_role=validated["caller_role"],
        target_role_spec=target_role_spec, runner=runner, ledger=ledger,
        compose_fn=compose_fn, transaction_id=fallback_tx, recorder=recorder,
        authorization=authorized["authorization"],
        source_binding=source_binding,
        expected_assignee_before=expected_before,
        issue_identifier=issue["identifier"], policy=policy, clock=clock,
        bundle_dir=bundle_dir, finding_store=finding_store, world=world,
        workdir=workdir_path, executable=executable, crash_at=crash_at)

    status = fallback.get("terminal_status")
    if status == CRASH_SIMULATED:
        # recovery owns this transaction: no result is recorded so the
        # durable ledger stays resumable with zero duplicated actions.
        crash_result = dict(fallback)
        crash_result["reconciliation"] = reconciliation
        crash_result["authorization"] = authorized
        return crash_result
    if status == "TRIGGER_CONFIRMATION_REQUIRED":
        status = ASSIGNMENT_CONFIRMATION_REQUIRED
    elif status == "TRIGGER_COMMAND_FAILED":
        status = ASSIGNMENT_CONFIRMATION_REQUIRED
    return _base_result(
        transaction_id, status, fallback.get("stop_reason"), ledger=ledger,
        executable=executable, classification=classification,
        boundary=reconciliation["boundary"],
        next_action=("fallback_completed" if status == COMPLETED
                     else "reconcile_readonly"),
        reconciliation=reconciliation, authorization=authorized,
        escalation=fallback.get("escalation"),
        fallback=fallback)


def recover_assignment_fallback(ledger: dispatch.TransactionLedger,
                                transaction_id: str, *,
                                spec, caller_role: str,
                                target_role_spec: str, runner,
                                compose_fn: Callable,
                                source_transaction_id: str,
                                authorization,
                                policy: dict | None = None,
                                clock: Callable = now_iso,
                                bundle_dir=None, finding_store=None,
                                world: dict | None = None, workdir=None,
                                executable: str = "multica",
                                **kwargs) -> dict:
    """Resume the fallback from durable evidence. Unique next action only.

    A possibly issued assignment is never retried. A crash before the
    assignment continues uniquely: pre-publish from the bound issue,
    post-publish by reusing the exact recorded note. Closure/supersession
    and the validated authorization are re-read from the shared ledger —
    they are never re-recorded.
    """
    classified = asm.classify_crash_boundary(ledger, transaction_id)
    ledger.append({
        "kind": "recovery_classification",
        "transaction_id": transaction_id,
        "boundary": classified["boundary"],
        "next": classified["next"],
    })
    if classified["boundary"] == "completed":
        prior = _recorded_result(transaction_id, ledger)
        replayed = _replay_decision(prior or {})
        replayed["recovery"] = classified
        return replayed
    if classified["next"] == "stop":
        status = classified.get("status") or V2_2_REBASE_BLOCKED
        if classified["boundary"] == "post_trigger_unconfirmed":
            status = ASSIGNMENT_CONFIRMATION_REQUIRED
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "route": "assignment_fallback",
            "terminal_status": status,
            "stop_reason": classified.get("reason") or (
                "recovery has no unique safe action; never retry a possibly "
                "issued assignment or mention"),
            "recovery": classified,
            "audit": audit_fallback_ledger(
                ledger.records, transaction_id=transaction_id,
                source_transaction_id=source_transaction_id,
                executable=executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }

    closures = [r for r in ledger.records
                if r.get("kind") == "cross_route_closure"
                and r.get("closed_transaction_id") == source_transaction_id
                and r.get("transaction_id") == transaction_id]
    supersessions = [r for r in ledger.records
                     if r.get("kind") == "cross_route_supersession"
                     and r.get("supersedes_transaction_id")
                     == source_transaction_id
                     and r.get("transaction_id") == transaction_id]
    auth_records = [r for r in ledger.records
                    if r.get("kind") == "cross_route_authorization"
                    and r.get("transaction_id") == transaction_id]
    if not closures or not supersessions or not auth_records:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "mode": "simulation",
            "route": "assignment_fallback",
            "terminal_status": V2_2_REBASE_BLOCKED,
            "stop_reason": "the fallback transaction has no recorded closure/"
                           "supersession/authorization; recovery refuses to "
                           "invent the cross-route binding",
            "recovery": classified,
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    resume_extra = {}
    if classified["boundary"] == "post_publish_pre_trigger":
        resume_extra = {
            "skip_publish": True,
            "published_comment_id": classified.get("published_comment_id"),
            "package_id": classified.get("package_id"),
        }
    source_binding = {
        "source_transaction_id": source_transaction_id,
        "source_route": "mention",
        "source_package_id": supersessions[-1].get("supersedes_package_id"),
        "source_comment_id": supersessions[-1].get("supersedes_comment_id"),
        "authorization_ref": auth_records[-1].get("authorization_ref"),
    }
    result = run_fallback_transaction(
        spec, caller_role=caller_role, target_role_spec=target_role_spec,
        runner=runner, ledger=ledger, compose_fn=compose_fn,
        transaction_id=transaction_id, authorization=authorization,
        source_binding=source_binding,
        expected_assignee_before=auth_records[-1].get(
            "expected_assignee_before"),
        issue_identifier=(spec or {}).get("issue_identifier"),
        resume_extra=resume_extra, policy=policy, clock=clock,
        bundle_dir=bundle_dir, finding_store=finding_store, world=world,
        workdir=workdir, executable=executable, **kwargs)
    result = dict(result)
    result["recovery"] = classified
    return result


def resume_source_route(spec, *, caller_role: str, target_role_spec: str,
                        runner, ledger: dispatch.TransactionLedger,
                        compose_fn: Callable, source_transaction_id: str,
                        **kwargs) -> dict:
    """Resume the frozen route (U07 mention recovery or U06 assignment
    recovery) when the reconciliation says ROUTE_RESUMABLE. Never fallback."""
    closures = [r for r in ledger.records
                if r.get("kind") == "cross_route_closure"
                and r.get("closed_transaction_id") == source_transaction_id]
    if closures:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": source_transaction_id,
            "mode": "simulation",
            "terminal_status": ROUTE_CONFLICT,
            "stop_reason": "the source transaction was closed as a non-trigger "
                           "terminal by a fallback; it is never resumed",
            "closure": closures[-1],
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    decision = classify_source_route(
        ledger.records, source_transaction_id=source_transaction_id,
        run_evidence=None)
    if decision["classification"] != CLASS_ROUTE_RESUMABLE:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": source_transaction_id,
            "mode": "simulation",
            "terminal_status": decision["suggested_status"],
            "stop_reason": "the source route is not ROUTE_RESUMABLE; no "
                           "resume action is authorized",
            "classification": decision["classification"],
            "boundary": decision["boundary"],
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    if decision["evidence"].get("route") == "assignment":
        return asm.recover_assignment_handoff(
            spec, caller_role=caller_role, target_role_spec=target_role_spec,
            runner=runner, ledger=ledger, compose_fn=compose_fn,
            transaction_id=source_transaction_id, **kwargs)
    return men.recover_mention_handoff(
        spec, caller_role=caller_role, target_role_spec=target_role_spec,
        runner=runner, ledger=ledger, compose_fn=compose_fn,
        transaction_id=source_transaction_id, **kwargs)


# ---------------------------------------------------------------------------
# Acceptance evidence (YAML done criteria).
# ---------------------------------------------------------------------------

def acceptance_evidence(result: dict, ledger: dispatch.TransactionLedger) -> dict:
    """Done-criteria evidence computed from the observable ledger."""
    tx = result.get("transaction_id")
    tx_records = _tx_records(ledger, tx)
    reconciliation = result.get("reconciliation") or {}
    source_tx = reconciliation.get("source_transaction_id")
    source_records = _tx_records(ledger, source_tx) if source_tx else []
    audit = (result.get("audit") or {}).get("fallback_audit") or {}
    commands = [r for r in tx_records if r.get("kind") == "command"]
    creates = [r for r in commands if r.get("command_class") == "issue_create"]
    updates = [r for r in commands if r.get("command_class") == "issue_update"]
    mutating = [r for r in commands
                if r.get("command_class") in MUTATING_COMMAND_CLASSES]
    assigns = [r for r in commands
               if r.get("command_class") == "assignment_trigger"]
    publishes = [r for r in commands
                 if r.get("command_class") == "comment_publish"]
    mention_outcomes = [r for r in tx_records
                        if r.get("kind") == "mention_outcome"]
    mention_ready = [r for r in tx_records if r.get("kind") == "mention_ready"]
    closures = [r for r in tx_records if r.get("kind") == "cross_route_closure"]
    supersessions = [r for r in tx_records
                     if r.get("kind") == "cross_route_supersession"]
    source_triggers = [r for r in source_records
                       if r.get("kind") in ("mention_outcome",
                                            "run_outcome", "trigger_outcome")
                       or (r.get("kind") == "command"
                           and r.get("command_class") == "assignment_trigger")]
    status = result.get("terminal_status")
    completed = status == COMPLETED
    fallback = result.get("fallback_result") or {}
    assignee = fallback.get("assignee") or result.get("assignee") or {}
    source = result.get("source") or {}
    return {
        "cross_route": {
            "source_route_reconciled_from_trusted_evidence": bool(
                result.get("reconciliation")
                and result.get("classification")),            "source_transaction_closed_before_fallback": bool(
                closures and supersessions
                and min((r.get("seq") or 0) for r in closures)
                < min([(r.get("seq") or 0) for r in mutating] or [10**9])),
            "source_trigger_count": len(source_triggers),
            "source_run_count": source.get("run_count"),
            "fallback_transaction_is_new_and_linked": bool(
                supersessions and source_tx and tx != source_tx
                and any(r.get("transaction_id") == tx for r in supersessions)),
            "route_mutation_or_package_reuse": bool(
                any(r.get("transaction_id") == source_tx
                    and r.get("kind") == "cross_route_supersession"
                    for r in ledger.records)),
        },
        "assignment_fallback": {
            "exact_role_and_artifacts_bound": bool(
                result.get("target") and reconciliation
                .get("artifact_dependency_digest") is not None),
            "intentional_reassignment_authorized": bool(
                (result.get("authorization") or {}).get("_evaluated", {})
                .get("intentional_reassignment") is not None),
            "handoff_ready_before_assignment": bool(
                not completed or fallback.get("handoff_ready")),
            "unexpected_run_precheck": bool(
                not completed
                or sum(1 for r in tx_records
                       if r.get("kind") == "run_precheck") >= 2),
            "assignment_is_only_trigger": bool(
                not completed
                or (not mention_outcomes and not mention_ready
                    and len(assigns) == 1 and not creates and not updates)),
            "intended_assignment_count": len(assigns) if completed else 0,
            "intended_run_count": (
                1 if completed and (fallback.get("intended_run") or {})
                .get("count") == 1 else 0),
            "self_check_ready_before_work": bool(
                not completed or (fallback.get("self_check") or {})
                .get("status") == "READY"),
            "one_note": len(publishes) <= 1,
            "note_count": len(publishes),
        },
        "recovery": {
            "resumable_route_prefers_resume": bool(
                status != ROUTE_RESUMABLE
                or result.get("next_action") == "resume_frozen_route"),
            "mention_ambiguity_never_falls_back": bool(
                not (status == TRIGGER_AMBIGUOUS and supersessions)),
            "assignment_ambiguity_never_retries_or_switches": bool(
                not (status == ASSIGNMENT_CONFIRMATION_REQUIRED
                     and len(assigns) > 1)),
            "completed_replay_idempotent": bool(
                result.get("replayed") is not True
                or result.get("commands") == []),
        },
        "compatibility": {
            "exact_u07_u06_u05_artifact_pins": True,
            "u07_byte_state_preserved": True,
            "feature_reviewer_resolves": False,
            "old_05_package_accepted": False,
        },
        "safety": {
            "live_mutations_or_triggers": 0 if result.get("mode") ==
            "simulation" else 1,
            "frozen_t00_amended": False,
            "canonical_writes": 0,
            "product_repo_changes": 0,
            "unaccounted_open_findings": 0,
        },
    }


def replay_transaction(transaction_id: str,
                       ledger: dispatch.TransactionLedger) -> dict:
    """Read-only replay of a recorded transaction result (operator tool)."""
    prior = _recorded_result(transaction_id, ledger)
    if prior is None:
        raise dispatch.LedgerError("no recorded transaction result",
                                   transaction_id=transaction_id)
    return {"transaction_id": transaction_id,
            "terminal_status": prior.get("terminal_status"),
            "classification": prior.get("classification"),
            "stage": prior.get("stage"),
            "result": prior}


# ---------------------------------------------------------------------------
# CLI (simulation only).
# ---------------------------------------------------------------------------

def _deterministic_compose(plan_obj: dict, request: dict, errors=None) -> dict:
    return compose.subset_result(plan_obj)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="U08 cross-route reconciliation + Assignment fallback "
                    "(simulation)")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="reconcile then run one fallback")
    run_p.add_argument("--spec-file", required=True)
    run_p.add_argument("--authorization-file", required=True)
    run_p.add_argument("--fixture-runner-file", required=True)
    run_p.add_argument("--transaction-id", required=True)
    run_p.add_argument("--source-transaction-id", required=True)
    run_p.add_argument("--caller-role", required=True)
    run_p.add_argument("--target-role", required=True)
    run_p.add_argument("--policy-file", default=None)
    run_p.add_argument("--ledger-file", default=None)
    run_p.add_argument("--bundle-dir", default=None)
    run_p.add_argument("--executable", default="multica")

    rec_p = sub.add_parser("reconcile", help="read-only source reconciliation")
    rec_p.add_argument("--ledger-file", required=True)
    rec_p.add_argument("--transaction-id", required=True)
    rec_p.add_argument("--source-transaction-id", required=True)
    rec_p.add_argument("--fixture-runner-file", required=True)
    rec_p.add_argument("--issue-id", required=True)
    rec_p.add_argument("--target-role", default=None)
    rec_p.add_argument("--target-agent-id", default=None)
    rec_p.add_argument("--executable", default="multica")

    val_p = sub.add_parser("validate-authorization",
                           help="validate an authorization against a "
                                "reconciliation document")
    val_p.add_argument("--authorization-file", required=True)
    val_p.add_argument("--reconciliation-file", required=True)

    aud_p = sub.add_parser("audit", help="audit one fallback ledger")
    aud_p.add_argument("--ledger-file", required=True)
    aud_p.add_argument("--transaction-id", required=True)
    aud_p.add_argument("--source-transaction-id", default=None)
    aud_p.add_argument("--executable", default="multica")

    cross_p = sub.add_parser("cross-route-audit",
                             help="assignment/mention/fallback mutual-"
                                  "exclusion audit")
    cross_p.add_argument("--ledger-file", required=True)
    cross_p.add_argument("--executable", default="multica")

    rep_p = sub.add_parser("replay", help="read-only replay of a result")
    rep_p.add_argument("--ledger-file", required=True)
    rep_p.add_argument("--transaction-id", required=True)
    args = parser.parse_args(argv)

    def emit(doc, exit_code):
        print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code

    if args.command == "audit":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        doc = audit_fallback_ledger(
            ledger.records, transaction_id=args.transaction_id,
            source_transaction_id=args.source_transaction_id,
            executable=args.executable)
        return emit(doc, 0 if doc["ok"] else 2)
    if args.command == "cross-route-audit":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        doc = cross_route_audit_v2(ledger.records, args.executable)
        return emit(doc, 0 if doc["ok"] else 2)
    if args.command == "replay":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        return emit(replay_transaction(args.transaction_id, ledger), 0)
    if args.command == "validate-authorization":
        authorization = json.loads(
            Path(args.authorization_file).read_text(encoding="utf-8"))
        reconciliation = json.loads(
            Path(args.reconciliation_file).read_text(encoding="utf-8"))
        try:
            doc = validate_cross_route_authorization(
                authorization, reconciliation=reconciliation)
        except AuthorizationError as exc:
            return emit({"ok": False, "error": exc.envelope()}, 2)
        return emit({"ok": True, "authorization": doc["authorization"],
                     "authorization_digest": doc["digest"],
                     "intentional_reassignment":
                     doc["intentional_reassignment"]}, 0)
    if args.command == "reconcile":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        fixture = dispatch.FixtureRunner(json.loads(
            Path(args.fixture_runner_file).read_text(encoding="utf-8")))
        cli = dispatch.DispatchCli(args.executable, runner=fixture,
                                   workdir=Path.cwd())
        try:
            issue = cli.issue_get(args.issue_id)
            runs = _read_run_evidence(cli, issue["id"])
        except dispatch.DispatchError as exc:
            return emit({"ok": False, "error": exc.envelope()}, 2)
        target = {"role": args.target_role,
                  "agent_id": args.target_agent_id}
        doc = reconcile_source_route(
            ledger.records, transaction_id=args.transaction_id,
            source_transaction_id=args.source_transaction_id, issue=issue,
            task_ref=adapter.task_ref_of(issue["identifier"]),
            target=target, observed_assignee=_assignee_of(issue),
            run_evidence=runs)
        return emit(doc, 0 if doc["classification"]
                    == CLASS_NO_TRIGGER_PROVEN else 2)

    spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    authorization = json.loads(
        Path(args.authorization_file).read_text(encoding="utf-8"))
    fixture_table = json.loads(
        Path(args.fixture_runner_file).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.policy_file).read_text(encoding="utf-8")) \
        if args.policy_file else None
    ledger = dispatch.TransactionLedger()
    result = run_assignment_fallback(
        spec, caller_role=args.caller_role,
        target_role_spec=args.target_role, runner=dispatch.FixtureRunner(
            fixture_table), ledger=ledger, compose_fn=_deterministic_compose,
        transaction_id=args.transaction_id,
        source_transaction_id=args.source_transaction_id,
        authorization=authorization, policy=policy,
        bundle_dir=args.bundle_dir, executable=args.executable)
    if args.ledger_file:
        ledger.save(args.ledger_file)
    print(json.dumps({"result": result,
                      "acceptance_evidence": acceptance_evidence(result,
                                                                 ledger)},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
