#!/usr/bin/env python3
"""T06 — non-trigger /note CONTEXT_HANDOFF publisher + discovery (YZT-59).

Adapter-only layer on top of the frozen T00-T03 handoff pipeline. It persists
a validated READY/PARTIAL prepare_handoff_result on a Multica issue through
the deployed non-trigger `/note` comment mechanism and deterministically
resolves the latest valid CONTEXT_HANDOFF record for (task_ref, target_role)
so T07 can hand the envelope into the existing T04 self-check API. T06 never
calls the self-check API and never amends a frozen schema.

Boundary (mirrors T05; this file is deliberately framework-SPECIFIC and is
therefore NOT scanned by `tools/chandoff.py scan`, which audits the frozen
framework-neutral schemas and Native helpers only):

- consumes ONLY a frozen-schema-valid prepare_handoff_result plus
  caller-supplied publication provenance/routing. The envelope is fully
  re-validated against the frozen `prepare-handoff-result` and reused
  `context-package` schemas; package integrity is re-checked with existing
  deterministic helpers (finalize ref-grammar). Duplicated §31.2 display
  metadata must exactly match the validated envelope and is never trusted
  on its own;
- READY is publishable. PARTIAL only with explicit caller authorization and
  with gaps kept visible and unchanged. BLOCKED is never publishable and is
  refused before any subprocess or write;
- the Multica WRITE allowlist is exactly ONE `issue comment add` argv
  carrying the rendered `/note` record via a UTF-8 `--content-file` inside
  the current working directory (inline content, stdin, attachments and
  `--allow-external-file` are refused). The temporary body file is deleted
  after the command completes, success or failure. No issue
  create/update/status/assign/rerun/cancel/metadata/label, no mention, no
  attachment, no comment delete/resolve, nothing else is ever emitted;
- an optional caller-supplied parent comment id routes the record as a
  reply; the publisher never invents or reuses a stale parent id;
- retry is idempotent: an already-valid identical record with the same
  package_id + task_ref + role returns the existing comment reference
  instead of publishing a duplicate. Any other reuse of the same package id
  (different task/role, different content, or an unverifiable record)
  fails closed;
- discovery reads are read-only: the deployed complete-thread read
  `issue comment list <issue> --full --output json` (every comment of every
  thread verbatim, resolved threads included — verified against deployed
  CLI v0.4.41 on a 41-comment issue: 17 roots + 24 replies, no pagination
  cursor on stderr). Clipped summaries, recent-thread windows and folded
  resolved threads are never used as completeness proof. If the deployed
  CLI reports a pagination cursor on a complete read, the read contract has
  drifted and discovery stops bounded instead of reading less than all;
- ordering/provenance authority is the SERVER comment `created_at` and
  comment id; embedded prepared_at/prepared_by never establish recency or
  trust. Newest server created_at wins, comment id is the deterministic
  tie-breaker. A newer malformed / schema-invalid / integrity-invalid /
  package-id-conflicting same-target candidate fails closed rather than
  silently exposing an older package;
- record/envelope is Adapter state, not a Native API/Core object. The
  frozen T00 schemas are validated, never modified; no second Task Context
  Package schema exists; no LLM call, no network library, no Canonical
  write, no Memory rebuild.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chandoff import canonical_json  # noqa: E402
from chandoff_finalize import grammar_invalid_refs, pseudo_scheme_refs  # noqa: E402
from cutil import now_iso  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

ADAPTER_VERSION = "T06/1.0"
RESULT_SCHEMA = "context-handoff/prepare-handoff-result.schema.json"
PACKAGE_SCHEMA = "context-package.schema.json"

NOTE_COMMAND = "/note"
RECORD_VERSION = 1
RECORD_MARKER = f"CONTEXT_HANDOFF_RECORD v{RECORD_VERSION}"
META_PREFIX = "CONTEXT_HANDOFF_META "
FENCE_OPEN = "```json"
FENCE_CLOSE = "```"

HANDOFF_META_KEYS = ("package_id", "task_ref", "target_role", "status",
                     "built_from", "prepared_by", "prepared_at")
PAYLOAD_KEYS = ("record_version", "context_handoff", "prepare_handoff_result")

WRITE_COMMAND = ("issue", "comment", "add")
READ_COMMANDS = (
    ("issue", "comment", "list"),
    ("version",),
)
FORBIDDEN_PUBLISH_FLAGS = ("--content", "--content-stdin", "--attachment",
                           "--allow-external-file")
COMMENT_CONTRACT_FIELDS = ("id", "content", "created_at", "parent_id")

CURSOR_NOTICE_RE = re.compile(r"Next (?:thread|reply) cursor", re.I)
SLASH_LINE_RE = re.compile(r"^[ \t]*/")
MENTION_LINK_RE = re.compile(r"mention://")
MAX_TEXT = 200


def _bounded(value, limit: int = MAX_TEXT):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    return value


class AdapterError(Exception):
    """Bounded stop. Nothing about the failure is guessed away."""

    code = "adapter_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = _bounded(str(message))
        self.details = {k: _bounded(v) for k, v in details.items()}

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class CliUnavailableError(AdapterError):
    code = "cli_unavailable"


class CliCommandError(AdapterError):
    code = "cli_command_failed"


class CliJsonError(AdapterError):
    code = "cli_json_malformed"


class CliContractError(AdapterError):
    code = "incompatible_cli_contract"


class SchemaViolationError(AdapterError):
    code = "result_schema_violation"


class PublishRefusedError(AdapterError):
    code = "publish_refused"


class PackageIdConflictError(AdapterError):
    code = "package_id_conflict"


class LatestHandoffInvalidError(AdapterError):
    code = "latest_handoff_invalid"


def _default_runner(argv: list) -> tuple:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
    except OSError as exc:
        raise CliUnavailableError(f"multica CLI unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CliCommandError("multica CLI timed out after 120s") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _prefix_of(argv: list):
    return tuple(str(a) for a in argv)


def _allowlisted_read(argv: list) -> bool:
    return any(tuple(str(a) for a in argv[:len(cmd)]) == cmd
               for cmd in READ_COMMANDS)


def _validate_publish_argv(argv: list) -> None:
    if tuple(str(a) for a in argv[:len(WRITE_COMMAND)]) != WRITE_COMMAND:
        raise AdapterError(
            "refused non-allowlisted multica write command",
            argv=" ".join(str(a) for a in argv))
    if "--content-file" not in [str(a) for a in argv]:
        raise AdapterError(
            "publish requires --content-file; inline/stdin bodies are refused",
            argv=" ".join(str(a) for a in argv))
    present = [str(a) for a in argv]
    for flag in FORBIDDEN_PUBLISH_FLAGS:
        if flag in present:
            raise AdapterError(
                f"refused publish flag {flag!r}", argv=" ".join(present))


class NoteCli:
    """Deployed CLI boundary with an explicit read/write split.

    Reads: `issue comment list` and `version` only. Writes: exactly
    `issue comment add` with a --content-file body. Every issued argv is
    recorded for the trace; anything outside the two allowlists is refused
    before a process is ever started.
    """

    def __init__(self, executable: str = "multica", runner: Callable | None = None):
        self.executable = executable
        self.runner = runner or _default_runner
        self.commands: list = []

    def _run(self, argv: list, *, write: bool) -> tuple:
        """Run one allowlisted argv; returns (stdout, stderr).

        Reads must match READ_COMMANDS prefixes; writes must be exactly one
        `issue comment add` argv with a --content-file body and none of the
        forbidden inline/stdin/attachment/external-path flags.
        """
        if write:
            _validate_publish_argv(argv)
        elif not _allowlisted_read(argv):
            raise AdapterError(
                "refused non-read-only multica command",
                argv=" ".join(str(a) for a in argv))
        self.commands.append([str(a) for a in argv])
        try:
            code, out, err = self.runner([self.executable] + list(argv))
        except OSError as exc:
            raise CliUnavailableError(f"multica CLI unavailable: {exc}") from exc
        if code != 0:
            first = next((l for l in err.splitlines() if l.strip()), "")
            raise CliCommandError(
                f"multica command failed (exit {code}): {first}",
                exit_code=code, stderr_excerpt=err[:MAX_TEXT])
        return out, err

    def version(self) -> str | None:
        out, _err = self._run(["version", "--output", "json"], write=False)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return None
        value = data.get("version") if isinstance(data, dict) else None
        return value if isinstance(value, str) else None

    def list_full(self, issue_id: str) -> list:
        """Complete-thread read: every comment verbatim, resolved included.

        Bounded completeness contract (v0.4.41): the default list is a
        complete-thread enumeration; `--full` disables resolved-thread
        folding so folded discussion cannot hide a record. A cursor notice
        on this mode means the deployed contract drifted; reading less than
        everything is never silently accepted.
        """
        argv = ["issue", "comment", "list", str(issue_id),
                "--full", "--output", "json"]
        out, err = self._run(argv, write=False)
        if CURSOR_NOTICE_RE.search(err or ""):
            raise CliContractError(
                "deployed CLI reported a pagination cursor on a complete "
                "comment read; complete discovery cannot be proven",
                stderr_excerpt=err[:MAX_TEXT])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise CliJsonError(
                f"comment list returned malformed JSON: {exc}") from exc
        if not isinstance(data, list):
            raise CliContractError("comment list JSON is not an array",
                                   kind=type(data).__name__)
        return [self._validate_comment(doc, i) for i, doc in enumerate(data)]

    def comment_add(self, issue_id: str, content_file: str,
                    parent_comment_id: str | None = None) -> dict:
        argv = ["issue", "comment", "add", str(issue_id),
                "--content-file", str(content_file), "--output", "json"]
        if parent_comment_id:
            argv += ["--parent", str(parent_comment_id)]
        out, _err = self._run(argv, write=True)
        return parse_comment_add_response(out)

    def _validate_comment(self, doc, index: int) -> dict:
        if not isinstance(doc, dict):
            raise CliContractError(
                "comment list element is not an object", index=index,
                kind=type(doc).__name__)
        missing = [f for f in COMMENT_CONTRACT_FIELDS if f not in doc]
        if missing:
            raise CliContractError(
                "deployed CLI comment JSON is missing contract fields",
                index=index, missing=missing)
        if not isinstance(doc["id"], str) or not doc["id"].strip():
            raise CliContractError("comment id is not a non-blank string",
                                   index=index)
        if not isinstance(doc["content"], str):
            raise CliContractError("comment content is not a string",
                                   index=index)
        if not isinstance(doc["created_at"], str) or not doc["created_at"].strip():
            raise CliContractError("comment created_at is missing or blank",
                                   index=index)
        if doc["parent_id"] is not None and not isinstance(doc["parent_id"], str):
            raise CliContractError("comment parent_id is neither a string nor null",
                                   index=index)
        return doc


def parse_comment_add_response(text: str) -> dict:
    """Validate the deployed comment-add response contract; stop on drift."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliJsonError(f"comment add returned malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliContractError("comment add JSON is not an object",
                               kind=type(data).__name__)
    comment_id = data.get("id")
    if not isinstance(comment_id, str) or not comment_id.strip():
        raise CliContractError("comment add response has no comment id")
    return data


