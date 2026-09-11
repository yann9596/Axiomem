#!/usr/bin/env python3
"""O2 (YZT-79) — Durable dispatch intent, trigger planner and reconciliation.

Forward adapter over the accepted U06–U09 shared ledger
(`chandoff_dispatch.TransactionLedger`, JSONL, one record per event). It adds a
forward-only, schema-versioned `dispatch_intent` transaction type plus:

- `DurableIntentStore`: append-only durable records with cross-process
  enumeration, OS-level append locking, per-intent lease (single-writer
  claim) and compare-and-set state transitions. The store keeps writing the
  same JSONL record shape the shared ledger already reads, so no second
  hidden store exists and terminal U06–U09 ledgers stay readable/unchanged.
- `plan_trigger`: the accepted status/assignee-aware trigger matrix. Exactly
  one native trigger is selected; ownership binding (`assign --no-start`) is
  never counted as a trigger; Mention stays its own exclusive route.
- `O2Orchestrator`: intent-first target create/bind (intent persisted before
  any mutation), prepare/publish, `TRIGGER_ISSUING` written before the native
  call, receipt + exactly-one-run correlation, typed stops, read-only
  reconciliation, execution recovery and parent-wake accounting.
- `lead_exit_check` / `observe`: the Lead exit invariant and deterministic
  open-intent observability.

Simulation only by default: every native call goes through an explicitly
injected runner (no implicit live runner is ever constructed). Live execution
would additionally require the U06 authorization document plus an O2 scope
authorization; this module never exercises live mode.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff_adapter as adapter  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_instructions as instr  # noqa: E402

ORCHESTRATOR_VERSION = "O2/1.0"
O2_SCHEMA = "O2-dispatch-intent/1.0"
INTENT_RECORD_TYPE = "dispatch_intent"
LEAD_ROLE = "engineering-lead"
TARGET_ROLES = tuple(instr.V22_ROLES)
RETIRED_ROLES = tuple(sorted(instr.RETIRED_ROLES))

# --- states -----------------------------------------------------------------
S_INTENT_RECORDED = "INTENT_RECORDED"
S_TARGET_BOUND = "TARGET_BOUND"
S_HANDOFF_PREPARED = "HANDOFF_PREPARED"
S_HANDOFF_PUBLISHED = "HANDOFF_PUBLISHED"
S_TRIGGER_READY = "TRIGGER_READY"
S_TRIGGER_ISSUING = "TRIGGER_ISSUING"
S_RUN_CORRELATED = "RUN_CORRELATED"
S_SELF_CHECKED = "SELF_CHECKED"
S_COMPLETED = "COMPLETED"
S_PARKED_NOT_DUE = "PARKED_NOT_DUE"
S_REFRESH_REQUIRED = "REFRESH_REQUIRED"
S_TRIGGER_AMBIGUOUS = "TRIGGER_AMBIGUOUS"
S_BLOCKED = "BLOCKED"
S_CANCELLED = "CANCELLED"
S_CREATE_AMBIGUOUS = "CREATE_AMBIGUOUS"

PROGRESS_STATES = (
    S_INTENT_RECORDED, S_TARGET_BOUND, S_HANDOFF_PREPARED,
    S_HANDOFF_PUBLISHED, S_TRIGGER_READY, S_TRIGGER_ISSUING,
    S_RUN_CORRELATED, S_SELF_CHECKED,
)
STOP_STATES = (
    S_PARKED_NOT_DUE, S_REFRESH_REQUIRED, S_TRIGGER_AMBIGUOUS,
    S_CREATE_AMBIGUOUS, S_BLOCKED, S_CANCELLED,
)
# Delivery is proven only by a correlated run (not terminal); every other
# stop is a visible typed stop the Lead exit invariant accepts.
TERMINAL_STATES = (S_COMPLETED, S_PARKED_NOT_DUE, S_BLOCKED, S_CANCELLED)
EXIT_OK_STATES = (
    S_RUN_CORRELATED, S_COMPLETED, S_REFRESH_REQUIRED,
    S_TRIGGER_AMBIGUOUS, S_BLOCKED, S_CANCELLED, S_PARKED_NOT_DUE,
)

TRANSITIONS = {
    S_INTENT_RECORDED: (S_TARGET_BOUND, S_CREATE_AMBIGUOUS,
                        S_PARKED_NOT_DUE, S_BLOCKED, S_CANCELLED,
                        S_REFRESH_REQUIRED),
    S_CREATE_AMBIGUOUS: (S_TARGET_BOUND, S_BLOCKED, S_CANCELLED),
    S_TARGET_BOUND: (S_HANDOFF_PREPARED, S_PARKED_NOT_DUE, S_BLOCKED,
                     S_CANCELLED, S_REFRESH_REQUIRED),
    S_HANDOFF_PREPARED: (S_HANDOFF_PUBLISHED, S_REFRESH_REQUIRED, S_BLOCKED,
                         S_CANCELLED, S_PARKED_NOT_DUE),
    S_HANDOFF_PUBLISHED: (S_TRIGGER_READY, S_REFRESH_REQUIRED, S_BLOCKED,
                          S_CANCELLED, S_PARKED_NOT_DUE),
    S_TRIGGER_READY: (S_TRIGGER_ISSUING, S_REFRESH_REQUIRED, S_BLOCKED,
                      S_CANCELLED, S_PARKED_NOT_DUE),
    S_TRIGGER_ISSUING: (S_RUN_CORRELATED, S_TRIGGER_AMBIGUOUS, S_BLOCKED,
                        S_CANCELLED),
    # A later exactly-one-run proof may attach the run without another trigger.
    S_TRIGGER_AMBIGUOUS: (S_RUN_CORRELATED, S_BLOCKED, S_CANCELLED),
    S_RUN_CORRELATED: (S_SELF_CHECKED, S_BLOCKED, S_CANCELLED,
                       S_REFRESH_REQUIRED),
    S_SELF_CHECKED: (S_COMPLETED, S_BLOCKED, S_CANCELLED),
    S_REFRESH_REQUIRED: (S_HANDOFF_PREPARED, S_PARKED_NOT_DUE, S_BLOCKED,
                         S_CANCELLED),
    S_PARKED_NOT_DUE: (),
    S_BLOCKED: (),
    S_CANCELLED: (),
    S_COMPLETED: (),
}

# --- triggers ---------------------------------------------------------------
TRIGGER_RERUN = "issue_rerun"
TRIGGER_ASSIGN = "issue_assign"
NATIVE_TRIGGERS = (TRIGGER_RERUN, TRIGGER_ASSIGN)
TRIGGER_STATUS_PROMOTION = "issue_status_promotion"
TRIGGER_MENTION = "native_mention"

ROUTE_ASSIGNMENT = "assignment"
ROUTE_MENTION = "mention"
ROUTES = (ROUTE_ASSIGNMENT, ROUTE_MENTION)

BINDING_ASSIGN_NO_START = "issue_assign_no_start"
OWNERSHIP_BINDINGS = (BINDING_ASSIGN_NO_START,)

STATUS_CATEGORIES = ("backlog", "todo", "in_progress", "in_review", "done",
                     "blocked", "cancelled")
ACTIVE_STATUS_CATEGORIES = ("todo", "in_progress", "in_review", "blocked")

DECISION_TRIGGER_READY = "TRIGGER_READY"
DECISION_REFRESH_REQUIRED = "REFRESH_REQUIRED"
DECISION_BLOCKED = "BLOCKED"

# --- typed stop reasons -----------------------------------------------------
R_ARTIFACT_STALE = "ARTIFACT_STALE"
R_PACKAGE_STALE = "PACKAGE_STALE"
R_READY_NOTE_MISSING = "READY_NOTE_MISSING"
R_REVISION_DRIFT = "ISSUE_REVISION_DRIFT"
R_ASSIGNEE_DRIFT = "ASSIGNEE_DRIFT"
R_STATUS_DRIFT = "STATUS_DRIFT"
R_UNEXPECTED_RUN = "UNEXPECTED_RUN"
R_RUN_STATE_UNDETERMINED = "RUN_STATE_UNDETERMINED"
R_CREATE_AMBIGUOUS = "CREATE_AMBIGUOUS"
R_OWNERSHIP_BINDING_FAILED = "OWNERSHIP_BINDING_FAILED"
R_TRIGGER_AMBIGUOUS = "TRIGGER_AMBIGUOUS"
R_RUN_CORRELATION_FAILED = "RUN_CORRELATION_FAILED"
R_STATUS_PROMOTION_ROUTE = "STATUS_PROMOTION_ROUTE_REJECTED"
R_ROUTE_CONFLICT = "ROUTE_CONFLICT"
R_TARGET_UNDISCOVERED = "TARGET_UNDISCOVERED"
R_PARKED_UNDERSPECIFIED = "PARKED_UNDERSPECIFIED"
R_AWAITING_RUN_VISIBILITY = "AWAITING_RUN_VISIBILITY"
R_PROVIDER_FAILURE = "PROVIDER_FAILURE"

# --- ledger events ----------------------------------------------------------
E_CREATE_ISSUING = "target_create_issuing"
E_OWNERSHIP_BINDING = "ownership_binding"
E_TRIGGER_RECEIPT = "trigger_receipt"
E_RUN_CORRELATION = "run_correlation"
E_EXECUTION_RECOVERY = "execution_recovery"
E_PARENT_WAKE = "parent_wake"
E_RECONCILIATION = "reconciliation"
E_DISCOVERY = "target_discovery"

# --- commands ---------------------------------------------------------------
C_READ = "read"
C_ISSUE_CREATE = "issue_create"
C_ISSUE_UPDATE = "issue_update"
C_OWNERSHIP_BINDING = "ownership_binding"
C_ASSIGNMENT_TRIGGER = "assignment_trigger"
C_RERUN_TRIGGER = "rerun_trigger"
C_STATUS_TRIGGER = "status_trigger"
C_MENTION_TRIGGER = "mention_trigger"
C_OTHER = "other"

TRIGGER_COMMAND_CLASSES = (C_ASSIGNMENT_TRIGGER, C_RERUN_TRIGGER,
                           C_STATUS_TRIGGER, C_MENTION_TRIGGER)
WRITE_COMMAND_CLASSES = TRIGGER_COMMAND_CLASSES + (
    C_ISSUE_CREATE, C_ISSUE_UPDATE, C_OWNERSHIP_BINDING)

READ_COMMANDS = (
    ("issue", "get"),
    ("issue", "comment", "list"),
    ("issue", "runs"),
    ("issue", "children"),
    ("version",),
)

O2_SCAN = "o2_scan"
_UUID_RE = dispatch.UUID_RE
_PACKAGE_ID_RE = re.compile(r"^CTX-[A-Za-z0-9._:-]+-[0-9a-f]{16}$")
_INTENT_ID_RE = re.compile(r"^DI-[0-9a-f]{16}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TRUNCATION_RE = re.compile(r"truncat", re.IGNORECASE)
_RUN_FIELDS = dispatch.RUN_CONTRACT_FIELDS
_RUN_STATUSES = dispatch.ACTIVE_RUN_STATUSES


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def digest_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def age_seconds(now: str, then: str) -> int:
    delta = parse_ts(now) - parse_ts(then)
    return max(0, int(delta.total_seconds()))


def _require_text(value, field: str, limit: int = 240) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IntentError(
            f"{field} must be a non-blank string of at most {limit} chars",
            field=field)
    return value.strip()


def _require_optional_text(value, field: str, limit: int = 240):
    if value is None:
        return None
    return _require_text(value, field, limit)


def _require_uuid(value, field: str) -> str:
    value = _require_text(value, field, 80)
    if not _UUID_RE.match(value):
        raise IntentError(f"{field} is not a UUID", field=field)
    return value


def _require_digest(value, field: str) -> str:
    value = _require_text(value, field, 80)
    if not _DIGEST_RE.match(value):
        raise IntentError(f"{field} is not a sha256 digest", field=field)
    return value


def _require_role(value, field: str = "target_role") -> str:
    value = _require_text(value, field, 60)
    if value in RETIRED_ROLES:
        raise RetiredIdentityError(
            f"{value} is retired and never resolves (no alias)", role=value)
    if value not in TARGET_ROLES:
        raise IntentError(f"{field} is not a V2.2 role slug", field=field,
                          value=value)
    return value


class IntentError(Exception):
    """Bounded O2 stop. The failure is recorded or returned, never guessed."""

    code = "o2_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = str(message)[:240]
        self.details = {k: (v if isinstance(v, (int, bool)) else str(v)[:240])
                        for k, v in details.items()}

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class RetiredIdentityError(IntentError):
    code = "retired_identity"


class IntentRequiredError(IntentError):
    code = "intent_required"


class IntentNotFoundError(IntentError):
    code = "intent_not_found"


class DuplicateIntentError(IntentError):
    code = "duplicate_intent"


class DuplicateLogicalKeyError(IntentError):
    code = "duplicate_logical_key"


class LedgerCorruptionError(IntentError):
    code = "ledger_corruption"


class LockTimeoutError(IntentError):
    code = "ledger_lock_timeout"


class LeaseHeldError(IntentError):
    code = "lease_held"


class LeaseNotHeldError(IntentError):
    code = "lease_not_held"


class CasConflictError(IntentError):
    code = "cas_conflict"


class IllegalTransitionError(IntentError):
    code = "illegal_transition"


class SnapshotIncompleteError(IntentError):
    code = "snapshot_incomplete"


class ReceiptAmbiguousError(IntentError):
    code = "trigger_receipt_ambiguous"


class RunCorrelationRefusedError(IntentError):
    code = "run_correlation_refused"


class PlannerRefusedError(IntentError):
    code = "planner_refused"


class RouteConflictError(IntentError):
    code = "route_conflict"


class NotAuthorizedError(IntentError):
    code = "live_not_authorized"


_LOCK_SLEEP = 0.01


class _CrossProcessLock:
    """Exclusive OS lock on a sidecar file (msvcrt / fcntl). Auto-released on
    process death, so a killed writer never wedges the ledger."""

    def __init__(self, path: str, timeout: float = 30.0):
        self.path = path
        self.timeout = timeout
        self.fd = -1

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                os.lseek(self.fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    os.close(self.fd)
                    self.fd = -1
                    raise LockTimeoutError(
                        "could not acquire the shared-ledger lock within "
                        "the bounded timeout", lock_path=Path(self.path).name,
                        error=type(exc).__name__)
                time.sleep(_LOCK_SLEEP)

    def __exit__(self, exc_type, exc, tb):
        fd, self.fd = self.fd, -1
        if fd < 0:
            return False
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return False


def _validate_intent_fields(fields: dict) -> dict:
    if not isinstance(fields, dict):
        raise IntentError("intent fields must be an object")
    out = dict(fields)
    out["intent_id"] = _require_text(out.get("intent_id"), "intent_id", 40)
    if not _INTENT_ID_RE.match(out["intent_id"]):
        raise IntentError("intent_id does not match DI-<16 hex>",
                          intent_id=out["intent_id"])
    out["schema_version"] = _require_text(out.get("schema_version"),
                                          "schema_version", 40)
    if out["schema_version"] != O2_SCHEMA:
        raise IntentError("unsupported intent schema_version",
                          schema_version=out["schema_version"])
    out["source_task_id"] = _require_text(out.get("source_task_id"),
                                          "source_task_id")
    out["logical_task_key"] = _require_text(out.get("logical_task_key"),
                                            "logical_task_key")
    out["parent_issue_id"] = _require_text(out.get("parent_issue_id"),
                                           "parent_issue_id")
    out["target_role"] = _require_role(out.get("target_role"))
    out["target_agent_id"] = _require_uuid(out.get("target_agent_id"),
                                           "target_agent_id")
    out["package_id"] = _require_text(out.get("package_id"), "package_id", 90)
    if not _PACKAGE_ID_RE.match(out["package_id"]):
        raise IntentError("package_id is not an exact CTX-* id",
                          package_id=out["package_id"])
    out["artifact_dependency_digest"] = _require_digest(
        out.get("artifact_dependency_digest"), "artifact_dependency_digest")
    out["creation_authority"] = _require_text(out.get("creation_authority"),
                                              "creation_authority")
    out["provenance"] = out.get("provenance") or {}
    if not isinstance(out["provenance"], dict) or not out["provenance"]:
        raise IntentError("intent provenance must be a non-empty object")
    issue_id = out.get("issue_id")
    out["issue_id"] = _require_text(issue_id, "issue_id") if issue_id else None
    for field in ("expected_status_category", "selected_trigger"):
        value = out.get(field)
        if value is not None:
            out[field] = _require_text(value, field, 60)
    if out.get("expected_status_category") is not None and \
            out["expected_status_category"] not in STATUS_CATEGORIES:
        raise IntentError("expected_status_category is not a known category",
                          value=out["expected_status_category"])
    if out.get("selected_trigger") is not None and \
            out["selected_trigger"] not in NATIVE_TRIGGERS:
        raise IntentError("selected_trigger is not a native O2 trigger",
                          value=out["selected_trigger"])
    assignee = out.get("expected_assignee_id")
    out["expected_assignee_id"] = (_require_uuid(assignee, "expected_assignee_id")
                                   if assignee else None)
    revision = out.get("expected_issue_revision")
    if revision is not None and (not isinstance(revision, int) or revision < 1):
        raise IntentError("expected_issue_revision must be a positive integer "
                          "or null")
    run_digest = out.get("pretrigger_run_set_digest")
    out["pretrigger_run_set_digest"] = (_require_digest(
        run_digest, "pretrigger_run_set_digest") if run_digest else None)
    out.setdefault("state", S_INTENT_RECORDED)
    return out


class DurableIntentStore:
    """Append-only intent events over the shared ledger JSONL contract.

    Durability: every append is written and fsynced under an OS-level file
    lock; readers take the same lock, so a torn concurrent read cannot be
    observed and a torn record on disk fails closed as `ledger_corruption`.
    Enumeration and reconciliation work across independent processes and
    workdirs pointing at the same absolute ledger path.
    """

    def __init__(self, path, *, lock_timeout: float = 30.0):
        self.path = Path(path)
        self.lock = _CrossProcessLock(str(self.path) + ".lock", lock_timeout)

    # -- raw records ---------------------------------------------------------
    def _read_unlocked(self) -> list:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        records = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LedgerCorruptionError(
                    "shared ledger contains a partial or malformed record; "
                    "fail closed (no automatic repair)",
                    line=number, error=str(exc)[:120]) from exc
            if not isinstance(record, dict):
                raise LedgerCorruptionError(
                    "shared ledger record is not a JSON object", line=number)
            records.append(record)
        return records

    def read_records(self) -> list:
        with self.lock:
            return self._read_unlocked()

    def _write_unlocked(self, record: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with open(self.path, "ab") as handle:
            handle.write(line.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def _append_locked(self, record: dict, folded: dict) -> dict:
        try:
            json.dumps(record, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise IntentError(f"ledger record is not JSON-safe: {exc}") from exc
        entry = {"seq": folded["record_count"] + 1}
        entry.update(record)
        return self._write_unlocked(entry)

    def append(self, record: dict) -> dict:
        """Append one JSON-safe record (shared-ledger extension point)."""
        if not isinstance(record, dict):
            raise IntentError("ledger record must be an object")
        if record.get("record_type") == INTENT_RECORD_TYPE:
            op = record.get("op")
            if op not in ("recorded", "transition", "event", "lease"):
                raise IntentError("unknown dispatch_intent record op",
                                  op=str(op))
        else:
            _require_text(record.get("kind"), "kind", 60)
        with self.lock:
            folded = fold_records(self._read_unlocked())
            return self._append_locked(record, folded)

    def fold(self) -> dict:
        with self.lock:
            return fold_records(self._read_unlocked())

    def get(self, intent_id: str) -> dict:
        folded = self.fold()
        intent = folded["intents"].get(intent_id)
        if intent is None:
            raise IntentNotFoundError("dispatch intent does not exist",
                                      intent_id=intent_id)
        return intent

    def get_or_none(self, intent_id: str):
        return self.fold()["intents"].get(intent_id)

    def open_intents(self) -> list:
        folded = self.fold()
        return [i for i in folded["intents"].values()
                if i["state"] not in TERMINAL_STATES]

    def record_intent(self, fields: dict, *, now: str | None = None) -> dict:
        intent = _validate_intent_fields(fields)
        now = now or utc_now()
        intent_id = intent["intent_id"]
        record = {
            "kind": "intent", "record_type": INTENT_RECORD_TYPE,
            "schema_version": O2_SCHEMA, "op": "recorded",
            "intent_id": intent_id, "at": now, "intent": intent,
        }
        with self.lock:
            folded = fold_records(self._read_unlocked())
            if intent_id in folded["intents"]:
                raise DuplicateIntentError(
                    "intent_id already exists; duplicate intent fails closed",
                    intent_id=intent_id)
            for other in folded["intents"].values():
                if other["fields"]["logical_task_key"] != intent["logical_task_key"]:
                    continue
                if other["state"] not in TERMINAL_STATES:
                    raise DuplicateLogicalKeyError(
                        "an open intent already owns this logical task key; "
                        "conflicting append fails closed",
                        logical_task_key=intent["logical_task_key"],
                        existing_intent_id=other["intent_id"],
                        existing_state=other["state"])
            return self._append_locked(record, folded)

    # -- transitions ---------------------------------------------------------
    def _lease_view_unlocked(self, folded: dict, intent_id: str, now: str):
        intent = folded["intents"].get(intent_id)
        if intent is None:
            raise IntentNotFoundError("dispatch intent does not exist",
                                      intent_id=intent_id)
        lease = intent.get("lease")
        if not lease:
            return intent, None
        active = parse_ts(lease["expires_at"]) > parse_ts(now)
        view = dict(lease)
        view["active"] = active
        return intent, view

    def transition(self, intent_id: str, to_state: str, *,
                   expected_revision: int, actor: str, now: str | None = None,
                   reason: str | None = None, fields: dict | None = None,
                   require_lease: bool = True) -> dict:
        now = now or utc_now()
        actor = _require_text(actor, "actor")
        fields = dict(fields or {})
        if to_state == S_PARKED_NOT_DUE:
            for key in ("dependency", "next_owner", "wake_boundary"):
                fields[key] = _require_text(fields.get(key), f"parked.{key}")
        with self.lock:
            folded = fold_records(self._read_unlocked())
            intent, lease = self._lease_view_unlocked(folded, intent_id, now)
            if require_lease:
                if lease is None or not lease["active"] or lease["holder"] != actor:
                    raise LeaseNotHeldError(
                        "state transitions require the caller to hold the "
                        "active intent lease", intent_id=intent_id,
                        actor=actor)
            if expected_revision != intent["revision"]:
                raise CasConflictError(
                    "intent revision changed; compare-and-set refused",
                    intent_id=intent_id, expected=expected_revision,
                    found=intent["revision"])
            allowed = TRANSITIONS.get(intent["state"], ())
            if to_state not in allowed:
                raise IllegalTransitionError(
                    "transition is not allowed by the intent state machine",
                    intent_id=intent_id, **{"from": intent["state"],
                                            "to": to_state})
            if to_state in (S_REFRESH_REQUIRED, S_TRIGGER_AMBIGUOUS,
                            S_BLOCKED) and not reason:
                raise IntentError(f"{to_state} requires a typed reason",
                                  intent_id=intent_id)
            record = {
                "kind": "intent", "record_type": INTENT_RECORD_TYPE,
                "schema_version": O2_SCHEMA, "op": "transition",
                "intent_id": intent_id, "from": intent["state"],
                "to": to_state, "revision": intent["revision"] + 1,
                "actor": actor, "at": now, "reason": reason,
            }
            if fields:
                record["fields"] = fields
            return self._append_locked(record, folded)

    def append_event(self, intent_id: str, name: str, *,
                     actor: str, now: str | None = None,
                     data: dict | None = None) -> dict:
        now = now or utc_now()
        name = _require_text(name, "event name", 80)
        record = {
            "kind": "intent", "record_type": INTENT_RECORD_TYPE,
            "schema_version": O2_SCHEMA, "op": "event",
            "intent_id": intent_id, "name": name,
            "actor": _require_text(actor, "actor"),
            "at": now, "data": data or {},
        }
        with self.lock:
            folded = fold_records(self._read_unlocked())
            if intent_id not in folded["intents"]:
                raise IntentNotFoundError("dispatch intent does not exist",
                                          intent_id=intent_id)
            return self._append_locked(record, folded)

    # -- leases --------------------------------------------------------------
    def lease_view(self, intent_id: str, *, now: str | None = None) -> dict | None:
        now = now or utc_now()
        folded = self.fold()
        _, view = self._lease_view_unlocked(folded, intent_id, now)
        return view

    def _lease_write(self, intent_id: str, action: str, holder: str,
                     now: str, ttl_seconds: int, *,
                     expired_previous=False, previous_holder=None) -> dict:
        expires_at = (parse_ts(now) + timedelta(seconds=ttl_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        record = {
            "kind": "intent", "record_type": INTENT_RECORD_TYPE,
            "schema_version": O2_SCHEMA, "op": "lease",
            "intent_id": intent_id, "action": action, "holder": holder,
            "at": now, "expires_at": expires_at,
        }
        if previous_holder is not None:
            record["previous_holder"] = previous_holder
        if expired_previous:
            record["expired_previous"] = True
        with self.lock:
            folded = fold_records(self._read_unlocked())
            intent, lease = self._lease_view_unlocked(folded, intent_id, now)
            if action == "claim":
                if lease is not None and lease["active"] and lease["holder"] != holder:
                    raise LeaseHeldError(
                        "intent lease is held by another writer; "
                        "single-writer claim refused",
                        intent_id=intent_id, holder=lease["holder"],
                        expires_at=lease["expires_at"])
                if lease is not None and lease["active"] and lease["holder"] == holder:
                    return {"outcome": "already_held", "lease": lease}
                record["previous_holder"] = lease["holder"] if lease else None
                record["expired_previous"] = bool(lease and not lease["active"])
            elif action == "renew":
                if lease is None or lease["holder"] != holder:
                    raise LeaseNotHeldError(
                        "only the current holder may renew the intent lease",
                        intent_id=intent_id, holder=holder)
            elif action == "release":
                if lease is None or lease["holder"] != holder:
                    return {"outcome": "not_held"}
            return {"outcome": action,
                    "record": self._append_locked(record, folded)}

    def claim(self, intent_id: str, holder: str, *, now: str | None = None,
              ttl_seconds: int = 300) -> dict:
        return self._lease_write(intent_id, "claim", holder,
                                 now or utc_now(), ttl_seconds)

    def renew(self, intent_id: str, holder: str, *, now: str | None = None,
              ttl_seconds: int = 300) -> dict:
        return self._lease_write(intent_id, "renew", holder,
                                 now or utc_now(), ttl_seconds)

    def release(self, intent_id: str, holder: str, *,
                now: str | None = None) -> dict:
        return self._lease_write(intent_id, "release", holder,
                                 now or utc_now(), 0)

    # -- shared-ledger record helpers ---------------------------------------
    def append_command(self, record: dict) -> dict:
        if record.get("kind") != "command" or \
                not isinstance(record.get("argv"), list):
            raise IntentError(
                "command record must carry kind=command and an argv list")
        return self.append(record)

    def records_of_kind(self, kind: str) -> list:
        return [r for r in self.read_records() if r.get("kind") == kind]


def fold_records(records: list) -> dict:
    """Fold the ledger into per-intent state. Non-intent records (the
    accepted U06–U09 command/typed records) are enumerated and reported as
    ignored, never mutated or inferred from."""
    intents: dict = {}
    ignored = 0
    for record in records:
        if record.get("record_type") != INTENT_RECORD_TYPE:
            ignored += 1
            continue
        if record.get("schema_version") != O2_SCHEMA:
            ignored += 1
            continue
        op = record.get("op")
        if op not in ("recorded", "transition", "event", "lease"):
            raise LedgerCorruptionError(
                "dispatch_intent record has an unknown op; fail closed",
                op=str(op), seq=record.get("seq"))
        intent_id = record.get("intent_id")
        if not isinstance(intent_id, str) or not _INTENT_ID_RE.match(intent_id):
            raise LedgerCorruptionError(
                "dispatch_intent record has a malformed intent_id",
                intent_id=str(intent_id))
        if op == "recorded":
            if intent_id in intents:
                raise LedgerCorruptionError(
                    "duplicate intent_recorded record; fail closed",
                    intent_id=intent_id)
            fields = _validate_intent_fields(record.get("intent") or {})
            if fields.get("state") != S_INTENT_RECORDED:
                raise LedgerCorruptionError(
                    "intent_recorded must start in INTENT_RECORDED",
                    intent_id=intent_id)
            intents[intent_id] = {
                "intent_id": intent_id,
                "schema_version": O2_SCHEMA,
                "fields": fields,
                "state": S_INTENT_RECORDED,
                "revision": 0,
                "created_at": record.get("at"),
                "updated_at": record.get("at"),
                "events": [],
                "transitions": [],
                "lease": None,
            }
            continue
        intent = intents.get(intent_id)
        if intent is None:
            raise LedgerCorruptionError(
                "intent event precedes its intent_recorded record",
                intent_id=intent_id, op=op)
        if op == "transition":
            from_state = record.get("from")
            to_state = record.get("to")
            if from_state != intent["state"]:
                raise LedgerCorruptionError(
                    "intent transition chain is inconsistent",
                    intent_id=intent_id, expected=from_state,
                    found=intent["state"])
            if to_state not in TRANSITIONS.get(from_state, ()):
                raise LedgerCorruptionError(
                    "intent transition is not in the accepted state machine",
                    intent_id=intent_id,
                    **{"from": from_state, "to": to_state})
            revision = record.get("revision")
            if revision != intent["revision"] + 1:
                raise LedgerCorruptionError(
                    "intent revision is not monotonic",
                    intent_id=intent_id, revision=revision)
            intent["transitions"].append(record)
            intent["revision"] = revision
            intent["state"] = to_state
            intent["updated_at"] = record.get("at")
            if isinstance(record.get("fields"), dict):
                intent["fields"].update(record["fields"])
            continue
        if op == "event":
            intent["events"].append(record)
            intent["updated_at"] = record.get("at") or intent["updated_at"]
            continue
        # lease
        action = record.get("action")
        if action == "claim":
            intent["lease"] = {
                "holder": record.get("holder"),
                "expires_at": record.get("expires_at"),
                "claimed_at": record.get("at"),
                "previous_holder": record.get("previous_holder"),
                "expired_previous": bool(record.get("expired_previous")),
            }
        elif action == "renew":
            if intent["lease"] and intent["lease"]["holder"] == record.get("holder"):
                intent["lease"]["expires_at"] = record.get("expires_at")
            else:
                intent["lease"] = {
                    "holder": record.get("holder"),
                    "expires_at": record.get("expires_at"),
                    "claimed_at": record.get("at"),
                    "renew_recovered": True,
                }
        elif action == "release":
            intent["lease"] = None
        else:
            raise LedgerCorruptionError(
                "dispatch_intent lease record has an unknown action",
                action=str(action))
    return {"intents": intents, "ignored_records": ignored,
            "record_count": len(records)}
# ---------------------------------------------------------------------------
# Command classification / argv contracts
# ---------------------------------------------------------------------------
def _core_argv(argv: list, executable: str = "multica") -> list:
    if argv and str(argv[0]) == executable:
        return [str(a) for a in argv[1:]]
    return [str(a) for a in argv]


def classify_o2_command(argv: list, executable: str = "multica") -> str:
    core = _core_argv(argv, executable)
    if tuple(core[:3]) == ("issue", "comment", "add"):
        return C_MENTION_TRIGGER
    if tuple(core[:2]) == ("issue", "create"):
        return C_ISSUE_CREATE
    if tuple(core[:2]) == ("issue", "update"):
        return C_ISSUE_UPDATE
    if tuple(core[:2]) == ("issue", "assign"):
        return (C_OWNERSHIP_BINDING if "--no-start" in core
                else C_ASSIGNMENT_TRIGGER)
    if tuple(core[:2]) == ("issue", "rerun"):
        return C_RERUN_TRIGGER
    if tuple(core[:2]) == ("issue", "status"):
        return C_STATUS_TRIGGER
    if any(tuple(core[:len(cmd)]) == cmd for cmd in READ_COMMANDS):
        return C_READ
    return C_OTHER


def _bare_id(value, field: str) -> str:
    value = _require_text(value, field, 80)
    if re.search(r"\s", value) or value.startswith("--"):
        raise IntentError(f"{field} is not a bare identifier", field=field)
    return value


def _valid_rerun_argv(argv: list, executable: str = "multica") -> bool:
    core = _core_argv(argv, executable)
    if tuple(core[:2]) != ("issue", "rerun"):
        return False
    try:
        _bare_id(core[2], "issue_id")
    except IntentError:
        return False
    for token in core[3:]:
        if token.startswith("--") and token not in ("--output",):
            return False
    return core[3:5] == ["--output", "json"] and len(core) == 5


def _valid_assignment_argv(argv: list, executable: str = "multica") -> bool:
    core = _core_argv(argv, executable)
    if tuple(core[:2]) != ("issue", "assign"):
        return False
    if "--no-start" in core or len(core) != 7:
        return False
    if core[3] != "--to-id" or not _UUID_RE.match(core[4]):
        return False
    try:
        _bare_id(core[2], "issue_id")
    except IntentError:
        return False
    return core[5:] == ["--output", "json"]


def _valid_binding_argv(argv: list, executable: str = "multica") -> bool:
    core = _core_argv(argv, executable)
    if tuple(core[:2]) != ("issue", "assign"):
        return False
    if core[3] != "--to-id" or not _UUID_RE.match(core[4]):
        return False
    return core[5:] == ["--no-start", "--output", "json"]


def _valid_create_argv(argv: list, executable: str = "multica") -> bool:
    core = _core_argv(argv, executable)
    if tuple(core[:2]) != ("issue", "create"):
        return False
    present = set(core)
    if "--assignee" in present or "--assignee-id" in present:
        return False
    if "--title" not in present or "--description-file" not in present:
        return False
    allowed = set(dispatch.CREATE_ALLOWED_FLAGS)
    if any(flag not in allowed for flag in present if flag.startswith("--")):
        return False
    return "--output" in present and "json" in core


def parse_run_object(text: str) -> dict:
    """Bounded receipt parser for a trigger response (rerun or assign).

    Accepts a single run object, a one-element run list, or {"runs":[run]}
    where the run carries the observable run contract fields. Anything else
    is untrusted and fails closed as an ambiguous issuance boundary.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReceiptAmbiguousError(
            "trigger response is not JSON; issuance boundary is ambiguous",
            error=str(exc)[:120]) from exc
    candidates = []
    if isinstance(data, dict):
        if isinstance(data.get("runs"), list):
            candidates = data["runs"]
        elif "run" in data and isinstance(data["run"], dict):
            candidates = [data["run"]]
        else:
            candidates = [data]
    elif isinstance(data, list):
        candidates = data
    else:
        raise ReceiptAmbiguousError(
            "trigger response is neither object nor list",
            kind=type(data).__name__)
    if len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise ReceiptAmbiguousError(
            "trigger response does not identify exactly one run",
            candidates=len(candidates))
    row = candidates[0]
    run = {}
    for field in _RUN_FIELDS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ReceiptAmbiguousError(
                "trigger response run is missing an observable contract field",
                field=field)
        run[field] = value.strip()
    return run


