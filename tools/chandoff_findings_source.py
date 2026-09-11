#!/usr/bin/env python3
"""YZT-88 — strict explicit Findings source binding and read-only snapshot reader.

Implements the YZT-87 `FINDINGS_SOURCE_BINDING_DESIGN.md` internal contracts
(`findings-source-binding/1`, `findings-source-observation/1`) without touching
any frozen T00–T06 payload:

- an absent Findings source is never reported as a successfully read empty
  store: every production read is bound to a Lead/Context-Engineer authority
  record and a fixed physical root;
- the reader validates a complete, flat inventory and every record before any
  filtering, and returns a detached snapshot plus one observation sidecar;
- observations bind the exact task/role/request/envelope and the previous
  boundary digest so a consumer can verify the join;
- whole-store drift (any added/removed/changed/renamed record) is a typed
  refusal, never an automatic refresh or retry.

Simulation-only sources (`SyntheticFindingsSource`) exist so unit tests and
explicit in-memory fixtures never need a live root; they are marked
`simulation: true` in their observation and are not production authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from schema_mini import Schema, load_schema_file  # noqa: E402

BINDING_SCHEMA = "findings-source-binding/1"
OBSERVATION_SCHEMA = "findings-source-observation/1"
SNAPSHOT_SCHEMA = "findings-source-snapshot/1"
LAYOUT_FLAT = "flat-finding-json-v1"
LAYOUT_SYNTHETIC = "synthetic-in-memory-v1"

FINDING_FILE_RE = None  # set below (compiled lazily to keep import light)

REFUSAL_CODES = (
    "findings_source_missing",
    "findings_source_unreadable",
    "findings_source_invalid",
    "findings_source_ambiguous",
    "findings_source_unbound",
    "findings_source_changed",
)

BINDING_KEYS = {
    "schema", "source_id", "project_id", "root", "layout", "authority",
    "allowed", "runtime", "created_at",
}
AUTHORITY_KEYS = {"comment_id", "issue_id", "author_id", "author_type", "digest"}
ALLOWED_KEYS = {"task_refs", "roles"}
RUNTIME_KEYS = {"commit", "adapter_digest"}


class FindingsSourceRefusal(Exception):
    """Typed, bounded refusal. Never a partial-success list."""

    def __init__(self, code: str, message: str, *, boundary: str | None = None,
                 detail: str = "", **details):
        if code not in REFUSAL_CODES:
            raise ValueError(f"unknown Findings source refusal code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.boundary = boundary
        self.detail = detail
        self.details = {k: v for k, v in details.items() if v is not None}

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.boundary:
            out["boundary"] = self.boundary
        if self.detail:
            out["detail"] = self.detail
        if self.details:
            out["details"] = self.details
        return out


# ---------------------------------------------------------------------------
# canonical helpers
# ---------------------------------------------------------------------------
def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_obj(obj) -> str:
    return sha256_digest(canonical_json(obj).encode("utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_constant(token):
    raise ValueError(f"non-finite JSON constant refused: {token!r}")


def _no_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key refused: {key!r}")
        out[key] = value
    return out


def load_json_strict_bytes(data: bytes, what: str) -> dict:
    """Strict JSON: UTF-8, no duplicate keys, no NaN/Infinity, object root."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FindingsSourceRefusal(
            "findings_source_invalid",
            f"{what} is not valid UTF-8: {exc}") from None
    try:
        doc = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                         parse_constant=_reject_constant)
    except ValueError as exc:
        raise FindingsSourceRefusal(
            "findings_source_invalid",
            f"{what} is not strict JSON: {exc}") from None
    if not isinstance(doc, dict):
        raise FindingsSourceRefusal(
            "findings_source_invalid", f"{what} must be a JSON object")
    return doc


def load_json_strict_file(path, what: str) -> tuple:
    """Return (doc, raw_sha256, byte_length) for a strict JSON file."""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise FindingsSourceRefusal(
            "findings_source_unreadable",
            f"{what} is unreadable: {type(exc).__name__}: {exc}") from None
    return load_json_strict_bytes(data, what), sha256_digest(data), len(data)


