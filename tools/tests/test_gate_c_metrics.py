#!/usr/bin/env python3
"""Gate C computed metrics fail-closed tests (YZT-42)."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (TOOLS, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cmetrics import (  # noqa: E402
    compute_false_canonical, compute_false_forget, compute_issue_noise,
    metric_bundle, verify_expectation_lock,
)
from cauthority import evaluate_all_rules  # noqa: E402
from failclosed_fixture import build_missing_evidence_root  # noqa: E402


class ReplayMetricTests(unittest.TestCase):
    def test_missing_evidence_does_not_default_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "migration" / "replay-evidence").mkdir(parents=True)
            bundle = metric_bundle("replay-yzt-22", ["rule:RULE-APP1-000001"],
                                   ["multica://issue/YZT-40"], root=root)
            self.assertEqual(bundle["status"], "missing_evidence")
            self.assertIsNone(bundle["false_canonical"])
            self.assertIsNone(bundle["false_forget"])
            self.assertIsNone(bundle["issue_noise"])
            self.assertNotEqual(bundle["false_canonical"], 0)
            self.assertNotEqual(bundle["issue_noise"], 0)

    def test_false_canonical_from_evidence_and_actual(self):
        evidence = {"false_canonical_refs": ["rule:FAKE", "fact:DROPPED"]}
        hits = compute_false_canonical(["rule:REAL", "rule:FAKE"], evidence)
        self.assertEqual(hits, ["rule:FAKE"])
        self.assertEqual(compute_false_canonical(["rule:REAL"], evidence), [])

    def test_false_forget_from_must_retain(self):
        evidence = {"must_retain_refs": ["rule:KEEP", "fact:KEEP"]}
        hits = compute_false_forget(["rule:KEEP"], evidence)
        self.assertEqual(hits, ["fact:KEEP"])

    def test_issue_noise_from_allowed_list(self):
        evidence = {"allowed_issue_refs": ["multica://issue/YZT-40"]}
        hits = compute_issue_noise(
            ["multica://issue/YZT-40", "multica://issue/YZT-99"], evidence)
        self.assertEqual(hits, ["multica://issue/YZT-99"])

    def test_corrupt_or_mismatched_lock_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "migration"
            replay = mig / "replay"
            replay.mkdir(parents=True)
            exp = replay / "replay-yzt-9.yaml"
            exp.write_text("task_id: replay-yzt-9\n", encoding="utf-8")
            lock = mig / "replay-expectation.lock.yaml"
            lock.write_text(
                "schema_version: \"1.1\"\n"
                "kind: replay_expectation_lock\n"
                "files:\n"
                "  - path: migration/replay/replay-yzt-9.yaml\n"
                "    sha256: \"" + ("ab" * 32) + "\"\n",
                encoding="utf-8")
            result = verify_expectation_lock(root=root)
            self.assertFalse(result["ok"])
            self.assertTrue(result["mismatches"])

    def test_missing_lock_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = verify_expectation_lock(root=Path(tmp))
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "missing_evidence")

    def test_matching_lock_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mig = root / "migration"
            replay = mig / "replay"
            replay.mkdir(parents=True)
            exp = replay / "replay-yzt-9.yaml"
            body = b"task_id: replay-yzt-9\n"
            exp.write_bytes(body)
            digest = hashlib.sha256(body).hexdigest()
            (mig / "replay-expectation.lock.yaml").write_text(
                "schema_version: \"1.1\"\n"
                "kind: replay_expectation_lock\n"
                "files:\n"
                "  - path: migration/replay/replay-yzt-9.yaml\n"
                f"    sha256: \"{digest}\"\n",
                encoding="utf-8")
            result = verify_expectation_lock(root=root)
            self.assertTrue(result["ok"], result)

    def test_real_repo_metrics_are_missing_evidence_not_zero(self):
        """Isolated fixture: missing replay/authority evidence must not default metrics to 0."""
        with tempfile.TemporaryDirectory() as tmp:
            root = build_missing_evidence_root(Path(tmp))
            bundle = metric_bundle(
                "replay-yzt-22", ["rule:RULE-APP1-000002"],
                ["multica://issue/YZT-40"], root=root)
            self.assertEqual(bundle["status"], "missing_evidence")
            self.assertIsNone(bundle["false_canonical"])
            self.assertIsNone(bundle["false_forget"])
            self.assertIsNone(bundle["issue_noise"])
            self.assertNotEqual(bundle["false_canonical"], 0)
            self.assertNotEqual(bundle["false_forget"], 0)
            self.assertNotEqual(bundle["issue_noise"], 0)
            lock = verify_expectation_lock(root=root)
            self.assertFalse(lock["ok"])
            self.assertEqual(lock["status"], "missing_evidence")
            self.assertIn("migration/replay-expectation.lock.yaml",
                          lock["missing_evidence"])
            auth = evaluate_all_rules(
                [{"id": "RULE-WIMG-000001",
                  "authority_refs": ["multica://project-context/web-imagegen"]}],
                evidence_path=root / "migration" / "authority-evidence.yaml")
            self.assertTrue(auth["fail_closed"])
            self.assertTrue(auth["missing_evidence"])
            self.assertGreater(auth["invalid_count"], 0)


if __name__ == "__main__":
    unittest.main()