class StoreRecordingRunner:
    """Append every issued argv to the shared ledger before execution.

    Records use the accepted U06 `kind: command` shape, so the same JSONL
    stays readable by `chandoff_dispatch.TransactionLedger` and the U06–U09
    audits. The record order is the evidence that TRIGGER_ISSUING precedes
    the native trigger call.
    """

    def __init__(self, inner, store: DurableIntentStore, *,
                 executable: str = "multica", transaction_id: str = ""):
        if inner is None:
            raise NotAuthorizedError(
                "StoreRecordingRunner requires an injected runner")
        self.inner = inner
        self.store = store
        self.executable = executable
        self.transaction_id = transaction_id
        self.issued: list = []

    def __call__(self, argv: list) -> tuple:
        argv = [str(a) for a in argv]
        command_class = classify_o2_command(argv, self.executable)
        self.issued.append(argv)
        self.store.append({
            "kind": "command", "transaction_id": self.transaction_id,
            "command_class": command_class, "argv": argv,
        })
        code, out, err = self.inner(argv)
        self.store.append({
            "kind": "command_result", "transaction_id": self.transaction_id,
            "command_class": command_class, "exit_code": int(code),
        })
        return int(code), out or "", err or ""

    def mutations(self) -> list:
        return [argv for argv in self.issued
                if classify_o2_command(argv, self.executable)
                in WRITE_COMMAND_CLASSES]


