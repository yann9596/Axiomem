#!/usr/bin/env python3
"""U12-R0B (YZT-84) — forward lifecycle binding adapter for the first R0 canary.

This module is a bounded forward adapter over the immutable accepted O2
`DurableIntentStore` / `O2Orchestrator`, the frozen PREPARE_HANDOFF and
SELF_CHECK contracts, T06 `publish_handoff` and the U12-P0R strict receipt
gate. It implements the design contract of `U12_R0_BINDING_DECISION.md`
(attachment 01a08f10-fc8c-778c-a150-2ed54825515a on YZT-66):

    creation intent (C) -> one unassigned backlog target -> ownership
    without start -> real-target execution package (E) -> one publication
    attempt -> publication confirmation bound to the actual observed issue
    revision -> one strict-gated rerun -> run correlation.

The YZT-83 accepted preflight decision (`U12_R0_PREFLIGHT_DECISION.md`, raw
SHA256 3acb66e3be54e9c00bf7c8f819b8970175d3a14984e3422f500220ae2106a2a9)
is implemented here as a forward repair: every consequential arm and every
not-yet-issued trigger recollects fresh material before entering the
unchanged O2 single-attempt issuance path. That preflight re-reads the pinned
artifact blobs, the current authority record, a fresh target-derived request
and current Findings, the exact bound note, the issue projection and the
complete run listing, and it fails closed as `REFRESH_REQUIRED` (confirmed
drift/supersession/non-READY) or `BLOCKED` (missing/unreadable/ambiguous
input) with zero native reruns. The separate exact Issue-revision equality
stop and the Human-approved publication attribution boundary are retained.

Hard boundaries kept by this module:

* no second ledger/service/scheduler; the same `DurableIntentStore` and the
  same `O2-dispatch-intent/1.0` records are used, with all new data namespaced
  under `r0_binding` with `contract_version = U12-R0B/1.0`;
* frozen T00 request/result schemas, the O2 state vocabulary and the strict
  receipt gate are byte-unchanged;
* every attempt is appended and fsynced under the existing intent lease
  before the external call; recovery is read-only or a typed ambiguity;
* the strict rerun entrypoint is the only trigger; assignment/mention/status
  triggers are unreachable from this factory;
* a tagged R0B intent is never executed through the plain O2 transition API:
  `mark_prepared`/`mark_published` are refused here, and missing/unsupported
  R0B data is a stop, never a downgrade.

The YZT-83 accepted create-recovery decision (`U12_R0_CREATE_RECOVERY_DECISION.md`,
raw SHA256 1fedade6694d6d15f076a1ecb84aedfa680c93767cd3ec27ac94b594220b101e)
is implemented here as a forward repair with two related capabilities:

* a prospective `single-terminal-lf/1` source-to-transport preparation that
  builds the final transport body before the creation spec is approved,
  persisted and digested (the persisted bytes are the sent bytes), while the
  raw source and the named transformation stay recorded as provenance;
* one narrow public `recover_created_target` operation that binds an intent
  already stopped at CREATE_AMBIGUOUS after exactly one create whose readback
  lost exactly one terminal LF. It acquires the same intent lease, revalidates
  the full original record and the complete live evidence, appends a
  namespaced recovery-evidence event and commits the existing
  CREATE_AMBIGUOUS -> TARGET_BOUND edge together with an effective-transport
  digest and a fenced execution binding. It has no create/assign/comment/rerun
  capability and never resets historical fields. A bare changed pin is still
  refused; only a committed, untampered, intent-bound proof permits the exact
  old-to-new execution transition, and the predecessor factory refuses the
  fenced record.

Nothing in this module performs a live create/assign/publication/rerun call by
itself; the runner is always injected explicitly by the caller.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff as chandoff  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_intent as o2  # noqa: E402
import chandoff_note as note  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402
import u12_strict_receipt as strict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ADAPTER_MODULE = "tools/u12_r0_binding.py"
ADAPTER_VERSION = "U12-R0B/1.1"
CONTRACT_VERSION = "U12-R0B/1.1"
R0B_FIELD = "r0_binding"

# --- forward create recovery (YZT-83 accepted decision) ----------------------
# The YZT-84 evidence correction (`U12_R0_RECOVERY_EVIDENCE_DECISION.md`, raw
# SHA256 60f265a8...) versions only the namespaced recovery decision, proof and
# execution-binding payloads. The lifecycle contract version, the frozen
# transport profile, the predecessor bridge and the O2 transition stay
# unchanged; an old v1.0 proof is preserved for inspection but is never
# accepted recovery authority (no silent upgrade).
PREDECESSOR_CONTRACT_VERSION = "U12-R0B/1.0"
PREDECESSOR_ADAPTER_COMMIT = "b49630b881170f7e6f40ffe61a82687492b99792"
PREDECESSOR_ADAPTER_DIGEST = (
    "sha256:8a9b75628f1263004ab214c96733d1b775399cdd6ffe5208fdaf4c0ea5abbc05")
TRANSPORT_PROFILE = "single-terminal-lf/1"
TRANSPORT_PROFILES = (TRANSPORT_PROFILE,)
RECOVERY_DECISION_SCHEMA = "u12-r0b-create-recovery-decision/1.1"
RECOVERY_PROOF_SCHEMA = "u12-r0b-recovery-proof/1.1"
EXECUTION_BINDING_SCHEMA = "u12-r0b-execution-binding/1.1"
RECOVERY_DESIGN_REF = "attachment/01a08f89-53bc-7bb0-bda2-1bf86c40be7f"
RECOVERY_DESIGN_DIGEST = (
    "sha256:1fedade6694d6d15f076a1ecb84aedfa680c93767cd3ec27ac94b594220b101e")
EVIDENCE_DECISION_REF = "attachment/01a08fbb-51cd-7aeb-907f-a547e4e066e9"
EVIDENCE_DECISION_DIGEST = (
    "sha256:60f265a8a446b5329b534bbb183de12d306fc0ea38f1da767ff9bf998ebd4334")
RECOVERY_DISPOSITION = "RECOVER_CREATED_TARGET"
RECOVERY_SCOPE = "CREATE_AMBIGUOUS"
RECOVERY_OWNERSHIP_NEXT = "RESUME_OWNERSHIP_NO_START"

# --- recovery evidence correction (YZT-84 accepted decision) -----------------
RECEIPT_STATUS_PERSISTED = "persisted"
RECEIPT_STATUS_NOT_PERSISTED = "not_persisted"
RECEIPT_LIMIT_SCOPE = (
    "READ_ONLY_TARGET_IDENTIFICATION_FOR_UNPERSISTED_RECEIPT_BODY")
EXECUTION_RESOLVER_GIT = "git-blob@repo-root"
EXECUTION_RESOLVER_INJECTED = "injected-fixture-proposal"
EXECUTION_RESOLVERS = (EXECUTION_RESOLVER_GIT, EXECUTION_RESOLVER_INJECTED)
LEDGER_PAIRING_ADJACENT = "adjacent-class-compatible-result"

CLS_INTENT_HISTORY = "expected-intent-history"
CLS_ORIGINAL_CREATE_COMMAND = "uniquely-correlated-original-create-command"
CLS_ORIGINAL_CREATE_RESULT = "uniquely-correlated-original-create-result"
CLS_READ_COMMAND = "recognized-read-command"
CLS_READ_RESULT = "recognized-read-result"

CREATION_ROLE = "engineering-lead"
EXECUTION_ROLE = "software-engineer"
CANARY_AGENT_ID = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
BACKLOG_STATUS = "backlog"

# --- namespaced ledger names -------------------------------------------------
E_INTENT_RECORDED = "r0b_intent_recorded"
E_CREATE_ISSUING = "r0b_create_issuing"
E_CREATE_BOUND = "r0b_create_bound"
E_OWNERSHIP_ISSUING = "r0b_ownership_issuing"
E_OWNERSHIP_BOUND = "r0b_ownership_bound"
E_OWNERSHIP_RECOVERED = "r0b_ownership_recovered"
E_EXECUTION_BOUND = "r0b_execution_bound"
E_PUBLICATION_ISSUING = "r0b_publication_issuing"
E_PUBLICATION_RESPONSE = "r0b_publication_response"
E_PUBLICATION_UNCERTAIN = "r0b_publication_uncertain"
E_PUBLICATION_BOUND = "r0b_publication_bound"
E_RECOVERY = "r0b_recovery"
E_EVIDENCE_REFUSED = "r0b_evidence_refused"
E_RECOVERY_EVIDENCE = "r0b_recovery_evidence"
E_RECOVERY_BOUND = "r0b_recovery_bound"

# --- typed reasons -----------------------------------------------------------
REASON_CONTRACT = "R0B_CONTRACT_MISSING_OR_UNSUPPORTED"
REASON_DOWNGRADE = "R0B_DOWNGRADE_REFUSED"
REASON_VALIDATION = "R0B_VALIDATION_REFUSED"
REASON_CREATE_AMBIGUOUS = "CREATE_AMBIGUOUS"
REASON_OWNERSHIP_UNPROVEN = "OWNERSHIP_UNPROVEN"
REASON_TARGET_DRIFT = "TARGET_EVIDENCE_DRIFT"
REASON_PUBLICATION_AMBIGUOUS = "PUBLICATION_AMBIGUOUS"
REASON_PUBLICATION_PROVENANCE = "PUBLICATION_PROVENANCE_INCOMPLETE"
REASON_PUBLICATION_CONFLICT = "PUBLICATION_DELTA_NOT_ATTRIBUTABLE"
REASON_MOVING_EVIDENCE = "EVIDENCE_REVISION_MOVING"

# --- create-recovery typed reasons -------------------------------------------
REASON_RECOVERY_REFUSED = "R0B_CREATE_RECOVERY_REFUSED"
REASON_RECOVERY_UNSUPPORTED = "R0B_CREATE_RECOVERY_UNSUPPORTED_RECORD"
REASON_RECOVERY_COMPETING = "R0B_CREATE_RECOVERY_COMPETING_PROOF"
REASON_RECOVERY_IDENTITY = "R0B_CREATE_RECOVERY_IDENTITY_UNPROVEN"
REASON_RECOVERY_EFFECT = "R0B_CREATE_RECOVERY_UNKNOWN_EFFECT"
REASON_RECOVERY_TARGET = "R0B_CREATE_RECOVERY_TARGET_MISMATCH"
REASON_RECOVERY_BODY = "R0B_CREATE_RECOVERY_BODY_RELATION_REFUSED"
REASON_TRANSPORT_UNSUPPORTED = "R0B_TRANSPORT_SOURCE_UNSUPPORTED"
REASON_RECOVERY_HISTORY = "R0B_CREATE_RECOVERY_SHARED_HISTORY_UNRESOLVED"
REASON_RECOVERY_ORIGINAL_EVIDENCE = (
    "R0B_CREATE_RECOVERY_ORIGINAL_EVIDENCE_GAP")
REASON_RECOVERY_PREFIX = "R0B_CREATE_RECOVERY_PREFIX_MISMATCH"
REASON_RECOVERY_RECEIPT = "R0B_CREATE_RECOVERY_RECEIPT_DISPOSITION_REFUSED"
REASON_RECOVERY_EXECUTION = "R0B_CREATE_RECOVERY_EXECUTION_IDENTITY_REFUSED"
REASON_RECOVERY_PROOF = "R0B_CREATE_RECOVERY_PROOF_INVALID"

# The only prior effects a first bounded recovery may observe besides the sole
# create attempt. A crashed-before-commit recovery may have appended its own
# evidence event; that event is never authorization and is recollected.
RECOVERY_ALLOWED_PRIOR_EVENTS = (E_INTENT_RECORDED, E_CREATE_ISSUING,
                                 E_RECOVERY, E_EVIDENCE_REFUSED,
                                 E_RECOVERY_EVIDENCE)
RECOVERY_FORBIDDEN_METHODS = ("create_backlog_issue", "create_issue",
                              "assign_ownership_no_start", "assign_trigger",
                              "rerun_issue", "publish_handoff")

# --- preflight (YZT-83 accepted forward repair) ------------------------------
AUTHORITY_EVIDENCE_SCHEMA = "u12-r0b-authority-evidence/1.0"
AUTHORITY_ARTIFACT_PATH = "adapters/multica/u12-p0r/readiness-manifest.json"
AUTHORITY_REF = "repo://" + AUTHORITY_ARTIFACT_PATH
AUTHORITY_DIGEST_METHOD = "canonical_json"

REASON_MATERIAL_STALE = "R0B_MATERIAL_STALE"
REASON_MATERIAL_UNAVAILABLE = "R0B_MATERIAL_UNAVAILABLE"
REASON_PREFLIGHT_INPUT_MISSING = "R0B_PREFLIGHT_INPUT_MISSING"
REASON_TRIGGER_ALREADY_ISSUED = "R0B_TRIGGER_ALREADY_ISSUED"

E_PREFLIGHT = "r0b_preflight"
R0B_STOP_STATES = (o2.S_BLOCKED, o2.S_CANCELLED, o2.S_REFRESH_REQUIRED)
CLOSED_TRIGGER_STATES = (o2.S_TRIGGER_ISSUING, o2.S_TRIGGER_AMBIGUOUS,
                         o2.S_RUN_CORRELATED, o2.S_SELF_CHECKED)
TRIGGER_REPLAY_STATES = (CLOSED_TRIGGER_STATES + R0B_STOP_STATES
                         + tuple(o2.TERMINAL_STATES))

# --- publication predicate codes --------------------------------------------
PUB_OK = "PUBLICATION_ATTRIBUTED"
PUB_INCOMPLETE = "PUBLICATION_PROVENANCE_INCOMPLETE"
PUB_NOTE_NOT_FOUND = "PUBLICATION_NOTE_NOT_FOUND"
PUB_DUPLICATE_NOTE = "PUBLICATION_DUPLICATE_NOTE"
PUB_UNAUTHORIZED_DELTA = "PUBLICATION_UNAUTHORIZED_DELTA"
PUB_UNEXPECTED_RUN = "PUBLICATION_UNEXPECTED_RUN"
PUB_MOVING_REVISION = "PUBLICATION_MOVING_REVISION"

VOLATILE_ISSUE_FIELDS = ("revision", "updated_at", "last_activity_at")

CREATE_FLAGS = ("--title", "--description-file", "--parent", "--project",
                "--priority", "--status", "--output")
R0B_CREATE_STATUS = "--status"
R0B_CREATE_STATUS_VALUE = "backlog"

_UUID_RE = dispatch.UUID_RE
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX16_RE = re.compile(r"^[0-9a-f]{16}$")
_MARKER_RE = re.compile(r"R0BI-[0-9a-f]{16}")
_TRUNCATION_RE = re.compile(r"truncat", re.IGNORECASE)
_CURSOR_RE = re.compile(r"Next (?:thread|reply) cursor", re.IGNORECASE)
_MENTION_RE = re.compile(r"mention://")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def canonical_json(obj) -> str:
    return chandoff.canonical_json(obj)


def digest(obj) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(obj).encode("utf-8")).hexdigest()


def digest_text_lf(text: str) -> str:
    data = text.encode("utf-8").replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def adapter_digest(path=None) -> str:
    """LF-normalized sha256 of this module (tamper/downgrade pin)."""
    target = Path(path) if path else Path(__file__).resolve()
    data = target.read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_utf8(text: str) -> str:
    """Exact raw-bytes digest of one UTF-8 string (no newline conversion)."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def single_terminal_lf_relation(source, observed) -> dict:
    """The accepted directional `single-terminal-lf/1` transport relation.

    Exact equality stays valid under the existing contract. The only
    exceptional form is: source ends in exactly one LF (never CRLF and never
    a repeated terminal LF), carries no other trailing whitespace, and the
    observed bytes equal the source with exactly that last byte removed.
    Interior, leading, Unicode and whitespace-equivalence changes are refused,
    and an arbitrary readback is never normalized until it matches.
    """
    if not isinstance(source, str) or not isinstance(observed, str):
        return {"accepted": False, "relation": None,
                "reason": REASON_TRANSPORT_UNSUPPORTED,
                "detail": "source and observed must both be strings"}
    if source == observed:
        return {"accepted": True, "relation": "exact", "removed_lf": False,
                "reason": None, "detail": ""}
    if source.endswith("\r\n") or source.endswith("\n\n"):
        return {"accepted": False, "relation": None, "removed_lf": False,
                "reason": REASON_TRANSPORT_UNSUPPORTED,
                "detail": "source has a CRLF or repeated terminal LF"}
    if source.endswith("\n"):
        if source[:-1] and source[:-1][-1] in " \t\r\f\v":
            return {"accepted": False, "relation": None, "removed_lf": False,
                    "reason": REASON_TRANSPORT_UNSUPPORTED,
                    "detail": "source carries trailing whitespace before the "
                              "final LF"}
        if observed == source[:-1]:
            return {"accepted": True, "relation": "single-terminal-lf-removed",
                    "removed_lf": True, "reason": None, "detail": ""}
        return {"accepted": False, "relation": None, "removed_lf": False,
                "reason": REASON_RECOVERY_BODY,
                "detail": "observed bytes differ beyond the terminal LF"}
    if source and source[-1] in " \t\r\f\v":
        return {"accepted": False, "relation": None, "removed_lf": False,
                "reason": REASON_TRANSPORT_UNSUPPORTED,
                "detail": "source carries unsupported trailing whitespace"}
    return {"accepted": False, "relation": None, "removed_lf": False,
            "reason": REASON_RECOVERY_BODY,
            "detail": "observed bytes are not an exact transport match"}


def prepare_transport_body(source_body, *,
                           profile: str = TRANSPORT_PROFILE) -> dict:
    """Prospective source-to-transport preparation for `single-terminal-lf/1`.

    The transport body is constructed here, before the creation spec is
    approved/persisted/digested, so the persisted bytes are exactly the bytes
    sent. The raw source and the named transformation are returned as separate
    provenance; unsupported trailing whitespace is a typed refusal and is
    never silently stripped. Unsupported profiles refuse.
    """
    if profile not in TRANSPORT_PROFILES:
        raise R0BValidationRefused(
            "transport profile is not accepted", profile=profile,
            accepted=list(TRANSPORT_PROFILES))
    if not isinstance(source_body, str) or not source_body:
        raise R0BValidationRefused(
            "source_body must be a non-empty string",
            profile=profile)
    if source_body.endswith("\r\n") or source_body.endswith("\n\n"):
        raise R0BValidationRefused(
            "source has a CRLF or repeated terminal LF; supply explicit "
            "source preparation instead of relying on trimming", profile=profile)
    if source_body.endswith("\n"):
        transport = source_body[:-1]
        if transport and transport[-1] in " \t\r\f\v":
            raise R0BValidationRefused(
                "source carries trailing whitespace before the final LF; "
                "explicit source preparation is required", profile=profile)
        transformation = "remove-single-terminal-lf"
    else:
        if source_body[-1] in " \t\r\f\v":
            raise R0BValidationRefused(
                "source carries unsupported trailing whitespace; explicit "
                "source preparation is required", profile=profile)
        transport = source_body
        transformation = "identity"
    return {
        "profile": profile,
        "transformation": transformation,
        "transport_body": transport,
        "transport_body_digest": _sha256_utf8(transport),
        "transport_body_lf_digest": digest_text_lf(transport),
        "source_raw_digest": _sha256_utf8(source_body),
        "source_lf_digest": digest_text_lf(source_body),
        "source_chars": len(source_body),
        "source_utf8_bytes": len(source_body.encode("utf-8")),
        "transport_chars": len(transport),
        "transport_utf8_bytes": len(transport.encode("utf-8")),
        "body_digest_method": "digest_text_lf",
    }


def _raw_prefix_digest(lines: list) -> str:
    """Exact digest of the audited ledger prefix.

    The convention is sha256 over the exact bytes of the first N ledger
    records, each serialized line including its terminating LF. It is the
    same value an operator computes with `ledger-audit`, and the disposition
    pins it as `ledger_prefix.digest`.
    """
    return "sha256:" + hashlib.sha256(
        b"".join(bytes(line) + b"\n" for line in lines)).hexdigest()


def shared_history_digest(shared: dict) -> str:
    """Recomputable digest of the serialized shared-history classification."""
    body = {key: shared.get(key) for key in (
        "interval", "audited_prefix", "records", "original_create")}
    return digest(body)


def _original_create_argv_problems(argv: list, spec: dict) -> list:
    """Full-argv semantic comparison of the original create command.

    The recorded `command_class` is never trusted on its own: the structured
    argv must reproduce the retained creation spec, including the exact
    parent/title/project/priority/backlog/unassigned behavior, and the
    description-file argument must encode the preserved source body digest so
    a later re-used temp path can never stand in for the historical input.
    """
    core = o2._core_argv([str(a) for a in (argv or [])], "multica")
    if tuple(core[:2]) != ("issue", "create"):
        return ["command is not `issue create`"]
    flags: dict = {}
    positionals: list = []
    index = 2
    while index < len(core):
        token = core[index]
        if token.startswith("--"):
            if index + 1 >= len(core):
                return [f"{token} has no value"]
            flags.setdefault(token, []).append(core[index + 1])
            index += 2
        else:
            positionals.append(token)
            index += 1
    problems = []
    if positionals:
        problems.append("unexpected positional arguments")
    unexpected = sorted(set(flags) - set(CREATE_FLAGS))
    if unexpected:
        problems.append("flags outside the create allowlist: "
                        + ",".join(unexpected))
    if flags.get("--title") != [spec["title"]]:
        problems.append("title")
    if flags.get("--status") != [BACKLOG_STATUS]:
        problems.append("literal --status backlog")
    if flags.get("--output") != ["json"]:
        problems.append("--output json")
    described = flags.get("--description-file")
    if not described or len(described) != 1:
        problems.append("--description-file")
    else:
        name = re.split(r"[\\/]", str(described[0]))[-1]
        expected = ".u12r0b-create-" + hashlib.sha1(
            spec["body"].encode("utf-8")).hexdigest()[:12] + ".md"
        if name != expected:
            problems.append(
                "description-file argument does not encode the preserved "
                "source body digest")
    if spec.get("parent_issue_id") is not None:
        if flags.get("--parent") != [spec["parent_issue_id"]]:
            problems.append("--parent")
    elif "--parent" in flags:
        problems.append("unexpected --parent")
    if spec.get("project_id") is not None:
        if flags.get("--project") != [spec["project_id"]]:
            problems.append("--project")
    elif "--project" in flags:
        problems.append("unexpected --project")
    if spec.get("priority") is not None:
        if flags.get("--priority") != [spec["priority"]]:
            problems.append("--priority")
    elif "--priority" in flags:
        problems.append("unexpected --priority")
    return problems


def new_operation_id(kind: str, payload: dict, *, nonce: str | None = None) -> str:
    body = {"kind": kind, "payload": payload, "nonce": nonce or uuid.uuid4().hex}
    return "OP-" + hashlib.sha256(
        canonical_json(body).encode("utf-8")).hexdigest()[:16]


def new_marker(intent_key: str, *, nonce: str | None = None) -> str:
    body = {"key": intent_key, "nonce": nonce or uuid.uuid4().hex}
    return "R0BI-" + hashlib.sha256(
        canonical_json(body).encode("utf-8")).hexdigest()[:16]


def self_check_request_from_prepare(request: dict) -> dict:
    return {
        "schema_version": "1.1",
        "kind": "self_check_request",
        "task_ref": request.get("task_ref"),
        "role": ((request.get("target") or {}).get("role")),
        "task_snapshot": request.get("task_snapshot") or {},
    }


# ---------------------------------------------------------------------------
# typed errors
# ---------------------------------------------------------------------------
class R0BError(o2.IntentError):
    code = "r0b_error"


class R0BContractError(R0BError):
    code = "r0b_contract_missing_or_unsupported"


class R0BDowngradeRefused(R0BError):
    code = "r0b_downgrade_refused"


class R0BCompatibilityRefused(R0BError):
    """A known-predecessor record without a committed forward recovery.

    The record stays readable/auditable, but no lifecycle operation except
    `recover_created_target` may execute it (no automatic migration, no
    downgrade).
    """
    code = "r0b_predecessor_requires_recovery"


class R0BValidationRefused(R0BError):
    code = "r0b_validation_refused"


class R0BTypedStop(R0BError):
    code = "r0b_typed_stop"

    def __init__(self, message, *, state, reason, detail="", **details):
        super().__init__(message, state=state, reason=reason, detail=detail,
                         **details)
        self.state = state
        self.reason = reason
        self.detail = detail


class PublicationProvenanceIncomplete(R0BError):
    code = PUB_INCOMPLETE


class PreflightRefusal(Exception):
    """A typed pre-issuance stop raised by the fresh material preflight.

    `state` is the exact O2 stop state (`REFRESH_REQUIRED` for confirmed
    changed/superseded/non-READY material, `BLOCKED` for missing, unreadable
    or provenance-ambiguous input). `subjects` carries bounded diagnostics;
    it is recorded in the namespaced ledger event and never reused as
    authorization.
    """

    def __init__(self, state, reason, detail, **subjects):
        super().__init__(str(detail))
        self.state = state
        self.reason = reason
        self.detail = str(detail)
        self.subjects = subjects


# ---------------------------------------------------------------------------
# artifact dependency digest (design: keyed by repo-relative artifact path)
# ---------------------------------------------------------------------------
DEFAULT_ARTIFACT_ENTRIES = (
    {"path": "adapters/multica/u12-p0r/readiness-manifest.json",
     "commit": "fcf63534e74b70220421c7b83c5447242d9a124b",
     "digest_method": "canonical_json"},
    {"path": "adapters/multica/u12-p0r/proposed-r0-canary-plan.json",
     "commit": "fcf63534e74b70220421c7b83c5447242d9a124b",
     "digest_method": "raw"},
    {"path": "tools/u12_strict_receipt.py",
     "commit": "fcf63534e74b70220421c7b83c5447242d9a124b",
     "digest_method": "LF"},
    {"path": "tools/chandoff_intent.py",
     "commit": "49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9",
     "digest_method": "LF"},
    {"path": "tools/chandoff_joint.py",
     "commit": "6439bd429af0aa9ec4b95d838643bd8b633ed053",
     "digest_method": "LF"},
    {"path": "tools/o2_store_probe.py",
     "commit": "6439bd429af0aa9ec4b95d838643bd8b633ed053",
     "digest_method": "LF"},
    {"path": "adapters/multica/joint-replay/final-gate-matrix.json",
     "commit": "6439bd429af0aa9ec4b95d838643bd8b633ed053",
     "digest_method": "raw"},
)


def _normalize_artifact_entries(entries) -> list:
    if isinstance(entries, dict):
        entries = [dict(entries[key], path=key) for key in sorted(entries)]
    out = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            raise R0BValidationRefused("artifact entry is not an object")
        path = entry.get("path")
        commit = entry.get("commit")
        method = entry.get("digest_method")
        if not isinstance(path, str) or not path.strip():
            raise R0BValidationRefused("artifact entry path is blank")
        if not isinstance(commit, str) or not re.match(r"^[0-9a-f]{7,40}$",
                                                       commit):
            raise R0BValidationRefused("artifact entry commit is not a git rev",
                                       path=path)
        if method not in ("raw", "LF", "canonical_json"):
            raise R0BValidationRefused("artifact digest_method is unsupported",
                                       path=path, digest_method=method)
        out.append({"path": path.strip(), "commit": commit,
                    "digest_method": method})
    if not out:
        raise R0BValidationRefused("artifact dependency set is empty")
    return out


def _digest_blob(data: bytes, method: str) -> str:
    if method == "LF":
        data = data.replace(b"\r\n", b"\n")
    elif method == "canonical_json":
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise R0BValidationRefused(
                f"canonical_json artifact is not UTF-8 JSON: {exc}")
        data = canonical_json(parsed).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git_blob_reader(root: Path):
    def read(commit: str, path: str) -> bytes:
        proc = subprocess.run(
            ["git", "-C", str(root), "show", f"{commit}:{path}"],
            capture_output=True)
        if proc.returncode != 0:
            raise R0BValidationRefused(
                "artifact blob is not resolvable at the pinned commit",
                commit=commit, path=path)
        return proc.stdout
    return read


def build_artifact_dependency_digest(entries=None, *, root=None,
                                     blob_reader=None) -> dict:
    """Exact digest object over the pinned artifact dependency set.

    Returns `{"digest", "entries"}`, where `entries` is the per-path map
    `{path: {commit, digest_method, sha256}}` and `digest` is the O2 canonical
    digest of that map. Any unresolvable entry refuses.
    """
    normalized = _normalize_artifact_entries(
        DEFAULT_ARTIFACT_ENTRIES if entries is None else entries)
    reader = blob_reader or _git_blob_reader(root or ROOT)
    mapping = {}
    for entry in normalized:
        actual = _digest_blob(reader(entry["commit"], entry["path"]),
                              entry["digest_method"])
        mapping[entry["path"]] = {
            "commit": entry["commit"],
            "digest_method": entry["digest_method"],
            "sha256": actual,
        }
    return {"digest": digest(mapping), "entries": mapping}


