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
import functools
import hashlib
import inspect
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
TRUSTED_SOURCE_SCHEMA = "findings-trusted-source-map/1"
LAYOUT_FLAT = "flat-finding-json-v1"
LAYOUT_SYNTHETIC = "synthetic-in-memory-v1"
V2_SYNTHETIC_SOURCE_ID = "yzt66-v2-synthetic-findings"
V2_SYNTHETIC_EMPTY_SOURCE_ID = "yzt66-v2-synthetic-findings-empty"

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

RESOLVER_CAPTURE_ONLY = "capture-only"
RESOLVER_AUTHENTICATED_CLI = "authenticated-cli"


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


def load_trusted_source_map(path_or_doc) -> dict:
    """Load a Lead-approved source map. A source_id string is not proof."""
    if isinstance(path_or_doc, dict):
        doc = copy.deepcopy(path_or_doc)
    else:
        doc, _digest, _size = load_json_strict_file(
            path_or_doc, "trusted findings source map")
    _require(isinstance(doc, dict), "trusted source map must be a JSON object")
    _require(doc.get("schema") == TRUSTED_SOURCE_SCHEMA,
             f"trusted source map schema must be {TRUSTED_SOURCE_SCHEMA!r}",
             found=doc.get("schema"))
    sources = doc.get("sources")
    _require(isinstance(sources, dict) and sources,
             "trusted source map.sources must be a non-empty object")
    pid = doc.get("project_id")
    _require(isinstance(pid, str) and pid.strip(),
             "trusted source map.project_id must be a non-blank string")
    for source_id, entry in sources.items():
        _require(isinstance(source_id, str) and source_id.strip(),
                 "trusted source map source_id must be a non-blank string")
        _require(isinstance(entry, dict),
                 "trusted source map entry must be an object",
                 source_id=source_id)
        root = entry.get("root")
        _require(isinstance(root, str) and root.strip(),
                 "trusted source map entry.root must be a non-blank string",
                 source_id=source_id)
        root_path = Path(root)
        _require(root_path.is_absolute(),
                 "trusted source map entry.root must be an absolute path",
                 source_id=source_id)
        entry["root"] = str(root_path)
        allowed = entry.get("allowed")
        _require(isinstance(allowed, dict),
                 "trusted source map entry.allowed must be an object",
                 source_id=source_id)
        for key in ("task_refs", "roles"):
            values = allowed.get(key)
            _require(isinstance(values, list) and values and
                     all(isinstance(v, str) and v.strip() for v in values),
                     f"trusted source map entry.allowed.{key} must be a "
                     "non-empty string list", source_id=source_id)
        if "snapshot_digest" in entry and entry["snapshot_digest"] is not None:
            _require(_sha256_hex(entry.get("snapshot_digest")) is not None,
                     "trusted source map snapshot_digest must be sha256",
                     source_id=source_id)
    return doc


def verify_trusted_identity(binding: dict, trusted: dict | None, *,
                            task_ref: str | None = None,
                            role: str | None = None,
                            boundary: str | None = None) -> dict:
    """Bind Lead-approved source_id/root/project; refuse arbitrary roots."""
    if trusted is None:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "a trusted source map is required; a source_id string or "
            "comment digest is not ownership proof",
            boundary=boundary)
    if not isinstance(trusted, dict) or \
            trusted.get("schema") != TRUSTED_SOURCE_SCHEMA:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "trusted source map is missing or has the wrong schema",
            boundary=boundary)
    sources = trusted.get("sources") or {}
    source_id = binding.get("source_id")
    entry = sources.get(source_id) if isinstance(source_id, str) else None
    if not isinstance(entry, dict):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "binding source_id is not in the trusted source map",
            boundary=boundary, detail=repr(source_id))
    if binding.get("project_id") != trusted.get("project_id"):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "binding project does not match the trusted source map project",
            boundary=boundary,
            detail=f"{binding.get('project_id')!r} != "
                   f"{trusted.get('project_id')!r}")
    bound_root = Path(binding["root"])
    mapped_root = Path(entry["root"])
    if bound_root != mapped_root:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "caller-selected root is not the trusted mapped physical root",
            boundary=boundary,
            detail=f"{str(bound_root)!r} != {str(mapped_root)!r}")
    allowed = entry.get("allowed") or {}
    if task_ref is not None and task_ref not in allowed.get("task_refs", []):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "the trusted source map does not authorize this dispatch task",
            boundary=boundary, detail=task_ref)
    if role is not None and role not in allowed.get("roles", []):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "the trusted source map does not authorize this dispatch role",
            boundary=boundary, detail=role)
    return entry


