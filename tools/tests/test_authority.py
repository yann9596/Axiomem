#!/usr/bin/env python3
"""Authority resolve + claim-support fail-closed tests (YZT-42)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from cauthority import (  # noqa: E402
    authority_resolves, evaluate_all_rules, evaluate_rule_authority,
)


class AuthorityTests(unittest.TestCase):
    def test_project_context_does_not_auto_pass_unknown(self):
        ok, reason = authority_resolves("multica://project-context/multica-agent-team-v1")
        self.assertFalse(ok)
        self.assertIn("not a registered project directory", reason)

    def test_repo_does_not_auto_pass_unknown(self):
        ok, reason = authority_resolves("repo://not-a-real-repo@deadbeef/nope.json")
        self.assertFalse(ok)

    def test_registered_project_context_resolves(self):
        ok, _ = authority_resolves("multica://project-context/web-imagegen")
        self.assertTrue(ok)

    def test_missing_evidence_fails_even_if_ref_resolves(self):
        rule = {
            "id": "RULE-WIMG-000001",
            "authority_refs": ["multica://project-context/web-imagegen"],
        }
        report = evaluate_rule_authority(rule, evidence=None)
        self.assertFalse(report["valid"])
        self.assertTrue(report["missing"])

    def test_claim_support_false_fails(self):
        rule = {
            "id": "RULE-WIMG-000001",
            "authority_refs": ["multica://project-context/web-imagegen"],
        }
        evidence = {
            "claims": [{
                "rule_id": "RULE-WIMG-000001",
                "authority_ref": "multica://project-context/web-imagegen",
                "claim_support": {
                    "supported": False,
                    "excerpt_or_pointer": "does not support this claim",
                    "claim_relation": "contradicts",
                },
                "scope_coverage": {
                    "covers": True,
                    "authority_scope": "project:web-imagegen",
                    "rule_scope": "project:web-imagegen",
                },
            }]
        }
        report = evaluate_rule_authority(rule, evidence)
        self.assertFalse(report["valid"])

    def test_scope_coverage_false_fails(self):
        rule = {
            "id": "RULE-WIMG-000001",
            "authority_refs": ["multica://project-context/web-imagegen"],
        }
        evidence = {
            "claims": [{
                "rule_id": "RULE-WIMG-000001",
                "authority_ref": "multica://project-context/web-imagegen",
                "claim_support": {
                    "supported": True,
                    "excerpt_or_pointer": "integrity constraint",
                    "claim_relation": "supports",
                },
                "scope_coverage": {
                    "covers": False,
                    "authority_scope": "team",
                    "rule_scope": "project:web-imagegen",
                },
            }]
        }
        report = evaluate_rule_authority(rule, evidence)
        self.assertFalse(report["valid"])

    def test_valid_claim_passes_single_rule(self):
        rule = {
            "id": "RULE-WIMG-000001",
            "authority_refs": ["multica://project-context/web-imagegen"],
        }
        evidence = {
            "claims": [{
                "rule_id": "RULE-WIMG-000001",
                "authority_ref": "multica://project-context/web-imagegen",
                "claim_support": {
                    "supported": True,
                    "excerpt_or_pointer": "worktree isolation paragraph",
                    "claim_relation": "supports",
                },
                "scope_coverage": {
                    "covers": True,
                    "authority_scope": "project:web-imagegen",
                    "rule_scope": "project:web-imagegen",
                },
            }]
        }
        report = evaluate_rule_authority(rule, evidence)
        self.assertTrue(report["valid"], report["errors"])

    def test_real_rules_fail_closed_without_evidence_file(self):
        rules = [{
            "id": "RULE-WIMG-000001",
            "authority_refs": ["multica://project-context/web-imagegen"],
        }]
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "authority-evidence.yaml"
            result = evaluate_all_rules(rules, evidence_path=missing)
            self.assertTrue(result["fail_closed"])
            self.assertGreater(result["invalid_count"], 0)
            self.assertTrue(result["missing_evidence"])


if __name__ == "__main__":
    unittest.main()
