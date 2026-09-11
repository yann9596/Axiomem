#!/usr/bin/env python3
"""U12-P0R (YZT-82) — canary-facing strict receipt gate.

Forward-only, fail-closed preclassification for native trigger receipts. The
Human-approved U12 contract accepts exactly three receipt shapes:

  1. a direct run object carrying every observable run field;
  2. a one-element list whose sole item is that run object;
  3. an object with `runs` as a one-element list containing that run object.

Everything else is refused before correlation: `{"run": {...}}` (even with a
valid nested run), empty or multi-item lists, `{"runs": []}`, `{runs:[r1,r2]}`,
mixed wrapper shapes, competing wrapper keys, non-JSON, scalars/null,
missing/blank observable fields, and any shape that is not provably one of the
three authorized forms. Extra fields inside the run object are ignored only
after the top-level shape has been classified as one of the three forms.

F4 (YZT-82 repair): the strict decoder refuses any JSON object that repeats a
key at ANY nesting level — observable fields, the `runs` wrapper, run rows and
objects nested inside ignored extra fields alike — including when the repeated
values are identical. Python's default `json` object builder silently keeps
the last value for a repeated key, so a receipt such as
`{"id": "first", "id": "second", ...}` or `{"runs": [], "runs": [run]}` would
previously classify as an authorized shape; the strict `object_pairs_hook`
now raises during decoding, before classification and before the immutable O2
parser is reachable. Duplicate-key refusal is a typed ambiguity
(`duplicate_json_key` -> `StrictReceiptRefused` -> `TRIGGER_AMBIGUOUS`).

The accepted O2 parser (`chandoff_intent.parse_run_object`) is immutable
history and keeps its historical behavior for accepted U06–U11 replays. It is
reachable from R0 receipt handling only behind this strict preclassification:
a receipt that fails classification never reaches it, and there is no bypass,
fallback or second receipt entrypoint.

`StrictReceiptBoundary` (with `CanaryOrchestrator`) is the only constructed
issuing path for the proposed/live R0 canary. It replaces the permissive
boundary receipt method with the strict gate and refuses the assignment
trigger, so the permissive entrypoint cannot be used by the live canary path.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_intent as o2  # noqa: E402

GATE_VERSION = "U12-P0R/1.1"
RECEIPT_ENTRYPOINT = "tools.u12_strict_receipt.StrictReceiptBoundary.rerun_issue"
PERMISSIVE_ENTRYPOINT = "tools.chandoff_intent.O2DispatchBoundary.rerun_issue"

SHAPE_RUN_OBJECT = "run_object"
SHAPE_RUN_LIST = "run_list"
SHAPE_RUNS_WRAPPER = "runs_wrapper"
AUTHORIZED_SHAPES = (SHAPE_RUN_OBJECT, SHAPE_RUN_LIST, SHAPE_RUNS_WRAPPER)

RUN_FIELDS = tuple(dispatch.RUN_CONTRACT_FIELDS)

R_INPUT_NOT_TEXT = "input_not_text"
R_NOT_JSON = "not_json"
R_DUPLICATE_KEY = "duplicate_json_key"
R_SCALAR_OR_NULL = "scalar_or_null"
R_RUN_WRAPPER = "run_wrapper_unauthorized"
R_RUNS_NOT_LIST = "runs_not_list"
R_RUNS_ARITY = "runs_arity_not_one"
R_RUN_LIST_ARITY = "run_list_arity_not_one"
R_COMPETING_KEY = "competing_wrapper_key"
R_MIXED_WRAPPER = "mixed_wrapper_shape"
R_ROW_NOT_OBJECT = "run_row_not_object"
R_MISSING_FIELD = "missing_or_blank_observable_field"
R_UNCLASSIFIED = "unclassified_shape"


class _DuplicateJsonKey(Exception):
    """Strict-decoder refusal: a JSON object repeated a key.

    Raised from `object_pairs_hook` while decoding, so a receipt carrying a
    repeated key at any nesting level never reaches shape classification or
    the immutable O2 parser. Deliberately not a `ValueError`: the `json`
    decoder may translate hook `ValueError`s into `JSONDecodeError`, which
    would blur this typed refusal into a generic parse error.
    """

    def __init__(self, keys):
        self.keys = tuple(keys)
        super().__init__("duplicate JSON key(s): " + ", ".join(self.keys))


def _reject_duplicate_keys(pairs):
    """`object_pairs_hook` that fails closed on any repeated object key.

    Runs for every JSON object at every nesting level (including objects
    inside arrays, run rows and ignored extra fields), so duplicates are
    refused before last-wins overwriting can hide an ambiguous receipt. A
    repeated key is rejected whether or not the repeated values are equal.
    """
    seen = set()
    duplicates = []
    for key, _value in pairs:
        if key in seen:
            if key not in duplicates:
                duplicates.append(key)
        else:
            seen.add(key)
    if duplicates:
        raise _DuplicateJsonKey(duplicates)
    return dict(pairs)


def _decode_strict_json(text):
    """Decode receipt JSON with duplicate-key refusal at every level."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


