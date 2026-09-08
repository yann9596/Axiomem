#!/usr/bin/env python3
"""Role-aware builder tests (YZT-42 / YZT-41 finding 1)."""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import cbuild  # noqa: E402
from cbuild import resolve_scope, build_package  # noqa: E402
from cdata import load_role_profile  # noqa: E402
from crole import content_fingerprint, role_content_differs  # noqa: E402


class RoleAwareBuilderTests(unittest.TestCase):
    def setUp(self):
        self.scope = resolve_scope("b8-probe", None, [], "web-imagegen")
        self.decision = "project posture provider switch"

    def test_load_role_profile_is_called_in_builder(self):
        src = inspect.getsource(cbuild.build_package)
        self.assertIn("load_role_profile(", src)
        profile = load_role_profile("engineering-lead")
        self.assertEqual(profile["role"], "engineering-lead")
        self.assertIn("retrieval_policy", profile)

    def test_same_task_lead_and_se_differ_in_content(self):
        lead = build_package("b8-probe", "engineering-lead", self.scope,
                             decision=self.decision)
        se = build_package("b8-probe", "software-engineer", self.scope,
                           decision=self.decision)
        self.assertTrue(role_content_differs(lead, se))
        self.assertTrue(lead["team_state_slice"])
        self.assertTrue(any(e.get("section") == "posture"
                            for e in lead["team_state_slice"]))
        self.assertEqual(se["team_state_slice"], [])
        self.assertTrue(se["project_state_slice"])
        self.assertEqual(lead["project_state_slice"], [])
        self.assertIn("role_profile_loaded", lead["assembly_trace"]["filters_applied"])
        self.assertIn("role_profile_loaded", se["assembly_trace"]["filters_applied"])

    def test_unapplied_role_policy_yields_identical_content(self):
        lead = build_package("b8-probe", "engineering-lead", self.scope,
                             decision=self.decision, apply_role_policy=False)
        se = build_package("b8-probe", "software-engineer", self.scope,
                           decision=self.decision, apply_role_policy=False)
        self.assertEqual(content_fingerprint(lead), content_fingerprint(se))
        self.assertFalse(role_content_differs(lead, se))
        self.assertIn("role_profile_not_applied",
                      lead["assembly_trace"]["filters_applied"])

    def test_missing_role_profile_fails_closed(self):
        with self.assertRaises(ValueError):
            build_package("b8-probe", "not-a-role", self.scope,
                          decision=self.decision)


if __name__ == "__main__":
    unittest.main()
