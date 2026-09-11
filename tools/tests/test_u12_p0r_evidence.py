#!/usr/bin/env python3
"""U12-P0R evidence tests — strict gate readiness bundle (YZT-82).

The generator is exercised against temporary output directories; the
committed bundle under `adapters/multica/u12-p0r/` is regenerated with its
recorded timestamp and compared byte-for-byte. No production state is
written and no platform call is made.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import u12_p0r as g  # noqa: E402
import u12_strict_receipt as gate  # noqa: E402

EVIDENCE_DIR = ROOT / "adapters" / "multica" / "u12-p0r"
LEDGER = Path(g.PRODUCTION_LEDGER_PATH)
FIXED_TIME = "2026-09-11T00:00:00Z"


def load(name: str) -> dict:
    with open(EVIDENCE_DIR / name, encoding="utf-8") as handle:
        return json.load(handle)


def generate_quiet(directory: Path, *, generated_at: str) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        g.generate(directory, generated_at=generated_at, root=ROOT)


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

    @unittest.skipUnless(LEDGER.exists(), "production ledger not present")
    def test_production_ledger_read_only_integrity(self):
        ledger = g.production_ledger_evidence()
        self.assertTrue(ledger["tip_unchanged"], ledger)
        self.assertEqual(ledger["tip_digest"], g.PRODUCTION_LEDGER_TIP)
        self.assertEqual(ledger["partial_or_corrupt_records"], 0)
        self.assertEqual(ledger["intent_records"], 0)
        self.assertTrue(ledger["audit_ok"])


class GenerationTests(unittest.TestCase):
    def test_generation_is_deterministic(self):
        with tempfile.TemporaryDirectory() as first, \
                tempfile.TemporaryDirectory() as second:
            generate_quiet(Path(first), generated_at=FIXED_TIME)
            generate_quiet(Path(second), generated_at=FIXED_TIME)
            for name in g.BUNDLE_FILES:
                with self.subTest(file=name):
                    self.assertEqual((Path(first) / name).read_bytes(),
                                     (Path(second) / name).read_bytes())

    @unittest.skipUnless(
        (EVIDENCE_DIR / "readiness-manifest.json").exists()
        and LEDGER.exists(), "committed bundle or production ledger absent")
    def test_committed_bundle_regenerates_byte_identical(self):
        committed = load("readiness-manifest.json")
        with tempfile.TemporaryDirectory() as tmp:
            generate_quiet(Path(tmp),
                           generated_at=committed["generated_at"])
            for name in g.BUNDLE_FILES:
                with self.subTest(file=name):
                    self.assertEqual((EVIDENCE_DIR / name).read_bytes(),
                                     (Path(tmp) / name).read_bytes())


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
        self.assertEqual(matrix["permissive_parser_invoked_on_refusal"], 0)
        self.assertTrue(evidence["wiring_proof"]["ok"])
        self.assertTrue(
            evidence["wiring_proof"]["checks"]["duplicate_key_guard_present"])
        self.assertTrue(evidence["gate"]["duplicate_key_fails_closed"])
        self.assertTrue(
            evidence["gate"]["same_valued_duplicate_keys_fail_closed"])
        self.assertTrue(evidence["f4_duplicate_key_boundary"]
                        ["enforced_before_o2_parser"])
        self.assertFalse(
            evidence["r0_path"]["permissive_entrypoint_reachable_in_r0_path"])
        self.assertFalse(
            evidence["trigger_route_provenance"]
            ["run_attribution_used_for_route"])


if __name__ == "__main__":
    unittest.main()