def _sha256_hex(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    body = value.split(":", 1)[1] if value.startswith("sha256:") else value
    body = body.strip().lower()
    if len(body) != 64 or any(c not in "0123456789abcdef" for c in body):
        return None
    return body


def _require(condition: bool, message: str, **details):
    if not condition:
        raise FindingsSourceRefusal("findings_source_invalid", message,
                                    **details)


# ---------------------------------------------------------------------------
# binding validation
# ---------------------------------------------------------------------------
def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    except OSError:
        return True
    return False


def _reject_reparse_chain(root: Path) -> None:
    """Refuse a root reached through a symlink/junction component."""
    current = Path(root).absolute()
    for candidate in (current, *current.parents):
        if candidate == candidate.parent:
            break
        if _is_reparse_point(candidate):
            raise FindingsSourceRefusal(
                "findings_source_ambiguous",
                "the bound root is reached through a symlink/reparse component; "
                "physical identity is required",
                detail=str(candidate))


def validate_binding(binding, *, project_id: str | None = None) -> dict:
    """Validate the binding document itself (no filesystem read yet)."""
    _require(isinstance(binding, dict), "binding must be a JSON object")
    unknown = sorted(set(binding) - BINDING_KEYS)
    _require(not unknown, "binding carries unsupported keys",
             keys=unknown[:8])
    _require(binding.get("schema") == BINDING_SCHEMA,
             f"binding schema must be {BINDING_SCHEMA!r}",
             found=binding.get("schema"))
    source_id = binding.get("source_id")
    _require(isinstance(source_id, str) and source_id.strip(),
             "binding.source_id must be a non-blank string")
    pid = binding.get("project_id")
    _require(isinstance(pid, str) and pid.strip(),
             "binding.project_id must be a non-blank string")
    if project_id is not None and pid != project_id:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "binding project does not match the dispatch project",
            detail=f"{pid!r} != {project_id!r}")
    root = binding.get("root")
    _require(isinstance(root, str) and root.strip(),
             "binding.root must be a non-blank string")
    root_path = Path(root)
    _require(root_path.is_absolute(), "binding.root must be an absolute path")
    _require(".." not in root_path.parts, "binding.root must not contain '..'")
    _require(not str(root_path.drive).startswith("\\\\"),
             "binding.root must not be a UNC path")
    _require(binding.get("layout") == LAYOUT_FLAT,
             f"binding.layout must be {LAYOUT_FLAT!r}",
             found=binding.get("layout"))
    authority = binding.get("authority")
    _require(isinstance(authority, dict), "binding.authority must be an object")
    unknown = sorted(set(authority) - AUTHORITY_KEYS)
    _require(not unknown, "binding.authority carries unsupported keys",
             keys=unknown[:8])
    for key in ("comment_id", "issue_id", "author_id", "author_type"):
        _require(isinstance(authority.get(key), str) and authority[key].strip(),
                 f"binding.authority.{key} must be a non-blank string")
    _require(authority["author_type"] in ("agent", "member"),
             "binding.authority.author_type must be agent or member",
             found=authority.get("author_type"))
    _require(_sha256_hex(authority.get("digest")) is not None,
             "binding.authority.digest must be an exact sha256 digest")
    allowed = binding.get("allowed")
    _require(isinstance(allowed, dict), "binding.allowed must be an object")
    unknown = sorted(set(allowed) - ALLOWED_KEYS)
    _require(not unknown, "binding.allowed carries unsupported keys",
             keys=unknown[:8])
    for key in ("task_refs", "roles"):
        values = allowed.get(key)
        _require(isinstance(values, list) and values and
                 all(isinstance(v, str) and v.strip() for v in values),
                 f"binding.allowed.{key} must be a non-empty string list")
    runtime = binding.get("runtime")
    _require(isinstance(runtime, dict), "binding.runtime must be an object")
    unknown = sorted(set(runtime) - RUNTIME_KEYS)
    _require(not unknown, "binding.runtime carries unsupported keys",
             keys=unknown[:8])
    commit = runtime.get("commit")
    _require(isinstance(commit, str) and len(commit) == 40 and
             all(c in "0123456789abcdef" for c in commit.lower()),
             "binding.runtime.commit must be a full 40-hex commit")
    _require(_sha256_hex(runtime.get("adapter_digest")) is not None,
             "binding.runtime.adapter_digest must be an exact sha256 digest")
    out = copy.deepcopy(binding)
    out["root"] = str(root_path)
    return out