# ---------------------------------------------------------------------------
# boundary: + literal unassigned backlog create, + evidence reads
# ---------------------------------------------------------------------------
class EvidenceReader:
    """Read-only evidence reader with an explicit raw runner.

    Kept separate from the store-recording boundary so new read subcommands
    (`issue comment list`, `issue timeline`) never enter the accepted O2
    command records and cannot distort the O2 ledger audit. Every
    completeness failure (truncation, pagination cursor, missing provenance
    field) raises `PublicationProvenanceIncomplete`.
    """

    def __init__(self, runner, *, executable: str = "multica"):
        if runner is None:
            raise o2.NotAuthorizedError(
                "EvidenceReader requires an explicitly injected runner")
        self.runner = runner
        self.executable = executable
        self.commands: list = []
        # Raw successful read responses (argv + exact stdout text + digest).
        # They are retained so a recovery proof can persist the actual bytes
        # that were used to decide identity instead of reconstructing an
        # asserted complete response from counts.
        self.responses: list = []

    def _run(self, argv: list) -> tuple:
        argv = [str(a) for a in argv]
        self.commands.append(argv)
        code, out, err = self.runner([self.executable] + argv)
        return int(code), out or "", err or ""

    def _json(self, argv: list, what: str):
        code, out, err = self._run(argv)
        if code != 0:
            raise PublicationProvenanceIncomplete(
                f"{what} failed (exit {code}); evidence is incomplete",
                stderr=str(err)[:160])
        if _TRUNCATION_RE.search(err or ""):
            raise PublicationProvenanceIncomplete(
                f"{what} reports truncation; evidence is incomplete",
                stderr=str(err)[:160])
        if _CURSOR_RE.search(err or ""):
            raise PublicationProvenanceIncomplete(
                f"{what} reports a pagination cursor; completeness cannot be "
                "proven", stderr=str(err)[:160])
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError as exc:
            raise PublicationProvenanceIncomplete(
                f"{what} is not JSON: {exc}") from exc
        self.responses.append({
            "what": what,
            "argv": [self.executable] + [str(a) for a in argv],
            "raw": out,
            "raw_digest": _sha256_utf8(out),
        })
        return parsed

    def issue_get(self, issue_id: str) -> dict:
        data = self._json(["issue", "get", str(issue_id), "--output", "json"],
                          "issue get")
        if not isinstance(data, dict):
            raise PublicationProvenanceIncomplete("issue get is not an object")
        if not isinstance(data.get("revision"), int) or data["revision"] < 1:
            raise PublicationProvenanceIncomplete(
                "issue get carries no exact revision")
        if not isinstance(data.get("id"), str) or not data["id"]:
            raise PublicationProvenanceIncomplete("issue get carries no id")
        return data

    def comments_full(self, issue_id: str) -> list:
        data = self._json(["issue", "comment", "list", str(issue_id),
                           "--full", "--output", "json"],
                          "issue comment list")
        if not isinstance(data, list):
            raise PublicationProvenanceIncomplete(
                "comment list is not an array")
        required = ("id", "content", "created_at", "parent_id", "revision",
                    "updated_at", "author_id", "author_type")
        rows = []
        for index, doc in enumerate(data):
            if not isinstance(doc, dict):
                raise PublicationProvenanceIncomplete(
                    "comment list element is not an object", index=index)
            missing = [f for f in required if f not in doc]
            if missing:
                raise PublicationProvenanceIncomplete(
                    "comment is missing a required provenance field",
                    index=index, missing=missing)
            if not isinstance(doc.get("content"), str):
                raise PublicationProvenanceIncomplete(
                    "comment content is not a string", index=index)
            rows.append(doc)
        return rows

    def timeline_activities(self, issue_id: str) -> list:
        data = self._json(["issue", "timeline", str(issue_id),
                           "--activity-only", "--output", "json"],
                          "issue timeline")
        if not isinstance(data, list):
            raise PublicationProvenanceIncomplete(
                "issue timeline is not an array")
        for index, row in enumerate(data):
            if not isinstance(row, dict):
                raise PublicationProvenanceIncomplete(
                    "timeline row is not an object", index=index)
            for field in ("id", "action", "created_at"):
                if field not in row:
                    raise PublicationProvenanceIncomplete(
                        "timeline row is missing a provenance field",
                        index=index, field=field)
        return data

    def runs_full(self, issue_id: str) -> list:
        data = self._json(["issue", "runs", str(issue_id), "--output", "json"],
                          "issue runs")
        if not isinstance(data, list):
            raise PublicationProvenanceIncomplete("issue runs is not a list")
        try:
            return dispatch.parse_runs_json(json.dumps(data))
        except dispatch.DispatchError as exc:
            raise PublicationProvenanceIncomplete(
                f"issue runs contract violated: {exc.message}") from exc

    def issue_children(self, parent_issue_id: str) -> dict:
        """Complete parent-child discovery including the live `unstaged` child.

        The documented listing carries staged groups plus one `unstaged`
        entry (object or list). The accepted O2 boundary drops `unstaged`;
        recovery must not, so this reader flattens both and reports the
        declared total so a partial listing fails closed.
        """
        data = self._json(["issue", "children", str(parent_issue_id),
                           "--output", "json"], "issue children")
        if not isinstance(data, dict) or not isinstance(
                data.get("stages"), list):
            if isinstance(data, list):
                return {"rows": [x for x in data if isinstance(x, dict)],
                        "total": len(data), "declared_total": len(data)}
            raise PublicationProvenanceIncomplete(
                "issue children JSON shape is unknown")
        rows: list = []
        for stage in data["stages"]:
            if not isinstance(stage, dict) or \
                    not isinstance(stage.get("issues"), list):
                raise PublicationProvenanceIncomplete(
                    "issue children stage is malformed")
            rows.extend(x for x in stage["issues"] if isinstance(x, dict))
        unstaged = data.get("unstaged")
        if isinstance(unstaged, dict):
            rows.append(unstaged)
        elif isinstance(unstaged, list):
            rows.extend(x for x in unstaged if isinstance(x, dict))
        elif unstaged is not None:
            raise PublicationProvenanceIncomplete(
                "issue children unstaged entry is malformed")
        declared = data.get("total")
        if not isinstance(declared, int):
            raise PublicationProvenanceIncomplete(
                "issue children carries no declared total; completeness "
                "cannot be proven")
        if declared != len(rows):
            raise PublicationProvenanceIncomplete(
                "issue children listing is partial",
                declared_total=declared, collected=len(rows))
        return {"rows": rows, "total": len(rows), "declared_total": declared}


class ReadinessManifestAuthorityReader:
    """Read-only reader for the existing accepted R0 readiness authority.

    This is the concrete binding of the required current-authority input to
    the existing authoritative source: the readiness manifest already bound
    in the intent artifact dependency. It reads the file from an explicit
    root at call time (never cached, never a Git HEAD lookup, never a newer
    artifact adopted implicitly) and returns the authority-evidence shape the
    preflight validates. No new authority source or registry is introduced.
    """

    def __init__(self, root=None):
        self.root = Path(root) if root else ROOT

    def read(self, *, path: str = AUTHORITY_ARTIFACT_PATH) -> dict:
        if path != AUTHORITY_ARTIFACT_PATH:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the configured authority reader only serves "
                f"{AUTHORITY_ARTIFACT_PATH}; requested {path!r}",
                subject="authority")
        target = self.root / AUTHORITY_ARTIFACT_PATH
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"authoritative readiness record is unreadable: {exc}",
                subject="authority", path=str(target))
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"authoritative readiness record is not UTF-8 JSON: {exc}",
                subject="authority", path=str(target))
        if not isinstance(record, dict):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "authoritative readiness record is not an object",
                subject="authority", path=str(target))
        return {
            "schema": AUTHORITY_EVIDENCE_SCHEMA,
            "ref": AUTHORITY_REF,
            "path": AUTHORITY_ARTIFACT_PATH,
            "digest_method": AUTHORITY_DIGEST_METHOD,
            "sha256": digest(record),
            "disposition": "READY",
            "record": record,
        }


def validate_authority_evidence(evidence, *, data) -> dict:
    """Validate current authority evidence against the saved binding.

    Confirmed different version, REVOKED or SUPERSEDED disposition stops as
    REFRESH_REQUIRED; missing/unreadable/ambiguous/non-matching evidence
    stops as BLOCKED. A bool or cached digest can never satisfy this check.
    """
    entries = data["artifact_dependency"]["entries"]
    bound = entries.get(AUTHORITY_ARTIFACT_PATH)
    if not isinstance(bound, dict) or \
            bound.get("digest_method") != AUTHORITY_DIGEST_METHOD:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "intent artifact dependency does not bind the authoritative "
            "readiness manifest with the accepted canonical method",
            subject="authority", path=AUTHORITY_ARTIFACT_PATH)
    if evidence is None:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_PREFLIGHT_INPUT_MISSING,
            "current authority evidence is missing; inject the read-only "
            "authority reader or supply one authority evidence input "
            "refreshed for this entrypoint",
            subject="authority", path=AUTHORITY_ARTIFACT_PATH)
    if not isinstance(evidence, dict):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "authority evidence is not an object", subject="authority")
    path = evidence.get("path") or AUTHORITY_ARTIFACT_PATH
    ref = evidence.get("ref") or ("repo://" + str(path))
    method = evidence.get("digest_method") or AUTHORITY_DIGEST_METHOD
    if evidence.get("schema") not in (None, AUTHORITY_EVIDENCE_SCHEMA):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            f"authority evidence schema is unsupported: "
            f"{evidence.get('schema')!r}", subject="authority")
    if path != AUTHORITY_ARTIFACT_PATH or ref != AUTHORITY_REF:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            f"authority evidence names {ref!r} instead of the bound "
            f"{AUTHORITY_REF!r}; no substitute authority source is accepted",
            subject="authority")
    if method != bound.get("digest_method"):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "authority evidence digest method is not the bound method",
            subject="authority", digest_method=method)
    disposition = evidence.get("disposition")
    if disposition not in ("READY", "REVOKED", "SUPERSEDED"):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            f"authority disposition is missing or unknown: {disposition!r}",
            subject="authority")
    if disposition != "READY":
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            f"authority disposition is {disposition}; the bound subject is no "
            "longer the current READY authority", subject="authority",
            disposition=disposition)
    version = evidence.get("sha256")
    if not isinstance(version, str) or not _DIGEST_RE.match(version):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "authority evidence carries no exact version digest",
            subject="authority")
    if version != bound.get("sha256"):
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the current authoritative record differs from the bound version "
            f"(bound {bound.get('sha256')}, current {version}); a newer or "
            "replaced authority is never adopted implicitly",
            subject="authority", digest=version)
    record = evidence.get("record")
    if record is not None:
        if not isinstance(record, dict):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "authority record is not an object", subject="authority")
        if digest(record) != version:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "authority evidence version does not match its own record",
                subject="authority")
        declared = record.get("manifest_digest")
        if declared is not None:
            recomputed = digest({k: v for k, v in record.items()
                                 if k != "manifest_digest"})
            if declared != recomputed:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                    "authority record self-digest does not reproduce",
                    subject="authority")
        cross_links = {
            "tools/u12_strict_receipt.py":
                (record.get("strict_receipt_gate") or {}).get("sha256_lf"),
            "adapters/multica/u12-p0r/proposed-r0-canary-plan.json":
                (record.get("evidence_files") or {}).get(
                    "proposed-r0-canary-plan.json"),
        }
        for cross_path, referenced in cross_links.items():
            entry = entries.get(cross_path)
            if isinstance(referenced, str) and isinstance(entry, dict) and \
                    referenced != entry.get("sha256"):
                raise PreflightRefusal(
                    o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                    "the authoritative record references a different "
                    f"{cross_path} (authority {referenced}, bound "
                    f"{entry.get('sha256')}); the bound set is not the "
                    "current approved subject", subject="authority",
                    path=cross_path)
    return {"ref": ref, "path": path, "digest_method": method,
            "version": version, "disposition": disposition}


class R0BBoundary(strict.StrictReceiptBoundary):
    """Strict receipt boundary + the one new create command class.

    The only added create form is the literal unassigned
    `issue create ... --status backlog`; every other create flag keeps the
    accepted O2 refusal. `rerun_issue` stays the strict gate (inherited).
    """

    def create_backlog_issue(self, *, title: str, description: str,
                             parent_issue_id: str | None = None,
                             project_id: str | None = None,
                             priority: str | None = None) -> dict:
        title = o2._require_text(title, "title")
        if not isinstance(description, str) or not description.strip():
            raise o2.IntentError("description must be a non-blank string")
        for field, value in (("title", title), ("description", description)):
            if _MENTION_RE.search(value):
                raise o2.IntentError(
                    f"{field} carries a mention link; the R0B create path "
                    "must contain no mention of any kind", field=field)
        argv = ["issue", "create", "--title", title,
                "--description-file", "", "--status", R0B_CREATE_STATUS_VALUE,
                "--output", "json"]
        if parent_issue_id is not None:
            argv += ["--parent", o2._bare_id(parent_issue_id,
                                             "parent_issue_id")]
        if project_id is not None:
            argv += ["--project", o2._require_text(project_id,
                                                   "project_id", 64)]
        if priority is not None:
            argv += ["--priority", o2._require_text(priority, "priority", 40)]
        present = {token for token in argv if token.startswith("--")}
        if present - set(CREATE_FLAGS):
            raise o2.IntentError(
                "create flag is outside the R0B backlog-create allowlist",
                flags=sorted(present - set(CREATE_FLAGS)))
        if argv.count("--status") != 1 or \
                argv[argv.index("--status") + 1] != R0B_CREATE_STATUS_VALUE:
            raise o2.IntentError(
                "R0B create requires exactly the literal --status backlog")
        temp_name = hashlib.sha1(description.encode("utf-8")).hexdigest()[:12]
        temp_path = self.workdir / f".u12r0b-create-{temp_name}.md"
        try:
            temp_path.write_bytes(description.encode("utf-8"))
            resolved = temp_path.resolve()
            if not resolved.is_relative_to(self.workdir.resolve()):
                raise o2.IntentError(
                    "temp description escaped the working directory")
            argv[argv.index("--description-file") + 1] = str(temp_path)
            code, out, err = self._run(argv)
        finally:
            temp_path.unlink(missing_ok=True)
        if code != 0:
            raise o2.ReceiptAmbiguousError(
                f"issue create failed (exit {code}); creation boundary is "
                "ambiguous until read-only discovery", stderr=str(err)[:160])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise o2.ReceiptAmbiguousError(
                f"issue create returned non-JSON output: {exc}") from exc
        if not isinstance(data, dict):
            raise o2.ReceiptAmbiguousError(
                "issue create response is not an object")
        missing = [f for f in dispatch.CREATE_CONTRACT_FIELDS if f not in data]
        if missing:
            raise o2.ReceiptAmbiguousError(
                "issue create response is missing contract fields",
                missing=",".join(missing))
        if data.get("assignee_id") not in (None, "") or \
                data.get("assignee") not in (None, ""):
            raise o2.IntentError(
                "created target unexpectedly carries an assignee; the R0B "
                "create path creates unassigned targets only")
        return data


