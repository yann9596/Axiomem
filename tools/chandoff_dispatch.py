#!/usr/bin/env python3
"""U06 — Multica dispatch CLI boundary + deterministic transaction ledger (YZT-74).

Rebases the historical T09 (YZT-64) allowlist onto the V2.2 Assignment route.
Framework-specific adapter layer (like T05/T06/T08/U05): it names Multica
runtime concepts by design and is deliberately NOT scanned by the frozen
framework-neutral boundary scan (`tools/chandoff.py scan`).

This module owns the ONLY command surface through which the U06 assignment
orchestrator may reach the deployed Multica CLI:

- reads (always allowed): `issue get`, `issue comment list`, `issue runs`,
  `version`. `issue runs --active --siblings --output json` is the
  unexpected-run precheck / correlation surface. The JSON contract is the
  observed list-of-objects shape (id, issue_id, agent_id, status); missing
  fields or a non-list fail closed as undetermined, never guessed;
- issue create: at most ONE unassigned argv (`--title` + `--description-file`,
  optional `--parent` / `--project` / `--priority`, always `--output json`).
  Assignee, attachment, inline/stdin description, `--status` / `--stage` and
  any `mention://` are refused before a process starts;
- issue update: optional NON_TRIGGER_MUTATION of an existing unassigned
  issue. Always `--no-start`; assignee / mention / status / stage refused;
- the sole run trigger: at most ONE `issue assign <id> --to-id <uuid>
  --output json`. `--no-start` / `--unassign` / fuzzy `--to` refused. A
  failed command or marker-less response fails closed without retry;
- no runner is ever constructed implicitly. Live execution requires a
  separate explicit authorization document. U06 never exercises live mode.

Exactly-once is claimed ONLY against this observable ledger evidence.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chandoff_adapter import parse_issue_json  # noqa: E402

DISPATCH_VERSION = "U06/2.2"

MAX_TEXT = 200
MAX_ARGV_TEXT = 240

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{12}$")
MENTION_RE = re.compile(r"mention://")

READ_COMMANDS = (
    ("issue", "get"),
    ("issue", "comment", "list"),
    ("issue", "runs"),
    ("version",),
)
CREATE_FORBIDDEN_FLAGS = (
    "--assignee", "--assignee-id", "--attachment", "--attachment-id",
    "--description", "--description-stdin", "--allow-external-file",
    "--allow-duplicate", "--status", "--stage", "--start-date", "--due-date",
)
CREATE_ALLOWED_FLAGS = ("--title", "--description-file", "--parent",
                        "--project", "--priority", "--output")
UPDATE_FORBIDDEN_FLAGS = (
    "--assignee", "--assignee-id", "--attachment", "--attachment-id",
    "--description", "--description-stdin", "--allow-external-file",
    "--status", "--stage", "--start-date", "--due-date", "--position",
)
UPDATE_ALLOWED_FLAGS = ("--title", "--description-file", "--parent",
                        "--project", "--priority", "--output", "--no-start")
ASSIGN_FORBIDDEN_FLAGS = ("--no-start", "--unassign", "--to")

CREATE_CONTRACT_FIELDS = ("id", "identifier", "title", "description")
RUN_CONTRACT_FIELDS = ("id", "issue_id", "agent_id", "status")
ACTIVE_RUN_STATUSES = frozenset({
    "queued", "dispatched", "running", "waiting_local_directory",
})
TRUNCATION_RE = re.compile(r"truncat", re.IGNORECASE)


def _bounded(value, limit: int = MAX_TEXT):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    return value


class DispatchError(Exception):
    """Bounded stop. The failure is recorded, never guessed away."""

    code = "dispatch_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = _bounded(str(message), MAX_ARGV_TEXT)
        self.details = {k: _bounded(v, MAX_ARGV_TEXT) for k, v in details.items()}

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class NotAuthorizedError(DispatchError):
    code = "live_not_authorized"


class ForbiddenCreateArgvError(DispatchError):
    code = "forbidden_create_argv"


class CreateCommandFailedError(DispatchError):
    code = "create_command_failed"


class CreateResponseInvalidError(DispatchError):
    code = "create_response_invalid"


class CreateNotUnassignedError(DispatchError):
    code = "create_response_not_unassigned"


class AssignArgvInvalidError(DispatchError):
    code = "assignment_argv_invalid"


class AssignCommandFailedError(DispatchError):
    code = "assignment_command_failed"


class AssignResponseInvalidError(DispatchError):
    code = "assignment_response_unconfirmable"


class UpdateArgvInvalidError(DispatchError):
    code = "forbidden_update_argv"


class UpdateCommandFailedError(DispatchError):
    code = "update_command_failed"


class UpdateResponseInvalidError(DispatchError):
    code = "update_response_invalid"


class UpdateNotUnassignedError(DispatchError):
    code = "update_response_not_unassigned"


class RunsResponseInvalidError(DispatchError):
    code = "runs_response_invalid"


class RunStateUndeterminedError(DispatchError):
    code = "run_state_undetermined"


class UnexpectedRunError(DispatchError):
    code = "unexpected_run"


class RunCorrelationError(DispatchError):
    code = "run_correlation_failed"


class LedgerError(DispatchError):
    code = "ledger_error"


def _strip_executable(argv: list, executable: str) -> list:
    if argv and str(argv[0]) == executable:
        return [str(a) for a in argv[1:]]
    return [str(a) for a in argv]


def classify_command(argv: list, executable: str = "multica") -> str:
    """Deterministic command class for one full argv (executable included)."""
    core = _strip_executable(argv, executable)
    if tuple(core[:3]) == ("issue", "comment", "add"):
        return "comment_publish"
    if tuple(core[:2]) == ("issue", "create"):
        return "issue_create"
    if tuple(core[:2]) == ("issue", "update"):
        return "issue_update"
    if tuple(core[:2]) == ("issue", "assign"):
        return "assignment_trigger"
    if any(tuple(core[:len(cmd)]) == cmd for cmd in READ_COMMANDS):
        return "read"
    return "other"


def _require_bare_id(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DispatchError(f"{field} must be a non-blank string", field=field)
    value = value.strip()
    if re.search(r"\s", value) or value.startswith("--"):
        raise DispatchError(f"{field} is not a bare identifier", field=field)
    return value


def _require_text(value, field: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise DispatchError(
            f"{field} must be a non-blank string of at most {limit} chars",
            field=field)
    return value


class TransactionLedger:
    """Deterministic, JSON-safe command/state ledger (one record per event).

    Records contain ids, argv tokens and exit codes only — never comment
    bodies, task descriptions, credentials or other secrets. The ledger is
    the auditable evidence that makes retries idempotent and stop states
    reviewable.
    """

    def __init__(self):
        self._records: list = []

    def append(self, record: dict) -> dict:
        if not isinstance(record, dict):
            raise LedgerError("ledger record must be a dict")
        entry = {"seq": len(self._records) + 1}
        entry.update(record)
        try:
            json.dumps(entry, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise LedgerError(f"ledger record not JSON-safe: {exc}") from exc
        self._records.append(entry)
        return entry

    @property
    def records(self) -> list:
        return list(self._records)

    def commands(self) -> list:
        return [r for r in self._records if r.get("kind") == "command"]

    def to_jsonl(self) -> str:
        return "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
            for r in self._records)

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_jsonl(), encoding="utf-8", newline="\n")
        return path

    @classmethod
    def from_jsonl(cls, text: str) -> "TransactionLedger":
        ledger = cls()
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                ledger._records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerError(f"ledger JSONL malformed: {exc}") from exc
        return ledger

    @classmethod
    def load(cls, path) -> "TransactionLedger":
        return cls.from_jsonl(Path(path).read_text(encoding="utf-8"))


class RecordingRunner:
    """Wrap an injected runner so every issued argv lands in the ledger.

    The inner runner receives the full argv (executable included), matching
    the T05/T06 CLI seam. Command and result/error records are appended in
    strict issue order, so the ledger proves the frozen command order.
    """

    def __init__(self, inner, ledger: TransactionLedger, executable: str = "multica",
                 transaction_id: str = ""):
        self.inner = inner
        self.ledger = ledger
        self.executable = executable
        self.transaction_id = transaction_id

    def __call__(self, argv: list) -> tuple:
        argv = [str(a) for a in argv]
        self.ledger.append({
            "kind": "command",
            "transaction_id": self.transaction_id,
            "command_class": classify_command(argv, self.executable),
            "argv": argv,
        })
        try:
            code, out, err = self.inner(argv)
        except Exception as exc:
            self.ledger.append({
                "kind": "command_error",
                "transaction_id": self.transaction_id,
                "error": _bounded(f"{type(exc).__name__}: {exc}"),
            })
            raise
        self.ledger.append({
            "kind": "command_result",
            "transaction_id": self.transaction_id,
            "exit_code": int(code),
        })
        return code, out, err


class FixtureRunner:
    """Canned runner for simulation/CLI dry-runs and replay drills.

    Deterministic response table: the FIRST entry whose `match` prefix
    equals the argv core (executable stripped) wins and returns its frozen
    (code, stdout, stderr). Unmatched argv returns (2, "", "unexpected").
    """

    def __init__(self, table: list):
        self.table = []
        for entry in table or []:
            self.table.append({
                "match": [str(x) for x in (entry.get("match") or [])],
                "code": int(entry.get("code", 0)),
                "stdout": str(entry.get("stdout", "")),
                "stderr": str(entry.get("stderr", "")),
            })

    def __call__(self, argv: list) -> tuple:
        core = _strip_executable(argv, "multica")
        for entry in self.table:
            if tuple(core[:len(entry["match"])]) == tuple(entry["match"]):
                return entry["code"], entry["stdout"], entry["stderr"]
        return 2, "", "unexpected fixture command"


def _subprocess_runner(executable: str):
    def run(argv: list) -> tuple:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  encoding="utf-8", timeout=120)
        except OSError as exc:
            raise DispatchError(f"multica CLI unavailable: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DispatchError("multica CLI timed out after 120s") from exc
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    return run


AUTHORIZATION_REQUIRED_KEYS = ("authorize_live_mutations", "authorized_by",
                               "authorization_ref", "scope")


def authorization_ok(authorization) -> bool:
    """The explicit, separate live-authorization document contract."""
    if not isinstance(authorization, dict):
        return False
    if authorization.get("authorize_live_mutations") is not True:
        return False
    for key in ("authorized_by", "authorization_ref", "scope"):
        value = authorization.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def authorized_runner(executable: str = "multica", authorization=None,
                      runner=None):
    """Return the runner for live mode, or refuse. Simulation never calls this."""
    if not authorization_ok(authorization):
        raise NotAuthorizedError(
            "live issue-create/assignment execution requires a separate "
            "explicit authorization document (authorize_live_mutations=true, "
            "authorized_by, authorization_ref, scope)")
    return runner if runner is not None else _subprocess_runner(executable)


class DispatchCli:
    """Deployed-CLI boundary for the assignment main path.

    Reads use the frozen T05 issue contract. Create/publish/trigger argv are
    validated against the frozen allowlists BEFORE any process starts; the
    create description is passed through a UTF-8 temp file inside the working
    directory that is always deleted afterwards.
    """

    def __init__(self, executable: str = "multica", runner=None,
                 workdir=None):
        if runner is None:
            raise NotAuthorizedError(
                "DispatchCli requires an explicitly injected runner; "
                "no implicit live runner is ever constructed")
        self.executable = executable
        self.runner = runner
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.commands: list = []

    def _run(self, argv: list) -> tuple:
        argv = [str(a) for a in argv]
        self.commands.append(argv)
        code, out, err = self.runner([self.executable] + argv)
        return int(code), out or "", err or ""

    def issue_get(self, issue_id: str) -> dict:
        issue_id = _require_bare_id(issue_id, "issue_id")
        code, out, err = self._run(
            ["issue", "get", issue_id, "--output", "json"])
        if code != 0:
            raise DispatchError(
                f"issue get failed (exit {code}): {_bounded(err, 120)}",
                exit_code=code)
        return parse_issue_json(out)

    def create_issue(self, *, title: str, description: str,
                     parent_issue_id: str | None = None,
                     project_id: str | None = None,
                     priority: str | None = None) -> dict:
        """Exactly one unassigned issue-create argv; canonical id comes back."""
        title = _require_text(title, "title")
        if not isinstance(description, str) or not description.strip():
            raise DispatchError("description must be a non-blank string")
        for field, value in (("title", title), ("description", description)):
            if MENTION_RE.search(value):
                raise ForbiddenCreateArgvError(
                    f"{field} carries a mention link; the assignment path "
                    "must contain no mention of any kind",
                    field=field)
        argv = ["issue", "create", "--title", title,
                "--description-file", "", "--output", "json"]
        if parent_issue_id is not None:
            argv += ["--parent", _require_bare_id(parent_issue_id, "parent_issue_id")]
        if project_id is not None:
            argv += ["--project", _require_text(project_id, "project_id", 64)]
        if priority is not None:
            argv += ["--priority", _require_text(priority, "priority", 40)]
        flags = {argv[i] for i in range(len(argv)) if argv[i].startswith("--")}
        for flag in flags:
            if flag not in CREATE_ALLOWED_FLAGS:
                raise ForbiddenCreateArgvError(
                    f"create flag {flag!r} is outside the unassigned-create "
                    "allowlist", flag=flag)
        if "--assignee" in argv or "--assignee-id" in argv:
            raise ForbiddenCreateArgvError(
                "an unassigned create never carries an assignee flag")

        temp_name = hashlib.sha1(description.encode("utf-8")).hexdigest()[:12]
        temp_path = self.workdir / f".u06-create-{temp_name}.md"
        try:
            temp_path.write_bytes(description.encode("utf-8"))
            resolved = temp_path.resolve()
            if not resolved.is_relative_to(self.workdir.resolve()):
                raise DispatchError("temp description file escaped the working directory")
            argv[argv.index("--description-file") + 1] = str(temp_path)
            code, out, err = self._run(argv)
        finally:
            temp_path.unlink(missing_ok=True)
        if code != 0:
            raise CreateCommandFailedError(
                f"issue create failed (exit {code}): {_bounded(err, 120)}",
                exit_code=code)
        return self._parse_create_response(out)

    def assign_issue(self, issue_id: str, agent_id: str) -> dict:
        """The sole run trigger: exactly one `issue assign --to-id <uuid>`.

        Returns {"outcome": "confirmed", "response": <parsed>} only when the
        response positively identifies this assignment. Anything else raises
        AssignResponseInvalidError (fail closed: TRIGGER_CONFIRMATION_REQUIRED).
        """
        issue_id = _require_bare_id(issue_id, "issue_id")
        agent_id = _require_bare_id(agent_id, "agent_id")
        if not UUID_RE.match(agent_id):
            raise AssignArgvInvalidError(
                "agent_id is not a UUID; the trigger uses only the exact "
                "--to-id form resolved from the U05 staged role map",
                agent_id=agent_id)
        argv = ["issue", "assign", issue_id, "--to-id", agent_id,
                "--output", "json"]
        present = set(argv)
        for flag in ASSIGN_FORBIDDEN_FLAGS:
            if flag in present:
                raise AssignArgvInvalidError(
                    f"trigger flag {flag!r} is refused", flag=flag)
        code, out, err = self._run(argv)
        if code != 0:
            raise AssignCommandFailedError(
                f"issue assign failed (exit {code}): {_bounded(err, 120)}",
                exit_code=code)
        try:
            data = json.loads(out)
        except json.JSONDecodeError as exc:
            raise AssignResponseInvalidError(
                f"issue assign returned non-JSON output: {exc}; the trigger "
                "state is unconfirmed and must not be retried") from exc
        if not isinstance(data, dict):
            raise AssignResponseInvalidError(
                "issue assign response is not a JSON object; the trigger "
                "state is unconfirmed and must not be retried",
                kind=type(data).__name__)
        if not _assignment_confirmed(data, issue_id, agent_id):
            raise AssignResponseInvalidError(
                "issue assign response carries no recognizable confirmation "
                "marker for this issue/agent; fail closed", response_keys=sorted(data)[:8])
        return {"outcome": "confirmed", "response": data}

    def update_issue(self, issue_id: str, *, title: str | None = None,
                     description: str | None = None,
                     parent_issue_id: str | None = None,
                     project_id: str | None = None,
                     priority: str | None = None) -> dict:
        """NON_TRIGGER_MUTATION: unassigned update with --no-start."""
        issue_id = _require_bare_id(issue_id, "issue_id")
        argv = ["issue", "update", issue_id, "--no-start", "--output", "json"]
        temp_path = None
        if title is not None:
            title = _require_text(title, "title")
            if MENTION_RE.search(title):
                raise UpdateArgvInvalidError(
                    "title carries a mention link; the assignment path "
                    "must contain no mention of any kind", field="title")
            argv += ["--title", title]
        if description is not None:
            if not isinstance(description, str) or not description.strip():
                raise DispatchError("description must be a non-blank string")
            if MENTION_RE.search(description):
                raise UpdateArgvInvalidError(
                    "description carries a mention link; the assignment path "
                    "must contain no mention of any kind", field="description")
            temp_name = hashlib.sha1(description.encode("utf-8")).hexdigest()[:12]
            temp_path = self.workdir / f".u06-update-{temp_name}.md"
            argv += ["--description-file", str(temp_path)]
        if parent_issue_id is not None:
            argv += ["--parent", _require_bare_id(parent_issue_id, "parent_issue_id")]
        if project_id is not None:
            argv += ["--project", _require_text(project_id, "project_id", 64)]
        if priority is not None:
            argv += ["--priority", _require_text(priority, "priority", 40)]
        flags = {argv[i] for i in range(len(argv)) if argv[i].startswith("--")}
        for flag in flags:
            if flag not in UPDATE_ALLOWED_FLAGS:
                raise UpdateArgvInvalidError(
                    f"update flag {flag!r} is outside the non-trigger "
                    "update allowlist", flag=flag)
        if "--assignee" in argv or "--assignee-id" in argv:
            raise UpdateArgvInvalidError(
                "an unassigned update never carries an assignee flag")
        if "--no-start" not in argv:
            raise UpdateArgvInvalidError(
                "issue update must carry --no-start so it cannot start a run")
        try:
            if temp_path is not None:
                temp_path.write_bytes(description.encode("utf-8"))
                resolved = temp_path.resolve()
                if not resolved.is_relative_to(self.workdir.resolve()):
                    raise DispatchError(
                        "temp description file escaped the working directory")
            code, out, err = self._run(argv)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        if code != 0:
            raise UpdateCommandFailedError(
                f"issue update failed (exit {code}): {_bounded(err, 120)}",
                exit_code=code)
        return self._parse_unassigned_issue_response(out, kind="update")

    def _parse_create_response(self, text: str) -> dict:
        return self._parse_unassigned_issue_response(text, kind="create")

    def _parse_unassigned_issue_response(self, text: str, *, kind: str) -> dict:
        invalid = (CreateResponseInvalidError if kind == "create"
                   else UpdateResponseInvalidError)
        not_unassigned = (CreateNotUnassignedError if kind == "create"
                          else UpdateNotUnassignedError)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise invalid(
                f"issue {kind} returned malformed JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise invalid(
                f"issue {kind} JSON is not an object", kind=type(data).__name__)
        missing = [f for f in CREATE_CONTRACT_FIELDS if f not in data]
        if missing:
            raise invalid(
                f"issue {kind} response is missing contract fields",
                missing=missing)
        for field in ("id", "identifier", "title"):
            if not isinstance(data[field], str) or not data[field].strip():
                raise invalid(
                    f"issue {kind} {field} is not a non-blank string")
        if data["description"] is None:
            data["description"] = ""
        if not isinstance(data["description"], str):
            raise invalid(f"issue {kind} description is not a string")
        assignee_id = data.get("assignee_id")
        assignee = data.get("assignee")
        if assignee_id not in (None, "") or assignee not in (None, ""):
            raise not_unassigned(
                f"{kind}d issue already carries an assignee; the handoff "
                "mutation must never start a target run",
                assignee_id=_bounded(str(assignee_id), 80),
                assignee=_bounded(str(assignee), 80))
        return data

    def list_runs(self, issue_id: str, *, active: bool = False,
                  siblings: bool = False) -> dict:
        """Read-only run listing. Fail closed when the shape is undetermined."""
        issue_id = _require_bare_id(issue_id, "issue_id")
        argv = ["issue", "runs", issue_id, "--output", "json"]
        if active:
            argv.append("--active")
        if siblings:
            argv.append("--siblings")
        code, out, err = self._run(argv)
        if code != 0:
            raise RunStateUndeterminedError(
                f"issue runs failed (exit {code}): {_bounded(err, 120)}",
                exit_code=code)
        if TRUNCATION_RE.search(err or ""):
            raise RunStateUndeterminedError(
                "issue runs stderr reports truncation; unexpected-run state "
                "cannot be determined", stderr=_bounded(err, 120))
        return {"runs": parse_runs_json(out), "stderr": err or ""}


def parse_runs_json(text: str) -> list:
    """Documented list-of-objects contract. Extra fields are ignored."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RunsResponseInvalidError(
            f"issue runs returned malformed JSON: {exc}") from exc
    if not isinstance(data, list):
        raise RunsResponseInvalidError(
            "issue runs JSON is not a list", kind=type(data).__name__)
    runs = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise RunsResponseInvalidError(
                f"issue runs[{i}] is not an object", index=i)
        parsed = {}
        for field in RUN_CONTRACT_FIELDS:
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RunsResponseInvalidError(
                    f"issue runs[{i}].{field} is not a non-blank string",
                    index=i, field=field)
            parsed[field] = value
        parsed["status"] = parsed["status"].strip()
        runs.append(parsed)
    return runs