def verify_authority(binding: dict, resolver) -> dict:
    """Resolve the authority record fresh and verify author/target/content."""
    authority = binding["authority"]
    if resolver is None:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "no authority resolver is configured; a path or self-authored "
            "binding alone is not authority")
    try:
        record = resolver(copy.deepcopy(authority))
    except FindingsSourceRefusal:
        raise
    except Exception as exc:  # noqa: BLE001 - resolver failure is a stop
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            f"authority resolution failed: {type(exc).__name__}: {exc}") from None
    if not isinstance(record, dict):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "authority resolver returned no record")
    for key in ("comment_id", "issue_id", "author_id", "author_type"):
        if record.get(key) != authority.get(key):
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                f"authority record {key} does not match the binding",
                detail=f"{record.get(key)!r} != {authority.get(key)!r}")
    content = record.get("content")
    if not isinstance(content, str):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "authority record carries no verifiable content")
    actual = sha256_digest(content.encode("utf-8"))
    if actual != authority["digest"]:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "authority content digest does not reproduce the binding digest",
            detail=f"{actual} != {authority['digest']}")
    return {"comment_id": authority["comment_id"],
            "issue_id": authority["issue_id"],
            "author_id": authority["author_id"],
            "author_type": authority["author_type"],
            "digest": authority["digest"]}


def verify_runtime(runtime: dict, *, expected_commit=None,
                   expected_adapter_digest=None) -> dict:
    """Compare the binding's runtime pins with the expected running pins."""
    problems = []
    if expected_commit is not None and runtime["commit"] != expected_commit:
        problems.append(f"commit {runtime['commit']} != {expected_commit}")
    if expected_adapter_digest is not None and \
            runtime["adapter_digest"] != expected_adapter_digest:
        problems.append(
            f"adapter_digest {runtime['adapter_digest']} != "
            f"{expected_adapter_digest}")
    if problems:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "binding runtime pins do not match the expected running pins",
            detail="; ".join(problems))
    return dict(runtime)


# ---------------------------------------------------------------------------
# strict read-only snapshot reader
# ---------------------------------------------------------------------------
class FilesystemReader:
    """The default file transport. Read-only, never creates anything."""

    def scandir(self, root: Path) -> list:
        return list(os.scandir(root))

    def read_bytes(self, path: Path) -> bytes:
        return Path(path).read_bytes()


def _finding_file_names(entries: list) -> list:
    names = []
    for entry in entries:
        name = entry.name if hasattr(entry, "name") else str(entry)
        path = getattr(entry, "path", None)
        if path is None:
            path = os.path.join(".", name)
        if name in (".", ".."):
            raise FindingsSourceRefusal(
                "findings_source_invalid",
                "unexpected directory entry in the flat Findings root",
                detail=name)
        try:
            is_file = entry.is_file(follow_symlinks=False) \
                if hasattr(entry, "is_file") else Path(path).is_file()
            is_symlink = entry.is_symlink() if hasattr(entry, "is_symlink") \
                else Path(path).is_symlink()
        except OSError as exc:
            raise FindingsSourceRefusal(
                "findings_source_unreadable",
                f"directory entry cannot be inspected: {type(exc).__name__}: "
                f"{exc}", detail=name) from None
        if is_symlink:
            raise FindingsSourceRefusal(
                "findings_source_ambiguous",
                "a symlink/reparse entry is not a flat finding record",
                detail=name)
        if not is_file:
            raise FindingsSourceRefusal(
                "findings_source_invalid",
                "the flat Findings layout allows only regular FIND-*.json "
                "files; a directory or special entry was found",
                detail=name)
        names.append(name)
    lower = {}
    for name in names:
        folded = name.lower()
        if folded in lower and lower[folded] != name:
            raise FindingsSourceRefusal(
                "findings_source_ambiguous",
                "case-fold colliding filenames refused",
                detail=f"{lower[folded]!r} vs {name!r}")
        lower[folded] = name
    for name in names:
        if name.startswith("."):
            raise FindingsSourceRefusal(
                "findings_source_invalid",
                "hidden entries are not part of the declared flat layout",
                detail=name)
        if not name.endswith(".json"):
            raise FindingsSourceRefusal(
                "findings_source_invalid",
                "unrecognized entry in the flat Findings root (only "
                "FIND-*.json is declared)", detail=name)
        if not name.startswith("FIND-"):
            raise FindingsSourceRefusal(
                "findings_source_invalid",
                "unrecognized entry in the flat Findings root (only "
                "FIND-*.json is declared)", detail=name)
    return sorted(names)


