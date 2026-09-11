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
import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
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
E_PUBLICATION_RECOVERY_EVIDENCE = "r0b_publication_recovery_evidence"

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

# --- publication transport repair + proof-bearing O2 recovery exception ------
# YZT-84 approved exception (Human approval comment
# 01a09003-f830-79ac-b022-c7aaf2cf4039; Lead boundary
# 01a08ffb-fb18-7135-bc19-c17297426127; authoritative contract
# U12_R0_PUBLICATION_TRANSPORT_DECISION.md, raw SHA256 0df4cec9...). Only the
# proof-bearing recovery commit writer/reducer and the execution-identity
# migration are opened; ordinary BLOCKED transitions, the trigger matrix, the
# strict receipt gate, Core and frozen T00 remain unchanged.
PUBLICATION_TRANSPORT_PROFILE = "publication-single-terminal-lf-v1"
PUBLICATION_TRANSPORT_PROFILES = (PUBLICATION_TRANSPORT_PROFILE,)
PUBLICATION_TRANSPORT_RENDERER_TAIL = "\n```\n"
PUBLICATION_RELATION_DIRECTIONAL = "publication-single-terminal-lf-removed"
PUBLICATION_RECOVERY_OP = "publication_recovery_commit_v1"
# v1.1 adds the resolved source-activation/authority-content verification and
# the shared pure semantic proof reconstruction. The frozen v1.0 payloads are
# preserved for inspection but are never accepted as recovery authority.
PUBLICATION_RECOVERY_DECISION_SCHEMA = \
    "u12-r0b-publication-recovery-decision/1.1"
PUBLICATION_RECOVERY_PROOF_SCHEMA = "u12-r0b-publication-recovery-proof/1.1"
PUBLICATION_MIGRATION_SCHEMA = \
    "u12-r0b-publication-execution-migration/1.1"
PUBLICATION_RECOVERY_DECISION_SCHEMA_V1_0 = \
    "u12-r0b-publication-recovery-decision/1.0"
PUBLICATION_RECOVERY_PROOF_SCHEMA_V1_0 = \
    "u12-r0b-publication-recovery-proof/1.0"
PUBLICATION_MIGRATION_SCHEMA_V1_0 = \
    "u12-r0b-publication-execution-migration/1.0"
PUBLICATION_RECOVERY_DISPOSITION = "RECOVER_BLOCKED_PUBLICATION"
PUBLICATION_RECOVERY_SCOPE = "PUBLICATION_TRANSPORT_REPAIR"
PUBLICATION_RECOVERY_PURPOSE = "SINGLE_PUBLICATION_RECOVERY_COMMIT"
PUBLICATION_TRIGGER_POLICY = "NO_TRIGGER_ARM_OR_CREATE"
PUBLICATION_TRANSPORT_DESIGN_REF = \
    "attachment/01a08ffa-604f-70c3-a03e-0d166505fa3d"
PUBLICATION_TRANSPORT_DESIGN_DIGEST = (
    "sha256:0df4cec9a7cfb26a2b9146806280a2dcae4924f6cdb29190560ab17db32c8997")
LEAD_AGENT_ID = "24f04aba-7da9-4371-bf89-685d7505a411"
HUMAN_APPROVER_ID = "1338bca6-ea41-4886-83bb-3375322a3049"
PUBLICATION_RECOVERY_HUMAN_APPROVAL_COMMENT_ID = \
    "01a09003-f830-79ac-b022-c7aaf2cf4039"
PUBLICATION_RECOVERY_HUMAN_APPROVAL_AUTHOR_ID = HUMAN_APPROVER_ID
PUBLICATION_RECOVERY_HUMAN_APPROVAL_CONTENT_DIGEST = (
    "sha256:2c47b6069230fdb2e1db7d60a58ed3738e5348a6c88d1b979f88e4b4031d9315")
PUBLICATION_RECOVERY_LEAD_APPROVAL_COMMENT_ID = \
    "01a08ffb-fb18-7135-bc19-c17297426127"
PUBLICATION_RECOVERY_LEAD_APPROVAL_AUTHOR_ID = LEAD_AGENT_ID
PUBLICATION_RECOVERY_LEAD_APPROVAL_CONTENT_DIGEST = (
    "sha256:4700cf98d489c044765381f8ba1a1ac6b9110e763212b7c27d4e0a7ac37e486d")
PUBLICATION_SOURCE_ACTIVATION_COMMENT_ID = \
    "01a08ff4-83c5-7b0c-a075-ec60184a843a"
PUBLICATION_SOURCE_ACTIVATION_AUTHOR_ID = LEAD_AGENT_ID
PUBLICATION_RECOVERY_HUMAN_APPROVAL_REF = \
    "multica://comment/" + PUBLICATION_RECOVERY_HUMAN_APPROVAL_COMMENT_ID
PUBLICATION_RECOVERY_LEAD_APPROVAL_REF = \
    "multica://comment/" + PUBLICATION_RECOVERY_LEAD_APPROVAL_COMMENT_ID
PUBLICATION_SOURCE_ACTIVATION_REF = \
    "multica://comment/" + PUBLICATION_SOURCE_ACTIVATION_COMMENT_ID
PREDECESSOR_FORWARD_COMMIT = "da99c112093ea576a449a1f3c8ce355652805a40"
PREDECESSOR_FORWARD_ADAPTER_DIGEST = (
    "sha256:40ccf07dd088d6a4077213714deef45c83f46116478ccc462c203ef7f07641fa")
PUBLICATION_MIGRATION_COMMIT_REVISION = 5
PUBLICATION_BLOCKER_REASON = REASON_PUBLICATION_PROVENANCE
PUBLICATION_BLOCKER_CODE = PUB_NOTE_NOT_FOUND
BODY_DIGEST_METHOD_LF = "digest_text_lf"
BODY_DIGEST_METHOD_RAW = "raw_utf8"
PUBLICATION_BINDING_SCHEMA = "u12-r0b-publication-binding/1.1"

# Publication-history classification vocabulary (shared audit form).
CLS_PUBLICATION_COMMAND = "uniquely-correlated-publication-command"
CLS_PUBLICATION_RESULT = "uniquely-correlated-publication-result"
CLS_OWNERSHIP_COMMAND = "uniquely-correlated-ownership-command"
CLS_OWNERSHIP_RESULT = "uniquely-correlated-ownership-result"


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


def prepare_publication_transport_body(rendered_body, *,
                                       profile: str =
                                       PUBLICATION_TRANSPORT_PROFILE) -> dict:
    """Prepare the versioned publication transport body (future sends).

    The accepted publication profile removes exactly one terminal U+000A from
    the renderer output and sends the remaining characters verbatim. The
    renderer's ending must be the exact accepted `\\n```\\n` shape -- no
    strip/rstrip, no line-ending normalization, no BOM handling and no variant
    guessing. The raw identities of the rendered body (R) and the transport
    body (T) are recorded separately.
    """
    if profile not in PUBLICATION_TRANSPORT_PROFILES:
        raise R0BValidationRefused(
            "publication transport profile is not accepted", profile=profile,
            accepted=list(PUBLICATION_TRANSPORT_PROFILES))
    if not isinstance(rendered_body, str) or not rendered_body:
        raise R0BValidationRefused(
            "rendered_body must be a non-empty string", profile=profile)
    if rendered_body.startswith("\ufeff"):
        raise R0BValidationRefused(
            "rendered_body carries a UTF-8 BOM; the publication transport "
            "never normalizes framing bytes", profile=profile)
    if rendered_body.endswith("\r\n") or rendered_body.endswith("\n\n"):
        raise R0BValidationRefused(
            "rendered_body carries a CRLF or repeated terminal LF; the "
            "accepted renderer shape is exactly LF-fence-LF", profile=profile)
    if not rendered_body.endswith(PUBLICATION_TRANSPORT_RENDERER_TAIL):
        raise R0BValidationRefused(
            "rendered_body does not end with the accepted LF-fence-LF "
            "renderer shape; no variant is guessed", profile=profile)
    transport = rendered_body[:-1]
    if transport and transport[-1] in " \t\r\f\v":
        raise R0BValidationRefused(
            "rendered_body carries trailing whitespace before the final LF; "
            "no trimming is applied", profile=profile)
    return {
        "profile": profile,
        "relation": PUBLICATION_RELATION_DIRECTIONAL,
        "transformation": "remove-single-terminal-lf",
        "rendered_body": rendered_body,
        "transport_body": transport,
        "rendered_body_digest_raw": _sha256_utf8(rendered_body),
        "rendered_body_digest_lf": digest_text_lf(rendered_body),
        "transport_body_digest_raw": _sha256_utf8(transport),
        "transport_body_digest_lf": digest_text_lf(transport),
        "rendered_chars": len(rendered_body),
        "rendered_utf8_bytes": len(rendered_body.encode("utf-8")),
        "transport_chars": len(transport),
        "transport_utf8_bytes": len(transport.encode("utf-8")),
        "observed_digest_method": BODY_DIGEST_METHOD_RAW,
    }


def publication_transport_relation(transport, observed) -> dict:
    """The future publication acceptance relation: O == T, exactly.

    Only byte-exact equality of the prepared transport text and the observed
    readback is accepted. There is no silent normalization fallback: leading
    or interior whitespace changes, CRLF rewrites, BOMs, trailing spaces,
    doubled terminal LFs and Unicode changes are all refusals.
    """
    if not isinstance(transport, str) or not isinstance(observed, str):
        return {"accepted": False, "relation": None,
                "reason": REASON_TRANSPORT_UNSUPPORTED,
                "detail": "transport and observed must both be strings"}
    if observed == transport:
        return {"accepted": True, "relation": "exact-transport",
                "removed_lf": False, "reason": None, "detail": ""}
    return {"accepted": False, "relation": None, "removed_lf": False,
            "reason": REASON_TRANSPORT_UNSUPPORTED,
            "detail": "observed bytes are not exactly the prepared transport "
                      "body; normalization is never applied"}


def adapter_raw_digest(path=None) -> str:
    """Exact raw sha256 of this module's bytes (no LF normalization)."""
    target = Path(path) if path else Path(__file__).resolve()
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def _file_lf_digest(path) -> str:
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _git_rev_parse(rev: str) -> str:
    proc = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT),
         "rev-parse", rev],
        capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise R0BValidationRefused(
            "git revision is not resolvable in this checkout",
            rev=rev, error=(proc.stderr or "")[:160])
    return (proc.stdout or "").strip()


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

    def attachment_content(self, attachment_id: str) -> dict:
        """Authenticated read of one attachment's exact bytes.

        Uses the documented CLI download and requires the returned local file
        to stay inside the private scratch directory, to parse as UTF-8 and to
        carry the exact declared attachment id. Returns the raw text plus its
        raw (non-normalized) digest; nothing is cached and the scratch
        directory is removed afterwards.
        """
        _require_uuid(attachment_id, "attachment id")
        holder = Path(tempfile.mkdtemp(prefix="u12-r0b-attachment-"))
        try:
            data = self._json(
                ["attachment", "download", str(attachment_id),
                 "--output-dir", str(holder)], "attachment download")
            if not isinstance(data, dict) or \
                    str(data.get("id")) != str(attachment_id):
                raise PublicationProvenanceIncomplete(
                    "attachment download did not return the requested "
                    "attachment identity", attachment_id=str(attachment_id))
            filename = data.get("filename")
            if not isinstance(filename, str) or not filename or \
                    filename != Path(filename).name:
                raise PublicationProvenanceIncomplete(
                    "attachment download returned an unsafe filename",
                    attachment_id=str(attachment_id))
            target = holder / filename
            try:
                raw = target.read_bytes()
            except OSError as exc:
                raise PublicationProvenanceIncomplete(
                    f"downloaded attachment is unreadable: {exc}",
                    attachment_id=str(attachment_id))
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PublicationProvenanceIncomplete(
                    f"attachment is not UTF-8 text: {exc}",
                    attachment_id=str(attachment_id))
            if isinstance(data.get("size"), int) and \
                    data["size"] != len(raw):
                raise PublicationProvenanceIncomplete(
                    "attachment size does not match the downloaded bytes",
                    attachment_id=str(attachment_id))
            return {
                "attachment_id": str(attachment_id),
                "filename": filename,
                "text": text,
                "raw_digest": _sha256_utf8(text),
                "chars": len(text),
                "utf8_bytes": len(raw),
            }
        finally:
            shutil.rmtree(holder, ignore_errors=True)

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