def unexpected_active_runs(runs: list, *, issue_id: str,
                           ignore_ids: set | None = None) -> list:
    """Active runs on the target/sibling set that are not already correlated."""
    ignore_ids = ignore_ids or set()
    unexpected = []
    for run in runs:
        if run["id"] in ignore_ids:
            continue
        if run["status"] in ACTIVE_RUN_STATUSES:
            unexpected.append(run)
    return unexpected


def correlate_intended_run(runs: list, *, issue_id: str, agent_id: str,
                           known_ids: set | None = None) -> dict:
    """Exactly one new run for this issue+agent, else fail closed."""
    known_ids = known_ids or set()
    newcomers = [r for r in runs if r["id"] not in known_ids]
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
    raise RunCorrelationError(
        "intended run cannot be uniquely correlated; fail closed",
        reason=reason, matching=len(matching), extra=len(wrong),
        new=len(newcomers))


def _assignment_confirmed(data: dict, issue_id: str, agent_id: str) -> bool:
    """Observable confirmation marker set for the deployed assign response."""
    if data.get("id") == issue_id:
        return True
    for key in ("assignee_id", "to_id", "agent_id"):
        if data.get(key) == agent_id:
            return True
    return False


def audit_ledger(records: list, executable: str = "multica") -> dict:
    """Deterministic main-path audit over the ledger's command records."""
    commands = [r for r in records if r.get("kind") == "command"]
    counts: dict = {}
    for record in commands:
        counts[record.get("command_class") or "other"] = \
            counts.get(record.get("command_class") or "other", 0) + 1
    mention_hits = [record["seq"] for record in commands
                    if any(MENTION_RE.search(str(a)) for a in record.get("argv") or [])]
    creates = [r for r in commands if r.get("command_class") == "issue_create"]
    create_ok = bool(creates) and all(
        _create_argv_ok(r.get("argv") or [], executable) for r in creates)
    assigns = [r for r in commands if r.get("command_class") == "assignment_trigger"]
    assign_ok = bool(assigns) and all(
        _assign_argv_ok(r.get("argv") or [], executable) for r in assigns)
    updates = [r for r in commands if r.get("command_class") == "issue_update"]
    update_ok = (not updates) or all(
        _update_argv_ok(r.get("argv") or [], executable) for r in updates)
    unexpected = sorted(set(counts) - {"read", "issue_create", "issue_update",
                                       "assignment_trigger", "comment_publish"})
    mutations = counts.get("issue_create", 0) + counts.get("issue_update", 0)
    ok = (not mention_hits and (not creates or create_ok) and update_ok
          and (not assigns or assign_ok) and not unexpected
          and mutations <= 1
          and counts.get("assignment_trigger", 0) <= 1)
    return {
        "command_counts": counts,
        "mention_hits": mention_hits,
        "issue_create": {"count": len(creates), "unassigned_argv_ok": create_ok},
        "issue_update": {"count": len(updates), "non_trigger_argv_ok": update_ok},
        "assignment_trigger": {"count": len(assigns), "argv_ok": assign_ok},
        "comment_publish": {"count": counts.get("comment_publish", 0)},
        "unexpected_write_classes": unexpected,
        "ok": ok,
    }