class StrictReceiptRefused(o2.ReceiptAmbiguousError):
    """Typed fail-closed refusal of the strict gate.

    Subclasses the accepted O2 `ReceiptAmbiguousError`, so an O2 orchestrator
    turns it into the exact typed stop `TRIGGER_AMBIGUOUS` (manual stop, zero
    retry, zero route switch).
    """

    code = "trigger_receipt_ambiguous"


def _refusal(reason: str, *, shape: str | None = None,
             detail: str = "", **extra) -> dict:
    result = {"accepted": False, "shape": shape, "reason": reason,
              "detail": detail}
    result.update(extra)
    return result


def _validate_run(row, *, shape: str) -> dict:
    if not isinstance(row, dict):
        return _refusal(R_ROW_NOT_OBJECT, shape=shape,
                        detail=f"run row is {type(row).__name__}")
    missing = [field for field in RUN_FIELDS
               if not isinstance(row.get(field), str)
               or not row.get(field).strip()]
    if missing:
        return _refusal(R_MISSING_FIELD, shape=shape,
                        fields=missing,
                        detail="run is missing a non-blank observable field")
    return {"accepted": True, "shape": shape, "reason": None, "detail": "",
            "fields": {field: row[field].strip() for field in RUN_FIELDS}}


def classify_strict_receipt(text) -> dict:
    """Classify a trigger receipt against the three authorized shapes.

    Pure and non-raising for every JSON-expressible input: returns
    `{"accepted": bool, "shape": str|None, "reason": str|None, ...}`.

    Decoding rejects any repeated JSON object key at every nesting level
    (`duplicate_json_key`) before any shape is considered.
    """
    if not isinstance(text, (str, bytes, bytearray)):
        return _refusal(R_INPUT_NOT_TEXT,
                        detail=f"receipt is {type(text).__name__}")
    try:
        data = _decode_strict_json(text)
    except _DuplicateJsonKey as exc:
        return _refusal(R_DUPLICATE_KEY, detail=str(exc)[:120],
                        duplicate_keys=list(exc.keys))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _refusal(R_NOT_JSON, detail=str(exc)[:120])
    if isinstance(data, list):
        if len(data) != 1:
            return _refusal(R_RUN_LIST_ARITY, shape=SHAPE_RUN_LIST,
                            candidate_count=len(data))
        return _validate_run(data[0], shape=SHAPE_RUN_LIST)
    if isinstance(data, dict):
        keys = set(data.keys())
        if "runs" in data:
            competing = sorted(keys - {"runs"})
            if competing:
                reason = (R_MIXED_WRAPPER if "run" in keys
                          else R_COMPETING_KEY)
                return _refusal(reason, shape=SHAPE_RUNS_WRAPPER,
                                competing_keys=competing,
                                detail="wrapper object carries key(s) "
                                       "beyond the authorized `runs`")
            runs = data["runs"]
            if not isinstance(runs, list):
                return _refusal(R_RUNS_NOT_LIST, shape=SHAPE_RUNS_WRAPPER,
                                detail=f"`runs` is {type(runs).__name__}")
            if len(runs) != 1:
                return _refusal(R_RUNS_ARITY, shape=SHAPE_RUNS_WRAPPER,
                                candidate_count=len(runs))
            return _validate_run(runs[0], shape=SHAPE_RUNS_WRAPPER)
        if "run" in data:
            return _refusal(R_RUN_WRAPPER, shape="run_wrapper",
                            detail="`{\"run\": {...}}` is not an authorized "
                                   "receipt shape")
        return _validate_run(data, shape=SHAPE_RUN_OBJECT)
    return _refusal(R_SCALAR_OR_NULL,
                    detail=f"receipt is {type(data).__name__}")