def comment_provenance(doc: dict) -> dict:
    """Server-authoritative provenance of one comment."""
    return {
        "id": doc.get("id"),
        "created_at": doc.get("created_at"),
        "parent_id": doc.get("parent_id"),
        "author_id": doc.get("author_id"),
        "author_type": doc.get("author_type"),
        "issue_id": doc.get("issue_id"),
        "resolved_at": doc.get("resolved_at"),
    }


# ---------------------------------------------------------------------------
# Frozen-envelope validation and package integrity (existing deterministic
# helpers; the frozen schemas are validated, never modified).
# ---------------------------------------------------------------------------

def _schema_errors(schema_name: str, instance, path: str = "$") -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path=path)


def envelope_integrity_errors(envelope: dict) -> list:
    """Deterministic integrity re-checks beyond the frozen schemas."""
    errors: list = []
    package = envelope.get("package")
    task_ref = envelope.get("task_ref")
    role = envelope.get("role")
    status = envelope.get("status")
    if isinstance(package, dict):
        request = package.get("request") or {}
        if request.get("task_id") != task_ref:
            errors.append(
                f"package.request.task_id {request.get('task_id')!r} != "
                f"result task_ref {task_ref!r}")
        if request.get("role") != role:
            errors.append(
                f"package.request.role {request.get('role')!r} != result role {role!r}")
        bad = pseudo_scheme_refs(package) + grammar_invalid_refs(package)
        if bad:
            errors.append(f"package ref grammar invalid: {bad[0]!r}")
        if status == "READY" and (package.get("open_conflicts") or package.get("blocked_by")):
            errors.append("READY package carries visible gaps "
                          "(open_conflicts/blocked_by must be empty)")
        if status == "PARTIAL" and not (package.get("open_conflicts") or package.get("blocked_by")):
            errors.append("PARTIAL package hides its gaps "
                          "(open_conflicts/blocked_by must stay visible)")
    return errors