# ---------------------------------------------------------------------------
# R0B contract validation
# ---------------------------------------------------------------------------
def _require_dict(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise R0BValidationRefused(f"{field} must be an object")
    return value


def _require_digest(value, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.match(value):
        raise R0BValidationRefused(f"{field} is not an exact sha256 digest")
    return value


def _require_uuid(value, field: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise R0BValidationRefused(f"{field} is not a UUID")
    return value


def _require_text(value, field: str, limit: int = 400) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise R0BValidationRefused(
            f"{field} must be a non-blank string of at most {limit} chars")
    return value


def validate_creation_spec(spec: dict) -> dict:
    spec = _require_dict(spec, "creation_spec")
    source_body = spec.get("source_body")
    transport_profile = spec.get("transport_profile")
    transport_preparation = None
    if source_body is not None or transport_profile is not None:
        if transport_profile is None:
            raise R0BValidationRefused(
                "source_body requires the named transport_profile for "
                "persisted source-to-transport preparation")
        prepared = prepare_transport_body(source_body,
                                          profile=transport_profile)
        declared_body = spec.get("body")
        if declared_body is not None and \
                declared_body != prepared["transport_body"]:
            raise R0BValidationRefused(
                "creation_spec.body is not the prepared transport body; the "
                "persisted bytes must be the sent bytes", profile=transport_profile)
        declared_digest = spec.get("body_digest")
        if declared_digest is not None and \
                declared_digest != prepared["transport_body_lf_digest"]:
            raise R0BValidationRefused(
                "creation_spec.body_digest is not the prepared transport "
                "digest", profile=transport_profile)
        body = prepared["transport_body"]
        body_digest = prepared["transport_body_lf_digest"]
        transport_preparation = {k: v for k, v in prepared.items()
                                 if k != "transport_body"}
    else:
        body = spec.get("body")
        body_digest = spec.get("body_digest")
    out = {
        "title": _require_text(spec.get("title"), "creation_spec.title"),
        "body": _require_text(body, "creation_spec.body",
                              limit=200000),
        "parent_issue_id": _require_text(spec.get("parent_issue_id"),
                                         "creation_spec.parent_issue_id"),
        "project_id": spec.get("project_id"),
        "priority": spec.get("priority"),
        "logical_task_key": _require_text(spec.get("logical_task_key"),
                                          "creation_spec.logical_task_key"),
        "target_role": _require_text(spec.get("target_role"),
                                     "creation_spec.target_role"),
        "target_agent_id": _require_uuid(spec.get("target_agent_id"),
                                         "creation_spec.target_agent_id"),
        "publisher_agent_id": _require_uuid(spec.get("publisher_agent_id"),
                                            "creation_spec.publisher_agent_id"),
        "status": _require_text(spec.get("status"), "creation_spec.status"),
        "marker": _require_text(spec.get("marker"), "creation_spec.marker"),
        "body_digest": _require_digest(body_digest,
                                       "creation_spec.body_digest"),
        "creation_task_ref": _require_text(spec.get("creation_task_ref"),
                                           "creation_spec.creation_task_ref"),
        "authority_refs": spec.get("authority_refs"),
    }
    if transport_preparation is not None:
        out["transport_preparation"] = transport_preparation
    if out["target_role"] != EXECUTION_ROLE:
        raise R0BValidationRefused(
            "creation_spec.target_role must be the execution role",
            target_role=out["target_role"])
    if out["status"] != BACKLOG_STATUS:
        raise R0BValidationRefused("creation_spec.status must be backlog")
    if not _MARKER_RE.match(out["marker"]):
        raise R0BValidationRefused("creation_spec.marker is not an R0BI marker")
    if out["marker"] not in out["body"]:
        raise R0BValidationRefused(
            "creation_spec.body does not embed the standalone intent marker")
    if digest_text_lf(out["body"]) != out["body_digest"]:
        raise R0BValidationRefused(
            "creation_spec.body_digest does not match the exact body bytes")
    if not isinstance(out["authority_refs"], list) or not out["authority_refs"]:
        raise R0BValidationRefused(
            "creation_spec.authority_refs must be a non-empty list")
    for index, ref in enumerate(out["authority_refs"]):
        ref = _require_dict(ref, f"creation_spec.authority_refs[{index}]")
        _require_text(ref.get("ref"), f"authority_refs[{index}].ref")
        _require_digest(ref.get("digest"), f"authority_refs[{index}].digest")
    artifact = _require_dict(spec.get("artifact_dependency"),
                             "creation_spec.artifact_dependency")
    artifact_digest = _require_digest(artifact.get("digest"),
                                      "artifact_dependency.digest")
    out["artifact_dependency"] = {
        "digest": artifact_digest,
        "entries": artifact.get("entries") or {},
    }
    if artifact_digest != digest(artifact.get("entries") or {}):
        raise R0BValidationRefused(
            "artifact_dependency.digest does not match its entry map")
    authority_entry = out["artifact_dependency"]["entries"].get(
        AUTHORITY_ARTIFACT_PATH)
    if not isinstance(authority_entry, dict) or \
            authority_entry.get("digest_method") != AUTHORITY_DIGEST_METHOD:
        raise R0BValidationRefused(
            "creation_spec.artifact_dependency must bind the authoritative "
            "readiness manifest with the accepted canonical method; the "
            "arm/trigger preflight requires it",
            path=AUTHORITY_ARTIFACT_PATH)
    return out


def validate_context(result: dict, request: dict, check: dict, *,
                     role: str, task_ref: str,
                     artifact_digest: str) -> dict:
    """Validate one C/E context package against frozen T00 + SELF_CHECK."""
    errors = note.validate_result_envelope(result)
    if errors:
        raise R0BValidationRefused(
            "context result fails frozen validation/integrity",
            role=role, errors=errors[:6])
    if result.get("status") != "READY":
        raise R0BValidationRefused("context result is not READY",
                                   status=result.get("status"))
    if result.get("role") != role:
        raise R0BValidationRefused("context role mismatch",
                                   expected=role, found=result.get("role"))
    if result.get("task_ref") != task_ref:
        raise R0BValidationRefused("context task_ref mismatch",
                                   expected=task_ref,
                                   found=result.get("task_ref"))
    if request.get("kind") != "prepare_handoff_request":
        raise R0BValidationRefused(
            "context request kind is not prepare_handoff_request")
    if ((request.get("target") or {}).get("role")) != role:
        raise R0BValidationRefused("context request role mismatch",
                                   expected=role)
    if request.get("task_ref") != task_ref:
        raise R0BValidationRefused("context request task_ref mismatch",
                                   expected=task_ref)
    fingerprint = chandoff.fingerprint_from_request(request)
    if (result.get("built_from") or {}).get("task_fingerprint") != fingerprint:
        raise R0BValidationRefused(
            "context built_from.task_fingerprint does not match recomputation")
    if not isinstance(check, dict) or check.get("status") != "READY" or \
            check.get("action") != "USE_EXISTING":
        raise R0BValidationRefused(
            "context SELF_CHECK is not READY/USE_EXISTING",
            status=(check or {}).get("status"),
            reasons=(check or {}).get("reasons"))
    if check.get("package_id") not in (None, result.get("package_id")):
        raise R0BValidationRefused("context SELF_CHECK package_id mismatch")
    return {
        "package_id": result["package_id"],
        "task_ref": result["task_ref"],
        "role": result["role"],
        "status": result["status"],
        "built_from": result["built_from"],
        "envelope_digest": digest(result),
        "request_digest": digest(request),
        "self_check": {"status": check["status"], "action": check["action"],
                       "reasons": check.get("reasons") or []},
        "artifact_dependency_digest": artifact_digest,
    }


def r0b_creation_context(creation_context: dict, spec: dict, *,
                         artifact_digest: str) -> dict:
    creation_context = _require_dict(creation_context, "creation_context")
    result = creation_context.get("result")
    request = creation_context.get("request")
    check = creation_context.get("self_check")
    validated = validate_context(result, request, check,
                                 role=CREATION_ROLE,
                                 task_ref=spec["creation_task_ref"],
                                 artifact_digest=artifact_digest)
    validated["source_task_id"] = creation_context.get("source_task_id")
    validated["decision_comment_ids"] = list(
        creation_context.get("decision_comment_ids") or [])
    validated["result"] = result
    validated["request"] = request
    return validated


def r0b_execution_context(execution_context: dict, spec: dict, *,
                          artifact_digest: str) -> dict:
    execution_context = _require_dict(execution_context, "execution_context")
    result = execution_context.get("result")
    request = execution_context.get("request")
    check = execution_context.get("self_check")
    target_ref = execution_context.get("target_task_ref")
    _require_text(target_ref, "execution_context.target_task_ref")
    validated = validate_context(result, request, check,
                                 role=EXECUTION_ROLE, task_ref=target_ref,
                                 artifact_digest=artifact_digest)
    validated["target_task_ref"] = target_ref
    validated["result"] = result
    validated["request"] = request
    return validated


def _validate_execution_binding(intent: dict, data: dict) -> dict:
    """Validate a committed v1.1 forward-recovery binding end to end.

    This is the narrow compatibility receipt: only a record whose original
    adapter pin is the exact known predecessor, whose v1.1 proof is complete,
    recomputable and bound to its v1.1 disposition, and whose accepted
    execution identity matches these executing bytes may execute under these
    bytes. Missing, edited, copied, cross-intent or hash-only proof refuses;
    an old v1.0 proof is preserved for inspection but is never silently
    upgraded into recovery authority.
    """
    execution = data.get("execution_binding")
    if not isinstance(execution, dict):
        raise R0BContractError(
            "record carries no execution binding to validate",
            intent_id=intent["intent_id"])
    if execution.get("schema") != EXECUTION_BINDING_SCHEMA:
        raise R0BContractError(
            "execution binding schema is unsupported; an old or hash-only "
            "binding is not accepted (no silent upgrade)",
            schema=execution.get("schema"))
    if execution.get("contract_version") != CONTRACT_VERSION:
        raise R0BContractError(
            "execution binding contract version is unsupported",
            contract_version=execution.get("contract_version"))
    running = adapter_digest()
    if execution.get("adapter_digest") != running:
        raise R0BDowngradeRefused(
            "execution binding names different execution adapter bytes; "
            "refusing to execute under a changed adapter (fail closed)",
            intent_id=intent["intent_id"])
    if execution.get("original_contract_version") != \
            PREDECESSOR_CONTRACT_VERSION or \
            execution.get("original_adapter_digest") != \
            PREDECESSOR_ADAPTER_DIGEST or \
            execution.get("predecessor_commit") != PREDECESSOR_ADAPTER_COMMIT:
        raise R0BDowngradeRefused(
            "execution binding does not name the exact known predecessor "
            "contract/digest/commit", intent_id=intent["intent_id"])
    if data.get("adapter_digest") != PREDECESSOR_ADAPTER_DIGEST:
        raise R0BValidationRefused(
            "the original adapter pin was relabeled; the predecessor pin must "
            "be preserved as recorded", intent_id=intent["intent_id"])
    proof = data.get("recovery_proof")
    if not isinstance(proof, dict):
        raise R0BContractError(
            "execution binding carries no committed recovery proof",
            intent_id=intent["intent_id"])
    if proof.get("schema") != RECOVERY_PROOF_SCHEMA:
        raise R0BContractError(
            "recovery proof schema is unsupported; an old or hash-only proof "
            "is not accepted recovery authority (no silent upgrade)",
            schema=proof.get("schema"))
    recorded = proof.get("proof_digest")
    recomputed = digest({k: v for k, v in proof.items()
                         if k != "proof_digest"})
    if not isinstance(recorded, str) or recorded != recomputed:
        raise R0BValidationRefused(
            "the committed recovery proof digest does not reproduce; the "
            "record is edited or incomplete", intent_id=intent["intent_id"])
    if execution.get("recovery_proof_digest") != recorded:
        raise R0BValidationRefused(
            "the execution binding does not reference the committed recovery "
            "proof", intent_id=intent["intent_id"])
    if proof.get("contract_version") != CONTRACT_VERSION:
        raise R0BContractError(
            "recovery proof contract version is unsupported",
            intent_id=intent["intent_id"])
    if proof.get("intent_id") != intent["intent_id"]:
        raise R0BValidationRefused(
            "the committed recovery proof belongs to a different intent "
            "(cross-intent proof refused)", intent_id=intent["intent_id"])
    if proof.get("state_before") != o2.S_CREATE_AMBIGUOUS:
        raise R0BValidationRefused(
            "the committed recovery proof does not bind the supported prior "
            "phase CREATE_AMBIGUOUS", intent_id=intent["intent_id"])
    target = data.get("target_binding") or {}
    target_id = target.get("issue_id")
    if not isinstance(target_id, str) or \
            proof.get("target", {}).get("issue_id") != target_id or \
            intent["fields"].get("issue_id") != target_id:
        raise R0BValidationRefused(
            "the committed recovery proof, target binding and intent field do "
            "not name the same target", intent_id=intent["intent_id"])
    before = proof.get("intent_revision_before")
    if not isinstance(before, int) or \
            execution.get("transition_revision") != before + 1 or \
            intent["revision"] < before + 1:
        raise R0BValidationRefused(
            "the committed recovery proof is not bound to the recorded intent "
            "revision/transition", intent_id=intent["intent_id"])
    transport = proof.get("transport") or {}
    if execution.get("transport_profile") != transport.get("profile") or \
            execution.get("effective_transport_body_digest") != \
            transport.get("effective_transport_body_digest") or \
            execution.get("effective_transport_body_lf_digest") != \
            transport.get("effective_transport_body_lf_digest"):
        raise R0BValidationRefused(
            "the execution binding does not carry the proof's effective "
            "transport identity", intent_id=intent["intent_id"])
    decision = proof.get("decision") or {}
    if execution.get("recovery_decision_digest") != \
            decision.get("decision_digest"):
        raise R0BValidationRefused(
            "the execution binding does not reference the committed recovery "
            "decision", intent_id=intent["intent_id"])
    if proof.get("decision_digest") != decision.get("decision_digest"):
        raise R0BValidationRefused(
            "the recovery proof does not bind its recovery decision",
            intent_id=intent["intent_id"])
    try:
        decision = validate_recovery_decision(proof.get("decision"))
    except R0BError as exc:
        raise R0BValidationRefused(
            "the committed recovery disposition no longer validates: "
            f"{exc.message}", intent_id=intent["intent_id"])
    # -- receipt decision --------------------------------------------------
    receipt = proof.get("receipt")
    if not isinstance(receipt, dict) or \
            receipt.get("status") != decision["original_receipt_body_status"]:
        raise R0BValidationRefused(
            "the committed proof receipt status does not match the "
            "disposition", intent_id=intent["intent_id"])
    if receipt["status"] == RECEIPT_STATUS_PERSISTED:
        if not isinstance(receipt.get("body"), dict) or \
                receipt.get("body", {}).get("id") != target_id:
            raise R0BValidationRefused(
                "the committed persisted receipt body is missing or names a "
                "different target", intent_id=intent["intent_id"])
        if receipt.get("body_digest") != digest(receipt["body"]):
            raise R0BValidationRefused(
                "the committed receipt body digest does not reproduce",
                intent_id=intent["intent_id"])
    elif receipt["status"] == RECEIPT_STATUS_NOT_PERSISTED:
        if decision.get("receipt_limit_scope") != RECEIPT_LIMIT_SCOPE:
            raise R0BValidationRefused(
                "an unpersisted receipt body is only admissible under the "
                "exact bounded receipt-limit disposition",
                intent_id=intent["intent_id"])
    # -- execution identity -------------------------------------------------
    accepted = decision["accepted_execution"]
    authority = proof.get("execution_authority") or {}
    resolution = authority.get("blob_resolution") or {}
    if execution.get("accepted_execution_commit") != accepted["commit"] or \
            authority.get("adapter_commit") != accepted["commit"]:
        raise R0BValidationRefused(
            "the committed execution commit is not the disposition's "
            "accepted execution commit", intent_id=intent["intent_id"])
    if execution.get("accepted_execution_adapter_digest") != \
            accepted["adapter_digest"]:
        raise R0BValidationRefused(
            "the committed accepted adapter digest does not match the "
            "disposition", intent_id=intent["intent_id"])
    if authority.get("adapter_digest") != running:
        raise R0BValidationRefused(
            "the committed proof execution adapter digest does not match "
            "these executing bytes", intent_id=intent["intent_id"])
    if resolution.get("ok") is not True or \
            resolution.get("commit") != accepted["commit"] or \
            resolution.get("adapter_digest") != running or \
            resolution.get("resolver") not in EXECUTION_RESOLVERS:
        raise R0BValidationRefused(
            "the committed execution blob resolution is missing, mismatched "
            "or fabricated; the exact accepted identity is required",
            intent_id=intent["intent_id"])
    # -- shared-history reconstructability ---------------------------------
    shared = proof.get("shared_history")
    if not isinstance(shared, dict) or not shared.get("records") or \
            not isinstance(shared.get("original_create"), dict):
        raise R0BValidationRefused(
            "the committed proof carries no inline shared-history "
            "classification; a hash/count-only proof is not accepted",
            intent_id=intent["intent_id"])
    if shared.get("classification_digest") != shared_history_digest(shared):
        raise R0BValidationRefused(
            "the committed shared-history classification does not recompute; "
            "the proof is edited or incomplete", intent_id=intent["intent_id"])
    for entry in shared["records"]:
        if not isinstance(entry, dict) or \
                entry.get("digest") != digest(entry.get("record")):
            raise R0BValidationRefused(
                "a committed shared-history record digest does not reproduce",
                intent_id=intent["intent_id"])
    original = shared["original_create"]
    if original.get("command_digest") != digest(original.get("command")) or \
            original.get("result_digest") != digest(original.get("result")):
        raise R0BValidationRefused(
            "the committed original create command/result record digest does "
            "not reproduce", intent_id=intent["intent_id"])
    if original.get("result_seq") != original.get("command_seq", 0) + 1:
        raise R0BValidationRefused(
            "the committed original create command/result pair is not the "
            "uniquely correlated adjacent pair", intent_id=intent["intent_id"])
    if not isinstance(original.get("command"), dict) or \
            not isinstance(original.get("result"), dict) or \
            original["result"].get("exit_code") != 0:
        raise R0BValidationRefused(
            "the committed original create result is missing or not the "
            "successful result", intent_id=intent["intent_id"])
    pair = decision["original_create_pair"]
    if original.get("command_seq") != pair["command_seq"] or \
            original.get("command_digest") != pair["command_digest"] or \
            original.get("result_seq") != pair["result_seq"] or \
            original.get("result_digest") != pair["result_digest"]:
        raise R0BValidationRefused(
            "the committed shared-history pair does not match the audited "
            "disposition pair", intent_id=intent["intent_id"])
    # -- inline observation reconstructability ------------------------------
    observations = proof.get("observations")
    if not isinstance(observations, dict) or \
            not isinstance(observations.get("digests"), dict):
        raise R0BValidationRefused(
            "the committed proof carries no inline observations; hash/count "
            "summaries are not substitutes", intent_id=intent["intent_id"])
    obs_digests = observations["digests"]
    sections = {
        "target_issue": (observations.get("target_issue"), dict),
        "target_recheck": (observations.get("target_recheck"), dict),
        "comments": (observations.get("comments"), list),
        "activities": (observations.get("activities"), list),
        "runs": (observations.get("runs"), list),
        "discovery": (observations.get("discovery"), dict),
        "raw_responses": (observations.get("raw_responses"), list),
    }
    for name, (body, kind) in sections.items():
        if not isinstance(body, kind) or obs_digests.get(name) != digest(body):
            raise R0BValidationRefused(
                f"the committed {name} observations are missing, edited, "
                "truncated or do not recompute", intent_id=intent["intent_id"])
    if observations["target_issue"].get("id") != target_id or \
            observations["target_recheck"].get("id") != target_id:
        raise R0BValidationRefused(
            "the committed target observations do not name the bound target",
            intent_id=intent["intent_id"])
    if proof.get("observed", {}).get("observations_digest") != \
            digest(observations):
        raise R0BValidationRefused(
            "the committed observations digest does not reproduce",
            intent_id=intent["intent_id"])
    # -- original create attempt cross-checks --------------------------------
    original_attempt = proof.get("original") or {}
    if original_attempt.get("create_attempt_digest") != digest(
            original_attempt.get("create_attempt") or {}):
        raise R0BValidationRefused(
            "the recovery proof's sole create attempt digest does not "
            "reproduce", intent_id=intent["intent_id"])
    if original_attempt.get("create_attempt", {}).get("body_digest") != \
            (data.get("creation_spec") or {}).get("body_digest"):
        raise R0BValidationRefused(
            "the recovery proof's sole create attempt does not match the "
            "preserved original body digest", intent_id=intent["intent_id"])
    return execution


def validate_recovery_decision(decision: dict) -> dict:
    """Validate one immutable create-recovery disposition (v1.1).

    The disposition references the accepted create-recovery design and the
    accepted evidence-correction decision, names the exact predecessor pins,
    the exact intent/target/revisions, the audited ledger prefix and the
    uniquely correlated original create command/result pair, declares the
    original receipt-body status and the accepted execution identity. Every
    field is revalidated by the operation: the disposition is a commitment,
    never an unchecked boolean.
    """
    decision = _require_dict(decision, "recovery_decision")
    out = {
        "schema": decision.get("schema"),
        "decision_id": _require_text(decision.get("decision_id"),
                                     "recovery_decision.decision_id"),
        "disposition": decision.get("disposition"),
        "scope": decision.get("scope"),
        "intent_id": decision.get("intent_id"),
        "expected_target_id": _require_uuid(
            decision.get("expected_target_id"),
            "recovery_decision.expected_target_id"),
        "expected_intent_revision": decision.get("expected_intent_revision"),
        "expected_target_revision": decision.get("expected_target_revision"),
        "expected_creator_id": _require_uuid(
            decision.get("expected_creator_id"),
            "recovery_decision.expected_creator_id"),
        "predecessor_commit": decision.get("predecessor_commit"),
        "predecessor_adapter_digest": decision.get(
            "predecessor_adapter_digest"),
        "design_ref": _require_text(decision.get("design_ref"),
                                    "recovery_decision.design_ref"),
        "design_digest": _require_digest(decision.get("design_digest"),
                                         "recovery_decision.design_digest"),
        "evidence_decision_ref": _require_text(
            decision.get("evidence_decision_ref"),
            "recovery_decision.evidence_decision_ref"),
        "evidence_decision_digest": _require_digest(
            decision.get("evidence_decision_digest"),
            "recovery_decision.evidence_decision_digest"),
        "original_receipt_body_status": decision.get(
            "original_receipt_body_status"),
        "receipt_limit_scope": decision.get("receipt_limit_scope"),
        "ledger_prefix": decision.get("ledger_prefix"),
        "original_create_pair": decision.get("original_create_pair"),
        "accepted_execution": decision.get("accepted_execution"),
        "approval_ref": _require_text(decision.get("approval_ref"),
                                      "recovery_decision.approval_ref"),
        "approved_by": _require_text(decision.get("approved_by"),
                                     "recovery_decision.approved_by"),
        "approved_at": _require_text(decision.get("approved_at"),
                                     "recovery_decision.approved_at"),
        "decision_digest": _require_digest(decision.get("decision_digest"),
                                           "recovery_decision.decision_digest"),
    }
    extras = sorted(set(decision) - set(out))
    if extras:
        raise R0BValidationRefused(
            "recovery decision carries unsupported fields; the disposition "
            "must stay within the exact schema", fields=extras)
    if out["schema"] != RECOVERY_DECISION_SCHEMA:
        raise R0BValidationRefused(
            "recovery decision schema is unsupported; an old or hash-only "
            "disposition is not accepted (no silent upgrade)",
            schema=out["schema"])
    if out["disposition"] != RECOVERY_DISPOSITION:
        raise R0BValidationRefused(
            "recovery decision disposition is unsupported",
            disposition=out["disposition"])
    if out["scope"] != RECOVERY_SCOPE:
        raise R0BValidationRefused(
            "recovery decision scope is unsupported", scope=out["scope"])
    if not isinstance(out["intent_id"], str) or \
            not re.match(r"^DI-[0-9a-f]{16}$", out["intent_id"]):
        raise R0BValidationRefused(
            "recovery decision intent_id is not DI-<16 hex>")
    for field in ("expected_intent_revision", "expected_target_revision"):
        if not isinstance(out[field], int) or out[field] < 0:
            raise R0BValidationRefused(
                f"recovery decision {field} must be a non-negative integer")
    if out["predecessor_commit"] != PREDECESSOR_ADAPTER_COMMIT:
        raise R0BValidationRefused(
            "recovery decision does not name the exact known predecessor "
            "commit", predecessor_commit=out["predecessor_commit"])
    if out["predecessor_adapter_digest"] != PREDECESSOR_ADAPTER_DIGEST:
        raise R0BValidationRefused(
            "recovery decision does not name the exact known predecessor "
            "adapter digest",
            predecessor_adapter_digest=out["predecessor_adapter_digest"])
    if out["design_ref"] != RECOVERY_DESIGN_REF or \
            out["design_digest"] != RECOVERY_DESIGN_DIGEST:
        raise R0BValidationRefused(
            "recovery decision does not reference the accepted design and its "
            "exact digest", design_ref=out["design_ref"],
            design_digest=out["design_digest"])
    if out["evidence_decision_ref"] != EVIDENCE_DECISION_REF or \
            out["evidence_decision_digest"] != EVIDENCE_DECISION_DIGEST:
        raise R0BValidationRefused(
            "recovery decision does not reference the accepted evidence "
            "correction decision and its exact digest",
            evidence_decision_ref=out["evidence_decision_ref"],
            evidence_decision_digest=out["evidence_decision_digest"])
    if out["original_receipt_body_status"] not in (
            RECEIPT_STATUS_PERSISTED, RECEIPT_STATUS_NOT_PERSISTED):
        raise R0BValidationRefused(
            "recovery decision original_receipt_body_status is unsupported",
            original_receipt_body_status=out["original_receipt_body_status"])
    if out["original_receipt_body_status"] == RECEIPT_STATUS_NOT_PERSISTED:
        if out["receipt_limit_scope"] != RECEIPT_LIMIT_SCOPE:
            raise R0BValidationRefused(
                "an unpersisted receipt body requires the exact bounded "
                "receipt-limit scope; it is never a general waiver",
                receipt_limit_scope=out["receipt_limit_scope"])
    elif out["receipt_limit_scope"] is not None:
        raise R0BValidationRefused(
            "a persisted receipt body must not carry a receipt-limit waiver "
            "scope", receipt_limit_scope=out["receipt_limit_scope"])
    prefix = out["ledger_prefix"]
    if not isinstance(prefix, dict) or set(prefix) != {"length", "digest"}:
        raise R0BValidationRefused(
            "recovery decision ledger_prefix must be exactly "
            "{length, digest}")
    if not isinstance(prefix.get("length"), int) or prefix["length"] < 1:
        raise R0BValidationRefused(
            "recovery decision ledger_prefix.length must be a positive "
            "integer")
    _require_digest(prefix.get("digest"),
                    "recovery_decision.ledger_prefix.digest")
    pair = out["original_create_pair"]
    if not isinstance(pair, dict) or set(pair) != {
            "command_seq", "command_digest", "result_seq", "result_digest"}:
        raise R0BValidationRefused(
            "recovery decision original_create_pair must be exactly "
            "{command_seq, command_digest, result_seq, result_digest}")
    for seq_field in ("command_seq", "result_seq"):
        if not isinstance(pair.get(seq_field), int) or pair[seq_field] < 1:
            raise R0BValidationRefused(
                f"recovery decision original_create_pair.{seq_field} must be "
                "a positive integer")
    for digest_field in ("command_digest", "result_digest"):
        _require_digest(pair.get(digest_field),
                        f"recovery_decision.original_create_pair.{digest_field}")
    accepted = out["accepted_execution"]
    if not isinstance(accepted, dict) or set(accepted) != {
            "commit", "adapter_digest"}:
        raise R0BValidationRefused(
            "recovery decision accepted_execution must be exactly "
            "{commit, adapter_digest}")
    if not isinstance(accepted.get("commit"), str) or \
            not re.match(r"^[0-9a-f]{40}$", accepted["commit"]):
        raise R0BValidationRefused(
            "recovery decision accepted_execution.commit must be a full "
            "40-hex git commit")
    _require_digest(accepted.get("adapter_digest"),
                    "recovery_decision.accepted_execution.adapter_digest")
    try:
        o2.parse_ts(out["approved_at"])
    except (ValueError, TypeError) as exc:
        raise R0BValidationRefused(
            f"recovery decision approved_at is not a timestamp: {exc}")
    recomputed = digest({k: v for k, v in out.items()
                         if k != "decision_digest"})
    if out["decision_digest"] != recomputed:
        raise R0BValidationRefused(
            "recovery decision self-digest does not reproduce; the "
            "disposition is edited or incomplete",
            expected=recomputed, found=out["decision_digest"])
    return out


def validate_intent_record(intent: dict, *, require=(),
                           executable: bool = False) -> dict:
    """Every entry/resume validates the namespaced data. Never downgrade.

    `executable=False` is the audit/recovery read: a known-predecessor record
    (U12-R0B/1.0 with the exact predecessor pin) is readable/classifiable but
    only `recover_created_target` may execute it. `executable=True` requires
    either current recorded bytes or a committed forward recovery.
    """
    data = intent["fields"].get(R0B_FIELD)
    if not isinstance(data, dict):
        raise R0BContractError(
            "intent is not tagged with an R0B contract; the R0B forward "
            "adapter refuses to execute it (no downgrade)",
            intent_id=intent["intent_id"])
    version = data.get("contract_version")
    if version == CONTRACT_VERSION:
        if isinstance(data.get("execution_binding"), dict):
            _validate_execution_binding(intent, data)
        elif data.get("adapter_digest") != adapter_digest():
            raise R0BDowngradeRefused(
                "R0B intent was recorded by different adapter bytes; refusing "
                "to resume under a changed adapter (fail closed)",
                intent_id=intent["intent_id"])
    elif version == PREDECESSOR_CONTRACT_VERSION:
        if data.get("adapter_digest") != PREDECESSOR_ADAPTER_DIGEST:
            raise R0BDowngradeRefused(
                "intent names a different adapter than the exact known "
                "predecessor; no wildcard predecessor is accepted",
                intent_id=intent["intent_id"])
        if executable:
            raise R0BCompatibilityRefused(
                "intent was recorded by the known predecessor adapter and "
                "carries no committed forward recovery; only "
                "recover_created_target may execute it (no automatic "
                "migration)", intent_id=intent["intent_id"])
    else:
        raise R0BContractError(
            "unsupported R0B contract version; stop, never downgrade",
            contract_version=version)
    for section in require:
        if not isinstance(data.get(section), dict):
            raise R0BContractError(
                f"R0B intent is missing the {section} section",
                intent_id=intent["intent_id"])
    return data


# ---------------------------------------------------------------------------
# evidence bundles + publication predicate
# ---------------------------------------------------------------------------
def comment_record(doc: dict) -> dict:
    return {
        "id": doc.get("id"),
        "revision": doc.get("revision"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "parent_id": doc.get("parent_id"),
        "author_id": doc.get("author_id"),
        "author_type": doc.get("author_type"),
        "source_task_id": doc.get("source_task_id"),
        "resolved_at": doc.get("resolved_at"),
        "content_digest": digest_text_lf(doc.get("content") or ""),
    }


def collect_evidence(reader: EvidenceReader, issue_id: str) -> dict:
    issue = reader.issue_get(issue_id)
    raw_comments = reader.comments_full(issue_id)
    comments = [comment_record(doc) for doc in raw_comments]
    activities = reader.timeline_activities(issue_id)
    runs = reader.runs_full(issue_id)
    return {
        "issue": issue,
        "comments": comments,
        "activities": activities,
        "runs": runs,
        "commands": list(reader.commands),
        "_raw_comments": [{"id": doc.get("id"), "content": doc.get("content")}
                          for doc in raw_comments],
    }


def evidence_projection(evidence: dict) -> dict:
    return {
        "issue_id": (evidence.get("issue") or {}).get("id"),
        "issue_revision": (evidence.get("issue") or {}).get("revision"),
        "comment_ids": sorted(c["id"] for c in evidence.get("comments") or []),
        "comment_fingerprints": sorted(
            canonical_json({k: c.get(k) for k in
                            ("id", "revision", "updated_at", "parent_id",
                             "author_id", "author_type", "source_task_id",
                             "content_digest", "resolved_at")})
            for c in evidence.get("comments") or []),
        "activity_ids": sorted(a["id"] for a in
                               evidence.get("activities") or []),
        "run_ids": sorted(r["id"] for r in evidence.get("runs") or []),
    }


def evidence_digest(evidence: dict) -> str:
    return digest(evidence_projection(evidence))


def ledger_evidence(evidence: dict) -> dict:
    """The durable, content-free form recorded in the ledger attempt event."""
    return {
        "issue": evidence["issue"],
        "comments": evidence["comments"],
        "activities": evidence["activities"],
        "runs": evidence["runs"],
    }


def issue_projection(issue: dict) -> dict:
    return {k: v for k, v in issue.items()
            if k not in VOLATILE_ISSUE_FIELDS}


def _issue_diff(before: dict, after: dict) -> list:
    keys = sorted(set(before) | set(after))
    changes = []
    for key in keys:
        if key in VOLATILE_ISSUE_FIELDS:
            continue
        if before.get(key) != after.get(key):
            changes.append(key)
    return changes


def _content_of(after: dict, comment_id: str) -> str:
    for doc in after.get("_raw_comments") or []:
        if doc.get("id") == comment_id:
            return doc.get("content") or ""
    raise PublicationProvenanceIncomplete(
        "note content is not available in the collected evidence")


def evaluate_publication_predicate(
        *, attempt_count: int, attempt: dict, before: dict, after: dict,
        recheck: dict, expected: dict, artifact_ok: bool,
        fingerprint_ok: bool, self_check_ok: bool) -> dict:
    """The YZT-83 publication acceptance predicate (all conditions or stop).

    Pure function over already-collected evidence. Returns
    `{"ok", "code", "detail", "proof"}`. Every unknown/ambiguous condition
    fails closed; revision attribution is delta-exclusion based because the
    documented CLI has no revision-to-event linkage.
    """
    def fail(code, detail):
        return {"ok": False, "code": code, "detail": detail, "proof": None}

    if attempt_count != 1:
        return fail(PUB_INCOMPLETE,
                    f"expected exactly one publication attempt, found "
                    f"{attempt_count}")
    for name, evidence in (("before", before), ("after", after)):
        if not isinstance(evidence, dict) or not evidence.get("issue"):
            return fail(PUB_INCOMPLETE, f"{name} evidence is missing")
    if not artifact_ok or not fingerprint_ok or not self_check_ok:
        return fail(PUB_INCOMPLETE,
                    "artifact/fingerprint/self-check recheck did not hold: "
                    f"artifact_ok={artifact_ok} fingerprint_ok={fingerprint_ok} "
                    f"self_check_ok={self_check_ok}")

    matches = [c for c in after["comments"]
               if c.get("content_digest") == expected["body_digest"]]
    if not matches:
        return fail(PUB_NOTE_NOT_FOUND,
                    "no publication is visible for this attempt")
    if len(matches) > 1:
        return fail(PUB_DUPLICATE_NOTE,
                    f"{len(matches)} identical note candidates; never choose "
                    "the latest")
    candidate = matches[0]
    if expected.get("note_comment_id") is not None and \
            candidate.get("id") != expected.get("note_comment_id"):
        return fail(PUB_INCOMPLETE,
                    "visible note id differs from the recorded response id")
    if candidate.get("revision") != 1 or \
            candidate.get("updated_at") != candidate.get("created_at"):
        return fail(PUB_INCOMPLETE,
                    "visible note was edited or carries a non-original revision")
    if candidate.get("parent_id") != expected.get("parent_id"):
        return fail(PUB_INCOMPLETE, "visible note thread/parent mismatch")
    if candidate.get("author_id") != expected.get("author_id") or \
            candidate.get("author_type") != expected.get("author_type"):
        return fail(PUB_INCOMPLETE,
                    "visible note author does not match the authorized "
                    "publisher")
    if expected.get("source_task_id") is not None and \
            candidate.get("source_task_id") != expected.get("source_task_id"):
        return fail(PUB_INCOMPLETE,
                    "visible note source_task_id does not match the "
                    "authorized source run")
    if candidate.get("id") in {c["id"] for c in before["comments"]}:
        return fail(PUB_INCOMPLETE,
                    "the bound note already existed before the attempt")

    before_projection = issue_projection(before["issue"])
    after_projection = issue_projection(after["issue"])
    changed = _issue_diff(before_projection, after_projection)
    if changed:
        return fail(PUB_UNAUTHORIZED_DELTA,
                    f"issue fields changed around the publication: {changed}")

    before_map = {c["id"]: c for c in before["comments"]}
    after_map = {c["id"]: c for c in after["comments"]}
    added = sorted(set(after_map) - set(before_map))
    removed = sorted(set(before_map) - set(after_map))
    if removed:
        return fail(PUB_UNAUTHORIZED_DELTA,
                    f"baseline comment(s) disappeared: {removed}")
    if added != [candidate["id"]]:
        return fail(PUB_UNAUTHORIZED_DELTA,
                    f"unexpected new comment(s) around publication: "
                    f"{[i for i in added if i != candidate['id']]}")
    for cid, previous in before_map.items():
        if canonical_json(after_map[cid]) != canonical_json(previous):
            return fail(PUB_UNAUTHORIZED_DELTA,
                        f"baseline comment {cid} changed (edit/revert) around "
                        "publication")

    before_act = {a["id"]: canonical_json(a) for a in before["activities"]}
    after_act = {a["id"]: canonical_json(a) for a in after["activities"]}
    new_actions = sorted(set(after_act) - set(before_act))
    gone_actions = sorted(set(before_act) - set(after_act))
    edited_actions = [i for i in set(before_act) & set(after_act)
                      if before_act[i] != after_act[i]]
    if new_actions or gone_actions or edited_actions:
        return fail(PUB_UNAUTHORIZED_DELTA,
                    "timeline activity delta around publication is not "
                    f"attributable (new={len(new_actions)} "
                    f"gone={len(gone_actions)} edited={len(edited_actions)})")

    if before["runs"] or after["runs"]:
        return fail(PUB_UNEXPECTED_RUN,
                    "run evidence is not empty around publication")

    if not isinstance(recheck, dict):
        return fail(PUB_INCOMPLETE, "post-collection issue re-read is missing")
    if recheck.get("revision") != after["issue"].get("revision"):
        return fail(PUB_MOVING_REVISION,
                    "issue revision moved during evidence collection")
    if _issue_diff(issue_projection(recheck), after_projection):
        return fail(PUB_MOVING_REVISION,
                    "issue fields moved during evidence collection")

    parsed = note._parse_record(_content_of(after, candidate["id"]))
    if not parsed.get("ok_record"):
        return fail(
            PUB_INCOMPLETE,
            "visible note does not parse as a complete CONTEXT_HANDOFF "
            "record: " + "; ".join(parsed.get("errors") or [])[:200])
    if canonical_json(parsed["envelope"]) != expected.get("envelope_canonical"):
        return fail(PUB_INCOMPLETE,
                    "visible note envelope differs from the prepared E")

    return {
        "ok": True,
        "code": PUB_OK,
        "detail": "",
        "proof": {
            "note_comment_id": candidate["id"],
            "note_revision": candidate["revision"],
            "author_id": candidate["author_id"],
            "author_type": candidate["author_type"],
            "source_task_id": candidate.get("source_task_id"),
            "body_digest": expected["body_digest"],
            "envelope_digest": expected["envelope_digest"],
            "before_issue_revision": before["issue"].get("revision"),
            "after_issue_revision": after["issue"].get("revision"),
            "revision_attribution": (
                "unchanged" if before["issue"].get("revision")
                == after["issue"].get("revision")
                else "single_delta_exclusion"),
            "comment_inventory_before": len(before["comments"]),
            "comment_inventory_after": len(after["comments"]),
            "activity_count_before": len(before["activities"]),
            "activity_count_after": len(after["activities"]),
            "run_ids_before": sorted(r["id"] for r in before["runs"]),
            "run_ids_after": sorted(r["id"] for r in after["runs"]),
            "evidence_before_digest": evidence_digest(before),
            "evidence_after_digest": evidence_digest(after),
            "recheck_digest": digest(issue_projection(recheck)),
            "attribution_boundary": (
                "delta-exclusion over the complete comment inventory, "
                "timeline activity set and issue projection; platform "
                "concurrency is not transactionally locked and an invisible "
                "concurrent write cannot be excluded by the adapter lease"),
            "artifact_dependency_digest": expected["artifact_dependency_digest"],
        },
    }


# ---------------------------------------------------------------------------
# fresh material preflight (YZT-83 accepted decision, forward repair)
# ---------------------------------------------------------------------------
def rebuild_artifact_dependency(entries, *, root, blob_reader) -> dict:
    """Rebuild the exact bound dependency map from the pinned blobs.

    A usable root/reader is mandatory on these consequential paths. A reader
    failure or missing blob is unavailable (BLOCKED); readable bytes that no
    longer satisfy the declared digest method are confirmed drift
    (REFRESH_REQUIRED). Returns `{"digest", "entries"}`.
    """
    if blob_reader is None and root is None:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "no usable artifact root/reader is configured; pinned-blob "
            "revalidation is mandatory before issuance", subject="artifact")
    reader = blob_reader or _git_blob_reader(root or ROOT)
    normalized = _normalize_artifact_entries(
        [dict(v, path=k) for k, v in entries.items()])
    mapping = {}
    for entry in normalized:
        path = entry["path"]
        try:
            data = reader(entry["commit"], path)
        except PreflightRefusal:
            raise
        except Exception as exc:  # noqa: BLE001 - reader failure is a stop
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"pinned artifact blob is unavailable at {path}: "
                f"{type(exc).__name__}: {exc}", subject="artifact", path=path)
        try:
            actual = _digest_blob(data, entry["digest_method"])
        except R0BError as exc:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                f"pinned bytes for {path} changed and no longer satisfy the "
                f"declared {entry['digest_method']} method: {exc}",
                subject="artifact", path=path)
        mapping[path] = {
            "commit": entry["commit"],
            "digest_method": entry["digest_method"],
            "sha256": actual,
        }
    return {"digest": digest(mapping), "entries": mapping}


def compare_artifact_dependency(bound_digest, bound_entries, rebuilt) -> dict:
    """Compare every rebuilt digest and the aggregate to the bound values."""
    if rebuilt["digest"] == bound_digest:
        return rebuilt
    mismatched = []
    for path in sorted(bound_entries):
        expected = (bound_entries.get(path) or {}).get("sha256")
        actual = (rebuilt["entries"].get(path) or {}).get("sha256")
        if actual != expected:
            mismatched.append({"path": path, "expected": expected,
                               "actual": actual})
    raise PreflightRefusal(
        o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
        "pinned artifact bytes changed or disappeared: "
        + ",".join(row["path"] for row in mismatched[:4]),
        subject="artifact", mismatched=mismatched[:4])


def preflight_request_check(data, current_request) -> dict:
    """Validate the fresh target-derived request against bound E.

    The caller builds the request through the same project mapping and
    decision-ref selection used for E; here its frozen fingerprint and every
    `built_from` revision are compared to the bound values.
    """
    execution = data.get("execution_context") or {}
    bound_request = execution.get("request")
    if not isinstance(bound_request, dict):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "the bound E request is not available in the intent record",
            subject="request")
    if current_request is None:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_PREFLIGHT_INPUT_MISSING,
            "a fresh target-derived current_request is required for this "
            "entrypoint", subject="request")
    if not isinstance(current_request, dict) or \
            current_request.get("kind") != "prepare_handoff_request":
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "current_request is not a prepare_handoff_request",
            subject="request")
    mismatched = []
    if current_request.get("task_ref") != bound_request.get("task_ref"):
        mismatched.append("task_ref")
    if ((current_request.get("target") or {}).get("role")
            != (bound_request.get("target") or {}).get("role")):
        mismatched.append("target.role")
    if current_request.get("project") != bound_request.get("project"):
        mismatched.append("project mapping")
    fresh_snapshot = current_request.get("task_snapshot") or {}
    bound_snapshot = bound_request.get("task_snapshot") or {}
    if (fresh_snapshot.get("relevant_decisions") or []) != \
            (bound_snapshot.get("relevant_decisions") or []):
        mismatched.append("selected decision refs")
    if mismatched:
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the current request no longer uses the bound E mapping: "
            + ", ".join(mismatched), subject="request",
            fields=mismatched)
    fingerprint = chandoff.fingerprint_from_request(current_request)
    bound_built = execution.get("built_from") or {}
    if fingerprint != bound_built.get("task_fingerprint"):
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the frozen task fingerprint changed: the current target snapshot "
            "no longer matches bound E", subject="request",
            fingerprint=fingerprint)
    fresh_built = chandoff.compute_built_from(current_request)
    drift = [key for key in ("task_fingerprint", "memory_revision",
                             "registry_revision", "role_profile_revision")
             if fresh_built.get(key) != bound_built.get(key)]
    if drift:
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "current context/profile/memory/registry revision drifted: "
            + ", ".join(drift), subject="request", fields=drift)
    return {"fingerprint": fingerprint, "built_from": fresh_built,
            "request_digest": digest(current_request)}


