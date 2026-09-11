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
ADAPTER_VERSION = "U12-R0B/1.0"
CONTRACT_VERSION = "U12-R0B/1.0"
R0B_FIELD = "r0_binding"

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
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise PublicationProvenanceIncomplete(
                f"{what} is not JSON: {exc}") from exc

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
    out = {
        "title": _require_text(spec.get("title"), "creation_spec.title"),
        "body": _require_text(spec.get("body"), "creation_spec.body",
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
        "body_digest": _require_digest(spec.get("body_digest"),
                                       "creation_spec.body_digest"),
        "creation_task_ref": _require_text(spec.get("creation_task_ref"),
                                           "creation_spec.creation_task_ref"),
        "authority_refs": spec.get("authority_refs"),
    }
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


def validate_intent_record(intent: dict, *, require=()) -> dict:
    """Every entry/resume validates the namespaced data. Never downgrade."""
    data = intent["fields"].get(R0B_FIELD)
    if not isinstance(data, dict):
        raise R0BContractError(
            "intent is not tagged with an R0B contract; the R0B forward "
            "adapter refuses to execute it (no downgrade)",
            intent_id=intent["intent_id"])
    if data.get("contract_version") != CONTRACT_VERSION:
        raise R0BContractError(
            "unsupported R0B contract version; stop, never downgrade",
            contract_version=data.get("contract_version"))
    if data.get("adapter_digest") != adapter_digest():
        raise R0BDowngradeRefused(
            "R0B intent was recorded by different adapter bytes; refusing to "
            "resume under a changed adapter (fail closed)",
            intent_id=intent["intent_id"])
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
                 artifact_root=None, authority_reader=None):
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

    # -- contract gates ------------------------------------------------------
    def _load(self, intent_id: str, *, require=()) -> dict:
        intent = self.store.get(intent_id)
        data = validate_intent_record(intent, require=require)
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
        intent = self._load(intent_id)
        data = validate_intent_record(intent)
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
        validate_intent_record(intent)
        raise R0BDowngradeRefused(
            "R0B intents must bind E through bind_execution_package; "
            "the plain mark_prepared transition is not an accepted R0B path",
            intent_id=intent_id)

    def mark_published(self, intent_id: str, *, note_comment_id: str,
                       receipt_digest: str | None, actor: str) -> dict:
        intent = self.store.get(intent_id)
        validate_intent_record(intent)
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
        intent = self._load(intent_id)
        data = intent["fields"].get(R0B_FIELD) or {}
        state = intent["state"]
        names = {e.get("name") for e in intent["events"]}
        self.store.append_event(
            intent_id, E_RECOVERY, actor=actor, now=self.now(),
            data={"window": state, "read_only": True,
                  "create": E_CREATE_ISSUING in names,
                  "ownership": E_OWNERSHIP_ISSUING in names,
                  "publication": E_PUBLICATION_ISSUING in names})
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
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
