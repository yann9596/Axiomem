#!/usr/bin/env python3
"""T01 deterministic Handoff PLAN + Finding Gate tests (YZT-47)."""
from __future__ import annotations

import copy
import inspect
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from schema_mini import Schema, load_schema_file  # noqa: E402
import app1_registry_fixture as app1_fixture  # noqa: E402
import chandoff  # noqa: E402
import chandoff_plan as plan  # noqa: E402
from cdata import load_all_docs  # noqa: E402
from yaml_mini import parse_yaml  # noqa: E402
from cutil import TEAM  # noqa: E402


PLAN_S = "context-handoff/context-plan.schema.json"


def validate_plan(instance: dict) -> list:
    schema = load_schema_file(PLAN_S)
    return Schema(schema, schema).validate(instance, path="$")


def sample_request(**overrides) -> dict:
    req = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": "multica://issue/YZT-47",
        "project": {"project_id": "web-imagegen"},
        "target": {"role": "software-engineer"},
        "purpose": "implementation",
        "task_snapshot": {
            "title": "Implement provider switch in an isolated worktree",
            "description": (
                "Product code for Web-ImageGen provider default|grok|gpt, "
                "canonical memory stays with Context Engineer, worktree isolation."
            ),
            "requirements": [
                "never clean the product repo",
                "write product code only in a worktree",
                "respect provider neutrality",
            ],
            "acceptance_criteria": [
                "scope pollution is zero",
                "plan is schema valid",
            ],
            "relevant_decisions": ["reuse frozen Native API contract"],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }
    req.update(overrides)
    return req


def app1_case_request(role: str = "solution-architect") -> dict:
    return sample_request(
        project={"project_id": "app1"},
        target={"role": role},
        purpose="architecture_design",
        task_snapshot={
            "title": "Design follow-up reminders and home-page notifications for App1",
            "description": (
                "Evaluating H3-type engagement/reminder hypotheses and "
                "follow-up presentation for teachers."
            ),
            "requirements": ["design follow-up presentation reminders notifications"],
            "acceptance_criteria": ["scenario match recorded"],
            "relevant_decisions": [],
        },
        options={"case_trigger": "explicit_historical_search", "limit": 8},
    )


def finding(fid: str, **kwargs) -> dict:
    doc = {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": fid,
        "project_id": "web-imagegen",
        "task_id": "multica://issue/YZT-47",
        "summary": "provider constraint observation",
        "detail": None,
        "intent": "observation",
        "source_refs": ["repo://web-imagegen@main/README.md"],
        "discovered_by": "software-engineer",
        "status": "open",
        "verification": "unverified",
        "created_at": "2026-09-09T00:00:00Z",
    }
    doc.update(kwargs)
    return doc


class CompatibilityTests(unittest.TestCase):
    def test_frozen_contract_can_host_finding_gate(self):
        report = plan.compatibility_check()
        self.assertTrue(report["frozen_prepare_handoff_contract_can_support_finding_gate"])
        self.assertTrue(report["frozen_self_check_contract_can_support_finding_gate"])
        self.assertTrue(report["frozen_result_schema_can_express_blocked_or_refresh"])
        self.assertTrue(report["ok"])