def validate_result_envelope(envelope) -> list:
    """Full frozen validation of a prepare_handoff_result (+ package)."""
    if not isinstance(envelope, dict):
        return ["envelope is not an object"]
    errors = [f"$.{e}" for e in _schema_errors(RESULT_SCHEMA, envelope)]
    package = envelope.get("package")
    if isinstance(package, dict):
        errors += [f"$.package.{e}"
                   for e in _schema_errors(PACKAGE_SCHEMA, package)]
    errors += [f"integrity: {e}" for e in envelope_integrity_errors(envelope)]
    return errors


def frozen_contract_supports_publish() -> dict:
    """Prove frozen T00 can host T06 without a Native API amendment."""
    result = load_schema_file(RESULT_SCHEMA)
    common = load_schema_file("context-handoff/handoff-common.schema.json")
    required = set(result.get("required") or [])
    marker_in_schemas = False
    for schema_path in sorted((Path(__file__).resolve().parents[1]
                               / "schemas").rglob("*.json")):
        if RECORD_MARKER.split(" ")[0] in schema_path.read_text(encoding="utf-8"):
            marker_in_schemas = True
            break
    checks = {
        "frozen_result_schema_present": (
            result.get("properties", {}).get("kind", {}).get("const")
            == "prepare_handoff_result"),
        "frozen_status_enum_exact": (
            result.get("properties", {}).get("status", {}).get("$ref", "")
            .rsplit("/", 1)[-1] == "prepare_handoff_status"),
        "frozen_status_vocabulary_unchanged": (
            common["$defs"]["prepare_handoff_status"]["enum"]
            == ["READY", "PARTIAL", "BLOCKED"]),
        "frozen_result_package_refs_existing_context_package_schema": (
            "context-package.schema.json"
            in (result.get("properties", {}).get("package", {}).get("$ref") or "")),
        "frozen_result_requires_publish_fields": required.issuperset({
            "status", "package_id", "task_ref", "role", "built_from",
            "package", "escalation"}),
        "record_marker_absent_from_frozen_schemas": not marker_in_schemas,
        "ref_grammar_helpers_available": callable(pseudo_scheme_refs) and callable(
            grammar_invalid_refs),
    }
    report = {k: bool(v) for k, v in checks.items()}
    report["ok"] = all(report.values())
    return report