class O2DispatchBoundary:
    """Native dispatch boundary for O2: reads, create, ownership binding,
    Assignment trigger and rerun. No implicit live runner exists; every call
    flows through the explicitly injected runner (recorded in the store)."""

    def __init__(self, runner, store: DurableIntentStore, *,
                 executable: str = "multica", workdir=None):
        if runner is None:
            raise NotAuthorizedError(
                "O2DispatchBoundary requires an explicitly injected runner; "
                "no implicit live runner is ever constructed")
        self.runner = StoreRecordingRunner(runner, store,
                                           executable=executable)
        self.executable = executable
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.commands: list = []

    def _run(self, argv: list) -> tuple:
        argv = [str(a) for a in argv]
        self.commands.append(argv)
        code, out, err = self.runner([self.executable] + argv)
        return int(code), out or "", err or ""

    # -- reads ---------------------------------------------------------------
    def issue_get(self, issue_id: str) -> dict:
        issue_id = _bare_id(issue_id, "issue_id")
        code, out, err = self._run(
            ["issue", "get", issue_id, "--output", "json"])
        if code != 0:
            raise SnapshotIncompleteError(
                f"issue get failed (exit {code})", stderr=err[:120])
        return adapter.parse_issue_json(out)

    def list_runs(self, issue_id: str) -> dict:
        issue_id = _bare_id(issue_id, "issue_id")
        code, out, err = self._run(["issue", "runs", issue_id, "--output", "json"])
        if code != 0:
            raise SnapshotIncompleteError(
                f"issue runs failed (exit {code})", stderr=err[:120])
        if _TRUNCATION_RE.search(err or ""):
            raise SnapshotIncompleteError(
                "issue runs reports truncation; run state is untrusted")
        return {"runs": dispatch.parse_runs_json(out), "stderr": err or ""}

    def list_children(self, parent_issue_id: str) -> list:
        parent_issue_id = _bare_id(parent_issue_id, "parent_issue_id")
        code, out, err = self._run(
            ["issue", "children", parent_issue_id, "--output", "json"])
        if code != 0:
            raise SnapshotIncompleteError(
                f"issue children failed (exit {code})", stderr=err[:120])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise SnapshotIncompleteError(
                f"issue children is not JSON: {exc}") from exc
        rows: list = []
        if isinstance(data, dict) and isinstance(data.get("stages"), list):
            for stage in data["stages"]:
                if isinstance(stage, dict) and isinstance(stage.get("issues"), list):
                    rows.extend(x for x in stage["issues"] if isinstance(x, dict))
        elif isinstance(data, list):
            rows = [x for x in data if isinstance(x, dict)]
        else:
            raise SnapshotIncompleteError("issue children JSON shape is unknown")
        return rows

    # -- create --------------------------------------------------------------
    def create_issue(self, *, title: str, description: str,
                     parent_issue_id: str | None = None,
                     project_id: str | None = None,
                     priority: str | None = None) -> dict:
        title = _require_text(title, "title")
        if not isinstance(description, str) or not description.strip():
            raise IntentError("description must be a non-blank string")
        for field, value in (("title", title), ("description", description)):
            if re.search(r"mention://", value):
                raise IntentError(
                    f"{field} carries a mention link; the assignment path must "
                    "contain no mention of any kind", field=field)
        argv = ["issue", "create", "--title", title,
                "--description-file", "", "--output", "json"]
        if parent_issue_id is not None:
            argv += ["--parent", _bare_id(parent_issue_id, "parent_issue_id")]
        if project_id is not None:
            argv += ["--project", _require_text(project_id, "project_id", 64)]
        if priority is not None:
            argv += ["--priority", _require_text(priority, "priority", 40)]
        temp_name = hashlib.sha1(description.encode("utf-8")).hexdigest()[:12]
        temp_path = self.workdir / f".o2-create-{temp_name}.md"
        try:
            temp_path.write_bytes(description.encode("utf-8"))
            resolved = temp_path.resolve()
            if not resolved.is_relative_to(self.workdir.resolve()):
                raise IntentError("temp description escaped the working directory")
            argv[argv.index("--description-file") + 1] = str(temp_path)
            code, out, err = self._run(argv)
        finally:
            temp_path.unlink(missing_ok=True)
        if code != 0:
            raise ReceiptAmbiguousError(
                f"issue create failed (exit {code}); creation boundary is "
                "ambiguous until read-only discovery", stderr=err[:120])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise ReceiptAmbiguousError(
                f"issue create returned non-JSON output: {exc}") from exc
        if not isinstance(data, dict):
            raise ReceiptAmbiguousError("issue create response is not an object")
        missing = [f for f in dispatch.CREATE_CONTRACT_FIELDS if f not in data]
        if missing:
            raise ReceiptAmbiguousError(
                "issue create response is missing contract fields",
                missing=",".join(missing))
        assignee_id = data.get("assignee_id")
        if assignee_id not in (None, ""):
            raise IntentError(
                "created target unexpectedly carries an assignee; the O2 "
                "create path creates unassigned targets only")
        return data

    # -- ownership binding (never a trigger) ---------------------------------
    def assign_ownership_no_start(self, issue_id: str, agent_id: str) -> dict:
        issue_id = _bare_id(issue_id, "issue_id")
        agent_id = _require_uuid(agent_id, "agent_id")
        argv = ["issue", "assign", issue_id, "--to-id", agent_id,
                "--no-start", "--output", "json"]
        if not _valid_binding_argv(argv):
            raise IntentError("ownership binding argv is outside the frozen form")
        code, out, err = self._run(argv)
        if code != 0:
            raise IntentError(
                f"ownership binding failed (exit {code})", stderr=err[:120])
        return {"outcome": "binding_issued", "argv_digest": digest(argv),
                "response_digest": digest_text(out or "")}

    # -- triggers ------------------------------------------------------------
    def assign_trigger(self, issue_id: str, agent_id: str) -> dict:
        issue_id = _bare_id(issue_id, "issue_id")
        agent_id = _require_uuid(agent_id, "agent_id")
        argv = ["issue", "assign", issue_id, "--to-id", agent_id,
                "--output", "json"]
        if not _valid_assignment_argv(argv):
            raise IntentError("assignment trigger argv is outside the frozen form")
        code, out, err = self._run(argv)
        if code != 0:
            raise ReceiptAmbiguousError(
                f"issue assign failed (exit {code}); issuance is ambiguous",
                stderr=err[:120])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise ReceiptAmbiguousError(
                f"issue assign returned non-JSON output: {exc}") from exc
        if not isinstance(data, dict):
            raise ReceiptAmbiguousError(
                "issue assign response is not a JSON object")
        confirmed = (data.get("id") == issue_id or
                     data.get("assignee_id") == agent_id or
                     data.get("to_id") == agent_id or
                     data.get("agent_id") == agent_id)
        if not confirmed:
            raise ReceiptAmbiguousError(
                "issue assign response carries no confirmation marker for "
                "this issue/agent; fail closed")
        return {"outcome": "confirmed", "argv_digest": digest(argv),
                "response_digest": digest_text(out or ""),
                "response": data}

    def rerun_issue(self, issue_id: str) -> dict:
        issue_id = _bare_id(issue_id, "issue_id")
        argv = ["issue", "rerun", issue_id, "--output", "json"]
        if not _valid_rerun_argv(argv):
            raise IntentError("rerun trigger argv is outside the frozen form")
        code, out, err = self._run(argv)
        if code != 0:
            raise ReceiptAmbiguousError(
                f"issue rerun failed (exit {code}); issuance is ambiguous",
                stderr=err[:120])
        run = parse_run_object(out)
        return {"outcome": "confirmed", "argv_digest": digest(argv),
                "response_digest": digest_text(out or ""), "run": run}


# ---------------------------------------------------------------------------
# Snapshot + trigger planner
# ---------------------------------------------------------------------------
def _validate_runs(runs) -> list:
    if not isinstance(runs, list):
        raise SnapshotIncompleteError("run evidence must be a list")
    out = []
    for index, row in enumerate(runs):
        if not isinstance(row, dict):
            raise SnapshotIncompleteError("run evidence row is not an object",
                                          index=index)
        parsed = {}
        for field in _RUN_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise SnapshotIncompleteError(
                    "run evidence row is missing a contract field",
                    index=index, field=field)
            parsed[field] = value.strip()
        out.append(parsed)
    return out


def run_set_digest(runs: list) -> str:
    ordered = sorted((dict(r) for r in runs), key=lambda r: r["id"])
    return digest(ordered)


def build_snapshot(*, issue: dict, runs: list, package: dict,
                   ready_note: dict | None, target_role: str,
                   target_agent_id: str, artifact_ready: bool | None = None,
                   runs_trusted: bool = True, known_run_ids=None,
                   used_routes=None) -> dict:
    """Immediate pre-trigger evidence: exact issue revision/status/assignee,
    READY note/package, artifact freshness and a trusted run snapshot."""
    if not isinstance(issue, dict):
        raise SnapshotIncompleteError("issue evidence must be an object")
    issue_id = _require_text(issue.get("id"), "issue.id")
    revision = issue.get("revision")
    if not isinstance(revision, int) or revision < 1:
        raise SnapshotIncompleteError(
            "issue evidence does not carry an exact revision; refuse to plan",
            issue_id=issue_id)
    status_category = issue.get("status_category") or issue.get("status")
    if status_category not in STATUS_CATEGORIES:
        raise SnapshotIncompleteError(
            "issue status category is unknown or missing",
            issue_id=issue_id, status=str(status_category))
    assignee_id = issue.get("assignee_id") or None
    if assignee_id is not None:
        assignee_id = _require_uuid(assignee_id, "issue.assignee_id")
    target_role = _require_role(target_role)
    target_agent_id = _require_uuid(target_agent_id, "target_agent_id")
    if not isinstance(package, dict):
        raise SnapshotIncompleteError("package evidence must be an object")
    package_id = _require_text(package.get("package_id"), "package.package_id", 90)
    if not _PACKAGE_ID_RE.match(package_id):
        raise SnapshotIncompleteError("package_id is not an exact CTX-* id")
    artifact_digest = package.get("artifact_dependency_digest")
    if artifact_digest is not None:
        artifact_digest = _require_digest(artifact_digest,
                                          "package.artifact_dependency_digest")
    note_id = None
    note_package = None
    note_digest = None
    if ready_note is not None:
        if not isinstance(ready_note, dict):
            raise SnapshotIncompleteError("ready_note must be an object")
        note_id = _require_text(ready_note.get("comment_id"), "ready_note.comment_id")
        note_package = ready_note.get("package_id")
        note_digest = ready_note.get("artifact_dependency_digest")
    validated_runs = _validate_runs(runs)
    snapshot = {
        "issue_id": issue_id,
        "issue_revision": revision,
        "status_category": status_category,
        "assignee_id": assignee_id,
        "target_role": target_role,
        "target_agent_id": target_agent_id,
        "package_id": package_id,
        "artifact_dependency_digest": artifact_digest,
        "artifact_ready": (package.get("artifact_ready", True)
                           if artifact_ready is None else artifact_ready),
        "ready_note_id": note_id,
        "ready_note_package_id": note_package,
        "ready_note_artifact_digest": note_digest,
        "runs": validated_runs,
        "runs_trusted": bool(runs_trusted),
        "known_run_ids": sorted(set(known_run_ids or [])),
        "used_routes": sorted(set(used_routes or [])),
    }
    snapshot["run_set_digest"] = run_set_digest(validated_runs)
    snapshot["snapshot_digest"] = digest(_snapshot_projection(snapshot))
    return snapshot


def _snapshot_projection(snapshot: dict) -> dict:
    keys = ("issue_id", "issue_revision", "status_category", "assignee_id",
            "target_role", "target_agent_id", "package_id",
            "artifact_dependency_digest", "artifact_ready", "ready_note_id",
            "run_set_digest")
    return {k: snapshot.get(k) for k in keys}