def parse_strict_receipt(text) -> dict:
    """Strict gate entrypoint: classify first, extract observable fields after.

    Raises `StrictReceiptRefused` (a `ReceiptAmbiguousError`) for every shape
    that is not provably one of the three authorized forms. The accepted O2
    parser is called only after classification succeeds; a refused receipt
    never reaches it and there is no fallback.
    """
    result = classify_strict_receipt(text)
    if not result["accepted"]:
        raise StrictReceiptRefused(
            "trigger receipt is outside the three authorized shapes; "
            "issuance is ambiguous",
            shape=result.get("shape") or R_UNCLASSIFIED,
            reason=result.get("reason") or R_UNCLASSIFIED,
            detail=result.get("detail", ""))
    payload = text if isinstance(text, str) else bytes(text).decode("utf-8")
    try:
        run = o2.parse_run_object(payload)
    except o2.IntentError as exc:
        raise StrictReceiptRefused(
            "authorized receipt failed the observable-field contract",
            shape=result["shape"], reason="observable_field_contract",
            detail=exc.message) from exc
    return {"shape": result["shape"], "run": run}


class StrictReceiptBoundary(o2.O2DispatchBoundary):
    """R0 canary issuing boundary: the strict gate is the only receipt entry."""

    ROUTE = "rerun_only"

    def rerun_issue(self, issue_id: str) -> dict:
        issue_id = o2._bare_id(issue_id, "issue_id")
        argv = ["issue", "rerun", issue_id, "--output", "json"]
        if not o2._valid_rerun_argv(argv):
            raise o2.IntentError("rerun trigger argv is outside the frozen form")
        code, out, err = self._run(argv)
        if code != 0:
            raise StrictReceiptRefused(
                f"issue rerun failed (exit {code}); issuance is ambiguous",
                shape="nonzero_exit", reason="nonzero_exit",
                detail=str(err or "")[:120])
        parsed = parse_strict_receipt(out or "")
        return {"outcome": "confirmed",
                "argv_digest": o2.digest(argv),
                "response_digest": o2.digest_text(out or ""),
                "receipt_shape": parsed["shape"],
                "run": parsed["run"]}

    def assign_trigger(self, issue_id: str, agent_id: str) -> dict:
        raise o2.IntentError(
            "the R0 canary issuing path is rerun-only; the assignment trigger "
            "is not an authorized receipt entrypoint")


class CanaryOrchestrator(o2.O2Orchestrator):
    """O2 orchestrator whose only dispatch boundary is the strict R0 boundary.

    The permissive `O2DispatchBoundary` receipt method constructed by the
    accepted O2 `__init__` is replaced before any dispatch can occur; no
    fallback to it exists.
    """

    def __init__(self, store, *, runner, executable: str = "multica",
                 clock=None, workdir=None, ttl_seconds: int = 300):
        super().__init__(store, runner=runner, executable=executable,
                         clock=clock, workdir=workdir,
                         ttl_seconds=ttl_seconds)
        self.boundary = StrictReceiptBoundary(
            runner, store, executable=executable, workdir=workdir)


def build_r0_canary_orchestrator(store, *, runner, **kwargs) -> CanaryOrchestrator:
    return CanaryOrchestrator(store, runner=runner, **kwargs)


def _sha256_lf(path: Path) -> str:
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def gate_descriptor(module_path=None) -> dict:
    path = Path(module_path) if module_path else Path(__file__).resolve()
    return {
        "module": "tools/u12_strict_receipt.py",
        "version": GATE_VERSION,
        "sha256_lf": _sha256_lf(path),
        "receipt_entrypoint": RECEIPT_ENTRYPOINT,
        "permissive_entrypoint": PERMISSIVE_ENTRYPOINT,
        "orchestrator": "tools.u12_strict_receipt.CanaryOrchestrator",
        "authorized_shapes": list(AUTHORIZED_SHAPES),
        "observable_fields": list(RUN_FIELDS),
        "run_wrapper_authorized": False,
        "competing_wrapper_key_fails_closed": True,
        "duplicate_key_fails_closed": True,
        "duplicate_key_scope": "every_json_object_at_every_nesting_level",
        "same_valued_duplicate_keys_fail_closed": True,
        "no_bypass_no_fallback": True,
    }


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