def validate_binding(binding, *, project_id: str | None = None,
                     trusted=None, require_trusted: bool = False) -> dict:
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
    if require_trusted or trusted is not None:
        verify_trusted_identity(out, trusted)
    return out


class SimulationEntry:
    """Fake-only test constructor. Does not wrap live runners.

    Production constructors never infer simulation from class names, fake
    attributes, or inner/runner wrappers. Official production construction
    cannot select this path. Tests that need omitted-binding fixture mode
    must wrap an inert recording spy with `construct_simulation_entry` and
    pass `legacy_fixture=True` / `require_findings_source=False` explicitly.

    Inner must be a class-instance spy (for example `FixtureRunner` or an
    in-memory fake). Functions, partials, and mixed wrappers whose
    inner/runner is a live callable are refused so this object cannot
    forward arbitrary live argv.
    """

    def __init__(self, inner):
        if isinstance(inner, SimulationEntry):
            inner = inner.inner
        if not _is_inert_recording_spy(inner):
            raise TypeError(
                "SimulationEntry only wraps an inert recording spy "
                "(a class-instance fake such as FixtureRunner); it does "
                "not forward arbitrary live runners")
        self.inner = inner

    def __call__(self, argv):
        return self.inner(argv)

    def __getattr__(self, name):
        # Forward spy helpers (e.g. FakeMultica.on_mention_authorized).
        # This is not a production exemption: is_simulation_transport is
        # isinstance-only.
        return getattr(self.inner, name)


def construct_simulation_entry(inner) -> SimulationEntry:
    """The only supported way to build a simulation transport entry."""
    if isinstance(inner, SimulationEntry):
        return inner
    return SimulationEntry(inner)


def _is_function_like(obj) -> bool:
    if obj is None:
        return False
    if inspect.isfunction(obj) or inspect.ismethod(obj) or inspect.isbuiltin(obj):
        return True
    if isinstance(obj, functools.partial):
        return True
    return type(obj).__name__ in (
        "function", "method", "builtin_function_or_method", "partial")


def _nested_transport(obj):
    if obj is None:
        return None
    for attr in ("inner", "runner"):
        nxt = getattr(obj, attr, None)
        if nxt is not None and nxt is not obj:
            return nxt
    return None


def _is_inert_recording_spy(obj) -> bool:
    """True for class-instance spies that do not nest a live callable.

    Hostile same-privilege objects that subprocess inside `__call__`
    without a nested inner are out of scope (not a capability platform).
    """
    current = obj
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, SimulationEntry):
            current = current.inner
            continue
        if _is_function_like(current) or isinstance(current, type):
            return False
        nxt = _nested_transport(current)
        if nxt is None:
            return True
        current = nxt
    return False


def is_simulation_transport(runner) -> bool:
    """True only for the independent SimulationEntry test constructor.

    `simulation_transport`, `explicit_simulation_entry`, class name
    `FixtureRunner`, and inner/runner traversal are not exemptions.
    `legacy_fixture=True` still cannot issue live effects unless the
    runner is this fake-only entry.
    """
    return isinstance(runner, SimulationEntry)


def resolver_kind(resolver) -> str | None:
    kind = getattr(resolver, "kind", None)
    return kind if isinstance(kind, str) else None


class CaptureFileResolver:
    """Simulation-only: rehash a local capture file. Never production authority.

    Production publication/dispatch must refuse this resolver. The operator
    helper CLI is the only supported capture-only consumer.
    """

    kind = RESOLVER_CAPTURE_ONLY
    is_production = False

    def __init__(self, path):
        self.path = path
        self.calls: list = []

    def __call__(self, authority):
        self.calls.append(copy.deepcopy(authority))
        doc, _digest, _size = load_json_strict_file(
            self.path, "authority capture")
        return doc