class LivePlanTests(unittest.TestCase):
    def setUp(self):
        self.result = plan.prepare_handoff_plan(sample_request(), findings=[])

    def test_emits_schema_valid_plan(self):
        self.assertEqual(self.result["status"], "PLAN_READY")
        self.assertEqual(validate_plan(self.result["plan"]), [])
        self.assertFalse(self.result["llm_called"])
        self.assertEqual(self.result["scope_pollution"], 0)

    def test_finding_gate_runs_before_plan_when_no_findings(self):
        gate = self.result["finding_gate"]
        self.assertEqual(gate["status"], "CLEAR")
        self.assertEqual(gate["boundary"], "handoff")
        self.assertEqual(gate["relevant_open_findings"], [])
        self.assertFalse(gate["context_engineer_woken"])
        self.assertIn("finding_gate_handoff", self.result["plan"]["hard_filters_applied"])

    def test_candidates_use_canonical_ids_and_omit_pseudo_scheme_refs(self):
        cands = self.result["plan"]["candidates"]
        self.assertTrue(cands["rules"] or cands["facts"] or cands["anchor"])
        for group in ("rules", "facts", "cases"):
            for item in cands[group]:
                self.assertIn("id", item)
                self.assertFalse(str(item.get("ref", "")).startswith("rule:"))
                self.assertFalse(str(item.get("ref", "")).startswith("fact:"))
                self.assertFalse(str(item.get("ref", "")).startswith("case:"))
        for item in cands["anchor"]:
            self.assertTrue(item["id"].startswith("anchor:"))
            self.assertTrue(item.get("ref", "").startswith("project://"))

    def test_semantic_jobs_are_bounded_frozen_vocabulary(self):
        jobs = self.result["plan"]["semantic_jobs"]
        self.assertLessEqual(len(jobs), len(plan.SEMANTIC_JOBS_ORDER))
        self.assertEqual(jobs, [j for j in plan.SEMANTIC_JOBS_ORDER if j in jobs])
        self.assertIn("interpret_task_intent", jobs)
        self.assertIn("produce_minimum_sufficient_context", jobs)

    def test_deterministic_repeated_output(self):
        a = plan.prepare_handoff_plan(sample_request(), findings=[])
        b = plan.prepare_handoff_plan(sample_request(), findings=[])
        self.assertEqual(a["plan"], b["plan"])
        self.assertEqual(a["scope_pollution"], 0)
        self.assertEqual(b["llm_called"], False)

    def test_no_llm_is_called(self):
        src = inspect.getsource(plan)
        for needle in ("openai", "anthropic", "litellm", "chat.completions",
                       "invoke_llm", "completion("):
            self.assertNotIn(needle, src)
        self.assertFalse(plan.LLM_CALLED)
        self.assertFalse(self.result["llm_called"])


