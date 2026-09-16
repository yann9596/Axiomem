#!/usr/bin/env python3
"""U12-P0R evidence tests — strict gate readiness bundle (YZT-82).

The generator is exercised against temporary output directories; the
committed bundle under `adapters/multica/u12-p0r/` is regenerated with its
recorded timestamp and compared byte-for-byte. No production state is
written and no platform call is made.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import u12_p0r as g  # noqa: E402
import u12_strict_receipt as gate  # noqa: E402

EVIDENCE_DIR = ROOT / "adapters" / "multica" / "u12-p0r"
FIXED_TIME = "2026-09-11T00:00:00Z"
LEDGER_INDEPENDENT_FILES = (
    "strict-receipt-gate-evidence.json",
    "O2_REPORT_DIGEST_ERRATA.json",
    "proposed-r0-canary-plan.json",
)
FICTIONAL_MATCHING_RECORD = {
    "kind": "yzt105_fictional_ledger_record",
    "record_type": "fixture_ignored",
    "op": "ledger_created",
    "note": "matching fixture; not a production ledger",
}


def load(name: str) -> dict:
    with open(EVIDENCE_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)


def write_fictional_ledger(path: Path, *, mode: str) -> Path:
    """Write missing/matching/drifted JSONL. Never the live production path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "missing":
        if path.exists():
            path.unlink()
        return path
    record = dict(FICTIONAL_MATCHING_RECORD)
    if mode == "drifted":
        record["note"] = "drifted fixture; bytes differ from matching"
    elif mode != "matching":
        raise ValueError(mode)
    path.write_text(json.dumps(record, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def ledger_pins(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return "sha256:" + hashlib.sha256(data).hexdigest(), len(data)


def matching_ledger_patches(path: Path):
    digest, size = ledger_pins(path)
    return mock.patch.multiple(
        g, PRODUCTION_LEDGER_TIP=digest, PRODUCTION_LEDGER_BYTES=size)


def generate_quiet(directory: Path, *, generated_at: str, ledger) -> None:
    """Generate against an injected ledger.

    F-03 changes tools/chandoff_joint.py, so the U12-P0R pin for that file
    no longer matches. Pin updates are F-05 and out of this wave; force the
    pin gate open so these tests isolate ledger injection only.
    """
    real_pins = g.pin_evidence

    def pin_gate_open(*args, **kwargs):
        result = real_pins(*args, **kwargs)
        result = dict(result)
        result["accepted_inputs_all_match"] = True
        return result

    with mock.patch.object(g, "pin_evidence", pin_gate_open), \
            contextlib.redirect_stdout(io.StringIO()):
        g.generate(directory, generated_at=generated_at, root=ROOT,
                   ledger=ledger)


def _not_production_path(path) -> bool:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path)))) != \
        os.path.normcase(os.path.normpath(os.path.abspath(g.PRODUCTION_LEDGER_PATH)))


class InputBoundaryTests(unittest.TestCase):
    def test_prior_p0_artifacts_match_pins(self):
        result = g.prior_artifacts_check(ROOT)
        self.assertTrue(result["all_match"], result)
        self.assertEqual(result["checks"]["prior_plan"]["expected"],
                         g.PRIOR_PLAN_DIGEST)
        self.assertEqual(result["checks"]["prior_manifest"]["expected"],
                         g.PRIOR_MANIFEST_DIGEST)
        self.assertTrue(result["checks"]["prior_p0r_manifest"]["match"],
                        result["checks"]["prior_p0r_manifest"])
        self.assertTrue(result["checks"]["prior_p0r_gate"]["match"],
                        result["checks"]["prior_p0r_gate"])
        self.assertEqual(result["checks"]["prior_p0r_manifest"]["commit"],
                         g.PRIOR_COMMIT)
        self.assertEqual(result["checks"]["lineage_p0r_manifest"]["expected"],
                         g.LINEAGE_P0R_MANIFEST_DIGEST)
        self.assertEqual(result["checks"]["lineage_p0r_gate"]["expected"],
                         g.LINEAGE_P0R_GATE_SHA256_LF)
        self.assertTrue(result["checks"]["lineage_p0r_manifest"]["match"],
                        result["checks"]["lineage_p0r_manifest"])
        self.assertTrue(result["checks"]["lineage_p0r_gate"]["match"],
                        result["checks"]["lineage_p0r_gate"])
        self.assertEqual(result["checks"]["lineage_p0r_manifest"]["commit"],
                         g.LINEAGE_COMMIT)

    def test_accepted_pins_and_errata_row(self):
        pins = g.pin_evidence(ROOT)
        self.assertTrue(pins["accepted_inputs_all_match"],
                        pins["checks"])
        errata_row = pins["o2_report_digest_errata"]
        self.assertEqual(errata_row["correct_lf_digest"],
                         g.O2_TEST_CORRECT_LF)
        self.assertEqual(errata_row["recorded_in_o2_report"],
                         g.O2_TEST_RECORDED)
        self.assertFalse(errata_row["match"])

    def test_errata_blobs_identical_at_all_accepted_commits(self):
        errata = g.errata_payload(ROOT)
        self.assertTrue(errata["ok"], errata["verification"])
        verification = errata["verification"]
        self.assertTrue(verification["blobs_identical_at_all_commits"])
        self.assertTrue(verification["matches_correct_lf_digest"])
        self.assertFalse(verification["recorded_value_reproducible_from_any_commit"])
        self.assertEqual(sorted(verification["blob_digests"]),
                         sorted(g.ERRATA_COMMITS))

class FictionalLedgerIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_matching_ledger_reports_tip_unchanged(self):
        path = write_fictional_ledger(self.root / "matching.jsonl",
                                      mode="matching")
        with matching_ledger_patches(path):
            ledger = g.production_ledger_evidence(path)
        self.assertTrue(ledger["exists"])
        self.assertTrue(ledger["tip_unchanged"], ledger)
        self.assertEqual(ledger["partial_or_corrupt_records"], 0)
        self.assertEqual(ledger["intent_records"], 0)
        self.assertTrue(ledger["audit_ok"])
        self.assertTrue(_not_production_path(ledger["path"]))

    def test_missing_ledger_reports_not_unchanged(self):
        path = write_fictional_ledger(self.root / "missing.jsonl",
                                      mode="missing")
        ledger = g.production_ledger_evidence(path)
        self.assertFalse(ledger["exists"])
        self.assertFalse(ledger["tip_unchanged"])
        self.assertIsNone(ledger["tip_digest"])
        self.assertTrue(_not_production_path(ledger["path"]))

    def test_drifted_ledger_reports_not_unchanged(self):
        matching = write_fictional_ledger(self.root / "matching.jsonl",
                                          mode="matching")
        drifted = write_fictional_ledger(self.root / "drifted.jsonl",
                                         mode="drifted")
        with matching_ledger_patches(matching):
            ledger = g.production_ledger_evidence(drifted)
        self.assertTrue(ledger["exists"])
        self.assertFalse(ledger["tip_unchanged"], ledger)
        self.assertTrue(_not_production_path(ledger["path"]))


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.matching = write_fictional_ledger(
            Path(self.tmp.name) / "matching.jsonl", mode="matching")
        self.drifted = write_fictional_ledger(
            Path(self.tmp.name) / "drifted.jsonl", mode="drifted")
        self.missing = write_fictional_ledger(
            Path(self.tmp.name) / "missing.jsonl", mode="missing")

    def test_generation_is_deterministic(self):
        with matching_ledger_patches(self.matching), \
                tempfile.TemporaryDirectory() as first, \
                tempfile.TemporaryDirectory() as second:
            generate_quiet(Path(first), generated_at=FIXED_TIME,
                           ledger=self.matching)
            generate_quiet(Path(second), generated_at=FIXED_TIME,
                           ledger=self.matching)
            for name in g.BUNDLE_FILES:
                with self.subTest(file=name):
                    self.assertEqual((Path(first) / name).read_bytes(),
                                     (Path(second) / name).read_bytes())

    def test_generation_refuses_missing_and_drifted_ledgers(self):
        with matching_ledger_patches(self.matching), \
                tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                generate_quiet(Path(tmp), generated_at=FIXED_TIME,
                               ledger=self.missing)
            with self.assertRaises(RuntimeError):
                generate_quiet(Path(tmp), generated_at=FIXED_TIME,
                               ledger=self.drifted)

    @unittest.skipUnless(
        (EVIDENCE_DIR / "readiness-manifest.json").exists(),
        "committed bundle absent")
    def test_committed_bundle_regenerates_ledger_independent_files(self):
        committed = load("readiness-manifest.json")
        with matching_ledger_patches(self.matching), \
                tempfile.TemporaryDirectory() as tmp:
            generate_quiet(Path(tmp), generated_at=committed["generated_at"],
                           ledger=self.matching)
            generated = Path(tmp)
            for name in LEDGER_INDEPENDENT_FILES:
                with self.subTest(file=name):
                    self.assertEqual((EVIDENCE_DIR / name).read_bytes(),
                                     (generated / name).read_bytes())
            for name in g.BUNDLE_FILES:
                self.assertTrue((generated / name).is_file(), name)
            integrity = json.loads(
                (generated / "production-ledger-readonly-integrity.json")
                .read_text(encoding="utf-8"))
            self.assertTrue(integrity["tip_unchanged"], integrity)
            self.assertTrue(_not_production_path(integrity["path"]))


