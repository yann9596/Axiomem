#!/usr/bin/env python3
"""T02 bounded Semantic Compose protocol tests (YZT-54)."""
from __future__ import annotations

import copy
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from schema_mini import Schema, load_schema_file  # noqa: E402
import app1_registry_fixture as app1_fixture  # noqa: E402
import chandoff  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_plan as plan  # noqa: E402
from cdata import load_all_docs  # noqa: E402


SEM_S = "context-handoff/semantic-compose-result.schema.json"


def validate_schema(schema_name: str, instance: dict) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def codes(result: dict) -> list:
    return [e["code"] for e in result.get("errors") or []]


def sample_request(**overrides) -> dict:
    req = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": "multica://issue/YZT-54",
        "project": {"project_id": "web-imagegen"},
        "target": {"role": "software-engineer"},
        "purpose": "implementation",
        "task_snapshot": {
            "title": "Implement provider switch CRUD in an isolated worktree",
            "description": (
                "Create, read, update, and delete the Web-ImageGen provider "
                "default|grok|gpt setting. Canonical memory stays with Context "
                "Engineer; product writes stay in a worktree."
            ),
            "requirements": [
                "never clean the product repo",
                "write product code only in a worktree",
                "respect provider neutrality",
            ],
            "acceptance_criteria": [
                "scope pollution is zero",
                "compose result is schema valid",
            ],
            "relevant_decisions": ["reuse frozen Native API contract"],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }
    req.update(overrides)
    return req