def done_criteria() -> dict:
    """Frozen done criteria (YZT-59), asserted by the focused tests."""
    return {
        "deployed_note_contract_checked": True,
        "publish_does_not_create_run": True,
        "ready_round_trip_readable": True,
        "partial_requires_explicit_authorization": True,
        "blocked_publish_attempts": 0,
        "latest_role_package_resolvable": True,
        "newer_invalid_candidate_fails_closed": True,
        "duplicate_publish_comments": 0,
        "frozen_schema_changes": 0,
        "canonical_writes": 0,
        "llm_calls": 0,
        "rebuilds": 0,
        "downstream_run_triggers": 0,
    }


# ---------------------------------------------------------------------------
# Record rendering / body contract (adapter-local, versioned).
# ---------------------------------------------------------------------------

def _require_text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
        raise AdapterError(
            f"{field} must be a non-blank string of at most {MAX_TEXT} chars",
            field=field)
    return value


def scan_body(body: str) -> list:
    """Deterministic non-trigger body contract.

    - line 1 is exactly `/note` (the deployed non-trigger command);
    - no mention link of any target kind appears anywhere;
    - no line other than line 1 looks like a slash command.
    """
    problems: list = []
    lines = body.split("\n")
    if not lines or lines[0] != NOTE_COMMAND:
        problems.append("note_first_line_missing")
    if MENTION_LINK_RE.search(body):
        problems.append("mention_link_present")
    for number, line in enumerate(lines[1:], start=2):
        if SLASH_LINE_RE.match(line):
            problems.append(f"stray_slash_command_line_{number}")
    return problems