def _create_argv_ok(argv: list, executable: str) -> bool:
    core = _strip_executable(argv, executable)
    if tuple(core[:2]) != ("issue", "create"):
        return False
    present = set(core)
    if "--assignee" in present or "--assignee-id" in present:
        return False
    if "--title" not in present or "--description-file" not in present:
        return False
    return all(flag in CREATE_ALLOWED_FLAGS for flag in present if flag.startswith("--"))


def _assign_argv_ok(argv: list, executable: str) -> bool:
    core = _strip_executable(argv, executable)
    if tuple(core[:2]) != ("issue", "assign"):
        return False
    present = set(core)
    for flag in ASSIGN_FORBIDDEN_FLAGS:
        if flag in present:
            return False
    if "--to-id" not in present:
        return False
    index = core.index("--to-id") + 1
    return index < len(core) and bool(UUID_RE.match(core[index]))


def _update_argv_ok(argv: list, executable: str) -> bool:
    core = _strip_executable(argv, executable)
    if tuple(core[:2]) != ("issue", "update"):
        return False
    present = set(core)
    if "--assignee" in present or "--assignee-id" in present:
        return False
    if "--no-start" not in present:
        return False
    return all(flag in UPDATE_ALLOWED_FLAGS
               for flag in present if flag.startswith("--"))
