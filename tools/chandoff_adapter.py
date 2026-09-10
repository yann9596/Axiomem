#!/usr/bin/env python3
"""T05 — read-only Multica Issue Snapshot Adapter (YZT-58).

This module is deliberately framework-SPECIFIC: it is the one place in the
repo that may name Multica runtime concepts. Its single output — the
`prepare_handoff_request` envelope — validates against the frozen,
framework-neutral T00 schema (schemas/context-handoff/), so Multica-only
fields cannot leak into the Native API or Memory Core shapes. The frozen
boundary scan (tools/chandoff.py scan) therefore does NOT scan this file.

Boundary approved by YZT-58 (owner: 02 Context Engineer):

- reads ONLY `multica issue get`, `multica issue comment list --thread`
  and `multica version`; every issued argv is checked against a read-only
  allowlist and anything else is refused (write commands never run);
- never interprets Memory Scope policy, never selects Canonical context,
  never calls prepare_handoff, never publishes, never rebuilds Memory;
- project mapping is explicit-only (caller map file or explicit registry
  project id). A Multica project UUID, a missing project or a null project
  is never treated as a Memory registry id; no stable mapping -> bounded
  error, fail closed;
- requirements / acceptance criteria / decisions enter the snapshot only
  through frozen explicit markers in the authoritative issue body or
  caller-selected comment ids, each with exact provenance in the trace;
  ordinary progress comments are never fetched and can never move the
  task fingerprint;
- the current assignee is never mapped: the frozen request contract has no
  assignee field and no assignee field is added to Core schemas;
- no LLM/model call, no network library, no Canonical write, no Memory
  rebuild. Errors are bounded AdapterError codes; nothing is guessed.

CLI compatibility (approved implementation plan §30.4): contract-based,
never version-pinned. The adapter validates the returned issue JSON
against its required contract field set and stops bounded
(`incompatible_cli_contract`) when a deployed CLI drifts; the observed
deployed version is recorded in the trace (historical capture provenance:
v0.4.41 at YZT-58).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chandoff import canonical_json, fingerprint_from_request, forbidden_concept_scan  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

ADAPTER_VERSION = "T05/1.0"
REQUEST_SCHEMA = "context-handoff/prepare-handoff-request.schema.json"

SNAPSHOT_KEYS = ("title", "description", "requirements", "acceptance_criteria",
                 "relevant_decisions", "parent_task_ref")

ISSUE_CONTRACT_FIELDS = ("id", "identifier", "title", "description",
                         "parent_issue_id", "project_id")

READONLY_COMMANDS = (
    ("issue", "get"),
    ("issue", "comment", "list"),
    ("version",),
)

# Frozen explicit heading markers (implementation plan §30.2/§30.3: only
# explicit headings/markers from authoritative issue content may define the
# task). A heading matches when its normalized text equals the marker or
# starts with the marker followed by whitespace. Requirements are checked
# before acceptance criteria; one heading feeds exactly one bucket.
DEFAULT_REQUIREMENT_MARKERS = (
    "requirements", "required work", "required changes",
    "required tests", "required snapshot mapping",
)
DEFAULT_ACCEPTANCE_MARKERS = (
    "acceptance criteria", "acceptance evidence", "done criteria", "done",
)

MAX_EXCERPT = 240
MAX_TRACE_TEXT = 20000

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
LIST_ITEM_RE = re.compile(r"^\s{0,6}(?:[-*+]|\d{1,3}[.)])\s+(.+)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
CONTAINER_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+:\s*$")


def _bounded(value, limit: int = MAX_EXCERPT):
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


class ProjectMappingError(AdapterError):
    code = "project_mapping_unresolved"


class SelectionError(AdapterError):
    code = "selected_comment_not_found"


class ForbiddenOptionsError(AdapterError):
    code = "forbidden_options_key"


class SchemaViolationError(AdapterError):
    code = "request_schema_violation"


def _allowlisted(argv: list) -> bool:
    return any(tuple(argv[:len(cmd)]) == cmd for cmd in READONLY_COMMANDS)


def _default_runner(argv: list) -> tuple:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
    except OSError as exc:
        raise CliUnavailableError(f"multica CLI unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CliCommandError("multica CLI timed out after 60s") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class MulticaCli:
    """Read-only deployed CLI boundary. Records every issued argv."""

    def __init__(self, executable: str = "multica", runner: Callable | None = None):
        self.executable = executable
        self.runner = runner or _default_runner
        self.commands: list = []

    def _run(self, argv: list) -> str:
        if not _allowlisted(argv):
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
                exit_code=code, stderr_excerpt=err[:MAX_EXCERPT])
        return out

    def issue_get(self, issue_id: str) -> dict:
        out = self._run(["issue", "get", str(issue_id), "--output", "json"])
        return parse_issue_json(out)

    def comment_thread(self, issue_id: str, comment_id: str) -> list:
        # --full: resolved threads are returned verbatim, so selecting any
        # comment (root or folded reply) is deterministic.
        out = self._run(["issue", "comment", "list", str(issue_id),
                         "--thread", str(comment_id), "--full",
                         "--output", "json"])
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise CliJsonError(f"comment list returned malformed JSON: {exc}") from exc
        if not isinstance(data, list):
            raise CliContractError("comment list JSON is not an array",
                                   kind=type(data).__name__)
        return data

    def version(self) -> str | None:
        out = self._run(["version", "--output", "json"])
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return None
        value = data.get("version") if isinstance(data, dict) else None
        return value if isinstance(value, str) else None


def parse_issue_json(text: str) -> dict:
    """Validate the deployed CLI issue contract; stop bounded on drift."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CliJsonError(f"issue get returned malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CliContractError("issue get JSON is not an object",
                               kind=type(data).__name__)
    missing = [f for f in ISSUE_CONTRACT_FIELDS if f not in data]
    if missing:
        raise CliContractError(
            "deployed CLI issue JSON is missing contract fields", missing=missing)
    if not isinstance(data["identifier"], str) or not data["identifier"].strip():
        raise CliContractError("issue identifier is not a non-blank string")
    if not isinstance(data["title"], str) or not data["title"].strip():
        raise CliContractError("issue title is missing or blank")
    if data["description"] is None:
        data["description"] = ""
    if not isinstance(data["description"], str):
        raise CliContractError("issue description is not a string")
    for field in ("parent_issue_id", "project_id"):
        if data[field] is not None and not isinstance(data[field], str):
            raise CliContractError(f"issue {field} is neither a string nor null")
    return data


