#!/usr/bin/env python3
"""T10 — Mention Handoff path orchestrator (YZT-65).

Implements the frozen strict order (impl doc §35.2) as a staged, fail-closed
state machine over the accepted T00–T09 boundaries. It is the MENTION route —
mutually exclusive with the T09 assignment route:

    1. bind an EXISTING canonical issue   (read-only issue get; no issue is
                                           ever created, no id is guessed)
    2. resolve target role                (constrained to the T08 baseline)
    3. PREPARE_HANDOFF                    (T05 snapshot -> T01 PLAN -> caller
                                           semantic compose -> T02/T03 finalize)
    4. publish non-trigger note           (T06, explicit authorization policy)
    5. confirm HANDOFF_READY              (T06 discovery re-resolution with
                                           exact issue/task/role/package/comment
                                           binding)
    6. emit MENTION_READY                 (bounded auditable envelope for the
                                           CURRENT agent; stage terminal)
    7. accept native mention evidence     (injected; proves ONE target-agent
                                           mention on the exact issue by the
                                           expected current agent, with the
                                           expected target id and no
                                           assignment mutation)
    8. correlate the target run           (injected; ONE run traceable to the
                                           single accepted mention)
    9. target SELF_CHECK                  (T04; bounded refresh, then work gate)

Boundary this module keeps (§35.4 and §22):

- the adapter NEVER constructs mention markdown, never turns plain text
  `@Name` into a trigger, and never issues the triggering comment itself:
  the real mention belongs to the current agent's native Multica reply
  surface. The MENTION_READY envelope only AUTHORIZES one native mention;
- one route per transaction: the mention route rejects any `issue assign`
  argv, any assignment trigger evidence, and any package/handoff already
  bound to the assignment route (and vice versa, via the shared cross-route
  audit). The Issue assignee must stay byte/ID-equivalent: it is observed
  before and re-observed after every path, and any observable mutation fails
  the transaction closed (ASSIGNMENT_MUTATION_DETECTED);
- every Multica argv flows through `chandoff_dispatch.DispatchCli` /
  `chandoff_note.NoteCli` / `chandoff_adapter.MulticaCli` allowlists and one
  shared `TransactionLedger` — no parallel ledger is created;
- the frozen T00–T03 schemas and policies are reused verbatim; the semantic
  compose step is INJECTED (the orchestrator never calls a model);
- PARTIAL stops by default; continuation requires an explicit caller policy
  decision recorded in the ledger with every gap kept visible;
- an ambiguous native mention result (missing/unparseable evidence, or
  evidence that cannot prove a clean single mention) becomes
  MENTION_CONFIRMATION_REQUIRED and never authorizes a retry — a second
  native mention would duplicate the trigger. Invalid evidence (wrong
  target/author/issue, multiple mentions, plain text `@Name`, fabricated
  link text, adapter-constructed mention, assignment+mention) fails closed
  to MENTION_EVIDENCE_REJECTED. Zero/duplicate/wrong-target/ambiguous run
  evidence fails closed to RUN_CORRELATION_FAILED; a second mention is never
  recommended;
- exactly-once is claimed ONLY against this observable ledger evidence;
  platform-side mention/run atomicity is explicitly not claimed (carried as
  uncertainty for T12). Replaying a recorded final transaction re-issues
  nothing; a recorded incomplete transaction is refused, not retried;
- simulation is the default and the only mode exercised by YZT-65; live
  activation is T13-owned. No live comment add, agent mention, assignment/
  status write or run trigger is performed by this task.
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
import chandoff_assignment as asm  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_note as note  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402
from cutil import now_iso  # noqa: E402

ORCHESTRATOR_VERSION = "T10/1.0"
MENTION_READY_SCHEMA = "T10-mention-ready/1.0"
MENTION_EVIDENCE_SCHEMA = "T10-mention-evidence/1.0"
RUN_EVIDENCE_SCHEMA = "T10-run-evidence/1.0"
MAX_SELF_CHECK_ATTEMPTS = 2
MAX_COMPOSE_ATTEMPTS = 2

PHASE_READY = "ready"
PHASE_EXECUTE = "execute"
PHASE_FULL = "full"
NATIVE_SURFACE = "native_agent_reply"
MENTION_SOURCE = "mention"

STATES = (
    "INIT", "ISSUE_BOUND", "ROUTE_FROZEN", "TARGET_RESOLVED",
    "HANDOFF_PREPARED", "HANDOFF_PUBLISHED", "HANDOFF_READY_CONFIRMED",
    "MENTION_READY", "MENTION_EVIDENCE_ACCEPTED", "TARGET_RUN_CORRELATED",
    "TARGET_SELF_CHECKED", "COMPLETED",
)

COMPLETED = "COMPLETED"
MENTION_READY = "MENTION_READY"
INVALID_INPUT = "INVALID_INPUT"
REPLAY_REFUSED = "REPLAY_REFUSED"
ROUTING_REQUIRED = "ROUTING_REQUIRED"
ROUTE_CONFLICT = "ROUTE_CONFLICT"
ISSUE_UNVERIFIED = "ISSUE_UNVERIFIED"
ISSUE_RESPONSE_INVALID = "ISSUE_RESPONSE_INVALID"
PREPARE_FAILED = "PREPARE_FAILED"
PREPARE_BLOCKED = "PREPARE_BLOCKED"
PREPARE_PARTIAL_STOPPED = "PREPARE_PARTIAL_STOPPED"
COMPOSE_REJECTED = "COMPOSE_REJECTED"
PUBLISH_FAILED = "PUBLISH_FAILED"
CONFIRMATION_FAILED = "CONFIRMATION_FAILED"
MENTION_CONFIRMATION_REQUIRED = "MENTION_CONFIRMATION_REQUIRED"
MENTION_EVIDENCE_REJECTED = "MENTION_EVIDENCE_REJECTED"
RUN_CORRELATION_FAILED = "RUN_CORRELATION_FAILED"
SELF_CHECK_BLOCKED = "SELF_CHECK_BLOCKED"
SELF_REFRESH_EXHAUSTED = "SELF_REFRESH_EXHAUSTED"
ASSIGNMENT_MUTATION_DETECTED = "ASSIGNMENT_MUTATION_DETECTED"

TERMINAL_STATUSES = (
    COMPLETED, MENTION_READY, INVALID_INPUT, REPLAY_REFUSED, ROUTING_REQUIRED,
    ROUTE_CONFLICT, ISSUE_UNVERIFIED, ISSUE_RESPONSE_INVALID, PREPARE_FAILED,
    PREPARE_BLOCKED, PREPARE_PARTIAL_STOPPED, COMPOSE_REJECTED, PUBLISH_FAILED,
    CONFIRMATION_FAILED, MENTION_CONFIRMATION_REQUIRED, MENTION_EVIDENCE_REJECTED,
    RUN_CORRELATION_FAILED, SELF_CHECK_BLOCKED, SELF_REFRESH_EXHAUSTED,
    ASSIGNMENT_MUTATION_DETECTED,
)

STAGED_TERMINALS = (COMPLETED, MENTION_READY)

UNCERTAINTY = (
    "exactly-once is evidenced only by this transaction's observable ledger; "
    "platform-side atomicity of native mentions and run dispatch is not proven",
    "native mention evidence is injected over the deployed platform's "
    "observable reply surface; distinguishing one real native agent mention "
    "from forged markdown relies on the bounded evidence contract validated "
    "here plus the run correlation step — T12 owns live observation",
    "the target run and its SELF_CHECK are simulated inside this transaction "
    "with injected evidence; platform-level pre-run guarantees do not exist",
    "assignee stability is proven against the observed issue responses at "
    "transaction start and finish; concurrent platform-side mutations between "
    "the two reads are not observable here",
)

GUARANTEES = {
    "live_mutations": 0,
    "mentions_constructed_by_adapter": 0,
    "assignments": 0,
    "issues_created": 0,
    "run_triggers_outside_native_mention": 0,
    "canonical_writes": 0,
    "llm_calls_in_orchestrator": 0,
    "frozen_schema_changes": 0,
}


class MentionHandoffError(Exception):
    """Bounded stop with a stable code (routing/route/evidence failures)."""

    code = "mention_handoff_error"

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


class RouteConflictError(MentionHandoffError):
    code = "route_conflict"


class EvidenceError(MentionHandoffError):
    code = "mention_evidence_rejected"


class AmbiguousMentionError(MentionHandoffError):
    code = "mention_confirmation_required"


class RunCorrelationError(MentionHandoffError):
    code = "run_correlation_failed"


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

    def __init__(self, ledger: dispatch.TransactionLedger, transaction_id: str,
                 state: str = "INIT", seed: list | None = None):
        self.ledger = ledger
        self.transaction_id = transaction_id
        self.state = state
        self.transitions = list(seed) if seed else [state]
        if not self.transitions or self.transitions[-1] != state:
            self.transitions.append(state)

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


def _require_native_id(value, field: str) -> str:
    try:
        return dispatch._require_bare_id(value, field)
    except dispatch.DispatchError as exc:
        raise _Stop(INVALID_INPUT, exc.message,
                    extra={"error": exc.envelope()}) from None


def _validate_spec(spec, caller_role, transaction_id, compose_fn) -> dict:
    """Zero-command input validation. Bounded INVALID_INPUT refusals."""
    try:
        return _validate_spec_fields(spec, caller_role, transaction_id,
                                     compose_fn)
    except dispatch.DispatchError as exc:
        raise _Stop(INVALID_INPUT, exc.message,
                    extra={"error": exc.envelope()}) from None


def _scan_for_mentions(spec: dict, path: str) -> None:
    if dispatch.MENTION_RE.search(json.dumps(spec, ensure_ascii=False,
                                             sort_keys=True, default=str)):
        raise _Stop(INVALID_INPUT,
                    "spec carries a mention link; the mention route input "
                    "must not smuggle mention markdown — the real mention is "
                    "emitted later on the current agent's native reply surface")


def _validate_spec_fields(spec, caller_role, transaction_id, compose_fn) -> dict:
    if not isinstance(spec, dict):
        raise _Stop(INVALID_INPUT, "spec must be a dict")
    _scan_for_mentions(spec, "spec")
    issue_id = _require_native_id(spec.get("issue_id"), "spec.issue_id")
    project_id = dispatch._require_text(spec.get("project_id"),
                                        "spec.project_id", 64)
    purpose = spec.get("purpose")
    purpose = dispatch._require_text(
        purpose if purpose is not None else "implementation",
        "spec.purpose", 120)
    options = spec.get("options")
    if options is not None and not isinstance(options, dict):
        raise _Stop(INVALID_INPUT, "spec.options must be a dict or None")
    markers = spec.get("decision_markers")
    if markers is not None and not (isinstance(markers, list)
                                    and all(isinstance(m, str) for m in markers)):
        raise _Stop(INVALID_INPUT,
                    "spec.decision_markers must be a list of strings")
    try:
        caller_slug = asm._slug_of(caller_role)
    except asm.RoutingRequiredError:
        raise _Stop(INVALID_INPUT,
                    f"caller_role {caller_role!r} is not in the frozen T08 "
                    "role table") from None
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
        "issue_id": issue_id, "project_id": project_id, "purpose": purpose,
        "options": options, "decision_markers": markers,
        "caller_role": caller_slug,
    }


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


def _assignee_of(issue: dict) -> dict:
    return {"assignee_id": issue.get("assignee_id"),
            "assignee": issue.get("assignee")}


def _assignee_equal(before: dict | None, after: dict | None) -> bool | None:
    if before is None or after is None:
        return None
    return before == after


def route_conflicts(records: list, *, transaction_id: str, issue_id: str,
                    task_ref: str, role: str, package_id: str | None) -> list:
    """Deterministic route mutual-exclusion scan over the shared ledger.

    One handoff (issue + task_ref + role binding), one package and one
    transaction each carry EXACTLY ONE route. Assignment evidence (issue
    assign commands / trigger outcomes / assignment-triggered results) and
    mention evidence (mention outcomes / mention-triggered results) must
    never overlap on the same transaction, package or handoff.
    """
    conflicts: list = []
    commands = [r for r in records if r.get("kind") == "command"]
    assign_txs = {r.get("transaction_id") for r in commands
                  if r.get("command_class") == "assignment_trigger"}
    mention_outcomes = [r for r in records if r.get("kind") == "mention_outcome"]
    mention_txs = {r.get("transaction_id") for r in mention_outcomes}

    both = sorted(assign_txs & mention_txs)
    conflicts += [{"transaction_id": t,
                   "reason": "assignment_and_mention_in_one_transaction"}
                  for t in both]

    package_routes: dict = {}
    for record in records:
        if record.get("kind") != "publish_outcome" \
                or not record.get("package_id"):
            continue
        tx = record.get("transaction_id")
        route = "assignment" if tx in assign_txs else (
            "mention" if tx in mention_txs else "unbound")
        package_routes.setdefault(record["package_id"], set()).add(route)
    if package_id is not None:
        routes = package_routes.get(package_id, set())
        if "assignment" in routes:
            conflicts.append({"package_id": package_id,
                              "reason": "package_bound_to_assignment_route"})
        if "mention" in routes and transaction_id not in mention_txs:
            conflicts.append({"package_id": package_id,
                              "reason": "package_already_mention_triggered"})
        for record in mention_outcomes:
            if record.get("package_id") == package_id \
                    and record.get("transaction_id") != transaction_id:
                conflicts.append({"package_id": package_id,
                                  "reason": "package_already_mention_triggered"})

    for record in records:
        if record.get("kind") != "transaction_result" \
                or record.get("transaction_id") == transaction_id:
            continue
        result = record.get("result") or {}
        trigger_type = (result.get("trigger") or {}).get("type")
        res_issue = (result.get("issue") or {}).get("id")
        res_role = (result.get("target") or {}).get("role")
        if trigger_type == "issue_assign" \
                and res_issue == issue_id and result.get("task_ref") == task_ref \
                and res_role == role:
            conflicts.append({
                "transaction_id": record.get("transaction_id"),
                "reason": "handoff_already_assignment_triggered"})
    return conflicts


def cross_route_audit(records: list, executable: str = "multica") -> dict:
    """Whole-ledger assignment/mention route audit (compatible extension).

    Fails when one transaction carries both assignment and mention evidence,
    when one package is bound to both routes, or when one handoff (issue +
    task_ref + role) was triggered by both trigger types. Counts the ledger's
    mention outcomes per handoff and target runs per mention.
    """
    conflicts = []
    commands = [r for r in records if r.get("kind") == "command"]
    assign_txs = {r.get("transaction_id") for r in commands
                  if r.get("command_class") == "assignment_trigger"}
    mention_outcomes = [r for r in records if r.get("kind") == "mention_outcome"]
    mention_txs = {r.get("transaction_id") for r in mention_outcomes}
    both = sorted(assign_txs & mention_txs)
    conflicts += [{"transaction_id": t,
                   "reason": "assignment_and_mention_in_one_transaction"}
                  for t in both]

    package_routes: dict = {}
    for record in records:
        if record.get("kind") != "publish_outcome" \
                or not record.get("package_id"):
            continue
        tx = record.get("transaction_id")
        route = "assignment" if tx in assign_txs else (
            "mention" if tx in mention_txs else "unbound")
        package_routes.setdefault(record["package_id"], set()).add(route)
    for package_id, routes in sorted(package_routes.items()):
        if {"assignment", "mention"} <= routes:
            conflicts.append({"package_id": package_id,
                              "reason": "package_bound_to_both_routes"})

    handoffs: dict = {}
    for record in records:
        if record.get("kind") != "transaction_result":
            continue
        result = record.get("result") or {}
        trigger_type = (result.get("trigger") or {}).get("type")
        issue_id = (result.get("issue") or {}).get("id")
        task_ref = result.get("task_ref")
        role = (result.get("target") or {}).get("role")
        if not (issue_id and task_ref and role):
            continue
        handoffs.setdefault((issue_id, task_ref, role), set()).add(trigger_type)
    for (issue_id, task_ref, role), types in sorted(
            handoffs.items(), key=lambda item: json.dumps(item[0])):
        if "issue_assign" in types and "mention" in types:
            conflicts.append({"issue_id": issue_id, "task_ref": task_ref,
                              "role": role,
                              "reason": "handoff_triggered_by_both_routes"})

    mentions_per_package: dict = {}
    for record in mention_outcomes:
        key = record.get("package_id")
        mentions_per_package[key] = mentions_per_package.get(key, 0) + 1
    over = sorted(k for k, v in mentions_per_package.items() if v > 1)
    conflicts += [{"package_id": k, "reason": "more_than_one_mention"}
                  for k in over]

    run_outcomes = [r for r in records if r.get("kind") == "run_outcome"]
    runs_per_mention: dict = {}
    for record in run_outcomes:
        key = record.get("mention_comment_id")
        runs_per_mention[key] = runs_per_mention.get(key, 0) + 1
    over_runs = sorted(k for k, v in runs_per_mention.items() if v > 1)
    conflicts += [{"mention_comment_id": k, "reason": "more_than_one_target_run"}
                  for k in over_runs]

    counts: dict = {}
    for record in commands:
        counts[record.get("command_class") or "other"] = \
            counts.get(record.get("command_class") or "other", 0) + 1
    mention_hits = [record["seq"] for record in commands
                    if any(dispatch.MENTION_RE.search(str(a))
                           for a in record.get("argv") or [])]
    if mention_hits:
        conflicts.append({"reason": "mention_link_in_issued_argv",
                          "seqs": mention_hits})
    return {
        "route_transactions": {
            "assignment": sorted(t for t in assign_txs if t),
            "mention": sorted(t for t in mention_txs if t),
        },
        "package_route_bindings": {
            k: sorted(v) for k, v in sorted(package_routes.items())},
        "mention_outcomes": len(mention_outcomes),
        "run_outcomes": len(run_outcomes),
        "command_counts": counts,
        "conflicts": conflicts,
        "ok": not conflicts,
    }


def audit_mention_ledger(records: list, executable: str = "multica") -> dict:
    """Minimal compatible extension of the T09 `audit_ledger` for one
    mention-route transaction: zero issue-create, zero assignment trigger,
    at most one published note (+ at most one bounded refresh publish), at
    most one mention authorization, at most one correlated run, and no
    adapter-constructed mention link in any issued argv."""
    commands = [r for r in records if r.get("kind") == "command"]
    counts: dict = {}
    for record in commands:
        counts[record.get("command_class") or "other"] = \
            counts.get(record.get("command_class") or "other", 0) + 1
    mention_hits = [record["seq"] for record in commands
                    if any(dispatch.MENTION_RE.search(str(a))
                           for a in record.get("argv") or [])]
    mention_outcomes = [r for r in records if r.get("kind") == "mention_outcome"]
    run_outcomes = [r for r in records if r.get("kind") == "run_outcome"]
    unexpected = sorted(set(counts) - {"read", "comment_publish"})
    route = route_conflicts(
        records, transaction_id=str(
            commands[0].get("transaction_id") if commands else ""),
        issue_id="", task_ref="", role="", package_id=None)
    route_internal = [c for c in route
                      if c["reason"] == "assignment_and_mention_in_one_transaction"]
    ok = (
        not mention_hits
        and not unexpected
        and counts.get("issue_create", 0) == 0
        and counts.get("assignment_trigger", 0) == 0
        and counts.get("comment_publish", 0) <= 2
        and len(mention_outcomes) <= 1
        and len(run_outcomes) <= 1
        and not route_internal
    )
    return {
        "command_counts": counts,
        "mention_hits": mention_hits,
        "issue_create": {"count": counts.get("issue_create", 0)},
        "assignment_trigger": {"count": counts.get("assignment_trigger", 0)},
        "comment_publish": {"count": counts.get("comment_publish", 0),
                            "note": "main note + at most one bounded "
                                    "self-refresh publish"},
        "mention_authorizations": len(mention_outcomes),
        "run_correlations": len(run_outcomes),
        "unexpected_write_classes": unexpected,
        "route_conflicts_internal": route_internal,
        "ok": ok,
    }


def mention_ready_envelope(*, transaction_id: str, issue: dict, caller: dict,
                           target: dict, package: dict,
                           published_comment_id: str | None,
                           published_comment_created_at: str | None) -> dict:
    """The bounded, auditable MENTION_READY envelope for the CURRENT agent.

    It authorizes exactly one native mention of the resolved target agent on
    the exact issue. It never carries mention markdown: the current agent
    emits the mention on its native reply surface (§35.4).
    """
    return {
        "kind": "mention_ready_envelope",
        "schema_version": MENTION_READY_SCHEMA,
        "transaction_id": transaction_id,
        "route": "mention",
        "issue": {"id": issue["id"], "identifier": issue["identifier"]},
        "caller": {"role": caller["role"], "agent_id": caller["agent_id"]},
        "target": {"role": target["role"], "role_name": target["role_name"],
                   "agent_id": target["agent_id"],
                   "agent_name": target["agent_name"]},
        "package": {"package_id": package["package_id"],
                    "status": package["status"]},
        "published_comment_id": published_comment_id,
        "published_comment_created_at": published_comment_created_at,
        "handoff_ready": True,
        "trigger": {"type": "mention", "authorized_mentions": 1},
        "constraints": {
            "emit_exactly_one_native_mention": True,
            "adapter_constructs_mention_markdown": False,
            "mention_target_agent_id": target["agent_id"],
            "assignment_forbidden": True,
            "assignee_must_remain_unchanged": True,
            "ambiguous_mention_requires_reconciliation_not_retry": True,
            "second_mention_never_authorized": True,
        },
    }


def validate_mention_evidence(evidence, *, ready: dict) -> dict:
    """Fail-closed validation of ONE injected native mention receipt.

    The evidence must prove: exactly one target-agent mention, on the exact
    issue, by the expected current agent, on the agent's native reply
    surface, after the confirmed handoff, with the expected target id and no
    assignment mutation. Plain text `@Name`, fabricated link text, wrong or
    multiple mentions, and assignment+mention combinations are rejected.
    Raises EvidenceError (MENTION_EVIDENCE_REJECTED) or
    AmbiguousMentionError (MENTION_CONFIRMATION_REQUIRED).
    """
    if not isinstance(evidence, dict):
        raise EvidenceError("mention evidence must be a dict",
                            kind=type(evidence).__name__)
    if evidence.get("kind") != "native_mention_evidence":
        raise EvidenceError("mention evidence kind must be "
                            "native_mention_evidence",
                            kind=str(evidence.get("kind"))[:80])
    if not str(evidence.get("schema_version", "")).startswith("T10-mention-evidence/"):
        raise EvidenceError("mention evidence schema_version is not "
                            "T10-mention-evidence",
                            schema_version=str(evidence.get("schema_version"))[:80])
    if evidence.get("transaction_id") != ready["transaction_id"]:
        raise EvidenceError("mention evidence belongs to another transaction",
                            transaction_id=str(evidence.get("transaction_id"))[:80])
    if evidence.get("issue_id") != ready["issue"]["id"]:
        raise EvidenceError("mention evidence names a different issue",
                            issue_id=str(evidence.get("issue_id"))[:80])
    if evidence.get("author_agent_id") != ready["caller"]["agent_id"]:
        raise EvidenceError(
            "mention evidence author is not the expected current agent",
            author_agent_id=str(evidence.get("author_agent_id"))[:80])
    if evidence.get("author_surface") != NATIVE_SURFACE:
        raise EvidenceError(
            "mention evidence does not attest the current agent's native "
            "reply surface; adapter-constructed or forged mentions are refused",
            author_surface=str(evidence.get("author_surface"))[:80])
    comment_id = dispatch._require_bare_id(evidence.get("comment_id"),
                                           "evidence.comment_id") \
        if isinstance(evidence.get("comment_id"), str) else None
    if not comment_id:
        raise EvidenceError("mention evidence carries no comment id")
    if ready.get("published_comment_id") \
            and comment_id == ready["published_comment_id"]:
        raise EvidenceError(
            "the mention comment id equals the published note comment; a "
            "non-trigger /note can never be the native mention",
            comment_id=comment_id[:80])
    published_at = ready.get("published_comment_created_at")
    mentioned_at = evidence.get("created_at")
    if not isinstance(mentioned_at, str) or not mentioned_at.strip():
        raise AmbiguousMentionError(
            "mention evidence carries no created_at; the strict order "
            "(handoff confirmed before mention) cannot be proven",
            comment_id=comment_id)
    if published_at and str(mentioned_at) <= str(published_at):
        raise EvidenceError(
            "the mention precedes the confirmed handoff publication; strict "
            "order violated",
            mentioned_at=mentioned_at[:80],
            published_at=str(published_at)[:80])
    mentions = evidence.get("mentions")
    if not isinstance(mentions, list):
        raise EvidenceError("mention evidence mentions must be a list",
                            kind=type(mentions).__name__)
    handles = evidence.get("plain_text_handles")
    if handles is not None and not (isinstance(handles, list)
                                    and all(isinstance(h, str) for h in handles)):
        raise EvidenceError("mention evidence plain_text_handles must be a "
                            "list of strings or absent")
    if len(mentions) == 0:
        raise EvidenceError(
            "mention evidence proves zero agent mention links; a plain text "
            "@Name is never a native mention",
            plain_text_handles=[str(h)[:40] for h in (handles or [])][:8])
    if len(mentions) > 1:
        raise EvidenceError(
            "mention evidence proves more than one agent mention; exactly one "
            "target mention is authorized",
            count=len(mentions))
    if handles:
        raise AmbiguousMentionError(
            "mention evidence reports plain text handles alongside the "
            "native mention; the receipt is ambiguous and must not authorize "
            "a retry",
            plain_text_handles=[str(h)[:40] for h in handles][:8])
    mention = mentions[0]
    if not isinstance(mention, dict):
        raise EvidenceError("mention entry is not an object",
                            kind=type(mention).__name__)
    if mention.get("agent_id") != ready["target"]["agent_id"]:
        raise EvidenceError(
            "the mention names the wrong agent; the resolved T08 target is "
            "the only authorized mention target",
            mention_agent_id=str(mention.get("agent_id"))[:80],
            expected_agent_id=str(ready["target"]["agent_id"])[:80])
    if mention.get("role") != ready["target"]["role"]:
        raise EvidenceError("mention role does not match the resolved target",
                            mention_role=str(mention.get("role"))[:80])
    expected_link = "mention://agent/" + ready["target"]["agent_id"]
    if mention.get("link") != expected_link:
        raise EvidenceError(
            "mention link is not the exact native agent mention link for the "
            "resolved target; fabricated link text is refused",
            link=str(mention.get("link"))[:120],
            expected_link=expected_link)
    mutation = evidence.get("assignment_mutation")
    if mutation is not None:
        raise EvidenceError(
            "mention evidence carries an assignment mutation; the mention "
            "route forbids any assignment trigger (no double trigger)",
            assignment_mutation=str(mutation)[:120])
    return {
        "accepted": True,
        "comment_id": comment_id,
        "created_at": mentioned_at,
        "author_agent_id": ready["caller"]["agent_id"],
        "target_agent_id": ready["target"]["agent_id"],
        "mention_count": 1,
    }


def validate_run_evidence(evidence, *, ready: dict, mention: dict) -> dict:
    """Fail-closed correlation of the target run to the single accepted
    mention. Zero, duplicate, wrong-target or uncorrelated runs stop the
    transaction; a second mention is never recommended."""
    if not isinstance(evidence, dict):
        raise RunCorrelationError("run evidence must be a dict",
                                  kind=type(evidence).__name__)
    if evidence.get("kind") != "target_run_evidence":
        raise RunCorrelationError("run evidence kind must be target_run_evidence",
                                  kind=str(evidence.get("kind"))[:80])
    if not str(evidence.get("schema_version", "")).startswith("T10-run-evidence/"):
        raise RunCorrelationError("run evidence schema_version is not "
                                  "T10-run-evidence",
                                  schema_version=str(evidence.get("schema_version"))[:80])
    if evidence.get("transaction_id") != ready["transaction_id"]:
        raise RunCorrelationError("run evidence belongs to another transaction",
                                  transaction_id=str(evidence.get("transaction_id"))[:80])
    if evidence.get("issue_id") != ready["issue"]["id"]:
        raise RunCorrelationError("run evidence names another issue",
                                  issue_id=str(evidence.get("issue_id"))[:80])
    if evidence.get("mention_comment_id") != mention["comment_id"]:
        raise RunCorrelationError(
            "run evidence is not traceable to the single accepted mention",
            mention_comment_id=str(evidence.get("mention_comment_id"))[:80],
            expected_mention_comment_id=str(mention["comment_id"])[:80])
    runs = evidence.get("runs")
    if not isinstance(runs, list):
        raise RunCorrelationError("run evidence runs must be a list",
                                  kind=type(runs).__name__)
    if len(runs) == 0:
        raise RunCorrelationError(
            "no target run is observable for the accepted mention; stop — "
            "never blindly emit or recommend a second mention",
            reason="no_target_run")
    if len(runs) > 1:
        raise RunCorrelationError(
            "more than one target run is observable for the accepted mention; "
            "ambiguous — reconcile read-only, never re-mention",
            reason="multiple_target_runs", count=len(runs))
    run = runs[0]
    if not isinstance(run, dict):
        raise RunCorrelationError("run entry is not an object",
                                  kind=type(run).__name__)
    if run.get("agent_id") != ready["target"]["agent_id"]:
        raise RunCorrelationError(
            "the observed run belongs to the wrong agent; stop and reconcile "
            "read-only — never a second mention",
            reason="wrong_target",
            run_agent_id=str(run.get("agent_id"))[:80],
            expected_agent_id=str(ready["target"]["agent_id"])[:80])
    if run.get("source") != MENTION_SOURCE:
        raise RunCorrelationError(
            "the observed run is not attributable to the accepted mention; "
            "uncorrelated evidence stops the path",
            reason="uncorrelated_source", source=str(run.get("source"))[:80])
    return {
        "correlated": True,
        "run_id": str(run.get("run_id") or "")[:80] or None,
        "agent_id": ready["target"]["agent_id"],
        "mention_comment_id": mention["comment_id"],
        "run_count": 1,
    }


MENTION_SOURCE = "mention"


class MentionHandoff:
    """One mention-handoff transaction: staged, ordered, ledgered, fail-closed."""

    def __init__(self, *, validated: dict, target_role_spec: str, recorder,
                 ledger, compose_fn: Callable, clock: Callable,
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

        self.machine: _Machine | None = None
        self.issue: dict | None = None
        self.task_ref: str | None = None
        self.target: dict | None = None
        self.caller_agent_id: str | None = None
        self.envelope: dict | None = None
        self.published_comment_id: str | None = None
        self.published_comment_created_at: str | None = None
        self.confirmed: dict | None = None
        self.ready_envelope: dict | None = None
        self.mention_evidence: dict | None = None
        self.run_evidence: dict | None = None
        self.assignee_before: dict | None = None
        self.assignee_after: dict | None = None
        self.self_check_evidence: dict | None = None
        self._last_request: dict | None = None

    # -- phase A -----------------------------------------------------------

    def run_ready(self) -> dict:
        self.machine = _Machine(self.ledger, self.recorder.transaction_id)
        try:
            self._step_bind_issue()
            self._step_route_precheck_handoff()
            self._step_resolve()
            self._step_freeze_route()
            envelope = self._prepare_pipeline()
            self._step_finalize(envelope)
            self._step_route_precheck_package()
            self._step_publish()
            self._step_confirm()
        except _Stop as stop:
            return self._finish(stop.status, stop.reason,
                                escalation=stop.escalation, extra=stop.extra,
                                stage=PHASE_READY)
        except dispatch.DispatchError as exc:
            return self._finish(_dispatch_terminal(exc), exc.message,
                                escalation={"required": True,
                                            "reason": exc.code},
                                extra={"error": exc.envelope()},
                                stage=PHASE_READY)
        except asm.RoutingRequiredError as exc:
            return self._finish(ROUTING_REQUIRED, exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad", exc.code,
                                    **exc.details),
                                extra={"error": exc.envelope()},
                                stage=PHASE_READY)
        except asm.T08BundleError as exc:
            return self._finish(ROUTING_REQUIRED, exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad", exc.code,
                                    **exc.details),
                                extra={"error": exc.envelope()},
                                stage=PHASE_READY)
        except RouteConflictError as exc:
            return self._finish(ROUTE_CONFLICT, exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad", exc.code,
                                    **exc.details),
                                extra={"error": exc.envelope(),
                                       "conflicts": exc.details.get("conflicts")},
                                stage=PHASE_READY)
        self.ready_envelope = mention_ready_envelope(
            transaction_id=self.recorder.transaction_id,
            issue=self.issue, caller={"role": self.spec["caller_role"],
                                      "agent_id": self.caller_agent_id},
            target=self.target,
            package={"package_id": self.envelope["package_id"],
                     "status": self.envelope["status"]},
            published_comment_id=self.published_comment_id,
            published_comment_created_at=self.published_comment_created_at)
        self.ledger.append({
            "kind": "mention_ready",
            "transaction_id": self.recorder.transaction_id,
            "route": "mention",
            "issue_id": self.issue["id"],
            "caller_agent_id": self.caller_agent_id,
            "target_role": self.target["role"],
            "target_agent_id": self.target["agent_id"],
            "package_id": self.envelope["package_id"],
            "published_comment_id": self.published_comment_id,
            "authorized_mentions": 1,
            "adapter_constructs_mention_markdown": False,
        })
        self.machine.to("MENTION_READY")
        return self._finish(MENTION_READY, None, stage=PHASE_READY)

    def _step_bind_issue(self) -> None:
        self.machine.require("INIT")
        try:
            issue = self.dispatch_cli.issue_get(self.spec["issue_id"])
        except dispatch.DispatchError as exc:
            raise _Stop(ISSUE_UNVERIFIED,
                        "the existing issue could not be read; whether the "
                        "issue exists is unverified — stop, never guess an id",
                        extra={"error": exc.envelope()}) from None
        except adapter.AdapterError as exc:
            raise _Stop(ISSUE_RESPONSE_INVALID,
                        "the existing issue response violates the frozen "
                        "issue contract; stop",
                        extra={"error": _bounded_reason(exc)}) from None
        self.issue = issue
        self.task_ref = adapter.task_ref_of(issue["identifier"])
        self.assignee_before = _assignee_of(issue)
        self.ledger.append({
            "kind": "assignee_observation",
            "transaction_id": self.recorder.transaction_id,
            "phase": "before",
            "issue_id": issue["id"],
            "assignee_id": issue.get("assignee_id"),
            "assignee": issue.get("assignee"),
        })
        self.machine.to("ISSUE_BOUND")

    def _step_route_precheck_handoff(self) -> None:
        self.machine.require("ISSUE_BOUND")
        conflicts = route_conflicts(
            self.ledger.records,
            transaction_id=self.recorder.transaction_id,
            issue_id=self.issue["id"], task_ref=self.task_ref, role="",
            package_id=None)
        if conflicts:
            raise RouteConflictError(
                "the shared ledger already binds this handoff to another "
                "route; one handoff carries exactly one trigger",
                conflicts=conflicts)
        # Handoff-level assignment binding is checked once the target role is
        # resolved; the issue-level scan above covers transaction/package
        # overlaps that are already provable.

    def _step_resolve(self) -> None:
        self.machine.require("ISSUE_BOUND")
        try:
            self.target = asm.resolve_target(self.target_role_spec,
                                             bundle_dir=self.bundle_dir)
        except (asm.RoutingRequiredError, asm.T08BundleError):
            raise
        mapping = asm.t08_mapping(self.bundle_dir)
        caller_entry = mapping["agents"].get(self.spec["caller_role"])
        if caller_entry is None:
            raise asm.RoutingRequiredError(
                f"caller role {self.spec['caller_role']!r} has no T08 "
                "baseline agent; T08 preconditions drifted",
                caller_role=self.spec["caller_role"])
        self.caller_agent_id = caller_entry["agent_id"]
        self.machine.to("TARGET_RESOLVED")

    def _step_freeze_route(self) -> None:
        self.machine.require("TARGET_RESOLVED")
        conflicts = route_conflicts(
            self.ledger.records,
            transaction_id=self.recorder.transaction_id,
            issue_id=self.issue["id"], task_ref=self.task_ref,
            role=self.target["role"], package_id=None)
        if conflicts:
            raise RouteConflictError(
                "the shared ledger already binds this handoff (issue/task/"
                "role) to the assignment route; one handoff carries exactly "
                "one trigger", conflicts=conflicts)
        self.ledger.append({
            "kind": "route_binding",
            "transaction_id": self.recorder.transaction_id,
            "route": "mention",
            "issue_id": self.issue["id"],
            "task_ref": self.task_ref,
            "target_role": self.target["role"],
        })
        self.machine.to("ROUTE_FROZEN")

    def _prepare_pipeline(self) -> dict:
        if self.machine.state not in ("ROUTE_FROZEN", "TARGET_RUN_CORRELATED"):
            raise _Stop(INVALID_INPUT,
                        f"prepare pipeline reached from state "
                        f"{self.machine.state!r}")
        request = self._ensure_snapshot_request()
        plan_result = self._plan(request)
        if plan_result.get("status") != "PLAN_READY":
            if plan_result.get("status") == "BLOCKED":
                raise _Stop(PREPARE_BLOCKED, "T01 PLAN blocked",
                            escalation=plan_result.get("escalation")
                            or {"required": True, "reason": "plan_blocked"},
                            extra={"finding_gate": asm._gate_summary(plan_result)})
            raise _Stop(PREPARE_FAILED,
                        f"T01 returned {plan_result.get('status')!r}",
                        extra={"escalation": plan_result.get("escalation")})
        return self._compose_and_finalize(plan_result["plan"], request)

    def _ensure_snapshot_request(self) -> dict:
        """T05 snapshot request over the EXISTING issue (read-only).

        The execute stage re-derives it from the ledger-recovered binding so
        the target run's SELF_CHECK sees the task as it stands at execution
        time; a changed task surfaces as task_changed -> REFRESH, never as a
        stale READY.
        """
        if self._last_request is not None:
            return self._last_request
        try:
            snap = adapter.build_snapshot_request(
                issue_id=self.spec["issue_id"],
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
        self._last_request = snap["request"]
        return self._last_request

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
        self.machine.require("ROUTE_FROZEN")
        if envelope["status"] == "PARTIAL":
            if not self.policy.get("allow_partial_publication"):
                raise _Stop(PREPARE_PARTIAL_STOPPED,
                            "PARTIAL is never normal-ready; no explicit "
                            "caller policy decision authorized publication",
                            escalation={"required": True,
                                        "route_to": "engineering-lead-or-squad",
                                        "reason": "partial_requires_policy"},
                            extra={"gaps": asm._gaps(envelope)})
            self.ledger.append({
                "kind": "policy_decision",
                "transaction_id": self.recorder.transaction_id,
                "decision": "publish_partial",
                "authorized_by": self.policy.get("authorized_by"),
                "gaps": asm._gaps(envelope),
            })
        self.envelope = envelope
        self.machine.to("HANDOFF_PREPARED")

    def _step_route_precheck_package(self) -> None:
        self.machine.require("HANDOFF_PREPARED")
        conflicts = route_conflicts(
            self.ledger.records,
            transaction_id=self.recorder.transaction_id,
            issue_id=self.issue["id"], task_ref=self.task_ref,
            role=self.target["role"], package_id=self.envelope["package_id"])
        if conflicts:
            raise RouteConflictError(
                "the package or handoff is already bound to another route in "
                "the shared ledger; one handoff carries exactly one trigger",
                conflicts=conflicts)

    def _step_publish(self) -> None:
        self.machine.require("HANDOFF_PREPARED")
        try:
            result = note.publish_handoff(
                self.envelope,
                issue_id=self.issue["id"],
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
        self.published_comment_created_at = comment.get("created_at")
        self.ledger.append({
            "kind": "publish_outcome",
            "transaction_id": self.recorder.transaction_id,
            "published": bool(result.get("published")),
            "idempotent": bool(result.get("idempotent")),
            "comment_id": self.published_comment_id,
            "package_id": self.envelope["package_id"],
        })
        self.machine.to("HANDOFF_PUBLISHED")

    def _step_confirm(self) -> dict:
        self.machine.require("HANDOFF_PUBLISHED")
        try:
            resolved = note.resolve_latest_handoff(
                self.issue["id"], task_ref=self.task_ref,
                target_role=self.target["role"], cli=self.note_cli)
        except Exception as exc:
            raise _Stop(CONFIRMATION_FAILED,
                        "T06 discovery fail-closed while confirming "
                        "HANDOFF_READY; no mention authorization",
                        extra={"error": _bounded_reason(exc)}) from None
        if not resolved.get("found"):
            raise _Stop(CONFIRMATION_FAILED,
                        "no valid CONTEXT_HANDOFF record resolvable for this "
                        "task/role; no mention authorization",
                        extra={"records_seen":
                               (resolved.get("selection") or {}).get("records_seen")})
        resolved_env = resolved["envelope"]
        comment = resolved.get("comment") or {}
        problems = []
        if resolved_env.get("package_id") != self.envelope["package_id"]:
            problems.append("package_id")
        if comment.get("id") != self.published_comment_id:
            problems.append("comment_id")
        if resolved_env.get("task_ref") != self.task_ref:
            problems.append("task_ref")
        if resolved_env.get("role") != self.target["role"]:
            problems.append("role")
        if problems:
            raise _Stop(CONFIRMATION_FAILED,
                        "the just-published note did not re-resolve as this "
                        "handoff's READY record; mismatch in " +
                        ", ".join(problems) + "; no mention authorization",
                        extra={"mismatch": problems,
                               "resolved_comment_id": comment.get("id")})
        self.confirmed = resolved_env
        self.machine.to("HANDOFF_READY_CONFIRMED")
        return resolved_env

    # -- phase B -----------------------------------------------------------

    def run_execute(self, *, mention_evidence, run_evidence) -> dict:
        prior = _recorded_result(self.recorder.transaction_id, self.ledger)
        if prior is None:
            return self._standalone(
                REPLAY_REFUSED,
                "no staged MENTION_READY result is recorded for this "
                "transaction id; run the ready stage first (the current "
                "agent must see the envelope before any native mention)")
        self.machine = _Machine(self.ledger, self.recorder.transaction_id,
                                state="MENTION_READY",
                                seed=prior.get("transitions"))
        if prior.get("stage") == PHASE_EXECUTE:
            if prior.get("terminal_status") == COMPLETED:
                replayed = dict(prior)
                replayed["replayed"] = True
                replayed["commands"] = []
                return replayed
            return {
                "ok": False,
                "orchestrator": ORCHESTRATOR_VERSION,
                "transaction_id": self.recorder.transaction_id,
                "stage": PHASE_EXECUTE,
                "mode": "simulation",
                "terminal_status": REPLAY_REFUSED,
                "stop_reason": "a prior incomplete execution result is "
                               "recorded for this id; reconcile read-only "
                               "from the ledger — never a second mention or "
                               "a re-run",
                "prior_terminal_status": prior.get("terminal_status"),
                "audit": audit_mention_ledger(
                    _tx_records(self.ledger, self.recorder.transaction_id),
                    self.executable),
                "guarantees": dict(GUARANTEES),
                "uncertainty": list(UNCERTAINTY),
            }
        if prior.get("terminal_status") != MENTION_READY:
            return self._standalone(
                REPLAY_REFUSED,
                "the recorded stage result is not MENTION_READY; the mention "
                "path stops before any native mention or run")
        self._recover(prior)
        try:
            self._step_reconfirm()
            self._step_mention_evidence(mention_evidence)
            self._step_run_correlation(run_evidence)
            self._step_self_check()
        except _Stop as stop:
            return self._finish(stop.status, stop.reason,
                                escalation=stop.escalation, extra=stop.extra,
                                stage=PHASE_EXECUTE)
        except (EvidenceError, AmbiguousMentionError, RunCorrelationError,
                RouteConflictError) as exc:
            details = dict(exc.details)
            detail_reason = details.pop("reason", None)
            return self._finish(_evidence_terminal(exc), exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad",
                                    detail_reason or exc.code, **details),
                                extra={"error": exc.envelope(),
                                       "conflicts":
                                       exc.details.get("conflicts")},
                                stage=PHASE_EXECUTE)
        except asm.RoutingRequiredError as exc:
            return self._finish(ROUTING_REQUIRED, exc.message,
                                escalation=_escalation(
                                    "engineering-lead-or-squad", exc.code,
                                    **exc.details),
                                extra={"error": exc.envelope()},
                                stage=PHASE_EXECUTE)
        except dispatch.DispatchError as exc:
            return self._finish(_dispatch_terminal(exc), exc.message,
                                escalation={"required": True,
                                            "reason": exc.code},
                                extra={"error": exc.envelope()},
                                stage=PHASE_EXECUTE)
        return self._finish(COMPLETED, None, stage=PHASE_EXECUTE)

    def _recover(self, prior: dict) -> None:
        issue = prior.get("issue") or {}
        target = prior.get("target") or {}
        caller = prior.get("caller") or {}
        package = prior.get("package") or {}
        if not (issue.get("id") and caller.get("agent_id")
                and target.get("agent_id") and package.get("package_id")):
            raise _Stop(REPLAY_REFUSED,
                        "the staged MENTION_READY result is incomplete; the "
                        "ledger cannot be continued — reconcile read-only")
        self.issue = {"id": issue["id"], "identifier": issue.get("identifier")}
        self.task_ref = prior.get("task_ref")
        self.target = dict(target)
        self.caller_agent_id = caller["agent_id"]
        self.assignee_before = (prior.get("assignee") or {}).get("before")
        self.spec = dict(self.spec)
        self.envelope = {"package_id": package["package_id"],
                         "status": package.get("status")}
        self.published_comment_id = prior.get("published_comment_id")
        self.published_comment_created_at = \
            (prior.get("mention_ready_envelope") or {}).get(
                "published_comment_created_at")
        self.ready_envelope = prior.get("mention_ready_envelope")
        self.confirmed = None

    def _step_reconfirm(self) -> None:
        self.machine.require("MENTION_READY")
        try:
            resolved = note.resolve_latest_handoff(
                self.issue["id"], task_ref=self.task_ref,
                target_role=self.target["role"], cli=self.note_cli)
        except Exception as exc:
            raise _Stop(CONFIRMATION_FAILED,
                        "the confirmed handoff record is no longer "
                        "resolvable before mention execution; no mention is "
                        "authorized",
                        extra={"error": _bounded_reason(exc)}) from None
        if not resolved.get("found"):
            raise _Stop(CONFIRMATION_FAILED,
                        "the confirmed handoff record is no longer "
                        "resolvable; no mention is authorized",
                        extra={"records_seen":
                               (resolved.get("selection") or {}).get("records_seen")})
        resolved_env = resolved["envelope"]
        comment = resolved.get("comment") or {}
        problems = []
        if resolved_env.get("package_id") != self.envelope["package_id"]:
            problems.append("package_id")
        if comment.get("id") != self.published_comment_id:
            problems.append("comment_id")
        if resolved_env.get("task_ref") != self.task_ref:
            problems.append("task_ref")
        if resolved_env.get("role") != self.target["role"]:
            problems.append("role")
        if problems:
            raise _Stop(CONFIRMATION_FAILED,
                        "the staged handoff record no longer re-resolves as "
                        "this handoff's READY record; mismatch in " +
                        ", ".join(problems) + "; no mention is authorized",
                        extra={"mismatch": problems,
                               "resolved_comment_id": comment.get("id")})
        self.confirmed = resolved_env

    def _step_mention_evidence(self, evidence) -> None:
        self.machine.require("MENTION_READY")
        ready = dict(self.ready_envelope or {})
        ready.update({
            "transaction_id": self.recorder.transaction_id,
            "issue": self.issue,
            "caller": {"agent_id": self.caller_agent_id},
            "target": self.target,
            "published_comment_id": self.published_comment_id,
            "published_comment_created_at": self.published_comment_created_at,
        })
        prior_receipts = [r for r in _tx_records(self.ledger,
                                                 self.recorder.transaction_id)
                          if r.get("kind") == "mention_outcome"]
        if prior_receipts:
            raise EvidenceError(
                "a native mention receipt is already recorded for this "
                "transaction; a duplicated receipt never authorizes a second "
                "mention", reason="duplicate_receipt")
        if evidence is None:
            raise _Stop(MENTION_CONFIRMATION_REQUIRED,
                        "the current agent's native mention produced no "
                        "readable receipt; the mention result is ambiguous — "
                        "reconcile read-only, never a second mention",
                        escalation=_escalation(
                            "engineering-lead-or-squad",
                            "mention_confirmation_required"),
                        extra={"error": {"code": "mention_evidence_missing"}})
        try:
            self.mention_evidence = validate_mention_evidence(
                evidence, ready=ready)
        except AmbiguousMentionError as exc:
            raise _Stop(MENTION_CONFIRMATION_REQUIRED, exc.message,
                        escalation=_escalation(
                            "engineering-lead-or-squad", exc.code, **exc.details),
                        extra={"error": exc.envelope()}) from None
        except EvidenceError as exc:
            details = dict(exc.details)
            detail_reason = details.pop("reason", None)
            raise _Stop(MENTION_EVIDENCE_REJECTED, exc.message,
                        escalation=_escalation(
                            "engineering-lead-or-squad",
                            detail_reason or exc.code, **details),
                        extra={"error": exc.envelope()}) from None
        self.ledger.append({
            "kind": "mention_outcome",
            "transaction_id": self.recorder.transaction_id,
            "outcome": "confirmed",
            "trigger_type": "mention",
            "issue_id": self.issue["id"],
            "mention_comment_id": self.mention_evidence["comment_id"],
            "author_agent_id": self.mention_evidence["author_agent_id"],
            "target_agent_id": self.mention_evidence["target_agent_id"],
            "package_id": self.envelope["package_id"],
            "count": 1,
        })
        self.machine.to("MENTION_EVIDENCE_ACCEPTED")

    def _step_run_correlation(self, evidence) -> None:
        self.machine.require("MENTION_EVIDENCE_ACCEPTED")
        ready = dict(self.ready_envelope or {})
        ready.update({
            "transaction_id": self.recorder.transaction_id,
            "issue": self.issue,
            "target": self.target,
        })
        try:
            self.run_evidence = validate_run_evidence(
                evidence, ready=ready, mention=self.mention_evidence)
        except RunCorrelationError as exc:
            details = dict(exc.details)
            detail_reason = details.pop("reason", None)
            raise _Stop(RUN_CORRELATION_FAILED, exc.message,
                        escalation=_escalation(
                            "engineering-lead-or-squad",
                            detail_reason or exc.code, **details),
                        extra={"error": exc.envelope()}) from None
        self.ledger.append({
            "kind": "run_outcome",
            "transaction_id": self.recorder.transaction_id,
            "outcome": "correlated",
            "issue_id": self.issue["id"],
            "mention_comment_id": self.mention_evidence["comment_id"],
            "target_agent_id": self.run_evidence["agent_id"],
            "run_id": self.run_evidence["run_id"],
            "run_count": 1,
        })
        self.machine.to("TARGET_RUN_CORRELATED")

    def _step_self_check(self) -> None:
        self.machine.require("TARGET_RUN_CORRELATED")
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
            self.machine.require("TARGET_RUN_CORRELATED")

    def _run_self_check(self, envelope: dict) -> dict:
        self._ensure_snapshot_request()
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
                        extra={"gaps": asm._gaps(envelope)})
        if chandoff.canonical_json(envelope.get("built_from") or {}) == \
                chandoff.canonical_json(confirmed.get("built_from") or {}):
            self.ledger.append({
                "kind": "refresh_publish_skipped",
                "transaction_id": self.recorder.transaction_id,
                "reason": "built_from unchanged; the confirmed record already "
                          "carries this package state",
            })
            return confirmed
        self._step_publish_refresh(envelope)
        self._step_confirm_refresh(envelope)
        return envelope

    def _step_publish_refresh(self, envelope: dict) -> None:
        try:
            result = note.publish_handoff(
                envelope, issue_id=self.issue["id"],
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
        self.machine.require("TARGET_RUN_CORRELATED")
        try:
            resolved = note.resolve_latest_handoff(
                self.issue["id"], task_ref=self.task_ref,
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

    # -- result ------------------------------------------------------------

    def _finish(self, status: str, reason: str | None,
                escalation=None, extra=None, stage: str = PHASE_FULL) -> dict:
        self._observe_assignee_after()
        tx_records = _tx_records(self.ledger, self.recorder.transaction_id)
        audit = audit_mention_ledger(tx_records, self.executable)
        mutation = self._mutation_status(status)
        if mutation is not None:
            status = mutation
            escalation = _escalation(
                "engineering-lead-or-squad", "assignee_mutation_detected")
            reason = "the issue assignee changed between the two read-only " \
                     "observations; the mention route never mutates " \
                     "assignment — fail closed"
        result = {
            "ok": status in STAGED_TERMINALS,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": self.recorder.transaction_id,
            "stage": stage,
            "mode": "simulation",
            "route": "mention",
            "terminal_status": status,
            "stop_reason": reason,
            "escalation": escalation,
            "issue": dict(self.issue) if self.issue else None,
            "task_ref": self.task_ref,
            "caller": ({"role": self.spec["caller_role"],
                        "agent_id": self.caller_agent_id}
                       if self.spec else None),
            "target": dict(self.target) if self.target else None,
            "package": ({"package_id": self.envelope["package_id"],
                         "status": self.envelope["status"]}
                        if self.envelope else None),
            "published_comment_id": self.published_comment_id,
            "handoff_ready": self.confirmed is not None
                             or self.ready_envelope is not None,
            "mention_ready_envelope": self.ready_envelope,
            "mention_evidence": (
                {k: v for k, v in self.mention_evidence.items()}
                if self.mention_evidence else None),
            "run_evidence": self.run_evidence,
            "trigger": {
                "type": "mention",
                "count": len([r for r in tx_records
                              if r.get("kind") == "mention_outcome"]),
                "confirmed": self.mention_evidence is not None,
                "runs": len([r for r in tx_records
                             if r.get("kind") == "run_outcome"]),
            },
            "assignee": {
                "before": self.assignee_before,
                "after": self.assignee_after,
                "unchanged": _assignee_equal(self.assignee_before,
                                             self.assignee_after),
            },
            "self_check": self.self_check_evidence or (extra or {}).get("self_check"),
            "transitions": (self.machine.transitions
                            if self.machine is not None else ["INIT"]),
            "compose_fn_source": "injected",
            "context": dict(self.spec),
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
            "stage": stage,
            "result": result,
        })
        return result

    def _observe_assignee_after(self) -> None:
        if self.issue is None:
            return
        try:
            issue = self.dispatch_cli.issue_get(self.issue["id"])
        except Exception as exc:
            self.ledger.append({
                "kind": "assignee_observation",
                "transaction_id": self.recorder.transaction_id,
                "phase": "after",
                "issue_id": self.issue["id"],
                "verified": False,
                "error": _bounded_reason(exc),
            })
            return
        self.assignee_after = _assignee_of(issue)
        self.ledger.append({
            "kind": "assignee_observation",
            "transaction_id": self.recorder.transaction_id,
            "phase": "after",
            "issue_id": issue["id"],
            "assignee_id": issue.get("assignee_id"),
            "assignee": issue.get("assignee"),
            "verified": True,
        })

    def _mutation_status(self, status: str) -> str | None:
        if self.assignee_before is None or self.assignee_after is None:
            return None
        if self.assignee_before == self.assignee_after:
            return None
        return ASSIGNMENT_MUTATION_DETECTED

    def _standalone(self, status: str, reason: str | None) -> dict:
        tx_records = _tx_records(self.ledger, self.recorder.transaction_id)
        result = {
            "ok": status in STAGED_TERMINALS,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": self.recorder.transaction_id,
            "stage": PHASE_EXECUTE,
            "mode": "simulation",
            "route": "mention",
            "terminal_status": status,
            "stop_reason": reason,
            "escalation": None,
            "issue": None, "task_ref": None, "caller": None, "target": None,
            "package": None,
            "published_comment_id": None,
            "handoff_ready": False,
            "mention_ready_envelope": None,
            "mention_evidence": None,
            "run_evidence": None,
            "trigger": {"type": "mention", "count": 0, "confirmed": False,
                        "runs": 0},
            "assignee": {"before": None, "after": None, "unchanged": None},
            "self_check": None,
            "transitions": ["INIT"],
            "compose_fn_source": "injected",
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
            "audit": audit_mention_ledger(tx_records, self.executable),
        }
        self.ledger.append({
            "kind": "transaction_result",
            "transaction_id": self.recorder.transaction_id,
            "terminal_status": status,
            "stage": PHASE_EXECUTE,
            "result": result,
        })
        return result


def _dispatch_terminal(exc: dispatch.DispatchError) -> str:
    return {
        "forbidden_create_argv": INVALID_INPUT,
        "create_command_failed": ISSUE_UNVERIFIED,
        "create_response_invalid": ISSUE_RESPONSE_INVALID,
        "create_response_not_unassigned": ISSUE_RESPONSE_INVALID,
        "assignment_argv_invalid": ROUTE_CONFLICT,
        "assignment_command_failed": ROUTE_CONFLICT,
        "assignment_response_unconfirmable": ROUTE_CONFLICT,
    }.get(exc.code, PREPARE_FAILED)


def _evidence_terminal(exc: MentionHandoffError) -> str:
    return {
        "route_conflict": ROUTE_CONFLICT,
        "mention_evidence_rejected": MENTION_EVIDENCE_REJECTED,
        "mention_confirmation_required": MENTION_CONFIRMATION_REQUIRED,
        "run_correlation_failed": RUN_CORRELATION_FAILED,
    }.get(exc.code, PREPARE_FAILED)


def _standalone_result(transaction_id: str, status: str, reason: str | None,
                       ledger: dispatch.TransactionLedger,
                       executable: str, escalation=None, extra=None) -> dict:
    result = {
        "ok": status in STAGED_TERMINALS,
        "orchestrator": ORCHESTRATOR_VERSION,
        "transaction_id": transaction_id,
        "stage": PHASE_READY,
        "mode": "simulation",
        "route": "mention",
        "terminal_status": status,
        "stop_reason": reason,
        "escalation": escalation,
        "issue": None, "task_ref": None, "caller": None, "target": None,
        "package": None,
        "published_comment_id": None,
        "handoff_ready": False,
        "mention_ready_envelope": None,
        "mention_evidence": None,
        "run_evidence": None,
        "trigger": {"type": "mention", "count": 0, "confirmed": False,
                    "runs": 0},
        "assignee": {"before": None, "after": None, "unchanged": None},
        "self_check": None,
        "transitions": ["INIT"],
        "compose_fn_source": "injected",
        "guarantees": dict(GUARANTEES),
        "uncertainty": list(UNCERTAINTY),
        "audit": audit_mention_ledger(_tx_records(ledger, transaction_id),
                                      executable),
    }
    if extra:
        result["extra"] = extra
    ledger.append({
        "kind": "transaction_result",
        "transaction_id": transaction_id,
        "terminal_status": status,
        "stage": PHASE_READY,
        "result": result,
    })
    return result


def run_mention_handoff(spec, *, caller_role: str, target_role_spec: str,
                        runner,
                        ledger: dispatch.TransactionLedger,
                        compose_fn: Callable, transaction_id: str,
                        purpose: str = "implementation",
                        policy: dict | None = None,
                        clock: Callable = now_iso,
                        bundle_dir=None, finding_store=None,
                        world: dict | None = None,
                        workdir=None, executable: str = "multica",
                        mention_evidence=None, run_evidence=None,
                        stage: str = PHASE_FULL) -> dict:
    """Run one mention-handoff transaction (simulation default).

    stage:
      - "full"   : ready stage then execute stage in one call; the injected
                   mention/run evidence stand in for the current agent's
                   native mention and the platform's observable target run.
      - "ready"  : stop after the MENTION_READY envelope (zero trigger; the
                   current agent emits the native mention outside this
                   transaction, on its own reply surface).
      - "execute": continue a staged transaction from the ledger; requires
                   the recorded ready-stage result.

    Idempotency: a recorded final COMPLETED result for this transaction_id is
    returned as-is with zero new commands; a recorded incomplete result is
    refused (REPLAY_REFUSED) — the operator reconciles read-only from the
    ledger, and a second native mention is never authorized.
    """
    if stage not in (PHASE_FULL, PHASE_READY, PHASE_EXECUTE):
        return _standalone_result(
            transaction_id, INVALID_INPUT,
            f"stage {stage!r} is not one of full/ready/execute",
            ledger, executable)
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
    if prior is not None and prior.get("stage") == PHASE_EXECUTE:
        if prior.get("terminal_status") == COMPLETED:
            replayed = dict(prior)
            replayed["replayed"] = True
            replayed["commands"] = []
            return replayed
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "stage": PHASE_FULL,
            "mode": "simulation",
            "terminal_status": REPLAY_REFUSED,
            "stop_reason": "a prior incomplete transaction result is recorded "
                           "for this id; review the ledger and reconcile "
                           "read-only — never a second mention or re-run",
            "prior_terminal_status": prior.get("terminal_status"),
            "audit": audit_mention_ledger(_tx_records(ledger, transaction_id),
                                          executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    ready_already_staged = bool(
        prior is not None and prior.get("stage") == PHASE_READY
        and prior.get("terminal_status") == MENTION_READY)
    if prior is not None and not ready_already_staged:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "stage": stage,
            "mode": "simulation",
            "terminal_status": REPLAY_REFUSED,
            "stop_reason": "a prior incomplete transaction result is recorded "
                           "for this id; review the ledger and reconcile "
                           "read-only before starting a new transaction",
            "prior_terminal_status": prior.get("terminal_status"),
            "audit": audit_mention_ledger(_tx_records(ledger, transaction_id),
                                          executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    workdir = Path(workdir) if workdir is not None else Path.cwd()
    recorder = dispatch.RecordingRunner(runner, ledger, executable=executable,
                                        transaction_id=transaction_id)
    handoff = MentionHandoff(
        validated=validated, target_role_spec=target_role_spec,
        recorder=recorder, ledger=ledger, compose_fn=compose_fn,
        clock=clock, bundle_dir=bundle_dir, finding_store=finding_store,
        world=world, policy=policy, workdir=workdir, executable=executable)
    ready_result = None
    if ready_already_staged:
        # The ready stage already ran for this id: never re-publish or
        # re-authorize; continue with the execute stage only.
        ready_result = dict(prior)
    else:
        ready_result = handoff.run_ready()
    if stage == PHASE_READY:
        if ready_already_staged:
            return dict(prior, replayed=True, commands=[])
        return ready_result
    if ready_result.get("terminal_status") != MENTION_READY:
        return ready_result
    return handoff.run_execute(mention_evidence=mention_evidence,
                               run_evidence=run_evidence)


def run_ready_stage(spec, **kwargs) -> dict:
    """Stage one mention handoff up to the MENTION_READY envelope."""
    kwargs.pop("mention_evidence", None)
    kwargs.pop("run_evidence", None)
    kwargs["stage"] = PHASE_READY
    return run_mention_handoff(spec, **kwargs)


def run_execute_stage(ledger: dispatch.TransactionLedger, transaction_id: str,
                      *, runner, executable: str = "multica",
                      mention_evidence, run_evidence, bundle_dir=None,
                      finding_store=None, world: dict | None = None,
                      policy: dict | None = None, workdir=None,
                      clock: Callable = now_iso) -> dict:
    """Execute the staged mention handoff after the current agent emitted
    its one native mention. Revalidates everything from the ledger."""
    prior = _recorded_result(transaction_id, ledger)
    if prior is None or prior.get("terminal_status") != MENTION_READY:
        return {
            "ok": False,
            "orchestrator": ORCHESTRATOR_VERSION,
            "transaction_id": transaction_id,
            "stage": PHASE_EXECUTE,
            "mode": "simulation",
            "route": "mention",
            "terminal_status": REPLAY_REFUSED,
            "stop_reason": "no staged MENTION_READY result is recorded for "
                           "this transaction id; run the ready stage first",
            "audit": audit_mention_ledger(_tx_records(ledger, transaction_id),
                                          executable),
            "guarantees": dict(GUARANTEES),
            "uncertainty": list(UNCERTAINTY),
        }
    context = prior.get("context") or {}
    spec = {
        "issue_id": (prior.get("issue") or {}).get("id"),
        "project_id": context.get("project_id"),
        "purpose": context.get("purpose"),
        "options": context.get("options"),
        "decision_markers": context.get("decision_markers"),
    }
    caller_role = ((prior.get("caller") or {}).get("role"))
    validated = {
        "issue_id": spec["issue_id"],
        "project_id": spec["project_id"] or "",
        "purpose": spec["purpose"] or "implementation",
        "options": spec["options"],
        "decision_markers": spec["decision_markers"],
        "caller_role": caller_role,
    }
    recorder = dispatch.RecordingRunner(runner, ledger, executable=executable,
                                        transaction_id=transaction_id)
    handoff = MentionHandoff(
        validated=validated,
        target_role_spec=(prior.get("target") or {}).get("role") or "",
        recorder=recorder, ledger=ledger,
        compose_fn=_no_compose, clock=clock, bundle_dir=bundle_dir,
        finding_store=finding_store, world=world, policy=policy,
        workdir=Path(workdir) if workdir is not None else Path.cwd(),
        executable=executable)
    return handoff.run_execute(mention_evidence=mention_evidence,
                               run_evidence=run_evidence)


def _no_compose(plan_obj, request, errors=None) -> dict:  # pragma: no cover
    raise AssertionError("execute stage never re-invokes the compose step")


def acceptance_evidence(result: dict, ledger: dispatch.TransactionLedger) -> dict:
    """YAML done-criteria evidence, computed from the observable ledger."""
    tx = result.get("transaction_id")
    tx_records = _tx_records(ledger, tx)
    audit = result.get("audit") or {}
    commands = [r for r in tx_records if r.get("kind") == "command"]
    creates = [r for r in commands
               if r.get("command_class") == "issue_create"]
    assigns = [r for r in commands
               if r.get("command_class") == "assignment_trigger"]
    confirms = [r for r in tx_records if r.get("kind") == "state_transition"
                and r.get("to") == "HANDOFF_READY_CONFIRMED"]
    mention_ready = [r for r in tx_records if r.get("kind") == "mention_ready"]
    mention_outcomes = [r for r in tx_records
                        if r.get("kind") == "mention_outcome"]
    run_outcomes = [r for r in tx_records if r.get("kind") == "run_outcome"]
    self_check = result.get("self_check") or {}
    assignee = result.get("assignee") or {}
    completed = result.get("terminal_status") == COMPLETED
    seq_of = lambda r: int(r.get("seq") or 0)  # noqa: E731
    trigger_type = (result.get("trigger") or {}).get("type")
    return {
        "existing_issue_reused": bool(
            not creates and result.get("issue") and result.get("task_ref")),
        "assignee_unchanged": assignee.get("unchanged", False)
        if assignee else False,
        "handoff_confirmed_before_mention": bool(
            confirms and mention_outcomes
            and all(seq_of(m) > seq_of(c) for m in mention_outcomes
                    for c in confirms)),
        "mention_is_only_trigger": bool(
            trigger_type == "mention"
            and not audit.get("mention_hits")
            and not audit.get("unexpected_write_classes")
            and audit.get("assignment_trigger", {}).get("count", 0) == 0
            and audit.get("issue_create", {}).get("count", 0) == 0),
        "intended_mention_count_per_handoff": (
            len(mention_outcomes) if completed else 0),
        "intended_run_count_per_handoff": (
            len(run_outcomes) if completed else 0),
        "assignment_not_used": bool(
            audit.get("assignment_trigger", {}).get("count", 0) == 0
            and not audit.get("unexpected_write_classes")),
        "assignment_plus_mention_rejected": bool(
            completed and not any(
                r.get("kind") in ("assignment_trigger", "trigger_outcome")
                for r in tx_records)
            and (result.get("mention_evidence") or {}).get("accepted")),
        "target_self_check_ready_before_work": bool(
            result.get("terminal_status") not in STAGED_TERMINALS
            or (self_check.get("status") == "READY")),
        "retry_does_not_duplicate_trigger": bool(
            result.get("replayed") is True and result.get("commands") == []
            or result.get("replayed") is None),
        "ambiguous_trigger_response_fails_closed": bool(
            result.get("terminal_status") != MENTION_CONFIRMATION_REQUIRED
            or (result.get("trigger") or {}).get("confirmed") is False),
        "adapter_constructs_mention_markdown": False,
        "live_mutations": 0 if result.get("mode") == "simulation" else 1,
    }


def replay_transaction(transaction_id: str,
                       ledger: dispatch.TransactionLedger) -> dict:
    """Read-only replay of the recorded transaction result (operator tool)."""
    prior = _recorded_result(transaction_id, ledger)
    if prior is None:
        raise dispatch.LedgerError("no recorded transaction result",
                                   transaction_id=transaction_id)
    return {"transaction_id": transaction_id,
            "terminal_status": prior.get("terminal_status"),
            "stage": prior.get("stage"),
            "result": prior}


def _deterministic_compose(plan_obj: dict, request: dict, errors=None) -> dict:
    return compose.subset_result(plan_obj)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T10 Mention Handoff path orchestrator (simulation)")
    sub = parser.add_subparsers(dest="command", required=True)

    def ready_p(p):
        p.add_argument("--spec-file", required=True)
        p.add_argument("--fixture-runner-file", required=True,
                       help="canned response table JSON for the simulation runner")
        p.add_argument("--transaction-id", required=True)
        p.add_argument("--caller-role", required=True)
        p.add_argument("--target-role", required=True)
        p.add_argument("--policy-file", default=None)
        p.add_argument("--ledger-file", default=None)
        p.add_argument("--bundle-dir", default=None)
        p.add_argument("--executable", default="multica")

    run_parser = sub.add_parser(
        "run", help="run the full mention handoff (ready + execute) with "
                    "injected evidence files")
    ready_p(run_parser)
    run_parser.add_argument("--mention-evidence-file", required=True)
    run_parser.add_argument("--run-evidence-file", required=True)

    ready_parser = sub.add_parser(
        "ready", help="stage the handoff up to the MENTION_READY envelope "
                      "(zero trigger)")
    ready_p(ready_parser)

    exec_parser = sub.add_parser(
        "execute", help="continue a staged transaction from its ledger after "
                        "the current agent emitted the native mention")
    exec_parser.add_argument("--ledger-file", required=True)
    exec_parser.add_argument("--transaction-id", required=True)
    exec_parser.add_argument("--mention-evidence-file", required=True)
    exec_parser.add_argument("--run-evidence-file", required=True)
    exec_parser.add_argument("--fixture-runner-file", required=True)
    exec_parser.add_argument("--executable", default="multica")

    val = sub.add_parser("validate-spec", help="zero-command spec validation")
    val.add_argument("--spec-file", required=True)
    val.add_argument("--caller-role", required=True)
    val.add_argument("--transaction-id", required=True)

    role = sub.add_parser("resolve-role", help="T08-constrained role resolution")
    role.add_argument("--role-spec", required=True)
    role.add_argument("--bundle-dir", default=None)

    audit_p = sub.add_parser("audit", help="audit one mention-route ledger")
    audit_p.add_argument("--ledger-file", required=True)
    audit_p.add_argument("--executable", default="multica")

    cross = sub.add_parser("cross-route-audit",
                           help="assignment/mention mutual-exclusion audit "
                                "over a shared ledger")
    cross.add_argument("--ledger-file", required=True)
    cross.add_argument("--executable", default="multica")

    replay_p2 = sub.add_parser("replay", help="read-only replay of a recorded result")
    replay_p2.add_argument("--transaction-id", required=True)
    replay_p2.add_argument("--ledger-file", required=True)
    args = parser.parse_args(argv)

    def emit(doc, exit_code):
        print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
        return exit_code

    if args.command == "resolve-role":
        try:
            target = asm.resolve_target(args.role_spec, bundle_dir=args.bundle_dir)
        except (asm.RoutingRequiredError, asm.T08BundleError) as exc:
            return emit({"ok": False, "routing_required": True,
                         "error": exc.envelope()}, 3)
        return emit({"ok": True, "target": target}, 0)
    if args.command == "validate-spec":
        try:
            spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
            _validate_spec(spec, args.caller_role, args.transaction_id,
                           compose_fn=_deterministic_compose)
        except _Stop as stop:
            return emit({"ok": False, "terminal_status": stop.status,
                         "stop_reason": stop.reason}, 2)
        return emit({"ok": True}, 0)
    if args.command == "audit":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        doc = audit_mention_ledger(ledger.records, args.executable)
        return emit(doc, 0 if doc["ok"] else 2)
    if args.command == "cross-route-audit":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        doc = cross_route_audit(ledger.records, args.executable)
        return emit(doc, 0 if doc["ok"] else 2)
    if args.command == "replay":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        return emit(replay_transaction(args.transaction_id, ledger), 0)

    if args.command == "execute":
        ledger = dispatch.TransactionLedger.load(args.ledger_file)
        fixture_table = json.loads(
            Path(args.fixture_runner_file).read_text(encoding="utf-8"))
        mention_evidence = json.loads(
            Path(args.mention_evidence_file).read_text(encoding="utf-8")) \
            if args.mention_evidence_file else None
        run_evidence = json.loads(
            Path(args.run_evidence_file).read_text(encoding="utf-8")) \
            if args.run_evidence_file else None
        fixture = dispatch.FixtureRunner(fixture_table)
        result = run_execute_stage(
            ledger, args.transaction_id, runner=fixture,
            executable=args.executable,
            mention_evidence=mention_evidence, run_evidence=run_evidence)
        print(json.dumps({"result": result}, ensure_ascii=False, indent=2,
                         sort_keys=True))
        return 0 if result.get("ok") else 2

    spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
    fixture_table = json.loads(
        Path(args.fixture_runner_file).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.policy_file).read_text(encoding="utf-8")) \
        if getattr(args, "policy_file", None) else None
    mention_evidence = json.loads(
        Path(args.mention_evidence_file).read_text(encoding="utf-8")) \
        if getattr(args, "mention_evidence_file", None) else None
    run_evidence = json.loads(
        Path(args.run_evidence_file).read_text(encoding="utf-8")) \
        if getattr(args, "run_evidence_file", None) else None

    ledger = dispatch.TransactionLedger()
    stage = PHASE_READY if args.command == "ready" else PHASE_FULL
    result = run_mention_handoff(
        spec, caller_role=args.caller_role,
        target_role_spec=args.target_role,
        runner=dispatch.FixtureRunner(fixture_table), ledger=ledger,
        compose_fn=_deterministic_compose,
        transaction_id=args.transaction_id,
        policy=policy, bundle_dir=args.bundle_dir,
        executable=args.executable,
        mention_evidence=mention_evidence, run_evidence=run_evidence,
        stage=stage)
    if args.ledger_file:
        ledger.save(args.ledger_file)
    print(json.dumps({"result": result,
                      "acceptance_evidence":
                      acceptance_evidence(result, ledger)},
                     ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