def app1_case_request(**overrides) -> dict:
    req = sample_request(
        project={"project_id": "app1"},
        target={"role": "solution-architect"},
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
    req.update(overrides)
    return req


def finding(fid: str, **kwargs) -> dict:
    doc = {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": fid,
        "project_id": "web-imagegen",
        "task_id": "multica://issue/YZT-54",
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


def live(request, **kwargs):
    return plan.prepare_handoff_plan(request, findings=kwargs.pop("findings", []), **kwargs)


class CompatibilityTests(unittest.TestCase):
    def test_frozen_t00_can_host_compose_without_amendment(self):
        report = compose.frozen_contract_supports_compose()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["frozen_semantic_result_schema_present"])
        self.assertTrue(report["frozen_result_requires_plan_id_and_selected_ids"])
        self.assertTrue(report["frozen_result_forbids_additional_properties"])
        self.assertTrue(report["frozen_semantic_jobs_vocabulary"])
        self.assertTrue(report["subset_helper_available"])


class LivePlanCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        live_app1 = app1_fixture.registry_with_live_app1()
        cls.crud = live(sample_request())
        cls.architecture = live(sample_request(purpose="architecture_design"))
        cls.cross = live(sample_request(
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
        ), registry=live_app1)
        docs = copy.deepcopy(load_all_docs())
        target = next(d for d in docs if d.get("id") == "RULE-WIMG-000001")
        target["status"] = "review_needed"
        cls.conflicted = live(sample_request(), docs=docs)
        cls.case_match = live(app1_case_request(), registry=live_app1)
        no_match = app1_case_request()
        no_match["task_snapshot"] = {
            "title": "Purely manual user-initiated recall with no push semantics",
            "description": "non-App1 general notification infrastructure only",
            "requirements": ["manual recall flows"],
            "acceptance_criteria": ["no push"],
            "relevant_decisions": [],
        }
        cls.case_nomatch = live(no_match, registry=live_app1)
        material = finding(
            "FIND-WIMG-T02-000001",
            summary="architecture ownership of provider routing must become a Rule",
            detail="This durable candidate would change canonical provider constraints.",
            intent="durable_candidate",
            verification="unverified",
        )
        cls.blocked = live(
            sample_request(), findings=[material],
            store=plan.MemoryFindingStore([material]),
        )


class TaskMatrixTests(LivePlanCache):
    def _accept(self, envelope, proposed=None, executor=None):
        if proposed is None and executor is None:
            executor = compose.auto_executor()
        result = compose.compose_semantic(envelope, proposed, executor=executor)
        self.assertEqual(result["status"], "ACCEPTED", result["errors"])
        self.assertEqual(validate_schema(SEM_S, result["result"]), [])
        self.assertEqual(
            chandoff.validate_semantic_result(envelope["plan"], result["result"]), [])
        self.assertEqual(result["executor_mode"], "caller_agent_llm_via_skill")
        self.assertFalse(result["llm_called"])
        self.assertEqual(result["canonical_writes"], 0)
        self.assertFalse(result["scope_expanded"])
        self.assertFalse(result["case_gate_overridden"])
        self.assertEqual(result["plan_id"], envelope["plan"]["plan_id"])
        return result

    def test_normal_crud_task(self):
        self.assertEqual(self.crud["status"], "PLAN_READY")
        result = self._accept(self.crud)
        plan_ids = {r["id"] for r in self.crud["plan"]["candidates"]["rules"]}
        for sid in result["result"]["selected_rule_ids"]:
            self.assertIn(sid, plan_ids)

    def test_cross_repo_only_explicitly_admitted_candidates(self):
        self.assertEqual(self.cross["status"], "PLAN_READY")
        self.assertEqual(self.cross["plan"]["scope"]["type"], "cross_project")
        admitted = set()
        for group in ("rules", "facts", "cases"):
            admitted.update(x["id"] for x in self.cross["plan"]["candidates"][group])
        result = self._accept(self.cross)
        selected = (
            result["result"]["selected_rule_ids"]
            + result["result"]["selected_fact_ids"]
            + result["result"]["selected_case_ids"]
        )
        self.assertTrue(selected)
        self.assertTrue(set(selected) <= admitted)
        foreign = compose.subset_result(
            self.cross["plan"], selected_rule_ids=["RULE-NOT-ADMITTED-000001"])
        rejected = compose.compose_semantic(self.cross, foreign)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertIn("SELECTED_ID_NOT_IN_PLAN", codes(rejected))
        self.assertIn("ADD_MEMORY_NOT_IN_PLAN", codes(rejected))

    def test_architecture_task(self):
        self.assertEqual(self.architecture["status"], "PLAN_READY")
        self.assertIn("interpret_task_intent", self.architecture["plan"]["semantic_jobs"])
        result = self._accept(self.architecture)
        self.assertTrue(
            result["result"]["selected_rule_ids"]
            or result["result"]["checkpoint_entry_ids"]
        )

    def test_conflicted_evidence_task(self):
        self.assertEqual(self.conflicted["status"], "PLAN_READY")
        conf_ids = [c["id"] for c in self.conflicted["plan"]["candidates"]["conflicts"]]
        self.assertIn("review:RULE-WIMG-000001", conf_ids)
        result = self._accept(self.conflicted)
        if "phrase_visible_conflicts" in self.conflicted["plan"]["semantic_jobs"]:
            self.assertIn("review:RULE-WIMG-000001", result["result"]["conflict_ids"])
        rule_ids = [r["id"] for r in self.conflicted["plan"]["candidates"]["rules"]]
        self.assertNotIn("RULE-WIMG-000001", rule_ids)
        self.assertNotIn("RULE-WIMG-000001", result["result"]["selected_rule_ids"])

    def test_case_match(self):
        self.assertTrue(self.case_match["plan"]["case_search"]["allowed"])
        case_ids = [c["id"] for c in self.case_match["plan"]["candidates"]["cases"]]
        self.assertIn("CASE-APP1-000001", case_ids)
        result = self._accept(self.case_match)
        self.assertIn("CASE-APP1-000001", result["result"]["selected_case_ids"])

    def test_case_no_match(self):
        self.assertTrue(self.case_nomatch["plan"]["case_search"]["allowed"])
        self.assertEqual(self.case_nomatch["plan"]["candidates"]["cases"], [])
        result = self._accept(self.case_nomatch)
        self.assertEqual(result["result"]["selected_case_ids"], [])
        forged = compose.subset_result(
            self.case_nomatch["plan"], selected_case_ids=["CASE-APP1-000001"])
        rejected = compose.compose_semantic(self.case_nomatch, forged)
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertIn("CASE_GATE_OVERRIDE", codes(rejected))
        self.assertIn("SELECTED_ID_NOT_IN_PLAN", codes(rejected))


class RejectionTests(LivePlanCache):
    def test_wrong_plan_id_rejected(self):
        bad = compose.subset_result(self.crud["plan"], plan_id="PLAN-other-role-deadbeef")
        result = compose.compose_semantic(self.crud, bad)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("PLAN_ID_MISMATCH", codes(result))
        self.assertIsNone(result["result"])

    def test_per_category_selected_id_subset_violations(self):
        mapping = (
            ("selected_rule_ids", "RULE-NOT-IN-PLAN"),
            ("selected_fact_ids", "FACT-NOT-IN-PLAN"),
            ("selected_case_ids", "CASE-NOT-IN-PLAN"),
            ("checkpoint_entry_ids", "cp-not-in-plan"),
            ("conflict_ids", "conflict-not-in-plan"),
        )
        for field, foreign in mapping:
            with self.subTest(field=field):
                bad = compose.subset_result(self.crud["plan"], **{field: [foreign]})
                result = compose.compose_semantic(self.crud, bad)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn("SELECTED_ID_NOT_IN_PLAN", codes(result))
                self.assertIn("ADD_MEMORY_NOT_IN_PLAN", codes(result))
                self.assertTrue(any(field in e["path"] for e in result["errors"]))
                if field == "selected_case_ids":
                    self.assertIn("CASE_GATE_OVERRIDE", codes(result))

    def test_extra_memory_scope_authority_verification_phase_rejected(self):
        base = compose.subset_result(self.crud["plan"])
        cases = [
            ({"result": base, "attempts": ["expand_scope"]}, "EXPAND_SCOPE"),
            ({"result": base, "scope": {"type": "team", "project_id": None,
                                        "projects": [], "task_id": None}},
             "EXPAND_SCOPE"),
            ({"result": base, "attempts": ["invent_authority"]}, "INVENT_AUTHORITY"),
            ({"result": base, "invented_rules": ["RULE-FAKE-000001"]}, "INVENT_AUTHORITY"),
            ({"result": base, "attempts": ["promote_verification"]}, "PROMOTE_VERIFICATION"),
            ({"result": base, "verification_promotions": ["FACT-WIMG-000001"]},
             "PROMOTE_VERIFICATION"),
            ({"result": base, "attempts": ["change_project_phase"]}, "CHANGE_PROJECT_PHASE"),
            ({"result": base, "project_phase": "archived"}, "CHANGE_PROJECT_PHASE"),
            ({"result": compose.subset_result(
                self.crud["plan"], selected_rule_ids=["RULE-ABSENT-000001"])},
             "ADD_MEMORY_NOT_IN_PLAN"),
        ]
        for payload, code in cases:
            with self.subTest(code=code, payload=payload):
                result = compose.compose_semantic(self.crud, payload)
                self.assertEqual(result["status"], "REJECTED")
                self.assertIn(code, codes(result))
                self.assertIsNone(result["result"])

    def test_case_hard_gate_override_rejected(self):
        self.assertFalse(self.crud["plan"]["case_search"]["allowed"])
        self.assertEqual(self.crud["plan"]["candidates"]["cases"], [])
        bad = compose.subset_result(
            self.crud["plan"], selected_case_ids=["CASE-APP1-000001"])
        result = compose.compose_semantic(self.crud, bad)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("CASE_GATE_OVERRIDE", codes(result))
        self.assertTrue(result["case_gate_overridden"])
        envelope = {
            "result": compose.subset_result(self.crud["plan"]),
            "attempts": ["bypass_case_hard_gate"],
        }
        attempted = compose.compose_semantic(self.crud, envelope)
        self.assertEqual(attempted["status"], "REJECTED")
        self.assertIn("CASE_GATE_OVERRIDE", codes(attempted))

    def test_free_form_only_result_rejected(self):
        markdown = "# Context\n\nA free-form package is not allowed."
        as_string = compose.compose_semantic(self.crud, markdown)
        self.assertEqual(as_string["status"], "REJECTED")
        self.assertIn("FREE_FORM_ONLY", codes(as_string))
        as_dict = compose.compose_semantic(self.crud, {"markdown": markdown})
        self.assertEqual(as_dict["status"], "REJECTED")
        self.assertIn("FREE_FORM_ONLY", codes(as_dict))
        missing = {
            "schema_version": "1.1",
            "kind": "semantic_compose_result",
            "plan_id": self.crud["plan"]["plan_id"],
            "markdown": markdown,
        }
        schema_hit = compose.compose_semantic(self.crud, missing)
        self.assertEqual(schema_hit["status"], "REJECTED")
        self.assertIn("SCHEMA_INVALID", codes(schema_hit))

    def test_t01_blocked_input_cannot_compose(self):
        self.assertEqual(self.blocked["status"], "BLOCKED")
        self.assertIsNone(self.blocked["plan"])
        recorder = compose.FixtureExecutor(lambda p: compose.subset_result(p))
        result = compose.compose_semantic(self.blocked, executor=recorder)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("T01_NOT_PLAN_READY", codes(result))
        self.assertEqual(recorder.calls, 0)
        self.assertIsNone(result["result"])

    def test_jobs_outside_plan_vocabulary_rejected(self):
        payload = {
            "result": compose.subset_result(self.crud["plan"]),
            "jobs_executed": ["rewrite_all_memory", "interpret_task_intent"],
        }
        result = compose.compose_semantic(self.crud, payload)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("JOB_NOT_IN_VOCABULARY", codes(result))

    def test_job_in_vocabulary_but_not_listed_on_plan_rejected(self):
        jobs = [j for j in self.crud["plan"]["semantic_jobs"]
                if j != "perform_case_scenario_match_when_case_search_already_allowed"]
        self.assertNotIn(
            "perform_case_scenario_match_when_case_search_already_allowed", jobs)
        payload = {
            "result": compose.subset_result(self.crud["plan"]),
            "jobs_executed": jobs + [
                "perform_case_scenario_match_when_case_search_already_allowed"],
        }
        result = compose.compose_semantic(self.crud, payload)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("JOB_NOT_IN_PLAN", codes(result))


class DeterminismAndBoundsTests(LivePlanCache):
    def test_validation_is_deterministic(self):
        proposed = compose.subset_result(self.crud["plan"])
        a = compose.compose_semantic(self.crud, proposed)
        b = compose.compose_semantic(self.crud, copy.deepcopy(proposed))
        self.assertEqual(a, b)
        bad = compose.subset_result(
            self.crud["plan"], selected_rule_ids=["RULE-X", "RULE-Y"])
        c = compose.compose_semantic(self.crud, bad)
        d = compose.compose_semantic(self.crud, copy.deepcopy(bad))
        self.assertEqual(c, d)
        self.assertEqual(c["errors"], sorted(
            c["errors"], key=lambda e: (e["code"], e["path"], e["message"])))

    def test_bounded_error_reporting(self):
        foreign = [f"RULE-EXTRA-{i:06d}" for i in range(40)]
        bad = compose.subset_result(self.crud["plan"], selected_rule_ids=foreign)
        payload = {
            "result": bad,
            "attempts": list(compose.FORBIDDEN_ATTEMPTS),
            "canonical_writes": 3,
            "finding_gate_rerun": True,
            "project_phase": "archived",
            "invented_rules": ["RULE-FAKE"],
            "verification_promotions": ["FACT-FAKE"],
        }
        result = compose.compose_semantic(self.crud, payload)
        self.assertEqual(result["status"], "REJECTED")
        self.assertLessEqual(len(result["errors"]), compose.MAX_ERRORS)
        self.assertEqual(len(result["errors"]), compose.MAX_ERRORS)
        ordered = sorted(
            result["errors"], key=lambda e: (e["code"], e["path"], e["message"]))
        self.assertEqual(result["errors"], ordered)

    def test_injected_executor_seam_is_used(self):
        recorder = compose.auto_executor()
        result = compose.compose_semantic(self.crud, executor=recorder)
        self.assertEqual(recorder.calls, 1)
        self.assertEqual(result["status"], "ACCEPTED")

    def test_raw_plan_is_accepted_as_plan_ready(self):
        result = compose.compose_semantic(
            self.crud["plan"], compose.subset_result(self.crud["plan"]))
        self.assertEqual(result["status"], "ACCEPTED")


class ProtocolBoundaryTests(LivePlanCache):
    def test_protocol_never_calls_a_model(self):
        src = inspect.getsource(compose)
        for needle in ("openai", "anthropic", "litellm", "chat.completions",
                       "invoke_llm", "completion("):
            self.assertNotIn(needle, src)
        self.assertFalse(compose.LLM_CALLED)
        self.assertEqual(compose.EXECUTOR_MODE, "caller_agent_llm_via_skill")

    def test_protocol_does_not_rerun_finding_gate_or_import_plan_builder(self):
        src = Path(compose.__file__).read_text(encoding="utf-8")
        self.assertNotIn("finding_gate(", src)
        self.assertNotIn("import chandoff_plan", src)
        self.assertNotIn("prepare_handoff_plan", src)
        payload = {
            "result": compose.subset_result(self.crud["plan"]),
            "finding_gate_rerun": True,
        }
        result = compose.compose_semantic(self.crud, payload)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("RERUN_FINDING_GATE", codes(result))

    def test_protocol_never_writes_canonical(self):
        src = Path(compose.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".write_text(", src)
        self.assertNotIn("Path.write", src)
        self.assertEqual(compose.CANONICAL_WRITES, 0)
        payload = {
            "result": compose.subset_result(self.crud["plan"]),
            "canonical_writes": 1,
        }
        result = compose.compose_semantic(self.crud, payload)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("WRITE_CANONICAL", codes(result))
        self.assertEqual(result["canonical_writes"], 0)

    def test_no_multica_runtime_dependencies(self):
        src = Path(compose.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from multica", src)
        self.assertNotIn("import multica", src)
        self.assertEqual(compose.MULTICA_RUNTIME_DEPENDENCIES, 0)
        self.assertEqual(chandoff.forbidden_concept_scan(src), [])

    def test_compose_module_is_in_boundary_scan(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})

    def test_cli_validate_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan_file = tmp_path / "plan.json"
            result_file = tmp_path / "result.json"
            plan_file.write_text(json.dumps(self.crud), encoding="utf-8")
            result_file.write_text(
                json.dumps(compose.subset_result(self.crud["plan"])), encoding="utf-8")
            accepted = compose.compose_semantic(
                json.loads(plan_file.read_text(encoding="utf-8")),
                json.loads(result_file.read_text(encoding="utf-8")),
            )
            self.assertEqual(accepted["status"], "ACCEPTED")


class DoneCriteriaTests(LivePlanCache):
    def test_done_matrix(self):
        accepted = compose.compose_semantic(
            self.crud, compose.subset_result(self.crud["plan"]))
        schema_errs = validate_schema(SEM_S, accepted["result"])
        subset_errs = chandoff.validate_semantic_result(
            self.crud["plan"], accepted["result"])
        expanded = compose.compose_semantic(self.crud, {
            "result": compose.subset_result(self.crud["plan"]),
            "attempts": ["expand_scope"],
        })
        override = compose.compose_semantic(self.crud, compose.subset_result(
            self.crud["plan"], selected_case_ids=["CASE-APP1-000001"]))
        matrix = {
            "output_schema_enforced": (
                accepted["status"] == "ACCEPTED" and not schema_errs),
            "selected_ids_subset_of_plan": not subset_errs,
            "scope_cannot_expand": expanded["status"] == "REJECTED",
            "case_gate_cannot_be_overridden": override["status"] == "REJECTED",
            "canonical_writes": accepted["canonical_writes"],
            "multica_runtime_dependencies": compose.MULTICA_RUNTIME_DEPENDENCIES,
        }
        self.assertEqual(matrix, compose.done_criteria())
        self.assertTrue(matrix["output_schema_enforced"])
        self.assertTrue(matrix["selected_ids_subset_of_plan"])
        self.assertTrue(matrix["scope_cannot_expand"])
        self.assertTrue(matrix["case_gate_cannot_be_overridden"])
        self.assertEqual(matrix["canonical_writes"], 0)
        self.assertEqual(matrix["multica_runtime_dependencies"], 0)


if __name__ == "__main__":
    unittest.main()