def task_ref_of(identifier: str) -> str:
    """Stable opaque external reference: multica://issue/<identifier>."""
    return f"multica://issue/{identifier.strip()}"


def _norm_heading(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _marker_hit(hnorm: str, markers) -> str | None:
    for marker in markers:
        m = " ".join(str(marker).strip().lower().split())
        if m and (hnorm == m or hnorm.startswith(m + " ")):
            return m
    return None


def _sections(text: str) -> list:
    """ATX heading sections with line numbers; fences never hide headings."""
    sections: list = []
    current = None
    inside = False
    for number, raw in enumerate(text.split("\n"), start=1):
        if FENCE_RE.match(raw):
            inside = not inside
            if current is not None:
                current["lines"].append((number, raw))
            continue
        match = HEADING_RE.match(raw)
        if match and not inside:
            current = {"line": number, "heading": match.group(2), "lines": []}
            sections.append(current)
            continue
        if current is not None:
            current["lines"].append((number, raw))
    return sections


def _list_items(lines: list) -> list:
    items = []
    inside = False
    for number, raw in lines:
        if FENCE_RE.match(raw):
            inside = not inside
            continue
        if inside:
            continue
        match = LIST_ITEM_RE.match(raw)
        if match:
            value = match.group(1).strip()
            if value:
                items.append((number, value))
    return items


def _fence_items(lines: list) -> list:
    """Fallback for sections with no list items: leaf lines of fenced
    blocks, dropping container keys (e.g. `done:`)."""
    items = []
    inside = False
    for number, raw in lines:
        if FENCE_RE.match(raw):
            inside = not inside
            continue
        if not inside:
            continue
        value = raw.strip()
        if not value or CONTAINER_KEY_RE.match(value):
            continue
        items.append((number, value))
    return items


def _section_items(section: dict) -> list:
    return _list_items(section["lines"]) or _fence_items(section["lines"])


def extract_structured(description: str) -> tuple:
    """Deterministic requirements / acceptance criteria extraction."""
    requirements, acceptance = [], []
    for section in _sections(description or ""):
        items = _section_items(section)
        if not items:
            continue
        hnorm = _norm_heading(section["heading"])
        if _marker_hit(hnorm, DEFAULT_REQUIREMENT_MARKERS):
            bucket, marker = requirements, "requirements"
        elif _marker_hit(hnorm, DEFAULT_ACCEPTANCE_MARKERS):
            bucket, marker = acceptance, "acceptance_criteria"
        else:
            continue
        for number, value in items:
            bucket.append({
                "value": value,
                "provenance": {
                    "source": "issue_description",
                    "marker": marker,
                    "heading": section["heading"].strip(),
                    "heading_line": section["line"],
                    "item_line": number,
                },
            })
    return requirements, acceptance


def extract_marker_decisions(description: str, markers) -> list:
    """Decisions from explicit caller-supplied heading markers only."""
    out = []
    normalized = []
    for marker in markers or []:
        m = " ".join(str(marker).strip().lower().split())
        if m and m not in normalized:
            normalized.append(m)
    if not normalized:
        return out
    for section in _sections(description or ""):
        items = _section_items(section)
        if not items:
            continue
        hnorm = _norm_heading(section["heading"])
        hit = next((m for m in normalized if m in hnorm), None)
        if hit is None:
            continue
        for number, value in items:
            out.append({
                "value": value,
                "provenance": {
                    "source": "issue_description",
                    "decision_marker": hit,
                    "heading": section["heading"].strip(),
                    "heading_line": section["line"],
                    "item_line": number,
                },
            })
        break  # first matching marker (caller order) owns the heading
    return out


def selected_comment_decisions(issue_id: str, comment_ids, cli: MulticaCli,
                               thread_files: dict | None, offline: bool) -> list:
    """Only caller-selected comment ids are admitted; exact provenance."""
    out = []
    seen = set()
    for comment_id in comment_ids or []:
        comment_id = str(comment_id).strip()
        if not comment_id or comment_id in seen:
            continue
        seen.add(comment_id)
        if offline:
            path = (thread_files or {}).get(comment_id)
            if not path:
                raise AdapterError(
                    "offline thread fixture required for selected comment",
                    comment_id=comment_id,
                    hint="--thread-file COMMENT_ID=PATH")
            try:
                data = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CliJsonError(
                    f"thread fixture unreadable/malformed: {exc}",
                    comment_id=comment_id) from exc
        else:
            data = cli.comment_thread(issue_id, comment_id)
        doc = next((c for c in data
                    if isinstance(c, dict) and c.get("id") == comment_id), None)
        if doc is None:
            raise SelectionError("selected comment not found in thread response",
                                 comment_id=comment_id)
        content = doc.get("content")
        if not isinstance(content, str) or not content.strip():
            raise SelectionError("selected comment has no textual content",
                                 comment_id=comment_id)
        out.append({
            "value": content,
            "provenance": {
                "source": "selected_comment",
                "comment_id": comment_id,
                "author_id": doc.get("author_id"),
                "author_type": doc.get("author_type"),
                "created_at": doc.get("created_at"),
                "issue_id": issue_id,
            },
        })
    return out


def resolve_project(issue: dict, *, explicit_project_id: str | None,
                    project_map: dict | None, map_source: str | None) -> dict:
    """Fail-closed explicit project mapping. Never guesses a registry id."""
    if explicit_project_id:
        return {
            "mode": "explicit_caller",
            "multica_project_id": issue.get("project_id"),
            "registry_project_id": explicit_project_id,
        }
    multica_project_id = issue.get("project_id")
    if not multica_project_id:
        raise ProjectMappingError(
            "multica issue carries no project id; an explicit Memory registry "
            "project_id (or map entry) is required — a missing/null project is "
            "never a registry id by default",
            multica_project_id=None)
    mapped = (project_map or {}).get(multica_project_id)
    if not mapped or not isinstance(mapped, str):
        raise ProjectMappingError(
            "no explicit multica project -> registry project_id mapping exists "
            "for this issue's project; supply --project-id or extend the map",
            multica_project_id=multica_project_id,
            map_source=map_source or "none")
    return {
        "mode": "explicit_map",
        "multica_project_id": multica_project_id,
        "registry_project_id": mapped,
    }


def _validate_request(request: dict) -> None:
    schema = load_schema_file(REQUEST_SCHEMA)
    errors = Schema(schema, schema).validate(request, path="$")
    if errors:
        raise SchemaViolationError(
            "assembled request violates the frozen prepare_handoff_request "
            "schema: " + "; ".join(errors[:8]))
    _validate_role_vocabulary(request)


# Roles are data, not a schema-enumerated roster (V2.2: team role changes must
# not be hard-coded into Memory Core). The shared role_id $def is pattern-based,
# so the adapter adds this compensating control: target/caller roles must
# resolve to a current role profile (team-context/roles/<role>.yaml), the same
# "resolve current Role Profile" step PLAN performs. Adding a role profile is
# therefore sufficient to make a new logical role dispatchable; no schema edit.
ROLE_PROFILE_DIR = Path(__file__).resolve().parents[1] / "team-context" / "roles"


def current_role_ids() -> set:
    return {p.stem for p in sorted(ROLE_PROFILE_DIR.glob("*.yaml"))}


def _validate_role_vocabulary(request: dict) -> None:
    known = current_role_ids()
    for side in ("target", "caller"):
        role = ((request.get(side) or {}).get("role") or "")
        if role not in known:
            raise SchemaViolationError(
                f"{side}.role {role!r} is not a currently resolvable logical "
                f"role: no team-context/roles/{role}.yaml profile exists; "
                "role vocabulary resolves from role-profile data, never from "
                "a hard-coded roster")


def build_snapshot_request(*, issue_id: str, target_role: str, caller_role: str,
                           purpose: str, cli: MulticaCli | None = None,
                           project_map: dict | None = None,
                           map_source: str | None = None,
                           explicit_project_id: str | None = None,
                           decision_comment_ids=None, decision_markers=None,
                           options: dict | None = None,
                           issue_file: str | None = None,
                           parent_file: str | None = None,
                           thread_files: dict | None = None) -> dict:
    """Multica issue -> frozen-schema prepare_handoff_request (+ trace).

    Read-only. Framework-specific in, framework-neutral out. Raises
    bounded AdapterError subclasses on every failure mode.
    """
    offline = issue_file is not None
    cli = cli or MulticaCli()
    commands_start = len(cli.commands)

    if offline:
        try:
            issue = parse_issue_json(Path(issue_file).read_text(encoding="utf-8"))
        except OSError as exc:
            raise CliJsonError(f"issue fixture unreadable: {exc}") from exc
        issue_source = "file"
        version, version_note = None, "offline fixture mode"
    else:
        try:
            version = cli.version()
        except AdapterError as exc:
            version, version_note = None, f"version stamp unavailable: {exc.message}"
        else:
            version_note = None
        issue = cli.issue_get(issue_id)
        issue_source = "cli"

    project = resolve_project(issue, explicit_project_id=explicit_project_id,
                              project_map=project_map, map_source=map_source)

    parent_ref = None
    if issue.get("parent_issue_id"):
        if offline:
            if not parent_file:
                raise AdapterError(
                    "offline parent fixture required: --parent-file",
                    parent_issue_id=issue["parent_issue_id"])
            try:
                parent = parse_issue_json(
                    Path(parent_file).read_text(encoding="utf-8"))
            except OSError as exc:
                raise CliJsonError(f"parent fixture unreadable: {exc}") from exc
        else:
            try:
                parent = cli.issue_get(issue["parent_issue_id"])
            except AdapterError as exc:
                raise AdapterError(
                    f"parent issue fetch failed: {exc.message}",
                    parent_issue_id=issue["parent_issue_id"],
                    cause=exc.code) from exc
        parent_ref = task_ref_of(parent["identifier"])

    requirements, acceptance = extract_structured(issue["description"])
    decisions = (extract_marker_decisions(issue["description"], decision_markers)
                 + selected_comment_decisions(issue["id"], decision_comment_ids,
                                              cli, thread_files, offline))

    if options is not None:
        if not isinstance(options, dict):
            raise AdapterError("options must be a JSON object",
                               kind=type(options).__name__)
        for key in options:
            if forbidden_concept_scan(str(key)):
                raise ForbiddenOptionsError(
                    f"options key {key!r} uses dispatch-framework vocabulary; "
                    "the request envelope stays framework-neutral", key=key)

    snapshot = {
        "title": issue["title"],
        "description": issue["description"],
        "requirements": [item["value"] for item in requirements],
        "acceptance_criteria": [item["value"] for item in acceptance],
        "relevant_decisions": [item["value"] for item in decisions],
    }
    if parent_ref:
        snapshot["parent_task_ref"] = parent_ref

    request = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": task_ref_of(issue["identifier"]),
        "project": {"project_id": project["registry_project_id"]},
        "target": {"role": target_role},
        "purpose": purpose,
        "task_snapshot": snapshot,
        "caller": {"role": caller_role},
        "options": options if options is not None else {},
    }
    _validate_request(request)
    fingerprint = fingerprint_from_request(request)

    trace = {
        "adapter": "tools/chandoff_adapter.py",
        "adapter_version": ADAPTER_VERSION,
        "issue": {"id": issue["id"], "identifier": issue["identifier"],
                  "source": issue_source},
        "cli": {
            "commands": cli.commands[commands_start:],
            "readonly_allowlist_enforced": True,
            "version": version,
            "version_note": version_note,
        },
        "project_mapping": project,
        "parent": ({"present": True, "task_ref": parent_ref}
                   if parent_ref else {"present": False}),
        "extraction": {
            "requirement_markers": list(DEFAULT_REQUIREMENT_MARKERS),
            "acceptance_markers": list(DEFAULT_ACCEPTANCE_MARKERS),
            "requirements": [item["provenance"] for item in requirements],
            "acceptance_criteria": [item["provenance"] for item in acceptance],
            "relevant_decisions": [item["provenance"] for item in decisions],
        },
        "excluded_by_default": {
            "comment_fetches": len({str(c).strip() for c in (decision_comment_ids or []) if str(c).strip()}),
            "ordinary_comments_admitted": 0,
            "ordinary_comments_in_fingerprint": 0,
        },
        "assignee": {
            "mapped": False,
            "reason": "frozen prepare_handoff_request has no assignee field; "
                      "no assignee field is added to Core schemas",
        },
        "guarantees": {
            "llm_called": False,
            "canonical_writes": 0,
            "memory_reads": 0,
            "memory_rebuilds": 0,
            "multica_write_commands": 0,
        },
    }
    return {
        "ok": True,
        "request": request,
        "task_fingerprint": fingerprint,
        "trace": trace,
    }