def preflight_self_check(data, current_request, current_findings) -> dict:
    """Run the frozen SELF_CHECK on bound E with the current Finding source.

    The adapter never substitutes an empty findings list for a missing live
    source; absent or malformed findings are a typed stop.
    """
    if current_findings is None:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_PREFLIGHT_INPUT_MISSING,
            "the current Finding source is required; the adapter never forces "
            "an empty findings list", subject="findings")
    if not isinstance(current_findings, list) or \
            any(not isinstance(item, dict) for item in current_findings):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "current_findings is not a list of Finding objects",
            subject="findings")
    execution = data.get("execution_context") or {}
    envelope = execution.get("result")
    if not isinstance(envelope, dict):
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "the bound E envelope is not available for SELF_CHECK",
            subject="context")
    sc_request = self_check_request_from_prepare(current_request)
    try:
        check = selfcheck.self_check(sc_request, packages=[envelope],
                                     findings=current_findings)
    except (o2.IntentError, ValueError) as exc:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            f"SELF_CHECK could not run on bound E: {type(exc).__name__}: {exc}",
            subject="context")
    status = check.get("status")
    action = check.get("action")
    if status != "READY" or action != "USE_EXISTING":
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "SELF_CHECK on bound E is not READY/USE_EXISTING: "
            f"status={status} action={action} "
            f"reasons={check.get('reasons')}", subject="context",
            self_check_status=status)
    if check.get("package_id") not in (None, execution.get("package_id")):
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "SELF_CHECK resolved a different package than bound E",
            subject="context")
    return {"status": status, "action": action,
            "reasons": list(check.get("reasons") or []),
            "package_id": check.get("package_id")}


def preflight_note_check(data, evidence) -> dict:
    """Re-read the exact bound note from the fresh comment inventory."""
    publication = data.get("publication_binding") or {}
    note_id = publication.get("note_comment_id")
    if not note_id:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "the intent has no bound publication note", subject="note")
    body_digest = publication.get("body_digest")
    candidates = [c for c in evidence.get("comments") or []
                  if c.get("id") == note_id]
    if not candidates:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "the bound publication note is not visible in the complete "
            "comment inventory", subject="note", note_comment_id=note_id)
    if len(candidates) > 1:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            "the bound publication note id appears more than once",
            subject="note", note_comment_id=note_id)
    candidate = candidates[0]
    problems = []
    if candidate.get("content_digest") != body_digest:
        problems.append("body digest")
    if candidate.get("revision") != publication.get("note_revision"):
        problems.append("original comment revision")
    if candidate.get("updated_at") != candidate.get("created_at"):
        problems.append("edited timestamp")
    if candidate.get("author_id") != publication.get("author_id") or \
            candidate.get("author_type") != publication.get("author_type"):
        problems.append("author")
    if candidate.get("source_task_id") != publication.get("source_task_id"):
        problems.append("source run")
    if candidate.get("parent_id") != publication.get("parent_id"):
        problems.append("thread/parent")
    if problems:
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the bound publication note drifted: " + ", ".join(problems),
            subject="note", note_comment_id=note_id,
            fields=problems)
    matching = [c for c in evidence.get("comments") or []
                if c.get("content_digest") == body_digest]
    if len(matching) != 1:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            f"{len(matching)} comments match the bound note body; duplicate "
            "or ambiguous publication evidence", subject="note",
            note_comment_id=note_id)
    content = _content_of(evidence, note_id)
    parsed = note._parse_record(content)
    if not parsed.get("ok_record"):
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the visible note does not parse as a complete CONTEXT_HANDOFF "
            "record: " + "; ".join(parsed.get("errors") or [])[:200],
            subject="note", note_comment_id=note_id)
    envelope_digest = digest(parsed["envelope"])
    if envelope_digest != publication.get("envelope_digest"):
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the visible note envelope differs from the bound E envelope",
            subject="note", note_comment_id=note_id,
            envelope_digest=envelope_digest)
    return {"note_comment_id": note_id,
            "note_revision": candidate.get("revision"),
            "body_digest": body_digest,
            "envelope_digest": envelope_digest,
            "source_task_id": candidate.get("source_task_id"),
            "comment_inventory": len(evidence.get("comments") or [])}


def preflight_issue_check(data, issue) -> dict:
    """Bind the fresh issue to the target role/agent and published baseline."""
    spec = data.get("creation_spec") or {}
    target = data.get("target_binding") or {}
    if issue.get("id") != target.get("issue_id"):
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "the target issue identity changed", subject="issue")
    problems = []
    if issue.get("parent_issue_id") != spec.get("parent_issue_id"):
        problems.append("parent")
    if spec.get("project_id") is not None and \
            issue.get("project_id") != spec.get("project_id"):
        problems.append("project")
    if issue.get("assignee_id") != spec.get("target_agent_id") or \
            issue.get("assignee_type") != "agent":
        problems.append("logical-role to exact-agent binding")
    status = issue.get("status_category") or issue.get("status")
    if status != BACKLOG_STATUS:
        problems.append(f"backlog status ({status})")
    if problems:
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "fresh target issue no longer matches the bound role/agent/status "
            "binding: " + ", ".join(problems), subject="issue",
            fields=problems)
    baseline = target.get("post_publication_projection")
    if not isinstance(baseline, dict):
        base_digest = target.get("post_publication_snapshot_digest")
        if not isinstance(base_digest, str):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the published issue baseline is absent from the intent "
                "record; the current projection cannot be compared",
                subject="issue")
        if digest(issue_projection(issue)) != base_digest:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                "the current issue projection differs from the published "
                "baseline digest", subject="issue")
        return {"projection_digest": base_digest, "changed_fields": []}
    changed = _issue_diff(baseline, issue_projection(issue))
    if changed:
        raise PreflightRefusal(
            o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
            "issue fields changed since the authorized publication: "
            + ", ".join(changed), subject="issue", fields=changed)
    return {"projection_digest": digest(issue_projection(issue)),
            "changed_fields": []}