def _validate_finding_doc(doc: dict, name: str) -> dict:
    schema = load_schema_file("finding.schema.json")
    errors = Schema(schema, schema).validate(doc, path=f"$.{name}")
    if errors:
        raise FindingsSourceRefusal(
            "findings_source_invalid",
            f"finding record {name} fails the frozen finding schema",
            detail="; ".join(errors[:6]))
    expected_name = f"{doc['finding_id']}.json"
    if expected_name != name:
        raise FindingsSourceRefusal(
            "findings_source_invalid",
            "finding filename does not equal its finding_id",
            detail=f"{name!r} != {expected_name!r}")
    return doc


def _read_start_finish():
    return now_iso(), None


def read_snapshot(root: Path, *, source_id: str,
                  reader: FilesystemReader | None = None,
                  boundary: str, clock=None,
                  simulation: bool = False) -> dict:
    """Read a complete flat inventory with stability and identity checks."""
    clock = clock or now_iso
    reader = reader or FilesystemReader()
    started = clock()
    root_path = Path(root)
    if not root_path.exists():
        raise FindingsSourceRefusal(
            "findings_source_missing",
            "the authorized Findings root does not exist; an absent source is "
            "never an empty store", boundary=boundary, detail=str(root_path))
    if _is_reparse_point(root_path):
        raise FindingsSourceRefusal(
            "findings_source_ambiguous",
            "the authorized Findings root is a symlink/reparse point",
            boundary=boundary, detail=str(root_path))
    if not root_path.is_dir():
        raise FindingsSourceRefusal(
            "findings_source_unreadable",
            "the authorized Findings root is not a directory",
            boundary=boundary, detail=str(root_path))
    try:
        entries = reader.scandir(root_path)
    except FindingsSourceRefusal:
        raise
    except OSError as exc:
        raise FindingsSourceRefusal(
            "findings_source_unreadable",
            f"the Findings root cannot be enumerated: "
            f"{type(exc).__name__}: {exc}", boundary=boundary,
            detail=str(root_path)) from None
    try:
        names = _finding_file_names(entries)
    except FindingsSourceRefusal as exc:
        exc.boundary = exc.boundary or boundary
        raise
    inventory = []
    records = []
    seen_ids = {}
    for name in names:
        path = root_path / name
        try:
            data = reader.read_bytes(path)
        except FindingsSourceRefusal:
            raise
        except OSError as exc:
            raise FindingsSourceRefusal(
                "findings_source_unreadable",
                f"finding file cannot be read: {name}",
                boundary=boundary, detail=f"{type(exc).__name__}: {exc}") from None
        raw_digest = sha256_digest(data)
        try:
            doc = load_json_strict_bytes(data, f"finding {name}")
            doc = _validate_finding_doc(doc, name)
        except FindingsSourceRefusal as exc:
            exc.boundary = exc.boundary or boundary
            raise
        fid = doc["finding_id"]
        if fid in seen_ids:
            raise FindingsSourceRefusal(
                "findings_source_invalid",
                "duplicate finding_id in the flat store",
                boundary=boundary, detail=f"{fid} ({seen_ids[fid]}, {name})")
        seen_ids[fid] = name
        inventory.append({
            "file": name,
            "bytes": len(data),
            "sha256": raw_digest,
            "finding_id": fid,
            "status": doc["status"],
        })
        records.append(copy.deepcopy(doc))
    # Stability: the inventory must not move while it is being read.
    try:
        after_entries = reader.scandir(root_path)
        after_names = _finding_file_names(after_entries)
    except FindingsSourceRefusal as exc:
        exc.boundary = exc.boundary or boundary
        raise
    except OSError as exc:
        raise FindingsSourceRefusal(
            "findings_source_unreadable",
            f"the post-read enumeration failed: {type(exc).__name__}: {exc}",
            boundary=boundary, detail=str(root_path)) from None
    if after_names != names:
        raise FindingsSourceRefusal(
            "findings_source_changed",
            "the Findings inventory changed while it was being read",
            boundary=boundary,
            detail=f"{names} -> {after_names}")
    finished = clock()
    snapshot_digest = digest_obj({
        "schema": SNAPSHOT_SCHEMA,
        "source_id": source_id,
        "resolved_root": str(root_path),
        "inventory": inventory,
    })
    open_ids = sorted(r["finding_id"] for r in records if r["status"] == "open")
    return {
        "records": records,
        "inventory": inventory,
        "open_ids": open_ids,
        "snapshot_digest": snapshot_digest,
        "resolved_root": str(root_path),
        "read_started_at": started,
        "read_finished_at": finished,
        "simulation": bool(simulation),
    }