class ScopeIsolationTests(unittest.TestCase):
    def test_project_scope_excludes_other_project_objects(self):
        result = plan.prepare_handoff_plan(sample_request(), findings=[])
        ids = [x["id"] for x in result["plan"]["candidates"]["rules"]]
        ids += [x["id"] for x in result["plan"]["candidates"]["facts"]]
        ids += [x["id"] for x in result["plan"]["candidates"]["cases"]]
        self.assertTrue(any(i.startswith("RULE-WIMG") or i.startswith("FACT-WIMG") or i.startswith("RULE-TEAM")
                            for i in ids) or result["plan"]["candidates"]["anchor"])
        self.assertFalse(any("APP1" in i for i in ids))
        self.assertEqual(result["scope_pollution"], 0)

    def test_cross_project_requires_explicit_allow_list(self):
        live_app1 = app1_fixture.registry_with_live_app1()
        with self.assertRaises(Exception):
            plan.resolve_handoff_scope(sample_request(
                options={"cross_project_projects": ["web-imagegen"]}))
        scope = plan.resolve_handoff_scope(sample_request(
            options={"cross_project_projects": ["app1", "web-imagegen"]}),
            registry=live_app1)
        self.assertEqual(scope["type"], "cross_project")
        self.assertEqual(scope["projects"], ["app1", "web-imagegen"])
        self.assertIsNone(scope["task_id"])

    def test_explicit_allow_list_admits_listed_projects_only(self):
        req = sample_request(
            purpose="architecture_design",
            task_snapshot={
                "title": "Cross-project follow-up and provider worktree review",
                "description": (
                    "App1 follow-up reminders plus Web-ImageGen provider switch "
                    "in a worktree; product repo isolation."
                ),
                "requirements": [
                    "follow-up presentation reminders",
                    "provider switch worktree",
                    "never clean the product repo",
                ],
                "acceptance_criteria": ["explicit allow list"],
                "relevant_decisions": [],
            },
            options={"cross_project_projects": ["app1", "web-imagegen"], "limit": 8},
        )
        result = plan.prepare_handoff_plan(
            req, findings=[], registry=app1_fixture.registry_with_live_app1())
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["plan"]["scope"]["type"], "cross_project")
        ids = [x["id"] for x in result["plan"]["candidates"]["facts"]]
        ids += [x["id"] for x in result["plan"]["candidates"]["rules"]]
        anchor_ids = [a["id"] for a in result["plan"]["candidates"]["anchor"]]
        self.assertTrue(any("APP1" in i for i in ids) or "anchor:app1" in anchor_ids)
        self.assertTrue(any("WIMG" in i for i in ids) or "anchor:web-imagegen" in anchor_ids)
        self.assertEqual(result["scope_pollution"], 0)
        self.assertEqual(validate_plan(result["plan"]), [])

    def test_canonical_registry_archives_app1(self):
        """YZT-98: the canonical Registry, not a fixture, keeps app1 archived."""
        registry = plan.load_registry()
        by_id = {p["id"]: p for p in registry["projects"]}
        self.assertEqual(by_id["app1"]["phase"], "archived")
        self.assertEqual(by_id["teachers-app1"]["phase"], "incubation")
        self.assertEqual(by_id["teachers-app1"]["multica_project_id"],
                         "7a2195b5-6628-4b02-9fb2-bc3ce161de85")
        blocked = plan.prepare_handoff_plan(
            sample_request(project={"project_id": "app1"}), findings=[])
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIsNone(blocked["plan"])
        self.assertIn("archived", blocked["escalation"]["reason"])
        ready = plan.prepare_handoff_plan(
            sample_request(project={"project_id": "teachers-app1"}), findings=[])
        self.assertEqual(ready["status"], "PLAN_READY")
        self.assertEqual(ready["plan"]["scope"]["project_id"], "teachers-app1")

    def test_archived_project_is_excluded(self):
        registry = parse_yaml((TEAM / "registry" / "projects.yaml").read_text(encoding="utf-8"))
        registry = copy.deepcopy(registry)
        for p in registry["projects"]:
            if p["id"] == "web-imagegen":
                p["phase"] = "archived"
        result = plan.prepare_handoff_plan(sample_request(), findings=[], registry=registry)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["plan"])
        self.assertIn("archived", result["escalation"]["reason"])


class RoleAndAuthorityTests(unittest.TestCase):
    def test_role_profile_changes_checkpoint_selection(self):
        se = plan.prepare_handoff_plan(sample_request(), findings=[])
        lead_req = sample_request(target={"role": "engineering-lead"})
        lead = plan.prepare_handoff_plan(lead_req, findings=[])
        se_cp = {(e["checkpoint"], e["section"], e["id"])
                 for e in se["plan"]["candidates"]["checkpoint_entries"]}
        lead_cp = {(e["checkpoint"], e["section"], e["id"])
                   for e in lead["plan"]["candidates"]["checkpoint_entries"]}
        self.assertNotEqual(se_cp, lead_cp)
        self.assertTrue(any(e["id"].startswith("team:posture-")
                            for e in lead["plan"]["candidates"]["checkpoint_entries"]))
        self.assertFalse(any(e["id"].startswith("team:posture-")
                             for e in se["plan"]["candidates"]["checkpoint_entries"]))
        self.assertTrue(any(e["checkpoint"] == "web-imagegen"
                            for e in se["plan"]["candidates"]["checkpoint_entries"]))

    def test_review_needed_is_conflict_not_silent_authority(self):
        docs = copy.deepcopy(load_all_docs())
        target = next(d for d in docs if d.get("id") == "RULE-WIMG-000001")
        target["status"] = "review_needed"
        result = plan.prepare_handoff_plan(sample_request(), findings=[], docs=docs)
        rule_ids = [r["id"] for r in result["plan"]["candidates"]["rules"]]
        self.assertNotIn("RULE-WIMG-000001", rule_ids)
        conf_ids = [c["id"] for c in result["plan"]["candidates"]["conflicts"]]
        self.assertIn("review:RULE-WIMG-000001", conf_ids)
        self.assertEqual(validate_plan(result["plan"]), [])

    def test_rule_without_authority_is_review_needed(self):
        docs = copy.deepcopy(load_all_docs())
        target = next(d for d in docs if d.get("id") == "RULE-WIMG-000001")
        target["authority_refs"] = []
        result = plan.prepare_handoff_plan(sample_request(), findings=[], docs=docs)
        rule = next(r for r in result["plan"]["candidates"]["rules"]
                    if r["id"] == "RULE-WIMG-000001")
        self.assertEqual(rule["status"], "review_needed")
        self.assertTrue(any(c["id"] == "authority:RULE-WIMG-000001"
                            for c in result["plan"]["candidates"]["conflicts"]))