# ---------------------------------------------------------------------------
# forward lifecycle factory
# ---------------------------------------------------------------------------
class R0BForwardFactory(strict.CanaryOrchestrator):
    """The only executable path for a tagged R0B intent.

    All lifecycle operations are here: record C, create once, bind ownership
    without start, bind E, publish once, confirm and bind the actual revision,
    arm, strict trigger, correlate. Every entry re-validates the namespaced
    contract and the adapter pin; the plain O2 preparation/publish transitions
    are refused for these intents.
    """

    def __init__(self, store, *, runner, note_runner=None,
                 executable: str = "multica", clock=None, workdir=None,
                 ttl_seconds: int = 300, artifact_blob_reader=None,
                 artifact_root=None, authority_reader=None,
                 execution_blob_resolver=None):
        super().__init__(store, runner=runner, executable=executable,
                         clock=clock, workdir=workdir,
                         ttl_seconds=ttl_seconds)
        self.boundary = R0BBoundary(runner, store, executable=executable,
                                    workdir=workdir)
        self.reader = EvidenceReader(runner, executable=executable)
        self.note_cli = note.NoteCli(executable=executable,
                                     runner=note_runner or runner)
        self.artifact_blob_reader = artifact_blob_reader
        self.artifact_root = artifact_root
        self.authority_reader = authority_reader
        if execution_blob_resolver is None:
            self.execution_blob_resolver = _git_blob_reader(ROOT)
            self.execution_resolver_kind = EXECUTION_RESOLVER_GIT
        else:
            self.execution_blob_resolver = execution_blob_resolver
            self.execution_resolver_kind = EXECUTION_RESOLVER_INJECTED

    # -- contract gates ------------------------------------------------------
    def _load(self, intent_id: str, *, require=(),
              executable: bool = True) -> dict:
        intent = self.store.get(intent_id)
        data = validate_intent_record(intent, require=require,
                                      executable=executable)
        stored_digest = intent["fields"].get("artifact_dependency_digest")
        spec = data.get("creation_spec") or {}
        bound = (spec.get("artifact_dependency") or {}).get("digest")
        if bound is not None and stored_digest not in (None, bound):
            raise R0BValidationRefused(
                "intent artifact_dependency_digest contradicts the creation "
                "spec", intent_id=intent_id)
        return intent

    def _stop(self, intent_id: str, state: str, reason: str, actor: str,
              *, fields=None, detail: str = "") -> dict:
        """Replay-safe typed stop: never illegally re-transition a stop state.

        Repeated recovery calls on an already-stopped intent return the same
        typed stop with zero side effects instead of raising, preserving the
        at-most-once contract.
        """
        intent = self.store.get(intent_id)
        if intent["state"] == state:
            return {"status": state, "reason": reason, "detail": detail,
                    "intent_id": intent_id, "replayed": True,
                    "side_effects": 0}
        if state not in o2.TRANSITIONS.get(intent["state"], ()):
            return {"status": intent["state"], "reason": reason,
                    "detail": detail, "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        return super()._stop(intent_id, state, reason, actor, fields=fields,
                             detail=detail)

    def validate(self, intent_id: str) -> dict:
        intent = self._load(intent_id, executable=False)
        data = validate_intent_record(intent, executable=False)
        return {
            "ok": True,
            "intent_id": intent_id,
            "state": intent["state"],
            "revision": intent["revision"],
            "contract_version": data.get("contract_version"),
            "adapter_digest": data.get("adapter_digest"),
            "phase": data.get("phase"),
            "sections": sorted(k for k, v in data.items()
                               if isinstance(v, dict)),
        }

    # -- fresh material preflight (mandatory at arm and unissued trigger) ----
    def _resolve_authority(self, authority_evidence) -> dict | None:
        """Obtain current authority evidence for this entrypoint.

        An injected read-only reader wins and is invoked fresh on every
        entrypoint; otherwise the caller must supply one explicit evidence
        input. Nothing is cached in the ledger and no substitute source is
        guessed.
        """
        if self.authority_reader is not None:
            try:
                return self.authority_reader.read(path=AUTHORITY_ARTIFACT_PATH)
            except PreflightRefusal:
                raise
            except Exception as exc:  # noqa: BLE001 - reader failure is a stop
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                    f"the authority reader failed: {type(exc).__name__}: {exc}",
                    subject="authority")
        return authority_evidence

    def _preflight_materials(self, intent_id: str, data: dict, *,
                             checkpoint: str, current_request=None,
                             current_findings=None,
                             authority_evidence=None) -> dict:
        """Collect and validate every fresh material source once, in order.

        Reads only: current issue/comments/timeline/runs, pinned artifact
        blobs, the current authority record, a fresh target-derived request
        and the current Finding source, the exact bound note and the issue
        projection. Raises `PreflightRefusal`; it never mutates external
        state and never issues a native call.
        """
        if checkpoint not in ("ARM", "TRIGGER"):
            raise R0BValidationRefused("unknown preflight checkpoint",
                                       checkpoint=checkpoint)
        issue_id = binding_issue_id(data)
        try:
            evidence = collect_evidence(self.reader, issue_id)
        except PreflightRefusal:
            raise
        except Exception as exc:  # noqa: BLE001 - incomplete evidence is a stop
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"fresh evidence collection is incomplete or unreadable: "
                f"{type(exc).__name__}: {exc}", subject="evidence")
        issue = evidence["issue"]
        try:
            recheck = self.reader.issue_get(issue_id)
        except Exception as exc:  # noqa: BLE001
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"post-collection issue re-read failed: "
                f"{type(exc).__name__}: {exc}", subject="evidence")
        if recheck.get("revision") != issue.get("revision") or \
                issue_projection(recheck) != issue_projection(issue):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the issue moved while fresh evidence was collected "
                f"(revision {issue.get('revision')} -> "
                f"{recheck.get('revision')}); the collection is not a stable "
                "basis", subject="evidence",
                revision_before=issue.get("revision"),
                revision_after=recheck.get("revision"))
        artifact = compare_artifact_dependency(
            data["artifact_dependency"]["digest"],
            data["artifact_dependency"]["entries"],
            rebuild_artifact_dependency(
                data["artifact_dependency"]["entries"],
                root=self.artifact_root,
                blob_reader=self.artifact_blob_reader))
        authority = validate_authority_evidence(
            self._resolve_authority(authority_evidence), data=data)
        request = preflight_request_check(data, current_request)
        check = preflight_self_check(data, current_request, current_findings)
        note_proof = preflight_note_check(data, evidence)
        issue_proof = preflight_issue_check(data, issue)
        if evidence["runs"]:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                "the target already carries run(s): "
                + ",".join(str(r.get("id"))
                           for r in evidence["runs"][:4]), subject="runs")
        return {
            "checkpoint": checkpoint,
            "evidence": evidence,
            "evidence_digest": evidence_digest(evidence),
            "issue": issue,
            "runs": [],
            "artifact_digest": artifact["digest"],
            "authority": authority,
            "request": request,
            "self_check": check,
            "note": note_proof,
            "issue_proof": issue_proof,
        }

    def _record_preflight(self, intent_id: str, actor: str,
                          bundle: dict) -> None:
        """Append the bounded diagnostic record; never reusable authority."""
        self.store.append_event(
            intent_id, E_PREFLIGHT, actor=actor, now=self.now(),
            data={
                "checkpoint": bundle["checkpoint"],
                "evidence_digest": bundle["evidence_digest"],
                "issue_revision": bundle["issue"].get("revision"),
                "artifact_dependency_digest": bundle["artifact_digest"],
                "authority": bundle["authority"],
                "request_digest": bundle["request"]["request_digest"],
                "fingerprint": bundle["request"]["fingerprint"],
                "self_check": dict(bundle["self_check"],
                                   reasons=bundle["self_check"]["reasons"][:8]),
                "note": bundle["note"],
                "issue_projection_digest":
                    bundle["issue_proof"]["projection_digest"],
                "run_ids": [r.get("id") for r in bundle["runs"]],
            })

    def _refuse_preflight(self, intent_id: str, refusal: PreflightRefusal,
                          actor: str) -> dict:
        subjects = {}
        for key, value in refusal.subjects.items():
            if isinstance(value, str):
                subjects[key] = value[:200]
            else:
                subjects[key] = canonical_json(value)[:200]
        self.store.append_event(
            intent_id, E_EVIDENCE_REFUSED, actor=actor, now=self.now(),
            data={"reason": refusal.reason, "state": refusal.state,
                  "detail": refusal.detail[:200], "subjects": subjects})
        return self._stop(intent_id, refusal.state, refusal.reason, actor,
                          detail=refusal.detail)

    def _require_intent_unchanged(self, intent_id: str, intent: dict) -> None:
        current = self.store.get(intent_id)
        if current["revision"] != intent["revision"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the intent changed while fresh materials were validated "
                f"(revision {intent['revision']} -> {current['revision']})",
                subject="intent")

    # -- plain O2 transitions are not an accepted R0B path -------------------
    def mark_prepared(self, intent_id: str, *, package_id: str,
                      artifact_dependency_digest: str, actor: str) -> dict:
        intent = self.store.get(intent_id)
        validate_intent_record(intent, executable=True)
        raise R0BDowngradeRefused(
            "R0B intents must bind E through bind_execution_package; "
            "the plain mark_prepared transition is not an accepted R0B path",
            intent_id=intent_id)

    def mark_published(self, intent_id: str, *, note_comment_id: str,
                       receipt_digest: str | None, actor: str) -> dict:
        intent = self.store.get(intent_id)
        validate_intent_record(intent, executable=True)
        raise R0BDowngradeRefused(
            "R0B intents must confirm publication through "
            "confirm_publication_and_bind; the plain mark_published "
            "transition is not an accepted R0B path", intent_id=intent_id)

    # -- caller-supplied snapshots cannot bypass the preflight ---------------
    def plan_and_arm(self, intent_id: str, snapshot: dict, *,
                     actor: str) -> dict:
        self._load(intent_id)
        raise R0BDowngradeRefused(
            "external snapshot-taking is not an accepted R0B arming path; "
            "call arm() so the fresh material preflight runs immediately "
            "before issuance", intent_id=intent_id)

    def issue_trigger(self, intent_id: str, snapshot: dict, *,
                      actor: str) -> dict:
        self._load(intent_id)
        raise R0BDowngradeRefused(
            "external snapshot-taking is not an accepted R0B trigger path; "
            "call trigger() so the fresh material preflight runs immediately "
            "before issuance", intent_id=intent_id)

    # -- step 1: creation intent --------------------------------------------
    def record_creation_intent(self, *, creation_context: dict,
                               creation_spec: dict, authority: str,
                               actor: str, source_run: str | None = None,
                               intent_id: str | None = None) -> dict:
        spec = validate_creation_spec(creation_spec)
        artifact = spec["artifact_dependency"]
        if self.artifact_blob_reader is not None or self.artifact_root:
            rebuilt = build_artifact_dependency_digest(
                [dict(v, path=k) for k, v in artifact["entries"].items()],
                root=self.artifact_root,
                blob_reader=self.artifact_blob_reader)
            if rebuilt["digest"] != artifact["digest"]:
                raise R0BValidationRefused(
                    "artifact dependency digest does not reproduce from the "
                    "pinned blobs")
        context = r0b_creation_context(creation_context, spec,
                                       artifact_digest=artifact["digest"])
        if context.get("source_task_id") not in (None, source_run):
            raise R0BValidationRefused(
                "creation context source task and source_run disagree")
        _require_text(authority, "authority")
        intent_id = intent_id or o2.new_intent_id(
            source_task_id=context.get("source_task_id") or authority,
            logical_task_key=spec["logical_task_key"],
            target_agent_id=spec["target_agent_id"],
            package_id=context["package_id"])
        if not _HEX16_RE.match(intent_id.split("-", 1)[-1]):
            raise R0BValidationRefused("intent_id is not DI-<16 hex>")
        binding = {
            "contract_version": CONTRACT_VERSION,
            "adapter_digest": adapter_digest(),
            "phase": "CREATION_INTENT",
            "creation_context": context,
            "creation_spec": spec,
            "artifact_dependency": artifact,
        }
        fields = {
            "intent_id": intent_id,
            "schema_version": o2.O2_SCHEMA,
            "source_task_id": context.get("source_task_id") or authority,
            "logical_task_key": spec["logical_task_key"],
            "parent_issue_id": spec["parent_issue_id"],
            "target_role": spec["target_role"],
            "target_agent_id": spec["target_agent_id"],
            "package_id": context["package_id"],
            "artifact_dependency_digest": artifact["digest"],
            "creation_authority": o2._require_text(authority,
                                                   "creation_authority"),
            "issue_id": None,
            "provenance": {
                "adapter": ADAPTER_MODULE,
                "adapter_version": ADAPTER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "adapter_digest": adapter_digest(),
                "creation_task_ref": spec["creation_task_ref"],
                "creation_envelope_digest": context["envelope_digest"],
                "authority_refs": spec["authority_refs"],
                "source_run": source_run,
            },
            R0B_FIELD: binding,
        }
        try:
            record = self.store.record_intent(fields, now=self.now())
        except o2.DuplicateLogicalKeyError as exc:
            raise R0BValidationRefused(
                "an open intent already owns this logical task key; refusing "
                "a duplicate creation intent",
                logical_task_key=spec["logical_task_key"]) from exc
        self.store.append_event(
            intent_id, E_INTENT_RECORDED, actor=actor, now=self.now(),
            data={"contract_version": CONTRACT_VERSION,
                  "marker": spec["marker"],
                  "body_digest": spec["body_digest"],
                  "artifact_dependency_digest": artifact["digest"],
                  "source_run": source_run})
        return {"status": o2.S_INTENT_RECORDED, "intent_id": intent_id,
                "seq": record["seq"], "marker": spec["marker"],
                "side_effects": 0}

    # -- step 2: create exactly once ----------------------------------------
    def _create_payload(self, data: dict) -> dict:
        spec = data["creation_spec"]
        return {
            "title": spec["title"],
            "description": spec["body"],
            "parent_issue_id": spec["parent_issue_id"],
            "project_id": spec.get("project_id"),
            "priority": spec.get("priority"),
            "marker": spec["marker"],
        }

    def create_target_once(self, intent_id: str, *, actor: str) -> dict:
        intent = self._load(intent_id, require=("creation_context",
                                                "creation_spec"))
        data = intent["fields"][R0B_FIELD]
        if intent["state"] in (o2.S_BLOCKED, o2.S_CANCELLED,
                               o2.S_REFRESH_REQUIRED):
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["fields"].get("issue_id") is not None:
            return {"status": intent["state"], "intent_id": intent_id,
                    "issue_id": intent["fields"].get("issue_id"),
                    "replayed": True, "side_effects": 0}
        if intent["state"] not in (o2.S_INTENT_RECORDED, o2.S_CREATE_AMBIGUOUS):
            raise o2.IllegalTransitionError(
                "target creation requires INTENT_RECORDED or CREATE_AMBIGUOUS",
                intent_id=intent_id, state=intent["state"])
        if intent["fields"].get("issue_id") is not None:
            raise R0BValidationRefused("intent already bound to a target",
                                       intent_id=intent_id)
        prior = [e for e in intent["events"]
                 if e.get("name") == E_CREATE_ISSUING]
        if prior:
            return self._recover_create(intent_id, data=data, actor=actor,
                                        detail="create attempt already "
                                               "recorded; read-only recovery "
                                               "only")
        payload = self._create_payload(data)
        if intent_id not in payload["description"]:
            raise R0BValidationRefused(
                "creation body must embed the intent_id for lost-response "
                "discovery")
        self._claim(intent_id, actor)
        try:
            self.store.append_event(
                intent_id, E_CREATE_ISSUING, actor=actor, now=self.now(),
                data={"contract_version": CONTRACT_VERSION,
                      "operation_id": new_operation_id("create", {
                          "intent_id": intent_id,
                          "body_digest": data["creation_spec"]["body_digest"]}),
                      "title": payload["title"],
                      "body_digest": data["creation_spec"]["body_digest"],
                      "parent_issue_id": payload["parent_issue_id"],
                      "project_id": payload["project_id"],
                      "status": BACKLOG_STATUS,
                      "marker": payload["marker"]})
            try:
                created = self.boundary.create_backlog_issue(
                    title=payload["title"], description=payload["description"],
                    parent_issue_id=payload["parent_issue_id"],
                    project_id=payload["project_id"],
                    priority=payload["priority"])
            except o2.ReceiptAmbiguousError as exc:
                return self._recover_create(intent_id, data=data, actor=actor,
                                            detail=exc.message)
            except o2.IntentError as exc:
                return self._stop(intent_id, o2.S_CREATE_AMBIGUOUS,
                                  REASON_CREATE_AMBIGUOUS, actor,
                                  detail=f"create refused: {exc.message}")
            try:
                issue = self.boundary.issue_get(created["id"])
            except o2.IntentError as exc:
                return self._recover_create(
                    intent_id, data=data, actor=actor,
                    detail=f"create read-back failed: {exc.message}")
            return self._bind_target(intent_id, data=data, issue=issue,
                                     actor=actor, receipt=created,
                                     discovered=False)
        finally:
            self._release(intent_id, actor)

    def _verify_created_issue(self, data: dict, issue: dict) -> str:
        spec = data["creation_spec"]
        if issue.get("parent_issue_id") != spec["parent_issue_id"]:
            raise R0BValidationRefused("created target parent mismatch")
        if spec.get("project_id") is not None and \
                issue.get("project_id") != spec["project_id"]:
            raise R0BValidationRefused("created target project mismatch")
        status = issue.get("status_category") or issue.get("status")
        if status != BACKLOG_STATUS:
            raise R0BValidationRefused(
                "created target is not in backlog", status=status)
        if issue.get("assignee_id") not in (None, ""):
            raise R0BValidationRefused(
                "created target unexpectedly carries an assignee")
        if issue.get("title") != spec["title"]:
            raise R0BValidationRefused("created target title mismatch")
        description = issue.get("description") or ""
        if "transport_preparation" in spec:
            if description != spec["body"]:
                raise R0BValidationRefused(
                    "created target does not carry the exact persisted "
                    "transport body bytes")
            return status
        if spec["marker"] not in description:
            raise R0BValidationRefused(
                "created target does not carry the standalone intent marker")
        if digest_text_lf(description) != spec["body_digest"]:
            raise R0BValidationRefused(
                "created target body bytes do not match the approved spec")
        return status

    def _bind_target(self, intent_id: str, *, data: dict, issue: dict,
                     actor: str, receipt: dict | None,
                     discovered: bool) -> dict:
        spec = data["creation_spec"]
        try:
            self._verify_created_issue(data, issue)
            if discovered:
                runs = self.boundary.list_runs(issue["id"])
                if runs.get("runs"):
                    raise R0BValidationRefused(
                        "discovered target already carries runs")
        except R0BValidationRefused as exc:
            self.store.append_event(
                intent_id, E_EVIDENCE_REFUSED, actor=actor, now=self.now(),
                data={"reason": REASON_CREATE_AMBIGUOUS,
                      "detail": exc.message[:200]})
            return self._stop(intent_id, o2.S_CREATE_AMBIGUOUS,
                              REASON_CREATE_AMBIGUOUS, actor,
                              detail=exc.message)
        revision = issue["revision"]
        snapshot_digest = digest(issue_projection(issue))
        binding = dict(data)
        binding["phase"] = "TARGET_BOUND"
        binding["target_binding"] = {
            "issue_id": issue["id"],
            "identifier": issue.get("identifier"),
            "parent_issue_id": issue.get("parent_issue_id"),
            "project_id": issue.get("project_id"),
            "target_role": spec["target_role"],
            "target_agent_id": spec["target_agent_id"],
            "post_create_revision": revision,
            "post_create_snapshot_digest": snapshot_digest,
            "post_create_status": (issue.get("status_category")
                                   or issue.get("status")),
            "creation_marker": spec["marker"],
            "creation_receipt_digest": (digest(receipt) if receipt else None),
            "discovered_by_read_only_proof": bool(discovered),
            "bound_at": self.now(),
        }
        fields = {
            "issue_id": issue["id"],
            "expected_issue_revision": revision,
            "expected_status_category": (issue.get("status_category")
                                         or issue.get("status")),
            "expected_assignee_id": issue.get("assignee_id") or None,
            "creation_receipt_digest": (digest(receipt) if receipt else None),
            "discovered_by_read_only_proof": bool(discovered),
            R0B_FIELD: binding,
        }
        transition = self.store.transition(
            intent_id, o2.S_TARGET_BOUND,
            expected_revision=self.store.get(intent_id)["revision"],
            actor=actor, now=self.now(), fields=fields)
        self.store.append_event(
            intent_id, E_CREATE_BOUND, actor=actor, now=self.now(),
            data={"issue_id": issue["id"],
                  "identifier": issue.get("identifier"),
                  "revision": revision,
                  "marker_verified": True,
                  "body_digest": spec["body_digest"],
                  "discovered": bool(discovered)})
        return {"status": o2.S_TARGET_BOUND, "intent_id": intent_id,
                "issue_id": issue["id"],
                "identifier": issue.get("identifier"),
                "issue_revision": revision,
                "revision": transition["revision"], "side_effects": 0}

    def _recover_create(self, intent_id: str, *, data: dict, actor: str,
                        detail: str) -> dict:
        """Read-only parent-child discovery for a lost create response."""
        spec = data["creation_spec"]
        try:
            rows = self.boundary.list_children(spec["parent_issue_id"])
        except o2.IntentError as exc:
            return self._stop(intent_id, o2.S_CREATE_AMBIGUOUS,
                              REASON_CREATE_AMBIGUOUS, actor,
                              detail=f"{detail}; discovery unavailable: "
                                     f"{exc.message}")
        matches = []
        for row in rows:
            if row.get("title") != spec["title"]:
                continue
            haystack = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if spec["marker"] not in haystack:
                continue
            if row.get("parent_issue_id") not in (None,
                                                  spec["parent_issue_id"]):
                continue
            matches.append(row)
        self.store.append_event(
            intent_id, E_RECOVERY, actor=actor, now=self.now(),
            data={"window": "create", "candidates": len(matches),
                  "ids": sorted(str(r.get("id")) for r in matches[:4])})
        if len(matches) != 1:
            return self._stop(
                intent_id, o2.S_CREATE_AMBIGUOUS, REASON_CREATE_AMBIGUOUS,
                actor,
                detail=f"{detail}; read-only discovery found {len(matches)} "
                       "marker-matching children; never duplicate a create")
        candidate_id = str(matches[0].get("id") or "")
        if not candidate_id:
            return self._stop(intent_id, o2.S_CREATE_AMBIGUOUS,
                              REASON_CREATE_AMBIGUOUS, actor,
                              detail=f"{detail}; candidate carries no id")
        try:
            issue = self.boundary.issue_get(candidate_id)
        except o2.IntentError as exc:
            return self._stop(
                intent_id, o2.S_CREATE_AMBIGUOUS, REASON_CREATE_AMBIGUOUS,
                actor,
                detail=f"{detail}; discovered child is not readable: "
                       f"{exc.message}")
        return self._bind_target(intent_id, data=data, issue=issue, actor=actor,
                                 receipt=None, discovered=True)

    # -- forward create recovery (YZT-83 accepted decision) ------------------
    def recover_created_target(self, intent_id: str, *,
                               expected_target_id: str,
                               recovery_decision: dict, actor: str,
                               authority_evidence: dict | None = None,
                               execution_commit: str,
                               original_receipt: dict | None = None) -> dict:
        """Bind the sole already-created target after the LF-loss ambiguity.

        This is the only operation that may execute a known-predecessor
        record. It has no create/assign/comment/rerun capability: every
        runner command it issues is a read, and the only durable writes are
        the namespaced recovery evidence plus the existing
        CREATE_AMBIGUOUS -> TARGET_BOUND edge. Replay of a committed recovery
        is read-only.

        The full exact accepted execution commit is mandatory: it must be the
        disposition's accepted execution commit, resolve to an adapter blob
        whose LF digest equals these executing bytes, and stay recorded as the
        exact identity. A missing, unaccepted, unresolvable or mismatched
        identity refuses live eligibility.
        """
        expected_target_id = _require_uuid(expected_target_id,
                                           "expected_target_id")
        decision = validate_recovery_decision(recovery_decision)
        if decision["expected_target_id"] != expected_target_id:
            raise R0BValidationRefused(
                "the recovery disposition names a different target than the "
                "operation", disposition=decision["expected_target_id"],
                operation=expected_target_id)
        if decision["intent_id"] != intent_id:
            raise R0BValidationRefused(
                "the recovery disposition names a different intent",
                disposition=decision["intent_id"], operation=intent_id)
        if not isinstance(execution_commit, str) or \
                not re.match(r"^[0-9a-f]{40}$", execution_commit):
            raise R0BValidationRefused(
                "a full exact accepted execution commit is required for live "
                "eligibility; a short or placeholder revision is refused",
                execution_commit=str(execution_commit)[:40])
        execution_resolution = self._resolve_execution_identity(
            execution_commit, decision)
        intent = self.store.get(intent_id)
        data = validate_intent_record(intent, executable=False)
        if intent["state"] == o2.S_TARGET_BOUND and \
                isinstance(data.get("execution_binding"), dict):
            return self._replay_committed_recovery(
                intent, data, expected_target_id, decision, actor)
        if data.get("contract_version") != PREDECESSOR_CONTRACT_VERSION:
            raise R0BValidationRefused(
                "recover_created_target applies only to the exact known "
                "predecessor record before any binding; current records use "
                "the ordinary lifecycle", intent_id=intent_id,
                state=intent["state"],
                contract_version=data.get("contract_version"))
        return self._execute_create_recovery(
            intent_id, expected_target_id=expected_target_id,
            decision=decision, actor=actor,
            authority_evidence=authority_evidence,
            execution_commit=execution_commit,
            execution_resolution=execution_resolution,
            original_receipt=original_receipt)

    def _replay_committed_recovery(self, intent, data, expected_target_id,
                                   decision, actor) -> dict:
        _validate_execution_binding(intent, data)
        target = data.get("target_binding") or {}
        if target.get("issue_id") != expected_target_id:
            raise R0BValidationRefused(
                "the committed recovery binds a different target; competing "
                "recovery is refused", committed=target.get("issue_id"),
                expected=expected_target_id)
        proof = data.get("recovery_proof") or {}
        committed = proof.get("decision") or {}
        if committed.get("decision_digest") != decision["decision_digest"]:
            raise R0BValidationRefused(
                "a different recovery decision is already committed; "
                "competing proof is refused",
                committed=committed.get("decision_digest"),
                supplied=decision["decision_digest"])
        return {"status": o2.S_TARGET_BOUND, "intent_id": intent["intent_id"],
                "issue_id": target.get("issue_id"),
                "identifier": target.get("identifier"),
                "issue_revision": target.get("post_create_revision"),
                "revision": intent["revision"], "replayed": True,
                "side_effects": 0, "next_action": RECOVERY_OWNERSHIP_NEXT,
                "proof_digest": proof.get("proof_digest")}

    def _require_predecessor_recovery_state(self, intent, decision) -> None:
        data = intent["fields"][R0B_FIELD]
        if data.get("contract_version") != PREDECESSOR_CONTRACT_VERSION:
            raise R0BValidationRefused(
                "recovery requires a record from the exact known predecessor "
                "contract", contract_version=data.get("contract_version"))
        if data.get("adapter_digest") != PREDECESSOR_ADAPTER_DIGEST:
            raise R0BDowngradeRefused(
                "recovery requires the exact known predecessor adapter pin",
                adapter_digest=data.get("adapter_digest"))
        if intent["state"] != o2.S_CREATE_AMBIGUOUS:
            raise R0BValidationRefused(
                "recovery requires the exact CREATE_AMBIGUOUS prior phase",
                state=intent["state"], intent_id=intent["intent_id"])
        fields = intent["fields"]
        if fields.get("issue_id") is not None or \
                data.get("target_binding") is not None or \
                data.get("execution_binding") is not None or \
                data.get("publication_binding") is not None:
            raise R0BValidationRefused(
                "the record already carries a binding; recovery never "
                "rebinds or overwrites", intent_id=intent["intent_id"])
        if intent["revision"] != decision["expected_intent_revision"]:
            raise R0BValidationRefused(
                "the disposition's expected intent revision differs from the "
                "recorded revision",
                expected=decision["expected_intent_revision"],
                found=intent["revision"])

    def _recovery_refusal(self, intent_id: str, refusal: PreflightRefusal,
                          actor: str) -> dict:
        """Typed, zero-native-effect refusal: the state stays CREATE_AMBIGUOUS.

        The bounded diagnostic event is appended for audit; it is never
        reusable authorization.
        """
        subjects = {}
        for key, value in refusal.subjects.items():
            if isinstance(value, str):
                subjects[key] = value[:200]
            else:
                subjects[key] = canonical_json(value)[:200]
        self.store.append_event(
            intent_id, E_EVIDENCE_REFUSED, actor=actor, now=self.now(),
            data={"reason": refusal.reason, "state": refusal.state,
                  "detail": refusal.detail[:200], "subjects": subjects,
                  "window": "create_recovery"})
        return {"status": o2.S_CREATE_AMBIGUOUS, "intent_id": intent_id,
                "outcome": "RECOVERY_REFUSED", "reason": refusal.reason,
                "detail": refusal.detail, "external_writes": 0,
                "side_effects": 0}

    def _discover_recovery_candidates(self, spec, intent_id,
                                      expected_target_id) -> dict:
        """Complete parent-child discovery with exact standalone identity.

        Marker substrings are never identity proof: a candidate matches only
        when its full body carries exactly one standalone marker line and
        exactly one standalone Intent line for this intent. The `unstaged`
        child the accepted O2 listing drops is included, and the declared
        listing total must match the collected rows.
        """
        try:
            listing = self.reader.issue_children(spec["parent_issue_id"])
        except o2.IntentError as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"parent-child discovery is incomplete: {exc}",
                subject="discovery")
        marker_line = "Intent marker: " + spec["marker"]
        intent_line = "Intent: " + intent_id
        candidates = []
        matches = []
        for row in listing["rows"]:
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                    "a discovered child carries no id", subject="discovery")
            if row.get("parent_issue_id") not in (None,
                                                  spec["parent_issue_id"]):
                continue
            haystack = json.dumps(row, ensure_ascii=False, sort_keys=True)
            likely = (row_id == expected_target_id
                      or row.get("title") == spec["title"]
                      or spec["marker"] in haystack
                      or intent_id in haystack)
            if not likely:
                continue
            body = row.get("description")
            full = row
            source = "children-listing-row"
            if not isinstance(body, str):
                try:
                    full = self.reader.issue_get(row_id)
                except o2.IntentError as exc:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                        f"candidate {row_id} body is not readable or "
                        f"complete: {exc}", subject="discovery")
                body = full.get("description") or ""
                source = "issue-get"
            lines = body.split("\n")
            candidate = {
                "id": row_id,
                "title": row.get("title"),
                "parent_issue_id": row.get("parent_issue_id"),
                "body": body,
                "body_digest": digest_text_lf(body),
                "body_source": source,
                "full_response": full,
            }
            candidates.append(candidate)
            if lines.count(marker_line) == 1 and lines.count(intent_line) == 1:
                matches.append(candidate)
        if len(matches) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_IDENTITY,
                "expected exactly one identity-proven marker/Intent candidate, "
                f"found {len(matches)}; marker substrings alone are never "
                "accepted", subject="discovery",
                candidates=[m["id"] for m in matches[:4]])
        if matches[0]["id"] != expected_target_id:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_IDENTITY,
                "the sole identity-proven candidate is not the expected "
                "target; never bind a different real target",
                subject="discovery", candidate=matches[0]["id"],
                expected=expected_target_id)
        return {"total": listing["total"],
                "declared_total": listing["declared_total"],
                "rows": listing["rows"],
                "candidates": candidates,
                "matches": matches}

    # -- shared-history classification (YZT-84 evidence correction) ----------
    def _read_ledger_lines(self) -> tuple:
        """Raw shared-ledger lines plus parsed records; fail closed on tears.

        Parsing the exact bytes (never a summary) is what lets the proof retain
        each record and its raw-prefix digest. A torn or malformed line is
        unattributed history and stops.
        """
        path = Path(self.store.path)
        if not path.exists():
            return [], []
        data = path.read_bytes()
        lines = data.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        records = []
        for index, line in enumerate(lines):
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    f"shared ledger line {index + 1} is torn or malformed; "
                    "unattributed history is unresolved evidence and fails "
                    f"closed: {exc}", subject="ledger")
            if not isinstance(record, dict):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    f"shared ledger line {index + 1} is not a JSON object",
                    subject="ledger")
            records.append(record)
        return lines, records

    @staticmethod
    def _mentions_intent(record: dict, intent_id: str, marker) -> bool:
        if record.get("intent_id") == intent_id:
            return True
        return bool(isinstance(marker, str) and marker
                    and marker in canonical_json(record))

    @staticmethod
    def _classify_record(record: dict, previous, *, intent_id: str,
                         spec: dict) -> dict:
        """Classify one shared record; refusal is always the safe default.

        `previous` is the classification entry of the immediately preceding
        record inside the relevant interval (or None). Result records inherit
        only a proven unique adjacent class/transaction-compatible command
        correlation; everything else is unresolved evidence.
        """
        entry = {"seq": record.get("seq"), "digest": digest(record),
                 "record": record}
        if record.get("record_type") == o2.INTENT_RECORD_TYPE:
            if record.get("intent_id") != intent_id:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    "a foreign dispatch-intent record lies in the relevant "
                    "interval; its complete scope and effects cannot be proven "
                    "disjoint", subject="shared_history",
                    record_seq=record.get("seq"))
            entry.update(classification=CLS_INTENT_HISTORY,
                         reason="expected intent record (recorded/transition/"
                                "event/lease)")
            return entry
        kind = record.get("kind")
        if kind == "command":
            argv = record.get("argv")
            recorded = record.get("command_class")
            if not isinstance(argv, list) or not argv or \
                    not all(isinstance(a, str) for a in argv):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    "a shared command record carries a malformed argv; it is "
                    "never treated as a read", subject="shared_history",
                    record_seq=record.get("seq"))
            if not isinstance(recorded, str):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    "a shared command record carries no recorded command "
                    "class", subject="shared_history",
                    record_seq=record.get("seq"))
            derived = o2.classify_o2_command(argv, "multica")
            if recorded != derived:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    "recorded command class and structured argv disagree; a "
                    "mislabelled command never becomes a read by trusting one "
                    "field", subject="shared_history",
                    record_seq=record.get("seq"), recorded=recorded,
                    derived=derived)
            if derived == o2.C_ISSUE_CREATE:
                problems = _original_create_argv_problems(argv, spec)
                if problems:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                        "the shared create command does not reproduce the "
                        "preserved creation spec: " + "; ".join(problems),
                        subject="original_create",
                        record_seq=record.get("seq"))
                entry.update(
                    classification=CLS_ORIGINAL_CREATE_COMMAND,
                    reason="structured argv fully matches the retained "
                           "creation spec (title/parent/project/priority/"
                           "literal backlog/unassigned, description-file "
                           "argument encoding the preserved body digest)")
                return entry
            if derived in o2.WRITE_COMMAND_CLASSES:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_EFFECT,
                    "a relevant prior native write attempt is persisted in "
                    "the shared ledger; a persisted attempt dominates any "
                    "current emptiness or exit code", subject="shared_history",
                    record_seq=record.get("seq"), command_class=derived,
                    argv=[str(a) for a in argv[:8]])
            if derived == o2.C_READ:
                entry.update(
                    classification=CLS_READ_COMMAND,
                    reason="narrow known-read verb with matching recorded "
                           "class")
                return entry
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                "an unrecognized shared command verb is unresolved evidence; "
                "an unknown effect stops", subject="shared_history",
                record_seq=record.get("seq"),
                argv=[str(a) for a in argv[:8]])
        if kind == "command_result":
            if previous is None or previous["classification"] not in (
                    CLS_ORIGINAL_CREATE_COMMAND, CLS_READ_COMMAND):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    "an orphan, duplicate or interleaved command result is "
                    "unresolved evidence; results are never paired by guess",
                    subject="shared_history", record_seq=record.get("seq"))
            command = previous["record"]
            if record.get("command_class") != command.get("command_class") or \
                    record.get("transaction_id") != \
                    command.get("transaction_id"):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                    "a command result does not correlate with its immediately "
                    "preceding command (class/transaction mismatch)",
                    subject="shared_history", record_seq=record.get("seq"))
            exit_code = record.get("exit_code")
            if previous["classification"] == CLS_ORIGINAL_CREATE_COMMAND:
                if exit_code != 0:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                        "the sole original create result is not the uniquely "
                        "paired successful result (nonzero or missing exit "
                        "code); native failure never proves absence of an "
                        "effect", subject="original_create",
                        record_seq=record.get("seq"), exit_code=exit_code)
                entry.update(
                    classification=CLS_ORIGINAL_CREATE_RESULT,
                    reason="uniquely paired adjacent class-compatible "
                           "successful result")
                return entry
            entry.update(
                classification=CLS_READ_RESULT,
                reason="uniquely paired adjacent read result",
                supplies_observation=(exit_code == 0))
            return entry
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
            "an unrecognized shared ledger record kind is unresolved "
            "evidence; it stops", subject="shared_history",
            record_seq=record.get("seq"))

    def _classify_shared_history(self, intent, spec, decision=None) -> dict:
        """Conservative full-interval classification with retained evidence.

        Audits the complete ledger interval from this intent's original
        recorded record through the fresh ledger tip, including shared records
        without `intent_id`. Every record gets a retained classification and
        reason; the unique original create command/result pair is correlated
        from full argv semantics and adjacency, and the audited prefix pinned
        by the disposition (when supplied) is revalidated exactly.
        """
        intent_id = intent["intent_id"]
        lines, records = self._read_ledger_lines()
        if not records:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "the shared ledger is empty; the original intent record is "
                "missing", subject="ledger")
        start = None
        for index, record in enumerate(records):
            if record.get("record_type") == o2.INTENT_RECORD_TYPE and \
                    record.get("intent_id") == intent_id:
                if start is None:
                    start = index
                continue
            if start is None and self._mentions_intent(
                    record, intent_id, spec.get("marker")):
                start = index
        if start is None:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "no original intent record for this intent exists in the "
                "shared ledger", subject="ledger")
        if start > 0 and records[start - 1].get("kind") == "command":
            # A command begun before the interval whose result falls inside it
            # is correlated, never discarded.
            start -= 1
        entries = []
        for index in range(start, len(records)):
            previous = entries[-1] if entries else None
            entry = self._classify_record(records[index], previous,
                                          intent_id=intent_id, spec=spec)
            entry["index"] = index
            entries.append(entry)
        create_commands = [
            entry for entry in entries
            if entry["classification"] == CLS_ORIGINAL_CREATE_COMMAND]
        create_results = [
            entry for entry in entries
            if entry["classification"] == CLS_ORIGINAL_CREATE_RESULT]
        if len(create_commands) > 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                "more than one shared create command exists for this intent; "
                "a second create is never accepted by recovery",
                subject="original_create",
                command_seqs=[e["seq"] for e in create_commands[:4]])
        if not create_commands:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                "the durable create-issuing event carries no matching create "
                "command in the shared ledger; the original create command "
                "evidence is missing", subject="original_create")
        if len(create_results) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                "the sole original create command has no unique adjacent "
                "class-compatible successful result; a missing, failed, "
                "orphan or multiply matchable result is never paired by "
                "guess", subject="original_create")
        command_entry = create_commands[0]
        result_entry = create_results[0]
        command = command_entry["record"]
        result = result_entry["record"]
        audited_length = len(records)
        audited_digest = _raw_prefix_digest(lines)
        if decision is not None:
            pinned = decision.get("ledger_prefix") or {}
            audited_length = pinned.get("length")
            audited_digest = pinned.get("digest")
            if not isinstance(audited_length, int) or audited_length < 1 or \
                    not isinstance(audited_digest, str):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_PREFIX,
                    "the disposition carries no valid audited ledger prefix",
                    subject="ledger_prefix")
            if len(records) < audited_length:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_PREFIX,
                    "the shared ledger is shorter than the audited "
                    "disposition prefix; audited history was removed or "
                    "replaced", subject="ledger_prefix",
                    pinned=audited_length, present=len(records))
            recomputed = _raw_prefix_digest(lines[:audited_length])
            if recomputed != audited_digest:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_PREFIX,
                    "the audited ledger prefix no longer reproduces; the "
                    "shared history changed after the disposition",
                    subject="ledger_prefix", pinned=audited_digest,
                    recomputed=recomputed)
            if command_entry["index"] >= audited_length or \
                    result_entry["index"] >= audited_length:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                    "the original create command/result pair lies outside the "
                    "audited disposition prefix", subject="original_create")
        raw_prefix = _raw_prefix_digest(lines[:audited_length])
        shared = {
            "interval": {
                "start_index": start,
                "start_seq": records[start].get("seq"),
                "tip_index": len(records) - 1,
                "tip_seq": records[-1].get("seq"),
                "ledger_record_count": len(records),
                "relevant_record_count": len(entries),
            },
            "audited_prefix": {
                "length": audited_length,
                "digest": audited_digest,
                "raw_prefix_digest": raw_prefix,
            },
            "records": [
                {key: entry[key] for key in (
                    "seq", "digest", "classification", "reason", "record")}
                for entry in entries],
            "original_create": {
                "command_seq": command.get("seq"),
                "command_digest": command_entry["digest"],
                "command": command,
                "result_seq": result.get("seq"),
                "result_digest": result_entry["digest"],
                "result": result,
                "pairing": LEDGER_PAIRING_ADJACENT,
                "transaction_id": command.get("transaction_id"),
            },
        }
        shared["classification_digest"] = shared_history_digest(shared)
        return shared

    def _resolve_execution_identity(self, execution_commit, decision) -> dict:
        """Resolve the accepted commit's adapter blob against executing bytes.

        A syntactically valid revision is not provenance: the commit must be
        the disposition's accepted execution commit, the blob must resolve in
        this checkout (or through the explicitly injected fixture resolver),
        and the resolved LF digest must equal these executing adapter bytes.
        """
        accepted = decision.get("accepted_execution") or {}
        if execution_commit != accepted.get("commit"):
            raise R0BValidationRefused(
                "the operation execution commit is not the disposition's "
                "accepted execution commit", operation=execution_commit,
                accepted=accepted.get("commit"))
        running = adapter_digest()
        if accepted.get("adapter_digest") != running:
            raise R0BValidationRefused(
                "the disposition's accepted adapter digest does not match "
                "these executing adapter bytes; the artifact is not accepted "
                "for this checkout", accepted=accepted.get("adapter_digest"),
                executing=running)
        try:
            blob = self.execution_blob_resolver(execution_commit,
                                                ADAPTER_MODULE)
        except Exception as exc:  # noqa: BLE001 - unresolvable is a refusal
            raise R0BValidationRefused(
                "the accepted execution commit adapter blob is not resolvable "
                "in this checkout; live eligibility refuses",
                commit=execution_commit,
                error=f"{type(exc).__name__}: {exc}")
        if not isinstance(blob, (bytes, bytearray)):
            raise R0BValidationRefused(
                "the execution blob resolver returned a non-bytes object",
                commit=execution_commit)
        resolved = "sha256:" + hashlib.sha256(
            bytes(blob).replace(b"\r\n", b"\n")).hexdigest()
        if resolved != running:
            raise R0BValidationRefused(
                "the resolved execution blob does not match these executing "
                "adapter bytes; a same-content substitute is not accepted",
                commit=execution_commit, resolved=resolved, executing=running)
        return {"resolver": self.execution_resolver_kind,
                "commit": execution_commit, "path": ADAPTER_MODULE,
                "adapter_digest": resolved, "ok": True}

    def _resolve_receipt_evidence(self, decision, spec, expected_target_id,
                                  original_receipt) -> dict:
        """The bounded receipt decision: actual receipt or explicit absence.

        Only a truly missing raw receipt body qualifies for the exact bounded
        receipt-limit disposition. A supplied receipt must be consistent and
        name the expected target; a receipt that names another target refuses
        even when the readback looks valid.
        """
        status = decision["original_receipt_body_status"]
        if original_receipt is not None:
            if not isinstance(original_receipt, dict):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_RECEIPT,
                    "the supplied original receipt is not an object",
                    subject="receipt")
            if status != RECEIPT_STATUS_PERSISTED:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_RECEIPT,
                    "a receipt body was supplied but the disposition declares "
                    "the raw receipt body never persisted; the bounded "
                    "receipt-limit waiver does not apply", subject="receipt")
            problems = []
            if original_receipt.get("id") != expected_target_id:
                problems.append("receipt does not name the expected target")
            if "title" in original_receipt and \
                    original_receipt.get("title") != spec["title"]:
                problems.append("receipt title")
            if "parent_issue_id" in original_receipt and \
                    original_receipt.get("parent_issue_id") != \
                    spec["parent_issue_id"]:
                problems.append("receipt parent")
            if problems:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_RECOVERY_RECEIPT,
                    "the supplied original create receipt conflicts: "
                    + "; ".join(problems), subject="receipt")
            return {"status": RECEIPT_STATUS_PERSISTED,
                    "body": original_receipt,
                    "body_digest": digest(original_receipt),
                    "note": "original create receipt body provided and "
                            "consistent with the exact target"}
        if status != RECEIPT_STATUS_NOT_PERSISTED:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_RECEIPT,
                "the disposition requires a persisted original receipt body "
                "but none was supplied; missing data stays missing",
                subject="receipt")
        if decision.get("receipt_limit_scope") != RECEIPT_LIMIT_SCOPE:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_RECEIPT,
                "the unpersisted-receipt path requires the exact bounded "
                "receipt-limit scope, not a general waiver of original "
                "evidence", subject="receipt")
        return {"status": RECEIPT_STATUS_NOT_PERSISTED, "body": None,
                "body_digest": None,
                "note": "no raw create receipt body was persisted; read-only "
                        "target identification is accepted only under the "
                        "exact bounded receipt-limit disposition"}

    def audit_recovery_ledger(self, intent_id: str) -> dict:
        """Read-only public audit for the Lead's disposition pins.

        Runs the same conservative classification the operation will run and
        returns the exact prefix/record pins a disposition must bind, plus the
        per-record classifications. It never writes and never issues a CLI
        command.
        """
        intent = self.store.get(intent_id)
        data = validate_intent_record(intent, executable=False)
        spec = validate_creation_spec(data.get("creation_spec") or {})
        shared = self._classify_shared_history(intent, spec, None)
        original = shared["original_create"]
        return {
            "ok": True,
            "intent_id": intent_id,
            "state": intent["state"],
            "revision": intent["revision"],
            "shared_history": shared,
            "suggested_disposition": {
                "ledger_prefix": {
                    "length": shared["audited_prefix"]["length"],
                    "digest": shared["audited_prefix"]["digest"],
                },
                "original_create_pair": {
                    "command_seq": original["command_seq"],
                    "command_digest": original["command_digest"],
                    "result_seq": original["result_seq"],
                    "result_digest": original["result_digest"],
                },
            },
        }

    def _recovery_prerequisites(self, intent, data, *, expected_target_id,
                                decision, authority_evidence,
                                original_receipt=None) -> dict:
        """All recovery prerequisites, read-only, under the intent lease."""
        fields = intent["fields"]
        spec = validate_creation_spec(data.get("creation_spec") or {})
        if spec.get("transport_preparation") is not None:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "a predecessor record cannot carry prospective transport "
                "preparation", subject="spec")
        creation = r0b_creation_context(
            data.get("creation_context") or {}, spec,
            artifact_digest=(data.get("artifact_dependency") or {}).get(
                "digest"))
        artifact = data.get("artifact_dependency") or {}
        if not isinstance(artifact.get("digest"), str) or \
                not isinstance(artifact.get("entries"), dict):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the original artifact dependency is not available",
                subject="artifact")
        provenance = fields.get("provenance") or {}
        problems = []
        if fields.get("logical_task_key") != spec["logical_task_key"]:
            problems.append("logical task key")
        if fields.get("target_agent_id") != spec["target_agent_id"]:
            problems.append("target agent")
        if fields.get("target_role") != spec["target_role"]:
            problems.append("target role")
        if fields.get("parent_issue_id") != spec["parent_issue_id"]:
            problems.append("parent")
        if fields.get("package_id") != creation.get("package_id"):
            problems.append("creation package")
        if fields.get("artifact_dependency_digest") != artifact.get("digest"):
            problems.append("artifact dependency digest")
        if provenance.get("adapter_digest") != PREDECESSOR_ADAPTER_DIGEST:
            problems.append("provenance adapter pin")
        if provenance.get("contract_version") != PREDECESSOR_CONTRACT_VERSION:
            problems.append("provenance contract version")
        if fields.get("source_task_id") != provenance.get("source_run"):
            problems.append("source run")
        if creation.get("source_task_id") not in (None,
                                                  fields.get("source_task_id")):
            problems.append("creation context source run")
        if problems:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "the original record does not revalidate: "
                + ", ".join(problems), subject="record", fields=problems)
        source = spec["body"]
        marker_line = "Intent marker: " + spec["marker"]
        intent_line = "Intent: " + intent["intent_id"]
        lines = source.split("\n")
        if lines.count(marker_line) != 1 or lines.count(intent_line) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_IDENTITY,
                "the original source does not carry exactly one standalone "
                "marker line and Intent line", subject="spec")
        names = [e.get("name") for e in intent["events"]]
        unexpected = sorted({n for n in names
                             if n not in RECOVERY_ALLOWED_PRIOR_EVENTS})
        if unexpected:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_EFFECT,
                "the record carries unexplained non-create effects; this first "
                "recovery rejects them", effects=unexpected)
        if names.count(E_INTENT_RECORDED) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "the record does not carry exactly one intent-recorded event",
                subject="events")
        creates = [e for e in intent["events"]
                   if e.get("name") == E_CREATE_ISSUING]
        if len(creates) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "recovery requires exactly one durable create attempt, found "
                f"{len(creates)}", subject="create")
        if len(intent["transitions"]) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "recovery requires exactly the one CREATE_AMBIGUOUS "
                "transition", count=len(intent["transitions"]))
        transition = intent["transitions"][0]
        if transition.get("from") != o2.S_INTENT_RECORDED or \
                transition.get("to") != o2.S_CREATE_AMBIGUOUS or \
                transition.get("revision") != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "the recorded transition chain is not the exact "
                "INTENT_RECORDED -> CREATE_AMBIGUOUS edge",
                subject="transition")
        create_event = creates[0]
        create_data = create_event.get("data") or {}
        shared = self._classify_shared_history(intent, spec, decision)
        receipt = self._resolve_receipt_evidence(
            decision, spec, expected_target_id, original_receipt)
        original = shared["original_create"]
        command = original["command"]
        result = original["result"]
        pair = decision["original_create_pair"]
        if original["command_seq"] != pair["command_seq"] or \
                original["command_digest"] != pair["command_digest"] or \
                original["result_seq"] != pair["result_seq"] or \
                original["result_digest"] != pair["result_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                "the disposition's pinned original create pair does not match "
                "the revalidated shared-ledger pair; the classification is "
                "recomputed, never trusted from the disposition",
                subject="original_create")
        attempt = {
            "event_seq": create_event.get("seq"),
            "at": create_event.get("at"),
            "actor": create_event.get("actor"),
            "operation_id": create_data.get("operation_id"),
            "body_digest": create_data.get("body_digest"),
            "title": create_data.get("title"),
            "parent_issue_id": create_data.get("parent_issue_id"),
            "project_id": create_data.get("project_id"),
            "status": create_data.get("status"),
            "marker": create_data.get("marker"),
            "command": command,
            "command_digest": original["command_digest"],
            "command_seq": original["command_seq"],
            "result": result,
            "result_digest": original["result_digest"],
            "result_seq": original["result_seq"],
            "pairing": original["pairing"],
            "transaction_id": original["transaction_id"],
            "receipt": receipt["body"],
            "receipt_status": receipt["status"],
            "receipt_note": receipt["note"],
        }
        if not attempt["operation_id"] or \
                attempt["body_digest"] != spec["body_digest"] or \
                attempt["marker"] != spec["marker"] or \
                attempt["title"] != spec["title"] or \
                attempt["parent_issue_id"] != spec["parent_issue_id"] or \
                attempt["project_id"] != spec.get("project_id") or \
                attempt["status"] != BACKLOG_STATUS:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_UNSUPPORTED,
                "the durable create attempt does not revalidate against the "
                "preserved creation spec", subject="create")
        intent_records = [
            entry["record"] for entry in shared["records"]
            if entry["classification"] == CLS_INTENT_HISTORY]
        chain = {
            "ledger_records": shared["interval"]["ledger_record_count"],
            "ledger_tip_seq": shared["interval"]["tip_seq"],
            "ledger_tip_digest": shared["records"][-1]["digest"],
            "intent_records": len(intent_records),
            "intent_tip_seq": (intent_records[-1].get("seq")
                               if intent_records else 0),
            "intent_tip_digest": (digest(intent_records[-1])
                                  if intent_records else None),
            "intent_revision_before": intent["revision"],
            "transition_reason": transition.get("reason"),
            "audited_prefix_length": shared["audited_prefix"]["length"],
            "audited_prefix_digest": shared["audited_prefix"]["digest"],
            "classification_digest": shared["classification_digest"],
        }
        rebuilt = compare_artifact_dependency(
            artifact["digest"], artifact["entries"],
            rebuild_artifact_dependency(artifact["entries"],
                                        root=self.artifact_root,
                                        blob_reader=self.artifact_blob_reader))
        authority = validate_authority_evidence(
            self._resolve_authority(authority_evidence), data=data)
        discovery = self._discover_recovery_candidates(
            spec, intent["intent_id"], expected_target_id)
        try:
            evidence = collect_evidence(self.reader, expected_target_id)
        except o2.IntentError as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"live target evidence is incomplete or unreadable: {exc}",
                subject="evidence")
        issue = evidence["issue"]
        try:
            recheck = self.reader.issue_get(expected_target_id)
        except o2.IntentError as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"post-collection target re-read failed: {exc}",
                subject="evidence")
        if recheck.get("revision") != issue.get("revision") or \
                issue_projection(recheck) != issue_projection(issue):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MOVING_EVIDENCE,
                "the target moved while its evidence was collected",
                subject="evidence")
        status = issue.get("status_category") or issue.get("status")
        target_problems = []
        if issue.get("id") != expected_target_id:
            target_problems.append("target id")
        if issue.get("parent_issue_id") != spec["parent_issue_id"]:
            target_problems.append("parent")
        if issue.get("title") != spec["title"]:
            target_problems.append("title")
        if issue.get("project_id") != spec.get("project_id"):
            target_problems.append("project (including null)")
        if spec.get("priority") is not None and \
                issue.get("priority") != spec.get("priority"):
            target_problems.append("priority")
        if issue.get("creator_id") != decision["expected_creator_id"] or \
                issue.get("creator_type") != "agent":
            target_problems.append("expected creator")
        if status != BACKLOG_STATUS:
            target_problems.append(f"backlog status ({status})")
        if issue.get("assignee_id") not in (None, ""):
            target_problems.append("unassigned status")
        if not isinstance(issue.get("revision"), int):
            target_problems.append("revision")
        elif issue.get("revision") != decision["expected_target_revision"]:
            target_problems.append(
                f"stable revision {decision['expected_target_revision']} "
                f"(found {issue['revision']})")
        if target_problems:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_RECOVERY_TARGET,
                "the live target does not match the exact expected fields: "
                + ", ".join(target_problems), subject="issue",
                fields=target_problems)
        if evidence["comments"]:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_RECOVERY_EFFECT,
                "the target carries comment(s); unexplained mutation",
                count=len(evidence["comments"]))
        if evidence["runs"]:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_RECOVERY_EFFECT,
                "the target already carries run(s)",
                run_ids=[r.get("id") for r in evidence["runs"][:4]])
        activities = evidence["activities"]
        if len(activities) != 1 or \
                activities[0].get("action") != "created" or \
                activities[0].get("actor_id") != decision["expected_creator_id"]:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_RECOVERY_EFFECT,
                "the creation timeline is not exactly one creator 'created' "
                "activity", activities=len(activities))
        observed = issue.get("description") or ""
        relation = single_terminal_lf_relation(source, observed)
        if not relation["accepted"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, relation["reason"], relation["detail"],
                subject="body")
        if relation["relation"] == "single-terminal-lf-removed":
            transport_body = source[:-1]
            transformation = "remove-single-terminal-lf"
        else:
            transport_body = source
            transformation = "identity"
        return {
            "spec": spec,
            "creation": creation,
            "artifact": rebuilt,
            "authority": authority,
            "chain": chain,
            "attempt": attempt,
            "shared_history": shared,
            "receipt": receipt,
            "discovery": discovery,
            "evidence": evidence,
            "evidence_digest": evidence_digest(evidence),
            "issue": issue,
            "status": status,
            "recheck": recheck,
            "source": source,
            "observed": observed,
            "relation": relation,
            "transport_body": transport_body,
            "transformation": transformation,
        }

    def _build_recovery_proof(self, *, intent, bundle, decision, actor,
                              execution_commit, execution_resolution) -> dict:
        fields = intent["fields"]
        data = fields[R0B_FIELD]
        spec = bundle["spec"]
        issue = bundle["issue"]
        entries = (data.get("artifact_dependency") or {}).get("entries") or {}
        observed = bundle["observed"]
        discovery = bundle["discovery"]
        receipt = bundle["receipt"]
        discovery_observations = {
            "total": discovery["total"],
            "declared_total": discovery["declared_total"],
            "rows": discovery["rows"],
            "candidates": discovery["candidates"],
            "matches": discovery["matches"],
            "completeness": {
                "declared_total_matches_collected": (
                    discovery["declared_total"] == discovery["total"]),
                "listing_shape": "stages+unstaged",
                "candidate_bodies_retained": True,
            },
        }
        observations = {
            "target_issue": issue,
            "target_recheck": bundle["recheck"],
            "comments": bundle["evidence"]["comments"],
            "activities": bundle["evidence"]["activities"],
            "runs": bundle["evidence"]["runs"],
            "discovery": discovery_observations,
            "raw_responses": bundle["raw_responses"],
            "declared_completeness": {
                "comments_count": len(bundle["evidence"]["comments"]),
                "activities_count": len(bundle["evidence"]["activities"]),
                "runs_count": len(bundle["evidence"]["runs"]),
                "children_total": discovery["total"],
                "children_declared_total": discovery["declared_total"],
                "raw_responses_count": len(bundle["raw_responses"]),
                "initial_and_recheck_match": (
                    bundle["recheck"].get("revision") == issue.get("revision")),
            },
            "digests": {
                "target_issue": digest(issue),
                "target_recheck": digest(bundle["recheck"]),
                "comments": digest(bundle["evidence"]["comments"]),
                "activities": digest(bundle["evidence"]["activities"]),
                "runs": digest(bundle["evidence"]["runs"]),
                "discovery": digest(discovery_observations),
                "raw_responses": digest(bundle["raw_responses"]),
            },
        }
        proof = {
            "schema": RECOVERY_PROOF_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "profile": TRANSPORT_PROFILE,
            "intent_id": intent["intent_id"],
            "logical_task_key": fields.get("logical_task_key"),
            "source_run": fields.get("source_task_id"),
            "state_before": o2.S_CREATE_AMBIGUOUS,
            "intent_revision_before": bundle["chain"][
                "intent_revision_before"],
            "target": {
                "issue_id": issue["id"],
                "identifier": issue.get("identifier"),
                "revision": issue["revision"],
                "parent_issue_id": issue.get("parent_issue_id"),
                "project_id": issue.get("project_id"),
                "priority": issue.get("priority"),
                "creator_id": issue.get("creator_id"),
                "creator_type": issue.get("creator_type"),
                "status_category": bundle["status"],
                "assignee_id": issue.get("assignee_id"),
                "snapshot_digest": digest(issue_projection(issue)),
            },
            "original": {
                "contract_version": PREDECESSOR_CONTRACT_VERSION,
                "adapter_module": ADAPTER_MODULE,
                "adapter_digest": PREDECESSOR_ADAPTER_DIGEST,
                "adapter_commit": PREDECESSOR_ADAPTER_COMMIT,
                "creation_spec_digest": digest(spec),
                "body_digest": spec["body_digest"],
                "body_source_raw_digest": _sha256_utf8(spec["body"]),
                "body_source_lf_digest": digest_text_lf(spec["body"]),
                "creation_context_digest": digest(
                    data.get("creation_context") or {}),
                "creation_authority": fields.get("creation_authority"),
                "authority_refs": spec["authority_refs"],
                "artifact_dependency_digest":
                    (data.get("artifact_dependency") or {}).get("digest"),
                "accepted_pins": {
                    "strict_gate_digest":
                        (entries.get("tools/u12_strict_receipt.py") or {}).get(
                            "sha256"),
                    "chandoff_intent_digest":
                        (entries.get("tools/chandoff_intent.py") or {}).get(
                            "sha256"),
                    "readiness_manifest_digest":
                        (entries.get(AUTHORITY_ARTIFACT_PATH) or {}).get(
                            "sha256"),
                },
                "artifact_rebuild_digest": bundle["artifact"]["digest"],
                "authority": bundle["authority"],
                "chain": bundle["chain"],
                "create_attempt": bundle["attempt"],
                "create_attempt_digest": digest(bundle["attempt"]),
            },
            "shared_history": bundle["shared_history"],
            "receipt": {
                "status": receipt["status"],
                "body": receipt["body"],
                "body_digest": receipt["body_digest"],
                "note": receipt["note"],
                "disposition_status": decision["original_receipt_body_status"],
                "disposition_scope": decision.get("receipt_limit_scope"),
            },
            "observations": observations,
            "observed": {
                "issue_id": issue["id"],
                "issue_revision": issue["revision"],
                "readback_raw_digest": _sha256_utf8(observed),
                "readback_lf_digest": digest_text_lf(observed),
                "readback_chars": len(observed),
                "readback_utf8_bytes": len(observed.encode("utf-8")),
                "source_raw_digest": _sha256_utf8(bundle["source"]),
                "source_lf_digest": digest_text_lf(bundle["source"]),
                "source_chars": len(bundle["source"]),
                "source_utf8_bytes": len(bundle["source"].encode("utf-8")),
                "relation": bundle["relation"]["relation"],
                "removed_terminal_lf": bundle["relation"]["removed_lf"],
                "profile": TRANSPORT_PROFILE,
                "exact_comparison": True,
                "evidence_digest": bundle["evidence_digest"],
                "observations_digest": digest(observations),
                "issue_projection_digest": digest(issue_projection(issue)),
                "recheck_revision": bundle["recheck"].get("revision"),
                "recheck_snapshot_digest":
                    digest(issue_projection(bundle["recheck"])),
                "comment_count": len(bundle["evidence"]["comments"]),
                "activity_count": len(bundle["evidence"]["activities"]),
                "run_count": len(bundle["evidence"]["runs"]),
                "discovery_total": discovery["total"],
                "discovery_declared_total": discovery["declared_total"],
                "discovery_matches": [
                    m["id"] for m in discovery["matches"]],
            },
            "transport": {
                "profile": TRANSPORT_PROFILE,
                "transformation": bundle["transformation"],
                "effective_transport_body_digest":
                    _sha256_utf8(bundle["transport_body"]),
                "effective_transport_body_lf_digest":
                    digest_text_lf(bundle["transport_body"]),
                "transport_chars": len(bundle["transport_body"]),
                "transport_utf8_bytes":
                    len(bundle["transport_body"].encode("utf-8")),
            },
            "execution_authority": {
                "contract_version": CONTRACT_VERSION,
                "adapter_module": ADAPTER_MODULE,
                "adapter_digest": adapter_digest(),
                "adapter_commit": execution_commit,
                "accepted_execution_commit":
                    decision["accepted_execution"]["commit"],
                "accepted_execution_adapter_digest":
                    decision["accepted_execution"]["adapter_digest"],
                "blob_resolution": execution_resolution,
                "strategy": "exact-known-predecessor-forward-recovery",
            },
            "coverage": {
                "timeline_is_complete_revision_journal": False,
                "note": "the documented CLI timeline carries actor/action/"
                        "timestamp with no issue-revision chain; revision "
                        "attribution stays delta-exclusion based (accepted "
                        "limitation). The inline observations reconstruct "
                        "every read actually used for this decision.",
            },
            "decision": decision,
            "decision_digest": decision["decision_digest"],
            "actor": actor,
            "observed_at": self.now(),
        }
        proof["proof_digest"] = digest(proof)
        return proof

    def _execute_create_recovery(self, intent_id: str, *,
                                 expected_target_id: str, decision: dict,
                                 actor: str, authority_evidence,
                                 execution_commit, execution_resolution,
                                 original_receipt=None) -> dict:
        self._claim(intent_id, actor)
        try:
            fresh = self.store.get(intent_id)
            fresh_data = validate_intent_record(fresh, executable=False)
            self._require_predecessor_recovery_state(fresh, decision)
            response_start = len(self.reader.responses)
            try:
                bundle = self._recovery_prerequisites(
                    fresh, fresh_data, expected_target_id=expected_target_id,
                    decision=decision,
                    authority_evidence=authority_evidence,
                    original_receipt=original_receipt)
            except PreflightRefusal as refusal:
                return self._recovery_refusal(intent_id, refusal, actor)
            bundle["raw_responses"] = list(
                self.reader.responses[response_start:])
            proof = self._build_recovery_proof(
                intent=fresh, bundle=bundle, decision=decision, actor=actor,
                execution_commit=execution_commit,
                execution_resolution=execution_resolution)
            evidence_event = self.store.append_event(
                intent_id, E_RECOVERY_EVIDENCE, actor=actor, now=self.now(),
                data=proof)
            current = self.store.get(intent_id)
            if current["revision"] != proof["intent_revision_before"] or \
                    current["state"] != o2.S_CREATE_AMBIGUOUS:
                raise R0BValidationRefused(
                    "the intent moved during recovery; the compare-and-set "
                    "refuses without any binding", intent_id=intent_id,
                    state=current["state"], revision=current["revision"])
            # Revalidate the shared prefix and classify the fresh tail
            # immediately before binding, in addition to the intent CAS: a
            # shared command can be appended without changing the revision.
            try:
                tail = self._classify_shared_history(
                    fresh, bundle["spec"], decision)
            except PreflightRefusal as refusal:
                return self._recovery_refusal(intent_id, refusal, actor)
            audited = bundle["shared_history"]["records"]
            if len(tail["records"]) != len(audited) + 1 or \
                    tail["records"][:-1] != audited or \
                    tail["records"][-1]["record"] != evidence_event:
                return self._recovery_refusal(
                    intent_id,
                    PreflightRefusal(
                        o2.S_BLOCKED, REASON_RECOVERY_HISTORY,
                        "the shared ledger prefix/tail changed while recovery "
                        "evidence was being written; revalidation refuses "
                        "before binding (revision CAS alone is insufficient)",
                        subject="tail_revalidation"),
                    actor)
            spec = bundle["spec"]
            issue = bundle["issue"]
            transport = proof["transport"]
            binding = dict(fresh_data)
            binding["contract_version"] = CONTRACT_VERSION
            binding["phase"] = "TARGET_BOUND"
            binding["target_binding"] = {
                "issue_id": issue["id"],
                "identifier": issue.get("identifier"),
                "parent_issue_id": issue.get("parent_issue_id"),
                "project_id": issue.get("project_id"),
                "target_role": spec["target_role"],
                "target_agent_id": spec["target_agent_id"],
                "post_create_revision": issue["revision"],
                "post_create_snapshot_digest":
                    digest(issue_projection(issue)),
                "post_create_status": bundle["status"],
                "creation_marker": spec["marker"],
                "creation_receipt_digest": None,
                "discovered_by_read_only_proof": True,
                "bound_at": self.now(),
                "recovered_forward_binding": True,
            }
            binding["recovery_proof"] = proof
            binding["execution_binding"] = {
                "schema": EXECUTION_BINDING_SCHEMA,
                "contract_version": CONTRACT_VERSION,
                "adapter_module": ADAPTER_MODULE,
                "adapter_digest": adapter_digest(),
                "adapter_commit": execution_commit,
                "accepted_execution_commit":
                    decision["accepted_execution"]["commit"],
                "accepted_execution_adapter_digest":
                    decision["accepted_execution"]["adapter_digest"],
                "original_contract_version": PREDECESSOR_CONTRACT_VERSION,
                "original_adapter_digest": PREDECESSOR_ADAPTER_DIGEST,
                "predecessor_commit": PREDECESSOR_ADAPTER_COMMIT,
                "transport_profile": transport["profile"],
                "effective_transport_body_digest":
                    transport["effective_transport_body_digest"],
                "effective_transport_body_lf_digest":
                    transport["effective_transport_body_lf_digest"],
                "recovery_proof_digest": proof["proof_digest"],
                "recovery_decision_digest": decision["decision_digest"],
                "evidence_event_seq": evidence_event.get("seq"),
                "evidence_event_digest": digest(evidence_event),
                "transition_revision": current["revision"] + 1,
                "bound_at": self.now(),
            }
            fields = {
                "issue_id": issue["id"],
                "expected_issue_revision": issue["revision"],
                "expected_status_category": bundle["status"],
                "expected_assignee_id": None,
                "creation_receipt_digest": None,
                "discovered_by_read_only_proof": True,
                R0B_FIELD: binding,
            }
            transition = self.store.transition(
                intent_id, o2.S_TARGET_BOUND,
                expected_revision=current["revision"], actor=actor,
                now=self.now(), fields=fields)
            if transition.get("revision") != current["revision"] + 1:
                raise R0BValidationRefused(
                    "recovery transition revision is not the exact "
                    "compare-and-set successor", intent_id=intent_id)
            self.store.append_event(
                intent_id, E_RECOVERY_BOUND, actor=actor, now=self.now(),
                data={"issue_id": issue["id"],
                      "proof_digest": proof["proof_digest"],
                      "decision_digest": decision["decision_digest"],
                      "transition_revision": transition["revision"],
                      "transport_profile": transport["profile"],
                      "effective_transport_body_digest":
                          transport["effective_transport_body_digest"],
                      "next_action": RECOVERY_OWNERSHIP_NEXT})
            return {"status": o2.S_TARGET_BOUND, "intent_id": intent_id,
                    "issue_id": issue["id"],
                    "identifier": issue.get("identifier"),
                    "issue_revision": issue["revision"],
                    "revision": transition["revision"], "replayed": False,
                    "side_effects": 0,
                    "next_action": RECOVERY_OWNERSHIP_NEXT,
                    "proof_digest": proof["proof_digest"]}
        finally:
            self._release(intent_id, actor)

    # -- step 3: ownership without start ------------------------------------
    def assign_ownership_once(self, intent_id: str, *, actor: str) -> dict:
        intent = self._load(intent_id,
                            require=("creation_spec", "target_binding"))
        data = intent["fields"][R0B_FIELD]
        if intent["state"] in (o2.S_BLOCKED, o2.S_CANCELLED,
                               o2.S_REFRESH_REQUIRED):
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] != o2.S_TARGET_BOUND:
            raise o2.IllegalTransitionError(
                "ownership binding requires TARGET_BOUND",
                intent_id=intent_id, state=intent["state"])
        target = data["target_binding"]
        issue_id = target["issue_id"]
        agent_id = data["creation_spec"]["target_agent_id"]
        prior = [e for e in intent["events"]
                 if e.get("name") == E_OWNERSHIP_ISSUING]
        bound = [e for e in intent["events"]
                 if e.get("name") in (E_OWNERSHIP_BOUND,
                                      E_OWNERSHIP_RECOVERED)]
        if bound:
            return {"status": o2.S_TARGET_BOUND, "intent_id": intent_id,
                    "issue_id": issue_id, "replayed": True, "side_effects": 0}
        if prior:
            return self._recover_ownership(intent_id, data=data, actor=actor,
                                           detail="ownership attempt already "
                                                  "recorded; no repeat call")
        self._claim(intent_id, actor)
        try:
            try:
                issue = self.boundary.issue_get(issue_id)
            except o2.IntentError as exc:
                return self._stop(intent_id, o2.S_BLOCKED,
                                  REASON_OWNERSHIP_UNPROVEN, actor,
                                  detail=f"pre-ownership read failed: "
                                         f"{exc.message}")
            if not isinstance(issue.get("revision"), int):
                return self._stop(intent_id, o2.S_BLOCKED,
                                  REASON_OWNERSHIP_UNPROVEN, actor,
                                  detail="pre-ownership read has no revision")
            self.store.append_event(
                intent_id, E_OWNERSHIP_ISSUING, actor=actor, now=self.now(),
                data={"contract_version": CONTRACT_VERSION,
                      "operation_id": new_operation_id("ownership", {
                          "intent_id": intent_id, "issue_id": issue_id,
                          "agent_id": agent_id}),
                      "issue_id": issue_id, "agent_id": agent_id,
                      "before_revision": issue["revision"],
                      "before_snapshot_digest":
                          digest(issue_projection(issue))})
            try:
                receipt = self.boundary.assign_ownership_no_start(issue_id,
                                                                  agent_id)
            except o2.IntentError as exc:
                return self._stop(intent_id, o2.S_BLOCKED,
                                  REASON_OWNERSHIP_UNPROVEN, actor,
                                  detail=f"ownership call failed: "
                                         f"{exc.message}")
            try:
                after = self.boundary.issue_get(issue_id)
            except o2.IntentError as exc:
                return self._stop(intent_id, o2.S_BLOCKED,
                                  REASON_OWNERSHIP_UNPROVEN, actor,
                                  detail=f"post-ownership read failed: "
                                         f"{exc.message}")
            return self._confirm_ownership(intent_id, data=data, issue=after,
                                           actor=actor, receipt=receipt)
        finally:
            self._release(intent_id, actor)

    def _confirm_ownership(self, intent_id: str, *, data: dict, issue: dict,
                           actor: str, receipt: dict | None) -> dict:
        spec = data["creation_spec"]
        agent_id = spec["target_agent_id"]
        target = data["target_binding"]
        problems = []
        if issue.get("assignee_id") != agent_id:
            problems.append("assignee did not reach the mapped 04 agent")
        status = issue.get("status_category") or issue.get("status")
        if status != BACKLOG_STATUS:
            problems.append(f"status moved away from backlog: {status}")
        if issue.get("id") != target["issue_id"]:
            problems.append("issue identity changed")
        if issue.get("parent_issue_id") != spec["parent_issue_id"]:
            problems.append("parent changed during ownership binding")
        if not isinstance(issue.get("revision"), int):
            problems.append("post-ownership read has no revision")
        if problems:
            self.store.append_event(
                intent_id, E_EVIDENCE_REFUSED, actor=actor, now=self.now(),
                data={"reason": REASON_OWNERSHIP_UNPROVEN,
                      "detail": "; ".join(problems)[:200]})
            return self._stop(intent_id, o2.S_BLOCKED,
                              REASON_OWNERSHIP_UNPROVEN, actor,
                              detail="; ".join(problems))
        binding = dict(data)
        binding["phase"] = "OWNERSHIP_BOUND"
        target = dict(target)
        target.update({
            "post_ownership_revision": issue["revision"],
            "post_ownership_snapshot_digest": digest(issue_projection(issue)),
            "post_ownership_status": status,
            "post_ownership_assignee_id": issue.get("assignee_id"),
            "ownership_receipt_digest": (digest(receipt) if receipt else None),
            "ownership_bound_at": self.now(),
        })
        binding["target_binding"] = target
        self.store.append_event(
            intent_id, E_OWNERSHIP_BOUND, actor=actor, now=self.now(),
            data={"issue_id": target["issue_id"],
                  "assignee_id": issue.get("assignee_id"),
                  "revision": issue["revision"],
                  "status": status,
                  "counted_as_trigger": False,
                  "post_ownership_revision": issue["revision"],
                  "post_ownership_snapshot_digest":
                      digest(issue_projection(issue)),
                  "target_binding_digest": digest(target)})
        return {"status": o2.S_TARGET_BOUND, "intent_id": intent_id,
                "issue_id": issue["id"],
                "assignee_id": issue.get("assignee_id"),
                "issue_revision": issue["revision"],
                "ownership_bound": True, "side_effects": 0}

    def _recover_ownership(self, intent_id: str, *, data: dict, actor: str,
                           detail: str) -> dict:
        target = data.get("target_binding") or {}
        issue_id = target.get("issue_id")
        agent_id = data["creation_spec"]["target_agent_id"]
        if not issue_id:
            return self._stop(intent_id, o2.S_BLOCKED,
                              REASON_OWNERSHIP_UNPROVEN, actor,
                              detail=f"{detail}; no bound target exists")
        try:
            issue = self.boundary.issue_get(issue_id)
        except o2.IntentError as exc:
            return self._stop(intent_id, o2.S_BLOCKED,
                              REASON_OWNERSHIP_UNPROVEN, actor,
                              detail=f"{detail}; read failed: {exc.message}")
        status = issue.get("status_category") or issue.get("status")
        if issue.get("assignee_id") == agent_id and status == BACKLOG_STATUS:
            self.store.append_event(
                intent_id, E_OWNERSHIP_RECOVERED, actor=actor, now=self.now(),
                data={"issue_id": issue_id, "assignee_id": agent_id,
                      "revision": issue.get("revision"),
                      "status": status,
                      "proof": "actual issue shows the exact authorized "
                               "ownership; zero additional calls",
                      "counted_as_trigger": False})
            return {"status": o2.S_TARGET_BOUND, "intent_id": intent_id,
                    "issue_id": issue_id, "assignee_id": agent_id,
                    "issue_revision": issue.get("revision"),
                    "recovered": True, "side_effects": 0}
        return self._stop(
            intent_id, o2.S_BLOCKED, REASON_OWNERSHIP_UNPROVEN, actor,
            detail=f"{detail}; actual assignee/status do not prove the "
                   "authorized ownership; never repeat an uncertain assign")

    # -- step 4: bind E ------------------------------------------------------
    def _ownership_evidence(self, intent: dict) -> dict | None:
        """Latest durable ownership proof from the append-only events."""
        bound = None
        recovered = None
        for event in intent["events"]:
            if event.get("name") == E_OWNERSHIP_BOUND:
                bound = event
            elif event.get("name") == E_OWNERSHIP_RECOVERED:
                recovered = event
        if bound is not None:
            data = bound.get("data") or {}
            return {
                "revision": data.get("post_ownership_revision",
                                     data.get("revision")),
                "snapshot_digest": data.get("post_ownership_snapshot_digest"),
                "status": data.get("status"),
                "assignee_id": data.get("assignee_id"),
                "receipt_digest": data.get("receipt_digest"),
                "bound_at": bound.get("at"),
                "recovered": False,
            }
        if recovered is not None:
            data = recovered.get("data") or {}
            return {
                "revision": data.get("revision"),
                "snapshot_digest": None,
                "status": data.get("status"),
                "assignee_id": data.get("assignee_id"),
                "receipt_digest": None,
                "bound_at": recovered.get("at"),
                "recovered": True,
            }
        return None

    def bind_execution_package(self, intent_id: str, *,
                               execution_context: dict, actor: str) -> dict:
        intent = self._load(intent_id, require=("creation_spec",))
        data = intent["fields"][R0B_FIELD]
        if intent["state"] == o2.S_REFRESH_REQUIRED:
            return {"status": o2.S_REFRESH_REQUIRED, "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] != o2.S_TARGET_BOUND:
            raise o2.IllegalTransitionError(
                "E binding requires TARGET_BOUND",
                intent_id=intent_id, state=intent["state"])
        if [e for e in intent["events"]
                if e.get("name") == E_PUBLICATION_ISSUING]:
            raise R0BValidationRefused(
                "E may not be replaced after a publication attempt exists",
                intent_id=intent_id)
        target = data.get("target_binding") or {}
        issue_id = target.get("issue_id")
        if not issue_id:
            raise R0BValidationRefused("intent has no bound target")
        spec = data["creation_spec"]
        execution = r0b_execution_context(
            execution_context, spec,
            artifact_digest=data["artifact_dependency"]["digest"])
        if execution.get("target_task_ref") != \
                f"multica://issue/{target.get('identifier')}":
            raise R0BValidationRefused(
                "E task_ref does not address the real returned target",
                expected=f"multica://issue/{target.get('identifier')}",
                found=execution.get("target_task_ref"))
        self._claim(intent_id, actor)
        try:
            try:
                issue = self.boundary.issue_get(issue_id)
            except o2.IntentError as exc:
                return self._stop(intent_id, o2.S_BLOCKED,
                                  REASON_TARGET_DRIFT, actor,
                                  detail=f"E binding read failed: "
                                         f"{exc.message}")
            ownership = self._ownership_evidence(intent)
            problems = []
            if issue.get("assignee_id") != spec["target_agent_id"]:
                problems.append("assignee is not the mapped 04 agent")
            status = issue.get("status_category") or issue.get("status")
            if status != BACKLOG_STATUS:
                problems.append(f"status is not backlog: {status}")
            if issue.get("parent_issue_id") != spec["parent_issue_id"]:
                problems.append("parent drifted after ownership")
            expected_revision = (ownership or {}).get("revision",
                                                      target.get(
                                                          "post_ownership_revision"))
            if expected_revision is not None and \
                    issue.get("revision") != expected_revision:
                problems.append(
                    f"revision drifted after ownership: "
                    f"{expected_revision} -> {issue.get('revision')}")
            if problems:
                self.store.append_event(
                    intent_id, E_EVIDENCE_REFUSED, actor=actor,
                    now=self.now(),
                    data={"reason": REASON_TARGET_DRIFT,
                          "detail": "; ".join(problems)[:200]})
                return self._stop(intent_id, o2.S_REFRESH_REQUIRED,
                                  REASON_TARGET_DRIFT, actor,
                                  detail="; ".join(problems))
            binding = dict(data)
            binding["phase"] = "HANDOFF_PREPARED"
            binding["execution_context"] = execution
            target = dict(target)
            if ownership:
                target.update({
                    "post_ownership_revision": ownership.get("revision"),
                    "post_ownership_snapshot_digest":
                        ownership.get("snapshot_digest"),
                    "post_ownership_status": ownership.get("status"),
                    "post_ownership_assignee_id":
                        ownership.get("assignee_id"),
                    "ownership_receipt_digest":
                        ownership.get("receipt_digest"),
                    "ownership_recovered_read_only":
                        ownership.get("recovered", False),
                })
            target.update({
                "pre_publication_revision": issue["revision"],
                "pre_publication_snapshot_digest":
                    digest(issue_projection(issue)),
                "pre_publication_status": status,
                "pre_publication_assignee_id": issue.get("assignee_id"),
            })
            binding["target_binding"] = target
            fields = {
                "package_id": execution["package_id"],
                "artifact_dependency_digest":
                    data["artifact_dependency"]["digest"],
                "expected_issue_revision": issue["revision"],
                "expected_status_category": status,
                "expected_assignee_id": issue.get("assignee_id"),
                R0B_FIELD: binding,
            }
            transition = self.store.transition(
                intent_id, o2.S_HANDOFF_PREPARED,
                expected_revision=self.store.get(intent_id)["revision"],
                actor=actor, now=self.now(), fields=fields)
            self.store.append_event(
                intent_id, E_EXECUTION_BOUND, actor=actor, now=self.now(),
                data={"package_id": execution["package_id"],
                      "target_task_ref": execution["target_task_ref"],
                      "artifact_dependency_digest":
                          data["artifact_dependency"]["digest"],
                      "issue_revision": issue["revision"],
                      "creation_package_id":
                          data["creation_context"]["package_id"]})
            return {"status": o2.S_HANDOFF_PREPARED, "intent_id": intent_id,
                    "package_id": execution["package_id"],
                    "issue_revision": issue["revision"],
                    "revision": transition["revision"], "side_effects": 0}
        finally:
            self._release(intent_id, actor)

    # -- step 5+6: publish once, confirm and bind ---------------------------
    def publish_handoff_once(self, intent_id: str, *, actor: str,
                             publisher_run_id: str,
                             prepared_by: str,
                             prepared_at: str | None = None,
                             parent_comment_id: str | None = None) -> dict:
        intent = self._load(intent_id, require=("execution_context",
                                                "creation_spec"))
        data = intent["fields"][R0B_FIELD]
        existing_attempts = [e for e in intent["events"]
                             if e.get("name") == E_PUBLICATION_ISSUING]
        if existing_attempts:
            if intent["state"] == o2.S_HANDOFF_PUBLISHED:
                return {"status": o2.S_HANDOFF_PUBLISHED,
                        "intent_id": intent_id, "replayed": True,
                        "side_effects": 0}
            return {
                "status": intent["state"],
                "outcome": "PUBLICATION_ALREADY_ATTEMPTED",
                "reason": REASON_PUBLICATION_AMBIGUOUS,
                "detail": "a publication attempt is already recorded; the "
                          "only available recovery is read-only confirmation, "
                          "never a second publication",
                "intent_id": intent_id, "side_effects": 0,
            }
        if intent["state"] in (o2.S_BLOCKED, o2.S_CANCELLED,
                               o2.S_REFRESH_REQUIRED):
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] != o2.S_HANDOFF_PREPARED:
            raise o2.IllegalTransitionError(
                "publication requires HANDOFF_PREPARED",
                intent_id=intent_id, state=intent["state"])
        _require_uuid(publisher_run_id, "publisher_run_id")
        execution = data["execution_context"]
        envelope = execution.get("result")
        if not isinstance(envelope, dict):
            raise R0BValidationRefused(
                "E envelope is not available for publish")
        approved_at = o2._require_text(prepared_at or self.now(),
                                       "prepared_at")
        body, _record = note.render_note_record(
            envelope,
            prepared_by=o2._require_text(prepared_by, "prepared_by"),
            prepared_at=approved_at)
        body_digest = digest_text_lf(body)
        attempt = {
            "contract_version": CONTRACT_VERSION,
            "operation_id": new_operation_id("publication", {
                "intent_id": intent_id, "body_digest": body_digest}),
            "package_id": execution["package_id"],
            "envelope_digest": execution["envelope_digest"],
            "body_digest": body_digest,
            "artifact_dependency_digest":
                data["artifact_dependency"]["digest"],
            "prepared_by": prepared_by,
            "prepared_at": approved_at,
            "publisher_run_id": publisher_run_id,
            "parent_comment_id": parent_comment_id,
            "before_issue_revision": intent["fields"].get(
                "expected_issue_revision"),
        }
        self._claim(intent_id, actor)
        try:
            try:
                before = collect_evidence(self.reader,
                                          binding_issue_id(data))
            except PublicationProvenanceIncomplete as exc:
                self.store.append_event(
                    intent_id, E_EVIDENCE_REFUSED, actor=actor,
                    now=self.now(),
                    data={"reason": REASON_PUBLICATION_PROVENANCE,
                          "detail": exc.message[:200]})
                return self._stop(intent_id, o2.S_BLOCKED,
                                  REASON_PUBLICATION_PROVENANCE, actor,
                                  detail=exc.message)
            attempt["before"] = ledger_evidence(before)
            attempt["before_evidence_digest"] = evidence_digest(before)
            self.store.append_event(
                intent_id, E_PUBLICATION_ISSUING, actor=actor, now=self.now(),
                data=attempt)
            try:
                published = note.publish_handoff(
                    envelope, issue_id=binding_issue_id(data),
                    prepared_by=prepared_by, prepared_at=approved_at,
                    parent_comment_id=parent_comment_id, cli=self.note_cli)
            except Exception as exc:  # noqa: BLE001 - never retry a send
                self.store.append_event(
                    intent_id, E_PUBLICATION_UNCERTAIN, actor=actor,
                    now=self.now(),
                    data={"operation_id": attempt["operation_id"],
                          "error": f"{type(exc).__name__}: {exc}"[:200],
                          "retry": False})
                # Deliberately keep HANDOFF_PREPARED: the durable attempt is
                # the at-most-once boundary and the only available recovery is
                # a read-only confirmation of exactly one matching note.
                return {
                    "status": intent["state"],
                    "outcome": "PUBLICATION_UNCERTAIN",
                    "reason": REASON_PUBLICATION_AMBIGUOUS,
                    "detail": "publication attempt outcome is uncertain; no "
                              "retry and no second publication; recovery is "
                              "read-only confirmation",
                    "intent_id": intent_id, "side_effects": 0,
                }
            self.store.append_event(
                intent_id, E_PUBLICATION_RESPONSE, actor=actor, now=self.now(),
                data={"operation_id": attempt["operation_id"],
                      "published": bool(published.get("published")),
                      "idempotent": bool(published.get("idempotent")),
                      "comment_id": (published.get("comment") or {}).get("id"),
                      "body_sha256": published.get("body_sha256"),
                      "write_commands": (published.get("trace") or {}).get(
                          "write_commands")})
            return self._confirm_locked(intent_id, data=data, actor=actor,
                                        attempt=attempt, response=published)
        finally:
            self._release(intent_id, actor)

    def confirm_publication_and_bind(self, intent_id: str, *,
                                     actor: str) -> dict:
        intent = self._load(intent_id, require=("execution_context",
                                                "creation_spec"))
        if intent["state"] == o2.S_HANDOFF_PUBLISHED:
            return {"status": o2.S_HANDOFF_PUBLISHED, "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] in (o2.S_BLOCKED, o2.S_CANCELLED,
                               o2.S_REFRESH_REQUIRED):
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] != o2.S_HANDOFF_PREPARED:
            raise o2.IllegalTransitionError(
                "publication confirmation requires HANDOFF_PREPARED",
                intent_id=intent_id, state=intent["state"])
        attempts = [e for e in intent["events"]
                    if e.get("name") == E_PUBLICATION_ISSUING]
        if len(attempts) != 1:
            return self._stop(intent_id, o2.S_BLOCKED,
                              REASON_PUBLICATION_PROVENANCE, actor,
                              detail=f"expected one publication attempt, "
                                     f"found {len(attempts)}")
        data = intent["fields"][R0B_FIELD]
        attempt = attempts[0]["data"]
        if not isinstance(attempt.get("before"), dict):
            return self._stop(intent_id, o2.S_BLOCKED,
                              REASON_PUBLICATION_PROVENANCE, actor,
                              detail="publication attempt has no durable "
                                     "before-evidence boundary")
        self._claim(intent_id, actor)
        try:
            return self._confirm_locked(intent_id, data=data, actor=actor,
                                        attempt=attempt, response=None)
        finally:
            self._release(intent_id, actor)

    def _confirm_locked(self, intent_id: str, *, data: dict, actor: str,
                        attempt: dict, response: dict | None) -> dict:
        execution = data["execution_context"]
        envelope = execution["result"]
        expected = {
            "body_digest": attempt["body_digest"],
            "envelope_digest": execution["envelope_digest"],
            "envelope_canonical": canonical_json(envelope),
            "package_id": execution["package_id"],
            "task_ref": execution["task_ref"],
            "role": execution["role"],
            "artifact_dependency_digest":
                data["artifact_dependency"]["digest"],
            "author_id": data["creation_spec"]["publisher_agent_id"],
            "author_type": "agent",
            "source_task_id": attempt.get("publisher_run_id"),
            "parent_id": attempt.get("parent_comment_id"),
            "note_comment_id": ((response or {}).get("comment") or {}).get("id"),
        }
        before = attempt.get("before") or {}
        try:
            after = collect_evidence(self.reader, binding_issue_id(data))
            recheck = self.reader.issue_get(binding_issue_id(data))
        except PublicationProvenanceIncomplete as exc:
            self.store.append_event(
                intent_id, E_EVIDENCE_REFUSED, actor=actor, now=self.now(),
                data={"reason": REASON_PUBLICATION_PROVENANCE,
                      "detail": exc.message[:200]})
            return self._stop(intent_id, o2.S_BLOCKED,
                              REASON_PUBLICATION_PROVENANCE, actor,
                              detail=exc.message)
        artifact_ok = self._artifact_recheck(data)
        fingerprint_ok = self._fingerprint_recheck(data)
        self_check_ok = self._self_check_ok(data)
        verdict = evaluate_publication_predicate(
            attempt_count=1, attempt=attempt, before=before, after=after,
            recheck=recheck, expected=expected, artifact_ok=artifact_ok,
            fingerprint_ok=fingerprint_ok, self_check_ok=self_check_ok)
        if not verdict["ok"]:
            reason = (REASON_PUBLICATION_PROVENANCE
                      if verdict["code"] in (PUB_INCOMPLETE,
                                             PUB_NOTE_NOT_FOUND,
                                             PUB_DUPLICATE_NOTE)
                      else REASON_PUBLICATION_CONFLICT)
            self.store.append_event(
                intent_id, E_EVIDENCE_REFUSED, actor=actor, now=self.now(),
                data={"reason": reason, "code": verdict["code"],
                      "detail": verdict["detail"][:200]})
            return self._stop(intent_id, o2.S_BLOCKED, reason, actor,
                              detail=f"{verdict['code']}: "
                                     f"{verdict['detail']}")
        proof = verdict["proof"]
        binding = dict(data)
        binding["phase"] = "HANDOFF_PUBLISHED"
        binding["publication_binding"] = dict(proof, **{
            "issue_id": binding_issue_id(data),
            "package_id": execution["package_id"],
            "task_ref": execution["task_ref"],
            "role": execution["role"],
            "publisher_run_id": attempt.get("publisher_run_id"),
            "prepared_by": attempt.get("prepared_by"),
            "prepared_at": attempt.get("prepared_at"),
            "operation_id": attempt.get("operation_id"),
        })
        target = dict(binding.get("target_binding") or {})
        target.update({
            "post_publication_revision": proof["after_issue_revision"],
            "post_publication_snapshot_digest":
                digest(issue_projection(after["issue"])),
            "post_publication_projection": issue_projection(after["issue"]),
        })
        binding["target_binding"] = target
        fields = {
            "expected_issue_revision": proof["after_issue_revision"],
            "expected_status_category": (after["issue"].get("status_category")
                                         or after["issue"].get("status")),
            "expected_assignee_id": after["issue"].get("assignee_id") or None,
            "note_comment_id": proof["note_comment_id"],
            "publish_receipt_digest": attempt.get("operation_id"),
            "published_at": self.now(),
            R0B_FIELD: binding,
        }
        revision = self.store.get(intent_id)["revision"]
        transition = self.store.transition(
            intent_id, o2.S_HANDOFF_PUBLISHED, expected_revision=revision,
            actor=actor, now=self.now(), fields=fields)
        self.store.append_event(
            intent_id, E_PUBLICATION_BOUND, actor=actor, now=self.now(),
            data={"note_comment_id": proof["note_comment_id"],
                  "post_publication_revision": proof["after_issue_revision"],
                  "attribution": proof["revision_attribution"],
                  "operation_id": attempt.get("operation_id")})
        return {
            "status": o2.S_HANDOFF_PUBLISHED, "intent_id": intent_id,
            "note_comment_id": proof["note_comment_id"],
            "post_publication_revision": proof["after_issue_revision"],
            "revision_attribution": proof["revision_attribution"],
            "revision": transition["revision"], "side_effects": 0,
            "proof": proof,
        }

    def _artifact_recheck(self, data: dict) -> bool:
        """Publication-predicate only. A usable reader is mandatory: the
        source-less default success is refused here; the arm/trigger
        preflight revalidates the full bound dependency independently."""
        if self.artifact_blob_reader is None and self.artifact_root is None:
            return False
        entries = data["artifact_dependency"]["entries"]
        try:
            rebuilt = build_artifact_dependency_digest(
                [dict(v, path=k) for k, v in entries.items()],
                root=self.artifact_root,
                blob_reader=self.artifact_blob_reader)
        except R0BError:
            return False
        return rebuilt["digest"] == data["artifact_dependency"]["digest"]

    def _fingerprint_recheck(self, data: dict) -> bool:
        """Recompute the frozen fingerprint + every built_from revision."""
        execution = data.get("execution_context") or {}
        request = execution.get("request")
        if not isinstance(request, dict):
            return False
        try:
            return chandoff.compute_built_from(request) == execution["built_from"]
        except (KeyError, TypeError):
            return False

    def _self_check_ok(self, data: dict) -> bool:
        """Publication-predicate SELF_CHECK over the bound E, no writes.

        This is the YZT-83 publication acceptance predicate, which runs
        before any arming; the mandatory arm/trigger preflight runs the
        frozen SELF_CHECK separately against the fresh request and the
        current Finding source instead of substituting an empty list.
        """
        execution = data.get("execution_context") or {}
        request = execution.get("request")
        envelope = execution.get("result")
        if not isinstance(request, dict) or not isinstance(envelope, dict):
            return False
        try:
            check = selfcheck.self_check(
                self_check_request_from_prepare(request),
                packages=[envelope], findings=[])
        except (o2.IntentError, ValueError):
            return False
        return check.get("status") == "READY" and \
            check.get("action") == "USE_EXISTING"

    # -- step 7: arm (strict-only rerun factory) ----------------------------
    def arm(self, intent_id: str, *, actor: str,
            current_request: dict | None = None,
            current_findings: list | None = None,
            authority_evidence: dict | None = None) -> dict:
        intent = self._load(intent_id, require=("execution_context",
                                                "publication_binding"))
        data = intent["fields"][R0B_FIELD]
        if intent["state"] in R0B_STOP_STATES:
            return {"status": intent["state"], "intent_id": intent_id,
                    "replayed": True, "side_effects": 0}
        if intent["state"] != o2.S_HANDOFF_PUBLISHED:
            raise o2.IllegalTransitionError(
                "arm requires HANDOFF_PUBLISHED", intent_id=intent_id,
                state=intent["state"])
        try:
            bundle = self._preflight_materials(
                intent_id, data, checkpoint="ARM",
                current_request=current_request,
                current_findings=current_findings,
                authority_evidence=authority_evidence)
        except PreflightRefusal as refusal:
            return self._refuse_preflight(intent_id, refusal, actor)
        self._require_intent_unchanged(intent_id, intent)
        self._record_preflight(intent_id, actor, bundle)
        publication = data["publication_binding"]
        snapshot = self._build_snapshot(data, issue=bundle["issue"],
                                        runs=bundle["runs"])
        result = super().plan_and_arm(intent_id, snapshot, actor=actor)
        plan_result = result.get("plan") or {}
        if result.get("status") == o2.S_TRIGGER_READY:
            if plan_result.get("selected_trigger") != o2.TRIGGER_RERUN:
                return self._stop(intent_id, o2.S_REFRESH_REQUIRED,
                                  REASON_TARGET_DRIFT, actor,
                                  detail="arming selected a non-rerun trigger")
            if plan_result.get("ownership_binding") is not None:
                return self._stop(
                    intent_id, o2.S_REFRESH_REQUIRED, REASON_TARGET_DRIFT,
                    actor,
                    detail="arming selected an ownership binding; the R0 path "
                           "binds ownership before publication")
            if publication.get("note_comment_id") != snapshot.get(
                    "ready_note_id"):
                return self._stop(
                    intent_id, o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    actor,
                    detail="armed snapshot note is not the bound note")
        return result

    def _build_snapshot(self, data: dict, *, issue: dict, runs: list) -> dict:
        execution = data["execution_context"]
        publication = data["publication_binding"]
        spec = data["creation_spec"]
        package = {
            "package_id": execution["package_id"],
            "artifact_dependency_digest":
                data["artifact_dependency"]["digest"],
            "artifact_ready": True,
        }
        ready_note = {
            "comment_id": publication["note_comment_id"],
            "package_id": execution["package_id"],
            "artifact_dependency_digest":
                data["artifact_dependency"]["digest"],
        }
        return o2.build_snapshot(
            issue=issue, runs=runs, package=package, ready_note=ready_note,
            target_role=spec["target_role"],
            target_agent_id=spec["target_agent_id"])

    # -- step 8: strict trigger ---------------------------------------------
    def trigger(self, intent_id: str, *, actor: str,
                current_request: dict | None = None,
                current_findings: list | None = None,
                authority_evidence: dict | None = None) -> dict:
        intent = self._load(intent_id, require=("execution_context",
                                                "publication_binding"))
        data = intent["fields"][R0B_FIELD]
        state = intent["state"]
        if state in (o2.S_TRIGGER_ISSUING, o2.S_TRIGGER_AMBIGUOUS):
            return {"status": state, "intent_id": intent_id, "replayed": True,
                    "outcome": "READ_ONLY_RECONCILE_REQUIRED",
                    "reason": REASON_TRIGGER_ALREADY_ISSUED,
                    "detail": "a trigger has already been issued; only the "
                              "existing read-only reconciliation is "
                              "available and no fresh re-eligibility check "
                              "may run", "side_effects": 0}
        if state in TRIGGER_REPLAY_STATES:
            return {"status": state, "intent_id": intent_id, "replayed": True,
                    "side_effects": 0}
        if state != o2.S_TRIGGER_READY:
            raise o2.IllegalTransitionError(
                "trigger requires an armed intent", intent_id=intent_id,
                state=state)
        try:
            bundle = self._preflight_materials(
                intent_id, data, checkpoint="TRIGGER",
                current_request=current_request,
                current_findings=current_findings,
                authority_evidence=authority_evidence)
        except PreflightRefusal as refusal:
            return self._refuse_preflight(intent_id, refusal, actor)
        self._require_intent_unchanged(intent_id, intent)
        self._record_preflight(intent_id, actor, bundle)
        snapshot = self._build_snapshot(data, issue=bundle["issue"],
                                        runs=bundle["runs"])
        return super().issue_trigger(intent_id, snapshot, actor=actor)

    # -- recovery ------------------------------------------------------------
    def recover(self, intent_id: str, *, actor: str,
                allow_create: bool = False) -> dict:
        result = self._recover_impl(intent_id, actor=actor,
                                    allow_create=allow_create)
        if "status" not in result:
            result["status"] = self.store.get(intent_id)["state"]
        return result

    def _recover_impl(self, intent_id: str, *, actor: str,
                      allow_create: bool = False) -> dict:
        intent = self._load(intent_id, executable=False)
        data = intent["fields"].get(R0B_FIELD) or {}
        state = intent["state"]
        names = {e.get("name") for e in intent["events"]}
        self.store.append_event(
            intent_id, E_RECOVERY, actor=actor, now=self.now(),
            data={"window": state, "read_only": True,
                  "create": E_CREATE_ISSUING in names,
                  "ownership": E_OWNERSHIP_ISSUING in names,
                  "publication": E_PUBLICATION_ISSUING in names})
        if data.get("contract_version") == PREDECESSOR_CONTRACT_VERSION:
            return {
                "classification": "PREDECESSOR_CREATE_RECOVERY_REQUIRED",
                "action": "RECOVER_CREATED_TARGET", "performed": False,
                "reason": "the record was recorded by the known predecessor "
                          "adapter and is not executable under these bytes; "
                          "use the narrow recover_created_target operation "
                          "with a Lead recovery disposition",
                "side_effects": 0}
        if state in (o2.S_INTENT_RECORDED, o2.S_CREATE_AMBIGUOUS):
            if E_CREATE_ISSUING not in names:
                if not allow_create:
                    return {"classification": "POST_INTENT_PRE_CREATE",
                            "action": "RESUME_CREATE",
                            "performed": False,
                            "reason": "no create attempt exists; supply "
                                      "allow_create to issue the single "
                                      "authorized creation",
                            "side_effects": 0}
                return self.create_target_once(intent_id, actor=actor)
            return self._recover_create(intent_id, data=data, actor=actor,
                                        detail="resume after recorded create")
        if state == o2.S_TARGET_BOUND:
            if E_OWNERSHIP_BOUND in names or E_OWNERSHIP_RECOVERED in names:
                return {"classification": "POST_BIND_PRE_PREPARE",
                        "action": "RESUME_PREPARE", "performed": False,
                        "reason": "ownership is proven; bind E through "
                                  "bind_execution_package", "side_effects": 0}
            if E_OWNERSHIP_ISSUING not in names:
                return {"classification": "POST_BIND_PRE_OWNERSHIP",
                        "action": "RESUME_OWNERSHIP", "performed": False,
                        "reason": "no ownership attempt exists; one "
                                  "authorized no-start call is still "
                                  "available", "side_effects": 0}
            return self._recover_ownership(intent_id, data=data, actor=actor,
                                           detail="resume after ownership "
                                                  "attempt")
        if state == o2.S_HANDOFF_PREPARED:
            if E_PUBLICATION_ISSUING not in names:
                return {"classification": "POST_PREPARE_PRE_PUBLICATION",
                        "action": "RESUME_PUBLISH", "performed": False,
                        "reason": "E is bound and no publication attempt "
                                  "exists; publish through the factory",
                        "side_effects": 0}
            return self.confirm_publication_and_bind(intent_id, actor=actor)
        if state == o2.S_HANDOFF_PUBLISHED:
            return {"classification": "POST_PUBLISH_PRE_TRIGGER",
                    "action": "RESUME_ARM_AND_TRIGGER", "performed": False,
                    "reason": "instruction only, never reusable authorization: "
                              "the resumed arm/trigger entrypoints recollect "
                              "fresh material, current authority and Finding "
                              "evidence and fail closed on any drift",
                    "side_effects": 0}
        if state == o2.S_TRIGGER_READY:
            return {"classification": "TRIGGER_READY_FRESH_VALIDATION",
                    "action": "RESUME_TRIGGER", "performed": False,
                    "reason": "instruction only: the resumed trigger reruns "
                              "the full fresh material preflight before the "
                              "single strict trigger", "side_effects": 0}
        if state in (o2.S_TRIGGER_ISSUING, o2.S_TRIGGER_AMBIGUOUS):
            return {"classification": "POST_TRIGGER_READ_ONLY",
                    "action": "READ_ONLY_RECONCILE", "performed": False,
                    "reason": "use the accepted O2 read-only reconciliation; "
                              "never reissue", "side_effects": 0}
        if state == o2.S_REFRESH_REQUIRED:
            return {"classification": "REFRESH_REQUIRED_STOP",
                    "action": "LEAD_DISPOSITION", "performed": False,
                    "reason": "a changed package/artifact requires Lead "
                              "disposition; this canary has no automatic "
                              "refresh/republish loop", "side_effects": 0}
        return {"classification": state, "action": "NONE", "performed": False,
                "reason": "terminal or typed stop; no dispatch action",
                "side_effects": 0}

    # -- evidence capability probe (read-only) ------------------------------
    def probe_evidence_capability(self, issue_id: str) -> dict:
        """Read-only assessment of deployed CLI evidence coverage.

        Distinguishes fields actually present (live shape) from what the
        publication predicate requires. Missing coverage is returned as a
        typed gap; it is never smoothed over.
        """
        gaps: list = []
        present: dict = {}
        try:
            issue = self.reader.issue_get(issue_id)
            present["issue_get"] = {
                "keys": sorted(issue),
                "revision_present": isinstance(issue.get("revision"), int),
                "assignee_fields": sorted(k for k in issue
                                          if k.startswith("assignee")),
            }
        except R0BError as exc:
            gaps.append({"field": "issue get", "need": str(exc)})
        try:
            comments = self.reader.comments_full(issue_id)
            agent_rows = [c for c in comments
                          if c.get("author_type") == "agent"]
            present["comments_full"] = {
                "count": len(comments),
                "fields": sorted({k for c in comments for k in c}),
                "agent_rows": len(agent_rows),
                "agent_rows_with_source_task_id": sum(
                    1 for c in agent_rows if c.get("source_task_id")),
                "has_comment_revision": all("revision" in c for c in comments),
            }
            if agent_rows and not all(c.get("source_task_id")
                                      for c in agent_rows):
                gaps.append({
                    "field": "comment.source_task_id",
                    "need": "source run binding on the published note",
                })
        except R0BError as exc:
            gaps.append({"field": "comment list", "need": str(exc)})
        try:
            activities = self.reader.timeline_activities(issue_id)
            present["timeline_activities"] = {
                "count": len(activities),
                "fields": sorted({k for a in activities for k in a}),
                "actions": sorted({a.get("action") for a in activities}),
                "revision_linkage": False,
            }
            gaps.append({
                "field": "timeline.revision_linkage",
                "need": "platform revision-to-event linkage; the documented "
                        "CLI timeline exposes actor/action/timestamp with no "
                        "issue-revision chain",
                "consequence": "revision attribution is delta-exclusion "
                               "based, not platform-causal; an invisible "
                               "concurrent write cannot be excluded by the "
                               "adapter lease",
            })
        except R0BError as exc:
            gaps.append({"field": "timeline", "need": str(exc)})
        try:
            runs = self.reader.runs_full(issue_id)
            present["runs_full"] = {
                "count": len(runs),
                "fields": sorted({k for r in runs for k in r}),
            }
        except R0BError as exc:
            gaps.append({"field": "runs", "need": str(exc)})
        blocking = [g for g in gaps
                    if g.get("field") != "timeline.revision_linkage"]
        return {
            "ok": not blocking,
            "present": present,
            "gaps": gaps,
            "verdict": ("LIVE_EVIDENCE_COVERAGE_PARTIAL" if gaps
                        else "LIVE_EVIDENCE_COVERAGE_COMPLETE"),
            "note": "fixture-positive proof is separate from live coverage; "
                    "the Lead keeps the bounded canary under single-dispatch "
                    "ownership and decides live R0",
        }

    # -- package construction (frozen pipeline) -----------------------------
    def build_context_package(self, request: dict, *, clock=None,
                              findings=None) -> dict:
        """Build one real READY package through T01->T02->T03 and SELF_CHECK."""
        plan_env = plan.prepare_handoff_plan(
            request, findings=[] if findings is None else findings)
        if plan_env.get("status") != "PLAN_READY":
            raise R0BValidationRefused(
                "PLAN did not reach PLAN_READY", status=plan_env.get("status"))
        import chandoff_compose as compose
        proposed = compose.subset_result(plan_env["plan"])
        compose_env = compose.compose_semantic(plan_env, proposed)
        if compose_env.get("status") != "ACCEPTED":
            raise R0BValidationRefused(
                "COMPOSE did not accept the deterministic subset",
                errors=compose_env.get("errors"))
        result = finalize.finalize_handoff(plan_env, compose_env, request,
                                           clock=clock or self.now)
        if result.get("status") != "READY":
            raise R0BValidationRefused(
                "FINALIZE did not produce READY",
                status=result.get("status"), errors=result.get("errors"))
        sc_request = self_check_request_from_prepare(request)
        check = selfcheck.self_check(sc_request, packages=[result],
                                     findings=[])
        if check.get("status") != "READY":
            raise R0BValidationRefused("SELF_CHECK is not READY", check=check)
        return {"result": result, "request": request, "self_check": check}