def _read_thread_specs(specs) -> dict:
    thread_files: dict = {}
    for spec in specs or []:
        comment_id, sep, path = str(spec).partition("=")
        if not sep or not comment_id.strip() or not path.strip():
            raise AdapterError("bad --thread-file spec, want COMMENT_ID=PATH",
                               spec=spec)
        thread_files[comment_id.strip()] = path.strip()
    return thread_files


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T05 read-only Multica Issue Snapshot Adapter (YZT-58)")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser(
        "build", help="build a frozen-schema prepare_handoff_request")
    build.add_argument("--issue", required=True,
                       help="issue id or identifier (e.g. YZT-58)")
    build.add_argument("--target-role", required=True)
    build.add_argument("--caller-role", required=True)
    build.add_argument("--purpose", required=True)
    build.add_argument("--project-id", default=None,
                       help="explicit Memory registry project_id (fail-closed mapping)")
    build.add_argument("--project-map", default=None,
                       help="JSON file: multica project id -> registry project_id")
    build.add_argument("--decision-comment", action="append", default=None,
                       metavar="COMMENT_ID")
    build.add_argument("--decision-marker", action="append", default=None,
                       metavar="TEXT")
    build.add_argument("--options-json", default=None)
    build.add_argument("--issue-file", default=None,
                       help="offline captured `issue get` JSON (no CLI calls)")
    build.add_argument("--parent-file", default=None,
                       help="offline captured parent `issue get` JSON")
    build.add_argument("--thread-file", action="append", default=None,
                       metavar="COMMENT_ID=PATH",
                       help="offline captured `comment list --thread` JSON")
    build.add_argument("--executable", default="multica")
    args = parser.parse_args(argv)

    try:
        project_map, map_source = None, None
        if args.project_map:
            project_map = json.loads(
                Path(args.project_map).read_text(encoding="utf-8"))
            if not isinstance(project_map, dict):
                raise ProjectMappingError("project map file must be a JSON object",
                                          map_source=args.project_map)
            map_source = args.project_map
        options = json.loads(args.options_json) if args.options_json else None
        envelope = build_snapshot_request(
            issue_id=args.issue,
            target_role=args.target_role,
            caller_role=args.caller_role,
            purpose=args.purpose,
            project_map=project_map,
            map_source=map_source,
            explicit_project_id=args.project_id,
            decision_comment_ids=args.decision_comment,
            decision_markers=args.decision_marker,
            options=options,
            issue_file=args.issue_file,
            parent_file=args.parent_file,
            thread_files=_read_thread_specs(args.thread_file),
        )
    except AdapterError as exc:
        print(json.dumps({"ok": False, "error": exc.envelope()},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    print(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