def render_note_record(envelope, *, prepared_by: str, prepared_at: str | None = None,
                       clock: Callable = now_iso, allow_partial: bool = False) -> tuple:
    """Render the versioned /note CONTEXT_HANDOFF body from a VALID result.

    All publish gates (frozen validation, integrity, BLOCKED/PARTIAL policy)
    run here, before any subprocess or Multica write can happen.
    Returns (body, record).
    """
    errors = validate_result_envelope(envelope)
    if errors:
        raise SchemaViolationError(
            "prepare_handoff_result fails frozen validation/integrity: "
            + "; ".join(errors[:8]))
    status = envelope.get("status")
    if status == "BLOCKED":
        raise PublishRefusedError(
            "BLOCKED results are never publishable",
            reason="blocked_not_publishable")
    if status == "PARTIAL" and not allow_partial:
        raise PublishRefusedError(
            "PARTIAL requires explicit caller authorization",
            reason="partial_requires_explicit_authorization")
    meta = {
        "package_id": envelope["package_id"],
        "task_ref": envelope["task_ref"],
        "target_role": envelope["role"],
        "status": envelope["status"],
        "built_from": envelope["built_from"],
        "prepared_by": _require_text(prepared_by, "prepared_by"),
        "prepared_at": _require_text(
            prepared_at if prepared_at is not None else clock(), "prepared_at"),
    }
    record = {
        "record_version": RECORD_VERSION,
        "context_handoff": meta,
        "prepare_handoff_result": envelope,
    }
    meta_line = json.dumps(meta, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"))
    payload_text = json.dumps(record, ensure_ascii=False, indent=2,
                              sort_keys=True)
    body = "\n".join([
        NOTE_COMMAND,
        "",
        RECORD_MARKER,
        META_PREFIX + meta_line,
        FENCE_OPEN,
        payload_text,
        FENCE_CLOSE,
    ]) + "\n"
    problems = scan_body(body)
    if problems:
        raise PublishRefusedError(
            "rendered body violates the non-trigger contract: "
            + ", ".join(problems[:4]),
            reason="non_trigger_contract_violation", problems=problems[:8])
    return body, record


# ---------------------------------------------------------------------------
# Record parsing (discovery side). A marker-bearing comment is a candidate;
# validity is decided only by full parsing + frozen validation.
# ---------------------------------------------------------------------------

def _parse_record(content: str) -> dict:
    out: dict = {
        "not_a_record": False,
        "ok_record": False,
        "binding": None,
        "meta": None,
        "payload": None,
        "envelope": None,
        "errors": [],
    }
    if not isinstance(content, str):
        out["not_a_record"] = True
        return out
    lines = content.split("\n")
    marker_at = [i for i, line in enumerate(lines) if line == RECORD_MARKER]
    if not marker_at:
        out["not_a_record"] = True
        return out
    errors = out["errors"]
    if len(marker_at) > 1:
        errors.append("ambiguous_record_marker")
    i = marker_at[0]
    if lines[0] != NOTE_COMMAND:
        errors.append("note_first_line_missing")
    meta = None
    if len(lines) > i + 1 and lines[i + 1].startswith(META_PREFIX):
        try:
            meta = json.loads(lines[i + 1][len(META_PREFIX):])
        except json.JSONDecodeError:
            errors.append("meta_malformed")
        else:
            if not isinstance(meta, dict):
                errors.append("meta_malformed")
                meta = None
            elif set(meta) != set(HANDOFF_META_KEYS):
                errors.append("meta_key_set_invalid")
                meta = None
            elif not isinstance(meta.get("built_from"), dict):
                errors.append("meta_built_from_invalid")
                meta = None
            else:
                for key in ("package_id", "task_ref", "target_role", "status",
                            "prepared_by", "prepared_at"):
                    if not isinstance(meta.get(key), str) or not meta[key].strip():
                        errors.append(f"meta_{key}_invalid")
                        meta = None
                        break
    else:
        errors.append("meta_line_missing")
    if meta is not None:
        out["meta"] = meta
        out["binding"] = {"task_ref": meta["task_ref"],
                          "target_role": meta["target_role"]}
    open_idx = i + 2
    if not (len(lines) > open_idx and lines[open_idx] == FENCE_OPEN):
        errors.append("payload_missing")
    else:
        close_candidates = [j for j in range(open_idx + 1, len(lines))
                            if lines[j] == FENCE_CLOSE]
        if not close_candidates:
            errors.append("payload_unterminated")
        else:
            close_idx = close_candidates[0]
            trailing = [line for line in lines[close_idx + 1:] if line.strip()]
            if trailing:
                errors.append("trailing_content_after_record")
            payload_text = "\n".join(lines[open_idx + 1:close_idx])
            try:
                payload = json.loads(payload_text)
            except json.JSONDecodeError as exc:
                errors.append(f"payload_malformed: {exc}")
            else:
                if not isinstance(payload, dict):
                    errors.append("payload_not_object")
                elif set(payload) != set(PAYLOAD_KEYS):
                    errors.append("payload_key_set_invalid")
                elif payload.get("record_version") != RECORD_VERSION:
                    errors.append("record_version_mismatch")
                else:
                    out["payload"] = payload
                    out["envelope"] = payload.get("prepare_handoff_result")
                    if not isinstance(payload.get("context_handoff"), dict):
                        errors.append("payload_context_handoff_invalid")
                    elif meta is not None and canonical_json(
                            payload["context_handoff"]) != canonical_json(meta):
                        errors.append("metadata_duplication_mismatch")
    envelope = out["envelope"]
    if isinstance(envelope, dict):
        if meta is not None:
            for meta_key, env_key in (("package_id", "package_id"),
                                      ("task_ref", "task_ref"),
                                      ("target_role", "role"),
                                      ("status", "status")):
                if meta.get(meta_key) != envelope.get(env_key):
                    errors.append(
                        f"metadata_mismatch: {meta_key} != envelope.{env_key}")
            if canonical_json(meta.get("built_from") or {}) != canonical_json(
                    envelope.get("built_from") or {}):
                errors.append("metadata_mismatch: built_from")
        if out["binding"] is None:
            task_ref = envelope.get("task_ref")
            role = envelope.get("role")
            if isinstance(task_ref, str) and task_ref.strip() and \
                    isinstance(role, str) and role.strip():
                out["binding"] = {"task_ref": task_ref, "target_role": role}
        errors += validate_result_envelope(envelope)
    out["ok_record"] = not errors
    out["errors"] = errors
    return out


def discover_handoff_records(issue_id: str, *, cli: NoteCli | None = None) -> list:
    """Parse every record candidate from the complete-thread read."""
    cli = cli or NoteCli()
    out = []
    for doc in cli.list_full(issue_id):
        parsed = _parse_record(doc.get("content") or "")
        if parsed.get("not_a_record"):
            continue
        out.append({"comment": comment_provenance(doc), "parsed": parsed})
    return out


def _order_key(candidate: dict) -> tuple:
    comment = candidate["comment"]
    return (str(comment.get("created_at") or ""), str(comment.get("id") or ""))


def resolve_latest_handoff(issue_id: str, *, task_ref: str, target_role: str,
                           cli: NoteCli | None = None) -> dict:
    """Deterministic latest-valid resolution for (task_ref, target_role).

    Newest server created_at wins; comment id is the tie-breaker. The newest
    same-target candidate must be valid: a newer malformed, schema-invalid,
    integrity-invalid or package-id-conflicting candidate fails closed
    instead of silently exposing an older package. Unbindable marker-bearing
    records (corrupt meta AND envelope) are fail-closed candidates too.
    """
    cli = cli or NoteCli()
    candidates = discover_handoff_records(issue_id, cli=cli)
    bound = []
    for cand in candidates:
        binding = cand["parsed"]["binding"]
        if binding is None:
            bound.append(cand)
        elif binding["task_ref"] == task_ref and binding["target_role"] == target_role:
            bound.append(cand)
    if not bound:
        return {
            "ok": True,
            "found": False,
            "reason": "no_handoff_record_for_task_role",
            "records_seen": len(candidates),
        }
    bound.sort(key=_order_key, reverse=True)
    newest = bound[0]
    if not newest["parsed"]["ok_record"]:
        raise LatestHandoffInvalidError(
            "newest same-target CONTEXT_HANDOFF candidate is not valid; "
            "refusing to fall back to an older package",
            comment_id=newest["comment"]["id"],
            created_at=newest["comment"]["created_at"],
            errors=newest["parsed"]["errors"][:8])
    by_package: dict = {}
    for cand in bound:
        if cand["parsed"]["ok_record"]:
            env = cand["parsed"]["envelope"]
            by_package.setdefault(env["package_id"], []).append(cand)
    for package_id, group in sorted(by_package.items()):
        if len({canonical_json(c["parsed"]["envelope"]) for c in group}) > 1:
            raise PackageIdConflictError(
                "package id reused with conflicting content",
                package_id=package_id,
                comments=[c["comment"]["id"] for c in group])
    selected = newest
    return {
        "ok": True,
        "found": True,
        "envelope": selected["parsed"]["envelope"],
        "record": selected["parsed"]["payload"],
        "meta": selected["parsed"]["meta"],
        "comment": selected["comment"],
        "selection": {
            "records_seen": len(candidates),
            "bound_candidates": len(bound),
            "order": [[c["comment"]["created_at"], c["comment"]["id"],
                       c["parsed"]["ok_record"]] for c in bound],
        },
    }


# ---------------------------------------------------------------------------
# Publisher.
# ---------------------------------------------------------------------------

def _package_id_of(parsed: dict):
    meta = parsed.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("package_id"), str):
        return meta["package_id"]
    envelope = parsed.get("envelope")
    if isinstance(envelope, dict) and isinstance(envelope.get("package_id"), str):
        return envelope["package_id"]
    return None