def check_drift(previous_observation: dict | None, current: dict,
                *, boundary: str) -> None:
    """Whole-store comparison against the accepted prepare baseline."""
    if previous_observation is None:
        return
    if previous_observation.get("source_id") != current.get("source_id"):
        raise FindingsSourceRefusal(
            "findings_source_changed",
            "the source identity changed since the accepted baseline",
            boundary=boundary,
            detail=f"{previous_observation.get('source_id')!r} -> "
                   f"{current.get('source_id')!r}")
    if previous_observation.get("resolved_root") != current.get("resolved_root"):
        raise FindingsSourceRefusal(
            "findings_source_changed",
            "the resolved physical root changed since the accepted baseline",
            boundary=boundary,
            detail=f"{previous_observation.get('resolved_root')!r} -> "
                   f"{current.get('resolved_root')!r}")
    if previous_observation.get("snapshot_digest") != current.get("snapshot_digest"):
        raise FindingsSourceRefusal(
            "findings_source_changed",
            "the Findings store changed since the accepted baseline; no "
            "automatic refresh or retry is allowed",
            boundary=boundary,
            detail=f"{previous_observation.get('snapshot_digest')} -> "
                   f"{current.get('snapshot_digest')}")


def build_observation(snapshot: dict, *, binding: dict, boundary: str,
                      observer_run_id: str | None = None,
                      join: dict | None = None) -> dict:
    join = dict(join or {})
    return {
        "schema": OBSERVATION_SCHEMA,
        "boundary": boundary,
        "observer_run_id": observer_run_id,
        "binding_digest": digest_obj(binding),
        "source_id": binding["source_id"],
        "project_id": binding["project_id"],
        "root": binding["root"],
        "resolved_root": snapshot["resolved_root"],
        "layout": binding["layout"],
        "authority_comment_id": binding["authority"]["comment_id"],
        "authority_digest": binding["authority"]["digest"],
        "runtime": dict(binding["runtime"]),
        "read_started_at": snapshot["read_started_at"],
        "read_finished_at": snapshot["read_finished_at"],
        "inventory": copy.deepcopy(snapshot["inventory"]),
        "total_records": len(snapshot["records"]),
        "open_ids": list(snapshot["open_ids"]),
        "open_count": len(snapshot["open_ids"]),
        "snapshot_digest": snapshot["snapshot_digest"],
        "simulation": bool(snapshot.get("simulation")),
        "join": join,
    }


def verify_observation(observation, *, binding: dict | None = None,
                       task_ref: str | None = None,
                       role: str | None = None,
                       request_digest: str | None = None,
                       envelope_digest: str | None = None,
                       expected_snapshot_digest: str | None = None,
                       boundary: str | None = None) -> list:
    """Return the list of join problems; empty means the join verifies."""
    problems = []
    if not isinstance(observation, dict):
        return ["observation is not an object"]
    if observation.get("schema") != OBSERVATION_SCHEMA:
        problems.append(f"observation schema {observation.get('schema')!r}")
    if binding is not None:
        if observation.get("binding_digest") != digest_obj(binding):
            problems.append("observation binding digest")
        if observation.get("source_id") != binding.get("source_id"):
            problems.append("observation source id")
        if observation.get("resolved_root") != binding.get("root"):
            problems.append("observation resolved root")
    if boundary is not None and observation.get("boundary") != boundary:
        problems.append(f"observation boundary {observation.get('boundary')!r}")
    join = observation.get("join") or {}
    for key, expected in (("task_ref", task_ref), ("role", role),
                          ("request_digest", request_digest),
                          ("envelope_digest", envelope_digest)):
        if expected is not None and join.get(key) != expected:
            problems.append(f"observation join {key}")
    if expected_snapshot_digest is not None and \
            observation.get("snapshot_digest") != expected_snapshot_digest:
        problems.append("observation snapshot digest")
    return problems