def _validate_execution_binding(intent: dict, data: dict, *,
                                executing: str | None = None,
                                allow_migration: bool = False) -> dict:
    """Validate a committed v1.1 forward-recovery binding end to end.

    This is the narrow compatibility receipt: only a record whose original
    adapter pin is the exact known predecessor, whose v1.1 proof is complete,
    recomputable and bound to its v1.1 disposition, and whose accepted
    execution identity matches these executing bytes may execute under these
    bytes. Missing, edited, copied, cross-intent or hash-only proof refuses;
    an old v1.0 proof is preserved for inspection but is never silently
    upgraded into recovery authority.

    `executing` names the digest the binding must match -- the running adapter
    bytes by default, or the explicit historical da99c11 pin on the restricted
    publication-recovery inspection path. `allow_migration` permits the
    original da99c11 execution bytes only when a committed, complete
    `publication_execution_migration` binds them to the running bytes; the
    migration is validated on every such load, never trusted.
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
    running = executing or adapter_digest()
    identity_granted_by_migration = False
    migration = data.get("publication_execution_migration")
    if migration is not None:
        validate_publication_execution_migration(intent, data, migration,
                                                 applying=False)
        identity_granted_by_migration = (
            migration.get("new_adapter_lf_digest") == running)
    if execution.get("adapter_digest") != running and \
            not identity_granted_by_migration:
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
    if authority.get("adapter_digest") != running and \
            not identity_granted_by_migration:
        raise R0BValidationRefused(
            "the committed proof execution adapter digest does not match "
            "these executing bytes", intent_id=intent["intent_id"])
    if resolution.get("ok") is not True or \
            resolution.get("commit") != accepted["commit"] or \
            (resolution.get("adapter_digest") != running
             and not identity_granted_by_migration) or \
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


def _strict_keys(obj, keys, what: str) -> dict:
    if not isinstance(obj, dict):
        raise R0BValidationRefused(f"{what} must be an object")
    extras = sorted(set(obj) - set(keys))
    missing = sorted(set(keys) - set(obj))
    if extras or missing:
        raise R0BValidationRefused(
            f"{what} must stay within the exact schema",
            extras=extras, missing=missing)
    return obj


def _require_full_commit(value, field: str) -> str:
    if not isinstance(value, str) or not re.match(r"^[0-9a-f]{40}$", value):
        raise R0BValidationRefused(
            f"{field} must be a full 40-hex git object id",
            value=str(value)[:48])
    return value


def validate_publication_recovery_decision(decision: dict) -> dict:
    """Validate one immutable publication-recovery disposition (v1.0).

    The Lead fixes the exact incident objects: the BLOCKED revision and its
    blocking transition/evidence records, the sole publication attempt, the
    exact expected note and the raw R/O digests, the completed create-recovery
    proof/decision/execution-binding references, the accepted source
    activation, the new execution-migration identities and the single-purpose
    no-trigger scope. Every field is a commitment revalidated by the operation;
    an unchecked boolean or a caller-supplied hash is never accepted.
    """
    fields = (
        "schema", "decision_id", "disposition", "scope", "intent_id",
        "expected_target_id", "expected_intent_revision", "expected_blocker",
        "publication_attempt", "expected_note", "original_create_recovery",
        "source_activation", "execution_migration", "purpose",
        "trigger_policy", "design_ref", "design_digest", "human_approval_ref",
        "lead_approval_ref", "approval_ref", "approved_by", "approved_at",
        "decision_digest")
    decision = _strict_keys(_require_dict(decision, "publication_recovery_"
                                                     "decision"),
                            fields, "publication recovery decision")
    if decision["schema"] != PUBLICATION_RECOVERY_DECISION_SCHEMA:
        raise R0BValidationRefused(
            "publication recovery decision schema is unsupported; an old or "
            "hash-only disposition is never accepted",
            schema=decision["schema"])
    _require_text(decision["decision_id"], "decision.decision_id")
    if decision["disposition"] != PUBLICATION_RECOVERY_DISPOSITION:
        raise R0BValidationRefused(
            "publication recovery disposition is unsupported",
            disposition=decision["disposition"])
    if decision["scope"] != PUBLICATION_RECOVERY_SCOPE:
        raise R0BValidationRefused(
            "publication recovery scope is unsupported",
            scope=decision["scope"])
    if not isinstance(decision["intent_id"], str) or \
            not re.match(r"^DI-[0-9a-f]{16}$", decision["intent_id"]):
        raise R0BValidationRefused(
            "publication recovery decision intent_id is not DI-<16 hex>")
    _require_uuid(decision["expected_target_id"],
                  "decision.expected_target_id")
    revision = decision["expected_intent_revision"]
    if not isinstance(revision, int) or revision < 1:
        raise R0BValidationRefused(
            "decision.expected_intent_revision must be a positive integer")
    blocker = _strict_keys(
        decision["expected_blocker"],
        ("seq", "digest", "reason", "evidence_code", "evidence_event"),
        "decision.expected_blocker")
    if not isinstance(blocker["seq"], int) or blocker["seq"] < 1:
        raise R0BValidationRefused(
            "decision.expected_blocker.seq must be a positive integer")
    _require_digest(blocker["digest"], "decision.expected_blocker.digest")
    if blocker["reason"] != PUBLICATION_BLOCKER_REASON:
        raise R0BValidationRefused(
            "decision.expected_blocker.reason is not the exact blocking reason",
            reason=blocker["reason"])
    if blocker["evidence_code"] != PUBLICATION_BLOCKER_CODE:
        raise R0BValidationRefused(
            "decision.expected_blocker.evidence_code is not the exact "
            "publication evidence code", evidence_code=blocker["evidence_code"])
    evidence_event = _strict_keys(
        blocker["evidence_event"], ("seq", "digest"),
        "decision.expected_blocker.evidence_event")
    if not isinstance(evidence_event["seq"], int) or evidence_event["seq"] < 1:
        raise R0BValidationRefused(
            "decision.expected_blocker.evidence_event.seq must be positive")
    _require_digest(evidence_event["digest"],
                    "decision.expected_blocker.evidence_event.digest")
    attempt = _strict_keys(
        decision["publication_attempt"],
        ("seq", "digest", "operation_id"),
        "decision.publication_attempt")
    if not isinstance(attempt["seq"], int) or attempt["seq"] < 1:
        raise R0BValidationRefused(
            "decision.publication_attempt.seq must be a positive integer")
    _require_digest(attempt["digest"], "decision.publication_attempt.digest")
    _require_text(attempt["operation_id"],
                  "decision.publication_attempt.operation_id")
    note = _strict_keys(
        decision["expected_note"],
        ("note_comment_id", "revision", "created_at", "updated_at",
         "author_id", "author_type", "source_task_id", "parent_id",
         "rendered_raw_digest", "observed_raw_digest"),
        "decision.expected_note")
    _require_uuid(note["note_comment_id"], "decision.expected_note."
                                           "note_comment_id")
    if note["revision"] != 1:
        raise R0BValidationRefused(
            "decision.expected_note.revision must be the original revision 1")
    _require_text(note["created_at"], "decision.expected_note.created_at")
    _require_text(note["updated_at"], "decision.expected_note.updated_at")
    if note["created_at"] != note["updated_at"]:
        raise R0BValidationRefused(
            "decision.expected_note must be an unedited original comment")
    _require_uuid(note["author_id"], "decision.expected_note.author_id")
    if note["author_type"] != "agent":
        raise R0BValidationRefused(
            "decision.expected_note.author_type must be agent",
            author_type=note["author_type"])
    _require_uuid(note["source_task_id"],
                  "decision.expected_note.source_task_id")
    if note["parent_id"] is not None:
        raise R0BValidationRefused(
            "decision.expected_note.parent_id must be null for this incident",
            parent_id=str(note["parent_id"])[:48])
    _require_digest(note["rendered_raw_digest"],
                    "decision.expected_note.rendered_raw_digest")
    _require_digest(note["observed_raw_digest"],
                    "decision.expected_note.observed_raw_digest")
    original = _strict_keys(
        decision["original_create_recovery"],
        ("decision_digest", "proof_digest", "execution_binding_digest",
         "accepted_commit", "accepted_adapter_digest"),
        "decision.original_create_recovery")
    for key in ("decision_digest", "proof_digest", "execution_binding_digest",
                "accepted_adapter_digest"):
        _require_digest(original[key], f"decision.original_create_recovery.{key}")
    _require_full_commit(original["accepted_commit"],
                         "decision.original_create_recovery.accepted_commit")
    activation = _strict_keys(
        decision["source_activation"],
        ("parent_issue_id", "comment_id", "author_id", "author_type",
         "comment_content_raw_digest", "resolution_attachment_id",
         "resolution_raw_digest", "request_attachment_id",
         "request_raw_digest", "package_id", "envelope_digest",
         "task_fingerprint"),
        "decision.source_activation")
    _require_uuid(activation["parent_issue_id"],
                  "decision.source_activation.parent_issue_id")
    _require_uuid(activation["comment_id"], "decision.source_activation."
                                            "comment_id")
    _require_uuid(activation["author_id"], "decision.source_activation."
                                           "author_id")
    if activation["author_type"] != "agent":
        raise R0BValidationRefused(
            "decision.source_activation.author_type must be agent",
            author_type=activation["author_type"])
    _require_digest(activation["comment_content_raw_digest"],
                    "decision.source_activation.comment_content_raw_digest")
    _require_uuid(activation["resolution_attachment_id"],
                  "decision.source_activation.resolution_attachment_id")
    _require_digest(activation["resolution_raw_digest"],
                    "decision.source_activation.resolution_raw_digest")
    _require_uuid(activation["request_attachment_id"],
                  "decision.source_activation.request_attachment_id")
    _require_digest(activation["request_raw_digest"],
                    "decision.source_activation.request_raw_digest")
    _require_text(activation["package_id"],
                  "decision.source_activation.package_id")
    _require_digest(activation["envelope_digest"],
                    "decision.source_activation.envelope_digest")
    _require_digest(activation["task_fingerprint"],
                    "decision.source_activation.task_fingerprint")
    migration = _strict_keys(
        decision["execution_migration"],
        ("accepted_commit", "accepted_tree", "adapter_raw_digest",
         "adapter_lf_digest", "store_file_digest", "note_file_digest",
         "artifact_dependency_digest"),
        "decision.execution_migration")
    _require_full_commit(migration["accepted_commit"],
                         "decision.execution_migration.accepted_commit")
    _require_full_commit(migration["accepted_tree"],
                         "decision.execution_migration.accepted_tree")
    for key in ("adapter_raw_digest", "adapter_lf_digest", "store_file_digest",
                "note_file_digest", "artifact_dependency_digest"):
        _require_digest(migration[key], f"decision.execution_migration.{key}")
    if decision["purpose"] != PUBLICATION_RECOVERY_PURPOSE:
        raise R0BValidationRefused(
            "decision.purpose is not the single bounded recovery purpose",
            purpose=decision["purpose"])
    if decision["trigger_policy"] != PUBLICATION_TRIGGER_POLICY:
        raise R0BValidationRefused(
            "decision.trigger_policy must forbid arm/trigger/create",
            trigger_policy=decision["trigger_policy"])
    if decision["design_ref"] != PUBLICATION_TRANSPORT_DESIGN_REF or \
            decision["design_digest"] != PUBLICATION_TRANSPORT_DESIGN_DIGEST:
        raise R0BValidationRefused(
            "decision does not reference the accepted publication transport "
            "contract and its exact digest",
            design_ref=decision["design_ref"],
            design_digest=decision["design_digest"])
    if decision["human_approval_ref"] != \
            PUBLICATION_RECOVERY_HUMAN_APPROVAL_REF:
        raise R0BValidationRefused(
            "decision does not reference the exact Human approval",
            human_approval_ref=decision["human_approval_ref"])
    if decision["lead_approval_ref"] != PUBLICATION_RECOVERY_LEAD_APPROVAL_REF:
        raise R0BValidationRefused(
            "decision does not reference the exact Lead exception boundary",
            lead_approval_ref=decision["lead_approval_ref"])
    _require_text(decision["approval_ref"], "decision.approval_ref")
    _require_text(decision["approved_by"], "decision.approved_by")
    _require_text(decision["approved_at"], "decision.approved_at")
    try:
        o2.parse_ts(decision["approved_at"])
    except (ValueError, TypeError) as exc:
        raise R0BValidationRefused(
            f"decision.approved_at is not a timestamp: {exc}")
    recomputed = digest({k: v for k, v in decision.items()
                         if k != "decision_digest"})
    if decision["decision_digest"] != recomputed:
        raise R0BValidationRefused(
            "publication recovery decision self-digest does not reproduce; "
            "the disposition is edited or incomplete",
            expected=recomputed, found=decision["decision_digest"])
    return {k: v for k, v in decision.items()}


def validate_publication_execution_migration(intent: dict, data: dict,
                                             migration: dict, *,
                                             applying: bool = False) -> dict:
    """Validate the committed execution-identity migration chain exactly.

    The migration binds the complete old da99c11 create-recovery identity
    (execution binding, proof, decision, exact predecessor pins), the current
    incident/attempt/blocker and audited shared prefix, and the new
    commit/tree/adapter raw+LF identity plus the modified store/note/
    dependency file digests. New adapter bytes and the migrated store/note
    bytes are checked against the committed blobs and the executing files; a
    migration that only substitutes the adapter hash, copies across intents or
    hides changed dependency bytes is refused.
    """
    fields = (
        "schema", "migration_id", "intent_id", "old_execution_binding_digest",
        "old_recovery_proof_digest", "old_decision_digest",
        "predecessor_commit", "predecessor_adapter_digest", "blocker_digest",
        "attempt_digest", "shared_prefix", "new_commit", "new_tree",
        "new_adapter_raw_digest", "new_adapter_lf_digest",
        "store_file_digest", "note_file_digest", "artifact_dependency_digest",
        "migration_decision_digest", "publication_recovery_proof_digest",
        "from_revision", "commit_revision", "migration_digest")
    migration = _strict_keys(_require_dict(
        migration, "publication_execution_migration"), fields,
        "publication execution migration")
    if migration["schema"] != PUBLICATION_MIGRATION_SCHEMA:
        raise R0BValidationRefused(
            "publication execution migration schema is unsupported",
            schema=migration["schema"])
    _require_text(migration["migration_id"], "migration.migration_id")
    if migration["intent_id"] != intent["intent_id"]:
        raise R0BValidationRefused(
            "the execution migration belongs to a different intent "
            "(cross-intent migration refused)", intent_id=intent["intent_id"])
    execution = data.get("execution_binding")
    proof = data.get("recovery_proof")
    if not isinstance(execution, dict) or not isinstance(proof, dict):
        raise R0BValidationRefused(
            "the execution migration requires the complete original "
            "create-recovery binding and proof")
    if migration["old_execution_binding_digest"] != digest(execution):
        raise R0BValidationRefused(
            "the migration does not bind the exact committed execution "
            "binding digest")
    if migration["old_recovery_proof_digest"] != digest(proof):
        raise R0BValidationRefused(
            "the migration does not bind the exact committed create-recovery "
            "proof digest")
    old_decision = proof.get("decision") or {}
    if migration["old_decision_digest"] != old_decision.get("decision_digest"):
        raise R0BValidationRefused(
            "the migration does not bind the committed create-recovery "
            "decision")
    if execution.get("adapter_digest") != PREDECESSOR_FORWARD_ADAPTER_DIGEST or \
            migration["predecessor_commit"] != PREDECESSOR_FORWARD_COMMIT or \
            migration["predecessor_adapter_digest"] != \
            PREDECESSOR_FORWARD_ADAPTER_DIGEST:
        raise R0BValidationRefused(
            "the migration does not name the exact da99c11 predecessor "
            "commit/adapter identity")
    publication_proof = data.get("publication_recovery_proof")
    if not isinstance(publication_proof, dict):
        raise R0BValidationRefused(
            "the execution migration requires the committed publication "
            "recovery proof")
    if migration["publication_recovery_proof_digest"] != \
            publication_proof.get("proof_digest"):
        raise R0BValidationRefused(
            "the migration does not bind the committed publication recovery "
            "proof digest")
    if migration["blocker_digest"] != \
            (publication_proof.get("blocker") or {}).get("transition_digest"):
        raise R0BValidationRefused(
            "the migration blocker digest does not match the committed "
            "publication recovery proof")
    if migration["attempt_digest"] != \
            (publication_proof.get("attempt") or {}).get("event_digest"):
        raise R0BValidationRefused(
            "the migration attempt digest does not match the committed "
            "publication recovery proof")
    shared_prefix = _strict_keys(
        migration["shared_prefix"], ("length", "digest", "raw_prefix_digest"),
        "migration.shared_prefix")
    if not isinstance(shared_prefix["length"], int) or \
            shared_prefix["length"] < 1:
        raise R0BValidationRefused(
            "migration.shared_prefix.length must be a positive integer")
    _require_digest(shared_prefix["digest"],
                    "migration.shared_prefix.digest")
    _require_digest(shared_prefix["raw_prefix_digest"],
                    "migration.shared_prefix.raw_prefix_digest")
    if shared_prefix != (publication_proof.get("shared_history") or {}).get(
            "audited_prefix"):
        raise R0BValidationRefused(
            "the migration shared prefix does not equal the committed "
            "publication proof's audited prefix")
    if migration["migration_decision_digest"] != \
            (publication_proof.get("decision") or {}).get("decision_digest"):
        raise R0BValidationRefused(
            "the migration does not bind the Lead migration decision")
    _require_full_commit(migration["new_commit"], "migration.new_commit")
    _require_full_commit(migration["new_tree"], "migration.new_tree")
    if migration["new_adapter_lf_digest"] != adapter_digest() or \
            migration["new_adapter_raw_digest"] != adapter_raw_digest():
        raise R0BValidationRefused(
            "the migration's new adapter identity does not match these "
            "executing adapter bytes; execution is never granted to "
            "unaccepted bytes")
    if _git_rev_parse(migration["new_commit"]) != migration["new_commit"]:
        raise R0BValidationRefused(
            "the migration's new commit is not resolvable in this checkout")
    if _git_rev_parse(migration["new_commit"] + "^{tree}") != \
            migration["new_tree"]:
        raise R0BValidationRefused(
            "the migration's new tree does not match the committed tree")
    blob_reader = _git_blob_reader(ROOT)
    for path, recorded in (
            (ADAPTER_MODULE, migration["new_adapter_lf_digest"]),
            ("tools/chandoff_intent.py", migration["store_file_digest"]),
            ("tools/chandoff_note.py", migration["note_file_digest"])):
        try:
            blob = blob_reader(migration["new_commit"], path)
        except Exception as exc:  # noqa: BLE001 - unresolvable is a refusal
            raise R0BValidationRefused(
                f"the migration's committed {path} blob is not resolvable: "
                f"{type(exc).__name__}: {exc}")
        actual = "sha256:" + hashlib.sha256(
            bytes(blob).replace(b"\r\n", b"\n")).hexdigest()
        if actual != recorded:
            raise R0BValidationRefused(
                f"the committed {path} bytes do not match the migration "
                "digest", path=path, recorded=recorded, actual=actual)
    if _file_lf_digest(ROOT / "tools/chandoff_intent.py") != \
            migration["store_file_digest"]:
        raise R0BValidationRefused(
            "the executing store bytes changed after the migration; the "
            "migrated identity does not cover these bytes")
    if _file_lf_digest(ROOT / "tools/chandoff_note.py") != \
            migration["note_file_digest"]:
        raise R0BValidationRefused(
            "the executing note bytes changed after the migration; the "
            "migrated identity does not cover these bytes")
    if migration["artifact_dependency_digest"] != \
            (data.get("artifact_dependency") or {}).get("digest"):
        raise R0BValidationRefused(
            "the migration artifact-dependency digest does not match the "
            "intent record")
    if not isinstance(migration["from_revision"], int) or \
            not isinstance(migration["commit_revision"], int):
        raise R0BValidationRefused(
            "migration revisions must be integers")
    if migration["commit_revision"] != migration["from_revision"] + 1 or \
            migration["commit_revision"] != PUBLICATION_MIGRATION_COMMIT_REVISION:
        raise R0BValidationRefused(
            "the migration must bind exactly the single BLOCKED -> "
            "HANDOFF_PUBLISHED commit revision",
            from_revision=migration["from_revision"],
            commit_revision=migration["commit_revision"])
    if applying:
        if intent["revision"] != migration["from_revision"]:
            raise R0BValidationRefused(
                "the migration is not applied to its exact prior revision",
                expected=migration["from_revision"],
                found=intent["revision"])
    elif intent["revision"] < migration["commit_revision"]:
        raise R0BValidationRefused(
            "the record has not reached the migration commit revision",
            revision=intent["revision"])
    recomputed = digest({k: v for k, v in migration.items()
                         if k != "migration_digest"})
    if migration["migration_digest"] != recomputed:
        raise R0BValidationRefused(
            "the execution migration self-digest does not reproduce",
            expected=recomputed, found=migration["migration_digest"])
    return dict(migration)


def reconstruct_request_from_fresh_snapshot(fresh_issue: dict,
                                            accepted_request: dict) -> dict:
    """Pure replay of the accepted fresh-target request reconstruction.

    Mirrors the accepted `build_snapshot_request` derivation for the exact
    accepted mapping: title/description/requirements/acceptance come from the
    freshly read target issue, the explicitly accepted decision array and
    every other caller input come from the accepted request. Callers compare
    the result verbatim with the accepted E request, so an edited or stale
    target body never reproduces it and an attached decision list is never
    silently substituted.
    """
    import chandoff_adapter as adapter
    if not isinstance(fresh_issue, dict) or \
            not isinstance(accepted_request, dict):
        raise R0BValidationRefused(
            "fresh-request reconstruction requires the target issue and the "
            "accepted request objects")
    title = fresh_issue.get("title")
    description = fresh_issue.get("description")
    identifier = fresh_issue.get("identifier")
    if not isinstance(title, str) or not title or \
            not isinstance(description, str) or not description or \
            not isinstance(identifier, str) or not identifier:
        raise R0BValidationRefused(
            "the fresh target issue does not carry title/description/"
            "identifier; the request cannot be reconstructed")
    snapshot = accepted_request.get("task_snapshot")
    if not isinstance(snapshot, dict):
        raise R0BValidationRefused(
            "the accepted request carries no task_snapshot")
    decisions = snapshot.get("relevant_decisions")
    if not isinstance(decisions, list) or \
            any(not isinstance(item, str) for item in decisions):
        raise R0BValidationRefused(
            "the accepted request relevant_decisions is not a string list")
    requirements, acceptance = adapter.extract_structured(description)
    rebuilt_snapshot = {
        "title": title,
        "description": description,
        "requirements": [item["value"] for item in requirements],
        "acceptance_criteria": [item["value"] for item in acceptance],
        "relevant_decisions": list(decisions),
    }
    if snapshot.get("parent_task_ref") is not None:
        rebuilt_snapshot["parent_task_ref"] = snapshot["parent_task_ref"]
    return {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": "multica://issue/" + identifier,
        "project": dict(accepted_request.get("project") or {}),
        "target": dict(accepted_request.get("target") or {}),
        "purpose": accepted_request.get("purpose"),
        "task_snapshot": rebuilt_snapshot,
        "caller": dict(accepted_request.get("caller") or {}),
        "options": copy.deepcopy(accepted_request.get("options") or {}),
    }


def _digest_present(text: str, digest_value: str) -> bool:
    """True when the text carries the digest, with or without the sha256 tag."""
    if not isinstance(text, str) or not isinstance(digest_value, str):
        return False
    body = digest_value.split(":", 1)[-1]
    return digest_value in text or (bool(body) and body in text)


def _publication_raw_read_plan(*, target_id: str, parent_id: str,
                               resolution_id: str, request_id: str) -> list:
    """The exact accepted read sequence of one publication recovery proof."""
    out = ["--output", "json"]
    return [
        ("parent_comments",
         ["issue", "comment", "list", parent_id, "--full"] + out),
        ("download_resolution",
         ["attachment", "download", resolution_id, "--output-dir", None]),
        ("download_request",
         ["attachment", "download", request_id, "--output-dir", None]),
        ("target_get", ["issue", "get", target_id] + out),
        ("target_comments",
         ["issue", "comment", "list", target_id, "--full"] + out),
        ("target_timeline",
         ["issue", "timeline", target_id, "--activity-only"] + out),
        ("target_runs", ["issue", "runs", target_id] + out),
        ("target_get", ["issue", "get", target_id] + out),
        ("target_comments",
         ["issue", "comment", "list", target_id, "--full"] + out),
        ("target_timeline",
         ["issue", "timeline", target_id, "--activity-only"] + out),
        ("target_runs", ["issue", "runs", target_id] + out),
        ("target_get", ["issue", "get", target_id] + out),
    ]


def _publication_parse_raw_reads(proof: dict, *, target_id: str,
                                 parent_id: str, resolution_id: str,
                                 request_id: str) -> list:
    """Require the complete persisted raw responses to be the exact reads.

    No substituted, duplicated, extra or missing read is accepted: the
    sequence is the accepted evidence collection shape, every exit is a
    success and every stdout digest reproduces.
    """
    responses = proof["observations"]["raw_responses"]
    plan = _publication_raw_read_plan(
        target_id=target_id, parent_id=parent_id,
        resolution_id=resolution_id, request_id=request_id)
    if not isinstance(responses, list) or len(responses) != len(plan):
        raise R0BValidationRefused(
            "the publication recovery proof raw responses are not the exact "
            "accepted read sequence; a rehashed or incomplete trace is never "
            "recovery authority",
            expected=len(plan),
            found=len(responses) if isinstance(responses, list) else None)
    reads: list = []
    for response, (kind, expected) in zip(responses, plan):
        entry = _strict_keys(
            response, ("argv", "exit_code", "stdout", "stdout_digest"),
            "proof.observations.raw_responses[]")
        if entry["exit_code"] != 0:
            raise R0BValidationRefused(
                "a publication recovery proof raw read was not a successful "
                "read", kind=kind, exit_code=entry["exit_code"])
        argv = entry["argv"]
        if not isinstance(argv, list) or len(argv) < 2 or \
                not all(isinstance(item, str) for item in argv):
            raise R0BValidationRefused(
                "a publication recovery proof raw read argv is malformed",
                kind=kind)
        core = argv[1:]
        if expected[-1] is None:
            if core[:4] != expected[:4] or len(core) != 5:
                raise R0BValidationRefused(
                    "a publication recovery proof attachment download did not "
                    "use the exact accepted argv", kind=kind, argv=core[:8])
        elif core != expected:
            raise R0BValidationRefused(
                "a publication recovery proof raw read is not the exact "
                "accepted argv; no substituted or extra read is accepted",
                kind=kind, argv=core[:10])
        if not isinstance(entry["stdout"], str) or \
                entry["stdout_digest"] != _sha256_utf8(entry["stdout"]):
            raise R0BValidationRefused(
                "a publication recovery proof raw read response digest does "
                "not reproduce", kind=kind)
        try:
            parsed = json.loads(entry["stdout"])
        except json.JSONDecodeError as exc:
            raise R0BValidationRefused(
                f"a publication recovery proof raw read is not JSON: {exc}",
                kind=kind)
        reads.append({"kind": kind, "argv": argv, "stdout": entry["stdout"],
                      "json": parsed})
    return reads


def _publication_derive_reads(proof: dict, *, target_id: str, parent_id: str,
                              resolution_id: str, request_id: str) -> dict:
    """Derive every normalized observation from the persisted raw responses."""
    reads = _publication_parse_raw_reads(
        proof, target_id=target_id, parent_id=parent_id,
        resolution_id=resolution_id, request_id=request_id)
    parent_comments = reads[0]["json"]
    if not isinstance(parent_comments, list) or \
            any(not isinstance(row, dict) for row in parent_comments):
        raise R0BValidationRefused(
            "the parent activation listing is not a complete comment array")
    for index, name in ((3, "first target issue read"),
                        (7, "second target issue read"),
                        (11, "target issue recheck")):
        if not isinstance(reads[index]["json"], dict):
            raise R0BValidationRefused(
                f"the {name} is not an issue object", kind=reads[index]["kind"])
    comments_1_raw: list = reads[4]["json"]
    comments_2_raw: list = reads[8]["json"]
    for rows in (comments_1_raw, comments_2_raw):
        if not isinstance(rows, list) or \
                any(not isinstance(row, dict) for row in rows):
            raise R0BValidationRefused(
                "a target comment listing is not a complete comment array")
    activities_1 = reads[5]["json"]
    activities_2 = reads[9]["json"]
    for rows in (activities_1, activities_2):
        if not isinstance(rows, list):
            raise R0BValidationRefused(
                "a target timeline listing is not an activity array")

    def runs_of(index: int) -> list:
        raw = reads[index]["json"]
        if not isinstance(raw, list):
            raise R0BValidationRefused(
                "a target run listing is not an array")
        try:
            return dispatch.parse_runs_json(json.dumps(raw))
        except dispatch.DispatchError as exc:
            raise R0BValidationRefused(
                f"a target run listing violates the runs contract: "
                f"{exc.message}") from exc

    return {
        "reads": reads,
        "parent_comments": parent_comments,
        "target_issue": reads[3]["json"],
        "target_second": reads[7]["json"],
        "target_recheck": reads[11]["json"],
        "comments_1_raw": comments_1_raw,
        "comments_2_raw": comments_2_raw,
        "comments_1": [comment_record(doc) for doc in comments_1_raw],
        "comments_2": [comment_record(doc) for doc in comments_2_raw],
        "activities_1": activities_1,
        "activities_2": activities_2,
        "runs_1": runs_of(6),
        "runs_2": runs_of(10),
        "content_of": {doc.get("id"): doc.get("content")
                       for doc in comments_1_raw},
    }


def validate_publication_recovery_semantics(proof: dict, *, intent: dict,
                                            data: dict, target: dict,
                                            blocker: dict, attempt: dict,
                                            note_doc: dict,
                                            observations: dict, shared: dict,
                                            decision: dict) -> dict:
    """One shared pure semantic verifier: writer AND reducer/restart.

    Every observation is re-derived from the complete persisted raw responses,
    the exact publication predicates are re-evaluated against those derived
    observations, the source activation and approval authority content are
    re-verified from the persisted inline evidence, and every shared-history
    classification is re-derived from the record bytes. Recomputed
    attacker-controlled hashes never make invalid evidence valid, and no live
    read is required.
    """
    execution = data.get("execution_context")
    envelope = (execution or {}).get("result")
    request = (execution or {}).get("request")
    if not isinstance(execution, dict) or not isinstance(request, dict) or \
            not isinstance(envelope, dict):
        raise R0BValidationRefused(
            "the committed record no longer carries the bound E request/"
            "result; the publication proof cannot be re-derived")
    activation = decision["source_activation"]
    derived = _publication_derive_reads(
        proof, target_id=target["issue_id"],
        parent_id=activation["parent_issue_id"],
        resolution_id=activation["resolution_attachment_id"],
        request_id=activation["request_attachment_id"])

    # -- 1. raw-response -> projection correspondence -----------------------
    if canonical_json(observations["target_issue"]) != \
            canonical_json(derived["target_issue"]) or \
            canonical_json(observations["target_recheck"]) != \
            canonical_json(derived["target_recheck"]):
        raise R0BValidationRefused(
            "the committed target observations are not the exact projection "
            "of the persisted raw responses; a rehashed projection is "
            "refused", subject="raw-response-mismatch")
    for name, derived_value in (("comments", derived["comments_1"]),
                                ("activities", derived["activities_1"]),
                                ("runs", derived["runs_1"])):
        if canonical_json(observations[name]) != canonical_json(derived_value):
            raise R0BValidationRefused(
                f"the committed {name} observations do not correspond to the "
                "persisted raw responses; the projection is edited or "
                "substituted", subject="raw-response-mismatch", field=name)

    # -- 2. stable full reread ---------------------------------------------
    if issue_projection(derived["target_second"]) != \
            issue_projection(derived["target_issue"]) or \
            derived["target_second"].get("revision") != \
            derived["target_issue"].get("revision"):
        raise R0BValidationRefused(
            "the persisted second complete issue read is not stable; a moving "
            "platform is never recovered", subject="reread")
    if canonical_json(derived["comments_2"]) != \
            canonical_json(derived["comments_1"]) or \
            canonical_json(derived["activities_2"]) != \
            canonical_json(derived["activities_1"]) or \
            canonical_json(derived["runs_2"]) != \
            canonical_json(derived["runs_1"]):
        raise R0BValidationRefused(
            "the persisted second complete comment/timeline/run read is not "
            "stable; recovery refuses a moving platform", subject="reread")

    # -- 3. the full run inventory must be empty -----------------------------
    if derived["runs_1"] or derived["runs_2"]:
        raise R0BValidationRefused(
            "the persisted full run inventory is not empty; any active or "
            "terminal run refuses and is never rehashed away", subject="runs")

    # -- 4. bound identities ------------------------------------------------
    if derived["target_issue"].get("id") != target["issue_id"] or \
            derived["target_recheck"].get("id") != target["issue_id"]:
        raise R0BValidationRefused(
            "the persisted target observations do not name the bound target",
            subject="target")
    if derived["target_issue"].get("parent_issue_id") != \
            activation["parent_issue_id"]:
        raise R0BValidationRefused(
            "the fresh target parent is not the disposed source activation "
            "parent", subject="source_activation")

    # -- 5. the proof blocker/attempt must be the recorded intent records ----
    transitions = intent.get("transitions") or []
    blocking = transitions[-1] if transitions else None
    if not isinstance(blocking, dict) or \
            blocking.get("to") != o2.S_BLOCKED or \
            blocking.get("seq") != blocker["transition_seq"] or \
            digest(blocking) != blocker["transition_digest"] or \
            blocking.get("reason") != blocker["reason"]:
        raise R0BValidationRefused(
            "the committed proof blocker is not the recorded blocking "
            "transition", subject="blocker")
    evidence_events = [
        event for event in intent.get("events") or []
        if event.get("name") == E_EVIDENCE_REFUSED and
        (event.get("data") or {}).get("reason") == REASON_PUBLICATION_PROVENANCE
        and (event.get("data") or {}).get("code") == PUB_NOTE_NOT_FOUND]
    if len(evidence_events) != 1 or \
            evidence_events[0].get("seq") != blocker["evidence_event_seq"] or \
            digest(evidence_events[0]) != blocker["evidence_event_digest"]:
        raise R0BValidationRefused(
            "the committed proof blocker evidence event is not the recorded "
            "intent evidence", subject="blocker")
    attempt_events = [event for event in intent.get("events") or []
                      if event.get("name") == E_PUBLICATION_ISSUING]
    if len(attempt_events) != 1:
        raise R0BValidationRefused(
            "the intent does not carry exactly one recorded publication "
            "attempt", subject="attempt")
    attempt_event = attempt_events[0]
    if attempt_event.get("seq") != attempt["event_seq"] or \
            digest(attempt_event) != attempt["event_digest"] or \
            (attempt_event.get("data") or {}).get("operation_id") != \
            attempt["operation_id"]:
        raise R0BValidationRefused(
            "the committed proof attempt is not the recorded publication "
            "attempt", subject="attempt")

    # -- 6. re-render R from the persisted E and the durable attempt meta ---
    if attempt["package_id"] != execution.get("package_id") or \
            attempt["envelope_digest"] != digest(envelope):
        raise R0BValidationRefused(
            "the committed proof attempt does not bind the persisted E "
            "package/envelope", subject="attempt")
    try:
        rendered, _record = note.render_note_record(
            envelope, prepared_by=attempt["prepared_by"],
            prepared_at=attempt["prepared_at"])
    except Exception as exc:  # noqa: BLE001 - re-render failure is a refusal
        raise R0BValidationRefused(
            f"the persisted E cannot be re-rendered with the durable attempt "
            f"meta: {type(exc).__name__}: {exc}", subject="attempt")
    rendered_raw = _sha256_utf8(rendered)
    if digest_text_lf(rendered) != attempt["rendered_body_digest_lf"] or \
            rendered_raw != attempt["rendered_body_digest_raw"] or \
            len(rendered) != attempt["rendered_chars"] or \
            note_doc["rendered_raw_digest"] != rendered_raw or \
            note_doc["rendered_lf_digest"] != digest_text_lf(rendered) or \
            note_doc["rendered_chars"] != len(rendered) or \
            note_doc["rendered_utf8_bytes"] != len(rendered.encode("utf-8")):
        raise R0BValidationRefused(
            "the re-rendered original body does not reproduce the committed "
            "attempt/note identities", subject="note")

    # -- 7. the unique observed note and the exact relation ------------------
    candidates = [
        item for item in derived["comments_1"]
        if item.get("content_raw_digest") in
        (rendered_raw, note_doc["observed_raw_digest"])]
    if len(candidates) != 1:
        raise R0BValidationRefused(
            "the derived comment inventory does not carry exactly one note "
            "matching the rendered or observed raw identity; duplicates, "
            "R/R-minus-one pairs and missing notes all refuse",
            subject="note", candidates=len(candidates))
    candidate = candidates[0]
    if candidate.get("content_raw_digest") != note_doc["observed_raw_digest"]:
        raise R0BValidationRefused(
            "the sole derived candidate is the rendered body, not the "
            "observed single-terminal-LF-removed transport", subject="note")
    content = derived["content_of"].get(candidate.get("id"))
    if not isinstance(content, str) or \
            _sha256_utf8(content) != note_doc["observed_raw_digest"]:
        raise R0BValidationRefused(
            "the derived note bytes do not reproduce the committed observed "
            "digest", subject="note")
    relation = single_terminal_lf_relation(rendered, content)
    if not relation["accepted"] or not relation["removed_lf"] or \
            note_doc["relation"] != PUBLICATION_RELATION_DIRECTIONAL:
        raise R0BValidationRefused(
            "the derived note is not exactly the rendered body minus one "
            "terminal LF", subject="note",
            detail=str(relation.get("detail"))[:160])
    for key in ("note_comment_id", "revision", "created_at", "updated_at",
                "author_id", "author_type", "source_task_id", "parent_id"):
        field = candidate.get("id" if key == "note_comment_id" else key)
        if field != note_doc[key]:
            raise R0BValidationRefused(
                f"the derived note {key} does not match the committed proof",
                subject="note", field=key)
    parsed = note._parse_record(content)
    meta = parsed.get("meta") or {}
    if not parsed.get("ok_record"):
        raise R0BValidationRefused(
            "the derived note does not parse as a complete CONTEXT_HANDOFF "
            "record", subject="note")
    if meta.get("prepared_by") != attempt["prepared_by"] or \
            meta.get("prepared_at") != attempt["prepared_at"]:
        raise R0BValidationRefused(
            "the derived note meta does not equal the durable attempt meta; "
            "the note never self-certifies", subject="note")
    if meta.get("package_id") != attempt["package_id"] or \
            meta.get("task_ref") != execution.get("task_ref") or \
            meta.get("target_role") != execution.get("role"):
        raise R0BValidationRefused(
            "the derived note meta package/task/role do not match the bound E",
            subject="note")
    if digest(parsed["envelope"]) != attempt["envelope_digest"] or \
            digest(parsed["envelope"]) != note_doc["envelope_digest"]:
        raise R0BValidationRefused(
            "the derived note envelope digest does not match the bound E",
            subject="note")

    # -- 8. before/after delta ----------------------------------------------
    before = (attempt_event.get("data") or {}).get("before") or {}
    before_map = {item.get("id"): item for item in before.get("comments") or []}
    after_map = {item.get("id"): item for item in derived["comments_1"]}
    removed = sorted(set(before_map) - set(after_map))
    added = sorted(set(after_map) - set(before_map))
    edited = [
        cid for cid in before_map
        if canonical_json({
            key: after_map[cid].get(key) for key in before_map[cid]})
        != canonical_json(before_map[cid])]
    if removed or edited or added != [candidate["id"]]:
        raise R0BValidationRefused(
            "the derived before/after comment delta is not exactly the single "
            "observed note", subject="delta", removed=removed[:4],
            edited=edited[:4],
            added=[item for item in added if item != candidate["id"]][:4])
    changed = _issue_diff(issue_projection(before.get("issue") or {}),
                          issue_projection(derived["target_issue"]))
    if changed:
        raise R0BValidationRefused(
            "target issue fields changed around the publication: "
            + ",".join(changed), subject="issue")
    if canonical_json(before.get("activities") or []) != \
            canonical_json(derived["activities_1"]):
        raise R0BValidationRefused(
            "the derived timeline activity delta around the publication is "
            "not attributable", subject="activities")

    # -- 9. resolved source activation and approval authority content --------
    source = _strict_keys(
        proof["source_activation"],
        ("parent_issue_id", "comment", "resolution", "request", "package_id",
         "envelope_digest", "task_fingerprint", "request_digest",
         "reconstructed_digest", "reconstructed_fingerprint"),
        "proof.source_activation")
    for key in ("parent_issue_id", "package_id", "envelope_digest",
                "task_fingerprint"):
        if source[key] != activation[key]:
            raise R0BValidationRefused(
                f"the committed source activation {key} does not equal the "
                "disposition", subject="source_activation", field=key)
    if source["package_id"] != execution.get("package_id") or \
            source["envelope_digest"] != digest(envelope):
        raise R0BValidationRefused(
            "the committed source activation does not bind the persisted E "
            "package/envelope", subject="source_activation")
    if source["task_fingerprint"] != \
            (execution.get("built_from") or {}).get("task_fingerprint"):
        raise R0BValidationRefused(
            "the committed source activation does not bind the frozen E task "
            "fingerprint", subject="source_activation")
    matches = [row for row in derived["parent_comments"]
               if row.get("id") == activation["comment_id"]]
    if len(matches) != 1:
        raise R0BValidationRefused(
            "the parent activation record is missing or duplicated; the "
            "unique Lead-authored activation is required",
            subject="source_activation", matches=len(matches))
    comment = matches[0]
    if comment.get("author_id") != activation["author_id"] or \
            comment.get("author_type") != activation["author_type"] or \
            comment.get("author_id") != PUBLICATION_SOURCE_ACTIVATION_AUTHOR_ID:
        raise R0BValidationRefused(
            "the parent activation record does not carry the exact Lead "
            "author identity", subject="source_activation")
    if _sha256_utf8(comment.get("content") or "") != \
            activation["comment_content_raw_digest"]:
        raise R0BValidationRefused(
            "the parent activation record content is edited; the committed "
            "content digest does not reproduce", subject="source_activation")
    if canonical_json(source["comment"]) != \
            canonical_json(comment_record(comment)):
        raise R0BValidationRefused(
            "the committed source activation comment observation does not "
            "equal the derived parent record", subject="source_activation")
    content_text = comment.get("content") or ""
    missing_bindings = [
        value for value in (source["package_id"], source["envelope_digest"],
                            source["task_fingerprint"], target["issue_id"],
                            intent["intent_id"])
        if isinstance(value, str) and value and value not in content_text]
    for digest_value in (activation["resolution_raw_digest"],
                         activation["request_raw_digest"]):
        if not _digest_present(content_text, digest_value):
            missing_bindings.append(digest_value)
    if missing_bindings:
        raise R0BValidationRefused(
            "the parent activation record does not bind the exact E package/"
            "envelope/fingerprint/target/intent/attachments",
            subject="source_activation", missing=missing_bindings[:4])
    downloads = {}
    for read in derived["reads"]:
        if read["kind"] in ("download_resolution", "download_request"):
            downloads[read["kind"]] = read
    for name, id_field, digest_field in (
            ("resolution", "resolution_attachment_id",
             "resolution_raw_digest"),
            ("request", "request_attachment_id", "request_raw_digest")):
        record = _strict_keys(
            source[name],
            ("attachment_id", "filename", "raw_digest", "chars",
             "utf8_bytes", "text"),
            f"proof.source_activation.{name}")
        if record["attachment_id"] != activation[id_field] or \
                record["raw_digest"] != activation[digest_field]:
            raise R0BValidationRefused(
                f"the committed {name} attachment identity/digest does not "
                "equal the disposition", subject="source_activation")
        text = record["text"]
        if not isinstance(text, str) or \
                _sha256_utf8(text) != record["raw_digest"] or \
                len(text) != record["chars"] or \
                len(text.encode("utf-8")) != record["utf8_bytes"]:
            raise R0BValidationRefused(
                f"the committed {name} attachment bytes do not reproduce "
                "their raw digest", subject="source_activation")
        download = downloads.get("download_" + name)
        stdout = (download or {}).get("json")
        if not isinstance(stdout, dict) or \
                str(stdout.get("id")) != record["attachment_id"] or \
                stdout.get("filename") != record["filename"]:
            raise R0BValidationRefused(
                f"the persisted {name} download response does not match the "
                "committed attachment record", subject="source_activation")
    try:
        attached = json.loads(source["request"]["text"])
    except json.JSONDecodeError as exc:
        raise R0BValidationRefused(
            f"the committed request attachment is not JSON: {exc}",
            subject="source_activation")
    if not isinstance(attached, dict):
        raise R0BValidationRefused(
            "the committed request attachment is not an object",
            subject="source_activation")
    if canonical_json(attached) != canonical_json(request):
        raise R0BValidationRefused(
            "the attached accepted request is not the exact persisted E "
            "request; a substituted request is refused",
            subject="source_activation")
    relevant = ((attached.get("task_snapshot") or {})
                .get("relevant_decisions"))
    if not isinstance(relevant, list) or not relevant or \
            relevant[0] != source["resolution"]["text"]:
        raise R0BValidationRefused(
            "the attached request relevant_decisions does not carry the exact "
            "accepted resolution text", subject="source_activation")
    resolution_hex = source["resolution"]["raw_digest"].split(":", 1)[1]
    if not any(isinstance(item, str) and resolution_hex in item
               for item in relevant[1:]):
        raise R0BValidationRefused(
            "the attached request relevant_decisions does not carry the "
            "resolution artifact raw digest", subject="source_activation")
    fingerprint = chandoff.fingerprint_from_request(attached)
    if fingerprint != source["task_fingerprint"] or \
            fingerprint != source["reconstructed_fingerprint"]:
        raise R0BValidationRefused(
            "the attached request fingerprint does not reproduce the frozen E "
            "fingerprint", subject="source_activation")
    if chandoff.compute_built_from(attached) != execution.get("built_from"):
        raise R0BValidationRefused(
            "the attached request built_from does not reproduce the frozen E "
            "built_from", subject="source_activation")
    reconstructed = reconstruct_request_from_fresh_snapshot(
        derived["target_issue"], attached)
    if canonical_json(reconstructed) != canonical_json(attached):
        raise R0BValidationRefused(
            "the freshly read target body plus the accepted explicit "
            "decisions do not reproduce the accepted E request; a stale or "
            "edited target is refused", subject="source_activation")
    if source["request_digest"] != digest(attached) or \
            source["reconstructed_digest"] != digest(reconstructed):
        raise R0BValidationRefused(
            "the committed request/reconstruction digests do not reproduce",
            subject="source_activation")
    approvals = _strict_keys(
        proof["authority_approvals"], ("human", "lead_boundary"),
        "proof.authority_approvals")
    for name, fixed_id, fixed_author, fixed_type, fixed_digest in (
            ("human", PUBLICATION_RECOVERY_HUMAN_APPROVAL_COMMENT_ID,
             PUBLICATION_RECOVERY_HUMAN_APPROVAL_AUTHOR_ID, "member",
             PUBLICATION_RECOVERY_HUMAN_APPROVAL_CONTENT_DIGEST),
            ("lead_boundary", PUBLICATION_RECOVERY_LEAD_APPROVAL_COMMENT_ID,
             PUBLICATION_RECOVERY_LEAD_APPROVAL_AUTHOR_ID, "agent",
             PUBLICATION_RECOVERY_LEAD_APPROVAL_CONTENT_DIGEST)):
        row = _strict_keys(
            approvals[name],
            ("comment_id", "author_id", "author_type", "content_raw_digest",
             "comment"),
            f"proof.authority_approvals.{name}")
        if row["comment_id"] != fixed_id or \
                row["author_id"] != fixed_author or \
                row["author_type"] != fixed_type or \
                row["content_raw_digest"] != fixed_digest:
            raise R0BValidationRefused(
                f"the committed {name} approval identity/content does not "
                "equal the accepted authority", subject="authority")
        found = [item for item in derived["parent_comments"]
                 if item.get("id") == fixed_id]
        if len(found) != 1:
            raise R0BValidationRefused(
                f"the {name} approval record is missing or duplicated on the "
                "parent", subject="authority", matches=len(found))
        actual = found[0]
        if actual.get("author_id") != fixed_author or \
                actual.get("author_type") != fixed_type or \
                _sha256_utf8(actual.get("content") or "") != fixed_digest:
            raise R0BValidationRefused(
                f"the actual {name} approval comment does not match the "
                "accepted author/content; a fixed reference string alone is "
                "never authority", subject="authority")
        if canonical_json(row["comment"]) != \
                canonical_json(comment_record(actual)):
            raise R0BValidationRefused(
                f"the committed {name} approval observation does not equal "
                "the derived parent record", subject="authority")
    join = proof["material"]["source_join"]
    if join.get("source_activation_comment_id") != activation["comment_id"] or \
            join.get("source_activation_author_id") != \
            activation["author_id"] or \
            join.get("resolution_raw_digest") != \
            activation["resolution_raw_digest"] or \
            join.get("request_raw_digest") != activation["request_raw_digest"] or \
            join.get("reconstructed_request_digest") != \
            digest(reconstructed) or \
            join.get("task_fingerprint") != source["task_fingerprint"]:
        raise R0BValidationRefused(
            "the committed source join does not bind the resolved activation",
            subject="source_join")

    # -- 10. re-derive the shared-history classification semantics -----------
    entries: list = []
    for item in shared["records"]:
        record = item.get("record")
        previous = entries[-1] if entries else None
        try:
            derived_entry = FactoryClass._publication_record_class(
                record, previous, intent_id=intent["intent_id"],
                spec=data["creation_spec"], target_id=target["issue_id"])
        except PreflightRefusal as exc:
            raise R0BValidationRefused(
                "the inline shared-history record does not classify under the "
                f"accepted rule: {exc.detail}", subject="shared_history",
                seq=item.get("seq")) from exc
        if derived_entry["classification"] != item.get("classification") or \
                derived_entry["reason"] != item.get("reason"):
            raise R0BValidationRefused(
                "a stored shared-history classification is relabelled; the "
                "record semantics do not reproduce the committed label",
                subject="shared_history", seq=item.get("seq"),
                recorded=item.get("classification"),
                derived=derived_entry["classification"])
        entries.append(derived_entry)

    def class_entries(name):
        return [entry for entry in entries
                if entry["classification"] == name]

    create_commands = class_entries(CLS_ORIGINAL_CREATE_COMMAND)
    create_results = class_entries(CLS_ORIGINAL_CREATE_RESULT)
    ownership_commands = class_entries(CLS_OWNERSHIP_COMMAND)
    ownership_results = class_entries(CLS_OWNERSHIP_RESULT)
    publication_commands = class_entries(CLS_PUBLICATION_COMMAND)
    publication_results = class_entries(CLS_PUBLICATION_RESULT)
    if len(create_commands) != 1 or len(create_results) != 1 or \
            len(ownership_commands) != 1 or len(ownership_results) != 1 or \
            len(publication_commands) > 1 or len(publication_results) > 1 or \
            len(publication_commands) != len(publication_results):
        raise R0BValidationRefused(
            "the re-derived shared-history pair counts are not the accepted "
            "single create/ownership and at most one publication pair",
            subject="shared_history")
    derived_pairs = {
        "create_pair": {
            "command_seq": create_commands[0]["record"].get("seq"),
            "command_digest": create_commands[0]["digest"],
            "result_seq": create_results[0]["record"].get("seq"),
            "result_digest": create_results[0]["digest"]},
        "ownership_pair": {
            "command_seq": ownership_commands[0]["record"].get("seq"),
            "command_digest": ownership_commands[0]["digest"],
            "result_seq": ownership_results[0]["record"].get("seq"),
            "result_digest": ownership_results[0]["digest"]},
        "publication_pair": ({
            "command_seq": publication_commands[0]["record"].get("seq"),
            "command_digest": publication_commands[0]["digest"],
            "result_seq": publication_results[0]["record"].get("seq"),
            "result_digest": publication_results[0]["digest"],
        } if publication_commands else None),
    }
    for pair_name, pair_value in derived_pairs.items():
        if canonical_json(shared[pair_name]) != canonical_json(pair_value):
            raise R0BValidationRefused(
                f"the committed {pair_name} does not reproduce from the "
                "inline shared-history records", subject="shared_history")
    return derived


def validate_publication_recovery_proof(proof: dict, *, intent: dict,
                                        data: dict,
                                        applying: bool = False,
                                        replay: bool = False) -> dict:
    """Recompute every committed publication-recovery proof predicate.

    The proof is fully inline and audit-reconstructable: the exact blocking
    transition and evidence event, the sole publication attempt, the observed
    note with both raw identities and the exact single-terminal-LF relation,
    the complete platform observations, the resolved source activation and
    approval authority content, the classification of every shared ledger
    record, the historical execution authority and the material rechecks.
    Hash/count-only, edited, truncated or cross-intent proofs are refused;
    successful parsing is never sufficient. The same semantics run for the
    commit writer and the reducer/restart replay and never require a live
    read.
    """
    fields = (
        "schema", "contract_version", "intent_id", "target", "state_before",
        "intent_revision_before", "commit_revision", "blocker", "attempt",
        "note", "original_create_recovery", "execution_authority_historical",
        "observations", "shared_history", "source_activation",
        "authority_approvals", "material", "decision",
        "decision_digest", "proof_digest")
    proof = _strict_keys(_require_dict(proof, "publication_recovery_proof"),
                         fields, "publication recovery proof")
    if proof["schema"] != PUBLICATION_RECOVERY_PROOF_SCHEMA:
        raise R0BValidationRefused(
            "publication recovery proof schema is unsupported; an old or "
            "hash-only proof is never accepted recovery authority",
            schema=proof["schema"])
    if proof["contract_version"] != CONTRACT_VERSION:
        raise R0BValidationRefused(
            "publication recovery proof contract version is unsupported")
    if proof["intent_id"] != intent["intent_id"]:
        raise R0BValidationRefused(
            "the publication recovery proof belongs to a different intent "
            "(cross-intent proof refused)")
    if proof["state_before"] != o2.S_BLOCKED:
        raise R0BValidationRefused(
            "the publication recovery proof does not bind the supported prior "
            "phase BLOCKED")
    if replay:
        if intent.get("state") != o2.S_HANDOFF_PUBLISHED or \
                intent["revision"] != proof["commit_revision"]:
            raise R0BValidationRefused(
                "the committed publication recovery proof is not replayed on "
                "its exact committed revision/state",
                state=intent.get("state"), revision=intent.get("revision"))
    elif proof["intent_revision_before"] != intent["revision"] and not applying:
        raise R0BValidationRefused(
            "the publication recovery proof revision does not match the "
            "record", expected=proof["intent_revision_before"],
            found=intent["revision"])
    if applying and proof["intent_revision_before"] != intent["revision"]:
        raise R0BValidationRefused(
            "the publication recovery proof is not applied to its exact prior "
            "revision")
    if proof["commit_revision"] != proof["intent_revision_before"] + 1 or \
            proof["commit_revision"] != PUBLICATION_MIGRATION_COMMIT_REVISION:
        raise R0BValidationRefused(
            "the publication recovery proof must bind exactly one "
            "BLOCKED -> HANDOFF_PUBLISHED commit revision")
    target = _strict_keys(proof["target"], ("issue_id", "identifier"),
                          "proof.target")
    bound_target = data.get("target_binding") or {}
    if target["issue_id"] != bound_target.get("issue_id") or \
            target["issue_id"] != intent["fields"].get("issue_id"):
        raise R0BValidationRefused(
            "the publication recovery proof target does not match the bound "
            "target")
    blocker = _strict_keys(
        proof["blocker"],
        ("transition_seq", "transition_digest", "reason", "evidence_code",
         "evidence_event_seq", "evidence_event_digest", "detail"),
        "proof.blocker")
    for key in ("transition_seq", "evidence_event_seq"):
        if not isinstance(blocker[key], int) or blocker[key] < 1:
            raise R0BValidationRefused(
                f"proof.blocker.{key} must be a positive integer")
    _require_digest(blocker["transition_digest"], "proof.blocker."
                                                   "transition_digest")
    _require_digest(blocker["evidence_event_digest"], "proof.blocker."
                                                      "evidence_event_digest")
    if blocker["reason"] != REASON_PUBLICATION_PROVENANCE or \
            blocker["evidence_code"] != PUB_NOTE_NOT_FOUND:
        raise R0BValidationRefused(
            "the publication recovery proof does not bind the exact typing "
            "incident blocker")
    attempt = _strict_keys(
        proof["attempt"],
        ("event_seq", "event_digest", "operation_id", "package_id",
         "envelope_digest", "rendered_body_digest_lf",
         "rendered_body_digest_raw", "rendered_chars",
         "before_issue_revision", "prepared_by",
         "prepared_at", "publisher_run_id", "parent_comment_id",
         "before_evidence_digest"),
        "proof.attempt")
    if not isinstance(attempt["event_seq"], int) or attempt["event_seq"] < 1:
        raise R0BValidationRefused(
            "proof.attempt.event_seq must be a positive integer")
    for key in ("event_digest", "envelope_digest", "rendered_body_digest_lf",
                "rendered_body_digest_raw", "before_evidence_digest"):
        _require_digest(attempt[key], f"proof.attempt.{key}")
    execution = data.get("execution_context") or {}
    if attempt["package_id"] != execution.get("package_id") or \
            attempt["envelope_digest"] != execution.get("envelope_digest"):
        raise R0BValidationRefused(
            "the publication recovery proof attempt does not bind the "
            "original E package identity")
    if not isinstance(attempt["rendered_chars"], int) or \
            attempt["rendered_chars"] < 1:
        raise R0BValidationRefused(
            "proof.attempt.rendered_chars must be a positive integer")
    if not isinstance(attempt["before_issue_revision"], int) or \
            attempt["before_issue_revision"] < 1:
        raise R0BValidationRefused(
            "proof.attempt.before_issue_revision must be a positive integer")
    note = _strict_keys(
        proof["note"],
        ("note_comment_id", "revision", "created_at", "updated_at",
         "author_id", "author_type", "source_task_id", "parent_id",
         "observed_raw_digest", "observed_lf_digest", "observed_chars",
         "observed_utf8_bytes", "rendered_raw_digest", "rendered_lf_digest",
         "rendered_chars", "rendered_utf8_bytes", "relation",
         "meta_prepared_by", "meta_prepared_at", "meta_package_id",
         "meta_task_ref", "meta_target_role", "envelope_digest",
         "record_parsed"),
        "proof.note")
    for key in ("observed_raw_digest", "observed_lf_digest",
                "rendered_raw_digest", "rendered_lf_digest",
                "envelope_digest"):
        _require_digest(note[key], f"proof.note.{key}")
    if note["relation"] != PUBLICATION_RELATION_DIRECTIONAL:
        raise R0BValidationRefused(
            "the publication recovery proof note relation is not the exact "
            "approved historical single-terminal-LF removal",
            relation=note["relation"])
    if note["parent_id"] is not None:
        raise R0BValidationRefused(
            "the publication recovery proof note parent must be null")
    if note["record_parsed"] is not True:
        raise R0BValidationRefused(
            "the publication recovery proof note must parse as a complete "
            "CONTEXT_HANDOFF record")
    if note["observed_chars"] + 1 != note["rendered_chars"]:
        raise R0BValidationRefused(
            "the publication recovery proof note lengths do not encode "
            "exactly one removed terminal LF")
    original = _strict_keys(
        proof["original_create_recovery"],
        ("decision_digest", "proof_digest", "execution_binding_digest",
         "accepted_commit", "accepted_adapter_digest"),
        "proof.original_create_recovery")
    recovered_execution = data.get("execution_binding") or {}
    recovered_proof = data.get("recovery_proof") or {}
    recovered_decision = recovered_proof.get("decision") or {}
    if original["execution_binding_digest"] != digest(recovered_execution) or \
            original["proof_digest"] != recovered_proof.get("proof_digest") or \
            original["decision_digest"] != \
            recovered_decision.get("decision_digest"):
        raise R0BValidationRefused(
            "the publication recovery proof does not reference the exact "
            "committed create-recovery proof/decision/binding")
    if original["accepted_commit"] != \
            recovered_execution.get("accepted_execution_commit") or \
            original["accepted_adapter_digest"] != \
            recovered_execution.get("accepted_execution_adapter_digest"):
        raise R0BValidationRefused(
            "the publication recovery proof does not bind the accepted "
            "create-recovery execution identity")
    authority = _strict_keys(
        proof["execution_authority_historical"],
        ("commit", "adapter_digest", "resolver", "path", "ok"),
        "proof.execution_authority_historical")
    if authority["commit"] != PREDECESSOR_FORWARD_COMMIT or \
            authority["adapter_digest"] != PREDECESSOR_FORWARD_ADAPTER_DIGEST \
            or authority["resolver"] not in EXECUTION_RESOLVERS or \
            authority["path"] != ADAPTER_MODULE or authority["ok"] is not True:
        raise R0BValidationRefused(
            "the publication recovery proof does not carry the exact "
            "historical da99c11 execution authority")
    observations = _strict_keys(
        proof["observations"],
        ("target_issue", "target_recheck", "comments", "activities", "runs",
         "raw_responses", "digests"),
        "proof.observations")
    digests = _strict_keys(
        observations["digests"],
        ("target_issue", "target_recheck", "comments", "activities", "runs",
         "raw_responses"),
        "proof.observations.digests")
    sections = {
        "target_issue": (observations["target_issue"], dict),
        "target_recheck": (observations["target_recheck"], dict),
        "comments": (observations["comments"], list),
        "activities": (observations["activities"], list),
        "runs": (observations["runs"], list),
        "raw_responses": (observations["raw_responses"], list),
    }
    for name, (body, kind) in sections.items():
        if not isinstance(body, kind) or digests[name] != digest(body):
            raise R0BValidationRefused(
                f"the publication recovery proof {name} observations are "
                "missing, edited, truncated or do not recompute")
    if observations["target_issue"].get("id") != target["issue_id"] or \
            observations["target_recheck"].get("id") != target["issue_id"]:
        raise R0BValidationRefused(
            "the publication recovery proof target observations do not name "
            "the bound target")
    for response in observations["raw_responses"]:
        entry = _strict_keys(
            response, ("argv", "exit_code", "stdout", "stdout_digest"),
            "proof.observations.raw_responses[]")
        if not isinstance(entry["argv"], list) or \
                not all(isinstance(a, str) for a in entry["argv"]) or \
                entry["exit_code"] != 0:
            raise R0BValidationRefused(
                "a publication recovery proof raw response is malformed or "
                "was not a successful read")
        if not isinstance(entry["stdout"], str) or \
                entry["stdout_digest"] != _sha256_utf8(entry["stdout"]):
            raise R0BValidationRefused(
                "a publication recovery proof raw read response digest does "
                "not reproduce")
    shared = _strict_keys(
        proof["shared_history"],
        ("interval", "audited_prefix", "records", "create_pair",
         "ownership_pair", "publication_pair", "classification_digest"),
        "proof.shared_history")
    if not isinstance(shared["records"], list) or not shared["records"]:
        raise R0BValidationRefused(
            "the publication recovery proof carries no inline shared-history "
            "classification; a hash/count-only proof is not accepted")
    if shared["classification_digest"] != publication_history_digest(shared):
        raise R0BValidationRefused(
            "the publication recovery proof shared-history classification "
            "does not recompute; the proof is edited or incomplete")
    for entry in shared["records"]:
        item = _strict_keys(
            entry, ("seq", "digest", "classification", "reason", "record"),
            "proof.shared_history.records[]")
        if item["digest"] != digest(item["record"]):
            raise R0BValidationRefused(
                "a publication recovery proof shared-history record digest "
                "does not reproduce")
    for pair_name in ("create_pair", "ownership_pair"):
        pair = shared[pair_name]
        if pair is not None:
            pair = _strict_keys(
                pair,
                ("command_seq", "command_digest", "result_seq",
                 "result_digest"),
                f"proof.shared_history.{pair_name}")
            if pair["result_seq"] != pair["command_seq"] + 1:
                raise R0BValidationRefused(
                    f"the publication recovery proof {pair_name} is not the "
                    "uniquely correlated adjacent pair")
            for key in ("command_digest", "result_digest"):
                _require_digest(pair[key],
                                f"proof.shared_history.{pair_name}.{key}")
    publication_pair = shared["publication_pair"]
    if publication_pair is not None:
        publication_pair = _strict_keys(
            publication_pair,
            ("command_seq", "command_digest", "result_seq", "result_digest"),
            "proof.shared_history.publication_pair")
        if publication_pair["result_seq"] != \
                publication_pair["command_seq"] + 1:
            raise R0BValidationRefused(
                "the publication command pair is not adjacent")
        for key in ("command_digest", "result_digest"):
            _require_digest(publication_pair[key],
                            f"proof.shared_history.publication_pair.{key}")
    material = _strict_keys(
        proof["material"],
        ("artifact_dependency_digest", "artifact_recheck_ok",
         "authority", "fingerprint", "self_check", "source_join"),
        "proof.material")
    if material["artifact_dependency_digest"] != \
            (data.get("artifact_dependency") or {}).get("digest"):
        raise R0BValidationRefused(
            "the publication recovery proof artifact dependency does not "
            "match the intent record")
    if material["artifact_recheck_ok"] is not True:
        raise R0BValidationRefused(
            "the publication recovery proof does not carry a true artifact "
            "recheck")
    fingerprint = _strict_keys(
        material["fingerprint"], ("task_fingerprint", "built_from_digest"),
        "proof.material.fingerprint")
    _require_digest(fingerprint["task_fingerprint"],
                    "proof.material.fingerprint.task_fingerprint")
    _require_digest(fingerprint["built_from_digest"],
                    "proof.material.fingerprint.built_from_digest")
    self_check = _strict_keys(
        material["self_check"], ("status", "action", "package_id", "reasons"),
        "proof.material.self_check")
    if self_check["status"] != "READY" or \
            self_check["action"] != "USE_EXISTING":
        raise R0BValidationRefused(
            "the publication recovery proof SELF_CHECK is not "
            "READY/USE_EXISTING")
    join = _strict_keys(
        material["source_join"],
        ("creation_package_id", "execution_package_id", "envelope_digest",
         "parent_issue_id", "target_task_ref",
         "source_activation_comment_id", "source_activation_author_id",
         "resolution_raw_digest", "request_raw_digest",
         "reconstructed_request_digest", "task_fingerprint"),
        "proof.material.source_join")
    if join["execution_package_id"] != attempt["package_id"] or \
            join["envelope_digest"] != attempt["envelope_digest"]:
        raise R0BValidationRefused(
            "the publication recovery proof source parent/E join is broken")
    recorded_decision = _strict_keys(
        proof["decision"],
        ("schema", "decision_id", "disposition", "scope", "intent_id",
         "expected_target_id", "expected_intent_revision", "expected_blocker",
         "publication_attempt", "expected_note", "original_create_recovery",
         "source_activation", "execution_migration", "purpose",
         "trigger_policy", "design_ref", "design_digest", "human_approval_ref",
         "lead_approval_ref", "approval_ref", "approved_by", "approved_at",
         "decision_digest"),
        "proof.decision")
    decision = validate_publication_recovery_decision(recorded_decision)
    if proof["decision_digest"] != decision["decision_digest"]:
        raise R0BValidationRefused(
            "the publication recovery proof does not bind its decision")
    # -- the decision's exact incident objects must equal the inline proof ---
    if decision["intent_id"] != intent["intent_id"] or \
            decision["expected_target_id"] != target["issue_id"]:
        raise R0BValidationRefused(
            "the decision does not name this intent/target")
    if decision["expected_intent_revision"] != \
            proof["intent_revision_before"]:
        raise R0BValidationRefused(
            "the decision prior revision does not match the proof")
    expected_blocker = decision["expected_blocker"]
    if expected_blocker["seq"] != blocker["transition_seq"] or \
            expected_blocker["digest"] != blocker["transition_digest"] or \
            expected_blocker["reason"] != blocker["reason"] or \
            expected_blocker["evidence_code"] != blocker["evidence_code"] or \
            expected_blocker["evidence_event"]["seq"] != \
            blocker["evidence_event_seq"] or \
            expected_blocker["evidence_event"]["digest"] != \
            blocker["evidence_event_digest"]:
        raise R0BValidationRefused(
            "the decision blocker does not match the committed proof blocker")
    expected_attempt = decision["publication_attempt"]
    if expected_attempt["seq"] != attempt["event_seq"] or \
            expected_attempt["digest"] != attempt["event_digest"] or \
            expected_attempt["operation_id"] != attempt["operation_id"]:
        raise R0BValidationRefused(
            "the decision publication attempt does not match the committed "
            "proof attempt")
    expected_note = decision["expected_note"]
    for key in ("note_comment_id", "revision", "created_at", "updated_at",
                "author_id", "author_type", "source_task_id", "parent_id"):
        if expected_note[key] != note[key]:
            raise R0BValidationRefused(
                f"the decision expected note {key} does not match the "
                "committed proof")
    if expected_note["rendered_raw_digest"] != note["rendered_raw_digest"] or \
            expected_note["observed_raw_digest"] != note["observed_raw_digest"]:
        raise R0BValidationRefused(
            "the decision note raw digests do not match the committed proof")
    expected_original = decision["original_create_recovery"]
    if expected_original["decision_digest"] != original["decision_digest"] or \
            expected_original["proof_digest"] != original["proof_digest"] or \
            expected_original["execution_binding_digest"] != \
            original["execution_binding_digest"] or \
            expected_original["accepted_commit"] != \
            original["accepted_commit"] or \
            expected_original["accepted_adapter_digest"] != \
            original["accepted_adapter_digest"]:
        raise R0BValidationRefused(
            "the decision original create-recovery references do not match "
            "the committed proof")
    expected_migration = decision["execution_migration"]
    if expected_migration["adapter_lf_digest"] != adapter_digest() or \
            expected_migration["adapter_raw_digest"] != adapter_raw_digest():
        raise R0BValidationRefused(
            "the decision execution migration does not name these executing "
            "adapter bytes")
    # -- the one shared pure semantic recheck (writer AND reducer/restart) ---
    validate_publication_recovery_semantics(
        proof=proof, intent=intent, data=data, target=target, blocker=blocker,
        attempt=attempt, note_doc=note, observations=observations,
        shared=shared, decision=decision)
    recomputed = digest({k: v for k, v in proof.items()
                         if k != "proof_digest"})
    if proof["proof_digest"] != recomputed:
        raise R0BValidationRefused(
            "the publication recovery proof digest does not reproduce; the "
            "record is edited or incomplete",
            expected=recomputed, found=proof["proof_digest"])
    return dict(proof)


def publication_history_digest(shared: dict) -> str:
    """Recomputable digest of the serialized publication-history form."""
    body = {key: shared.get(key) for key in (
        "interval", "audited_prefix", "records", "create_pair",
        "ownership_pair", "publication_pair")}
    return digest(body)


def _publication_commit_fields(intent: dict, data: dict, proof: dict,
                               migration: dict, *, actor: str,
                               now: str) -> dict:
    """The exact new-state fields of the single recovery commit.

    Only publication-state fields and the namespaced R0B binding may change.
    The original execution binding, recovery proof, creation spec, E, attempt
    digests and every historical section are preserved byte-for-byte.
    """
    binding = dict(data)
    binding["phase"] = "HANDOFF_PUBLISHED"
    binding["publication_binding"] = {
        "schema": PUBLICATION_BINDING_SCHEMA,
        "contract_version": CONTRACT_VERSION,
        "issue_id": proof["target"]["issue_id"],
        "package_id": proof["attempt"]["package_id"],
        "envelope_digest": proof["attempt"]["envelope_digest"],
        "note_comment_id": proof["note"]["note_comment_id"],
        "note_revision": proof["note"]["revision"],
        "author_id": proof["note"]["author_id"],
        "author_type": proof["note"]["author_type"],
        "source_task_id": proof["note"]["source_task_id"],
        "parent_id": proof["note"]["parent_id"],
        "body_digest": proof["note"]["observed_raw_digest"],
        "body_digest_method": BODY_DIGEST_METHOD_RAW,
        "rendered_body_digest_raw": proof["note"]["rendered_raw_digest"],
        "rendered_body_digest_lf": proof["note"]["rendered_lf_digest"],
        "observed_body_digest_lf": proof["note"]["observed_lf_digest"],
        "relation": proof["note"]["relation"],
        "transport_profile": None,
        "operation_id": proof["attempt"]["operation_id"],
        "publisher_run_id": proof["attempt"]["publisher_run_id"],
        "prepared_by": proof["attempt"]["prepared_by"],
        "prepared_at": proof["attempt"]["prepared_at"],
        "before_issue_revision": proof["attempt"]["before_issue_revision"],
        "after_issue_revision":
            (proof["observations"]["target_issue"] or {}).get("revision"),
        "recovered": True,
    }
    binding["publication_recovery_proof"] = dict(proof)
    binding["publication_execution_migration"] = dict(migration)
    target = dict(binding.get("target_binding") or {})
    after_issue = proof["observations"]["target_issue"] or {}
    target.update({
        "post_publication_revision": after_issue.get("revision"),
        "post_publication_snapshot_digest":
            digest(issue_projection(after_issue)),
        "post_publication_projection": issue_projection(after_issue),
    })
    binding["target_binding"] = target
    allowed_top = {
        "note_comment_id", "publish_receipt_digest", "published_at",
        "expected_issue_revision", "expected_status_category",
        "expected_assignee_id", R0B_FIELD,
    }
    fields = {
        "note_comment_id": proof["note"]["note_comment_id"],
        "publish_receipt_digest": proof["proof_digest"],
        "published_at": now,
        "expected_issue_revision":
            (proof["observations"]["target_issue"] or {}).get("revision"),
        "expected_status_category": "backlog",
        "expected_assignee_id": intent["fields"].get("expected_assignee_id"),
        R0B_FIELD: binding,
    }
    return fields, allowed_top


def validate_publication_recovery_commit_record(record: dict,
                                                intent: dict) -> dict:
    """Shared writer/reducer validation of one publication recovery commit.

    The writer validates before the single fsync append; the reducer runs the
    identical predicate while folding the ledger. Ordinary
    `transition(BLOCKED, ...)` remains refused: this op is the only entry and
    it must bind the exact incident, revision CAS, complete proof and
    migration, single consumption and old-field immutability.
    """
    fields = (
        "seq", "kind", "record_type", "schema_version", "op", "intent_id",
        "from", "to", "revision", "actor", "at", "publication_recovery_proof",
        "publication_execution_migration", "fields",
    )
    record = _require_dict(record, "publication recovery commit")
    extras = sorted(set(record) - set(fields))
    missing = sorted(set(fields) - {"seq"} - set(record))
    if extras or missing:
        raise R0BValidationRefused(
            "publication recovery commit must stay within the exact schema",
            extras=extras, missing=missing)
    if record["kind"] != "intent" or \
            record["record_type"] != o2.INTENT_RECORD_TYPE or \
            record["schema_version"] != o2.O2_SCHEMA or \
            record["op"] != PUBLICATION_RECOVERY_OP:
        raise R0BValidationRefused(
            "the recovery record is not the exact versioned O2 extension op")
    if record["intent_id"] != intent["intent_id"]:
        raise R0BValidationRefused(
            "the recovery commit intent_id does not match")
    if record["from"] != o2.S_BLOCKED or record["to"] != o2.S_HANDOFF_PUBLISHED:
        raise R0BValidationRefused(
            "only the single BLOCKED -> HANDOFF_PUBLISHED recovery commit is "
            "authorized", **{"from": record["from"], "to": record["to"]})
    if intent["state"] != o2.S_BLOCKED:
        raise R0BValidationRefused(
            "the recovery commit requires the exact prior BLOCKED phase",
            state=intent["state"])
    if record["revision"] != intent["revision"] + 1:
        raise R0BValidationRefused(
            "the recovery commit revision is not the exact single increment")
    _require_text(record["actor"], "recovery commit actor")
    _require_text(record["at"], "recovery commit at")
    data = intent["fields"].get(R0B_FIELD) or {}
    if data.get("publication_recovery_proof") is not None or \
            data.get("publication_execution_migration") is not None:
        raise R0BValidationRefused(
            "a publication recovery proof/commit already exists for this "
            "intent; a second commit is refused")
    proof = validate_publication_recovery_proof(
        record["publication_recovery_proof"], intent=intent, data=data,
        applying=True)
    migration_data = dict(data)
    migration_data["publication_recovery_proof"] = \
        record["publication_recovery_proof"]
    migration = validate_publication_execution_migration(
        intent, migration_data, record["publication_execution_migration"],
        applying=True)
    expected_migration = (proof.get("decision") or {}).get(
        "execution_migration") or {}
    if migration["new_commit"] != expected_migration.get("accepted_commit") or \
            migration["new_tree"] != expected_migration.get("accepted_tree") or \
            migration["new_adapter_raw_digest"] != \
            expected_migration.get("adapter_raw_digest") or \
            migration["new_adapter_lf_digest"] != \
            expected_migration.get("adapter_lf_digest") or \
            migration["store_file_digest"] != \
            expected_migration.get("store_file_digest") or \
            migration["note_file_digest"] != \
            expected_migration.get("note_file_digest") or \
            migration["artifact_dependency_digest"] != \
            expected_migration.get("artifact_dependency_digest"):
        raise R0BValidationRefused(
            "the commit migration does not equal the decision's accepted "
            "execution identities")
    if migration["migration_decision_digest"] != proof["decision_digest"]:
        raise R0BValidationRefused(
            "the commit migration and publication proof do not share the "
            "single Lead decision")
    new_fields, allowed_top = _publication_commit_fields(
        intent, data, proof, migration, actor=record["actor"],
        now=record["at"])
    fields_body = record["fields"]
    if not isinstance(fields_body, dict):
        raise R0BValidationRefused("the recovery commit fields must be an "
                                   "object")
    extras = sorted(set(fields_body) - allowed_top)
    if extras:
        raise R0BValidationRefused(
            "the recovery commit would change unapproved top-level fields",
            fields=extras)
    binding = fields_body.get(R0B_FIELD)
    if not isinstance(binding, dict):
        raise R0BValidationRefused(
            "the recovery commit carries no namespaced binding")
    for key, old_value in data.items():
        if key in ("phase", "publication_binding", "publication_recovery_proof",
                   "publication_execution_migration", "target_binding"):
            continue
        if canonical_json(binding.get(key)) != canonical_json(old_value):
            raise R0BValidationRefused(
                "the recovery commit would rewrite a preserved original "
                "binding section", section=key)
    old_target = data.get("target_binding") or {}
    new_target = binding.get("target_binding")
    if not isinstance(new_target, dict):
        raise R0BValidationRefused(
            "the recovery commit carries no target binding")
    target_extras = sorted(set(new_target) - set(old_target) - {
        "post_publication_revision", "post_publication_snapshot_digest",
        "post_publication_projection"})
    if target_extras:
        raise R0BValidationRefused(
            "the recovery commit would add unapproved target-binding fields",
            fields=target_extras)
    for key, old_value in old_target.items():
        if canonical_json(new_target.get(key)) != canonical_json(old_value):
            raise R0BValidationRefused(
                "the recovery commit would rewrite a preserved target-binding "
                "field", field=key)
    after_issue = proof["observations"]["target_issue"] or {}
    if new_target.get("post_publication_revision") != \
            after_issue.get("revision") or \
            canonical_json(new_target.get("post_publication_projection")) != \
            canonical_json(issue_projection(after_issue)):
        raise R0BValidationRefused(
            "the recovery commit target baseline does not match the committed "
            "observation")
    if canonical_json(binding.get("execution_binding")) != \
            canonical_json(data.get("execution_binding")) or \
            canonical_json(binding.get("recovery_proof")) != \
            canonical_json(data.get("recovery_proof")) or \
            canonical_json(binding.get("creation_spec")) != \
            canonical_json(data.get("creation_spec")):
        raise R0BValidationRefused(
            "the recovery commit would rewrite the preserved original "
            "execution binding, recovery proof or creation spec")
    if binding.get("phase") != "HANDOFF_PUBLISHED":
        raise R0BValidationRefused(
            "the recovery commit binding phase is not HANDOFF_PUBLISHED")
    if canonical_json(binding) != canonical_json(new_fields[R0B_FIELD]):
        raise R0BValidationRefused(
            "the recovery commit namespaced binding is not the exact "
            "proof/migration-bound HANDOFF_PUBLISHED binding")
    return {"proof": proof, "migration": migration, "fields": new_fields}


def fold_publication_recovery_commit(record: dict, intent: dict,
                                     intents: dict) -> None:
    """Reducer for the single versioned publication recovery commit op.

    Runs the exact same complete validation as the writer and then applies
    the committed state/revision delta. Any failure is ledger corruption: the
    record is never silently ignored, and a second commit for the same intent
    fails closed instead of replaying it as a state change.
    """
    if any(item.get("op") == PUBLICATION_RECOVERY_OP
           for item in intent.get("recovery_commits") or []):
        raise o2.LedgerCorruptionError(
            "duplicate publication recovery commit for this intent",
            intent_id=intent["intent_id"], seq=record.get("seq"))
    try:
        validated = validate_publication_recovery_commit_record(record, intent)
    except R0BError as exc:
        raise o2.LedgerCorruptionError(
            "publication recovery commit failed strict validation; fail "
            f"closed: {exc.message}", intent_id=intent["intent_id"],
            seq=record.get("seq")) from exc
    intent["state"] = record["to"]
    intent["revision"] = record["revision"]
    if isinstance(record.get("fields"), dict):
        intent["fields"].update(record["fields"])
    intent.setdefault("recovery_commits", []).append(record)
    intent["updated_at"] = record.get("at") or intent["updated_at"]
    return validated


o2.register_extension_op(PUBLICATION_RECOVERY_OP,
                         fold_publication_recovery_commit)


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
            _validate_execution_binding(intent, data, allow_migration=True)
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
        "content_raw_digest": _sha256_utf8(doc.get("content") or ""),
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

    profile = expected.get("transport_profile")
    if profile is not None:
        if profile not in PUBLICATION_TRANSPORT_PROFILES:
            return fail(PUB_INCOMPLETE,
                        f"unsupported publication transport profile {profile}")
        t_raw = expected.get("transport_body_digest_raw")
        r_raw = expected.get("rendered_body_digest_raw")
        if not isinstance(t_raw, str) or not isinstance(r_raw, str):
            return fail(PUB_INCOMPLETE,
                        "the prepared rendered/transport byte identities are "
                        "missing for this versioned transport")
        relation_candidates = [
            c for c in after["comments"]
            if c.get("content_raw_digest") in (t_raw, r_raw)]
        if not relation_candidates:
            return fail(PUB_NOTE_NOT_FOUND,
                        "no publication is visible for this attempt")
        if len(relation_candidates) > 1:
            return fail(PUB_DUPLICATE_NOTE,
                        f"{len(relation_candidates)} notes match the "
                        "rendered/transport byte identities (duplicate note or "
                        "an R/T pair); never choose the latest")
        candidate = relation_candidates[0]
        if candidate.get("content_raw_digest") != t_raw:
            return fail(PUB_INCOMPLETE,
                        "the visible note is not the prepared transport body "
                        "(O != T); no normalization fallback exists")
        observed_text = _content_of(after, candidate["id"])
        if _sha256_utf8(observed_text) != t_raw:
            return fail(PUB_INCOMPLETE,
                        "the visible note bytes do not reproduce the prepared "
                        "transport digest")
        matches = relation_candidates
    else:
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

    proof = {
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
    }
    if profile is not None:
        proof.update({
            "body_digest_method": BODY_DIGEST_METHOD_RAW,
            "transport_profile": profile,
            "transport_body_digest_raw":
                expected.get("transport_body_digest_raw"),
            "rendered_body_digest_raw":
                expected.get("rendered_body_digest_raw"),
            "observed_relation": "exact-transport",
        })
    return {"ok": True, "code": PUB_OK, "detail": "", "proof": proof}


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
    method = publication.get("body_digest_method", BODY_DIGEST_METHOD_LF)
    problems = []
    if method == BODY_DIGEST_METHOD_RAW:
        if candidate.get("content_raw_digest") != body_digest:
            problems.append("raw body digest")
    elif method == BODY_DIGEST_METHOD_LF:
        if candidate.get("content_digest") != body_digest:
            problems.append("body digest")
    else:
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
            f"the bound publication body digest method {method!r} is "
            "unsupported; no normalization fallback exists", subject="note")
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
    if method == BODY_DIGEST_METHOD_RAW:
        related = {body_digest}
        rendered_raw = publication.get("rendered_body_digest_raw")
        if isinstance(rendered_raw, str):
            related.add(rendered_raw)
        matching = [c for c in evidence.get("comments") or []
                    if c.get("content_raw_digest") in related]
    else:
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
        prepared = prepare_publication_transport_body(body)
        transport_body = prepared["transport_body"]
        attempt = {
            "contract_version": CONTRACT_VERSION,
            "transport_profile": prepared["profile"],
            "transport_relation": prepared["relation"],
            "operation_id": new_operation_id("publication", {
                "intent_id": intent_id,
                "rendered_body_digest_raw":
                    prepared["rendered_body_digest_raw"],
                "transport_body_digest_raw":
                    prepared["transport_body_digest_raw"]}),
            "package_id": execution["package_id"],
            "envelope_digest": execution["envelope_digest"],
            "body_digest": prepared["rendered_body_digest_lf"],
            "body_digest_method": BODY_DIGEST_METHOD_LF,
            "rendered_body_digest_lf": prepared["rendered_body_digest_lf"],
            "rendered_body_digest_raw": prepared["rendered_body_digest_raw"],
            "transport_body_digest_raw":
                prepared["transport_body_digest_raw"],
            "transport_body_digest_lf": prepared["transport_body_digest_lf"],
            "rendered_chars": prepared["rendered_chars"],
            "rendered_utf8_bytes": prepared["rendered_utf8_bytes"],
            "transport_chars": prepared["transport_chars"],
            "transport_utf8_bytes": prepared["transport_utf8_bytes"],
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
                    parent_comment_id=parent_comment_id, cli=self.note_cli,
                    transport_body=transport_body)
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
            sent_hex = str(published.get("body_sha256") or "")
            if published.get("published") and \
                    sent_hex != prepared["transport_body_digest_raw"][7:]:
                self.store.append_event(
                    intent_id, E_PUBLICATION_UNCERTAIN, actor=actor,
                    now=self.now(),
                    data={"operation_id": attempt["operation_id"],
                          "error": "the sent transport bytes differ from the "
                                   "verified prepared transport body",
                          "retry": False})
                return {
                    "status": intent["state"],
                    "outcome": "PUBLICATION_UNCERTAIN",
                    "reason": REASON_PUBLICATION_AMBIGUOUS,
                    "detail": "the publisher did not send the verified exact "
                              "transport bytes; no retry and no second "
                              "publication",
                    "intent_id": intent_id, "side_effects": 0,
                }
            self.store.append_event(
                intent_id, E_PUBLICATION_RESPONSE, actor=actor, now=self.now(),
                data={"operation_id": attempt["operation_id"],
                      "published": bool(published.get("published")),
                      "idempotent": bool(published.get("idempotent")),
                      "comment_id": (published.get("comment") or {}).get("id"),
                      "body_sha256": published.get("body_sha256"),
                      "transport_profile": attempt["transport_profile"],
                      "transport_body_digest_raw":
                          attempt["transport_body_digest_raw"],
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
            "transport_profile": attempt.get("transport_profile"),
            "transport_body_digest_raw":
                attempt.get("transport_body_digest_raw"),
            "rendered_body_digest_raw":
                attempt.get("rendered_body_digest_raw"),
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
        publication_binding = dict(proof, **{
            "issue_id": binding_issue_id(data),
            "package_id": execution["package_id"],
            "task_ref": execution["task_ref"],
            "role": execution["role"],
            "publisher_run_id": attempt.get("publisher_run_id"),
            "prepared_by": attempt.get("prepared_by"),
            "prepared_at": attempt.get("prepared_at"),
            "operation_id": attempt.get("operation_id"),
        })
        if attempt.get("transport_profile") is not None:
            publication_binding.update({
                "schema": PUBLICATION_BINDING_SCHEMA,
                "contract_version": CONTRACT_VERSION,
                "body_digest": attempt["transport_body_digest_raw"],
                "body_digest_method": BODY_DIGEST_METHOD_RAW,
                "rendered_body_digest_raw":
                    attempt["rendered_body_digest_raw"],
                "rendered_body_digest_lf":
                    attempt["rendered_body_digest_lf"],
                "observed_body_digest_lf":
                    attempt["transport_body_digest_lf"],
                "relation": PUBLICATION_RELATION_DIRECTIONAL,
                "before_issue_revision": attempt.get("before_issue_revision"),
                "recovered": False,
            })
        else:
            publication_binding.setdefault("body_digest_method",
                                           BODY_DIGEST_METHOD_LF)
        binding["publication_binding"] = publication_binding
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

    # -- step 9: approved proof-bearing publication recovery -----------------
    def _publication_recovery_inspection(self, intent_id: str) -> tuple:
        """Restricted historical inspection of the da99c11 recovered record.

        This is the only pre-commit path allowed to read the original
        create-recovery binding under the explicit historical da99c11 pin. It
        never grants general execution: normal load still refuses these bytes
        until the committed migration binds them, and an already-committed
        recovery is never inspected as if it were pending.
        """
        intent = self.store.get(intent_id)
        data = intent["fields"].get(R0B_FIELD)
        if not isinstance(data, dict):
            raise R0BContractError(
                "intent is not tagged with an R0B contract",
                intent_id=intent_id)
        if data.get("contract_version") != CONTRACT_VERSION:
            raise R0BValidationRefused(
                "publication recovery requires the exact current R0B contract "
                "version on the already create-recovered record",
                contract_version=data.get("contract_version"))
        if data.get("adapter_digest") != PREDECESSOR_ADAPTER_DIGEST:
            raise R0BDowngradeRefused(
                "publication recovery requires the exact known predecessor "
                "adapter pin preserved on the record",
                adapter_digest=data.get("adapter_digest"))
        if not isinstance(data.get("execution_binding"), dict):
            raise R0BValidationRefused(
                "publication recovery requires the committed forward "
                "create-recovery execution binding")
        _validate_execution_binding(
            intent, data, executing=PREDECESSOR_FORWARD_ADAPTER_DIGEST)
        if data.get("publication_recovery_proof") is not None or \
                data.get("publication_execution_migration") is not None:
            raise R0BValidationRefused(
                "a publication recovery proof/migration is already committed; "
                "use the committed replay path, never a second commit")
        if data.get("publication_binding") is not None:
            raise R0BValidationRefused(
                "the record already carries a publication binding; recovery "
                "never rebinds or overwrites")
        return intent, data

    def recover_blocked_publication(self, intent_id: str, *,
                                    decision: dict,
                                    accepted_execution: dict,
                                    actor: str,
                                    current_findings: list | None = None,
                                    authority_evidence: dict | None = None
                                    ) -> dict:
        """Recover this exact publication incident to HANDOFF_PUBLISHED.

        Factory-only, single-use: platform access is read-only, and the only
        durable effects are the bounded evidence event plus the versioned
        `publication_recovery_commit_v1` record that atomically moves
        BLOCKED revision 4 to HANDOFF_PUBLISHED revision 5 and binds the
        execution-identity migration. There is no note resend, no new E, no
        create recovery, no assign/status/rerun and no ARM/TRIGGER.
        """
        decision = validate_publication_recovery_decision(decision)
        if decision["intent_id"] != intent_id:
            raise R0BValidationRefused(
                "the publication recovery disposition names a different "
                "intent", disposition=decision["intent_id"],
                operation=intent_id)
        accepted = dict(_require_dict(accepted_execution,
                                      "accepted_execution"))
        if canonical_json(accepted) != \
                canonical_json(decision["execution_migration"]):
            raise R0BValidationRefused(
                "the accepted execution input does not equal the disposition's "
                "accepted migration identities")
        intent = self.store.get(intent_id)
        data = intent["fields"].get(R0B_FIELD)
        if intent["state"] == o2.S_HANDOFF_PUBLISHED and \
                isinstance(data, dict) and \
                isinstance(data.get("publication_recovery_proof"), dict):
            return self._replay_publication_recovery(intent, data, decision,
                                                     actor)
        intent, data = self._publication_recovery_inspection(intent_id)
        try:
            bundle = self._publication_recovery_prerequisites(
                intent, data, decision=decision,
                current_findings=current_findings,
                authority_evidence=authority_evidence)
        except PreflightRefusal as refusal:
            return self._publication_recovery_refusal(intent_id, refusal,
                                                      actor)
        proof = self._build_publication_recovery_proof(
            intent, data, decision, bundle)
        migration = self._build_publication_execution_migration(
            intent, data, decision, proof)
        record = {
            "kind": "intent",
            "record_type": o2.INTENT_RECORD_TYPE,
            "schema_version": o2.O2_SCHEMA,
            "op": PUBLICATION_RECOVERY_OP,
            "intent_id": intent_id,
            "from": o2.S_BLOCKED,
            "to": o2.S_HANDOFF_PUBLISHED,
            "revision": proof["commit_revision"],
            "actor": o2._require_text(actor, "actor"),
            "at": self.now(),
            "publication_recovery_proof": proof,
            "publication_execution_migration": migration,
        }
        fields, _allowed = _publication_commit_fields(
            intent, data, proof, migration, actor=record["actor"],
            now=record["at"])
        record["fields"] = fields
        validate_publication_recovery_commit_record(record, intent)
        claim = self._claim(intent_id, actor)
        try:
            tail_digests = []
            if isinstance(claim.get("record"), dict):
                tail_digests.append(digest(claim["record"]))
            evidence_event = self.store.append_event(
                intent_id, E_PUBLICATION_RECOVERY_EVIDENCE, actor=actor,
                now=self.now(),
                data={"stage": "pre_commit",
                      "proof_digest": proof["proof_digest"],
                      "decision_digest": decision["decision_digest"],
                      "note_comment_id": proof["note"]["note_comment_id"]})
            tail_digests.append(digest(evidence_event))
            try:
                committed = self._commit_publication_recovery(
                    intent_id, record=record,
                    expected_revision=proof["intent_revision_before"],
                    expected_tail_digests=tail_digests,
                    prefix_count=bundle["ledger_prefix_count"],
                    prefix_raw_digest=bundle["ledger_prefix_raw_digest"])
            except PreflightRefusal as refusal:
                return self._publication_recovery_refusal(intent_id, refusal,
                                                          actor)
            return {
                "status": o2.S_HANDOFF_PUBLISHED,
                "intent_id": intent_id,
                "outcome": "PUBLICATION_RECOVERED",
                "revision": committed.get("revision"),
                "note_comment_id": proof["note"]["note_comment_id"],
                "proof_digest": proof["proof_digest"],
                "migration_digest": migration["migration_digest"],
                "platform_writes": 0,
                "ledger_commits": 1,
                "notes_sent": 0,
                "triggers_issued": 0,
                "next_action": "LEAD_DECISION_ARM_OR_STOP",
            }
        finally:
            self._release(intent_id, actor)

    def _replay_publication_recovery(self, intent, data, decision,
                                     actor) -> dict:
        proof = data.get("publication_recovery_proof") or {}
        # Restart/replay runs the one shared pure semantic verifier again;
        # an already-committed record is never trusted by its digest alone.
        validate_publication_recovery_proof(
            proof, intent=intent, data=data, replay=True)
        committed = proof.get("decision") or {}
        if committed.get("decision_digest") != decision["decision_digest"]:
            raise R0BValidationRefused(
                "a different publication recovery decision is already "
                "committed; competing proof is refused",
                committed=committed.get("decision_digest"),
                supplied=decision["decision_digest"])
        return {
            "status": o2.S_HANDOFF_PUBLISHED,
            "intent_id": intent["intent_id"],
            "outcome": "PUBLICATION_RECOVERED",
            "revision": intent["revision"],
            "note_comment_id": (data.get("publication_binding") or {}).get(
                "note_comment_id"),
            "proof_digest": proof.get("proof_digest"),
            "migration_digest": (data.get("publication_execution_migration")
                                 or {}).get("migration_digest"),
            "replayed": True,
            "platform_writes": 0,
            "ledger_commits": 0,
            "notes_sent": 0,
            "triggers_issued": 0,
            "next_action": "LEAD_DECISION_ARM_OR_STOP",
        }

    def _publication_recovery_refusal(self, intent_id: str,
                                      refusal: PreflightRefusal,
                                      actor: str) -> dict:
        """Typed zero-native-effect refusal: the state stays BLOCKED."""
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
                  "window": "publication_recovery"})
        return {"status": o2.S_BLOCKED, "intent_id": intent_id,
                "outcome": "RECOVERY_REFUSED", "reason": refusal.reason,
                "detail": refusal.detail, "platform_writes": 0,
                "ledger_commits": 0, "recovered": False}

    @staticmethod
    def _publication_record_class(record: dict, previous, *, intent_id: str,
                                  spec: dict, target_id: str) -> dict:
        """Classify one publication-history record; refusal is the default.

        The accepted create and ownership commands survive as proven history;
        at most one `issue comment add` command/result pair is admitted as the
        single historical publication; reads and this intent's own records
        stay allowed. Any other write/trigger class, unrecognized verb, orphan
        or unpaired result is unresolved evidence and stops.
        """
        entry = {"seq": record.get("seq"), "digest": digest(record),
                 "record": record, "classification": None, "reason": None}
        if record.get("record_type") == o2.INTENT_RECORD_TYPE:
            if record.get("intent_id") != intent_id:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "a foreign dispatch-intent record lies in the relevant "
                    "publication interval; its effects cannot be proven "
                    "disjoint", subject="shared_history",
                    record_seq=record.get("seq"))
            if record.get("op") == PUBLICATION_RECOVERY_OP:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "a competing publication recovery commit record already "
                    "exists; no second commit", subject="shared_history",
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
                    not all(isinstance(a, str) for a in argv) or \
                    not isinstance(recorded, str):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "a shared command record carries a malformed argv or no "
                    "recorded class; it is never treated as a read",
                    subject="shared_history", record_seq=record.get("seq"))
            derived = o2.classify_o2_command(argv, "multica")
            if recorded != derived:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
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
                entry.update(classification=CLS_ORIGINAL_CREATE_COMMAND,
                             reason="structured argv fully matches the "
                                    "retained creation spec")
                return entry
            if derived == o2.C_OWNERSHIP_BINDING:
                if not o2._valid_binding_argv(argv, "multica"):
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                        "the shared ownership-binding command is not the "
                        "accepted exact argv form", subject="shared_history",
                        record_seq=record.get("seq"))
                entry.update(classification=CLS_OWNERSHIP_COMMAND,
                             reason="exact accepted no-start ownership "
                                    "binding argv")
                return entry
            if derived == o2.C_MENTION_TRIGGER:
                core = o2._core_argv(argv, "multica")
                name = re.split(r"[\\/]", str(
                    core[core.index("--content-file") + 1]))[-1] \
                    if "--content-file" in core else ""
                if tuple(core[:3]) != ("issue", "comment", "add") or \
                        len(core) < 4 or core[3] != target_id or \
                        not name.startswith(".t06-note-") or \
                        not name.endswith(".md"):
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                        "the shared comment-add command is not the exact "
                        "accepted publisher form for this target",
                        subject="shared_history", record_seq=record.get("seq"))
                entry.update(
                    classification=CLS_PUBLICATION_COMMAND,
                    reason="the single publication publisher argv (exact "
                           "target, --content-file with the publisher temp "
                           "name)")
                return entry
            if derived in o2.WRITE_COMMAND_CLASSES:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "a relevant prior native write/trigger attempt is "
                    "persisted in the shared ledger; a persisted attempt "
                    "dominates any current emptiness", subject="shared_history",
                    record_seq=record.get("seq"), command_class=derived,
                    argv=[str(a) for a in argv[:8]])
            if derived == o2.C_READ:
                entry.update(classification=CLS_READ_COMMAND,
                             reason="narrow known-read verb with matching "
                                    "recorded class")
                return entry
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "an unrecognized shared command verb is unresolved evidence; "
                "an unknown effect stops", subject="shared_history",
                record_seq=record.get("seq"), argv=[str(a) for a in argv[:8]])
        if kind == "command_result":
            if previous is None or previous["classification"] not in (
                    CLS_ORIGINAL_CREATE_COMMAND, CLS_OWNERSHIP_COMMAND,
                    CLS_PUBLICATION_COMMAND, CLS_READ_COMMAND):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "an orphan, duplicate or interleaved command result is "
                    "unresolved evidence; results are never paired by guess",
                    subject="shared_history", record_seq=record.get("seq"))
            command = previous["record"]
            if record.get("command_class") != command.get("command_class") or \
                    record.get("transaction_id") != \
                    command.get("transaction_id"):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "a command result does not correlate with its immediately "
                    "preceding command (class/transaction mismatch)",
                    subject="shared_history", record_seq=record.get("seq"))
            exit_code = record.get("exit_code")
            if previous["classification"] == CLS_ORIGINAL_CREATE_COMMAND:
                if exit_code != 0:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                        "the sole original create result is not the uniquely "
                        "paired successful result", subject="original_create",
                        record_seq=record.get("seq"))
                entry.update(classification=CLS_ORIGINAL_CREATE_RESULT,
                             reason="uniquely paired adjacent successful "
                                    "create result")
                return entry
            if previous["classification"] == CLS_OWNERSHIP_COMMAND:
                if exit_code != 0:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                        "the sole ownership-binding result is not the uniquely "
                        "paired successful result", subject="shared_history",
                        record_seq=record.get("seq"))
                entry.update(classification=CLS_OWNERSHIP_RESULT,
                             reason="uniquely paired adjacent successful "
                                    "ownership result")
                return entry
            if previous["classification"] == CLS_PUBLICATION_COMMAND:
                if exit_code != 0:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                        "the sole publication command result is not the "
                        "uniquely paired successful result",
                        subject="shared_history", record_seq=record.get("seq"))
                entry.update(classification=CLS_PUBLICATION_RESULT,
                             reason="uniquely paired adjacent successful "
                                    "publication result")
                return entry
            entry.update(classification=CLS_READ_RESULT,
                         reason="uniquely paired adjacent read result")
            return entry
        raise PreflightRefusal(
            o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
            "an unrecognized shared ledger record kind is unresolved evidence",
            subject="shared_history", record_seq=record.get("seq"))

    def _classify_publication_history(self, intent, spec, decision) -> dict:
        """Complete publication-interval classification with exact counts.

        The full interval from this intent's original record to the fresh tip
        is audited, shared records without `intent_id` included. Exactly one
        create pair, exactly one ownership pair and at most one publication
        command pair may exist; the durable publication issuing/response
        events must each occur exactly once; any trigger receipt, run
        correlation or publication-bound event refuses.
        """
        intent_id = intent["intent_id"]
        target_id = intent["fields"].get("issue_id") or ""
        lines, records = self._read_ledger_lines()
        if not records:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
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
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "no original intent record for this intent exists in the "
                "shared ledger", subject="ledger")
        if start > 0 and records[start - 1].get("kind") == "command":
            start -= 1
        entries = []
        for index in range(start, len(records)):
            previous = entries[-1] if entries else None
            entry = self._publication_record_class(
                records[index], previous, intent_id=intent_id, spec=spec,
                target_id=target_id)
            entry["index"] = index
            entries.append(entry)
        classes = [e["classification"] for e in entries]

        def class_entries(name):
            return [e for e in entries if e["classification"] == name]

        create_commands = class_entries(CLS_ORIGINAL_CREATE_COMMAND)
        create_results = class_entries(CLS_ORIGINAL_CREATE_RESULT)
        ownership_commands = class_entries(CLS_OWNERSHIP_COMMAND)
        ownership_results = class_entries(CLS_OWNERSHIP_RESULT)
        publication_commands = class_entries(CLS_PUBLICATION_COMMAND)
        publication_results = class_entries(CLS_PUBLICATION_RESULT)
        if len(create_commands) != 1 or len(create_results) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_RECOVERY_ORIGINAL_EVIDENCE,
                "the durable original create command/result pair is not "
                "uniquely present in the shared ledger",
                subject="original_create",
                commands=len(create_commands), results=len(create_results))
        if len(ownership_commands) != 1 or len(ownership_results) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the single accepted no-start ownership command/result pair is "
                "not uniquely present", subject="shared_history",
                commands=len(ownership_commands), results=len(ownership_results))
        if len(publication_commands) > 1 or len(publication_results) > 1 or \
                (len(publication_commands) != len(publication_results)):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the shared ledger carries more than one publication command "
                "pair or an unpaired publication command; a second "
                "publication is never accepted", subject="shared_history")
        attempt_events = [e for e in intent["events"]
                          if e.get("name") == E_PUBLICATION_ISSUING]
        response_events = [e for e in intent["events"]
                           if e.get("name") == E_PUBLICATION_RESPONSE]
        forbidden_events = sorted(
            e.get("name") for e in intent["events"]
            if e.get("name") in (E_PUBLICATION_BOUND, E_PUBLICATION_UNCERTAIN,
                                 "r0b_trigger_receipt", o2.E_TRIGGER_RECEIPT,
                                 o2.E_RUN_CORRELATION, o2.E_PARENT_WAKE,
                                 o2.E_EXECUTION_RECOVERY, o2.E_DISCOVERY,
                                 o2.E_RECONCILIATION))
        if len(attempt_events) != 1 or len(response_events) != 1 or \
                forbidden_events:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the intent does not carry exactly one publication attempt "
                "and one recorded response with no bound/triggered/uncertain "
                "state", subject="shared_history",
                attempts=len(attempt_events), responses=len(response_events),
                forbidden=forbidden_events[:4])
        audited_length = len(records)
        raw_prefix = _raw_prefix_digest(lines)
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
                "digest": raw_prefix,
                "raw_prefix_digest": raw_prefix,
            },
            "records": [
                {key: entry[key] for key in (
                    "seq", "digest", "classification", "reason", "record")}
                for entry in entries],
            "create_pair": {
                "command_seq": create_commands[0]["record"].get("seq"),
                "command_digest": create_commands[0]["digest"],
                "result_seq": create_results[0]["record"].get("seq"),
                "result_digest": create_results[0]["digest"],
            },
            "ownership_pair": {
                "command_seq": ownership_commands[0]["record"].get("seq"),
                "command_digest": ownership_commands[0]["digest"],
                "result_seq": ownership_results[0]["record"].get("seq"),
                "result_digest": ownership_results[0]["digest"],
            },
            "publication_pair": (
                {
                    "command_seq": publication_commands[0]["record"].get("seq"),
                    "command_digest": publication_commands[0]["digest"],
                    "result_seq": publication_results[0]["record"].get("seq"),
                    "result_digest": publication_results[0]["digest"],
                } if publication_commands else None),
        }
        shared["classification_digest"] = publication_history_digest(shared)
        return shared

    @staticmethod
    def _authority_content(parent_rows) -> dict:
        """Verify the actual approval/authority comment content, not refs."""
        out = {}
        for name, comment_id, author_id, author_type, digest_pin in (
                ("human", PUBLICATION_RECOVERY_HUMAN_APPROVAL_COMMENT_ID,
                 PUBLICATION_RECOVERY_HUMAN_APPROVAL_AUTHOR_ID, "member",
                 PUBLICATION_RECOVERY_HUMAN_APPROVAL_CONTENT_DIGEST),
                ("lead_boundary", PUBLICATION_RECOVERY_LEAD_APPROVAL_COMMENT_ID,
                 PUBLICATION_RECOVERY_LEAD_APPROVAL_AUTHOR_ID, "agent",
                 PUBLICATION_RECOVERY_LEAD_APPROVAL_CONTENT_DIGEST)):
            matches = [row for row in parent_rows
                       if row.get("id") == comment_id]
            if len(matches) != 1:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    f"the {name} approval record is missing or duplicated on "
                    "the parent", subject="authority", matches=len(matches))
            row = matches[0]
            if row.get("author_id") != author_id or \
                    row.get("author_type") != author_type or \
                    _sha256_utf8(row.get("content") or "") != digest_pin:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    f"the actual {name} approval comment does not match the "
                    "accepted author/content; a fixed reference string alone "
                    "is never authority", subject="authority")
            out[name] = {
                "comment_id": comment_id,
                "author_id": author_id,
                "author_type": author_type,
                "content_raw_digest": digest_pin,
                "comment": comment_record(row),
            }
        return out

    def _resolve_source_activation(self, intent, data, decision) -> dict:
        """Resolve the unique Lead-authored source activation and its inputs.

        Reads the parent record through the authenticated CLI only: the unique
        Lead-authored activation comment matching the actual E package/envelope
        identity, target and intent; the two attached artifacts by raw hash;
        the fixed approval/authority comments by exact author and content. Any
        missing, duplicate, edited, wrong-author or nonmatching record is a
        typed stop; nothing is selected by recency and no field is invented.
        """
        activation = decision["source_activation"]
        spec = data.get("creation_spec") or {}
        execution = data.get("execution_context") or {}
        envelope = execution.get("result")
        if not isinstance(envelope, dict):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the bound original E envelope is not available for source "
                "activation verification", subject="source_activation")
        if activation["parent_issue_id"] != spec.get("parent_issue_id"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the disposed source activation parent is not the recorded "
                "target parent", subject="source_activation")
        try:
            parent_rows = self.reader.comments_full(
                activation["parent_issue_id"])
        except Exception as exc:  # noqa: BLE001 - unreadable is a stop
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"the parent activation records are incomplete or unreadable: "
                f"{type(exc).__name__}: {exc}", subject="source_activation")
        matches = [row for row in parent_rows
                   if row.get("id") == activation["comment_id"]]
        if len(matches) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the parent activation record is missing or duplicated; the "
                "unique Lead-authored activation is required",
                subject="source_activation", matches=len(matches))
        comment = matches[0]
        if comment.get("author_id") != activation["author_id"] or \
                comment.get("author_type") != activation["author_type"] or \
                comment.get("author_id") != \
                PUBLICATION_SOURCE_ACTIVATION_AUTHOR_ID:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the parent activation record does not carry the exact Lead "
                "author identity", subject="source_activation")
        content = comment.get("content")
        if not isinstance(content, str) or \
                _sha256_utf8(content) != \
                activation["comment_content_raw_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the parent activation record content is edited; the disposed "
                "content digest does not reproduce",
                subject="source_activation")
        fingerprint = ((execution.get("built_from") or {})
                       .get("task_fingerprint"))
        package_id = execution.get("package_id")
        envelope_digest = digest(envelope)
        if package_id != activation["package_id"] or \
                envelope_digest != activation["envelope_digest"] or \
                fingerprint != activation["task_fingerprint"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the disposal E package/envelope/fingerprint does not match "
                "the bound original E", subject="source_activation")
        target_id = binding_issue_id(data)
        missing = [value for value in (
            package_id, envelope_digest, fingerprint, target_id,
            intent["intent_id"])
            if isinstance(value, str) and value and value not in content]
        for digest_value in (activation["resolution_raw_digest"],
                             activation["request_raw_digest"]):
            if not _digest_present(content, digest_value):
                missing.append(digest_value)
        if missing:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the parent activation record does not bind the exact E "
                "package/envelope/fingerprint/target/intent/attachments",
                subject="source_activation", missing=missing[:4])
        attachments = comment.get("attachments")
        if not isinstance(attachments, list):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the parent activation record carries no attachment listing",
                subject="source_activation")
        declared = {item.get("id") for item in attachments
                    if isinstance(item, dict)}
        if activation["resolution_attachment_id"] not in declared or \
                activation["request_attachment_id"] not in declared:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the parent activation record does not carry the disposed "
                "resolution and request attachments",
                subject="source_activation")
        try:
            resolution = self.reader.attachment_content(
                activation["resolution_attachment_id"])
            request_doc = self.reader.attachment_content(
                activation["request_attachment_id"])
        except PublicationProvenanceIncomplete as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE, exc.message,
                subject="source_activation")
        except Exception as exc:  # noqa: BLE001
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"the source activation attachments are unreadable: "
                f"{type(exc).__name__}: {exc}", subject="source_activation")
        if resolution["raw_digest"] != activation["resolution_raw_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the resolution attachment raw digest does not reproduce the "
                "disposition", subject="source_activation")
        if request_doc["raw_digest"] != activation["request_raw_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the request attachment raw digest does not reproduce the "
                "disposition", subject="source_activation")
        try:
            attached = json.loads(request_doc["text"])
        except json.JSONDecodeError as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                f"the request attachment is not JSON: {exc}",
                subject="source_activation")
        if not isinstance(attached, dict):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the request attachment is not an object",
                subject="source_activation")
        if canonical_json(attached) != \
                canonical_json(execution.get("request")):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the attached accepted request is not the exact bound E "
                "request", subject="source_activation")
        relevant = ((attached.get("task_snapshot") or {})
                    .get("relevant_decisions"))
        if not isinstance(relevant, list) or not relevant or \
                relevant[0] != resolution["text"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the attached request relevant_decisions does not carry the "
                "exact accepted resolution text", subject="source_activation")
        resolution_hex = resolution["raw_digest"].split(":", 1)[1]
        if not any(isinstance(item, str) and resolution_hex in item
                   for item in relevant[1:]):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the attached request relevant_decisions does not carry the "
                "resolution artifact raw digest", subject="source_activation")
        attached_fp = chandoff.fingerprint_from_request(attached)
        if attached_fp != fingerprint:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_STALE,
                "the attached request fingerprint does not reproduce the "
                "frozen E fingerprint", subject="source_activation")
        if chandoff.compute_built_from(attached) != \
                execution.get("built_from"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_STALE,
                "the attached request built_from does not reproduce the "
                "frozen E built_from", subject="source_activation")
        return {
            "comment": comment_record(comment),
            "resolution": {
                "attachment_id": resolution["attachment_id"],
                "filename": resolution["filename"],
                "raw_digest": resolution["raw_digest"],
                "chars": resolution["chars"],
                "utf8_bytes": resolution["utf8_bytes"],
                "text": resolution["text"],
            },
            "request": {
                "attachment_id": request_doc["attachment_id"],
                "filename": request_doc["filename"],
                "raw_digest": request_doc["raw_digest"],
                "chars": request_doc["chars"],
                "utf8_bytes": request_doc["utf8_bytes"],
                "text": request_doc["text"],
            },
            "attached_request": attached,
            "request_digest": digest(attached),
            "authority_approvals": self._authority_content(parent_rows),
        }

    def _publication_recovery_prerequisites(self, intent, data, *, decision,
                                            current_findings,
                                            authority_evidence) -> dict:
        """Collect and validate every live recovery precondition, in order.

        Read-only: current issue/comments/timeline/runs, the complete stable
        re-read, the exact note relation, the full before/after delta, the
        shared-ledger history, the pinned artifact/authority material, the
        frozen fingerprint/SELF_CHECK and the source parent/E join. Raises
        `PreflightRefusal`; nothing is mutated and no live call is issued.
        """
        intent_id = intent["intent_id"]
        expected_blocker = decision["expected_blocker"]
        responses_start = len(self.reader.responses)
        source_activation = self._resolve_source_activation(
            intent, data, decision)
        transitions = intent.get("transitions") or []
        if not transitions:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the intent carries no recorded transition", subject="blocker")
        blocker_transition = transitions[-1]
        if blocker_transition.get("to") != o2.S_BLOCKED or \
                blocker_transition.get("reason") != \
                REASON_PUBLICATION_PROVENANCE or \
                blocker_transition.get("seq") != expected_blocker["seq"] or \
                digest(blocker_transition) != expected_blocker["digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the recorded blocking transition is not the exact disposed "
                "BLOCKED transition", subject="blocker")
        evidence_events = [
            e for e in intent["events"]
            if e.get("name") == E_EVIDENCE_REFUSED and
            (e.get("data") or {}).get("reason") ==
            REASON_PUBLICATION_PROVENANCE and
            (e.get("data") or {}).get("code") == PUB_NOTE_NOT_FOUND]
        if len(evidence_events) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "expected exactly one blocking evidence event with code "
                f"{PUB_NOTE_NOT_FOUND}, found {len(evidence_events)}",
                subject="blocker")
        evidence_event = evidence_events[0]
        if evidence_event.get("seq") != \
                expected_blocker["evidence_event"]["seq"] or \
                digest(evidence_event) != \
                expected_blocker["evidence_event"]["digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the blocking evidence event does not match the disposition",
                subject="blocker")
        if intent["revision"] != decision["expected_intent_revision"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the intent revision is not the disposed BLOCKED revision",
                subject="blocker", revision=intent["revision"])
        attempts = [e for e in intent["events"]
                    if e.get("name") == E_PUBLICATION_ISSUING]
        attempt_event = attempts[0]
        attempt = attempt_event.get("data") or {}
        expected_attempt = decision["publication_attempt"]
        if attempt_event.get("seq") != expected_attempt["seq"] or \
                digest(attempt_event) != expected_attempt["digest"] or \
                attempt.get("operation_id") != expected_attempt["operation_id"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the sole publication attempt does not match the disposition",
                subject="attempt")
        execution = data.get("execution_context") or {}
        envelope = execution.get("result")
        if not isinstance(envelope, dict) or \
                not isinstance(execution.get("request"), dict):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                "the bound original E result/request is not available",
                subject="context")
        expected_note = decision["expected_note"]
        if attempt.get("publisher_run_id") != \
                expected_note["source_task_id"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the durable attempt source run does not match the disposed "
                "note source run", subject="attempt")
        if attempt.get("parent_comment_id") != expected_note["parent_id"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the durable attempt parent does not match the disposed note "
                "parent", subject="attempt")
        if evidence_digest(attempt.get("before") or {}) != \
                attempt.get("before_evidence_digest"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the durable attempt before-evidence digest does not "
                "reproduce", subject="attempt")
        try:
            rendered, _record = note.render_note_record(
                envelope, prepared_by=attempt.get("prepared_by"),
                prepared_at=attempt.get("prepared_at"))
        except Exception as exc:  # noqa: BLE001
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"the original E cannot be re-rendered with the durable "
                f"attempt meta: {type(exc).__name__}: {exc}", subject="attempt")
        if digest_text_lf(rendered) != attempt.get("body_digest"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the re-rendered original body does not reproduce the durable "
                "attempt body digest", subject="attempt")
        rendered_raw = _sha256_utf8(rendered)
        if rendered_raw != expected_note["rendered_raw_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the re-rendered original body digest does not match the "
                "disposition's rendered digest", subject="attempt")
        issue_id = binding_issue_id(data)
        try:
            evidence = collect_evidence(self.reader, issue_id)
        except PublicationProvenanceIncomplete as exc:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE, exc.message,
                subject="evidence")
        except Exception as exc:  # noqa: BLE001
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"publication recovery evidence collection is incomplete or "
                f"unreadable: {type(exc).__name__}: {exc}", subject="evidence")
        issue = evidence["issue"]
        if issue.get("id") != issue_id:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the fresh target identity changed", subject="issue")
        try:
            reread = collect_evidence(self.reader, issue_id)
            recheck = self.reader.issue_get(issue_id)
        except Exception as exc:  # noqa: BLE001
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                f"the stable second read is unavailable: "
                f"{type(exc).__name__}: {exc}", subject="evidence")
        if evidence_projection(reread) != evidence_projection(evidence) or \
                issue_projection(recheck) != issue_projection(issue) or \
                recheck.get("revision") != issue.get("revision"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_MOVING_EVIDENCE,
                "the second complete read is not stable; recovery refuses a "
                "moving platform", subject="evidence")
        comments = evidence["comments"]
        relation_candidates = [
            c for c in comments
            if c.get("content_raw_digest") in
            (rendered_raw, expected_note["observed_raw_digest"])]
        if len(relation_candidates) != 1:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "expected exactly one note matching the rendered or observed "
                f"raw identity, found {len(relation_candidates)}; duplicates "
                "and R/R-minus-one pairs are never resolved by choice",
                subject="note", candidates=len(relation_candidates))
        candidate = relation_candidates[0]
        if candidate.get("content_raw_digest") != \
                expected_note["observed_raw_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the sole candidate is the rendered body, not the observed "
                "single-terminal-LF-removed transport", subject="note")
        content = _content_of(evidence, candidate["id"])
        if _sha256_utf8(content) != expected_note["observed_raw_digest"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the observed note bytes do not reproduce the disposed "
                "observed digest", subject="note")
        relation = single_terminal_lf_relation(rendered, content)
        if not relation["accepted"] or not relation["removed_lf"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the observed content is not exactly the rendered body minus "
                "one terminal LF: " + relation["detail"], subject="note")
        for key in ("note_comment_id", "revision", "created_at", "updated_at",
                    "author_id", "author_type", "source_task_id", "parent_id"):
            field = "note_comment_id" if key == "note_comment_id" else key
            if candidate.get("id" if key == "note_comment_id" else key) != \
                    expected_note[key]:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    f"the observed note {key} does not match the disposition",
                    subject="note", field=key)
        parsed = note._parse_record(content)
        meta = parsed.get("meta") or {}
        if not parsed.get("ok_record"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the visible note does not parse as a complete "
                "CONTEXT_HANDOFF record", subject="note")
        if meta.get("prepared_by") != attempt.get("prepared_by") or \
                meta.get("prepared_at") != attempt.get("prepared_at"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the note meta prepared_by/prepared_at do not equal the "
                "durable attempt meta; the note never self-certifies",
                subject="note")
        if meta.get("package_id") != attempt.get("package_id") or \
                meta.get("task_ref") != execution.get("task_ref") or \
                meta.get("target_role") != execution.get("role"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the note meta package/task/role do not match the bound E",
                subject="note")
        envelope_digest = digest(parsed["envelope"])
        if envelope_digest != attempt.get("envelope_digest"):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the note envelope digest does not match the disposed attempt "
                "envelope digest", subject="note")
        before = attempt.get("before") or {}
        before_map = {c.get("id"): c for c in before.get("comments") or []}
        after_map = {c.get("id"): c for c in comments}
        removed = sorted(set(before_map) - set(after_map))
        added = sorted(set(after_map) - set(before_map))
        edited = [
            cid for cid in before_map
            if canonical_json({
                key: after_map[cid].get(key) for key in before_map[cid]})
            != canonical_json(before_map[cid])]
        if removed or edited or added != [candidate["id"]]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_CONFLICT,
                "the before/after comment delta is not exactly the single "
                "observed note", subject="delta", removed=removed[:4],
                edited=edited[:4],
                added=[i for i in added if i != candidate["id"]][:4])
        changed = _issue_diff(issue_projection(before.get("issue") or {}),
                              issue_projection(issue))
        if changed:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_CONFLICT,
                "target issue fields changed around the publication: "
                + ",".join(changed), subject="issue")
        if canonical_json(before.get("activities") or []) != \
                canonical_json(evidence["activities"]):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_CONFLICT,
                "timeline activity delta around the publication is not "
                "attributable", subject="activities")
        if evidence["runs"]:
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                "the target already carries run(s): "
                + ",".join(str(r.get("id")) for r in evidence["runs"][:4]),
                subject="runs")
        shared = self._classify_publication_history(
            intent, data["creation_spec"], decision)
        artifact = compare_artifact_dependency(
            data["artifact_dependency"]["digest"],
            data["artifact_dependency"]["entries"],
            rebuild_artifact_dependency(
                data["artifact_dependency"]["entries"],
                root=self.artifact_root,
                blob_reader=self.artifact_blob_reader))
        authority = validate_authority_evidence(
            self._resolve_authority(authority_evidence), data=data)
        if not self._fingerprint_recheck(data):
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                "the frozen fingerprint/built_from recheck failed",
                subject="request")
        reconstructed = reconstruct_request_from_fresh_snapshot(
            issue, source_activation["attached_request"])
        if canonical_json(reconstructed) != \
                canonical_json(source_activation["attached_request"]):
            raise PreflightRefusal(
                o2.S_REFRESH_REQUIRED, REASON_MATERIAL_STALE,
                "the freshly read target body plus the accepted explicit "
                "decisions do not reproduce the accepted E request; a stale "
                "or edited target refuses", subject="source_activation")
        check = preflight_self_check(data, reconstructed, current_findings)
        preflight_request_check(data, reconstructed)
        creation = data.get("creation_context") or {}
        target = data.get("target_binding") or {}
        spec = data["creation_spec"]
        join = {
            "creation_package_id": creation.get("package_id"),
            "execution_package_id": execution.get("package_id"),
            "envelope_digest": execution.get("envelope_digest"),
            "parent_issue_id": spec["parent_issue_id"],
            "target_task_ref": execution.get("target_task_ref"),
            "source_activation_comment_id":
                source_activation["comment"]["id"],
            "source_activation_author_id":
                source_activation["comment"]["author_id"],
            "resolution_raw_digest":
                source_activation["resolution"]["raw_digest"],
            "request_raw_digest": source_activation["request"]["raw_digest"],
            "reconstructed_request_digest": digest(reconstructed),
            "task_fingerprint":
                (execution.get("built_from") or {}).get("task_fingerprint"),
        }
        if join["execution_package_id"] != attempt.get("package_id") or \
                join["envelope_digest"] != attempt.get("envelope_digest") or \
                join["parent_issue_id"] != \
                (before.get("issue") or {}).get("parent_issue_id") or \
                join["creation_package_id"] != \
                (creation.get("package_id") or
                 (data.get("creation_context") or {}).get("package_id")):
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the source parent/E identity join is broken",
                subject="source_join")
        if join["source_activation_comment_id"] != \
                decision["source_activation"]["comment_id"] or \
                join["resolution_raw_digest"] != \
                decision["source_activation"]["resolution_raw_digest"] or \
                join["request_raw_digest"] != \
                decision["source_activation"]["request_raw_digest"] or \
                join["task_fingerprint"] != \
                decision["source_activation"]["task_fingerprint"]:
            raise PreflightRefusal(
                o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                "the source activation join does not bind the disposed "
                "resolution/request/fingerprint", subject="source_join")
        return {
            "blocker_transition": blocker_transition,
            "blocker_evidence": evidence_event,
            "attempt_event": attempt_event,
            "attempt": attempt,
            "rendered": rendered,
            "rendered_raw": rendered_raw,
            "candidate": candidate,
            "content": content,
            "parsed": parsed,
            "evidence": evidence,
            "reread": reread,
            "recheck": recheck,
            "shared": shared,
            "artifact_digest": artifact["digest"],
            "authority": authority,
            "self_check": check,
            "join": join,
            "source_activation": source_activation,
            "authority_approvals": source_activation["authority_approvals"],
            "reconstructed": reconstructed,
            "responses": list(self.reader.responses[responses_start:]),
            "ledger_prefix_count": shared["audited_prefix"]["length"],
            "ledger_prefix_raw_digest":
                shared["audited_prefix"]["raw_prefix_digest"],
        }

    def _build_publication_recovery_proof(self, intent, data, decision,
                                          bundle) -> dict:
        """Assemble the complete inline publication recovery proof."""
        attempt = bundle["attempt"]
        candidate = bundle["candidate"]
        note_info = bundle["parsed"]
        meta = note_info.get("meta") or {}
        envelope = bundle["parsed"]["envelope"]
        observations = {
            "target_issue": bundle["evidence"]["issue"],
            "target_recheck": bundle["recheck"],
            "comments": bundle["evidence"]["comments"],
            "activities": bundle["evidence"]["activities"],
            "runs": bundle["evidence"]["runs"],
            "raw_responses": [
                {"argv": response["argv"], "exit_code": 0,
                 "stdout": response["raw"],
                 "stdout_digest": response["raw_digest"]}
                for response in bundle["responses"]],
        }
        observations["digests"] = {
            name: digest(observations[name]) for name in (
                "target_issue", "target_recheck", "comments", "activities",
                "runs", "raw_responses")}
        shared = bundle["shared"]
        recovered_execution = data.get("execution_binding") or {}
        recovered_proof = data.get("recovery_proof") or {}
        recovered_decision = recovered_proof.get("decision") or {}
        proof = {
            "schema": PUBLICATION_RECOVERY_PROOF_SCHEMA,
            "contract_version": CONTRACT_VERSION,
            "intent_id": intent["intent_id"],
            "target": {
                "issue_id": bundle["evidence"]["issue"]["id"],
                "identifier": bundle["evidence"]["issue"].get("identifier"),
            },
            "state_before": o2.S_BLOCKED,
            "intent_revision_before": intent["revision"],
            "commit_revision": intent["revision"] + 1,
            "blocker": {
                "transition_seq": bundle["blocker_transition"].get("seq"),
                "transition_digest":
                    digest(bundle["blocker_transition"]),
                "reason": REASON_PUBLICATION_PROVENANCE,
                "evidence_code": PUB_NOTE_NOT_FOUND,
                "evidence_event_seq":
                    bundle["blocker_evidence"].get("seq"),
                "evidence_event_digest":
                    digest(bundle["blocker_evidence"]),
                "detail": ((bundle["blocker_evidence"].get("data") or {})
                           .get("detail") or "")[:200],
            },
            "attempt": {
                "event_seq": bundle["attempt_event"].get("seq"),
                "event_digest": digest(bundle["attempt_event"]),
                "operation_id": attempt.get("operation_id"),
                "package_id": attempt.get("package_id"),
                "envelope_digest": attempt.get("envelope_digest"),
                "rendered_body_digest_lf": attempt.get("body_digest"),
                "rendered_body_digest_raw": bundle["rendered_raw"],
                "rendered_chars": len(bundle["rendered"]),
                "before_issue_revision": ((attempt.get("before") or {}).get(
                    "issue") or {}).get("revision"),
                "prepared_by": attempt.get("prepared_by"),
                "prepared_at": attempt.get("prepared_at"),
                "publisher_run_id": attempt.get("publisher_run_id"),
                "parent_comment_id": attempt.get("parent_comment_id"),
                "before_evidence_digest": attempt.get(
                    "before_evidence_digest"),
            },
            "note": {
                "note_comment_id": candidate["id"],
                "revision": candidate["revision"],
                "created_at": candidate["created_at"],
                "updated_at": candidate["updated_at"],
                "author_id": candidate["author_id"],
                "author_type": candidate["author_type"],
                "source_task_id": candidate["source_task_id"],
                "parent_id": candidate["parent_id"],
                "observed_raw_digest": _sha256_utf8(bundle["content"]),
                "observed_lf_digest": digest_text_lf(bundle["content"]),
                "observed_chars": len(bundle["content"]),
                "observed_utf8_bytes":
                    len(bundle["content"].encode("utf-8")),
                "rendered_raw_digest": bundle["rendered_raw"],
                "rendered_lf_digest": digest_text_lf(bundle["rendered"]),
                "rendered_chars": len(bundle["rendered"]),
                "rendered_utf8_bytes":
                    len(bundle["rendered"].encode("utf-8")),
                "relation": PUBLICATION_RELATION_DIRECTIONAL,
                "meta_prepared_by": meta.get("prepared_by"),
                "meta_prepared_at": meta.get("prepared_at"),
                "meta_package_id": meta.get("package_id"),
                "meta_task_ref": meta.get("task_ref"),
                "meta_target_role": meta.get("target_role"),
                "envelope_digest": digest(envelope),
                "record_parsed": bool(note_info.get("ok_record")),
            },
            "original_create_recovery": {
                "decision_digest": recovered_decision.get("decision_digest"),
                "proof_digest": recovered_proof.get("proof_digest"),
                "execution_binding_digest": digest(recovered_execution),
                "accepted_commit": recovered_execution.get(
                    "accepted_execution_commit"),
                "accepted_adapter_digest": recovered_execution.get(
                    "accepted_execution_adapter_digest"),
            },
            "execution_authority_historical": {
                "commit": PREDECESSOR_FORWARD_COMMIT,
                "adapter_digest": PREDECESSOR_FORWARD_ADAPTER_DIGEST,
                "resolver": self.execution_resolver_kind,
                "path": ADAPTER_MODULE,
                "ok": True,
            },
            "observations": observations,
            "shared_history": shared,
            "source_activation": {
                "parent_issue_id":
                    decision["source_activation"]["parent_issue_id"],
                "comment": dict(bundle["source_activation"]["comment"]),
                "resolution":
                    dict(bundle["source_activation"]["resolution"]),
                "request": dict(bundle["source_activation"]["request"]),
                "package_id": decision["source_activation"]["package_id"],
                "envelope_digest":
                    decision["source_activation"]["envelope_digest"],
                "task_fingerprint":
                    decision["source_activation"]["task_fingerprint"],
                "request_digest":
                    bundle["source_activation"]["request_digest"],
                "reconstructed_digest": digest(bundle["reconstructed"]),
                "reconstructed_fingerprint":
                    chandoff.fingerprint_from_request(bundle["reconstructed"]),
            },
            "authority_approvals": copy.deepcopy(bundle["authority_approvals"]),
            "material": {
                "artifact_dependency_digest": bundle["artifact_digest"],
                "artifact_recheck_ok": True,
                "authority": bundle["authority"],
                "fingerprint": {
                    "task_fingerprint": ((data.get("execution_context")
                                          or {}).get("built_from")
                                         or {}).get("task_fingerprint"),
                    "built_from_digest": digest(
                        (data.get("execution_context") or {}).get(
                            "built_from") or {}),
                },
                "self_check": dict(bundle["self_check"]),
                "source_join": bundle["join"],
            },
            "decision": dict(decision),
            "decision_digest": decision["decision_digest"],
        }
        proof["proof_digest"] = digest({
            k: v for k, v in proof.items() if k != "proof_digest"})
        return proof

    def _build_publication_execution_migration(self, intent, data, decision,
                                               proof) -> dict:
        """Build the single committed execution-identity migration."""
        execution = decision["execution_migration"]
        migration = {
            "schema": PUBLICATION_MIGRATION_SCHEMA,
            "migration_id": "u12-r0b-pub-migration-" + intent["intent_id"][3:],
            "intent_id": intent["intent_id"],
            "old_execution_binding_digest": digest(
                data.get("execution_binding") or {}),
            "old_recovery_proof_digest": digest(
                data.get("recovery_proof") or {}),
            "old_decision_digest": ((data.get("recovery_proof") or {}).get(
                "decision") or {}).get("decision_digest"),
            "predecessor_commit": PREDECESSOR_FORWARD_COMMIT,
            "predecessor_adapter_digest": PREDECESSOR_FORWARD_ADAPTER_DIGEST,
            "blocker_digest": proof["blocker"]["transition_digest"],
            "attempt_digest": proof["attempt"]["event_digest"],
            "shared_prefix": dict(proof["shared_history"]["audited_prefix"]),
            "new_commit": execution["accepted_commit"],
            "new_tree": execution["accepted_tree"],
            "new_adapter_raw_digest": execution["adapter_raw_digest"],
            "new_adapter_lf_digest": execution["adapter_lf_digest"],
            "store_file_digest": execution["store_file_digest"],
            "note_file_digest": execution["note_file_digest"],
            "artifact_dependency_digest":
                execution["artifact_dependency_digest"],
            "migration_decision_digest": proof["decision_digest"],
            "publication_recovery_proof_digest": proof["proof_digest"],
            "from_revision": proof["intent_revision_before"],
            "commit_revision": proof["commit_revision"],
        }
        migration["migration_digest"] = digest(migration)
        return migration

    def _commit_publication_recovery(self, intent_id: str, *, record: dict,
                                     expected_revision: int,
                                     expected_tail_digests: list,
                                     prefix_count: int,
                                     prefix_raw_digest: str) -> dict:
        """The one atomic commit: OS lock, CAS/lease/tail recheck, one fsync."""
        with self.store.lock:
            lines, records = self._read_ledger_lines()
            folded = o2.fold_records(records)
            current = folded["intents"].get(intent_id)
            if current is None:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "the intent disappeared from the shared ledger",
                    subject="commit")
            now = self.now()
            lease = current.get("lease")
            if not lease or lease.get("holder") != record["actor"] or \
                    o2.parse_ts(lease["expires_at"]) <= o2.parse_ts(now):
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_MATERIAL_UNAVAILABLE,
                    "the effective intent lease holder recheck failed under "
                    "the OS ledger lock; no commit", subject="lease")
            if current["state"] != o2.S_BLOCKED or \
                    current["revision"] != expected_revision:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "the revision/state compare-and-set failed under the OS "
                    "ledger lock; a revision CAS alone is never sufficient",
                    subject="cas", revision=current["revision"])
            if len(lines) < prefix_count or \
                    _raw_prefix_digest(lines[:prefix_count]) != \
                    prefix_raw_digest:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "the audited shared prefix changed after evidence "
                    "collection; re-read is required and no commit is made",
                    subject="prefix")
            tail = records[prefix_count:]
            if [digest(item) for item in tail] != expected_tail_digests:
                raise PreflightRefusal(
                    o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                    "an unexpected shared tail record appeared at the same "
                    "revision; the append is refused", subject="tail",
                    tail_seqs=[item.get("seq") for item in tail[:4]])
            logic = current["fields"].get("logical_task_key")
            for other in folded["intents"].values():
                if other["intent_id"] == intent_id:
                    continue
                if other["fields"].get("logical_task_key") == logic and \
                        other["state"] not in o2.TERMINAL_STATES:
                    raise PreflightRefusal(
                        o2.S_BLOCKED, REASON_PUBLICATION_PROVENANCE,
                        "a conflicting open intent owns the same logical task "
                        "key; no commit", subject="logical_key")
            validate_publication_recovery_commit_record(record, current)
            appended = self.store._append_locked(record, folded)
            return dict(appended)

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


# One shared classifier for the live collection and the pure proof recheck,
# so a relabelled shared-history record can never survive the reducer.
FactoryClass = R0BForwardFactory


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
        "audit_recovery_ledger", "recover_blocked_publication",
        "_publication_recovery_inspection", "_replay_publication_recovery",
        "_publication_recovery_refusal", "_classify_publication_history",
        "_publication_record_class", "_publication_recovery_prerequisites",
        "_build_publication_recovery_proof",
        "_build_publication_execution_migration",
        "_commit_publication_recovery")]
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
        "publication_transport_profile_pinned": (
            f'PUBLICATION_TRANSPORT_PROFILE = "{PUBLICATION_TRANSPORT_PROFILE}"'
            in text
            and functions.get("prepare_publication_transport_body") is not None
            and functions.get("publication_transport_relation") is not None
            and functions.get("adapter_raw_digest") is not None),
        "publication_send_is_exact_transport": (
            "transport_body=transport_body" in _unparse(
                _method(factory, "publish_handoff_once"))
            and "sent_hex" in _unparse(
                _method(factory, "publish_handoff_once"))
            and "transport_body_digest_raw" in _unparse(
                _method(factory, "publish_handoff_once"))),
        "publication_recovery_operation_present": (
            _method(factory, "recover_blocked_publication") is not None
            and _method(factory, "_commit_publication_recovery") is not None
            and functions.get("validate_publication_recovery_decision")
            is not None
            and functions.get("validate_publication_recovery_proof")
            is not None),
        "publication_recovery_op_registered": (
            PUBLICATION_RECOVERY_OP in o2.registered_extension_ops()
            and functions.get("fold_publication_recovery_commit") is not None
            and functions.get("validate_publication_recovery_commit_record")
            is not None),
        "writer_and_reducer_share_validation": (
            _calls_name(_method(factory, "_commit_publication_recovery"),
                        "validate_publication_recovery_commit_record")
            and _calls_name(functions.get("fold_publication_recovery_commit"),
                            "validate_publication_recovery_commit_record")),
        "publication_semantic_proof_recheck": (
            functions.get("validate_publication_recovery_semantics")
            is not None
            and _calls_name(functions.get("validate_publication_recovery_proof"),
                            "validate_publication_recovery_semantics")
            and _calls_name(
                _method(factory, "_replay_publication_recovery"),
                "validate_publication_recovery_proof")),
        "publication_source_activation_resolved": (
            functions.get("reconstruct_request_from_fresh_snapshot")
            is not None
            and _calls_attr(
                _method(factory, "_publication_recovery_prerequisites"),
                "_resolve_source_activation")
            and _calls_attr(functions.get("validate_publication_recovery_"
                                          "semantics"),
                            "_publication_derive_reads")
            and _method(classes.get("EvidenceReader"),
                        "attachment_content") is not None),
        "execution_migration_validated_on_load": (
            functions.get("validate_publication_execution_migration")
            is not None
            and _calls_name(functions.get("_validate_execution_binding"),
                            "validate_publication_execution_migration")),
        "historical_inspection_pin_explicit": (
            "PREDECESSOR_FORWARD_ADAPTER_DIGEST" in text
            and _calls_attr(_method(factory, "_publication_recovery_inspection"),
                            "get")
            and "executing=PREDECESSOR_FORWARD_ADAPTER_DIGEST" in text),
        "no_blanket_blocked_exit": (
            "PUBLICATION_RECOVERY_OP" in text
            and "S_HANDOFF_PUBLISHED" in _unparse(
                _method(factory, "recover_blocked_publication"))
            and "S_BLOCKED" in _unparse(
                _method(factory, "recover_blocked_publication"))),
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


def cmd_recover_blocked_publication(args) -> int:
    """Operator entrypoint: one approved proof-bearing publication recovery.

    Read-only platform reads plus the bounded evidence event and the single
    versioned commit record. The exact Lead disposition, the accepted
    execution-migration identities and the current Finding source are
    mandatory; no note resend, create, assign, status, rerun or trigger is
    reachable from this path.
    """
    store = o2.DurableIntentStore(args.ledger)
    factory = build_r0b_factory(
        store, runner=_probe_runner, executable=args.executable,
        artifact_root=args.artifact_root or str(ROOT),
        authority_reader=ReadinessManifestAuthorityReader(
            args.authority_root or args.artifact_root or str(ROOT)))
    decision = _load_json(args.decision_file)
    accepted = _load_json(args.accepted_execution_file)
    findings = _load_json(args.findings_file) if args.findings_file else None
    try:
        result = factory.recover_blocked_publication(
            args.intent_id, decision=decision,
            accepted_execution=accepted, actor=args.actor,
            current_findings=findings)
    except o2.IntentError as exc:
        print(json.dumps({"ok": False, "code": getattr(exc, "code", None),
                          "message": exc.message,
                          "details": getattr(exc, "details", {})},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    result = dict(result)
    result["ok"] = result.get("status") == o2.S_HANDOFF_PUBLISHED
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

    pubrec = sub.add_parser(
        "recover-blocked-publication",
        help="one approved proof-bearing recovery of the exact BLOCKED "
             "publication incident (BLOCKED rev4 -> HANDOFF_PUBLISHED rev5 "
             "plus the execution-identity migration)")
    pubrec.add_argument("--ledger", required=True)
    pubrec.add_argument("--intent-id", required=True)
    pubrec.add_argument("--decision-file", required=True,
                        help="exact Lead publication recovery disposition")
    pubrec.add_argument("--accepted-execution-file", required=True,
                        help="accepted execution-migration identities "
                             "(must equal decision.execution_migration)")
    pubrec.add_argument("--findings-file", default=None,
                        help="current Finding source (required; the adapter "
                             "never forces an empty list)")
    pubrec.add_argument("--actor", required=True)
    pubrec.add_argument("--artifact-root", default=None)
    pubrec.add_argument("--authority-root", default=None)
    pubrec.add_argument("--executable", default="multica")
    pubrec.set_defaults(func=cmd_recover_blocked_publication)

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