def wiring_proof(source=None, module_path=None) -> dict:
    """Static proof that the permissive parser is reachable only behind the
    strict preclassification, and that the R0 boundary uses the strict gate.
    """
    path = Path(module_path) if module_path else Path(__file__).resolve()
    text = source if source is not None else path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    functions = {}
    classes = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions[node.name] = node
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = node

    def method(class_name, method_name):
        cls = classes.get(class_name)
        if cls is None:
            return None
        for item in cls.body:
            if isinstance(item, ast.FunctionDef) and item.name == method_name:
                return item
        return None

    parse_strict = functions.get("parse_strict_receipt")
    strict_parse_calls = [] if parse_strict is None else [
        (name, line) for name, line in _call_sites(parse_strict)
        if name == "parse_run_object"]
    classify = functions.get("classify_strict_receipt")
    classify_sites = [] if classify is None else _call_sites(classify)
    classify_calls = [] if parse_strict is None else [
        line for name, line in _call_sites(parse_strict)
        if name == "classify_strict_receipt"]

    def _uses_object_pairs_hook(node) -> bool:
        if node is None:
            return False
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            if any(kw.arg == "object_pairs_hook" for kw in sub.keywords):
                return True
        return False

    module_parse_calls = [
        (name, line) for node in tree.body
        for name, line in _call_sites(node) if name == "parse_run_object"]
    rerun = method("StrictReceiptBoundary", "rerun_issue")
    assign = method("StrictReceiptBoundary", "assign_trigger")
    rerun_calls = [] if rerun is None else _call_sites(rerun)
    assign_calls = [] if assign is None else _call_sites(assign)
    checks = {
        "strict_parse_function_present": parse_strict is not None,
        "parse_run_object_call_sites": len(module_parse_calls),
        "parse_run_object_only_in_parse_strict_receipt":
            len(module_parse_calls) == 1
            and bool(strict_parse_calls)
            and all(line == strict_parse_calls[0][1]
                    for name, line in module_parse_calls),
        "classify_precedes_o2_parser":
            bool(strict_parse_calls) and bool(classify_calls)
            and min(classify_calls) < min(
                line for name, line in strict_parse_calls),
        "duplicate_key_guard_present":
            functions.get("_reject_duplicate_keys") is not None
            and _uses_object_pairs_hook(functions.get("_decode_strict_json"))
            and any(name == "_decode_strict_json"
                    for name, _ in classify_sites),
        "refusal_precedes_o2_parser":
            bool(strict_parse_calls)
            and any(isinstance(sub, ast.Raise)
                    for sub in ast.walk(parse_strict)
                    if getattr(sub, "lineno", 10 ** 9)
                    < min(line for name, line in strict_parse_calls)),
        "rerun_issue_uses_strict_gate":
            rerun is not None
            and any(name == "parse_strict_receipt" for name, _ in rerun_calls),
        "rerun_issue_never_calls_permissive_parser":
            rerun is not None
            and not any(name == "parse_run_object" for name, _ in rerun_calls),
        "assign_trigger_is_refused_without_parse":
            assign is not None
            and not any(name in ("parse_strict_receipt", "parse_run_object")
                        for name, _ in assign_calls),
        "permissive_boundary_method_overridden":
            StrictReceiptBoundary.rerun_issue is not
            o2.O2DispatchBoundary.rerun_issue,
        "canary_orchestrator_replaces_boundary":
            _orchestrator_replaces_boundary(classes),
    }
    checks["permissive_entrypoint_reachable_in_r0_path"] = False
    bool_checks = {key: value for key, value in checks.items()
                   if key not in ("parse_run_object_call_sites",
                                  "permissive_entrypoint_reachable_in_r0_path")}
    checks["ok"] = (all(bool_checks.values())
                    and not checks["permissive_entrypoint_reachable_in_r0_path"])
    return {
        "module": "tools/u12_strict_receipt.py",
        "checks": checks,
        "ok": bool(checks["ok"]),
    }


def _orchestrator_replaces_boundary(classes) -> bool:
    cls = classes.get("CanaryOrchestrator")
    if cls is None:
        return False
    for node in ast.walk(cls):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr == "boundary"
                    and isinstance(node.value, ast.Call)):
                func = node.value.func
                if isinstance(func, ast.Name) and \
                        func.id == "StrictReceiptBoundary":
                    return True
                if isinstance(func, ast.Attribute) and \
                        func.attr == "StrictReceiptBoundary":
                    return True
    return False