class AuthenticatedCommentResolver:
    """Production authority: authenticated CLI read of the live comment.

    Each call re-reads `issue comment list --thread` for the bound
    comment_id. A local capture is never consulted; platform edit or
    revocation with an unchanged capture is a refusal.
    """

    kind = RESOLVER_AUTHENTICATED_CLI
    is_production = True

    def __init__(self, cli):
        if cli is None:
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "authenticated authority resolution requires a CLI transport")
        self.cli = cli
        self.calls: list = []

    def __call__(self, authority):
        issue_id = authority.get("issue_id")
        comment_id = authority.get("comment_id")
        self.calls.append({"issue_id": issue_id, "comment_id": comment_id})
        try:
            thread = self.cli.comment_thread(issue_id, comment_id)
        except FindingsSourceRefusal:
            raise
        except Exception as exc:  # noqa: BLE001 - CLI failure is a stop
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "authenticated authority CLI read failed: "
                f"{type(exc).__name__}: {exc}") from None
        if not isinstance(thread, list):
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "authenticated authority CLI did not return a comment list")
        match = None
        for rec in thread:
            if isinstance(rec, dict) and rec.get("id") == comment_id:
                match = rec
                break
        if match is None:
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "the authority comment is absent or revoked on the platform; "
                "a local capture is not authority")
        content = match.get("content")
        if not isinstance(content, str):
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                "the live authority comment carries no verifiable content")
        return {
            "comment_id": match.get("id"),
            "issue_id": match.get("issue_id") or issue_id,
            "author_id": match.get("author_id"),
            "author_type": match.get("author_type"),
            "content": content,
        }