def binding_issue_id(data: dict) -> str:
    target = (data or {}).get("target_binding") or {}
    issue_id = target.get("issue_id")
    if not issue_id:
        raise R0BValidationRefused("R0B intent has no bound target issue")
    return issue_id


def build_r0b_factory(store, *, runner, note_runner=None,
                      executable: str = "multica",
                      **kwargs) -> R0BForwardFactory:
    """The single factory entrypoint. No implicit live runner exists."""
    if runner is None:
        raise o2.NotAuthorizedError(
            "the R0B factory requires an explicitly injected runner")
    return R0BForwardFactory(store, runner=runner, note_runner=note_runner,
                             executable=executable, **kwargs)


# ---------------------------------------------------------------------------
# static wiring proof
# ---------------------------------------------------------------------------
def _call_sites(node) -> list:
    out = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Attribute):
            out.append((func.attr, sub.lineno))
        elif isinstance(func, ast.Name):
            out.append((func.id, sub.lineno))
    return out


def _method(cls, name):
    if cls is None:
        return None
    for item in cls.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
            return item
    return None


def _base_names(cls) -> set:
    names = set()
    for base in getattr(cls, "bases", []) or []:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _calls_name(node, name: str) -> bool:
    return bool(node) and any(call == name for call, _ in _call_sites(node))