class CaseHardGateTests(unittest.TestCase):
    def test_default_case_search_off_rejects_cases(self):
        result = plan.prepare_handoff_plan(
            sample_request(project={"project_id": "app1"}), findings=[],
            registry=app1_fixture.registry_with_live_app1())
        self.assertFalse(result["plan"]["case_search"]["allowed"])
        self.assertEqual(result["plan"]["candidates"]["cases"], [])
        self.assertIn("case_activation_default_off",
                      result["plan"]["hard_filters_applied"])

    def test_case_hard_gate_admits_scenario_match(self):
        result = plan.prepare_handoff_plan(
            app1_case_request(), findings=[],
            registry=app1_fixture.registry_with_live_app1())
        self.assertTrue(result["plan"]["case_search"]["allowed"])
        case_ids = [c["id"] for c in result["plan"]["candidates"]["cases"]]
        self.assertIn("CASE-APP1-000001", case_ids)
        self.assertIn("case_hard_eligibility_gate",
                      result["plan"]["hard_filters_applied"])
        self.assertEqual(validate_plan(result["plan"]), [])

    def test_case_hard_gate_rejects_non_matching_scenario(self):
        req = app1_case_request()
        req["task_snapshot"] = {
            "title": "Purely manual user-initiated recall with no push semantics",
            "description": "non-App1 general notification infrastructure only",
            "requirements": ["manual recall flows"],
            "acceptance_criteria": ["no push"],
            "relevant_decisions": [],
        }
        result = plan.prepare_handoff_plan(
            req, findings=[], registry=app1_fixture.registry_with_live_app1())
        self.assertTrue(result["plan"]["case_search"]["allowed"])
        self.assertEqual(result["plan"]["candidates"]["cases"], [])