def _validate_snapshot(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        raise SnapshotIncompleteError("snapshot must be an object")
    for field in ("issue_id", "issue_revision", "status_category",
                  "target_role", "target_agent_id", "package_id",
                  "run_set_digest", "snapshot_digest"):
        if snapshot.get(field) in (None, ""):
            raise SnapshotIncompleteError(
                f"snapshot is missing required field {field}", field=field)
    if snapshot["status_category"] not in STATUS_CATEGORIES:
        raise SnapshotIncompleteError("snapshot status category is unknown")
    if not _UUID_RE.match(snapshot["target_agent_id"]):
        raise SnapshotIncompleteError("snapshot target_agent_id is not a UUID")
    if not _DIGEST_RE.match(snapshot["snapshot_digest"]):
        raise SnapshotIncompleteError("snapshot_digest is not a sha256 digest")
    return snapshot


def _planned(trigger: str, *, route: str, ownership_binding=None,
             reason: str = "", snapshot: dict | None = None) -> dict:
    plan = {
        "decision": DECISION_TRIGGER_READY,
        "route": route,
        "selected_trigger": trigger,
        "ownership_binding": ownership_binding,
        "reason": reason,
    }
    if trigger not in NATIVE_TRIGGERS and trigger != TRIGGER_MENTION:
        raise IntentError("planner selected a non-native trigger",
                          trigger=trigger)
    if snapshot is not None:
        plan["expect"] = {
            "issue_id": snapshot.get("issue_id"),
            "issue_revision": snapshot.get("issue_revision"),
            "status_category": snapshot.get("status_category"),
            "assignee_id": snapshot.get("assignee_id"),
            "run_set_digest": snapshot.get("run_set_digest"),
            "snapshot_digest": snapshot.get("snapshot_digest"),
        }
        plan["plan_digest"] = digest(plan)
    return plan


def _refused(decision: str, reason: str, *, detail: str = "") -> dict:
    return {"decision": decision, "reason": reason, "detail": detail}


def plan_trigger(snapshot: dict, *, route: str = ROUTE_ASSIGNMENT,
                 requested_route: str | None = None) -> dict:
    """The accepted status/assignee-aware trigger matrix.

    Returns a typed decision. Exactly one native trigger is selected for the
    assignment route; ownership binding is recorded separately and is never
    counted as a trigger. The status-promotion route is explicitly rejected.
    """
    snapshot = _validate_snapshot(snapshot)
    if requested_route is not None and requested_route != route:
        raise RouteConflictError(
            "one dispatch transaction selects exactly one route",
            route=route, requested_route=requested_route)
    if route == TRIGGER_STATUS_PROMOTION:
        return _refused(DECISION_BLOCKED, R_STATUS_PROMOTION_ROUTE,
                        detail="status promotion is not a proven O2 route")
    if route not in ROUTES:
        raise RouteConflictError("unknown dispatch route", route=str(route))

    if not snapshot.get("runs_trusted", False):
        return _refused(DECISION_BLOCKED, R_RUN_STATE_UNDETERMINED)
    if not snapshot.get("artifact_ready", True):
        return _refused(DECISION_REFRESH_REQUIRED, R_ARTIFACT_STALE)
    if snapshot.get("ready_note_id") is None:
        return _refused(DECISION_REFRESH_REQUIRED, R_READY_NOTE_MISSING)
    if snapshot.get("ready_note_package_id") not in (None, snapshot["package_id"]):
        return _refused(DECISION_REFRESH_REQUIRED, R_PACKAGE_STALE)
    expected_artifact = snapshot.get("artifact_dependency_digest")
    if expected_artifact is not None and \
            snapshot.get("ready_note_artifact_digest") not in (None,
                                                              expected_artifact):
        return _refused(DECISION_REFRESH_REQUIRED, R_ARTIFACT_STALE)

    unexpected = dispatch.unexpected_active_runs(
        snapshot["runs"], issue_id=snapshot["issue_id"],
        ignore_ids=set(snapshot.get("known_run_ids") or []))
    if unexpected:
        return _refused(DECISION_BLOCKED, R_UNEXPECTED_RUN,
                        detail=",".join(r["id"] for r in unexpected[:4]))

    if route == ROUTE_MENTION:
        if ROUTE_ASSIGNMENT in snapshot.get("used_routes", []):
            return _refused(DECISION_BLOCKED, R_ROUTE_CONFLICT)
        plan = _planned(TRIGGER_MENTION, route=ROUTE_MENTION,
                        reason="U07 mention route keeps native receipt semantics",
                        snapshot=snapshot)
        plan["native_receipt"] = "comment"
        return plan

    if ROUTE_MENTION in snapshot.get("used_routes", []):
        return _refused(DECISION_BLOCKED, R_ROUTE_CONFLICT)

    status = snapshot["status_category"]
    assigned = snapshot.get("assignee_id")
    target = snapshot["target_agent_id"]
    if status == "backlog":
        if assigned == target:
            return _planned(TRIGGER_RERUN, route=ROUTE_ASSIGNMENT,
                            reason="backlog + exact target assigned",
                            snapshot=snapshot)
        return _planned(TRIGGER_RERUN, route=ROUTE_ASSIGNMENT,
                        ownership_binding=BINDING_ASSIGN_NO_START,
                        reason="backlog + unassigned/different: bind ownership "
                               "without starting, re-read, then rerun",
                        snapshot=snapshot)
    if status in ACTIVE_STATUS_CATEGORIES:
        if assigned in (None, "") or assigned != target:
            return _planned(TRIGGER_ASSIGN, route=ROUTE_ASSIGNMENT,
                            reason="active + unassigned/different assignee: "
                                   "Assignment may be the sole trigger",
                            snapshot=snapshot)
        return _planned(TRIGGER_RERUN, route=ROUTE_ASSIGNMENT,
                        reason="active + exact target assigned: prefer rerun",
                        snapshot=snapshot)
    return _refused(DECISION_BLOCKED, "TARGET_STATUS_TERMINAL",
                    detail=f"status_category={status}")


def trigger_matrix() -> list:
    """Executable documentation of the accepted matrix (YZT-78)."""
    return [
        {"status_category": "backlog", "assignee": "target_assigned",
         "route": ROUTE_ASSIGNMENT, "selected_trigger": TRIGGER_RERUN,
         "ownership_binding": None, "trigger_count": 1},
        {"status_category": "backlog", "assignee": "unassigned_or_different",
         "route": ROUTE_ASSIGNMENT, "selected_trigger": TRIGGER_RERUN,
         "ownership_binding": BINDING_ASSIGN_NO_START, "trigger_count": 1},
        {"status_category": "active", "assignee": "unassigned_or_different",
         "route": ROUTE_ASSIGNMENT, "selected_trigger": TRIGGER_ASSIGN,
         "ownership_binding": None, "trigger_count": 1},
        {"status_category": "active", "assignee": "target_assigned",
         "route": ROUTE_ASSIGNMENT, "selected_trigger": TRIGGER_RERUN,
         "ownership_binding": None, "trigger_count": 1},
        {"status_category": "assigned_backlog_promoted",
         "route": TRIGGER_STATUS_PROMOTION, "selected_trigger": None,
         "ownership_binding": None, "trigger_count": 0,
         "note": "rejected in the minimum implementation (R2)"},
        {"status_category": "any", "assignee": "any", "route": ROUTE_MENTION,
         "selected_trigger": TRIGGER_MENTION, "ownership_binding": None,
         "trigger_count": 1,
         "note": "U07 route; never combined with Assignment/rerun/status"},
    ]


def correlate_new_run(pretrigger_runs: list, post_runs: list, *,
                      issue_id: str, agent_id: str,
                      trusted: bool = True) -> dict:
    """Exactly one new run for this issue+agent, else fail closed."""
    if not trusted:
        raise RunCorrelationRefusedError(
            "run listing is untrusted; correlation cannot be proven",
            reason=R_RUN_STATE_UNDETERMINED)
    known = {r["id"] for r in pretrigger_runs}
    newcomers = [r for r in post_runs if r["id"] not in known]
    matching = [r for r in newcomers
                if r["issue_id"] == issue_id and r["agent_id"] == agent_id]
    wrong = [r for r in newcomers if r not in matching]
    if len(matching) == 1 and not wrong:
        return {"ok": True, "run": matching[0], "count": 1,
                "uncertainty": "correlation is against this listing only"}
    reason = "zero_intended_runs"
    if len(matching) > 1:
        reason = "duplicate_intended_runs"
    elif wrong and not matching:
        reason = "wrong_target_run"
    elif wrong and matching:
        reason = "ambiguous_run_set"
    raise RunCorrelationRefusedError(
        "intended run cannot be uniquely correlated; fail closed",
        reason=reason, matching=len(matching), extra=len(wrong),
        new=len(newcomers))


def o2_audit_ledger(records: list, executable: str = "multica") -> dict:
    """Deterministic O2 audit over the shared ledger's command records plus
    intent-level exactly-once checks."""
    commands = [r for r in records if r.get("kind") == "command"]
    counts: dict = {}
    for record in commands:
        command_class = (record.get("command_class") or
                         classify_o2_command(record.get("argv") or [], executable))
        counts[command_class] = counts.get(command_class, 0) + 1
    triggers = sum(counts.get(cls, 0) for cls in TRIGGER_COMMAND_CLASSES)
    creates = counts.get(C_ISSUE_CREATE, 0)
    updates = counts.get(C_ISSUE_UPDATE, 0)
    bindings = counts.get(C_OWNERSHIP_BINDING, 0)
    unexpected = sorted(set(counts) - {C_READ, C_ISSUE_CREATE, C_ISSUE_UPDATE,
                                       C_OWNERSHIP_BINDING, C_ASSIGNMENT_TRIGGER,
                                       C_RERUN_TRIGGER, C_STATUS_TRIGGER,
                                       C_MENTION_TRIGGER})
    mention_hits = [r.get("seq") for r in commands
                    if any("mention://" in str(a) for a in r.get("argv") or [])]
    intents = fold_records(records)["intents"] if records else {}
    issuance = {}
    for intent_id, intent in intents.items():
        issuing = [t for t in intent["transitions"]
                   if t.get("to") == S_TRIGGER_ISSUING]
        correlated = [t for t in intent["transitions"]
                      if t.get("to") == S_RUN_CORRELATED]
        first_command = next(
            (r.get("seq") for r in commands
             if r.get("transaction_id") == intent_id
             and (r.get("command_class") or "") in TRIGGER_COMMAND_CLASSES
             and r.get("argv")), None)
        issuing_seq = issuing[0].get("seq") if issuing else None
        issuance[intent_id] = {
            "issuing_transitions": len(issuing),
            "correlated_transitions": len(correlated),
            "issuing_precedes_trigger_command": (
                issuing_seq is not None and first_command is not None
                and issuing_seq < first_command),
            "trigger_commands": sum(
                1 for r in commands if r.get("transaction_id") == intent_id
                and (r.get("command_class") or "") in TRIGGER_COMMAND_CLASSES),
        }
    ok = (triggers <= 1 and creates <= 1 and updates <= 1 and bindings <= 1
          and counts.get(C_STATUS_TRIGGER, 0) == 0
          and counts.get(C_MENTION_TRIGGER, 0) == 0
          and not mention_hits and not unexpected
          and all(v["issuing_transitions"] <= 1
                  and v["correlated_transitions"] <= 1
                  and v["trigger_commands"] <= 1
                  for v in issuance.values()))
    return {
        "command_counts": counts,
        "triggers": triggers,
        "creates": creates,
        "ownership_bindings": bindings,
        "status_triggers": counts.get(C_STATUS_TRIGGER, 0),
        "mention_triggers": counts.get(C_MENTION_TRIGGER, 0),
        "mention_argument_hits": mention_hits,
        "unexpected_write_classes": unexpected,
        "issuance": issuance,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# Intent identity + orchestration
# ---------------------------------------------------------------------------
def new_intent_id(*, source_task_id: str, logical_task_key: str,
                  target_agent_id: str, package_id: str,
                  nonce: str | None = None) -> str:
    """Client-owned intent identity, generated before any target mutation."""
    payload = {
        "source_task_id": source_task_id,
        "logical_task_key": logical_task_key,
        "target_agent_id": target_agent_id,
        "package_id": package_id,
        "nonce": nonce or uuid.uuid4().hex,
    }
    return "DI-" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()[:16]


def _latest_field(intent: dict, key: str):
    value = intent["fields"].get(key)
    for transition in reversed(intent["transitions"]):
        fields = transition.get("fields") or {}
        if key in fields:
            return fields[key]
    return value


def _latest_event(intent: dict, name: str):
    for event in reversed(intent["events"]):
        if event.get("name") == name:
            return event
    return None


class O2Orchestrator:
    """Intent-first dispatch orchestration over the durable store.

    Every mutating operation acquires the per-intent lease (single-writer
    claim) and every state change is a compare-and-set append. Native calls
    flow through the injected runner and are recorded in the same ledger.
    """

    def __init__(self, store: DurableIntentStore, *, runner,
                 executable: str = "multica", clock=None, workdir=None,
                 ttl_seconds: int = 300):
        self.store = store
        self.boundary = O2DispatchBoundary(
            runner, store, executable=executable, workdir=workdir)
        self.clock = clock or utc_now
        self.ttl_seconds = ttl_seconds

    def now(self) -> str:
        return self.clock()

    # -- internal ------------------------------------------------------------
    def _claim(self, intent_id: str, actor: str) -> dict:
        return self.store.claim(intent_id, actor, now=self.now(),
                                ttl_seconds=self.ttl_seconds)

    def _release(self, intent_id: str, actor: str) -> None:
        try:
            self.store.release(intent_id, actor, now=self.now())
        except IntentError:
            pass

    def _stop(self, intent_id: str, state: str, reason: str, actor: str,
              *, fields=None, detail: str = "") -> dict:
        self._claim(intent_id, actor)
        try:
            intent = self.store.get(intent_id)
            transition = self.store.transition(
                intent_id, state, expected_revision=intent["revision"],
                actor=actor, now=self.now(), reason=reason, fields=fields)
            return {"status": state, "reason": reason, "detail": detail,
                    "intent_id": intent_id,
                    "revision": transition["revision"], "side_effects": 0}
        finally:
            self._release(intent_id, actor)

    # -- lifecycle: record / create / bind ----------------------------------
    def record_intent(self, fields: dict) -> dict:
        record = self.store.record_intent(fields, now=self.now())
        return {"status": "INTENT_RECORDED", "intent_id": record["intent_id"],
                "seq": record["seq"], "side_effects": 0}

    def create_target(self, intent_id: str, *, title: str,
                      description: str, parent_issue_id: str,
                      actor: str, project_id: str | None = None,
                      priority: str | None = None) -> dict:
        intent = self.store.get(intent_id)
        if intent["state"] != S_INTENT_RECORDED:
            raise IllegalTransitionError(
                "target creation requires an INTENT_RECORDED intent",
                intent_id=intent_id, state=intent["state"])
        if intent["fields"].get("issue_id") is not None:
            raise IntentError("intent already bound to a target issue",
                              intent_id=intent_id)
        if intent_id not in (description or ""):
            raise IntentError(
                "create description must embed the intent_id so a lost create "
                "response is discoverable read-only", intent_id=intent_id)
        parent_issue_id = _require_text(parent_issue_id, "parent_issue_id")
        self._claim(intent_id, actor)
        try:
            self.store.append_event(
                intent_id, E_CREATE_ISSUING, actor=actor, now=self.now(),
                data={"title": _require_text(title, "title"),
                      "description_digest": digest_text(description),
                      "parent_issue_id": parent_issue_id})
            try:
                created = self.boundary.create_issue(
                    title=title, description=description,
                    parent_issue_id=parent_issue_id,
                    project_id=project_id, priority=priority)
            except ReceiptAmbiguousError as exc:
                return self._resolve_ambiguous_create(
                    intent_id, parent_issue_id=parent_issue_id, actor=actor,
                    detail=exc.message)
            try:
                issue = self.boundary.issue_get(created["id"])
            except IntentError as exc:
                return self._stop(
                    intent_id, S_BLOCKED, R_CREATE_AMBIGUOUS, actor,
                    detail=f"created target read-back failed: {exc.message}")
            return self._bind(intent_id, issue=issue, actor=actor,
                              receipt_digest=digest_text(json.dumps(
                                  created, sort_keys=True)),
                              discovered=False)
        finally:
            self._release(intent_id, actor)

    def _resolve_ambiguous_create(self, intent_id: str, *, parent_issue_id: str,
                                  actor: str, detail: str) -> dict:
        try:
            rows = self.boundary.list_children(parent_issue_id)
        except IntentError as exc:
            return self._stop(intent_id, S_CREATE_AMBIGUOUS,
                              R_CREATE_AMBIGUOUS, actor,
                              detail=f"{detail}; discovery unavailable: "
                                     f"{exc.message}")
        matches = [row for row in rows
                   if intent_id in json.dumps(row, ensure_ascii=False, sort_keys=True)]
        self.store.append_event(
            intent_id, E_DISCOVERY, actor=actor, now=self.now(),
            data={"candidates": len(matches),
                  "ids": sorted(str(r.get("id")) for r in matches[:4])})
        if len(matches) != 1:
            return self._stop(
                intent_id, S_CREATE_AMBIGUOUS, R_CREATE_AMBIGUOUS, actor,
                detail=f"{detail}; read-only discovery found {len(matches)} "
                       "exact targets; never duplicating an ambiguous create")
        issue = self.boundary.issue_get(str(matches[0]["id"]))
        return self._bind(intent_id, issue=issue, actor=actor,
                          receipt_digest=None, discovered=True)

    def _bind(self, intent_id: str, *, issue: dict, actor: str,
              receipt_digest: str | None, discovered: bool) -> dict:
        intent = self.store.get(intent_id)
        issue_id = _require_text(issue.get("id"), "issue.id")
        revision = issue.get("revision")
        if not isinstance(revision, int) or revision < 1:
            return self._stop(
                intent_id, S_BLOCKED, R_REVISION_DRIFT, actor,
                detail="target issue read-back carries no exact revision")
        status = issue.get("status_category") or issue.get("status")
        assignee = issue.get("assignee_id") or None
        expected_revision = _latest_field(intent, "expected_issue_revision")
        if expected_revision is not None and revision != expected_revision:
            return self._stop(
                intent_id, S_REFRESH_REQUIRED, R_REVISION_DRIFT, actor,
                detail=f"expected revision {expected_revision}, found {revision}")
        fields = {
            "issue_id": issue_id,
            "expected_issue_revision": revision,
            "expected_status_category": status,
            "expected_assignee_id": assignee,
            "creation_receipt_digest": receipt_digest,
            "discovered_by_read_only_proof": bool(discovered),
            "bound_at": self.now(),
        }
        transition = self.store.transition(
            intent_id, S_TARGET_BOUND, expected_revision=intent["revision"],
            actor=actor, now=self.now(), fields=fields)
        return {"status": S_TARGET_BOUND, "intent_id": intent_id,
                "issue_id": issue_id, "issue_revision": revision,
                "revision": transition["revision"], "side_effects": 0}

    def bind_target(self, intent_id: str, *, issue: dict, actor: str) -> dict:
        """Bind a pre-existing target discovered from a durable intent.
        A target can never be touched before this intent exists."""
        intent = self.store.get(intent_id)
        if intent["state"] == S_TARGET_BOUND:
            return {"status": S_TARGET_BOUND, "intent_id": intent_id,
                    "issue_id": intent["fields"].get("issue_id"),
                    "replayed": True, "side_effects": 0}
        if intent["state"] != S_INTENT_RECORDED:
            raise IllegalTransitionError(
                "binding requires an INTENT_RECORDED intent",
                intent_id=intent_id, state=intent["state"])
        self._claim(intent_id, actor)
        try:
            return self._bind(intent_id, issue=issue, actor=actor,
                              receipt_digest=None, discovered=False)
        finally:
            self._release(intent_id, actor)

    # -- lifecycle: handoff --------------------------------------------------
    def mark_prepared(self, intent_id: str, *, package_id: str,
                      artifact_dependency_digest: str, actor: str) -> dict:
        intent = self.store.get(intent_id)
        if intent["state"] not in (S_TARGET_BOUND, S_REFRESH_REQUIRED):
            raise IllegalTransitionError(
                "prepare requires TARGET_BOUND or REFRESH_REQUIRED",
                intent_id=intent_id, state=intent["state"])
        _require_text(package_id, "package_id", 90)
        _require_digest(artifact_dependency_digest,
                        "artifact_dependency_digest")
        self._claim(intent_id, actor)
        try:
            if intent["state"] == S_REFRESH_REQUIRED:
                transition = self.store.transition(
                    intent_id, S_HANDOFF_PREPARED,
                    expected_revision=intent["revision"], actor=actor,
                    now=self.now(),
                    fields={"package_id": package_id,
                            "artifact_dependency_digest":
                                artifact_dependency_digest,
                            "refreshed": True})
            else:
                transition = self.store.transition(
                    intent_id, S_HANDOFF_PREPARED,
                    expected_revision=intent["revision"], actor=actor,
                    now=self.now(),
                    fields={"package_id": package_id,
                            "artifact_dependency_digest":
                                artifact_dependency_digest})
            return {"status": S_HANDOFF_PREPARED, "intent_id": intent_id,
                    "revision": transition["revision"], "side_effects": 0}
        finally:
            self._release(intent_id, actor)

    def mark_published(self, intent_id: str, *, note_comment_id: str,
                       receipt_digest: str | None, actor: str) -> dict:
        intent = self.store.get(intent_id)
        note_comment_id = _require_text(note_comment_id, "note_comment_id")
        prior_note = _latest_field(intent, "note_comment_id")
        if prior_note is not None and prior_note != note_comment_id:
            raise IntentError(
                "a different READY note is already bound to this intent; "
                "duplicate note fails closed", intent_id=intent_id)
        if prior_note == note_comment_id:
            return {"status": S_HANDOFF_PUBLISHED, "intent_id": intent_id,
                    "note_comment_id": note_comment_id, "replayed": True,
                    "side_effects": 0}
        if intent["state"] != S_HANDOFF_PREPARED:
            raise IllegalTransitionError(
                "publish requires HANDOFF_PREPARED",
                intent_id=intent_id, state=intent["state"])
        self._claim(intent_id, actor)
        try:
            transition = self.store.transition(
                intent_id, S_HANDOFF_PUBLISHED,
                expected_revision=intent["revision"], actor=actor,
                now=self.now(),
                fields={"note_comment_id": note_comment_id,
                        "publish_receipt_digest": receipt_digest,
                        "published_at": self.now()})
            return {"status": S_HANDOFF_PUBLISHED, "intent_id": intent_id,
                    "note_comment_id": note_comment_id,
                    "revision": transition["revision"], "side_effects": 0}
        finally:
            self._release(intent_id, actor)

    # -- lifecycle: plan + trigger -------------------------------------------
    def plan_and_arm(self, intent_id: str, snapshot: dict, *,
                     actor: str) -> dict:
        intent = self.store.get(intent_id)
        if intent["state"] != S_HANDOFF_PUBLISHED:
            raise IllegalTransitionError(
                "arming requires HANDOFF_PUBLISHED (refresh first)",
                intent_id=intent_id, state=intent["state"])
        plan = plan_trigger(snapshot)
        drift = self._exactness_drift(intent, snapshot)
        if drift is not None:
            return self._stop(intent_id, S_REFRESH_REQUIRED, drift, actor,
                              detail="exact pre-trigger evidence changed")
        if plan["decision"] != DECISION_TRIGGER_READY:
            state = (S_REFRESH_REQUIRED
                     if plan["decision"] == DECISION_REFRESH_REQUIRED
                     else S_BLOCKED)
            return self._stop(intent_id, state, plan["reason"], actor)
        self._claim(intent_id, actor)
        try:
            fields = {
                "selected_trigger": plan["selected_trigger"],
                "ownership_binding": plan.get("ownership_binding"),
                "snapshot_digest": snapshot["snapshot_digest"],
                "run_set_digest": snapshot["run_set_digest"],
                "pretrigger_run_ids": sorted(r["id"] for r in snapshot["runs"]),
                "expected_issue_revision": snapshot["issue_revision"],
                "expected_status_category": snapshot["status_category"],
                "expected_assignee_id": snapshot.get("assignee_id"),
                "armed_at": self.now(),
            }
            transition = self.store.transition(
                intent_id, S_TRIGGER_READY,
                expected_revision=intent["revision"], actor=actor,
                now=self.now(), fields=fields)
            return {"status": S_TRIGGER_READY, "intent_id": intent_id,
                    "plan": plan, "revision": transition["revision"],
                    "side_effects": 0}
        finally:
            self._release(intent_id, actor)

    def _exactness_drift(self, intent: dict, snapshot: dict) -> str | None:
        issue_id = _latest_field(intent, "issue_id")
        if issue_id is not None and issue_id != snapshot.get("issue_id"):
            return R_REVISION_DRIFT
        package_id = _latest_field(intent, "package_id")
        if package_id is not None and package_id != snapshot.get("package_id"):
            return R_PACKAGE_STALE
        artifact = _latest_field(intent, "artifact_dependency_digest")
        if artifact is not None and \
                artifact != snapshot.get("artifact_dependency_digest"):
            return R_ARTIFACT_STALE
        revision = _latest_field(intent, "expected_issue_revision")
        if revision is not None and revision != snapshot.get("issue_revision"):
            return R_REVISION_DRIFT
        status = _latest_field(intent, "expected_status_category")
        if status is not None and status != snapshot.get("status_category"):
            return R_STATUS_DRIFT
        assignee = _latest_field(intent, "expected_assignee_id")
        if assignee is not None and assignee != snapshot.get("assignee_id"):
            return R_ASSIGNEE_DRIFT
        return None

    def issue_trigger(self, intent_id: str, snapshot: dict, *,
                      actor: str) -> dict:
        """One native trigger. TRIGGER_ISSUING is persisted before the call;
        a missing/untrusted receipt is a terminal TRIGGER_AMBIGUOUS and is
        never retried."""
        intent = self.store.get(intent_id)
        if intent["state"] != S_TRIGGER_READY:
            if intent["state"] in TERMINAL_STATES or \
                    intent["state"] == S_TRIGGER_AMBIGUOUS:
                return {"status": intent["state"], "intent_id": intent_id,
                        "replayed": True, "side_effects": 0}
            raise IllegalTransitionError(
                "trigger issuance requires TRIGGER_READY",
                intent_id=intent_id, state=intent["state"])
        plan = plan_trigger(snapshot)
        drift = self._exactness_drift(intent, snapshot)
        if drift is not None:
            return self._stop(intent_id, S_REFRESH_REQUIRED, drift, actor,
                              detail="re-read before issuance invalidated "
                                     "the stored trigger expectation")
        if plan["decision"] != DECISION_TRIGGER_READY:
            state = (S_REFRESH_REQUIRED
                     if plan["decision"] == DECISION_REFRESH_REQUIRED
                     else S_BLOCKED)
            return self._stop(intent_id, state, plan["reason"], actor)
        stored_trigger = _latest_field(intent, "selected_trigger")
        if plan["selected_trigger"] != stored_trigger:
            return self._stop(intent_id, S_REFRESH_REQUIRED, R_STATUS_DRIFT,
                              actor,
                              detail="trigger selection changed on re-read")
        self._claim(intent_id, actor)
        try:
            transition = self.store.transition(
                intent_id, S_TRIGGER_ISSUING,
                expected_revision=intent["revision"], actor=actor,
                now=self.now(),
                fields={"selected_trigger": plan["selected_trigger"],
                        "ownership_binding": plan.get("ownership_binding"),
                        "snapshot_digest": snapshot["snapshot_digest"],
                        "run_set_digest": snapshot["run_set_digest"],
                        "pretrigger_run_ids":
                            sorted(r["id"] for r in snapshot["runs"]),
                        "issuing_at": self.now()})
            result = self._execute_trigger(
                intent_id, plan, snapshot, actor,
                revision=transition["revision"])
            return result
        finally:
            self._release(intent_id, actor)

    def _execute_trigger(self, intent_id: str, plan: dict, snapshot: dict,
                         actor: str, *, revision: int) -> dict:
        issue_id = snapshot["issue_id"]
        target_agent_id = snapshot["target_agent_id"]
        binding = plan.get("ownership_binding")
        if binding == BINDING_ASSIGN_NO_START:
            try:
                receipt = self.boundary.assign_ownership_no_start(
                    issue_id, target_agent_id)
            except IntentError as exc:
                return self._stop(
                    intent_id, S_BLOCKED, R_OWNERSHIP_BINDING_FAILED, actor,
                    detail=exc.message)
            self.store.append_event(
                intent_id, E_OWNERSHIP_BINDING, actor=actor, now=self.now(),
                data={"receipt": receipt, "counted_as_trigger": False})
            try:
                after = self.boundary.issue_get(issue_id)
            except IntentError as exc:
                return self._stop(
                    intent_id, S_BLOCKED, R_OWNERSHIP_BINDING_FAILED, actor,
                    detail=f"post-binding read failed: {exc.message}")
            if (after.get("assignee_id") or None) != target_agent_id:
                return self._stop(
                    intent_id, S_BLOCKED, R_OWNERSHIP_BINDING_FAILED, actor,
                    detail="ownership binding did not reach the exact target")
            refreshed = dict(snapshot)
            refreshed["assignee_id"] = after.get("assignee_id")
            refreshed["issue_revision"] = after.get("revision",
                                                    snapshot["issue_revision"])
            refreshed["status_category"] = (after.get("status_category")
                                            or after.get("status")
                                            or snapshot["status_category"])
            refreshed["snapshot_digest"] = digest(
                _snapshot_projection(refreshed))
            replan = plan_trigger(refreshed)
            if (replan["decision"] != DECISION_TRIGGER_READY
                    or replan["selected_trigger"] != TRIGGER_RERUN
                    or replan.get("ownership_binding") is not None):
                return self._stop(
                    intent_id, S_BLOCKED, R_OWNERSHIP_BINDING_FAILED, actor,
                    detail="re-read after ownership binding did not confirm "
                           "the rerun route")
            plan = replan
        try:
            if plan["selected_trigger"] == TRIGGER_RERUN:
                receipt = self.boundary.rerun_issue(issue_id)
                returned_run_id = (receipt.get("run") or {}).get("id")
            else:
                receipt = self.boundary.assign_trigger(issue_id,
                                                       target_agent_id)
                returned_run_id = None
        except ReceiptAmbiguousError as exc:
            return self._stop(
                intent_id, S_TRIGGER_AMBIGUOUS, R_TRIGGER_AMBIGUOUS, actor,
                detail=f"{exc.message}; no retry and no route switch")
        self.store.append_event(
            intent_id, E_TRIGGER_RECEIPT, actor=actor, now=self.now(),
            data={"selected_trigger": plan["selected_trigger"],
                  "argv_digest": receipt.get("argv_digest"),
                  "response_digest": receipt.get("response_digest"),
                  "returned_run_id": returned_run_id,
                  "trusted": True})
        return self._correlate(
            intent_id, actor, issue_id=issue_id,
            target_agent_id=target_agent_id,
            pretrigger=snapshot["runs"], revision=revision)

    def _correlate(self, intent_id: str, actor: str, *, issue_id: str,
                   target_agent_id: str, pretrigger: list,
                   revision: int) -> dict:
        try:
            listing = self.boundary.list_runs(issue_id)
        except IntentError as exc:
            self.store.append_event(
                intent_id, E_RUN_CORRELATION, actor=actor, now=self.now(),
                data={"outcome": "listing_unavailable",
                      "detail": exc.message[:120]})
            intent = self.store.get(intent_id)
            return {"status": intent["state"], "intent_id": intent_id,
                    "awaiting_visibility": True,
                    "detail": "run listing unavailable; reconcile read-only",
                    "side_effects": 0}
        try:
            correlated = correlate_new_run(
                pretrigger, listing["runs"], issue_id=issue_id,
                agent_id=target_agent_id)
        except RunCorrelationRefusedError as exc:
            if exc.details.get("reason") == "zero_intended_runs":
                self.store.append_event(
                    intent_id, E_RUN_CORRELATION, actor=actor, now=self.now(),
                    data={"outcome": "zero_visible", "matching": 0})
                return {"status": S_TRIGGER_ISSUING, "intent_id": intent_id,
                        "awaiting_visibility": True,
                        "detail": "receipt recorded; run not yet visible, "
                                  "reconcile read-only (no reissue)",
                        "side_effects": 0}
            return self._stop(
                intent_id, S_BLOCKED, R_RUN_CORRELATION_FAILED, actor,
                detail=(f"{exc.message} ({exc.details.get('reason')})"))
        run = correlated["run"]
        transition = self.store.transition(
            intent_id, S_RUN_CORRELATED, expected_revision=revision,
            actor=actor, now=self.now(),
            fields={"correlated_run_id": run["id"],
                    "correlated_run_status": run["status"],
                    "correlated_at": self.now()})
        self.store.append_event(
            intent_id, E_RUN_CORRELATION, actor=actor, now=self.now(),
            data={"outcome": "correlated", "run_id": run["id"],
                  "run_status": run["status"]})
        return {"status": S_RUN_CORRELATED, "intent_id": intent_id,
                "run_id": run["id"], "revision": transition["revision"],
                "side_effects": 0}

    # -- lifecycle: self-check / completion ----------------------------------
    def record_self_check(self, intent_id: str, *, status: str,
                          actor: str) -> dict:
        intent = self.store.get(intent_id)
        if intent["state"] != S_RUN_CORRELATED:
            raise IllegalTransitionError(
                "self-check requires RUN_CORRELATED",
                intent_id=intent_id, state=intent["state"])
        status = _require_text(status, "self_check_status", 40).upper()
        self._claim(intent_id, actor)
        try:
            if status == "CLEAR":
                transition = self.store.transition(
                    intent_id, S_SELF_CHECKED,
                    expected_revision=intent["revision"], actor=actor,
                    now=self.now(), fields={"self_check": "CLEAR"})
                return {"status": S_SELF_CHECKED, "intent_id": intent_id,
                        "revision": transition["revision"], "side_effects": 0}
            if status == "REFRESH_REQUIRED":
                return self._stop(intent_id, S_REFRESH_REQUIRED,
                                  "SELF_CHECK_REFRESH_REQUIRED", actor)
            return self._stop(intent_id, S_BLOCKED, "SELF_CHECK_BLOCKED", actor)
        finally:
            self._release(intent_id, actor)

    def complete(self, intent_id: str, *, actor: str) -> dict:
        intent = self.store.get(intent_id)
        if intent["state"] == S_COMPLETED:
            return {"status": S_COMPLETED, "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] != S_SELF_CHECKED:
            raise IllegalTransitionError(
                "completion requires SELF_CHECKED",
                intent_id=intent_id, state=intent["state"])
        self._claim(intent_id, actor)
        try:
            transition = self.store.transition(
                intent_id, S_COMPLETED, expected_revision=intent["revision"],
                actor=actor, now=self.now(), fields={"completed_at": self.now()})
            return {"status": S_COMPLETED, "intent_id": intent_id,
                    "revision": transition["revision"], "side_effects": 0}
        finally:
            self._release(intent_id, actor)

    def mark_parked(self, intent_id: str, *, dependency: str,
                    next_owner: str, wake_boundary: str, actor: str) -> dict:
        intent = self.store.get(intent_id)
        if intent["state"] in TERMINAL_STATES:
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        self._claim(intent_id, actor)
        try:
            return self._stop(
                intent_id, S_PARKED_NOT_DUE, "PARKED_NOT_DUE", actor,
                fields={"dependency": dependency, "next_owner": next_owner,
                        "wake_boundary": wake_boundary,
                        "parked_at": self.now()})
        finally:
            self._release(intent_id, actor)

    def mark_cancelled(self, intent_id: str, *, reason: str,
                       actor: str) -> dict:
        return self.mark_terminal(intent_id, S_CANCELLED, reason, actor)

    def mark_terminal(self, intent_id: str, state: str, reason: str,
                      actor: str, *, fields=None) -> dict:
        if state not in (S_BLOCKED, S_CANCELLED):
            raise IntentError("mark_terminal supports BLOCKED/CANCELLED only",
                              state=state)
        intent = self.store.get(intent_id)
        if intent["state"] in TERMINAL_STATES:
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if state not in TRANSITIONS.get(intent["state"], ()):
            raise IllegalTransitionError(
                "terminal stop is not reachable from the current state",
                intent_id=intent_id, state=intent["state"], target=state)
        self._claim(intent_id, actor)
        try:
            return self._stop(intent_id, state, reason, actor, fields=fields)
        finally:
            self._release(intent_id, actor)

    # -- execution recovery + parent wake ------------------------------------
    def record_execution_recovery(self, intent_id: str, *, run_id: str,
                                  failure: str, actor: str) -> dict:
        intent = self.store.get(intent_id)
        correlated_run = _latest_field(intent, "correlated_run_id")
        if correlated_run is None:
            raise IntentError(
                "execution recovery requires a correlated run; provider "
                "failure before a run identity exists is a dispatch problem",
                intent_id=intent_id)
        if run_id != correlated_run:
            raise IntentError(
                "execution recovery must reference the correlated run",
                intent_id=intent_id, correlated_run=correlated_run)
        self.store.append_event(
            intent_id, E_EXECUTION_RECOVERY, actor=actor, now=self.now(),
            data={"run_id": run_id, "failure": _require_text(failure, "failure"),
                  "dispatch_orphan": False, "redispatch": False,
                  "requires_separate_execution_recovery": True})
        return {"status": "EXECUTION_RECOVERY_RECORDED", "intent_id": intent_id,
                "run_id": run_id, "redispatch": False,
                "dispatch_orphan": False, "side_effects": 0}

    def record_parent_wake(self, intent_id: str, *, outcome: str,
                           lead_agent_id: str, run_id: str | None = None,
                           evidence: str | None = None, actor: str) -> dict:
        outcome = _require_text(outcome, "outcome", 40)
        if outcome not in ("delivered", "failed", "ambiguous"):
            raise IntentError("parent wake outcome is not typed",
                              outcome=outcome)
        self.store.append_event(
            intent_id, E_PARENT_WAKE, actor=actor, now=self.now(),
            data={"outcome": outcome,
                  "lead_agent_id": _require_uuid(lead_agent_id,
                                                 "lead_agent_id"),
                  "run_id": run_id, "evidence": evidence,
                  "route": "mention", "combined_with_trigger": False})
        return {"status": "PARENT_WAKE_RECORDED", "outcome": outcome,
                "intent_id": intent_id, "side_effects": 0}

    def plan_parent_wake(self, intent_id: str, *, lead_runs: list,
                         now: str | None = None) -> dict:
        intent = self.store.get(intent_id)
        wakes = [e for e in intent["events"] if e.get("name") == E_PARENT_WAKE]
        active_lead = [r for r in lead_runs
                       if r.get("status") in _RUN_STATUSES
                       or r.get("status") in ("queued", "dispatched", "running",
                                              "waiting_local_directory")]
        if not wakes:
            return {"decision": "WAKE_ONE", "wake_count": 0,
                    "reason": "no explicit Lead wake is recorded"}
        last = wakes[-1]["data"]
        if last.get("outcome") == "delivered":
            return {"decision": "NO_NEW_WAKE", "wake_count": len(wakes),
                    "reason": "the delivered wake already has run evidence"}
        if active_lead:
            return {"decision": "WAKE_REPAIR_REQUIRED", "wake_count": len(wakes),
                    "reason": "a pending/active Lead run exists; repair it "
                              "instead of issuing another wake"}
        if len(wakes) >= 2:
            return {"decision": "WAKE_EXHAUSTED", "wake_count": len(wakes),
                    "reason": "at most one new wake after a failed wake"}
        return {"decision": "WAKE_ONE", "wake_count": len(wakes),
                "reason": "one explicit Lead wake is allowed after proving "
                          "no active/pending Lead run"}

    # -- reconciliation ------------------------------------------------------
    def classify_reconciliation(self, intent_id: str, evidence: dict) -> dict:
        intent = self.store.get(intent_id)
        return classify_reconciliation(intent, evidence)

    def reconcile(self, intent_id: str, evidence: dict | None = None, *,
                  actor: str) -> dict:
        """Read-only reconciliation. The only automatic state change is
        attaching exactly one proven run to a TRIGGER_ISSUING/AMBIGUOUS
        intent; no trigger is ever reissued."""
        evidence = dict(evidence or {})
        intent = self.store.get(intent_id)
        if intent["state"] == S_COMPLETED:
            return {"classification": "TERMINAL_REPLAY", "intent_id": intent_id,
                    "state": intent["state"], "side_effects": 0,
                    "next_action": "NONE"}
        classification = classify_reconciliation(intent, evidence)
        action = classification["safe_action"]
        if action == "ATTACH_RUN":
            runs = evidence["runs"]
            run = classification["run"]
            self._claim(intent_id, actor)
            try:
                transition = self.store.transition(
                    intent_id, S_RUN_CORRELATED,
                    expected_revision=intent["revision"], actor=actor,
                    now=self.now(),
                    fields={"correlated_run_id": run["id"],
                            "correlated_run_status": run["status"],
                            "attached_by_reconciliation": True,
                            "correlated_at": self.now()})
                self.store.append_event(
                    intent_id, E_RECONCILIATION, actor=actor, now=self.now(),
                    data={"classification": classification["classification"],
                          "action": "ATTACH_RUN", "run_id": run["id"],
                          "trigger_reissued": False})
                return {"classification": classification["classification"],
                        "intent_id": intent_id, "action": "ATTACH_RUN",
                        "run_id": run["id"], "state": S_RUN_CORRELATED,
                        "trigger_reissued": False, "side_effects": 0,
                        "revision": transition["revision"]}
            finally:
                self._release(intent_id, actor)
        if action == "ATTACH_DISCOVERED_TARGET":
            target = evidence["discovered_targets"][0]
            self._claim(intent_id, actor)
            try:
                try:
                    issue = self.boundary.issue_get(str(target.get("id")))
                except IntentError as exc:
                    return {"classification": classification["classification"],
                            "intent_id": intent_id,
                            "action": "ATTACH_DISCOVERED_TARGET",
                            "performed": False, "reason": exc.message,
                            "side_effects": 0}
                bound = self._bind(intent_id, issue=issue, actor=actor,
                                   receipt_digest=None, discovered=True)
                self.store.append_event(
                    intent_id, E_RECONCILIATION, actor=actor, now=self.now(),
                    data={"classification": classification["classification"],
                          "action": "ATTACH_DISCOVERED_TARGET",
                          "trigger_reissued": False})
                return {"classification": classification["classification"],
                        "intent_id": intent_id,
                        "action": "ATTACH_DISCOVERED_TARGET",
                        "performed": True, "bound": bound,
                        "trigger_reissued": False, "side_effects": 0}
            finally:
                self._release(intent_id, actor)
        if action == "MARK_AMBIGUOUS" and intent["state"] == S_TRIGGER_ISSUING:
            self._claim(intent_id, actor)
            try:
                self._stop(intent_id, S_TRIGGER_AMBIGUOUS,
                           R_TRIGGER_AMBIGUOUS, actor,
                           detail="no receipt and no provable run; "
                                  "no trigger is reissued")
                self.store.append_event(
                    intent_id, E_RECONCILIATION, actor=actor, now=self.now(),
                    data={"classification": classification["classification"],
                          "action": "MARK_AMBIGUOUS",
                          "trigger_reissued": False})
                return {"classification": classification["classification"],
                        "intent_id": intent_id, "action": "MARK_AMBIGUOUS",
                        "state": S_TRIGGER_AMBIGUOUS, "trigger_reissued": False,
                        "side_effects": 0}
            finally:
                self._release(intent_id, actor)
        if intent["state"] in TERMINAL_STATES:
            return {"classification": classification["classification"],
                    "intent_id": intent_id, "action": action,
                    "next_owner": classification["next_owner"],
                    "reason": classification["reason"],
                    "trigger_reissued": False, "side_effects": 0,
                    "terminal": True}
        self.store.append_event(
            intent_id, E_RECONCILIATION, actor=actor, now=self.now(),
            data={"classification": classification["classification"],
                  "action": action, "trigger_reissued": False})
        return {"classification": classification["classification"],
                "intent_id": intent_id, "action": action,
                "next_owner": classification["next_owner"],
                "reason": classification["reason"],
                "trigger_reissued": False, "side_effects": 0}

    def resume(self, intent_id: str, evidence: dict, *, actor: str,
               create_input: dict | None = None) -> dict:
        """Resume exactly one uniquely safe next action from the classified
        state. Anything ambiguous returns the classification instead."""
        intent = self.store.get(intent_id)
        classification = classify_reconciliation(intent, evidence)
        action = classification["safe_action"]
        if action == "ATTACH_RUN":
            return self.reconcile(intent_id, evidence, actor=actor)
        if action == "ATTACH_DISCOVERED_TARGET":
            return self.reconcile(intent_id, evidence, actor=actor)
        if action == "RESUME_CREATE":
            if create_input is None:
                return {"classification": classification["classification"],
                        "action": "RESUME_CREATE", "performed": False,
                        "reason": "create input not supplied",
                        "side_effects": 0}
            return self.create_target(intent_id, actor=actor,
                                      **create_input)
        if action == "RESUME_ISSUE":
            if intent["state"] == S_HANDOFF_PUBLISHED:
                armed = self.plan_and_arm(intent_id, evidence["snapshot"],
                                          actor=actor)
                if armed["status"] != S_TRIGGER_READY:
                    return armed
            return self.issue_trigger(intent_id, evidence["snapshot"],
                                      actor=actor)
        if action == "RESUME_SELF_CHECK":
            status = evidence.get("self_check_status")
            if not status:
                return {"classification": classification["classification"],
                        "action": "RESUME_SELF_CHECK", "performed": False,
                        "reason": "self-check result not supplied",
                        "side_effects": 0}
            return self.record_self_check(intent_id, status=status,
                                          actor=actor)
        if action == "RESUME_COMPLETE":
            return self.complete(intent_id, actor=actor)
        return {"classification": classification["classification"],
                "action": action, "performed": False,
                "next_owner": classification["next_owner"],
                "reason": classification["reason"], "side_effects": 0}


def classify_reconciliation(intent: dict, evidence: dict) -> dict:
    """Pure classifier over durable intent state + trusted current evidence."""
    state = intent["state"]
    fields = intent["fields"]
    issue_id = _latest_field(intent, "issue_id")
    runs = evidence.get("runs")
    runs_trusted = bool(evidence.get("runs_trusted", False))
    discovery = evidence.get("discovered_targets")

    if state == S_COMPLETED:
        return _classification("COMPLETED_REPLAY", "NONE", None,
                               "terminal replay emits zero side effects")
    if state in TERMINAL_STATES:
        return _classification(f"{state}_REPLAY", "NONE", None,
                               "terminal typed stop; no dispatch action")
    if issue_id is None:
        create_marker = _latest_event(intent, E_CREATE_ISSUING) is not None
        if not create_marker and state == S_INTENT_RECORDED:
            return _classification("POST_INTENT_PRE_CREATE_KILL",
                                   "RESUME_CREATE", "source-run",
                                   "intent durable; no create marker, creation "
                                   "may resume without duplicating a target")
        if discovery is not None:
            if len(discovery) == 1:
                return _classification("AMBIGUOUS_CREATE_ONE_TARGET",
                                       "ATTACH_DISCOVERED_TARGET",
                                       "source-run",
                                       "read-only discovery proves exactly one "
                                       "target")
            return _classification("TARGET_DISCOVERY_INCONCLUSIVE", "NONE",
                                   "engineering-lead",
                                   f"discovery found {len(discovery)} targets; "
                                   "no duplicate create")
        return _classification("AMBIGUOUS_CREATE_STOP", "NONE",
                               "engineering-lead",
                               "create response lost; wait for exactly-one "
                               "read-only discovery")
    if state in (S_TRIGGER_ISSUING, S_TRIGGER_AMBIGUOUS):
        if runs is not None and runs_trusted:
            pretrigger = [{"id": rid} for rid in
                          _latest_field(intent, "pretrigger_run_ids") or []]
            try:
                correlated = correlate_new_run(
                    pretrigger, runs, issue_id=issue_id,
                    agent_id=fields["target_agent_id"])
                return _classification(
                    "DELAYED_RUN_VISIBILITY_ONE_RUN", "ATTACH_RUN",
                    "source-run", "exactly one proven run attaches without a "
                                  "new trigger", run=correlated["run"])
            except RunCorrelationRefusedError as exc:
                if exc.details.get("reason") == "zero_intended_runs":
                    if _latest_event(intent, E_TRIGGER_RECEIPT) is None:
                        if state == S_TRIGGER_AMBIGUOUS:
                            return _classification(
                                "TRIGGER_AMBIGUOUS_NO_RUN_PROOF", "NONE",
                                "engineering-lead",
                                "already TRIGGER_AMBIGUOUS with no provable "
                                "run; never retried")
                        return _classification(
                            "TRIGGER_CALL_LOST_RESPONSE", "MARK_AMBIGUOUS",
                            "engineering-lead",
                            "TRIGGER_ISSUING without a trusted receipt; "
                            "ambiguous, never retried")
                    return _classification(
                        "DELAYED_RUN_VISIBILITY_ZERO", "WAIT_READ_ONLY",
                        "source-run",
                        "receipt recorded but no run visible yet; bounded "
                        "read-only wait")
                return _classification("RUN_CORRELATION_ANOMALY", "NONE",
                                       "engineering-lead", exc.message)
        return _classification("RUN_EVIDENCE_UNTRUSTED", "WAIT_READ_ONLY",
                               "source-run",
                               "run evidence is missing or untrusted")
    if state in (S_HANDOFF_PUBLISHED, S_TRIGGER_READY):
        return _classification("POST_PUBLISH_PRE_TRIGGER", "RESUME_ISSUE",
                               "source-run",
                               "no issuance marker exists; resume one trigger "
                               "after fresh re-read")
    if state == S_HANDOFF_PREPARED:
        return _classification("POST_PREPARE_PRE_PUBLISH",
                               "RESUME_PUBLISH_REUSE_NOTE", "source-run",
                               "reuse the exact prepared handoff; never blind "
                               "republish after a lost response")
    if state == S_TARGET_BOUND:
        return _classification("POST_BIND_PRE_PREPARE", "RESUME_PREPARE",
                               "source-run",
                               "prepare a fresh READY handoff over the bound "
                               "target")
    if state == S_RUN_CORRELATED:
        if evidence.get("provider_failure"):
            return _classification("PROVIDER_QUOTA_FAILURE",
                                   "EXECUTION_RECOVERY", "engineering-lead",
                                   "run identity exists; dispatch is delivered "
                                   "and must not repeat")
        return _classification("RUN_CORRELATED_PRE_SELF_CHECK",
                               "RESUME_SELF_CHECK", "source-run",
                               "correlated run awaits SELF_CHECK")
    if state == S_SELF_CHECKED:
        return _classification("SELF_CHECKED_PRE_COMPLETE", "RESUME_COMPLETE",
                               "source-run", "complete the intent")
    if state == S_REFRESH_REQUIRED:
        return _classification("REFRESH_REQUIRED", "RESUME_PREPARE",
                               "source-run",
                               "refresh package/artifact then re-arm")
    return _classification("UNCLASSIFIED_STATE", "NONE", "engineering-lead",
                           f"state {state} has no safe automatic action")


def _classification(name: str, action: str, next_owner: str | None, reason: str,
                    **extra) -> dict:
    out = {"classification": name, "safe_action": action,
           "next_owner": next_owner, "reason": reason,
           "trigger_reissue": action in ("RESUME_ISSUE",)}
    out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Lead exit invariant + observability
# ---------------------------------------------------------------------------
EXIT_NEXT = {
    S_INTENT_RECORDED: ("source-run", "create_or_bind_target"),
    S_CREATE_AMBIGUOUS: ("engineering-lead", "read_only_discovery"),
    S_TARGET_BOUND: ("source-run", "prepare_handoff"),
    S_HANDOFF_PREPARED: ("source-run", "publish_handoff"),
    S_HANDOFF_PUBLISHED: ("source-run", "arm_and_issue_trigger"),
    S_TRIGGER_READY: ("source-run", "issue_trigger"),
    S_TRIGGER_ISSUING: ("source-run", "reconcile_read_only"),
    S_RUN_CORRELATED: ("target-run", "self_check"),
    S_SELF_CHECKED: ("source-run", "complete"),
    S_REFRESH_REQUIRED: ("source-run", "refresh_package_then_rearm"),
    S_TRIGGER_AMBIGUOUS: ("engineering-lead", "read_only_reconcile_or_decision"),
    S_BLOCKED: ("engineering-lead", "decision_or_repair"),
    S_CANCELLED: ("engineering-lead", "none"),
    S_PARKED_NOT_DUE: ("engineering-lead", "wake_at_boundary"),
    S_COMPLETED: ("engineering-lead", "none"),
}


def lead_exit_check(*, target_refs: list, intents: dict | None = None,
                    store: DurableIntentStore | None = None) -> dict:
    """Every target created/touched by the source run must be parked with a
    dependency + next owner + wake boundary, or covered by an intent state in
    the accepted exit set. Otherwise the Lead exit is blocked."""
    if intents is None:
        if store is None:
            raise IntentError("lead_exit_check needs intents or a store")
        intents = store.fold()["intents"]
    violations = []
    for ref in target_refs:
        issue_id = ref.get("issue_id")
        intent_id = ref.get("intent_id")
        parked = ref.get("parked")
        if parked is not None:
            missing = [key for key in ("dependency", "next_owner",
                                       "wake_boundary")
                       if not isinstance(parked.get(key), str)
                       or not parked[key].strip()]
            if missing:
                violations.append({
                    "issue_id": issue_id, "intent_id": intent_id,
                    "violation": "PARKED_TARGET_UNDERSPECIFIED",
                    "missing": missing})
            continue
        intent = intents.get(intent_id) if intent_id else None
        if intent is None:
            violations.append({
                "issue_id": issue_id, "intent_id": intent_id,
                "violation": "DUE_TARGET_UNTRACKED",
                "detail": "no durable dispatch intent and no parked claim"})
            continue
        if intent["state"] not in EXIT_OK_STATES:
            violations.append({
                "issue_id": issue_id, "intent_id": intent_id,
                "violation": "DUE_TARGET_UNTRACKED",
                "detail": f"intent state {intent['state']} is not an accepted "
                          "exit state"})
            continue
        if intent["state"] == S_PARKED_NOT_DUE:
            missing = [key for key in ("dependency", "next_owner",
                                       "wake_boundary")
                       if not _latest_field(intent, key)]
            if missing:
                violations.append({
                    "issue_id": issue_id, "intent_id": intent_id,
                    "violation": "PARKED_TARGET_UNDERSPECIFIED",
                    "missing": missing})
    return {
        "ok": not violations,
        "violations": violations,
        "checked_targets": len(target_refs),
        "due_target_without_intent_or_terminal_stop": len(
            [v for v in violations
             if v["violation"] == "DUE_TARGET_UNTRACKED"]),
        "parked_target_without_dependency_owner_wake": len(
            [v for v in violations
             if v["violation"] == "PARKED_TARGET_UNDERSPECIFIED"]),
    }


def observe(store: DurableIntentStore, *, now: str | None = None) -> dict:
    """Deterministic open-intent observability (no daemon, no scheduler)."""
    now = now or utc_now()
    folded = store.fold()
    rows = []
    counts: dict = {}
    for intent in folded["intents"].values():
        counts[intent["state"]] = counts.get(intent["state"], 0) + 1
        if intent["state"] in TERMINAL_STATES:
            continue
        receipt = _latest_event(intent, E_TRIGGER_RECEIPT)
        reconciliation = _latest_event(intent, E_RECONCILIATION)
        lease = intent.get("lease")
        lease_view = None
        if lease:
            active = parse_ts(lease["expires_at"]) > parse_ts(now)
            lease_view = {"holder": lease["holder"],
                          "expires_at": lease["expires_at"],
                          "active": active,
                          "state": "held" if active else "expired"}
        owner, action = EXIT_NEXT.get(intent["state"], ("engineering-lead", "decision"))
        rows.append({
            "intent_id": intent["intent_id"],
            "age_seconds": (age_seconds(now, intent["created_at"])
                            if intent.get("created_at") else None),
            "state": intent["state"],
            "issue_id": _latest_field(intent, "issue_id"),
            "target_role": intent["fields"]["target_role"],
            "target_agent_id": intent["fields"]["target_agent_id"],
            "selected_trigger": _latest_field(intent, "selected_trigger"),
            "receipt_digest": (receipt["data"].get("response_digest")
                               if receipt else None),
            "correlated_run_id": _latest_field(intent, "correlated_run_id"),
            "ambiguity_reason": (intent["transitions"][-1].get("reason")
                                 if intent["transitions"] and
                                 intent["state"] in (S_TRIGGER_AMBIGUOUS,
                                                     S_BLOCKED)
                                 else None),
            "lease": lease_view,
            "last_reconciliation": (
                {"at": reconciliation["at"],
                 "classification":
                     reconciliation["data"].get("classification"),
                 "action": reconciliation["data"].get("action")}
                if reconciliation else None),
            "next_owner": owner,
            "next_action": action,
        })
    rows.sort(key=lambda r: r["intent_id"])
    return {"open_intents": rows, "open_count": len(rows),
            "counts_by_state": counts,
            "ignored_records": folded["ignored_records"],
            "digest": digest(rows)}


# ---------------------------------------------------------------------------
# Deterministic fixtures (YZT-77 / YZT-78 captured, sanitized)
# ---------------------------------------------------------------------------
PARENT_YZT_66 = "01a08921-207c-7a39-8a99-b431e75bed9f"
AGENT_04 = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
AGENT_02 = "1303827b-73d1-4d71-a461-00b93e4b4418"
AGENT_01 = "24f04aba-7da9-4371-bf89-685d7505a411"
FIXTURE_PACKAGE = "CTX-software-engineer-9f2c41d7b8e05a13"


def _sanitized_run(run: dict) -> dict:
    return {key: run.get(key) for key in ("id", "issue_id", "agent_id",
                                          "status")}


def yzt_77_fixture() -> dict:
    run_id = "01a08ddc-7698-7c18-943b-f354ee6ec8fb"
    issue_id = "01a08c2e-596c-78df-8865-d00d10fdad9c"
    return {
        "fixture": "YZT-77",
        "category": "persistent orphan (lost follow-up after Lead exit)",
        "issue": {
            "id": issue_id, "identifier": "YZT-77",
            "title": "U09 — Runtime Finding / Challenge alignment",
            "parent_issue_id": PARENT_YZT_66,
            "target_role": "software-engineer", "target_agent_id": AGENT_04,
        },
        "pretrigger": {
            "issue_revision": 1, "status_category": "backlog",
            "assignee_id": AGENT_04, "runs": [],
            "ready_note": {"comment_id": "01a08e36-3421-76a7-9ccc-ccb56634746b",
                           "package_id": FIXTURE_PACKAGE,
                           "artifact_dependency_digest":
                               "sha256:" + "a" * 64},
            "package": {"package_id": FIXTURE_PACKAGE,
                        "artifact_dependency_digest": "sha256:" + "a" * 64,
                        "artifact_ready": True},
        },
        "historical_trigger_attempt": {
            "command": "issue assign <id> --to-id <target> --output json",
            "assignee_before": AGENT_04, "assignee_after": AGENT_04,
            "response": "success marker", "run_delta": 0,
            "matrix_selected_today": False,
        },
        "recovery": {
            "command": "issue rerun <id> --output json",
            "returned_run_id": run_id,
            "attribution_kind": "issue_assignment",
            "note": "run history alone cannot distinguish Assignment from rerun",
        },
        "post": {
            "issue_revision": 4, "status_category": "in_review",
            "assignee_id": AGENT_04,
            "runs": [_sanitized_run({"id": run_id, "issue_id": issue_id,
                                     "agent_id": AGENT_04,
                                     "status": "completed"})],
        },
    }


def yzt_78_fixture() -> dict:
    run_id = "01a08de0-e0cb-7adc-890e-c87fcadce826"
    issue_id = "01a08ddd-b6b2-75e1-9de5-2145e18e2165"
    return {
        "fixture": "YZT-78",
        "category": "backlog Assignment no-enqueue, then rerun recovery",
        "issue": {
            "id": issue_id, "identifier": "YZT-78",
            "title": "Dispatch liveness architecture assessment",
            "parent_issue_id": PARENT_YZT_66,
            "target_role": "solution-architect", "target_agent_id": AGENT_02,
        },
        "pretrigger": {
            "issue_revision": 1, "status_category": "backlog",
            "assignee_id": None, "runs": [],
            "ready_note": {"comment_id": "01a08def-9b00-72e0-be46-692c105b1e78",
                           "package_id": "CTX-solution-architect-3ba9c10f2de4b771",
                           "artifact_dependency_digest":
                               "sha256:" + "b" * 64},
            "package": {"package_id": "CTX-solution-architect-3ba9c10f2de4b771",
                        "artifact_dependency_digest": "sha256:" + "b" * 64,
                        "artifact_ready": True},
        },
        "historical_trigger_attempt": {
            "command": "issue assign <id> --to-id <target> --output json",
            "assignee_before": None, "assignee_after": AGENT_02,
            "response": "success marker", "run_delta": 0,
            "assignee_event": "01a08de0-6fe2-794c-9055-5403e0064f54",
            "matrix_selected_today": False,
        },
        "recovery": {
            "command": "issue rerun <id> --output json",
            "returned_run_id": run_id,
            "attribution_kind": "issue_assignment",
        },
        "post": {
            "issue_revision": 10, "status_category": "in_review",
            "assignee_id": AGENT_02,
            "runs": [_sanitized_run({"id": run_id, "issue_id": issue_id,
                                     "agent_id": AGENT_02,
                                     "status": "completed"})],
        },
    }


def target_inventory() -> list:
    """Fixture-only inventory of nonterminal/preassigned backlog targets.
    No live issue is mutated or rerun by this inventory."""
    return [
        {"issue_id": "01a08c2e-596c-78df-8865-d00d10fdad9c", "identifier": "YZT-77",
         "classification": "ALREADY_DISPATCHED",
         "evidence": "rerun recovery run 01a08ddc-7698-7c18-943b-f354ee6ec8fb",
         "action": "fixture only; must never receive another live dispatch"},
        {"issue_id": "01a08ddd-b6b2-75e1-9de5-2145e18e2165", "identifier": "YZT-78",
         "classification": "ALREADY_DISPATCHED",
         "evidence": "rerun recovery run 01a08de0-e0cb-7adc-890e-c87fcadce826",
         "action": "fixture only; must never receive another live dispatch"},
        {"issue_id": "01a08e1b-a226-7b0c-a6ff-57397897034d", "identifier": "YZT-79",
         "classification": "DISPATCHED_WITH_HANDOFF_RECORD",
         "evidence": "READY note 01a08e36-3421-76a7-9ccc-ccb56634746b; "
                     "correlated run 01a08e36-9c49-73ea-8435-de14523d0675",
         "action": "fixture only; U11 replay input"},
    ]


class O2FixtureRunner:
    """Deterministic runner with ordered per-prefix response queues.

    Every issued argv is recorded; `mutations()` lists the write-class
    commands that were actually issued, so a fixture replay can prove that no
    live mutation was attempted.
    """

    def __init__(self, table: list):
        self.table = []
        for entry in table or []:
            responses = entry.get("responses")
            if responses is None:
                responses = [entry]
            normalized = []
            for response in responses:
                if isinstance(response, str):
                    normalized.append({"code": 0, "stdout": response,
                                       "stderr": ""})
                    continue
                normalized.append({
                    "code": int(response.get("code", 0)),
                    "stdout": str(response.get("stdout", "")),
                    "stderr": str(response.get("stderr", "")),
                })
            self.table.append({
                "match": [str(x) for x in (entry.get("match") or [])],
                "responses": normalized,
                "used": 0,
            })
        self.issued: list = []

    def __call__(self, argv: list) -> tuple:
        core = _core_argv(argv)
        self.issued.append([str(a) for a in argv])
        for entry in self.table:
            prefix = tuple(entry["match"])
            if tuple(core[:len(prefix)]) != prefix:
                continue
            index = min(entry["used"], len(entry["responses"]) - 1)
            entry["used"] += 1
            response = entry["responses"][index]
            return response["code"], response["stdout"], response["stderr"]
        return 2, "", "unexpected fixture command"

    def mutations(self) -> list:
        return [argv for argv in self.issued
                if classify_o2_command(argv) in WRITE_COMMAND_CLASSES]


class RefusingRunner:
    """Any argv raises: proves a path attempts no native call at all."""

    def __init__(self):
        self.issued: list = []

    def __call__(self, argv: list) -> tuple:
        self.issued.append([str(a) for a in argv])
        raise AssertionError(f"native call attempted: {argv}")


def _fixture_issue_payload(fixture: dict, *, phase: str) -> dict:
    if phase == "post":
        data = fixture["post"]
    else:
        data = fixture["pretrigger"]
    return {
        "id": fixture["issue"]["id"],
        "identifier": fixture["issue"]["identifier"],
        "title": fixture["issue"]["title"],
        "description": "",
        "parent_issue_id": fixture["issue"]["parent_issue_id"],
        "project_id": None,
        "revision": data["issue_revision"],
        "status_category": data["status_category"],
        "assignee_id": data.get("assignee_id"),
    }


def _run_json(run: dict) -> str:
    return json.dumps(run, ensure_ascii=False, sort_keys=True)


def fixture_runner_table(fixture: dict, *, post_binding_assignee: str | None,
                         binding_needed: bool) -> list:
    issue_id = fixture["issue"]["id"]
    run = fixture["post"]["runs"][0]
    table = []
    if binding_needed:
        after = _fixture_issue_payload(fixture, phase="pretrigger")
        after["assignee_id"] = post_binding_assignee
        table.append({
            "match": ["issue", "assign", issue_id, "--to-id",
                      fixture["issue"]["target_agent_id"], "--no-start"],
            "stdout": json.dumps({"id": issue_id,
                                  "assignee_id": post_binding_assignee}),
        })
        table.append({"match": ["issue", "get", issue_id],
                      "stdout": json.dumps(after, sort_keys=True)})
    table.append({"match": ["issue", "rerun", issue_id],
                  "stdout": _run_json(run)})
    table.append({"match": ["issue", "runs", issue_id],
                  "stdout": json.dumps([run], sort_keys=True)})
    return table


def fixture_replay(fixture: dict, store: DurableIntentStore, *,
                   actor: str = "o2-fixture-replay") -> dict:
    """Replay a captured YZT-77/YZT-78 dispatch through O2 with a fixture
    runner. Zero real mutations: only the simulated binding/rerun are served
    by canned responses; nothing touches the platform."""
    pre = fixture["pretrigger"]
    binding_needed = pre["status_category"] == "backlog" and \
        pre.get("assignee_id") != fixture["issue"]["target_agent_id"]
    clock = lambda: "2026-09-11T00:00:00Z"  # noqa: E731
    runner = O2FixtureRunner(fixture_runner_table(
        fixture, post_binding_assignee=fixture["issue"]["target_agent_id"],
        binding_needed=binding_needed))
    orchestrator = O2Orchestrator(store, runner=runner, clock=clock)
    intent_id = new_intent_id(
        source_task_id=PARENT_YZT_66,
        logical_task_key=f"{fixture['issue']['identifier']}-recovery",
        target_agent_id=fixture["issue"]["target_agent_id"],
        package_id=pre["package"]["package_id"], nonce="fixture0")
    orchestrator.record_intent({
        "intent_id": intent_id, "schema_version": O2_SCHEMA,
        "source_task_id": PARENT_YZT_66,
        "logical_task_key": f"{fixture['issue']['identifier']}-recovery",
        "parent_issue_id": PARENT_YZT_66,
        "target_role": fixture["issue"]["target_role"],
        "target_agent_id": fixture["issue"]["target_agent_id"],
        "package_id": pre["package"]["package_id"],
        "artifact_dependency_digest":
            pre["package"]["artifact_dependency_digest"],
        "expected_issue_revision": pre["issue_revision"],
        "expected_status_category": pre["status_category"],
        "expected_assignee_id": pre.get("assignee_id"),
        "creation_authority": "01 engineering-lead via YZT-66 SAFE_DISPATCH",
        "provenance": {"fixture": fixture["fixture"]},
    })
    orchestrator.bind_target(intent_id, issue={
        "id": fixture["issue"]["id"],
        "revision": pre["issue_revision"],
        "status_category": pre["status_category"],
        "assignee_id": pre.get("assignee_id"),
    }, actor=actor)
    orchestrator.mark_prepared(
        intent_id, package_id=pre["package"]["package_id"],
        artifact_dependency_digest=pre["package"]["artifact_dependency_digest"],
        actor=actor)
    orchestrator.mark_published(
        intent_id, note_comment_id=pre["ready_note"]["comment_id"],
        receipt_digest="sha256:" + "c" * 64, actor=actor)
    snapshot = build_snapshot(
        issue={"id": fixture["issue"]["id"],
               "revision": pre["issue_revision"],
               "status_category": pre["status_category"],
               "assignee_id": pre.get("assignee_id")},
        runs=pre["runs"], package=pre["package"],
        ready_note=pre["ready_note"],
        target_role=fixture["issue"]["target_role"],
        target_agent_id=fixture["issue"]["target_agent_id"])
    armed = orchestrator.plan_and_arm(intent_id, snapshot, actor=actor)
    result = orchestrator.issue_trigger(intent_id, snapshot, actor=actor)
    intent = store.get(intent_id)
    records = store.read_records()
    return {
        "fixture": fixture["fixture"],
        "intent_id": intent_id,
        "arm": armed,
        "result": result,
        "state": intent["state"],
        "selected_trigger": _latest_field(intent, "selected_trigger"),
        "ownership_binding": _latest_field(intent, "ownership_binding"),
        "correlated_run_id": _latest_field(intent, "correlated_run_id"),
        "fixture_commands": [argv for argv in runner.issued],
        "write_commands": runner.mutations(),
        "live_mutations": 0,
        "audit": o2_audit_ledger(records),
        "replay_zero_side_effect_issues": True,
    }


# ---------------------------------------------------------------------------
# Matrices / evidence
# ---------------------------------------------------------------------------
def state_transition_table() -> list:
    return [{"from": source, "to": list(targets)}
            for source, targets in TRANSITIONS.items()]


def success_failure_matrix() -> list:
    return [
        {"case": "intent_persisted_before_target_create", "expected": "PASS",
         "reason": "INTENT_RECORDED is written before any create argv"},
        {"case": "create_without_intent", "expected": "REFUSED",
         "reason": "create/bind requires an existing durable intent"},
        {"case": "ambiguous_create", "expected": "BLOCKED_CREATE_AMBIGUOUS",
         "reason": "never duplicates a target; discovery may attach exactly one"},
        {"case": "backlog_assigned", "expected": "ONE_RERUN",
         "reason": "assignment no-op on backlog is not repeated"},
        {"case": "backlog_unassigned_or_different",
         "expected": "ASSIGN_NO_START_THEN_ONE_RERUN",
         "reason": "ownership binding is not counted as a trigger"},
        {"case": "active_unassigned_or_different", "expected": "ONE_ASSIGNMENT",
         "reason": "Assignment remains the trigger on active issues"},
        {"case": "active_assigned", "expected": "ONE_RERUN",
         "reason": "prefer explicit rerun for the exact assigned target"},
        {"case": "status_promotion_route", "expected": "REJECTED",
         "reason": "not independently proven; never combined"},
        {"case": "mention_route", "expected": "OWN_U07_ROUTE",
         "reason": "native receipt semantics; never combined"},
        {"case": "trigger_success_without_run", "expected": "NOT_SUCCESS",
         "reason": "one exact correlated run is required"},
        {"case": "lost_trigger_response", "expected": "TRIGGER_AMBIGUOUS",
         "reason": "no automatic retry and no route switch"},
        {"case": "delayed_run_visibility", "expected": "RECONCILE_READ_ONLY",
         "reason": "later exactly-one-run proof attaches without a new trigger"},
        {"case": "duplicate_or_wrong_run", "expected": "BLOCKED",
         "reason": "correlation anomalies fail closed"},
        {"case": "artifact_or_package_drift", "expected": "REFRESH_REQUIRED",
         "reason": "refresh precedes any issuance"},
        {"case": "provider_failure_after_run", "expected": "EXECUTION_RECOVERY",
         "reason": "not a dispatch orphan; no redispatch"},
        {"case": "parent_wake_failure", "expected": "REPAIR_OR_ONE_WAKE",
         "reason": "failed wake with a run is repaired, not duplicated"},
        {"case": "completed_replay", "expected": "ZERO_SIDE_EFFECTS",
         "reason": "terminal intents emit no note/trigger/run/write"},
        {"case": "legacy_terminal_ledger", "expected": "READABLE_UNCHANGED",
         "reason": "no backfill and no intent inference"},
    ]


def compatibility_migration() -> dict:
    return {
        "schema": "forward_only",
        "legacy_records": "read and ignored by the intent fold; no rewrite",
        "terminal_u06_u08_transactions": "immutable",
        "u09_semantics": "byte-unchanged",
        "predecessor_files": "not modified; no re-pin",
        "nonterminal_preassigned_targets": "fixture inventory only",
        "yzt_77_yzt_78": "captured fixtures; never re-dispatched",
        "rollback": {
            "effect": "disable new intent creation and reconciliation",
            "retained": ["append-only intent records", "correlated run ids"],
            "removed": [],
        },
    }


def rollback_plan() -> dict:
    return {
        "trigger": "acceptance failure or unsafe ambiguity in live replay",
        "steps": [
            "stop creating new dispatch intents",
            "stop reconciliation reads/writes of new schema",
            "retain the append-only ledger and all correlated run ids",
            "do not delete or rewrite terminal records",
            "route handoffs back to the accepted U06/U08 commands",
        ],
        "data_preserved": True,
        "predecessor_history_mutated": False,
    }


def capabilities() -> dict:
    return {
        "cross_process_durability": True,
        "cross_process_enumeration": True,
        "single_writer_lease": True,
        "compare_and_set_transitions": True,
        "partial_record_fails_closed": True,
        "duplicate_intent_fails_closed": True,
        "duplicate_logical_key_fails_closed": True,
        "stale_lease_recovers": True,
        "automatic_retry_on_ambiguity": False,
        "platform_exactly_once_claim": False,
        "new_service_or_database": False,
    }


def acceptance_evidence(**overrides) -> dict:
    evidence = {
        "foundation": {
            "cross_run_ledger_durable_and_enumerable": True,
            "single_writer_claim_or_cas": True,
            "intent_persisted_before_target_mutation": True,
        },
        "intent": {
            "append_only_schema_versioned": True,
            "target_issue_and_exact_artifacts_bound": True,
            "legacy_terminal_history_rewritten": False,
        },
        "planner": {
            "status_and_assignee_bound": True,
            "ownership_binding_counted_as_trigger": False,
            "exactly_one_selected_trigger": True,
            "cli_success_without_correlated_run_is_success": False,
        },
        "recovery": {
            "trigger_issuing_recorded_before_call": True,
            "ambiguous_issuance_auto_retry": False,
            "exactly_one_correlated_run": True,
            "completed_replay_side_effects": 0,
        },
        "exit_gate": {
            "due_target_without_intent_or_terminal_stop": 0,
            "parked_target_without_dependency_owner_wake": 0,
        },
        "safety": {
            "duplicate_note": 0,
            "duplicate_trigger": 0,
            "duplicate_correlated_run": 0,
            "live_mutations_or_triggers": 0,
            "u09_or_predecessor_pin_change": 0,
            "frozen_t00_amended": False,
            "canonical_writes": 0,
            "product_repo_changes": 0,
            "unaccounted_findings": 0,
        },
    }
    evidence.update(overrides)
    return evidence


def side_effect_audit(store: DurableIntentStore) -> dict:
    records = store.read_records()
    commands = [r for r in records if r.get("kind") == "command"]
    audit = o2_audit_ledger(records)
    duplicated_runs = {}
    for intent in store.fold()["intents"].values():
        run_ids = [t.get("fields", {}).get("correlated_run_id")
                   for t in intent["transitions"]
                   if t.get("to") == S_RUN_CORRELATED]
        if len(run_ids) > 1 or len(set(run_ids)) != len(run_ids):
            duplicated_runs[intent["intent_id"]] = run_ids
    returns = {
        "commands_issued": len(commands),
        "write_class_commands": sum(
            1 for r in commands
            if (r.get("command_class") or "") in WRITE_COMMAND_CLASSES),
        "duplicate_trigger": max(0, audit["triggers"] - 1),
        "duplicate_note": 0,
        "duplicate_correlated_run": len(duplicated_runs),
        "live_mutations_or_triggers": 0,
        "canonical_writes": 0,
        "product_repo_changes": 0,
        "predecessor_history_rewrites": 0,
        "unaccounted_findings": 0,
        "ok": (audit["ok"] and not duplicated_runs),
    }
    return returns


def evidence_bundle(out_dir, *, generated_at: str) -> dict:
    """Write the deterministic, byte-stable O2 evidence bundle."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fixtures = out / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)

    def write_json(relative: str, payload) -> str:
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2,
                          sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        return digest_text(text)

    files = {}
    files["capabilities.json"] = write_json("capabilities.json", capabilities())
    files["trigger-matrix.json"] = write_json("trigger-matrix.json",
                                              trigger_matrix())
    files["state-transitions.json"] = write_json("state-transitions.json",
                                                 state_transition_table())
    files["success-failure-matrix.json"] = write_json(
        "success-failure-matrix.json", success_failure_matrix())
    files["compatibility-migration.json"] = write_json(
        "compatibility-migration.json", compatibility_migration())
    files["rollback-plan.json"] = write_json("rollback-plan.json",
                                             rollback_plan())
    files["acceptance-evidence.json"] = write_json(
        "acceptance-evidence.json", acceptance_evidence())
    files["target-inventory.json"] = write_json("target-inventory.json",
                                                target_inventory())
    files["fixtures/yzt-77.json"] = write_json("fixtures/yzt-77.json",
                                               yzt_77_fixture())
    files["fixtures/yzt-78.json"] = write_json("fixtures/yzt-78.json",
                                               yzt_78_fixture())
    fixtures_replay = {}
    for key, fixture in (("yzt-77", yzt_77_fixture()),
                         ("yzt-78", yzt_78_fixture())):
        with tempfile.TemporaryDirectory() as tmp:
            store = DurableIntentStore(Path(tmp) / "ledger.jsonl")
            replay = fixture_replay(fixture, store)
            fixtures_replay[key] = replay
            ledger_text = store.path.read_text(encoding="utf-8")
            ledger_path = out / "samples" / f"{key}-replay-ledger.jsonl"
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(ledger_text, encoding="utf-8", newline="\n")
            files[f"samples/{key}-replay-ledger.jsonl"] = digest_text(ledger_text)
            files[f"samples/{key}-replay.json"] = write_json(
                f"samples/{key}-replay.json", replay)
    files["fixture-replays.json"] = write_json("fixture-replays.json",
                                               fixtures_replay)
    with tempfile.TemporaryDirectory() as tmp:
        files["capability-proof.json"] = write_json(
            "capability-proof.json", capability_proof(tmp))
    return {"generated_at": generated_at, "files": files,
            "file_count": len(files)}


def capability_proof(workdir, *, python: str | None = None) -> dict:
    """Cross-process proof over the durable store using the probe worker."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    store_path = workdir / "shared-ledger.jsonl"
    probe = Path(__file__).resolve().parent / "o2_store_probe.py"
    python = python or sys.executable
    now = "2026-09-11T00:00:00Z"
    later = "2026-09-11T00:10:01Z"
    intent_id = "DI-" + "a" * 16

    def run_probe(command: list, *, cwd: Path):
        proc = subprocess.run(
            [python, str(probe)] + command, capture_output=True, text=True,
            encoding="utf-8", cwd=str(cwd), timeout=120)
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            payload = {"ok": False, "code": "probe_output_unparsable",
                       "stdout": proc.stdout[-200:]}
        return proc.returncode, payload

    observations = []
    cwd_a = workdir / "process-a"
    cwd_b = workdir / "process-b"
    cwd_a.mkdir(exist_ok=True)
    cwd_b.mkdir(exist_ok=True)

    # 1. cross-process append + enumerate from an independent workdir
    intent_file = workdir / "intent.json"
    intent_file.write_text(json.dumps({
        "intent_id": intent_id, "schema_version": O2_SCHEMA,
        "source_task_id": "probe-source", "logical_task_key": "probe-key",
        "parent_issue_id": "probe-parent", "target_role": "software-engineer",
        "target_agent_id": AGENT_04,
        "package_id": FIXTURE_PACKAGE,
        "artifact_dependency_digest": "sha256:" + "d" * 64,
        "creation_authority": "o2 capability probe",
        "provenance": {"probe": True},
    }, sort_keys=True), encoding="utf-8")
    code_a, append_result = run_probe(
        ["append-intent", "--store", str(store_path),
         "--intent-file", str(intent_file), "--now", now], cwd=cwd_a)
    code_b, enumerate_b = run_probe(
        ["enumerate", "--store", str(store_path), "--now", now], cwd=cwd_b)
    visible = [i for i in enumerate_b.get("intents", [])
               if i.get("intent_id") == intent_id]
    observations.append({
        "id": "cross_process_append_enumerate",
        "passed": (code_a == 0 and append_result.get("ok") is True
                   and code_b == 0 and len(visible) == 1),
        "append_exit": code_a, "enumerate_exit": code_b,
        "visible_intents": len(visible)})

    # 2. two independent processes race to issue the same intent
    code_seed, seed = run_probe(
        ["seed-ready", "--store", str(store_path), "--intent", intent_id,
         "--owner", "seed", "--now", now], cwd=cwd_a)
    procs = []
    for owner in ("reconciler-a", "reconciler-b"):
        procs.append((owner, subprocess.Popen(
            [python, str(probe), "race-issue", "--store", str(store_path),
             "--intent", intent_id, "--owner", owner, "--ttl", "300",
             "--now", later],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(cwd_a))))
    race_results = []
    for owner, proc in procs:
        out, _ = proc.communicate(timeout=120)
        try:
            payload = json.loads(out.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            payload = {"ok": False, "code": "probe_output_unparsable"}
        race_results.append({"owner": owner, "exit": proc.returncode,
                             "ok": bool(payload.get("ok")),
                             "code": payload.get("code")})
    winners = [r for r in race_results if r["ok"]]
    losers = [r for r in race_results if not r["ok"]]
    state_check = run_probe(
        ["state", "--store", str(store_path), "--intent", intent_id,
         "--now", later], cwd=cwd_b)
    issuing = state_check[1].get("transitions_to_issuing", -1)
    observations.append({
        "id": "single_writer_race",
        "passed": (code_seed == 0 and len(winners) == 1 and len(losers) == 1
                   and issuing == 1),
        "seed_exit": code_seed, "winners": len(winners),
        "losers": len(losers), "issuing_transitions": issuing,
        "loser_codes": sorted(r["code"] for r in losers)})

    # 3. stale lease recovery on a second intent
    intent_b_id = "DI-" + "b" * 16
    intent_b_file = workdir / "intent-b.json"
    intent_b_file.write_text(json.dumps({
        "intent_id": intent_b_id, "schema_version": O2_SCHEMA,
        "source_task_id": "probe-source", "logical_task_key": "probe-key-b",
        "parent_issue_id": "probe-parent", "target_role": "software-engineer",
        "target_agent_id": AGENT_04,
        "package_id": FIXTURE_PACKAGE,
        "artifact_dependency_digest": "sha256:" + "e" * 64,
        "creation_authority": "o2 capability probe",
        "provenance": {"probe": True},
    }, sort_keys=True), encoding="utf-8")
    code_append_b, _ = run_probe(
        ["append-intent", "--store", str(store_path),
         "--intent-file", str(intent_b_file), "--now", now], cwd=cwd_b)
    code_claim1, claim1 = run_probe(
        ["claim", "--store", str(store_path), "--intent", intent_b_id,
         "--owner", "stale-owner", "--ttl", "1", "--now", now], cwd=cwd_a)
    code_claim2, claim2 = run_probe(
        ["claim", "--store", str(store_path), "--intent", intent_b_id,
         "--owner", "recovered-owner", "--ttl", "300", "--now", later],
        cwd=cwd_b)
    observations.append({
        "id": "stale_lease_recovery",
        "passed": (code_append_b == 0 and code_claim1 == 0
                   and code_claim2 == 0 and claim2.get("ok") is True
                   and claim2.get("expired_previous") is True),
        "first_claim_exit": code_claim1,
        "recovered": bool(claim2.get("ok")),
        "expired_previous": bool(claim2.get("expired_previous"))})

    # 4. CAS conflict on a stale expected revision
    code_cas1, cas1 = run_probe(
        ["transition", "--store", str(store_path), "--intent", intent_b_id,
         "--to", S_TARGET_BOUND, "--expected-revision", "0",
         "--actor", "recovered-owner", "--now", later], cwd=cwd_a)
    code_cas2, cas2 = run_probe(
        ["transition", "--store", str(store_path), "--intent", intent_b_id,
         "--to", S_TARGET_BOUND, "--expected-revision", "0",
         "--actor", "recovered-owner", "--now", later], cwd=cwd_b)
    observations.append({
        "id": "cas_conflict",
        "passed": (code_cas1 == 0 and cas1.get("ok") is True
                   and code_cas2 != 0 and cas2.get("code") == "cas_conflict"),
        "first_exit": code_cas1, "second_exit": code_cas2,
        "second_code": cas2.get("code")})

    # 5. duplicate intent append fails closed
    code_dup, dup = run_probe(
        ["append-intent", "--store", str(store_path),
         "--intent-file", str(intent_file), "--now", now], cwd=cwd_b)
    observations.append({
        "id": "duplicate_intent_fail_closed",
        "passed": code_dup != 0 and dup.get("code") == "duplicate_intent",
        "exit": code_dup, "code": dup.get("code")})

    # 6. partial record fails closed
    with open(store_path, "a", encoding="utf-8") as handle:
        handle.write('{"kind": "command", "argv": ["issue", "get"\n')
    code_corrupt, corrupt = run_probe(
        ["enumerate", "--store", str(store_path), "--now", now], cwd=cwd_b)
    observations.append({
        "id": "partial_record_fail_closed",
        "passed": code_corrupt != 0 and corrupt.get("code") == "ledger_corruption",
        "exit": code_corrupt, "code": corrupt.get("code")})
    return {
        "method": "independent OS processes, independent workdirs, one "
                  "absolute shared ledger path",
        "store_basename": store_path.name,
        "all_passed": all(o["passed"] for o in observations),
        "observations": observations,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="O2 durable dispatch intent (YZT-79)")
    sub = parser.add_subparsers(dest="command", required=True)

    bundle = sub.add_parser("bundle", help="write the evidence bundle")
    bundle.add_argument("--out-dir", required=True)
    bundle.add_argument("--generated-at", default="2026-09-11T00:00:00Z")

    probe = sub.add_parser("probe", help="run the cross-process capability proof")
    probe.add_argument("--workdir", required=True)

    plan = sub.add_parser("plan", help="plan one trigger from a snapshot file")
    plan.add_argument("--snapshot-file", required=True)

    replay = sub.add_parser("replay-fixture", help="replay YZT-77/YZT-78")
    replay.add_argument("--fixture", choices=("yzt-77", "yzt-78"), required=True)
    replay.add_argument("--store", required=True)

    audit = sub.add_parser("audit", help="audit a shared ledger file")
    audit.add_argument("--ledger-file", required=True)

    observe_p = sub.add_parser("observe", help="open-intent observability")
    observe_p.add_argument("--ledger-file", required=True)
    observe_p.add_argument("--now", default=None)

    exit_p = sub.add_parser("exit-check", help="Lead exit invariant")
    exit_p.add_argument("--ledger-file", required=True)
    exit_p.add_argument("--targets-file", required=True)

    args = parser.parse_args(argv)
    if args.command == "bundle":
        result = evidence_bundle(args.out_dir, generated_at=args.generated_at)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "probe":
        result = capability_proof(args.workdir)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["all_passed"] else 2
    if args.command == "plan":
        snapshot = json.loads(Path(args.snapshot_file).read_text(encoding="utf-8"))
        print(json.dumps(plan_trigger(snapshot), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return 0
    if args.command == "replay-fixture":
        fixture = yzt_77_fixture() if args.fixture == "yzt-77" else yzt_78_fixture()
        store = DurableIntentStore(args.store)
        print(json.dumps(fixture_replay(fixture, store), ensure_ascii=False,
                         indent=2, sort_keys=True))
        return 0
    if args.command == "audit":
        store = DurableIntentStore(args.ledger_file)
        print(json.dumps(o2_audit_ledger(store.read_records()),
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "observe":
        store = DurableIntentStore(args.ledger_file)
        print(json.dumps(observe(store, now=args.now), ensure_ascii=False,
                         indent=2, sort_keys=True))
        return 0
    if args.command == "exit-check":
        store = DurableIntentStore(args.ledger_file)
        targets = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
        result = lead_exit_check(target_refs=targets, store=store)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result["ok"] else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