# ---------------------------------------------------------------------------
# bound source object
# ---------------------------------------------------------------------------
class BoundFindingsSource:
    """A verified binding plus its per-read verification and file transport.

    Every `read()` re-verifies the authority record freshly, rechecks runtime
    pins when expected pins are supplied, and reads the root from scratch; no
    list or digest is cached between boundaries.
    """

    is_production = True

    def __init__(self, binding: dict, *, resolver, reader=None,
                 project_id: str | None = None,
                 expected_commit: str | None = None,
                 expected_adapter_digest: str | None = None,
                 clock=None):
        self.binding = validate_binding(binding, project_id=project_id)
        self.resolver = resolver
        self.reader = reader or FilesystemReader()
        self.expected_commit = expected_commit
        self.expected_adapter_digest = expected_adapter_digest
        self.clock = clock or now_iso
        self._last_observation: dict | None = None

    @property
    def source_id(self) -> str:
        return self.binding["source_id"]

    @property
    def binding_digest(self) -> str:
        return digest_obj(self.binding)

    def _check_allowed(self, *, task_ref, role, boundary):
        allowed = self.binding["allowed"]
        if task_ref is not None and task_ref not in allowed["task_refs"]:
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "the binding does not authorize this dispatch task",
                boundary=boundary, detail=task_ref)
        if role is not None and role not in allowed["roles"]:
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "the binding does not authorize this dispatch role",
                boundary=boundary, detail=role)

    def read(self, *, boundary: str, observer_run_id: str | None = None,
             task_ref: str | None = None, role: str | None = None,
             request_digest: str | None = None,
             task_fingerprint: str | None = None,
             envelope_digest: str | None = None,
             previous_receipt_digest: str | None = None,
             expected_snapshot_digest: str | None = None,
             prior_observation: dict | None = None) -> dict:
        verify_authority(self.binding, self.resolver)
        verify_runtime(self.binding["runtime"],
                       expected_commit=self.expected_commit,
                       expected_adapter_digest=self.expected_adapter_digest)
        self._check_allowed(task_ref=task_ref, role=role, boundary=boundary)
        snapshot = read_snapshot(self.binding["root"],
                                 source_id=self.binding["source_id"],
                                 reader=self.reader, boundary=boundary,
                                 clock=self.clock)
        join = {"task_ref": task_ref, "role": role,
                "request_digest": request_digest,
                "task_fingerprint": task_fingerprint,
                "envelope_digest": envelope_digest,
                "previous_receipt_digest": previous_receipt_digest}
        join = {k: v for k, v in join.items() if v is not None}
        observation = build_observation(snapshot, binding=self.binding,
                                        boundary=boundary,
                                        observer_run_id=observer_run_id,
                                        join=join)
        if expected_snapshot_digest is not None and \
                observation["snapshot_digest"] != expected_snapshot_digest:
            raise FindingsSourceRefusal(
                "findings_source_changed",
                "the Findings store does not match the expected snapshot "
                "digest", boundary=boundary,
                detail=f"{observation['snapshot_digest']} != "
                       f"{expected_snapshot_digest}")
        if prior_observation is not None:
            problems = verify_observation(prior_observation,
                                          task_ref=task_ref, role=role)
            if problems:
                raise FindingsSourceRefusal(
                    "findings_source_changed",
                    "the accepted baseline observation does not join this "
                    "dispatch", boundary=boundary,
                    detail="; ".join(problems))
        check_drift(prior_observation, observation, boundary=boundary)
        self._last_observation = observation
        out = dict(snapshot)
        out["observation"] = observation
        out["binding_digest"] = self.binding_digest
        return out


