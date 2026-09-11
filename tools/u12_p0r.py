#!/usr/bin/env python3
"""U12-P0R (YZT-82) — strict R0 receipt gate readiness evidence generator.

Forward-only tooling that turns the accepted U12-P0 production state plus the
new canary-facing strict receipt gate into one deterministic, machine-readable
evidence bundle:

  generate -> strict gate evidence, pin recheck, production-ledger read-only
              integrity, F1 forward errata, updated R0 canary plan and the
              superseding readiness manifest
  verify   -> read-only live recheck of the same boundaries (no writes)
  check    -> regenerate the committed bundle with its recorded timestamp and
              compare every file byte-for-byte

Safety: this tool never issues a trigger, never writes to the production
ledger, never mutates an issue/comment/run, and never touches Canonical
Memory, the product repository or accepted predecessor history.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff_intent as o2  # noqa: E402
import u12_preflight as p0  # noqa: E402
import u12_strict_receipt as gate  # noqa: E402

TOOL_VERSION = "U12-P0R/1.2"
SCHEMA_VERSION = "U12-P0R/1.2"
TASK_REF = "YZT-82"
BRANCH = "yzt-82-u12p0r-strict-receipt-gate"
BASE_COMMIT = "6a6c018c718795aedc677fe61fabfe30fee96217"
PRIOR_COMMIT = "13138538fbaffe0349f6e85ea94313fb78db508b"
LINEAGE_COMMIT = "afc13fd43c1fa5b468c881f9abc5e174bd0c77bd"
ROOT = Path(__file__).resolve().parent.parent

AUTHORITY = {
    "human_approval": "01a08e93-0a1a-7074-9dda-912d1237bf56",
    "lead_u12_decision": "01a08e89-22ec-7db7-a581-0c62f30db538",
    "lead_f2_escalation": "01a08ebc-a0e5-7c7f-a150-f917e2cd447d",
    "parent": "YZT-66",
}

PRIOR_PLAN = "adapters/multica/u12-p0/proposed-r0-canary-plan.json"
PRIOR_PLAN_DIGEST = ("sha256:0b88856d9e9969d1d9357b3da7a114f34089e7b0247794c412dcb0"
                     "e1a7febc7a")
PRIOR_MANIFEST = "adapters/multica/u12-p0/production-root-manifest.json"
PRIOR_MANIFEST_DIGEST = ("sha256:78f6a4566d40963672e724824cd715a96a9f7f1ccb339d0c"
                         "09025b60c795ee7b")

# The F5 repair is forward-only from the rejected U12-P0R/1.1 readiness
# revision at commit 13138538. Those bytes stay in history; the new manifest
# records the superseded revision and verifies it from that commit's blobs,
# and also verifies the original U12-P0R/1.0 revision at afc13fd as lineage.
PRIOR_P0R_MANIFEST = "adapters/multica/u12-p0r/readiness-manifest.json"
PRIOR_P0R_MANIFEST_DIGEST = ("sha256:95a2be772aa837dd7507503a84d5eaefc4e43057f216"
                             "663484d4fd703f9f496e")
PRIOR_P0R_GATE_SHA256_LF = ("sha256:6d6452a5a17911926503a2eece3725cd58ac22b1ef1"
                            "0d7bb5f292901a207eb49")
LINEAGE_P0R_MANIFEST_DIGEST = ("sha256:8eed5a7b36130bff199aca3eb89c20cbd291e15652"
                               "d2708f711e293027750791")
LINEAGE_P0R_GATE_SHA256_LF = ("sha256:a8280b398125788f9e52c1dad411a26228a685d424"
                              "d272bd08d53c0a4b7b076a")

PRODUCTION_LEDGER_PATH = r"D:\AI\multica-state\web-imagegen\dispatch\ledger.jsonl"
PRODUCTION_LEDGER_TIP = ("sha256:c96838987bd362b263c177ba670e193f2cdcdf02e1694f8966"
                         "67ab57920c3412")
PRODUCTION_LEDGER_BYTES = 622

STRICT_GATE_MODULE = "tools/u12_strict_receipt.py"
O2_TEST_PATH = "tools/tests/test_handoff_intent.py"
O2_TEST_RECORDED = ("sha256:649580b22055202280a670889e0a8d5ec5b93a51d2a90ce8dc"
                    "094d268502923d")
O2_TEST_CORRECT_LF = ("sha256:78fb99d40ad22ffcf45a087314901e26df5c1d502b038404728"
                      "c4f1672283665")
ERRATA_COMMITS = ("c24284a", "49c48a9", "6439bd4", "6a6c018")
ERRATA_REPORT = "adapters/multica/O2_DURABLE_DISPATCH_INTENT_REPORT.md"

BUNDLE_FILES = (
    "strict-receipt-gate-evidence.json",
    "pin-verification.json",
    "production-ledger-readonly-integrity.json",
    "O2_REPORT_DIGEST_ERRATA.json",
    "proposed-r0-canary-plan.json",
    "readiness-manifest.json",
)

RUN_CASE = {"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1",
            "status": "running"}
RUN_CASE_EXTRA = dict(RUN_CASE, extra_field="ignored", note=None)

# U12-P0R-F4: repeated JSON object keys must fail closed during decoding.
# These receipts are raw text (json.dumps cannot emit duplicate keys).
DUP_OBSERVABLE_LAST_WINS = (
    '{"id": "first", "id": "second", "issue_id": "iss-1", '
    '"agent_id": "agent-1", "status": "running"}')
DUP_OBSERVABLE_SAME_VALUE = (
    '{"id": "run-1", "id": "run-1", "issue_id": "iss-1", '
    '"agent_id": "agent-1", "status": "running"}')
DUP_RUNS_EMPTY_OVERWRITTEN = (
    '{"runs": [], "runs": [{"id": "run-1", "issue_id": "iss-1", '
    '"agent_id": "agent-1", "status": "running"}]}')
DUP_RUNS_SAME_VALUE = '{"runs": [], "runs": []}'
DUP_NESTED_RUN_ROW_FIELD = (
    '{"runs": [{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "status": "running"}]}')
DUP_NESTED_EXTRA_OBJECT = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": {"k": 1, "k": 2}}')
DUP_NESTED_EXTRA_ARRAY = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": [{"k": 1, "k": 2}]}')

# U12-P0R-F5: unquoted NaN/Infinity/-Infinity are not valid JSON numeric
# tokens. Raw text again; the first entry is the exact Lead reproduction.
NON_JSON_LEAD_REPRODUCTION = (
    '{"id":"r","issue_id":"i","agent_id":"a","status":"queued","extra":NaN}')
NON_JSON_TOP_LEVEL_NAN = "NaN"
NON_JSON_TOP_LEVEL_INFINITY = "Infinity"
NON_JSON_TOP_LEVEL_NEG_INFINITY = "-Infinity"
NON_JSON_TOP_LEVEL_LIST = "[NaN]"
NON_JSON_DIRECT_RUN_EXTRA_NAN = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": NaN}')
NON_JSON_DIRECT_RUN_EXTRA_INFINITY = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": Infinity}')
NON_JSON_DIRECT_RUN_EXTRA_NEG_INFINITY = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": -Infinity}')
NON_JSON_RUN_LIST_ROW_EXTRA = (
    '[{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": NaN}]')
NON_JSON_RUNS_WRAPPER_ROW_EXTRA = (
    '{"runs": [{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": -Infinity}]}')
NON_JSON_RUNS_VALUE = '{"runs": NaN}'
NON_JSON_NESTED_EXTRA_OBJECT = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": {"k": Infinity}}')
NON_JSON_NESTED_EXTRA_ARRAY = (
    '{"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1", '
    '"status": "running", "extra": [NaN]}')

RUN_CASE_QUOTED_CONSTANTS = dict(
    RUN_CASE, extra="NaN", note="-Infinity",
    nested={"words": ["Infinity", "-Infinity", "NaN"],
            "numbers": [0, 1, -2.5, 1e3]})

AUTHORIZED_CASES = (
    ("direct_run_object", gate.SHAPE_RUN_OBJECT, RUN_CASE),
    ("direct_run_object_with_extra_fields", gate.SHAPE_RUN_OBJECT,
     RUN_CASE_EXTRA),
    ("direct_run_object_with_quoted_constant_words", gate.SHAPE_RUN_OBJECT,
     RUN_CASE_QUOTED_CONSTANTS),
    ("one_item_run_list", gate.SHAPE_RUN_LIST, [RUN_CASE]),
    ("runs_one_item_wrapper", gate.SHAPE_RUNS_WRAPPER, {"runs": [RUN_CASE]}),
)

REFUSED_CASES = (
    ("run_wrapper", {"run": RUN_CASE}, gate.R_RUN_WRAPPER),
    ("run_wrapper_plus_observable_field", {"run": RUN_CASE, "id": "x"},
     gate.R_RUN_WRAPPER),
    ("mixed_run_plus_runs", {"run": RUN_CASE, "runs": [RUN_CASE]},
     gate.R_MIXED_WRAPPER),
    ("runs_extra_competing_key", {"runs": [RUN_CASE], "page": 1},
     gate.R_COMPETING_KEY),
    ("runs_extra_run_key", {"runs": [RUN_CASE], "run": RUN_CASE},
     gate.R_MIXED_WRAPPER),
    ("empty_list", [], gate.R_RUN_LIST_ARITY),
    ("two_runs", [RUN_CASE, RUN_CASE], gate.R_RUN_LIST_ARITY),
    ("runs_empty", {"runs": []}, gate.R_RUNS_ARITY),
    ("runs_two", {"runs": [RUN_CASE, RUN_CASE]}, gate.R_RUNS_ARITY),
    ("runs_not_list", {"runs": dict(RUN_CASE)}, gate.R_RUNS_NOT_LIST),
    ("empty_object", {}, gate.R_MISSING_FIELD),
    ("missing_field", {"id": "r", "issue_id": "i"}, gate.R_MISSING_FIELD),
    ("blank_field", dict(RUN_CASE, status="   "), gate.R_MISSING_FIELD),
    ("non_string_field", dict(RUN_CASE, id=42), gate.R_MISSING_FIELD),
    ("list_row_not_object", [1], gate.R_ROW_NOT_OBJECT),
    ("json_null", None, gate.R_SCALAR_OR_NULL),
    ("json_scalar_number", 7, gate.R_SCALAR_OR_NULL),
    ("json_scalar_string", "run", gate.R_SCALAR_OR_NULL),
    ("json_bool", True, gate.R_SCALAR_OR_NULL),
    ("raw_not_json", None, gate.R_NOT_JSON, "not json"),
    ("raw_empty_string", None, gate.R_NOT_JSON, ""),
    ("duplicate_observable_field_last_wins", None, gate.R_DUPLICATE_KEY,
     DUP_OBSERVABLE_LAST_WINS),
    ("duplicate_observable_field_same_value", None, gate.R_DUPLICATE_KEY,
     DUP_OBSERVABLE_SAME_VALUE),
    ("duplicate_runs_wrapper_empty_overwritten", None, gate.R_DUPLICATE_KEY,
     DUP_RUNS_EMPTY_OVERWRITTEN),
    ("duplicate_runs_wrapper_same_value", None, gate.R_DUPLICATE_KEY,
     DUP_RUNS_SAME_VALUE),
    ("duplicate_nested_run_row_field", None, gate.R_DUPLICATE_KEY,
     DUP_NESTED_RUN_ROW_FIELD),
    ("duplicate_nested_extra_object_key", None, gate.R_DUPLICATE_KEY,
     DUP_NESTED_EXTRA_OBJECT),
    ("duplicate_nested_extra_array_object_key", None, gate.R_DUPLICATE_KEY,
     DUP_NESTED_EXTRA_ARRAY),
    ("non_json_constant_lead_reproduction", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_LEAD_REPRODUCTION),
    ("non_json_constant_top_level_nan", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_TOP_LEVEL_NAN),
    ("non_json_constant_top_level_infinity", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_TOP_LEVEL_INFINITY),
    ("non_json_constant_top_level_neg_infinity", None,
     gate.R_NON_JSON_CONSTANT, NON_JSON_TOP_LEVEL_NEG_INFINITY),
    ("non_json_constant_top_level_list", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_TOP_LEVEL_LIST),
    ("non_json_constant_direct_run_extra_nan", None,
     gate.R_NON_JSON_CONSTANT, NON_JSON_DIRECT_RUN_EXTRA_NAN),
    ("non_json_constant_direct_run_extra_infinity", None,
     gate.R_NON_JSON_CONSTANT, NON_JSON_DIRECT_RUN_EXTRA_INFINITY),
    ("non_json_constant_direct_run_extra_neg_infinity", None,
     gate.R_NON_JSON_CONSTANT, NON_JSON_DIRECT_RUN_EXTRA_NEG_INFINITY),
    ("non_json_constant_run_list_row_extra", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_RUN_LIST_ROW_EXTRA),
    ("non_json_constant_runs_wrapper_row_extra", None,
     gate.R_NON_JSON_CONSTANT, NON_JSON_RUNS_WRAPPER_ROW_EXTRA),
    ("non_json_constant_runs_value", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_RUNS_VALUE),
    ("non_json_constant_nested_extra_object", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_NESTED_EXTRA_OBJECT),
    ("non_json_constant_nested_extra_array", None, gate.R_NON_JSON_CONSTANT,
     NON_JSON_NESTED_EXTRA_ARRAY),
)


class _ParserSpy:
    """Counts calls to the immutable O2 parser while the matrix runs."""

    def __init__(self):
        self.calls: list = []
        self._original = o2.parse_run_object

    def __enter__(self):
        def recorder(text):
            self.calls.append({"chars": len(text or ""),
                               "digest": p0.digest_bytes(
                                   str(text).encode("utf-8"))})
            return self._original(text)
        o2.parse_run_object = recorder
        return self

    def __exit__(self, *exc):
        o2.parse_run_object = self._original
        return False


def strict_shape_matrix() -> dict:
    """Every authorized shape accepted, every other shape refused before the
    O2 parser, with the parser-call count captured per case."""
    authorized = []
    refused = []
    with _ParserSpy() as spy:
        for case, shape, value in AUTHORIZED_CASES:
            text = json.dumps(value, sort_keys=True)
            before = len(spy.calls)
            classification = gate.classify_strict_receipt(text)
            parsed = gate.parse_strict_receipt(text)
            authorized.append({
                "case": case, "expected_shape": shape,
                "classified_shape": classification["shape"],
                "accepted": bool(classification["accepted"]),
                "run_id": parsed["run"]["id"],
                "parse_run_object_calls": len(spy.calls) - before,
            })
        for entry in REFUSED_CASES:
            case, value = entry[0], entry[1]
            expected_reason = entry[2]
            raw = entry[3] if len(entry) > 3 else None
            text = raw if raw is not None else json.dumps(value, sort_keys=True)
            before = len(spy.calls)
            classification = gate.classify_strict_receipt(text)
            raised = None
            try:
                gate.parse_strict_receipt(text)
                error = None
            except gate.StrictReceiptRefused as exc:
                error = {"type": type(exc).__name__, "code": exc.code,
                         "reason": exc.details.get("reason"),
                         "is_receipt_ambiguous": isinstance(
                             exc, o2.ReceiptAmbiguousError)}
            raised = error
            refused.append({
                "case": case,
                "accepted": bool(classification["accepted"]),
                "classified_shape": classification["shape"],
                "reason": classification["reason"],
                "expected_reason": expected_reason,
                "typed_stop": raised,
                "parse_run_object_calls": len(spy.calls) - before,
            })
    shape_acceptance = {
        shape: all(row["accepted"] and row["classified_shape"] == shape
                   and row["parse_run_object_calls"] == 1
                   for row in authorized if row["expected_shape"] == shape)
        for shape in gate.AUTHORIZED_SHAPES
    }
    run_wrapper = next(row for row in refused if row["case"] == "run_wrapper")
    all_rejected = all(
        (not row["accepted"])
        and row["reason"] == row["expected_reason"]
        and row["parse_run_object_calls"] == 0
        and row["typed_stop"] is not None
        and row["typed_stop"]["is_receipt_ambiguous"]
        for row in refused)
    duplicate_rows = [row for row in refused
                      if row["expected_reason"] == gate.R_DUPLICATE_KEY]
    duplicates_rejected = bool(duplicate_rows) and all(
        (not row["accepted"]) and row["reason"] == gate.R_DUPLICATE_KEY
        and row["parse_run_object_calls"] == 0
        and row["typed_stop"] is not None
        and row["typed_stop"]["is_receipt_ambiguous"]
        for row in duplicate_rows)
    constant_rows = [row for row in refused
                     if row["expected_reason"] == gate.R_NON_JSON_CONSTANT]
    constants_rejected = bool(constant_rows) and all(
        (not row["accepted"]) and row["reason"] == gate.R_NON_JSON_CONSTANT
        and row["parse_run_object_calls"] == 0
        and row["typed_stop"] is not None
        and row["typed_stop"]["is_receipt_ambiguous"]
        for row in constant_rows)
    return {
        "kind": "u12_p0r_strict_receipt_shape_matrix",
        "schema_version": SCHEMA_VERSION,
        "gate_version": gate.GATE_VERSION,
        "authorized_shapes": list(gate.AUTHORIZED_SHAPES),
        "authorized_cases": authorized,
        "refused_cases": refused,
        "shape_acceptance": shape_acceptance,
        "authorized_receipt_shapes_accepted":
            f"{sum(1 for ok in shape_acceptance.values() if ok)}/"
            f"{len(shape_acceptance)}",
        "authorized_cases_accepted":
            f"{sum(1 for row in authorized if row['accepted'])}/"
            f"{len(authorized)}",
        "run_wrapper_shape_rejected": (
            not run_wrapper["accepted"] and run_wrapper["typed_stop"] is not None
            and run_wrapper["parse_run_object_calls"] == 0),
        "all_unauthorized_or_ambiguous_shapes_rejected": all_rejected,
        "refused_case_count": len(refused),
        "duplicate_key_policy":
            "any repeated JSON object key at any nesting level fails closed "
            "during decoding, before classification and the O2 parser",
        "duplicate_key_cases": [row["case"] for row in duplicate_rows],
        "duplicate_key_cases_rejected":
            f"{sum(1 for row in duplicate_rows if not row['accepted'])}/"
            f"{len(duplicate_rows)}",
        "duplicate_keys_including_same_value_rejected": duplicates_rejected,
        "duplicate_key_parser_calls_on_rejection": sum(
            row["parse_run_object_calls"] for row in duplicate_rows),
        "non_json_constant_policy":
            "unquoted NaN, Infinity and -Infinity are not JSON tokens and "
            "fail closed during decoding at every position and nesting "
            "level, before classification and the O2 parser; quoted "
            "occurrences remain ordinary strings",
        "non_json_constant_cases": [row["case"] for row in constant_rows],
        "non_json_constant_cases_rejected":
            f"{sum(1 for row in constant_rows if not row['accepted'])}/"
            f"{len(constant_rows)}",
        "non_json_constants_rejected": constants_rejected,
        "non_json_constant_parser_calls_on_rejection": sum(
            row["parse_run_object_calls"] for row in constant_rows),
        "permissive_parser_invoked_on_refusal": sum(
            row["parse_run_object_calls"] for row in refused),
        "permissive_parser_invoked_only_after_classification": all(
            row["parse_run_object_calls"] == 1 for row in authorized),
        "correlation_requires_trusted_listing": True,
        "receipt_alone_counts_as_delivery": False,
        "live_triggers_issued": 0,
        "ok": all(shape_acceptance.values()) and all_rejected
              and run_wrapper["parse_run_object_calls"] == 0
              and duplicates_rejected and constants_rejected,
    }


def strict_gate_evidence(root=ROOT) -> dict:
    descriptor = gate.gate_descriptor(Path(root) / STRICT_GATE_MODULE)
    proof = gate.wiring_proof(module_path=Path(root) / STRICT_GATE_MODULE)
    matrix = strict_shape_matrix()
    evidence = {
        "kind": "u12_p0r_strict_receipt_gate_evidence",
        "schema_version": SCHEMA_VERSION,
        "task": TASK_REF,
        "authorization": dict(AUTHORITY),
        "gate": descriptor,
        "shape_matrix": matrix,
        "wiring_proof": proof,
        "r0_path": {
            "receipt_entrypoint": gate.RECEIPT_ENTRYPOINT,
            "orchestrator": "tools.u12_strict_receipt.CanaryOrchestrator",
            "permissive_entrypoint": gate.PERMISSIVE_ENTRYPOINT,
            "permissive_entrypoint_reachable_in_r0_path": False,
            "assignment_trigger_refused": True,
            "bypass_or_fallback": False,
            "o2_parser_calls_on_refused_shape": 0,
        },
        "trigger_route_provenance": {
            "finding": "U12-P0-F3",
            "route_source": "issuing_transaction_and_ledger",
            "ledger_fields": [
                "dispatch_intent TRIGGER_ISSUING transition "
                "fields.selected_trigger",
                "trigger_receipt event data.selected_trigger",
            ],
            "run_attribution_used_for_route": False,
            "attribution_observation": (
                "U12-P0 run-correlation-capture.json records the single "
                "authorized execution run reported by the platform as "
                "issue_assignment/delegation while the route was issue rerun; "
                "run attribution is therefore not route proof"),
            "correlation_uses": (
                "identity diff against the trusted untruncated "
                "`issue runs <target> --output json` listing"),
        },
        "history_boundary": {
            "o2_parser_modified": False,
            "o2_parser_pin": p0.PIN_FILES_LF["tools/chandoff_intent.py"],
            "o2_report_modified": False,
            "predecessor_history_rewritten": False,
        },
        "f4_duplicate_key_boundary": {
            "finding": "U12-P0R-F4",
            "decoder": "_decode_strict_json with "
                       "object_pairs_hook=_reject_duplicate_keys",
            "scope": "every JSON object at every nesting level (observable "
                     "fields, runs wrapper, run rows, nested extra-field "
                     "objects and arrays)",
            "same_valued_duplicates_rejected": True,
            "enforced_during_decoding": True,
            "enforced_before_classification": True,
            "enforced_before_o2_parser": True,
        },
        "f5_non_json_constant_boundary": {
            "finding": "U12-P0R-F5",
            "decoder": "_decode_strict_json with "
                       "parse_constant=_reject_non_json_constant",
            "constants": ["NaN", "Infinity", "-Infinity"],
            "scope": "every position at every nesting level (top level, "
                     "direct run extras, run rows, wrapper rows, nested "
                     "extra-field objects and arrays, `runs` value)",
            "quoted_constant_words_remain_legal": True,
            "finite_json_numbers_unchanged": True,
            "enforced_during_decoding": True,
            "enforced_before_classification": True,
            "enforced_before_o2_parser": True,
        },
    }
    evidence["evidence_digest"] = p0.digest(
        {k: v for k, v in evidence.items() if k != "evidence_digest"})
    return evidence


def _git_blob(root, rev: str, path: str, run) -> bytes | None:
    proc = run(["git", "-C", str(root), "cat-file", "blob", f"{rev}:{path}"],
               capture_output=True)
    if proc.returncode != 0:
        return None
    return bytes(proc.stdout or b"")


def _revision_blob_check(root, run, commit: str, expected_manifest: str,
                         expected_gate: str) -> dict:
    manifest_raw = _git_blob(root, commit, PRIOR_P0R_MANIFEST, run)
    manifest_digest = None
    if manifest_raw is not None:
        try:
            doc = json.loads(manifest_raw.decode("utf-8"))
            manifest_digest = p0.digest(
                {k: v for k, v in doc.items() if k != "manifest_digest"})
        except (ValueError, UnicodeDecodeError):
            manifest_digest = None
    gate_raw = _git_blob(root, commit, STRICT_GATE_MODULE, run)
    gate_digest = None if gate_raw is None else "sha256:" + hashlib.sha256(
        gate_raw.replace(b"\r\n", b"\n")).hexdigest()
    return {
        "manifest": {
            "path": PRIOR_P0R_MANIFEST,
            "commit": commit,
            "actual": manifest_digest,
            "expected": expected_manifest,
            "match": manifest_digest == expected_manifest,
        },
        "gate": {
            "module": STRICT_GATE_MODULE,
            "commit": commit,
            "actual": gate_digest,
            "expected": expected_gate,
            "match": gate_digest == expected_gate,
        },
    }


def _prior_p0r_checks(root, run) -> dict:
    prior = _revision_blob_check(root, run, PRIOR_COMMIT,
                                 PRIOR_P0R_MANIFEST_DIGEST,
                                 PRIOR_P0R_GATE_SHA256_LF)
    lineage = _revision_blob_check(root, run, LINEAGE_COMMIT,
                                   LINEAGE_P0R_MANIFEST_DIGEST,
                                   LINEAGE_P0R_GATE_SHA256_LF)
    return {
        "prior_p0r_manifest": prior["manifest"],
        "prior_p0r_gate": prior["gate"],
        "lineage_p0r_manifest": lineage["manifest"],
        "lineage_p0r_gate": lineage["gate"],
    }


def prior_artifacts_check(root=ROOT, *, run=subprocess.run) -> dict:
    root = Path(root)
    prior_plan_path = root / PRIOR_PLAN
    prior_manifest_path = root / PRIOR_MANIFEST
    prior_plan_digest = p0.digest_file(prior_plan_path)
    with open(prior_manifest_path, encoding="utf-8") as handle:
        prior_manifest = json.load(handle)
    checks = {
        "prior_plan": {
            "path": PRIOR_PLAN,
            "actual": prior_plan_digest,
            "expected": PRIOR_PLAN_DIGEST,
            "match": prior_plan_digest == PRIOR_PLAN_DIGEST,
        },
        "prior_manifest": {
            "path": PRIOR_MANIFEST,
            "actual": prior_manifest.get("manifest_digest"),
            "expected": PRIOR_MANIFEST_DIGEST,
            "match": prior_manifest.get("manifest_digest")
            == PRIOR_MANIFEST_DIGEST,
        },
        "prior_manifest_plan_row": {
            "actual": prior_manifest.get("evidence_files", {}).get(
                "proposed-r0-canary-plan.json"),
            "expected": PRIOR_PLAN_DIGEST,
            "match": prior_manifest.get("evidence_files", {}).get(
                "proposed-r0-canary-plan.json") == PRIOR_PLAN_DIGEST,
        },
    }
    checks.update(_prior_p0r_checks(root, run))
    return {
        "kind": "u12_p0r_prior_artifact_check",
        "schema_version": SCHEMA_VERSION,
        "checks": checks,
        "all_match": all(row["match"] for row in checks.values()),
    }


def pin_evidence(root=ROOT, *, run=subprocess.run) -> dict:
    pins = p0.verify_pins(Path(root), run=run)
    row = pins["checks"]["o2_report_test_row"]
    return {
        "kind": "u12_p0r_u11_o2_pin_verification",
        "schema_version": SCHEMA_VERSION,
        "base_commit": BASE_COMMIT,
        "checks": pins["checks"],
        "accepted_inputs_all_match": pins["accepted_inputs_all_match"],
        "documentation_discrepancies": pins["documentation_discrepancies"],
        "o2_report_digest_errata": {
            "finding": "U12-P0-F1",
            "path": row["path"],
            "recorded_in_o2_report": row["recorded_in_o2_report"],
            "correct_lf_digest": row["actual_lf"],
            "match": row["match"],
            "state": "accepted_forward_errata",
        },
    }


def production_ledger_evidence(ledger=PRODUCTION_LEDGER_PATH) -> dict:
    ledger = Path(ledger)
    integrity = p0.ledger_integrity(ledger)
    tip_unchanged = (integrity["tip_digest"] == PRODUCTION_LEDGER_TIP
                     and integrity["bytes"] == PRODUCTION_LEDGER_BYTES)
    return {
        "kind": "u12_p0r_production_ledger_readonly_integrity",
        "schema_version": SCHEMA_VERSION,
        "path": str(ledger),
        "read_only": True,
        "exists": integrity["exists"],
        "bytes": integrity["bytes"],
        "accepted_bytes": PRODUCTION_LEDGER_BYTES,
        "tip_digest": integrity["tip_digest"],
        "accepted_tip_digest": PRODUCTION_LEDGER_TIP,
        "tip_unchanged": tip_unchanged,
        "total_records": integrity["total_records"],
        "intent_records": integrity["intent_records"],
        "ignored_records": integrity["ignored_records"],
        "partial_or_corrupt_records": integrity["partial_or_corrupt_records"],
        "audit_ok": integrity["audit_ok"],
        "legacy_reader_ok": integrity["legacy_reader_ok"],
        "dispatch_intents_written": integrity["intent_records"],
        "live_triggers_issued": 0,
    }


def _git_blob_digest(root, rev: str, path: str, run) -> str:
    proc = run(["git", "-C", str(root), "cat-file", "blob", f"{rev}:{path}"],
               capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git cat-file failed for {rev}:{path}: "
            f"{(proc.stderr or b'').decode('utf-8', 'replace')[:160]}")
    data = bytes(proc.stdout or b"").replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def errata_payload(root=ROOT, *, run=subprocess.run) -> dict:
    root = Path(root)
    blobs = {rev: _git_blob_digest(root, rev, O2_TEST_PATH, run)
             for rev in ERRATA_COMMITS}
    distinct = sorted(set(blobs.values()))
    observed = distinct[0] if len(distinct) == 1 else None
    worktree = p0.digest_file(root / O2_TEST_PATH, normalize_lf=True)
    return {
        "kind": "o2_report_digest_errata",
        "schema_version": SCHEMA_VERSION,
        "task": TASK_REF,
        "finding": "U12-P0-F1",
        "status": "ACCEPTED_FORWARD_ERRATA",
        "authority": {
            "human_approval": "01a08e93-0a1a-7074-9dda-912d1237bf56",
            "lead_disposition":
                "01a08ebc-a0e5-7c7f-a150-f917e2cd447d: "
                "ACCEPTED_NONBLOCKING_DOCUMENTATION_ERRATA",
        },
        "subject": {
            "report": ERRATA_REPORT,
            "path": O2_TEST_PATH,
            "recorded_in_o2_report": O2_TEST_RECORDED,
            "correct_lf_digest": O2_TEST_CORRECT_LF,
        },
        "defect": {
            "nature": "documentation only; the O2 report's digest row for its "
                      "own new test file is not reproducible from the "
                      "committed blob",
            "byte_drift": False,
        },
        "verification": {
            "commits_checked": list(ERRATA_COMMITS),
            "blob_digests": blobs,
            "blobs_identical_at_all_commits": len(distinct) == 1,
            "observed_lf_digest": observed,
            "matches_correct_lf_digest": observed == O2_TEST_CORRECT_LF,
            "recorded_value_reproducible_from_any_commit":
                O2_TEST_RECORDED in distinct,
            "worktree_lf_digest": worktree,
            "worktree_matches_correct": worktree == O2_TEST_CORRECT_LF,
        },
        "remediation": {
            "forward_only": True,
            "o2_report_edited": False,
            "o2_or_predecessor_history_rewritten": False,
            "superseding_value_for_future_verification": O2_TEST_CORRECT_LF,
        },
        "ok": (len(distinct) == 1 and observed == O2_TEST_CORRECT_LF
               and O2_TEST_RECORDED not in distinct
               and worktree == O2_TEST_CORRECT_LF),
    }


def canary_plan(descriptor: dict, prior_check: dict) -> dict:
    return {
        "kind": "u12_p0r_proposed_r0_canary_plan",
        "schema_version": SCHEMA_VERSION,
        "task": TASK_REF,
        "status": "proposal_only_no_runs_created",
        "supersedes": {
            "path": PRIOR_PLAN,
            "digest": PRIOR_PLAN_DIGEST,
            "verified_match": prior_check["checks"]["prior_plan"]["match"],
            "scope": "supersedes the prior R0 canary plan receipt entrypoint "
                     "only; target shape, sole trigger, correlation and "
                     "rollback boundaries are carried forward",
        },
        "authorization_required": (
            "Engineering Lead (01) explicit R0 activation; Human U12 approval "
            "already recorded"),
        "objective": (
            "Prove the first live O2 dispatch through the exact production "
            "ledger with the strict receipt gate as the only receipt "
            "entrypoint: one real trigger recorded before the native call, "
            "exactly one correlated run, zero duplicate/ambiguous runs, zero "
            "R1/R2 activation."),
        "strict_receipt_gate": {
            "module": STRICT_GATE_MODULE,
            "version": descriptor["version"],
            "sha256_lf": descriptor["sha256_lf"],
            "receipt_entrypoint": descriptor["receipt_entrypoint"],
            "orchestrator": descriptor["orchestrator"],
            "authorized_shapes": descriptor["authorized_shapes"],
            "permissive_entrypoint": descriptor["permissive_entrypoint"],
            "permissive_entrypoint_reachable_in_r0_path": False,
            "bypass_or_fallback": False,
            "duplicate_key_rejected": True,
            "duplicate_key_scope": descriptor["duplicate_key_scope"],
            "same_valued_duplicate_keys_rejected": True,
            "non_json_constant_rejected": True,
            "non_json_constant_scope": descriptor["non_json_constant_scope"],
            "quoted_constant_words_remain_legal": True,
            "evidence": "strict-receipt-gate-evidence.json",
        },
        "target_issue_shape": {
            "type": "new dedicated R0 canary issue under parent YZT-66",
            "owner_role": "software-engineer (04) as sole producer/executor",
            "problem_class": "bounded local R0 task with no product behavior, "
                             "no public API/schema change, no review/QA route",
            "required_sections": [
                "Goal", "Authoritative References", "Preconditions",
                "Exact Scope", "Deliverables", "Stop Conditions",
                "Review Level R0",
            ],
            "pretrigger_status": "backlog",
            "pretrigger_assignee":
                "04 Software Engineer (fa7d16a7-2dae-4994-80b8-7435b3fcca47)",
            "forbidden_pretrigger_states": [
                "in_progress", "in_review", "done", "cancelled"],
        },
        "expected_package": {
            "kind": "CONTEXT_HANDOFF_RECORD v1 READY on the target issue",
            "package_id": "CTX-software-engineer-<fresh compute>",
            "artifact_dependency_digest":
                "sha256 of the superseding U12-P0R readiness manifest + exact "
                "U11 pins",
            "publication": (
                "non-trigger /note comment on the target issue; verify zero "
                "target runs after publication"),
        },
        "preconditions": {
            "production_ledger_tip_digest":
                "read and recorded immediately before the trigger",
            "production_ledger_integrity": "0 partial/corrupt records, audit ok",
            "pretrigger_run_set_digest":
                "canonical digest of the trusted full issue-run listing "
                "immediately before the trigger",
            "unexpected_active_runs": 0,
            "sibling_active_runs":
                "advisory snapshot only; never a completeness proof",
            "ledger_lock":
                "same-host OS lock held by the issuing reconciler for the "
                "transaction",
        },
        "sole_trigger": {
            "route": "issue rerun <target-issue-id> --output json",
            "count": 1,
            "write_ahead": (
                "TRIGGER_ISSUING transition persisted and fsynced in the "
                "production ledger before the native call"),
            "route_provenance": {
                "source": "issuing_transaction_and_ledger",
                "ledger_fields": [
                    "dispatch_intent TRIGGER_ISSUING transition "
                    "fields.selected_trigger",
                    "trigger_receipt event data.selected_trigger",
                ],
                "run_attribution_text_used_for_route": False,
                "finding": "U12-P0-F3",
            },
            "forbidden": [
                "issue assign as trigger",
                "mention trigger",
                "status promotion trigger",
                "second trigger of any kind",
                "automatic retry after any ambiguity",
            ],
        },
        "run_correlation_rule": {
            "receipt": (
                "strict gate only: direct run object, one-item run list, or "
                "{runs:[one]} with observable contract fields (id, issue_id, "
                "agent_id, status); `{run:{...}}`, competing wrapper keys, "
                "empty/multi lists, missing/blank fields, any repeated JSON "
                "object key at any nesting level (same-valued or not), any "
                "unquoted NaN/Infinity/-Infinity token at any position and "
                "every other shape fail closed before the O2 parser"),
            "receipt_entrypoint": descriptor["receipt_entrypoint"],
            "listing": (
                "trusted untruncated full `issue runs <target> --output json` "
                "execution history; any truncation or ambiguity means manual "
                "stop"),
            "require": (
                "exactly one new run with "
                "agent_id=fa7d16a7-2dae-4994-80b8-7435b3fcca47 and "
                "issue_id=target; zero new, multiple new, or a wrong-target "
                "run fails closed"),
            "on_ambiguity": (
                "typed stop (TRIGGER_AMBIGUOUS); manual Lead/Human decision; "
                "no retry, no re-route, no status toggle"),
        },
        "rollback_stop_rule": {
            "trigger_ambiguous":
                "stop; persist typed stop; escalate to Lead; never auto-retry",
            "correlated_run_failed":
                "record execution_recovery against the correlated run; no "
                "redispatch",
            "ledger_repair": (
                "only from the immutable timestamped backup into a separate "
                "validation path, only with Lead authorization; never "
                "overwrite the production ledger in place without a "
                "pre-repair immutable backup"),
            "canary_failure":
                "halt R1/R2 transitions; keep all records; escalate to Lead",
            "rollback_scope": (
                "disable creation/reconciliation of new intent records; retain "
                "append-only history; route handoffs back to accepted U06/U08 "
                "commands per O2 rollback plan"),
        },
        "explicit_non_goals": [
            "no R1 Delivery Review or R2 QA activation",
            "no live 05/06 activation",
            "no O3, daemon, scheduler or autonomous wake",
            "no platform API/database/queue change",
            "no merge",
        ],
    }


def completion_evidence(matrix: dict, ledger: dict, pins: dict,
                        errata: dict, prior_check: dict, source: dict) -> dict:
    return {
        "authorized_receipt_shapes_accepted":
            matrix["authorized_receipt_shapes_accepted"],
        "run_wrapper_shape_rejected": matrix["run_wrapper_shape_rejected"],
        "all_unauthorized_or_ambiguous_shapes_rejected":
            matrix["all_unauthorized_or_ambiguous_shapes_rejected"],
        "r0_path_uses_strict_gate_only": bool(source["wiring_proof"]["ok"]),
        "o2_parser_or_history_modified": False,
        "trigger_route_derived_from_attribution_text": False,
        "production_ledger_tip_unchanged": ledger["tip_unchanged"],
        "live_r0_runs_created": 0,
        "live_05_or_06_activation": 0,
        "canonical_or_product_writes": 0,
        "o3_or_autonomous_wake_added": False,
        "accepted_inputs_all_match": pins["accepted_inputs_all_match"],
        "prior_artifacts_all_match": prior_check["all_match"],
        "o2_report_digest_errata_verified": errata["ok"],
        "duplicate_key_cases_rejected": matrix["duplicate_key_cases_rejected"],
        "duplicate_keys_including_same_value_rejected":
            matrix["duplicate_keys_including_same_value_rejected"],
        "duplicate_key_parser_calls_on_rejection":
            matrix["duplicate_key_parser_calls_on_rejection"],
        "non_json_constant_cases_rejected":
            matrix["non_json_constant_cases_rejected"],
        "non_json_constants_rejected": matrix["non_json_constants_rejected"],
        "non_json_constant_parser_calls_on_rejection":
            matrix["non_json_constant_parser_calls_on_rejection"],
        "f4_disposition": "RESOLVED_BY_STRICT_DUPLICATE_KEY_DECODER",
        "f5_disposition": "RESOLVED_BY_STRICT_NON_JSON_CONSTANT_GUARD",
        "r0_canary_authorized": False,
    }


def assemble_manifest(*, generated_at: str, gate_evidence: dict,
                      pins: dict, ledger: dict, errata: dict,
                      prior_check: dict, evidence_dir: Path) -> dict:
    evidence_files = {}
    for path in sorted(Path(evidence_dir).glob("*.json")):
        if path.name == "readiness-manifest.json":
            continue
        evidence_files[path.name] = p0.digest_file(path)
    manifest = {
        "kind": "u12_p0r_strict_receipt_readiness_manifest",
        "schema_version": SCHEMA_VERSION,
        "task": TASK_REF,
        "generated_at": generated_at,
        "branch": BRANCH,
        "base_commit": BASE_COMMIT,
        "authorization": dict(AUTHORITY),
        "supersedes": {
            "path": PRIOR_MANIFEST,
            "digest": PRIOR_MANIFEST_DIGEST,
            "verified_match":
                prior_check["checks"]["prior_manifest"]["match"],
            "superseded_scope":
                "prior receipt_contract_bounded / R0 readiness conclusion "
                "only",
            "retained_as_accepted_history": [
                "production ledger path and deployment",
                "ACL least-privilege evidence",
                "backup/restore evidence",
                "absolute-root capability 6/6",
                "path validation",
                "U11/O2 pins",
            ],
            "prior_u12_p0r_revision": {
                "path": PRIOR_P0R_MANIFEST,
                "commit": PRIOR_COMMIT,
                "manifest_digest": PRIOR_P0R_MANIFEST_DIGEST,
                "strict_gate_sha256_lf": PRIOR_P0R_GATE_SHA256_LF,
                "verified_manifest_match":
                    prior_check["checks"]["prior_p0r_manifest"]["match"],
                "verified_gate_match":
                    prior_check["checks"]["prior_p0r_gate"]["match"],
                "superseded_scope":
                    "prior U12-P0R/1.1 F4-only readiness conclusion "
                    "(rejected as U12_P0R_CHANGES_REQUIRED for U12-P0R-F5); "
                    "the F5 repair is forward-only and leaves those bytes in "
                    "history",
            },
            "prior_u12_p0r_lineage": {
                "path": PRIOR_P0R_MANIFEST,
                "commit": LINEAGE_COMMIT,
                "manifest_digest": LINEAGE_P0R_MANIFEST_DIGEST,
                "strict_gate_sha256_lf": LINEAGE_P0R_GATE_SHA256_LF,
                "verified_manifest_match":
                    prior_check["checks"]["lineage_p0r_manifest"]["match"],
                "verified_gate_match":
                    prior_check["checks"]["lineage_p0r_gate"]["match"],
                "superseded_scope":
                    "original U12-P0R/1.0 F2-only readiness conclusion "
                    "(rejected as U12_P0R_CHANGES_REQUIRED); retained in "
                    "history and byte-verified",
            },
        },
        "strict_receipt_gate": {
            "module": STRICT_GATE_MODULE,
            "version": gate_evidence["gate"]["version"],
            "sha256_lf": gate_evidence["gate"]["sha256_lf"],
            "receipt_entrypoint": gate_evidence["gate"]["receipt_entrypoint"],
            "permissive_entrypoint":
                gate_evidence["gate"]["permissive_entrypoint"],
            "permissive_entrypoint_reachable_in_r0_path": False,
            "no_bypass_no_fallback": True,
            "duplicate_key_rejected": True,
            "duplicate_key_scope":
                gate_evidence["gate"]["duplicate_key_scope"],
            "same_valued_duplicate_keys_rejected": True,
            "non_json_constant_rejected": True,
            "non_json_constant_scope":
                gate_evidence["gate"]["non_json_constant_scope"],
            "quoted_constant_words_remain_legal": True,
            "shape_matrix_ok": gate_evidence["shape_matrix"]["ok"],
            "wiring_proof_ok": gate_evidence["wiring_proof"]["ok"],
            "evidence_file": "strict-receipt-gate-evidence.json",
        },
        "production_ledger": {
            "path": ledger["path"],
            "tip_digest": ledger["tip_digest"],
            "accepted_tip_digest": ledger["accepted_tip_digest"],
            "tip_unchanged": ledger["tip_unchanged"],
            "bytes": ledger["bytes"],
            "partial_or_corrupt_records":
                ledger["partial_or_corrupt_records"],
            "intent_records": ledger["intent_records"],
            "audit_ok": ledger["audit_ok"],
            "legacy_reader_ok": ledger["legacy_reader_ok"],
            "read_only_check": True,
            "evidence_file": "production-ledger-readonly-integrity.json",
        },
        "u11_o2_pins": {
            "accepted_inputs_all_match": pins["accepted_inputs_all_match"],
            "documentation_discrepancies": pins["documentation_discrepancies"],
            "evidence_file": "pin-verification.json",
        },
        "o2_report_digest_errata": {
            "finding": "U12-P0-F1",
            "status": errata["status"],
            "path": errata["subject"]["path"],
            "recorded_in_o2_report": errata["subject"]["recorded_in_o2_report"],
            "correct_lf_digest": errata["subject"]["correct_lf_digest"],
            "history_rewritten": False,
            "evidence_file": "O2_REPORT_DIGEST_ERRATA.json",
            "ok": errata["ok"],
        },
        "findings": {
            "U12-P0-F1": "ACCEPTED_FORWARD_ERRATA (recorded, non-blocking)",
            "U12-P0-F2": "RESOLVED_BY_STRICT_RECEIPT_GATE",
            "U12-P0-F3": "BOUND_BY_ROUTE_PROVENANCE (ledger, not attribution)",
            "U12-P0R-F4": "RESOLVED_BY_STRICT_DUPLICATE_KEY_DECODER "
                          "(every nesting level, including same-valued)",
            "U12-P0R-F5": "RESOLVED_BY_STRICT_NON_JSON_CONSTANT_GUARD "
                          "(unquoted NaN/Infinity/-Infinity refused at every "
                          "position during decoding)",
        },
        "r0_canary_plan": {
            "path": "adapters/multica/u12-p0r/proposed-r0-canary-plan.json",
            "evidence_file": "proposed-r0-canary-plan.json",
            "r0_canary_authorized": False,
        },
        "completion_evidence": completion_evidence(
            gate_evidence["shape_matrix"], ledger, pins, errata, prior_check,
            gate_evidence),
        "r0_canary_authorized": False,
        "evidence_files": evidence_files,
    }
    manifest["manifest_digest"] = p0.digest(
        {k: v for k, v in manifest.items() if k != "manifest_digest"})
    return manifest


def generate(evidence_dir: Path, *, generated_at: str | None = None,
             root=ROOT, run=subprocess.run, ledger=PRODUCTION_LEDGER_PATH) -> dict:
    evidence_dir = Path(evidence_dir)
    root = Path(root)
    generated_at = generated_at or p0.utc_now()
    prior_check = prior_artifacts_check(root, run=run)
    if not prior_check["all_match"]:
        raise RuntimeError(
            "prior U12-P0 plan/manifest digests drifted; fail closed")
    pins = pin_evidence(root, run=run)
    if not pins["accepted_inputs_all_match"]:
        raise RuntimeError("accepted U11/O2 pins drifted; fail closed")
    errata = errata_payload(root, run=run)
    if not errata["ok"]:
        raise RuntimeError("O2 report digest errata verification failed")
    ledger_evidence = production_ledger_evidence(ledger)
    if not ledger_evidence["tip_unchanged"]:
        raise RuntimeError("production ledger tip drifted; fail closed")
    gate_evidence = strict_gate_evidence(root)
    if not gate_evidence["shape_matrix"]["ok"] \
            or not gate_evidence["wiring_proof"]["ok"]:
        raise RuntimeError("strict receipt gate evidence failed")
    plan = canary_plan(gate_evidence["gate"], prior_check)

    written = {}
    written["strict-receipt-gate-evidence.json"] = p0.write_evidence(
        evidence_dir, "strict-receipt-gate-evidence.json", gate_evidence)
    written["pin-verification.json"] = p0.write_evidence(
        evidence_dir, "pin-verification.json", pins)
    written["production-ledger-readonly-integrity.json"] = p0.write_evidence(
        evidence_dir, "production-ledger-readonly-integrity.json",
        ledger_evidence)
    written["O2_REPORT_DIGEST_ERRATA.json"] = p0.write_evidence(
        evidence_dir, "O2_REPORT_DIGEST_ERRATA.json", errata)
    written["proposed-r0-canary-plan.json"] = p0.write_evidence(
        evidence_dir, "proposed-r0-canary-plan.json", plan)
    manifest = assemble_manifest(
        generated_at=generated_at, gate_evidence=gate_evidence, pins=pins,
        ledger=ledger_evidence, errata=errata,
        prior_check=prior_check, evidence_dir=evidence_dir)
    written["readiness-manifest.json"] = p0.write_evidence(
        evidence_dir, "readiness-manifest.json", manifest)
    summary = {
        "verdict": "U12_P0R_READY_FOR_LEAD_REVIEW",
        "evidence_dir": str(evidence_dir),
        "manifest_digest": manifest["manifest_digest"],
        "strict_gate_sha256_lf": gate_evidence["gate"]["sha256_lf"],
        "authorized_receipt_shapes_accepted":
            gate_evidence["shape_matrix"]["authorized_receipt_shapes_accepted"],
        "production_ledger_tip_unchanged": ledger_evidence["tip_unchanged"],
        "r0_canary_authorized": False,
        "evidence_files": written,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return summary


def check_bundle(evidence_dir: Path, *, root=ROOT,
                 run=subprocess.run) -> dict:
    """Regenerate with the committed timestamp and compare every byte."""
    evidence_dir = Path(evidence_dir)
    manifest_path = evidence_dir / "readiness-manifest.json"
    with open(manifest_path, encoding="utf-8") as handle:
        committed = json.load(handle)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        generate(tmp_dir, generated_at=committed["generated_at"], root=root,
                 run=run)
        comparisons = {}
        for name in BUNDLE_FILES:
            committed_bytes = (evidence_dir / name).read_bytes()
            regenerated_bytes = (tmp_dir / name).read_bytes()
            comparisons[name] = {
                "match": committed_bytes == regenerated_bytes,
                "committed_digest": p0.digest_bytes(committed_bytes),
                "regenerated_digest": p0.digest_bytes(regenerated_bytes),
            }
    result = {
        "kind": "u12_p0r_regeneration_check",
        "schema_version": SCHEMA_VERSION,
        "timestamp_used": committed["generated_at"],
        "files": comparisons,
        "all_match": all(row["match"] for row in comparisons.values()),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return result


def cmd_verify(args) -> int:
    root = Path(args.root)
    gate_evidence = strict_gate_evidence(root)
    pins = pin_evidence(root)
    ledger_evidence = production_ledger_evidence()
    errata = errata_payload(root)
    prior_check = prior_artifacts_check(root)
    result = {
        "kind": "u12_p0r_verify",
        "schema_version": SCHEMA_VERSION,
        "generated_at": p0.utc_now(),
        "strict_gate_ok": gate_evidence["shape_matrix"]["ok"]
                          and gate_evidence["wiring_proof"]["ok"],
        "strict_gate_sha256_lf": gate_evidence["gate"]["sha256_lf"],
        "authorized_receipt_shapes_accepted":
            gate_evidence["shape_matrix"]["authorized_receipt_shapes_accepted"],
        "run_wrapper_shape_rejected":
            gate_evidence["shape_matrix"]["run_wrapper_shape_rejected"],
        "non_json_constants_rejected":
            gate_evidence["shape_matrix"]["non_json_constants_rejected"],
        "accepted_inputs_all_match": pins["accepted_inputs_all_match"],
        "o2_report_digest_errata_ok": errata["ok"],
        "production_ledger_tip_unchanged": ledger_evidence["tip_unchanged"],
        "production_ledger_tip_digest": ledger_evidence["tip_digest"],
        "partial_or_corrupt_records":
            ledger_evidence["partial_or_corrupt_records"],
        "prior_artifacts_all_match": prior_check["all_match"],
        "r0_canary_authorized": False,
    }
    result["ok"] = (result["strict_gate_ok"]
                    and result["accepted_inputs_all_match"]
                    and result["o2_report_digest_errata_ok"]
                    and result["production_ledger_tip_unchanged"]
                    and result["partial_or_corrupt_records"] == 0
                    and result["prior_artifacts_all_match"])
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="U12-P0R strict receipt gate readiness evidence (YZT-82)")
    sub = parser.add_subparsers(dest="command", required=True)
    generate_parser = sub.add_parser("generate", help="write the evidence bundle")
    generate_parser.add_argument("--evidence-dir", required=True)
    generate_parser.add_argument("--generated-at", default=None)
    generate_parser.add_argument("--root", default=str(ROOT))
    check_parser = sub.add_parser(
        "check", help="regenerate committed bundle and compare bytes")
    check_parser.add_argument("--evidence-dir", required=True)
    check_parser.add_argument("--root", default=str(ROOT))
    verify_parser = sub.add_parser("verify", help="read-only live recheck")
    verify_parser.add_argument("--root", default=str(ROOT))
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate":
        generate(Path(args.evidence_dir), generated_at=args.generated_at,
                 root=Path(args.root))
        return 0
    if args.command == "check":
        result = check_bundle(Path(args.evidence_dir), root=Path(args.root))
        return 0 if result["all_match"] else 2
    return cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