def _calls_attr(node, attr: str) -> bool:
    return bool(node) and any(call == attr for call, _ in _call_sites(node))


def _refuses_transition(node, exception_name: str) -> bool:
    if node is None or _calls_attr(node, "transition"):
        return False
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Raise) or sub.exc is None:
            continue
        exc = sub.exc
        if isinstance(exc, ast.Call):
            exc = exc.func
        if isinstance(exc, ast.Name) and exc.id == exception_name:
            return True
    return False


def _factory_sets_r0b_boundary(factory) -> bool:
    init = _method(factory, "__init__")
    if init is None:
        return False
    for sub in ast.walk(init):
        if not isinstance(sub, ast.Assign):
            continue
        for target in sub.targets:
            if (isinstance(target, ast.Attribute)
                    and getattr(target.value, "id", None) == "self"
                    and target.attr == "boundary"
                    and isinstance(sub.value, ast.Call)
                    and getattr(sub.value.func, "id", None) == "R0BBoundary"):
                return True
    return False


def _create_checks_backlog(text: str) -> bool:
    return ('R0B_CREATE_STATUS_VALUE = "backlog"' in text
            and "R0B_CREATE_STATUS" in text
            and "arg" in text)


def _unparse(node) -> str:
    return ast.unparse(node) if node is not None else ""