class SyntheticFindingsSource:
    """Simulation-only in-memory source. Never production authority.

    The observation is explicitly marked `simulation: true` and the layout is
    `synthetic-in-memory-v1`, so a consumer cannot confuse it with a verified
    live snapshot.
    """

    is_production = False

    def __init__(self, records: list | None = None, *,
                 source_id: str = "SYNTHETIC-IN-MEMORY",
                 project_id: str = "simulation", clock=None):
        self.records = [copy.deepcopy(r) for r in (records or [])]
        self.source_id = source_id
        self.project_id = project_id
        self.clock = clock or now_iso
        self.binding_digest = digest_obj({"schema": BINDING_SCHEMA,
                                          "source_id": source_id,
                                          "simulation": True})
        self._last_observation = None

    def read(self, *, boundary: str, observer_run_id: str | None = None,
             task_ref: str | None = None, role: str | None = None,
             request_digest: str | None = None,
             task_fingerprint: str | None = None,
             envelope_digest: str | None = None,
             previous_receipt_digest: str | None = None,
             expected_snapshot_digest: str | None = None,
             prior_observation: dict | None = None) -> dict:
        started = self.clock()
        inventory = []
        ids = set()
        for record in sorted(self.records, key=lambda r: r.get("finding_id") or ""):
            fid = record.get("finding_id")
            if fid in ids:
                raise FindingsSourceRefusal(
                    "findings_source_invalid",
                    "duplicate finding_id in the synthetic store", detail=fid)
            ids.add(fid)
            raw = canonical_json(record).encode("utf-8")
            inventory.append({
                "file": f"{fid}.json",
                "bytes": len(raw),
                "sha256": sha256_digest(raw),
                "finding_id": fid,
                "status": record.get("status"),
            })
        snapshot = {
            "records": [copy.deepcopy(r) for r in self.records],
            "inventory": inventory,
            "open_ids": sorted(r["finding_id"] for r in self.records
                               if r.get("status") == "open"),
            "snapshot_digest": digest_obj({
                "schema": SNAPSHOT_SCHEMA,
                "source_id": self.source_id,
                "resolved_root": "synthetic://in-memory",
                "inventory": inventory,
            }),
            "resolved_root": "synthetic://in-memory",
            "read_started_at": started,
            "read_finished_at": self.clock(),
            "simulation": True,
        }
        join = {"task_ref": task_ref, "role": role,
                "request_digest": request_digest,
                "task_fingerprint": task_fingerprint,
                "envelope_digest": envelope_digest,
                "previous_receipt_digest": previous_receipt_digest}
        join = {k: v for k, v in join.items() if v is not None}
        binding = {"source_id": self.source_id, "project_id": self.project_id,
                   "root": "synthetic://in-memory", "layout": LAYOUT_SYNTHETIC,
                   "authority": {"comment_id": "synthetic", "issue_id": "synthetic",
                                 "author_id": "synthetic", "author_type": "agent",
                                 "digest": "sha256:" + "0" * 64},
                   "runtime": {"commit": "0" * 40,
                               "adapter_digest": "sha256:" + "0" * 64}}
        observation = build_observation(snapshot, binding=binding,
                                        boundary=boundary,
                                        observer_run_id=observer_run_id,
                                        join=join)
        if expected_snapshot_digest is not None and \
                observation["snapshot_digest"] != expected_snapshot_digest:
            raise FindingsSourceRefusal(
                "findings_source_changed",
                "the synthetic store does not match the expected snapshot "
                "digest", boundary=boundary)
        if prior_observation is not None:
            problems = verify_observation(prior_observation,
                                          task_ref=task_ref, role=role)
            if problems:
                raise FindingsSourceRefusal(
                    "findings_source_changed",
                    "the accepted baseline observation does not join this "
                    "dispatch", boundary=boundary,
                    detail="; ".join(problems))
        check_drift(prior_observation, observation, boundary=boundary)
        self._last_observation = observation
        out = dict(snapshot)
        out["observation"] = observation
        out["binding_digest"] = self.binding_digest
        return out


def source_from_binding_file(path, *, resolver, project_id=None,
                             expected_commit=None, expected_adapter_digest=None,
                             reader=None, clock=None) -> BoundFindingsSource:
    binding, _digest, _size = load_json_strict_file(
        path, "findings source binding")
    return BoundFindingsSource(
        binding, resolver=resolver, reader=reader, project_id=project_id,
        expected_commit=expected_commit,
        expected_adapter_digest=expected_adapter_digest, clock=clock)