class FindingGateTests(unittest.TestCase):
    def test_one_relevant_and_one_irrelevant_finding(self):
        relevant = finding(
            "FIND-WIMG-T01-000001",
            summary="provider grok gpt worktree observation for this implementation",
            intent="observation",
            discovered_by="software-engineer",
        )
        irrelevant = finding(
            "FIND-APP1-T01-000001",
            project_id="app1",
            summary="App1 teacher VOC follow-up reminder hypothesis",
            intent="observation",
            discovered_by="delivery-reviewer",
        )
        store = plan.MemoryFindingStore([relevant, irrelevant])
        result = plan.prepare_handoff_plan(
            sample_request(), findings=[relevant, irrelevant], store=store)
        self.assertEqual(result["status"], "PLAN_READY")
        rel_ids = [f["finding_id"] for f in result["finding_gate"]["relevant_open_findings"]]
        self.assertEqual(rel_ids, ["FIND-WIMG-T01-000001"])
        self.assertNotIn("FIND-APP1-T01-000001", rel_ids)
        self.assertEqual(result["scope_pollution"], 0)
        self.assertFalse(result["finding_gate"]["escalation"]["required"])
        self.assertFalse(result["context_engineer_woken"])

    def test_multiple_findings_only_one_relevant_to_target(self):
        se_obs = finding(
            "FIND-WIMG-T01-000002",
            summary="worktree provider switch note for software engineer",
            intent="observation",
            discovered_by="software-engineer",
        )
        qa_obs = finding(
            "FIND-WIMG-T01-000003",
            summary="qa milestone coverage pytest count for acceptance",
            intent="observation",
            discovered_by="qa",
        )
        other_task = finding(
            "FIND-WIMG-T01-000004",
            task_id="multica://issue/YZT-99",
            summary="provider architecture durable candidate for another task",
            intent="durable_candidate",
        )
        result = plan.prepare_handoff_plan(
            sample_request(), findings=[se_obs, qa_obs, other_task],
            store=plan.MemoryFindingStore([se_obs, qa_obs, other_task]))
        rel_ids = [f["finding_id"] for f in result["finding_gate"]["relevant_open_findings"]]
        self.assertEqual(rel_ids, ["FIND-WIMG-T01-000002"])
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertFalse(result["finding_gate"]["context_engineer_woken"])

    def test_unresolved_material_finding_blocks_plan(self):
        material = finding(
            "FIND-WIMG-T01-000005",
            summary="architecture ownership of provider routing must become a Rule",
            detail="This durable candidate would change canonical provider constraints.",
            intent="durable_candidate",
            verification="unverified",
        )
        result = plan.prepare_handoff_plan(
            sample_request(), findings=[material],
            store=plan.MemoryFindingStore([material]))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIsNone(result["plan"])
        self.assertTrue(result["escalation"]["required"])
        self.assertFalse(result["context_engineer_woken"])
        self.assertEqual(result["finding_gate"]["status"], "BLOCKED")

    def test_revision_refresh_after_processing(self):
        overlay = []

        def revision():
            base = chandoff.memory_revision()
            if overlay:
                return chandoff.sha256_text(base + "".join(overlay))
            return base

        class OverlayMutator:
            def apply(self, finding_doc, disposition):
                if disposition == "carry_to_checkpoint":
                    overlay.append(finding_doc["finding_id"])
                    return {"applied": True, "canonical_changed": True, "block_reason": None}
                if disposition in plan.SAFE_DISPOSITIONS:
                    return {"applied": True, "canonical_changed": False, "block_reason": None}
                return {"applied": False, "canonical_changed": False,
                        "block_reason": "unsafe"}

        material = finding(
            "FIND-WIMG-T01-000006",
            summary="verified checkpoint carry of provider worktree constraint",
            intent="durable_candidate",
            verification="verified",
            disposition="carry_to_checkpoint",
        )
        before = revision()
        result = plan.prepare_handoff_plan(
            sample_request(), findings=[material],
            store=plan.MemoryFindingStore([material]),
            mutator=OverlayMutator(), revision_fn=revision)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(result["finding_gate"]["status"], "REFRESH_REQUIRED")
        self.assertNotEqual(result["memory_revision_before_gate"],
                            result["memory_revision_after_gate"])
        self.assertEqual(result["memory_revision_before_gate"], before)
        self.assertIn("FIND-WIMG-T01-000006", overlay)
        self.assertEqual(validate_plan(result["plan"]), [])

    def test_ordinary_capture_does_not_wake_context_engineer(self):
        store = plan.MemoryFindingStore()
        captured = plan.capture_finding(finding("FIND-WIMG-T01-000007"), store=store)
        self.assertEqual(captured["status"], "open")
        self.assertFalse(captured["context_engineer_run"])
        self.assertFalse(captured["wake_context_engineer"])
        self.assertFalse(captured["canonical_write"])
        self.assertFalse(captured["process_now"])
        result = plan.prepare_handoff_plan(
            sample_request(), findings=store.load_open(), store=store)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertFalse(result["finding_gate"]["escalation"]["required"])
        self.assertFalse(result["finding_gate"]["context_engineer_woken"])
        self.assertFalse(result["context_engineer_woken"])


class FrameworkNeutralPlanTests(unittest.TestCase):
    def test_plan_module_is_in_boundary_scan(self):
        report = chandoff.scan_handoff_contracts()
        self.assertEqual(report, {})
        src = Path(plan.__file__).read_text(encoding="utf-8")
        self.assertEqual(chandoff.forbidden_concept_scan(src), [])


if __name__ == "__main__":
    unittest.main()