@unittest.skipUnless(
    (EVIDENCE_DIR / "readiness-manifest.json").exists(),
    "committed bundle absent")
class CommittedBundleTests(unittest.TestCase):
    def test_manifest_digest_and_completion_evidence(self):
        manifest = load("readiness-manifest.json")
        recomputed = g.p0.digest(
            {k: v for k, v in manifest.items() if k != "manifest_digest"})
        self.assertEqual(manifest["manifest_digest"], recomputed)
        self.assertEqual(manifest["supersedes"]["digest"],
                         g.PRIOR_MANIFEST_DIGEST)
        self.assertTrue(manifest["supersedes"]["verified_match"])
        prior_rev = manifest["supersedes"]["prior_u12_p0r_revision"]
        self.assertEqual(prior_rev["manifest_digest"],
                         g.PRIOR_P0R_MANIFEST_DIGEST)
        self.assertEqual(prior_rev["strict_gate_sha256_lf"],
                         g.PRIOR_P0R_GATE_SHA256_LF)
        self.assertTrue(prior_rev["verified_manifest_match"])
        self.assertTrue(prior_rev["verified_gate_match"])
        lineage = manifest["supersedes"]["prior_u12_p0r_lineage"]
        self.assertEqual(lineage["manifest_digest"],
                         g.LINEAGE_P0R_MANIFEST_DIGEST)
        self.assertEqual(lineage["strict_gate_sha256_lf"],
                         g.LINEAGE_P0R_GATE_SHA256_LF)
        self.assertEqual(lineage["commit"], g.LINEAGE_COMMIT)
        self.assertTrue(lineage["verified_manifest_match"])
        self.assertTrue(lineage["verified_gate_match"])
        self.assertFalse(manifest["r0_canary_authorized"])
        completion = manifest["completion_evidence"]
        self.assertEqual(completion["authorized_receipt_shapes_accepted"],
                         "3/3")
        self.assertTrue(completion["run_wrapper_shape_rejected"])
        self.assertTrue(completion["all_unauthorized_or_ambiguous_shapes_rejected"])
        self.assertTrue(completion["duplicate_keys_including_same_value_rejected"])
        self.assertEqual(completion["duplicate_key_parser_calls_on_rejection"], 0)
        self.assertEqual(completion["duplicate_key_cases_rejected"], "7/7")
        self.assertEqual(completion["f4_disposition"],
                         "RESOLVED_BY_STRICT_DUPLICATE_KEY_DECODER")
        self.assertIn("U12-P0R-F4", manifest["findings"])
        self.assertTrue(completion["non_json_constants_rejected"])
        self.assertEqual(completion["non_json_constant_parser_calls_on_rejection"],
                         0)
        self.assertEqual(completion["non_json_constant_cases_rejected"],
                         "13/13")
        self.assertEqual(completion["f5_disposition"],
                         "RESOLVED_BY_STRICT_NON_JSON_CONSTANT_GUARD")
        self.assertIn("U12-P0R-F5", manifest["findings"])
        self.assertTrue(completion["r0_path_uses_strict_gate_only"])
        self.assertFalse(completion["o2_parser_or_history_modified"])
        self.assertFalse(completion["trigger_route_derived_from_attribution_text"])
        self.assertTrue(completion["production_ledger_tip_unchanged"])
        self.assertEqual(completion["live_r0_runs_created"], 0)
        self.assertEqual(completion["live_05_or_06_activation"], 0)
        self.assertEqual(completion["canonical_or_product_writes"], 0)
        self.assertFalse(completion["o3_or_autonomous_wake_added"])

    def test_plan_binds_exact_strict_gate(self):
        plan = load("proposed-r0-canary-plan.json")
        descriptor = gate.gate_descriptor(TOOLS / "u12_strict_receipt.py")
        bound = plan["strict_receipt_gate"]
        self.assertEqual(bound["module"], descriptor["module"])
        self.assertEqual(bound["version"], descriptor["version"])
        self.assertEqual(bound["sha256_lf"], descriptor["sha256_lf"])
        self.assertEqual(bound["receipt_entrypoint"],
                         descriptor["receipt_entrypoint"])
        self.assertFalse(bound["permissive_entrypoint_reachable_in_r0_path"])
        self.assertFalse(bound["bypass_or_fallback"])
        self.assertTrue(bound["duplicate_key_rejected"])
        self.assertTrue(bound["same_valued_duplicate_keys_rejected"])
        self.assertEqual(bound["duplicate_key_scope"],
                         descriptor["duplicate_key_scope"])
        self.assertTrue(bound["non_json_constant_rejected"])
        self.assertEqual(bound["non_json_constant_scope"],
                         descriptor["non_json_constant_scope"])
        self.assertTrue(bound["quoted_constant_words_remain_legal"])
        provenance = plan["sole_trigger"]["route_provenance"]
        self.assertFalse(provenance["run_attribution_text_used_for_route"])
        self.assertEqual(provenance["source"], "issuing_transaction_and_ledger")

    def test_errata_artifact_records_f1_forward_only(self):
        errata = load("O2_REPORT_DIGEST_ERRATA.json")
        self.assertEqual(errata["kind"], "o2_report_digest_errata")
        self.assertEqual(errata["finding"], "U12-P0-F1")
        self.assertEqual(errata["subject"]["correct_lf_digest"],
                         g.O2_TEST_CORRECT_LF)
        self.assertTrue(errata["verification"]["blobs_identical_at_all_commits"])
        self.assertFalse(errata["remediation"]["o2_report_edited"])
        self.assertFalse(
            errata["remediation"]["o2_or_predecessor_history_rewritten"])

    def test_strict_gate_evidence_is_green(self):
        evidence = load("strict-receipt-gate-evidence.json")
        matrix = evidence["shape_matrix"]
        self.assertTrue(matrix["ok"])
        self.assertEqual(matrix["authorized_receipt_shapes_accepted"], "3/3")
        self.assertTrue(matrix["run_wrapper_shape_rejected"])
        self.assertTrue(matrix["all_unauthorized_or_ambiguous_shapes_rejected"])
        self.assertTrue(matrix["duplicate_keys_including_same_value_rejected"])
        self.assertEqual(matrix["duplicate_key_cases_rejected"], "7/7")
        self.assertEqual(matrix["duplicate_key_parser_calls_on_rejection"], 0)
        self.assertEqual(len(matrix["duplicate_key_cases"]), 7)
        self.assertTrue(matrix["non_json_constants_rejected"])
        self.assertEqual(matrix["non_json_constant_cases_rejected"], "13/13")
        self.assertEqual(
            matrix["non_json_constant_parser_calls_on_rejection"], 0)
        self.assertEqual(len(matrix["non_json_constant_cases"]), 13)
        self.assertEqual(matrix["permissive_parser_invoked_on_refusal"], 0)
        self.assertTrue(evidence["wiring_proof"]["ok"])
        self.assertTrue(
            evidence["wiring_proof"]["checks"]["duplicate_key_guard_present"])
        self.assertTrue(
            evidence["wiring_proof"]["checks"]
            ["non_json_constant_guard_present"])
        self.assertTrue(evidence["gate"]["duplicate_key_fails_closed"])
        self.assertTrue(
            evidence["gate"]["same_valued_duplicate_keys_fail_closed"])
        self.assertTrue(evidence["gate"]["non_json_constant_fails_closed"])
        self.assertTrue(evidence["f4_duplicate_key_boundary"]
                        ["enforced_before_o2_parser"])
        self.assertTrue(evidence["f5_non_json_constant_boundary"]
                        ["enforced_before_o2_parser"])
        self.assertTrue(evidence["f5_non_json_constant_boundary"]
                        ["quoted_constant_words_remain_legal"])
        self.assertFalse(
            evidence["r0_path"]["permissive_entrypoint_reachable_in_r0_path"])
        self.assertFalse(
            evidence["trigger_route_provenance"]
            ["run_attribution_used_for_route"])


if __name__ == "__main__":
    unittest.main()