def observation_from_file(path) -> dict:
    doc, _digest, _size = load_json_strict_file(path, "findings observation")
    if doc.get("schema") != OBSERVATION_SCHEMA:
        raise FindingsSourceRefusal(
            "findings_source_invalid",
            f"evidence file schema must be {OBSERVATION_SCHEMA!r}",
            detail=str(doc.get("schema")))
    return doc


def verify_worker_entry(source, *, task_ref=None, role=None,
                        request_digest=None, envelope_digest=None,
                        expected_snapshot_digest=None,
                        prior_observation=None, observer_run_id=None,
                        allow_simulation=False) -> dict:
    """Worker-startup verification before any consequential work.

    Verifies the runtime pins and authority (inside `source.read`), re-reads
    the authorized root at the WORKER_START boundary, checks the whole-store
    drift against the accepted baseline, joins the exact task/role/request/
    envelope, and returns the detached records for a fresh source-aware
    SELF_CHECK. A missing/changed source stops this worker; it never
    dispatches a replacement.
    """
    if not allow_simulation and not getattr(source, "is_production", False):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "a simulation-only Findings source is not production worker-entry "
            "authority")
    snapshot = source.read(
        boundary="WORKER_START", observer_run_id=observer_run_id,
        task_ref=task_ref, role=role, request_digest=request_digest,
        envelope_digest=envelope_digest,
        expected_snapshot_digest=expected_snapshot_digest,
        prior_observation=prior_observation)
    observation = snapshot["observation"]
    problems = verify_observation(
        observation, task_ref=task_ref, role=role,
        request_digest=request_digest, envelope_digest=envelope_digest,
        expected_snapshot_digest=expected_snapshot_digest,
        boundary="WORKER_START")
    if problems:
        raise FindingsSourceRefusal(
            "findings_source_changed",
            "the worker-entry observation does not join the accepted "
            "package/evidence", boundary="WORKER_START",
            detail="; ".join(problems))
    return {"observation": observation, "records": snapshot["records"],
            "open_ids": snapshot["open_ids"],
            "snapshot_digest": snapshot["snapshot_digest"],
            "binding_digest": snapshot["binding_digest"]}


def make_binding(*, source_id: str, project_id: str, root: str,
                 authority: dict, allowed: dict, runtime: dict,
                 created_at: str | None = None) -> dict:
    """Convenience constructor for operators/tests (still fully validated)."""
    return {
        "schema": BINDING_SCHEMA,
        "source_id": source_id,
        "project_id": project_id,
        "root": root,
        "layout": LAYOUT_FLAT,
        "authority": authority,
        "allowed": allowed,
        "runtime": runtime,
        "created_at": created_at or now_iso(),
    }


def main(argv=None) -> int:
    """Operator helper: validate a binding and print one read observation."""
    import argparse
    parser = argparse.ArgumentParser(
        description="strict Findings source binding helper (read-only)")
    sub = parser.add_subparsers(dest="command", required=True)
    val = sub.add_parser("validate", help="validate a binding file")
    val.add_argument("--binding-file", required=True)
    val.add_argument("--authority-file", required=True,
                     help="captured authority comment JSON "
                          "(comment_id/issue_id/author_id/author_type/content)")
    read = sub.add_parser("read", help="read one snapshot and print the "
                                       "observation")
    read.add_argument("--binding-file", required=True)
    read.add_argument("--authority-file", required=True)
    read.add_argument("--boundary", default="OPERATOR_READ")
    args = parser.parse_args(argv)

    def resolver(_authority):
        doc, _d, _s = load_json_strict_file(args.authority_file,
                                            "authority capture")
        return doc

    try:
        source = source_from_binding_file(args.binding_file,
                                          resolver=resolver)
        if args.command == "validate":
            print(json.dumps({"ok": True, "source_id": source.source_id,
                              "binding_digest": source.binding_digest,
                              "root": source.binding["root"]},
                             ensure_ascii=False, indent=2))
            return 0
        snap = source.read(boundary=args.boundary)
        print(json.dumps(snap["observation"], ensure_ascii=False, indent=2))
        return 0
    except FindingsSourceRefusal as exc:
        print(json.dumps({"ok": False, **exc.as_dict()},
                         ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