def publish_handoff(envelope, *, issue_id: str, prepared_by: str,
                    parent_comment_id: str | None = None,
                    allow_partial: bool = False, prepared_at: str | None = None,
                    clock: Callable = now_iso, cli: NoteCli | None = None,
                    transport_body: str | None = None) -> dict:
    """Publish ONE validated /note CONTEXT_HANDOFF record.

    Order of operations: frozen validation + integrity -> BLOCKED/PARTIAL
    policy -> render -> non-trigger body scan -> idempotency/conflict
    pre-check (reads only) -> exactly one comment-add write via a UTF-8
    --content-file inside the cwd -> temp file deleted in all cases.

    `transport_body` (optional, versioned publication transport profile) is
    the pre-validated UTF-8 transport text the caller prepared from the
    rendered body. The legacy call without it sends the rendered body
    verbatim. With it, the exact bytes written and sent are the supplied
    transport text -- never a re-rendered body -- after the exact relation
    `transport_body == rendered_body[:-1]` and the renderer's exact
    `\\n```\\n` ending are re-verified here. The file write is byte-verified
    before the one comment-add call, and no BOM is ever admitted.
    """
    cli = cli or NoteCli()
    commands_start = len(cli.commands)
    body, record = render_note_record(
        envelope, prepared_by=prepared_by, prepared_at=prepared_at,
        clock=clock, allow_partial=allow_partial)

    if transport_body is not None:
        if not isinstance(transport_body, str) or not transport_body:
            raise AdapterError(
                "transport_body must be a non-empty string",
                field="transport_body")
        if not body.endswith("\n```\n"):
            raise AdapterError(
                "the rendered body does not end with the accepted "
                "LF-fence-LF renderer shape; no variant is guessed",
                field="transport_body")
        if transport_body != body[:-1]:
            raise AdapterError(
                "the supplied transport body is not exactly the rendered "
                "body with its single terminal LF removed",
                field="transport_body")
    sent = transport_body if transport_body is not None else body

    if parent_comment_id is not None:
        _require_text(parent_comment_id, "parent_comment_id")
        if re.search(r"\s", parent_comment_id) or parent_comment_id.startswith("--"):
            raise AdapterError(
                "parent_comment_id is not a bare comment id",
                parent_comment_id=parent_comment_id)

    candidates = discover_handoff_records(issue_id, cli=cli)
    our_task_ref = envelope["task_ref"]
    our_role = envelope["role"]
    existing = None
    for cand in candidates:
        parsed = cand["parsed"]
        package_id = _package_id_of(parsed)
        if package_id != envelope["package_id"]:
            continue
        binding = parsed["binding"]
        if binding is None:
            raise PackageIdConflictError(
                "unverifiable record reuses this package id; refusing to "
                "publish or to treat it as an existing record",
                package_id=envelope["package_id"],
                comment_id=cand["comment"]["id"])
        if (binding["task_ref"], binding["target_role"]) != (our_task_ref, our_role):
            raise PackageIdConflictError(
                "package id already bound to another task/role pair",
                package_id=envelope["package_id"],
                bound_task_ref=binding["task_ref"],
                bound_target_role=binding["target_role"])
        if parsed["ok_record"]:
            if canonical_json(parsed["envelope"]) == canonical_json(envelope):
                if existing is None or _order_key(cand) > _order_key(existing):
                    existing = cand
            else:
                raise PackageIdConflictError(
                    "package id reused with conflicting content",
                    package_id=envelope["package_id"],
                    comment_id=cand["comment"]["id"])
        else:
            raise PackageIdConflictError(
                "invalid record reuses this package id for the same "
                "task/role; refusing to publish a duplicate",
                package_id=envelope["package_id"],
                comment_id=cand["comment"]["id"],
                errors=parsed["errors"][:8])
    if existing is not None:
        return _publish_trace({
            "ok": True,
            "published": False,
            "idempotent": True,
            "issue_id": issue_id,
            "existing_comment": existing["comment"],
            "record": record,
            "reason": "identical_record_already_published",
        }, cli, commands_start, temp_deleted=True, body=sent,
            rendered_body=body)

    body_sha256 = hashlib.sha256(sent.encode("utf-8")).hexdigest()
    cwd = Path.cwd()
    temp_path = cwd / f".t06-note-{uuid.uuid4().hex[:12]}.md"
    temp_deleted = False
    try:
        # Raw bytes: text mode would translate \n to \r\n on Windows and
        # corrupt the record framing the deployed CLI reads back. The exact
        # sent bytes are verified after the write; a BOM is never admitted.
        payload = sent.encode("utf-8")
        if payload.startswith(b"\xef\xbb\xbf"):
            raise AdapterError("transport body carries a UTF-8 BOM; refused",
                               field="transport_body")
        temp_path.write_bytes(payload)
        if temp_path.read_bytes() != payload:
            raise AdapterError(
                "the written content-file bytes differ from the verified "
                "transport bytes; refusing to send", field="transport_body")
        resolved = temp_path.resolve()
        if not resolved.is_relative_to(cwd.resolve()):
            raise AdapterError("temp body file escaped the working directory",
                               path=str(resolved))
        data = cli.comment_add(issue_id, str(temp_path), parent_comment_id)
    finally:
        temp_path.unlink(missing_ok=True)
        temp_deleted = not temp_path.exists()
    comment_ref = {
        "id": data.get("id"),
        "created_at": data.get("created_at"),
        "parent_id": data.get("parent_id"),
    }
    return _publish_trace({
        "ok": True,
        "published": True,
        "idempotent": False,
        "issue_id": issue_id,
        "comment": comment_ref,
        "record": record,
        "body_sha256": body_sha256,
        "transport_body": (transport_body is not None),
    }, cli, commands_start, temp_deleted=temp_deleted, body=sent,
        rendered_body=body)


