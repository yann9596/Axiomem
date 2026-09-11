#!/usr/bin/env python3
"""U12-P0R focused tests — canary-facing strict receipt gate (YZT-82).

Every case runs against temporary ledgers and fixture runners. Nothing here
calls the platform, writes the production ledger, or mutates any issue.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_intent as o2  # noqa: E402
import u12_strict_receipt as gate  # noqa: E402

TARGET_AGENT = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
ISSUE_ID = "01a08c2e-596c-78df-8865-d00d10fdad9c"
PARENT = "01a08921-207c-7a39-8a99-b431e75bed9f"
PACKAGE = "CTX-software-engineer-9f2c41d7b8e05a13"
ARTIFACT = "sha256:" + "a" * 64
NOTE = "01a08e36-3421-76a7-9ccc-ccb56634746b"
INTENT_ID = "DI-" + "1" * 16
RUN_ID = "01a08ddc-7698-7c18-943b-f354ee6ec8fb"
BASE_TIME = "2026-09-11T00:00:00Z"

RUN = {"id": RUN_ID, "issue_id": ISSUE_ID, "agent_id": TARGET_AGENT,
       "status": "queued"}

# U12-P0R-F4: raw receipts with repeated JSON object keys (json.dumps cannot
# emit them). The first two are the exact Lead reproductions.
DUP_OBSERVABLE_LAST_WINS = (
    '{"id":"first","id":"second","issue_id":"i","agent_id":"a",'
    '"status":"queued"}')
DUP_RUNS_EMPTY_OVERWRITTEN = (
    '{"runs":[],"runs":[{"id":"r","issue_id":"i","agent_id":"a",'
    '"status":"queued"}]}')
DUP_RUNS_EMPTY_OVERWRITTEN_VALID = (
    '{"runs":[],"runs":[{"id":"%s","issue_id":"%s","agent_id":"%s",'
    '"status":"queued"}]}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
DUP_OBSERVABLE_SAME_VALUE = (
    '{"id":"%s","id":"%s","issue_id":"%s","agent_id":"%s",'
    '"status":"queued"}' % (RUN_ID, RUN_ID, ISSUE_ID, TARGET_AGENT))
DUP_RUNS_SAME_VALUE = '{"runs":[],"runs":[]}'
DUP_NESTED_RUN_ROW_FIELD = (
    '{"runs":[{"id":"%s","issue_id":"%s","agent_id":"%s",'
    '"status":"queued","status":"queued"}]}'
    % (RUN_ID, ISSUE_ID, TARGET_AGENT))
DUP_NESTED_EXTRA_OBJECT = (
    '{"id":"%s","issue_id":"%s","agent_id":"%s","status":"queued",'
    '"extra":{"k":1,"k":2}}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
DUP_NESTED_EXTRA_ARRAY = (
    '{"id":"%s","issue_id":"%s","agent_id":"%s","status":"queued",'
    '"extra":[{"k":1,"k":2}]}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
DUP_LIST_ROW_FIELD = (
    '[{"id":"%s","issue_id":"%s","agent_id":"%s","status":"queued",'
    '"status":"queued"}]' % (RUN_ID, ISSUE_ID, TARGET_AGENT))

DUPLICATE_RECEIPTS = (
    DUP_OBSERVABLE_LAST_WINS,
    DUP_RUNS_EMPTY_OVERWRITTEN,
    DUP_RUNS_EMPTY_OVERWRITTEN_VALID,
    DUP_OBSERVABLE_SAME_VALUE,
    DUP_RUNS_SAME_VALUE,
    DUP_NESTED_RUN_ROW_FIELD,
    DUP_NESTED_EXTRA_OBJECT,
    DUP_NESTED_EXTRA_ARRAY,
    DUP_LIST_ROW_FIELD,
)

# U12-P0R-F5: unquoted NaN/Infinity/-Infinity are not valid JSON numeric
# tokens. The first entry is the exact Lead reproduction. raw text again:
# json.dumps would not emit these tokens for a strict consumer.
NON_JSON_LEAD_REPRODUCTION = (
    '{"id":"r","issue_id":"i","agent_id":"a","status":"queued","extra":NaN}')
NON_JSON_TOP_LEVEL_NAN = "NaN"
NON_JSON_TOP_LEVEL_INFINITY = "Infinity"
NON_JSON_TOP_LEVEL_NEG_INFINITY = "-Infinity"
NON_JSON_TOP_LEVEL_LIST = "[NaN]"
NON_JSON_DIRECT_RUN_EXTRA_NAN = (
    '{"id": "%s", "issue_id": "%s", "agent_id": "%s", "status": "queued", '
    '"extra": NaN}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
NON_JSON_DIRECT_RUN_EXTRA_INFINITY = (
    '{"id": "%s", "issue_id": "%s", "agent_id": "%s", "status": "queued", '
    '"extra": Infinity}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
NON_JSON_DIRECT_RUN_EXTRA_NEG_INFINITY = (
    '{"id": "%s", "issue_id": "%s", "agent_id": "%s", "status": "queued", '
    '"extra": -Infinity}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
NON_JSON_RUN_LIST_ROW_EXTRA = (
    '[{"id": "%s", "issue_id": "%s", "agent_id": "%s", "status": "queued", '
    '"extra": NaN}]' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
NON_JSON_RUNS_WRAPPER_ROW_EXTRA = (
    '{"runs": [{"id": "%s", "issue_id": "%s", "agent_id": "%s", '
    '"status": "queued", "extra": -Infinity}]}'
    % (RUN_ID, ISSUE_ID, TARGET_AGENT))
NON_JSON_RUNS_VALUE = '{"runs": NaN}'
NON_JSON_NESTED_EXTRA_OBJECT = (
    '{"id": "%s", "issue_id": "%s", "agent_id": "%s", "status": "queued", '
    '"extra": {"k": Infinity}}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))
NON_JSON_NESTED_EXTRA_ARRAY = (
    '{"id": "%s", "issue_id": "%s", "agent_id": "%s", "status": "queued", '
    '"extra": [NaN]}' % (RUN_ID, ISSUE_ID, TARGET_AGENT))

NON_JSON_CONSTANT_RECEIPTS = (
    NON_JSON_LEAD_REPRODUCTION,
    NON_JSON_TOP_LEVEL_NAN,
    NON_JSON_TOP_LEVEL_INFINITY,
    NON_JSON_TOP_LEVEL_NEG_INFINITY,
    NON_JSON_TOP_LEVEL_LIST,
    NON_JSON_DIRECT_RUN_EXTRA_NAN,
    NON_JSON_DIRECT_RUN_EXTRA_INFINITY,
    NON_JSON_DIRECT_RUN_EXTRA_NEG_INFINITY,
    NON_JSON_RUN_LIST_ROW_EXTRA,
    NON_JSON_RUNS_WRAPPER_ROW_EXTRA,
    NON_JSON_RUNS_VALUE,
    NON_JSON_NESTED_EXTRA_OBJECT,
    NON_JSON_NESTED_EXTRA_ARRAY,
)


def run_json(**over) -> dict:
    payload = dict(RUN)
    payload.update(over)
    return payload


def text(value) -> str:
    return json.dumps(value, sort_keys=True)


class ParserSpy:
    def __init__(self):
        self.calls: list = []
        self._original = o2.parse_run_object

    def __enter__(self):
        def recorder(payload):
            self.calls.append(payload)
            return self._original(payload)
        o2.parse_run_object = recorder
        return self

    def __exit__(self, *exc):
        o2.parse_run_object = self._original
        return False


class StrictShapeTests(unittest.TestCase):
    def test_three_authorized_shapes_accepted(self):
        for shape, value in (
                (gate.SHAPE_RUN_OBJECT, RUN),
                (gate.SHAPE_RUN_LIST, [RUN]),
                (gate.SHAPE_RUNS_WRAPPER, {"runs": [RUN]})):
            parsed = gate.parse_strict_receipt(text(value))
            self.assertEqual(parsed["shape"], shape)
            self.assertEqual(parsed["run"]["id"], RUN_ID)

    def test_direct_run_object_extra_fields_ignored_after_classification(self):
        value = dict(RUN, extra="ignored", nested={"a": 1}, note=None)
        parsed = gate.parse_strict_receipt(text(value))
        self.assertEqual(parsed["shape"], gate.SHAPE_RUN_OBJECT)
        self.assertEqual(set(parsed["run"]), set(dispatch.RUN_CONTRACT_FIELDS))

    def test_run_wrapper_rejected_even_when_nested_run_valid(self):
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(text({"run": RUN}))
        self.assertEqual(caught.exception.code, "trigger_receipt_ambiguous")
        self.assertIsInstance(caught.exception, o2.ReceiptAmbiguousError)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_RUN_WRAPPER)

    def test_mixed_and_competing_wrapper_keys_rejected(self):
        cases = (
            ({"run": RUN, "runs": [RUN]}, gate.R_MIXED_WRAPPER),
            ({"runs": [RUN], "run": RUN}, gate.R_MIXED_WRAPPER),
            ({"runs": [RUN], "page": 1}, gate.R_COMPETING_KEY),
            ({"runs": [RUN], "data": RUN}, gate.R_COMPETING_KEY),
            ({"run": RUN, "id": "x"}, gate.R_RUN_WRAPPER),
        )
        for value, reason in cases:
            with self.subTest(value=value):
                with self.assertRaises(gate.StrictReceiptRefused) as caught:
                    gate.parse_strict_receipt(text(value))
                self.assertEqual(caught.exception.details.get("reason"), reason)

    def test_list_and_wrapper_arity_rejected(self):
        for value, reason in (
                ([], gate.R_RUN_LIST_ARITY),
                ([RUN, RUN], gate.R_RUN_LIST_ARITY),
                ({}, gate.R_MISSING_FIELD),
                ({"runs": []}, gate.R_RUNS_ARITY),
                ({"runs": [RUN, RUN]}, gate.R_RUNS_ARITY),
                ({"runs": RUN}, gate.R_RUNS_NOT_LIST),
                ([1], gate.R_ROW_NOT_OBJECT),
                ({"runs": [1]}, gate.R_ROW_NOT_OBJECT)):
            with self.subTest(value=value):
                with self.assertRaises(gate.StrictReceiptRefused) as caught:
                    gate.parse_strict_receipt(text(value))
                self.assertEqual(caught.exception.details.get("reason"), reason)

    def test_missing_blank_and_non_string_fields_rejected(self):
        for value in ({"id": "r", "issue_id": "i"},
                      run_json(status="   "),
                      run_json(id=42),
                      run_json(agent_id=None)):
            with self.subTest(value=value):
                with self.assertRaises(gate.StrictReceiptRefused) as caught:
                    gate.parse_strict_receipt(text(value))
                self.assertEqual(caught.exception.details.get("reason"),
                                 gate.R_MISSING_FIELD)

    def test_non_json_scalars_and_non_text_rejected(self):
        for payload in ("not json", "", "{", "\x00"):
            with self.subTest(payload=payload):
                with self.assertRaises(gate.StrictReceiptRefused) as caught:
                    gate.parse_strict_receipt(payload)
                self.assertEqual(caught.exception.details.get("reason"),
                                 gate.R_NOT_JSON)
        for value in (None, 7, True, "run", 3.14):
            with self.subTest(value=value):
                with self.assertRaises(gate.StrictReceiptRefused) as caught:
                    gate.parse_strict_receipt(text(value))
                self.assertEqual(caught.exception.details.get("reason"),
                                 gate.R_SCALAR_OR_NULL)
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(None)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_INPUT_NOT_TEXT)

    def test_classification_is_refusal_only_and_typed(self):
        result = gate.classify_strict_receipt(text({"run": RUN}))
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], gate.R_RUN_WRAPPER)
        self.assertEqual(result["shape"], "run_wrapper")


class DuplicateKeyTests(unittest.TestCase):
    """U12-P0R-F4: repeated JSON object keys fail closed during decoding."""

    def test_lead_reproductions_are_refused(self):
        for payload in (DUP_OBSERVABLE_LAST_WINS, DUP_RUNS_EMPTY_OVERWRITTEN):
            with self.subTest(payload=payload):
                result = gate.classify_strict_receipt(payload)
                self.assertFalse(result["accepted"])
                self.assertEqual(result["reason"], gate.R_DUPLICATE_KEY)
                self.assertIsNone(result["shape"])

    def test_duplicate_observable_field_refused(self):
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(DUP_OBSERVABLE_LAST_WINS)
        self.assertEqual(caught.exception.code, "trigger_receipt_ambiguous")
        self.assertIsInstance(caught.exception, o2.ReceiptAmbiguousError)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_DUPLICATE_KEY)

    def test_duplicate_runs_wrapper_refused(self):
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(DUP_RUNS_EMPTY_OVERWRITTEN)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_DUPLICATE_KEY)
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(DUP_RUNS_EMPTY_OVERWRITTEN_VALID)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_DUPLICATE_KEY)

    def test_same_valued_duplicates_refused(self):
        for payload in (DUP_OBSERVABLE_SAME_VALUE, DUP_RUNS_SAME_VALUE,
                        DUP_NESTED_RUN_ROW_FIELD,
                        DUP_NESTED_EXTRA_OBJECT, DUP_NESTED_EXTRA_ARRAY,
                        DUP_LIST_ROW_FIELD):
            with self.subTest(payload=payload):
                with self.assertRaises(gate.StrictReceiptRefused) as caught:
                    gate.parse_strict_receipt(payload)
                self.assertEqual(caught.exception.details.get("reason"),
                                 gate.R_DUPLICATE_KEY)

    def test_nested_duplicates_refused_at_every_level(self):
        for payload, duplicate in (
                (DUP_NESTED_RUN_ROW_FIELD, "status"),
                (DUP_LIST_ROW_FIELD, "status"),
                (DUP_NESTED_EXTRA_OBJECT, "k"),
                (DUP_NESTED_EXTRA_ARRAY, "k"),
                (DUP_OBSERVABLE_SAME_VALUE, "id"),
                (DUP_RUNS_SAME_VALUE, "runs")):
            with self.subTest(payload=payload):
                result = gate.classify_strict_receipt(payload)
                self.assertEqual(result["reason"], gate.R_DUPLICATE_KEY)
                self.assertIn(duplicate, result["duplicate_keys"])

    def test_duplicate_rejection_calls_no_o2_parser(self):
        with ParserSpy() as spy:
            for payload in DUPLICATE_RECEIPTS:
                with self.subTest(payload=payload):
                    with self.assertRaises(gate.StrictReceiptRefused):
                        gate.parse_strict_receipt(payload)
            self.assertEqual(spy.calls, [])

    def test_bytes_receipt_duplicates_refused(self):
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(DUP_OBSERVABLE_LAST_WINS.encode("utf-8"))
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_DUPLICATE_KEY)

    def test_authorized_shapes_without_duplicates_still_accepted(self):
        with ParserSpy() as spy:
            for shape, value in (
                    (gate.SHAPE_RUN_OBJECT, RUN),
                    (gate.SHAPE_RUN_LIST, [RUN]),
                    (gate.SHAPE_RUNS_WRAPPER, {"runs": [RUN]})):
                parsed = gate.parse_strict_receipt(text(value))
                self.assertEqual(parsed["shape"], shape)
                self.assertEqual(parsed["run"]["id"], RUN_ID)
        self.assertEqual(len(spy.calls), 3)


class NonJsonConstantTests(unittest.TestCase):
    """U12-P0R-F5: unquoted NaN/Infinity/-Infinity fail closed at decode."""

    def test_lead_reproduction_is_refused(self):
        result = gate.classify_strict_receipt(NON_JSON_LEAD_REPRODUCTION)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], gate.R_NON_JSON_CONSTANT)
        self.assertIsNone(result["shape"])
        self.assertEqual(result["constant_token"], "NaN")

    def test_constants_refused_at_every_position_and_nesting_level(self):
        tokens = {"NaN", "Infinity", "-Infinity"}
        for payload in NON_JSON_CONSTANT_RECEIPTS:
            with self.subTest(payload=payload):
                result = gate.classify_strict_receipt(payload)
                self.assertFalse(result["accepted"])
                self.assertEqual(result["reason"], gate.R_NON_JSON_CONSTANT)
                self.assertIsNone(result["shape"])
                self.assertIn(result["constant_token"], tokens)

    def test_constant_refusal_is_typed_ambiguous(self):
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(NON_JSON_DIRECT_RUN_EXTRA_NAN)
        self.assertEqual(caught.exception.code, "trigger_receipt_ambiguous")
        self.assertIsInstance(caught.exception, o2.ReceiptAmbiguousError)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_NON_JSON_CONSTANT)

    def test_constant_rejection_calls_no_o2_parser(self):
        with ParserSpy() as spy:
            for payload in NON_JSON_CONSTANT_RECEIPTS:
                with self.subTest(payload=payload):
                    with self.assertRaises(gate.StrictReceiptRefused):
                        gate.parse_strict_receipt(payload)
            self.assertEqual(spy.calls, [])

    def test_bytes_receipt_constant_refused(self):
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            gate.parse_strict_receipt(
                NON_JSON_RUNS_WRAPPER_ROW_EXTRA.encode("utf-8"))
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_NON_JSON_CONSTANT)

    def test_quoted_constant_words_and_finite_numbers_remain_legal(self):
        controls = (
            run_json(extra="NaN", note="-Infinity",
                     nested={"words": ["Infinity", "-Infinity", "NaN"],
                             "numbers": [0, 1, -2.5, 1e3]}),
            run_json(status="Infinity"),
        )
        with ParserSpy() as spy:
            for value in controls:
                with self.subTest(value=value):
                    parsed = gate.parse_strict_receipt(text(value))
                    self.assertEqual(parsed["shape"], gate.SHAPE_RUN_OBJECT)
                    self.assertEqual(parsed["run"]["id"], RUN_ID)
        self.assertEqual(len(spy.calls), len(controls))

    def test_authorized_shapes_without_constants_still_accepted(self):
        with ParserSpy() as spy:
            for shape, value in (
                    (gate.SHAPE_RUN_OBJECT, RUN),
                    (gate.SHAPE_RUN_LIST, [RUN]),
                    (gate.SHAPE_RUNS_WRAPPER, {"runs": [RUN]})):
                parsed = gate.parse_strict_receipt(text(value))
                self.assertEqual(parsed["shape"], shape)
                self.assertEqual(parsed["run"]["id"], RUN_ID)
        self.assertEqual(len(spy.calls), 3)

    def test_f4_duplicate_receipts_still_refused(self):
        for payload in DUPLICATE_RECEIPTS:
            with self.subTest(payload=payload):
                result = gate.classify_strict_receipt(payload)
                self.assertEqual(result["reason"], gate.R_DUPLICATE_KEY)


class ParserReachabilityTests(unittest.TestCase):
    def test_permissive_parser_never_called_for_refused_shapes(self):
        refused = ({"run": RUN}, {"run": RUN, "runs": [RUN]},
                   {"runs": [RUN], "page": 1}, [], [RUN, RUN],
                   {"runs": []}, {"runs": [RUN, RUN]}, {"id": "x"},
                   run_json(status=" "), None, 7, "not json")
        with ParserSpy() as spy:
            for value in refused:
                payload = value if isinstance(value, str) else text(value)
                with self.assertRaises(gate.StrictReceiptRefused):
                    gate.parse_strict_receipt(payload)
        self.assertEqual(spy.calls, [])

    def test_permissive_parser_called_once_only_after_classification(self):
        with ParserSpy() as spy:
            for value in (RUN, [RUN], {"runs": [RUN]}):
                parsed = gate.parse_strict_receipt(text(value))
                self.assertEqual(parsed["run"]["id"], RUN_ID)
        self.assertEqual(len(spy.calls), 3)

    def test_wiring_proof_green(self):
        proof = gate.wiring_proof()
        self.assertTrue(proof["ok"], proof)
        checks = proof["checks"]
        self.assertTrue(checks["classify_precedes_o2_parser"])
        self.assertTrue(checks["refusal_precedes_o2_parser"])
        self.assertTrue(checks["rerun_issue_uses_strict_gate"])
        self.assertTrue(checks["rerun_issue_never_calls_permissive_parser"])
        self.assertTrue(checks["assign_trigger_is_refused_without_parse"])
        self.assertTrue(checks["permissive_boundary_method_overridden"])
        self.assertTrue(checks["canary_orchestrator_replaces_boundary"])
        self.assertTrue(checks["duplicate_key_guard_present"])
        self.assertTrue(checks["non_json_constant_guard_present"])
        self.assertFalse(checks["permissive_entrypoint_reachable_in_r0_path"])
        self.assertEqual(checks["parse_run_object_call_sites"], 1)

    def test_gate_descriptor_binds_checked_out_bytes(self):
        descriptor = gate.gate_descriptor(TOOLS / "u12_strict_receipt.py")
        self.assertEqual(descriptor["module"], "tools/u12_strict_receipt.py")
        self.assertEqual(descriptor["version"], gate.GATE_VERSION)
        self.assertEqual(descriptor["receipt_entrypoint"],
                         gate.RECEIPT_ENTRYPOINT)
        self.assertFalse(descriptor["run_wrapper_authorized"])
        self.assertTrue(descriptor["non_json_constant_fails_closed"])
        self.assertEqual(descriptor["non_json_constant_scope"],
                         "every_position_at_every_nesting_level")
        self.assertTrue(descriptor["quoted_constant_words_remain_legal"])
        self.assertTrue(descriptor["sha256_lf"].startswith("sha256:"))


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = o2.DurableIntentStore(self.tmp / "shared-ledger.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def test_assign_trigger_refused_without_native_call(self):
        runner = o2.O2FixtureRunner([])
        boundary = gate.StrictReceiptBoundary(runner, self.store)
        with self.assertRaises(o2.IntentError):
            boundary.assign_trigger(ISSUE_ID, TARGET_AGENT)
        self.assertEqual(runner.issued, [])

    def test_rerun_uses_strict_gate_and_refuses_run_wrapper(self):
        runner = o2.O2FixtureRunner([
            {"match": ["issue", "rerun", ISSUE_ID],
             "stdout": json.dumps({"run": RUN}, sort_keys=True)},
        ])
        boundary = gate.StrictReceiptBoundary(runner, self.store)
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            boundary.rerun_issue(ISSUE_ID)
        self.assertEqual(caught.exception.details.get("reason"),
                         gate.R_RUN_WRAPPER)
        self.assertEqual(len(runner.issued), 1)

    def test_rerun_accepts_authorized_shape(self):
        runner = o2.O2FixtureRunner([
            {"match": ["issue", "rerun", ISSUE_ID],
             "stdout": json.dumps({"runs": [RUN]}, sort_keys=True)},
        ])
        boundary = gate.StrictReceiptBoundary(runner, self.store)
        receipt = boundary.rerun_issue(ISSUE_ID)
        self.assertEqual(receipt["outcome"], "confirmed")
        self.assertEqual(receipt["receipt_shape"], gate.SHAPE_RUNS_WRAPPER)
        self.assertEqual(receipt["run"]["id"], RUN_ID)

    def test_nonzero_exit_is_typed_refusal(self):
        runner = o2.O2FixtureRunner([
            {"match": ["issue", "rerun", ISSUE_ID], "code": 1, "stdout": "",
             "stderr": "boom"},
        ])
        boundary = gate.StrictReceiptBoundary(runner, self.store)
        with self.assertRaises(gate.StrictReceiptRefused) as caught:
            boundary.rerun_issue(ISSUE_ID)
        self.assertEqual(caught.exception.details.get("shape"),
                         "nonzero_exit")


class Intents:
    @staticmethod
    def fields() -> dict:
        return {
            "intent_id": INTENT_ID, "schema_version": o2.O2_SCHEMA,
            "source_task_id": "multica://issue/YZT-66",
            "logical_task_key": "YZT-82-r0-canary-fixture",
            "parent_issue_id": PARENT,
            "target_role": "software-engineer",
            "target_agent_id": TARGET_AGENT,
            "package_id": PACKAGE,
            "artifact_dependency_digest": ARTIFACT,
            "creation_authority": "01 engineering-lead via YZT-66 SAFE_DISPATCH",
            "provenance": {"issue": "YZT-82"},
        }

    @staticmethod
    def issue() -> dict:
        return {"id": ISSUE_ID, "identifier": "YZT-82", "title": "R0 fixture",
                "description": "", "parent_issue_id": PARENT,
                "project_id": None, "revision": 1,
                "status_category": "backlog", "assignee_id": TARGET_AGENT}

    @staticmethod
    def snapshot() -> dict:
        return o2.build_snapshot(
            issue={"id": ISSUE_ID, "revision": 1,
                   "status_category": "backlog", "assignee_id": TARGET_AGENT},
            runs=[],
            package={"package_id": PACKAGE,
                     "artifact_dependency_digest": ARTIFACT,
                     "artifact_ready": True},
            ready_note={"comment_id": NOTE, "package_id": PACKAGE,
                        "artifact_dependency_digest": ARTIFACT},
            target_role="software-engineer",
            target_agent_id=TARGET_AGENT)


class CanaryOrchestratorE2ETests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = o2.DurableIntentStore(self.tmp / "shared-ledger.jsonl")

    def tearDown(self):
        self._tmp.cleanup()

    def _armed(self, *, rerun_stdout: str, runs_post):
        table = [
            {"match": ["issue", "rerun", ISSUE_ID], "stdout": rerun_stdout},
            {"match": ["issue", "runs", ISSUE_ID],
             "stdout": json.dumps(runs_post, sort_keys=True)},
        ]
        runner = o2.O2FixtureRunner(table)
        orch = gate.build_r0_canary_orchestrator(
            self.store, runner=runner, clock=lambda: BASE_TIME)
        self.assertIsInstance(orch.boundary, gate.StrictReceiptBoundary)
        orch.record_intent(Intents.fields())
        orch.bind_target(INTENT_ID, issue=Intents.issue(), actor="r1")
        orch.mark_prepared(INTENT_ID, package_id=PACKAGE,
                           artifact_dependency_digest=ARTIFACT, actor="r1")
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest=ARTIFACT, actor="r1")
        snap = Intents.snapshot()
        armed = orch.plan_and_arm(INTENT_ID, snap, actor="r1")
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        self.assertEqual(armed["plan"]["selected_trigger"], o2.TRIGGER_RERUN)
        return orch, runner, snap

    def test_run_wrapper_receipt_stops_typed_and_never_retries(self):
        orch, runner, snap = self._armed(
            rerun_stdout=json.dumps({"run": RUN}, sort_keys=True),
            runs_post=[RUN])
        result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["reason"], o2.R_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["side_effects"], 0)
        triggers = [argv for argv in runner.issued
                    if o2.classify_o2_command(argv)
                    in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(o2.classify_o2_command(triggers[0]),
                         o2.C_RERUN_TRIGGER)
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_TRIGGER_AMBIGUOUS)
        replay = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(len(runner.issued), 1)

    def test_duplicate_key_receipt_stops_typed_and_never_retries(self):
        orch, runner, snap = self._armed(
            rerun_stdout=DUP_OBSERVABLE_LAST_WINS, runs_post=[RUN])
        result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["reason"], o2.R_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["side_effects"], 0)
        triggers = [argv for argv in runner.issued
                    if o2.classify_o2_command(argv)
                    in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(o2.classify_o2_command(triggers[0]),
                         o2.C_RERUN_TRIGGER)
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_TRIGGER_AMBIGUOUS)
        replay = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(len(runner.issued), 1)

    def test_duplicate_key_receipt_never_reaches_o2_parser(self):
        orch, runner, snap = self._armed(
            rerun_stdout=DUP_RUNS_EMPTY_OVERWRITTEN_VALID,
            runs_post=[RUN])
        with ParserSpy() as spy:
            result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertEqual(spy.calls, [])

    def test_non_json_constant_receipt_stops_typed_and_never_retries(self):
        orch, runner, snap = self._armed(
            rerun_stdout=NON_JSON_DIRECT_RUN_EXTRA_NAN, runs_post=[RUN])
        result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["reason"], o2.R_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["side_effects"], 0)
        triggers = [argv for argv in runner.issued
                    if o2.classify_o2_command(argv)
                    in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(o2.classify_o2_command(triggers[0]),
                         o2.C_RERUN_TRIGGER)
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_TRIGGER_AMBIGUOUS)
        replay = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertTrue(replay.get("replayed"))
        self.assertEqual(len(runner.issued), 1)

    def test_non_json_constant_receipt_never_reaches_o2_parser(self):
        orch, runner, snap = self._armed(
            rerun_stdout=NON_JSON_RUNS_WRAPPER_ROW_EXTRA,
            runs_post=[RUN])
        with ParserSpy() as spy:
            result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertEqual(spy.calls, [])

    def test_authorized_receipt_correlates_exactly_one_run(self):
        orch, runner, snap = self._armed(
            rerun_stdout=json.dumps({"runs": [RUN]}, sort_keys=True),
            runs_post=[RUN])
        result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(result["run_id"], RUN_ID)
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_RUN_CORRELATED)
        triggers = [argv for argv in runner.issued
                    if o2.classify_o2_command(argv)
                    in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_selected_trigger_route_persisted_in_ledger(self):
        orch, runner, snap = self._armed(
            rerun_stdout=json.dumps([RUN], sort_keys=True), runs_post=[RUN])
        result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        records = self.store.read_records()
        issuing = [r for r in records
                   if r.get("op") == "transition"
                   and r.get("to") == o2.S_TRIGGER_ISSUING]
        self.assertEqual(len(issuing), 1)
        self.assertEqual(issuing[0]["fields"]["selected_trigger"],
                         o2.TRIGGER_RERUN)
        receipts = [r for r in records
                    if r.get("name") == o2.E_TRIGGER_RECEIPT]
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]["data"]["selected_trigger"],
                         o2.TRIGGER_RERUN)

    def test_misleading_attribution_text_is_ignored_by_correlation(self):
        attributed = run_json(attribution={"kind": "issue_assignment"},
                              attribution_source="delegation")
        orch, runner, snap = self._armed(
            rerun_stdout=json.dumps(attributed, sort_keys=True),
            runs_post=[attributed])
        result = orch.issue_trigger(INTENT_ID, snap, actor="r1")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(result["run_id"], RUN_ID)


if __name__ == "__main__":
    unittest.main()