def _recovery_is_read_only(factory) -> bool:
    nodes = [_method(factory, name) for name in (
        "recover_created_target", "_execute_create_recovery",
        "_replay_committed_recovery", "_recovery_prerequisites",
        "_discover_recovery_candidates", "_build_recovery_proof",
        "_recovery_refusal", "_require_predecessor_recovery_state",
        "_classify_shared_history", "_classify_record", "_read_ledger_lines",
        "_resolve_receipt_evidence", "_resolve_execution_identity",
        "audit_recovery_ledger")]
    if any(node is None for node in nodes):
        return False
    for node in nodes:
        for call, _line in _call_sites(node):
            if call in RECOVERY_FORBIDDEN_METHODS:
                return False
    return True


def _recovery_transitions(factory) -> bool:
    node = _method(factory, "_execute_create_recovery")
    if node is None:
        return False
    transitions = [sub for sub in ast.walk(node)
                   if isinstance(sub, ast.Call)
                   and getattr(sub.func, "attr", None) == "transition"]
    if len(transitions) != 1:
        return False
    states = set(re.findall(r"o2\.S_[A-Z_]+", ast.unparse(transitions[0])))
    return states == {"o2.S_TARGET_BOUND"}


def wiring_proof(source=None, module_path=None) -> dict:
    """Static proof of factory-only execution and strict-only triggering."""
    path = Path(module_path) if module_path else Path(__file__).resolve()
    text = source if source is not None else path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    classes, functions = {}, {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = node
        elif isinstance(node, ast.FunctionDef):
            functions[node.name] = node
    factory = classes.get("R0BForwardFactory")
    boundary = classes.get("R0BBoundary")
    module_parse_calls = [(n, line) for node in tree.body
                          for n, line in _call_sites(node)
                          if n == "parse_run_object"]
    preflight_fn = functions.get("preflight_self_check")
    findings_kwarg_is_live = bool(preflight_fn) and any(
        isinstance(sub, ast.Call)
        and getattr(sub.func, "attr", None) == "self_check"
        and any(kw.arg == "findings"
                and not isinstance(kw.value, ast.List)
                for kw in sub.keywords)
        for sub in ast.walk(preflight_fn))
    checks = {
        "factory_present": factory is not None,
        "boundary_present": boundary is not None,
        "factory_subclasses_canary": (
            factory is not None
            and "CanaryOrchestrator" in _base_names(factory)),
        "boundary_subclasses_strict": (
            boundary is not None
            and "StrictReceiptBoundary" in _base_names(boundary)),
        "factory_replaces_boundary_with_r0b":
            _factory_sets_r0b_boundary(factory),
        "plain_prepare_refused": _refuses_transition(
            _method(factory, "mark_prepared"), "R0BDowngradeRefused"),
        "plain_publish_refused": _refuses_transition(
            _method(factory, "mark_published"), "R0BDowngradeRefused"),
        "snapshot_arming_refused": _refuses_transition(
            _method(factory, "plan_and_arm"), "R0BDowngradeRefused"),
        "snapshot_trigger_refused": _refuses_transition(
            _method(factory, "issue_trigger"), "R0BDowngradeRefused"),
        "preflight_present": classes.get("PreflightRefusal") is not None and (
            functions.get("preflight_request_check") is not None
            and functions.get("preflight_self_check") is not None
            and functions.get("preflight_note_check") is not None
            and functions.get("preflight_issue_check") is not None
            and functions.get("validate_authority_evidence") is not None),
        "arm_runs_preflight": _calls_attr(
            _method(factory, "arm"), "_preflight_materials"),
        "trigger_runs_preflight": _calls_attr(
            _method(factory, "trigger"), "_preflight_materials"),
        "preflight_observes_artifact": _calls_name(
            _method(factory, "_preflight_materials"),
            "compare_artifact_dependency"),
        "preflight_observes_authority": _calls_name(
            _method(factory, "_preflight_materials"),
            "validate_authority_evidence"),
        "preflight_observes_note": _calls_name(
            _method(factory, "_preflight_materials"),
            "preflight_note_check"),
        "preflight_findings_not_forced_empty": findings_kwarg_is_live,
        "preflight_diagnostic_event": (
            "E_PREFLIGHT" in text and "r0b_preflight" in text),
        "authority_reader_binding_present": (
            "ReadinessManifestAuthorityReader" in text
            and "AUTHORITY_ARTIFACT_PATH" in text),
        "material_stale_is_refresh": (
            'REASON_MATERIAL_STALE = "R0B_MATERIAL_STALE"' in text
            and 'REASON_MATERIAL_UNAVAILABLE = "R0B_MATERIAL_UNAVAILABLE"'
            in text),
        "arm_delegates_to_super": _calls_attr(
            _method(factory, "arm"), "plan_and_arm"),
        "trigger_delegates_to_super": _calls_attr(
            _method(factory, "trigger"), "issue_trigger"),
        "rerun_is_strict_gate": (
            boundary is not None
            and _method(boundary, "rerun_issue") is None),
        "no_permissive_parser_calls": not module_parse_calls,
        "create_requires_literal_backlog": _create_checks_backlog(text),
        "contract_version_pinned":
            f'CONTRACT_VERSION = "{CONTRACT_VERSION}"' in text,
        "adapter_pin_enforced": ("adapter_digest()" in text
                                 and "R0BDowngradeRefused" in text),
        "single_factory_entrypoint":
            functions.get("build_r0b_factory") is not None,
        "publication_attempt_before_call": text.index(
            "E_PUBLICATION_ISSUING, actor=actor") < text.index(
            "note.publish_handoff("),
        "transport_profile_present": (
            f'TRANSPORT_PROFILE = "{TRANSPORT_PROFILE}"' in text
            and functions.get("prepare_transport_body") is not None
            and functions.get("single_terminal_lf_relation") is not None),
        "prospective_preparation_before_spec": (
            "source_body" in text and "transport_preparation" in text
            and "transport_preparation" in _unparse(
                functions.get("validate_creation_spec"))),
        "recovery_operation_present": (
            _method(factory, "recover_created_target") is not None
            and _method(factory, "_execute_create_recovery") is not None),
        "recovery_has_no_native_operations": _recovery_is_read_only(factory),
        "recovery_uses_only_target_bound_edge": _recovery_transitions(
            factory),
        "predecessor_fence_present": (
            "PREDECESSOR_CONTRACT_VERSION" in text
            and "PREDECESSOR_ADAPTER_DIGEST" in text
            and "R0BCompatibilityRefused" in text),
        "execution_binding_validated": (
            functions.get("_validate_execution_binding") is not None
            and _calls_name(_method(factory, "_load"),
                            "validate_intent_record")),
        "recovery_decision_validated": (
            functions.get("validate_recovery_decision") is not None
            and _calls_name(_method(factory, "recover_created_target"),
                            "validate_recovery_decision")),
        "old_listing_kept_unstaged_discovery": (
            _method(classes.get("EvidenceReader"), "issue_children") is not None
            and "issue_children" in text),
        "shared_history_classification_present": (
            functions.get("shared_history_digest") is not None
            and _method(factory, "_classify_shared_history") is not None
            and _calls_attr(_method(factory, "_recovery_prerequisites"),
                            "_classify_shared_history")
            and _calls_attr(_method(factory, "audit_recovery_ledger"),
                            "_classify_shared_history")),
        "tail_revalidated_before_binding": (
            _calls_attr(_method(factory, "_execute_create_recovery"),
                        "_classify_shared_history")
            and "tail_revalidation" in text),
        "receipt_limit_path_present": (
            f'RECEIPT_STATUS_NOT_PERSISTED = "{RECEIPT_STATUS_NOT_PERSISTED}"'
            in text
            and RECEIPT_LIMIT_SCOPE in text
            and _method(factory, "_resolve_receipt_evidence") is not None),
        "execution_identity_resolved": (
            _method(factory, "_resolve_execution_identity") is not None
            and "blob_resolution" in text),
        "evidence_versions_pinned": (
            f'RECOVERY_PROOF_SCHEMA = "{RECOVERY_PROOF_SCHEMA}"' in text
            and f'RECOVERY_DECISION_SCHEMA = "{RECOVERY_DECISION_SCHEMA}"'
            in text
            and f'EXECUTION_BINDING_SCHEMA = "{EXECUTION_BINDING_SCHEMA}"'
            in text),
        "inline_observations_present": (
            "observations" in _unparse(_method(factory,
                                               "_build_recovery_proof"))
            and "raw_responses" in text),
        "evidence_correction_ref_pinned": (
            "01a08fbb-51cd-7aeb-907f-a547e4e066e9" in text
            and "sha256:60f265a8a446b5329b534bbb183de12d306fc0ea38f1da767ff9"
                "bf998ebd4334" in text),
    }
    checks["ok"] = all(bool(v) for v in checks.values())
    return {"module": ADAPTER_MODULE, "checks": checks, "ok": checks["ok"]}


def contract_proof() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "adapter_digest": adapter_digest(),
        "creation_role": CREATION_ROLE,
        "execution_role": EXECUTION_ROLE,
        "backlog_status": BACKLOG_STATUS,
        "canary_agent_id": CANARY_AGENT_ID,
        "receipt_entrypoint": strict.RECEIPT_ENTRYPOINT,
        "permissive_entrypoint": strict.PERMISSIVE_ENTRYPOINT,
        "permissive_entrypoint_reachable_in_r0_path": False,
        "single_publication_no_retry": True,
        "no_second_ledger": True,
        "no_second_store": True,
        "assignment_trigger_available": False,
        "mention_trigger_available": False,
        "status_trigger_available": False,
        "preflight_entrypoints": ["arm", "trigger"],
        "authority_evidence_schema": AUTHORITY_EVIDENCE_SCHEMA,
        "authority_artifact_path": AUTHORITY_ARTIFACT_PATH,
        "authority_ref": AUTHORITY_REF,
        "snapshot_taking_entrypoints_refused": True,
        "predecessor_contract_version": PREDECESSOR_CONTRACT_VERSION,
        "predecessor_adapter_digest": PREDECESSOR_ADAPTER_DIGEST,
        "predecessor_adapter_commit": PREDECESSOR_ADAPTER_COMMIT,
        "transport_profiles": list(TRANSPORT_PROFILES),
        "recovery_decision_schema": RECOVERY_DECISION_SCHEMA,
        "recovery_proof_schema": RECOVERY_PROOF_SCHEMA,
        "execution_binding_schema": EXECUTION_BINDING_SCHEMA,
        "evidence_decision_ref": EVIDENCE_DECISION_REF,
        "evidence_decision_digest": EVIDENCE_DECISION_DIGEST,
        "receipt_statuses": [RECEIPT_STATUS_PERSISTED,
                             RECEIPT_STATUS_NOT_PERSISTED],
        "receipt_limit_scope": RECEIPT_LIMIT_SCOPE,
        "execution_resolvers": list(EXECUTION_RESOLVERS),
        "shared_history_classifications": [
            CLS_INTENT_HISTORY, CLS_ORIGINAL_CREATE_COMMAND,
            CLS_ORIGINAL_CREATE_RESULT, CLS_READ_COMMAND, CLS_READ_RESULT],
        "recovery_operation": "recover_created_target",
        "recovery_native_capability": "none (reads and ledger writes only)",
        "recovery_transition": "CREATE_AMBIGUOUS -> TARGET_BOUND",
        "recovery_execution_fence": "U12-R0B/1.1 fenced execution binding",
        "recovery_evidence_events": [E_RECOVERY_EVIDENCE, E_RECOVERY_BOUND],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_artifact_digest(args) -> int:
    entries = _load_json(args.entries_file) if args.entries_file else None
    if isinstance(entries, dict):
        entries = [dict(v, path=k) for k, v in entries.items()]
    result = build_artifact_dependency_digest(entries, root=Path(args.root))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_probe(args) -> int:
    store = o2.DurableIntentStore(
        args.ledger or str(Path.cwd() / "unused-r0b-probe-ledger.jsonl"))
    factory = build_r0b_factory(store, runner=_probe_runner,
                                executable=args.executable)
    result = factory.probe_evidence_capability(args.issue)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_validate(args) -> int:
    store = o2.DurableIntentStore(args.ledger)
    factory = build_r0b_factory(store, runner=_probe_runner,
                                executable=args.executable)
    try:
        result = factory.validate(args.intent_id)
    except o2.IntentError as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", None),
                          "message": exc.message,
                          "details": getattr(exc, "details", {})},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_self_check(args) -> int:
    result = {"wiring_proof": wiring_proof(),
              "contract_proof": contract_proof()}
    result["ok"] = bool(result["wiring_proof"]["ok"])
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_authority_evidence(args) -> int:
    reader = ReadinessManifestAuthorityReader(args.root)
    try:
        evidence = reader.read()
    except PreflightRefusal as exc:
        print(json.dumps({"ok": False, "state": exc.state,
                          "reason": exc.reason, "detail": exc.detail},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_recover_created_target(args) -> int:
    """Operator entrypoint: read-only runner + the one forward recovery edge.

    The runner executes documentation-defined read commands only; the adapter
    contains no create/assign/comment/rerun call on the recovery path. The
    full exact accepted execution commit and the Lead disposition are
    mandatory.
    """
    store = o2.DurableIntentStore(args.ledger)
    factory = build_r0b_factory(
        store, runner=_probe_runner, executable=args.executable,
        artifact_root=args.artifact_root or str(ROOT),
        authority_reader=ReadinessManifestAuthorityReader(
            args.authority_root or args.artifact_root or str(ROOT)))
    decision = _load_json(args.decision_file)
    receipt = _load_json(args.receipt_file) if args.receipt_file else None
    try:
        result = factory.recover_created_target(
            args.intent_id, expected_target_id=args.target,
            recovery_decision=decision, actor=args.actor,
            execution_commit=args.execution_commit,
            original_receipt=receipt)
    except o2.IntentError as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", None),
                          "message": exc.message,
                          "details": getattr(exc, "details", {})},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    result = dict(result)
    result["ok"] = result.get("status") == o2.S_TARGET_BOUND and \
        result.get("outcome") != "RECOVERY_REFUSED"
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_ledger_audit(args) -> int:
    """Read-only ledger audit for the disposition prefix/record pins."""
    store = o2.DurableIntentStore(args.ledger)
    factory = build_r0b_factory(store, runner=_probe_runner,
                                executable=args.executable)
    try:
        result = factory.audit_recovery_ledger(args.intent_id)
    except PreflightRefusal as exc:
        print(json.dumps({"ok": False, "state": exc.state,
                          "reason": exc.reason, "detail": exc.detail,
                          "subjects": exc.subjects},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    except o2.IntentError as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", None),
                          "message": exc.message,
                          "details": getattr(exc, "details", {})},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_prepare_transport(args) -> int:
    source = Path(args.source_file).read_text(encoding="utf-8")
    try:
        prepared = prepare_transport_body(source, profile=args.profile)
    except R0BError as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", None),
                          "message": exc.message,
                          "details": getattr(exc, "details", {})},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    out = {k: v for k, v in prepared.items() if k != "transport_body"}
    out["ok"] = True
    if args.out:
        Path(args.out).write_text(prepared["transport_body"], encoding="utf-8",
                                  newline="")
        out["written"] = str(Path(args.out))
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_transport_relation(args) -> int:
    source = Path(args.source_file).read_text(encoding="utf-8")
    observed = Path(args.observed_file).read_text(encoding="utf-8")
    result = single_terminal_lf_relation(source, observed)
    result["ok"] = bool(result["accepted"])
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def cmd_recovery_decision_digest(args) -> int:
    decision = _load_json(args.decision_file)
    if not isinstance(decision, dict):
        print(json.dumps({"ok": False,
                          "message": "recovery decision is not an object"},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    body = {k: v for k, v in decision.items() if k != "decision_digest"}
    recomputed = digest(body)
    result = {"ok": True, "decision_digest": recomputed}
    if args.check:
        found = decision.get("decision_digest")
        result["found"] = found
        result["ok"] = found == recomputed
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def _probe_runner(argv: list) -> tuple:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
    except OSError as exc:
        raise o2.IntentError(f"multica CLI unavailable: {exc}") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="U12-R0B forward lifecycle binding adapter (read-only "
                    "operator entrypoints; no implicit live writes)")
    sub = parser.add_subparsers(dest="command", required=True)

    dig = sub.add_parser("artifact-digest",
                         help="compute the pinned artifact dependency digest")
    dig.add_argument("--entries-file", default=None,
                     help="optional JSON map path -> {commit,digest_method}")
    dig.add_argument("--root", default=str(ROOT))
    dig.set_defaults(func=cmd_artifact_digest)

    probe = sub.add_parser("probe-evidence",
                           help="read-only deployed CLI evidence capability "
                                "probe for one issue")
    probe.add_argument("--issue", required=True)
    probe.add_argument("--ledger", default=None)
    probe.add_argument("--executable", default="multica")
    probe.set_defaults(func=cmd_probe)

    val = sub.add_parser("validate-intent",
                         help="read-only validation of one R0B intent")
    val.add_argument("--ledger", required=True)
    val.add_argument("--intent-id", required=True)
    val.add_argument("--executable", default="multica")
    val.set_defaults(func=cmd_validate)

    chk = sub.add_parser("self-check",
                         help="static wiring/contract proof for this adapter")
    chk.set_defaults(func=cmd_self_check)

    auth = sub.add_parser("authority-evidence",
                          help="read the existing accepted readiness manifest "
                               "and print one current authority evidence input")
    auth.add_argument("--root", default=str(ROOT),
                      help="repository root holding the bound authority path")
    auth.set_defaults(func=cmd_authority_evidence)

    rec = sub.add_parser("recover-created-target",
                         help="one narrow forward recovery of the sole "
                              "already-created target (CREATE_AMBIGUOUS -> "
                              "TARGET_BOUND); read-only runner commands and "
                              "the namespaced ledger edge only")
    rec.add_argument("--ledger", required=True)
    rec.add_argument("--intent-id", required=True)
    rec.add_argument("--target", required=True)
    rec.add_argument("--decision-file", required=True)
    rec.add_argument("--receipt-file", default=None,
                     help="optional persisted original create receipt body; "
                          "omit only under the exact bounded receipt-limit "
                          "disposition")
    rec.add_argument("--actor", required=True)
    rec.add_argument("--artifact-root", default=None)
    rec.add_argument("--authority-root", default=None)
    rec.add_argument("--execution-commit", required=True,
                     help="full exact accepted execution commit (40 hex) "
                          "named by the disposition")
    rec.add_argument("--executable", default="multica")
    rec.set_defaults(func=cmd_recover_created_target)

    audit = sub.add_parser("ledger-audit",
                           help="read-only full-interval classification of "
                                "the shared ledger plus the exact disposition "
                                "prefix/pair pins the Lead must bind")
    audit.add_argument("--ledger", required=True)
    audit.add_argument("--intent-id", required=True)
    audit.add_argument("--executable", default="multica")
    audit.set_defaults(func=cmd_ledger_audit)

    prep = sub.add_parser("prepare-transport",
                          help="prepare a source body for prospective "
                               "creation under the accepted transport profile")
    prep.add_argument("--source-file", required=True)
    prep.add_argument("--profile", default=TRANSPORT_PROFILE)
    prep.add_argument("--out", default=None,
                      help="optional path for the prepared transport body")
    prep.set_defaults(func=cmd_prepare_transport)

    rel = sub.add_parser("transport-relation",
                         help="read-only classification of one source/observed "
                              "pair under the accepted transport relation")
    rel.add_argument("--source-file", required=True)
    rel.add_argument("--observed-file", required=True)
    rel.set_defaults(func=cmd_transport_relation)

    dec = sub.add_parser("recovery-decision-digest",
                         help="print (and optionally check) the canonical "
                              "self-digest of a recovery disposition")
    dec.add_argument("--decision-file", required=True)
    dec.add_argument("--check", action="store_true")
    dec.set_defaults(func=cmd_recovery_decision_digest)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