def _publish_trace(result: dict, cli: NoteCli, commands_start: int, *,
                   temp_deleted: bool, body: str,
                   rendered_body: str | None = None) -> dict:
    issued = cli.commands[commands_start:]
    writes = [argv for argv in issued if tuple(argv[:3]) == WRITE_COMMAND]
    result["trace"] = {
        "adapter": "tools/chandoff_note.py",
        "adapter_version": ADAPTER_VERSION,
        "cli": {"commands": issued, "allowlist_enforced": True},
        "write_commands": len(writes),
        "temp_file_deleted": bool(temp_deleted),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "sent_body_sha256": hashlib.sha256(
            body.encode("utf-8")).hexdigest(),
        "sent_body_utf8_bytes": len(body.encode("utf-8")),
        "guarantees": {
            "llm_called": False,
            "canonical_writes": 0,
            "memory_rebuilds": 0,
            "issue_lifecycle_writes": 0,
            "assignments": 0,
            "mentions": 0,
            "downstream_run_triggers": 0,
            "frozen_schema_changes": 0,
        },
    }
    if rendered_body is not None:
        result["trace"]["rendered_body_sha256"] = hashlib.sha256(
            rendered_body.encode("utf-8")).hexdigest()
        result["trace"]["transport_renderer_relation"] = (
            "rendered-minus-single-terminal-lf")
    return result


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------

