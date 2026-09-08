#!/usr/bin/env python3
"""Gate B content-level role check and default-case identity (YZT-42)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from cbuild import resolve_scope, build_package  # noqa: E402
from cgates import (  # noqa: E402
    NORMAL_TASK_ID, ROLE_PROBE_DECISION, ROLE_PROBE_TASK_ID,
    default_case_count_ok, role_profile_changes_ok,
)


class GateBRoleAndCaseTests(unittest.TestCase):
    def test_role_check_passes_when_policy_applied(self):
        s = resolve_scope(ROLE_PROBE_TASK_ID, None, [], "web-imagegen")
        lead = build_package(ROLE_PROBE_TASK_ID, "engineering-lead", s,
                             decision=ROLE_PROBE_DECISION)
        se = build_package(ROLE_PROBE_TASK_ID, "software-engineer", s,
                           decision=ROLE_PROBE_DECISION)
        ok, detail = role_profile_changes_ok(lead, se)
        self.assertTrue(ok, detail)
        self.assertNotIn("lead=engineering-lead se=software-engineer", detail)

    def test_role_check_fails_when_policy_not_applied(self):
        s = resolve_scope(ROLE_PROBE_TASK_ID, None, [], "web-imagegen")
        lead = build_package(ROLE_PROBE_TASK_ID, "engineering-lead", s,
                             decision=ROLE_PROBE_DECISION, apply_role_policy=False)
        se = build_package(ROLE_PROBE_TASK_ID, "software-engineer", s,
                           decision=ROLE_PROBE_DECISION, apply_role_policy=False)
        ok, detail = role_profile_changes_ok(lead, se)
        self.assertFalse(ok, detail)

    def test_echo_label_difference_is_not_enough(self):
        pkg_a = {
            "request": {"task_id": "t", "role": "engineering-lead"},
            "assembly_trace": {"role_policy_applied": "engineering-lead",
                               "case_search_performed": False},
            "team_state_slice": [], "project_state_slice": [],
            "rules": [], "current_facts": [], "cases": [],
            "open_conflicts": [], "blocked_by": [], "source_refs": [],
        }
        pkg_b = dict(pkg_a)
        pkg_b = {**pkg_a, "request": {"task_id": "t", "role": "software-engineer"},
                 "assembly_trace": {"role_policy_applied": "software-engineer",
                                    "case_search_performed": False}}
        ok, _ = role_profile_changes_ok(pkg_a, pkg_b)
        self.assertFalse(ok)

    def test_default_case_check_requires_normal_task_identity(self):
        pkg4 = {
            "request": {"task_id": "b9-probe"},
            "cases": [],
            "assembly_trace": {"case_search_performed": False},
        }
        pkg5 = {
            "request": {"task_id": NORMAL_TASK_ID},
            "cases": [],
            "assembly_trace": {"case_search_performed": False},
        }
        ok4, detail4 = default_case_count_ok(pkg4, expected_task_id=NORMAL_TASK_ID)
        ok5, _ = default_case_count_ok(pkg5, expected_task_id=NORMAL_TASK_ID)
        self.assertFalse(ok4, "checking pkg4 (b9-probe) must fail identity guard")
        self.assertIn("b9-probe", detail4)
        self.assertTrue(ok5)

    def test_default_case_negative_fixture_kills_pkg4_regression(self):
        """If the gate asserted pkg4, a case-activated architect package would
        silently pass the old 0-case check whenever pkg4 happened to have none.
        The identity guard fails pkg4 even with 0 cases."""
        s = resolve_scope("b9-probe", None, [], "app1")
        pkg4 = build_package("b9-probe", "solution-architect", s,
                             decision="app1 domain design")
        pkg5 = build_package(NORMAL_TASK_ID, "software-engineer", s,
                             decision="small normal implementation task")
        trap = build_package("b9-probe", "solution-architect", s,
                             decision="app1 domain design",
                             case_trigger="architecture_design")
        ok4, _ = default_case_count_ok(pkg4, expected_task_id=NORMAL_TASK_ID)
        ok5, detail5 = default_case_count_ok(pkg5, expected_task_id=NORMAL_TASK_ID)
        ok_trap, _ = default_case_count_ok(trap, expected_task_id=NORMAL_TASK_ID)
        self.assertFalse(ok4)
        self.assertTrue(ok5, detail5)
        self.assertEqual(pkg5["request"]["task_id"], NORMAL_TASK_ID)
        self.assertFalse(ok_trap)

    def test_default_case_fails_when_cases_present_on_named_package(self):
        pkg = {
            "request": {"task_id": NORMAL_TASK_ID},
            "cases": [{"ref": "case:CASE-APP1-000001"}],
            "assembly_trace": {"case_search_performed": True},
        }
        ok, _ = default_case_count_ok(pkg, expected_task_id=NORMAL_TASK_ID)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