def gate_effectful_findings(*, source, runner, require_source: bool,
                            allow_legacy_fixture: bool, what: str) -> None:
    """Refuse omitted/capture-only sources before publication or trigger.

    A legacy fixture is allowed only when explicitly requested AND the
    runner is a `SimulationEntry` (fake-only test constructor). Marker
    attributes, class names, and mixed inner/runner wrappers are not
    enough.
    """
    sim = is_simulation_transport(runner)
    if source is None:
        if require_source or not allow_legacy_fixture:
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                f"{what} requires a verified Findings source binding; "
                "omitted binding is refused")
        if not sim:
            raise FindingsSourceRefusal(
                "findings_source_unbound",
                f"legacy unbound Findings fixture mode cannot {what} "
                "through a non-simulation transport")
        return
    kind = resolver_kind(getattr(source, "resolver", None))
    if kind == RESOLVER_CAPTURE_ONLY and not sim:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            f"capture-only authority cannot {what}; production requires "
            "authenticated CLI reads of the live comment")
    if not getattr(source, "is_production", False) and not sim:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            f"a simulation-only Findings source cannot {what} through a "
            "non-simulation transport")


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
    after_digests = []
    for name in after_names:
        path = root_path / name
        try:
            data = reader.read_bytes(path)
        except FindingsSourceRefusal:
            raise
        except OSError as exc:
            raise FindingsSourceRefusal(
                "findings_source_unreadable",
                f"finding file cannot be re-read: {name}",
                boundary=boundary,
                detail=f"{type(exc).__name__}: {exc}") from None
        after_digests.append((name, sha256_digest(data), len(data)))
    before_digests = [(row["file"], row["sha256"], row["bytes"])
                      for row in inventory]
    if after_digests != before_digests:
        raise FindingsSourceRefusal(
            "findings_source_changed",
            "a same-name Findings record changed while it was being read; "
            "no automatic re-read to success is allowed",
            boundary=boundary,
            detail=f"{before_digests} -> {after_digests}")
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
                       boundary: str | None = None,
                       trusted: dict | None = None) -> list:
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
        if observation.get("project_id") != binding.get("project_id"):
            problems.append("observation project id")
    if trusted is not None and isinstance(observation, dict):
        try:
            verify_trusted_identity(
                {"source_id": observation.get("source_id"),
                 "project_id": observation.get("project_id"),
                 "root": observation.get("resolved_root")},
                trusted, task_ref=task_ref, role=role, boundary=boundary)
        except FindingsSourceRefusal as exc:
            problems.append(exc.message)
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
    list or digest is cached between boundaries. Production identity follows
    the resolver: capture-only is never production authority.
    """

    def __init__(self, binding: dict, *, resolver, reader=None,
                 project_id: str | None = None,
                 expected_commit: str | None = None,
                 expected_adapter_digest: str | None = None,
                 clock=None, trusted=None, require_trusted: bool = False):
        self.trusted = (load_trusted_source_map(trusted)
                        if isinstance(trusted, (str, Path)) else trusted)
        self.binding = validate_binding(
            binding, project_id=project_id, trusted=self.trusted,
            require_trusted=require_trusted)
        self.resolver = resolver
        self.reader = reader or FilesystemReader()
        self.expected_commit = expected_commit
        self.expected_adapter_digest = expected_adapter_digest
        self.clock = clock or now_iso
        self._last_observation: dict | None = None

    @property
    def is_production(self) -> bool:
        flag = getattr(self.resolver, "is_production", None)
        if flag is not None:
            return bool(flag)
        return resolver_kind(self.resolver) == RESOLVER_AUTHENTICATED_CLI

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
        if self.trusted is not None:
            verify_trusted_identity(
                self.binding, self.trusted, task_ref=task_ref, role=role,
                boundary=boundary)
        snapshot = read_snapshot(self.binding["root"],
                                 source_id=self.binding["source_id"],
                                 reader=self.reader, boundary=boundary,
                                 clock=self.clock)
        if self.trusted is not None:
            entry = (self.trusted.get("sources") or {}).get(
                self.binding["source_id"]) or {}
            expected = entry.get("snapshot_digest")
            if expected and prior_observation is None and \
                    snapshot["snapshot_digest"] != expected:
                raise FindingsSourceRefusal(
                    "findings_source_changed",
                    "the Findings bytes do not match the trusted map "
                    "inventory digest recorded for this source",
                    boundary=boundary,
                    detail=f"{snapshot['snapshot_digest']} != {expected}")
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
            problems = verify_observation(
                prior_observation, binding=self.binding,
                task_ref=task_ref, role=role, trusted=self.trusted)
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
                             reader=None, clock=None, trusted=None,
                             require_trusted: bool = False) -> BoundFindingsSource:
    binding, _digest, _size = load_json_strict_file(
        path, "findings source binding")
    return BoundFindingsSource(
        binding, resolver=resolver, reader=reader, project_id=project_id,
        expected_commit=expected_commit,
        expected_adapter_digest=expected_adapter_digest, clock=clock,
        trusted=trusted, require_trusted=require_trusted)


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
    if source is None:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "worker entry requires a verified Findings source binding; "
            "omitted binding is refused")
    if not allow_simulation and not getattr(source, "is_production", False):
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "a simulation-only Findings source is not production worker-entry "
            "authority")
    if not allow_simulation and resolver_kind(getattr(source, "resolver", None)) \
            == RESOLVER_CAPTURE_ONLY:
        raise FindingsSourceRefusal(
            "findings_source_unbound",
            "capture-only authority is not production worker-entry authority")
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

    resolver = CaptureFileResolver(args.authority_file)

    try:
        source = source_from_binding_file(args.binding_file,
                                          resolver=resolver)
        if args.command == "validate":
            print(json.dumps({"ok": True, "source_id": source.source_id,
                              "binding_digest": source.binding_digest,
                              "root": source.binding["root"],
                              "authority_kind": RESOLVER_CAPTURE_ONLY,
                              "is_production": source.is_production,
                              "effects": "none — capture-only helper cannot "
                                         "publish or dispatch"},
                             ensure_ascii=False, indent=2))
            return 0
        snap = source.read(boundary=args.boundary)
        observation = dict(snap["observation"])
        observation["authority_kind"] = RESOLVER_CAPTURE_ONLY
        observation["is_production"] = source.is_production
        print(json.dumps(observation, ensure_ascii=False, indent=2))
        return 0
    except FindingsSourceRefusal as exc:
        print(json.dumps({"ok": False, **exc.as_dict()},
                         ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