def _load_json_file(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T06 non-trigger /note CONTEXT_HANDOFF publisher + discovery")
    sub = parser.add_subparsers(dest="command", required=True)

    render = sub.add_parser("render", help="render the /note record body")
    render.add_argument("--result-file", required=True)
    render.add_argument("--prepared-by", required=True)
    render.add_argument("--prepared-at", default=None)
    render.add_argument("--allow-partial", action="store_true")

    publish = sub.add_parser("publish", help="publish the /note record")
    publish.add_argument("--issue", required=True)
    publish.add_argument("--result-file", required=True)
    publish.add_argument("--prepared-by", required=True)
    publish.add_argument("--prepared-at", default=None)
    publish.add_argument("--parent", default=None,
                         help="optional parent comment id (reply routing)")
    publish.add_argument("--allow-partial", action="store_true")
    publish.add_argument("--transport-body-file", default=None,
                         help="pre-validated exact transport body (must be "
                              "the rendered body minus its single terminal LF)")
    publish.add_argument("--dry-run", action="store_true",
                         help="validate + render without any CLI call")
    publish.add_argument("--executable", default="multica")

    resolve = sub.add_parser(
        "resolve", help="resolve the latest valid record for task/role")
    resolve.add_argument("--issue", required=True)
    resolve.add_argument("--task-ref", required=True)
    resolve.add_argument("--role", required=True)
    resolve.add_argument("--executable", default="multica")

    records = sub.add_parser("records", help="list discovered record candidates")
    records.add_argument("--issue", required=True)
    records.add_argument("--executable", default="multica")

    compat = sub.add_parser("compatibility",
                            help="prove frozen T00 can host T06")
    args = parser.parse_args(argv)

    if args.command == "compatibility":
        report = frozen_contract_supports_publish()
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["ok"] else 2

    try:
        if args.command == "render":
            envelope = _load_json_file(args.result_file)
            body, _record = render_note_record(
                envelope, prepared_by=args.prepared_by,
                prepared_at=args.prepared_at, allow_partial=args.allow_partial)
            print(body, end="")
            return 0
        if args.command == "publish":
            envelope = _load_json_file(args.result_file)
            if args.dry_run:
                body, record = render_note_record(
                    envelope, prepared_by=args.prepared_by,
                    prepared_at=args.prepared_at,
                    allow_partial=args.allow_partial)
                print(json.dumps({
                    "ok": True,
                    "dry_run": True,
                    "body_sha256": hashlib.sha256(
                        body.encode("utf-8")).hexdigest(),
                    "record": record,
                }, ensure_ascii=False, indent=2, sort_keys=True))
                return 0
            result = publish_handoff(
                envelope, issue_id=args.issue, prepared_by=args.prepared_by,
                parent_comment_id=args.parent,
                allow_partial=args.allow_partial, prepared_at=args.prepared_at,
                cli=NoteCli(executable=args.executable),
                transport_body=(
                    Path(args.transport_body_file).read_bytes().decode("utf-8")
                    if args.transport_body_file else None))
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        cli = NoteCli(executable=args.executable)
        if args.command == "resolve":
            result = resolve_latest_handoff(
                args.issue, task_ref=args.task_ref, target_role=args.role,
                cli=cli)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result["ok"] else 2
        if args.command == "records":
            candidates = discover_handoff_records(args.issue, cli=cli)
            print(json.dumps({
                "ok": True,
                "records": [{
                    "comment": c["comment"],
                    "ok_record": c["parsed"]["ok_record"],
                    "binding": c["parsed"]["binding"],
                    "errors": c["parsed"]["errors"][:8],
                } for c in candidates],
            }, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
    except AdapterError as exc:
        print(json.dumps({"ok": False, "error": exc.envelope()},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
