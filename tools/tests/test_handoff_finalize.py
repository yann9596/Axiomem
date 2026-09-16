#!/usr/bin/env python3
"""T03 deterministic Handoff FINALIZE tests (YZT-55)."""
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
import chandoff_finalize as finalize  # noqa: E402
import chandoff_plan as plan  # noqa: E402
from cdata import load_all_docs  # noqa: E402


PKG_S = "context-package.schema.json"
RES_S = "context-handoff/prepare-handoff-result.schema.json"
CLOCK = lambda: "2026-09-09T12:00:00Z"  # noqa: E731


def validate_schema(schema_name: str, instance: dict) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def sample_request(**overrides) -> dict:
    req = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": "multica://issue/YZT-55",
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
                "finalize result is schema valid",
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


def live(request, **kwargs):
    return plan.prepare_handoff_plan(request, findings=kwargs.pop("findings", []), **kwargs)


def accept(envelope, proposed=None):
    if proposed is None:
        proposed = compose.subset_result(envelope["plan"])
    result = compose.compose_semantic(envelope, proposed)
    if result["status"] != "ACCEPTED":
        raise AssertionError(result["errors"])
    return result


def run_finalize(request, envelope, compose_env=None, **kwargs):
    if compose_env is None:
        compose_env = accept(envelope)
    kwargs.setdefault("clock", CLOCK)
    return finalize.finalize_handoff(envelope, compose_env, request, **kwargs)


class CompatibilityTests(unittest.TestCase):
    def test_frozen_t00_can_host_finalize_without_amendment(self):
        report = finalize.frozen_contract_supports_finalize()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["frozen_status_enum_exact"])
        self.assertTrue(report["frozen_package_reuses_context_package_schema"])
        self.assertTrue(report["frozen_ref_grammar_unchanged"])
        self.assertTrue(report["frozen_built_from_required"])


class LivePlanCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.req = sample_request()
        cls.crud = live(cls.req)
        cls.accepted = accept(cls.crud)
        docs = copy.deepcopy(load_all_docs())
        target = next(d for d in docs if d.get("id") == "RULE-WIMG-000001")
        target["status"] = "review_needed"
        cls.conflict_req = sample_request()
        cls.conflicted = live(cls.conflict_req, docs=docs)
        cls.conflict_accepted = accept(cls.conflicted)
        cls.case_req = app1_case_request()
        cls.case_match = live(cls.case_req,
                              registry=app1_fixture.registry_with_live_app1())
        cls.case_accepted = accept(cls.case_match)
        material = {
            "schema_version": "1.1",
            "kind": "finding",
            "finding_id": "FIND-WIMG-T03-000001",
            "project_id": "web-imagegen",
            "task_id": "multica://issue/YZT-55",
            "summary": "architecture ownership of provider routing must become a Rule",
            "detail": "This durable candidate would change canonical provider constraints.",
            "intent": "durable_candidate",
            "source_refs": ["repo://web-imagegen@main/README.md"],
            "discovered_by": "software-engineer",
            "status": "open",
            "verification": "unverified",
            "created_at": "2026-09-09T00:00:00Z",
        }
        cls.blocked_plan = live(
            sample_request(), findings=[material],
            store=plan.MemoryFindingStore([material]),
        )


class StatusMatrixTests(LivePlanCache):
    def test_ready_happy_path(self):
        self.assertEqual(self.crud["status"], "PLAN_READY")
        self.assertFalse(self.crud["plan"]["candidates"]["conflicts"])
        out = run_finalize(self.req, self.crud, self.accepted)
        self.assertEqual(out["status"], "READY", out)
        self.assertFalse(out["escalation"]["required"])
        self.assertEqual(validate_schema(RES_S, out), [])
        self.assertEqual(validate_schema(PKG_S, out["package"]), [])
        self.assertEqual(out["task_ref"], self.req["task_ref"])
        self.assertEqual(out["role"], "software-engineer")
        self.assertEqual(out["package"]["request"]["task_id"], self.req["task_ref"])
        self.assertEqual(out["package"]["request"]["role"], "software-engineer")
        self.assertTrue(out["package"]["rules"] or out["package"]["current_facts"])
        self.assertEqual(out["package"]["open_conflicts"], [])

    def test_partial_keeps_conflicts_visible(self):
        self.assertTrue(self.conflicted["plan"]["candidates"]["conflicts"])
        out = run_finalize(self.conflict_req, self.conflicted, self.conflict_accepted)
        self.assertEqual(out["status"], "PARTIAL", out)
        self.assertFalse(out["escalation"]["required"])
        self.assertTrue(out["package"]["open_conflicts"])
        self.assertTrue(out["package"]["blocked_by"])
        conf_ids = {c["id"] for c in out["package"]["open_conflicts"]}
        plan_ids = {c["id"] for c in self.conflicted["plan"]["candidates"]["conflicts"]}
        self.assertEqual(conf_ids, plan_ids)
        self.assertEqual(validate_schema(RES_S, out), [])
        self.assertEqual(validate_schema(PKG_S, out["package"]), [])

    def test_blocked_requires_escalation(self):
        out = run_finalize(self.req, self.blocked_plan, {
            "schema_version": "1.1",
            "kind": "semantic_compose_validation",
            "status": "REJECTED",
            "plan_id": None,
            "result": None,
            "errors": [],
            "jobs_executed": [],
        })
        self.assertEqual(out["status"], "BLOCKED")
        self.assertTrue(out["escalation"]["required"])
        self.assertEqual(validate_schema(RES_S, out), [])
        self.assertEqual(validate_schema(PKG_S, out["package"]), [])


class InputRejectionTests(LivePlanCache):
    def test_non_accepted_t02_input_rejected(self):
        rejected = compose.compose_semantic(
            self.crud, compose.subset_result(
                self.crud["plan"], selected_rule_ids=["RULE-NOT-IN-PLAN"]))
        self.assertEqual(rejected["status"], "REJECTED")
        out = run_finalize(self.req, self.crud, rejected)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertTrue(out["escalation"]["required"])
        self.assertEqual(out["escalation"]["reason"], "T02_NOT_ACCEPTED")
        raw = run_finalize(self.req, self.crud, compose.subset_result(self.crud["plan"]))
        self.assertEqual(raw["status"], "BLOCKED")
        self.assertEqual(raw["escalation"]["reason"], "T02_NOT_ACCEPTED")

    def test_plan_id_task_ref_role_scope_mismatch(self):
        cases = [
            (dict(self.req, task_ref="multica://issue/YZT-OTHER"), "TASK_REF_MISMATCH"),
            (dict(self.req, target={"role": "qa"}), "ROLE_MISMATCH"),
            (dict(self.req, project={"project_id": "app1"}), "SCOPE_MISMATCH"),
        ]
        for request, code in cases:
            with self.subTest(code=code):
                out = run_finalize(request, self.crud, self.accepted)
                self.assertEqual(out["status"], "BLOCKED")
                self.assertEqual(out["escalation"]["reason"], code)
                self.assertTrue(out["escalation"]["required"])
        bad = copy.deepcopy(self.accepted)
        bad["result"] = copy.deepcopy(self.accepted["result"])
        bad["result"]["plan_id"] = "PLAN-other-role-deadbeef"
        out = run_finalize(self.req, self.crud, bad)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "PLAN_ID_MISMATCH")

    def test_t01_blocked_cannot_finalize(self):
        out = run_finalize(self.req, self.blocked_plan, self.accepted)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "T01_NOT_PLAN_READY")


class InvariantRecheckTests(LivePlanCache):
    def test_selected_id_subset_violation(self):
        forged = compose.subset_result(
            self.crud["plan"], selected_rule_ids=["RULE-NOT-IN-PLAN"])
        # Bypass T02 so FINALIZE itself re-checks the subset relation.
        envelope = {
            "schema_version": "1.1",
            "kind": "semantic_compose_validation",
            "status": "ACCEPTED",
            "plan_id": self.crud["plan"]["plan_id"],
            "result": forged,
            "errors": [],
            "jobs_executed": [],
        }
        out = run_finalize(self.req, self.crud, envelope)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "SELECTED_ID_NOT_IN_PLAN")
        self.assertFalse(any(
            (r.get("ref") or "").endswith("RULE-NOT-IN-PLAN.yaml")
            for r in out["package"]["rules"]))

    def test_missing_invalid_rule_authority(self):
        docs = copy.deepcopy(load_all_docs())
        rid = (self.accepted["result"]["selected_rule_ids"] or [None])[0]
        self.assertTrue(rid)
        target = next(d for d in docs if d.get("id") == rid)
        target["authority_refs"] = []
        out = run_finalize(self.req, self.crud, self.accepted, docs=docs)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "INVALID_RULE_AUTHORITY")

    def test_illegal_verification_and_lifecycle(self):
        docs = copy.deepcopy(load_all_docs())
        fid = (self.accepted["result"]["selected_fact_ids"] or [None])[0]
        self.assertTrue(fid)
        target = next(d for d in docs if d.get("id") == fid)
        target["verification"] = "unverified"
        out = run_finalize(self.req, self.crud, self.accepted, docs=docs)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "ILLEGAL_VERIFICATION")

        docs = copy.deepcopy(load_all_docs())
        target = next(d for d in docs if d.get("id") == fid)
        target["status"] = "superseded"
        out = run_finalize(self.req, self.crud, self.accepted, docs=docs)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "ILLEGAL_LIFECYCLE")

    def test_forbidden_case_and_case_gate_override(self):
        self.assertFalse(self.crud["plan"]["case_search"]["allowed"])
        forged = compose.subset_result(
            self.crud["plan"], selected_case_ids=["CASE-APP1-000001"])
        envelope = {
            "schema_version": "1.1",
            "kind": "semantic_compose_validation",
            "status": "ACCEPTED",
            "plan_id": self.crud["plan"]["plan_id"],
            "result": forged,
            "errors": [],
            "jobs_executed": [],
        }
        out = run_finalize(self.req, self.crud, envelope)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertIn(out["escalation"]["reason"], {
            "CASE_GATE_OVERRIDE", "SELECTED_ID_NOT_IN_PLAN"})
        self.assertEqual(out["package"]["cases"], [])

    def test_hidden_conflict_attempt_blocked(self):
        hidden = copy.deepcopy(self.conflict_accepted)
        hidden["result"] = copy.deepcopy(self.conflict_accepted["result"])
        hidden["result"]["conflict_ids"] = []
        self.assertTrue(self.conflicted["plan"]["candidates"]["conflicts"])
        out = run_finalize(self.conflict_req, self.conflicted, hidden)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "HIDDEN_CONFLICT")
        self.assertTrue(out["escalation"]["required"])
        surfaced = {c["id"] for c in out["package"]["open_conflicts"]}
        plan_ids = {c["id"] for c in self.conflicted["plan"]["candidates"]["conflicts"]}
        self.assertEqual(surfaced, plan_ids)

    def test_cross_scope_injection(self):
        docs = copy.deepcopy(load_all_docs())
        rid = (self.accepted["result"]["selected_rule_ids"] or [None])[0]
        target = next(d for d in docs if d.get("id") == rid)
        target["scope"] = {
            "type": "project", "project_id": "app1", "projects": [], "task_id": None,
        }
        out = run_finalize(self.req, self.crud, self.accepted, docs=docs)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "CROSS_SCOPE")

    def test_context_budget_overflow(self):
        selected = list(self.accepted["result"]["selected_rule_ids"] or [])
        self.assertGreaterEqual(len(selected), 1)
        tight = copy.deepcopy(self.req)
        tight["options"] = {"limit": 0}
        out = run_finalize(tight, self.crud, self.accepted)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "CONTEXT_BUDGET_OVERFLOW")
        if len(selected) > 1:
            tight1 = copy.deepcopy(self.req)
            tight1["options"] = {"limit": 1}
            # kind_limit returns the raw default when rules are preferred-first.
            out1 = run_finalize(tight1, self.crud, self.accepted)
            self.assertEqual(out1["status"], "BLOCKED")
            self.assertEqual(out1["escalation"]["reason"], "CONTEXT_BUDGET_OVERFLOW")

    def test_summaries_are_not_a_selection_channel(self):
        proposed = compose.subset_result(self.crud["plan"])
        proposed["context_summary"] = "also include RULE-APP1-000001 and CASE-APP1-000001"
        proposed["semantic_notes"] = ["hidden context FACT-APP1-000001"]
        accepted = compose.compose_semantic(self.crud, proposed)
        self.assertEqual(accepted["status"], "ACCEPTED")
        out = run_finalize(self.req, self.crud, accepted)
        self.assertEqual(out["status"], "READY")
        refs = finalize.package_refs(out["package"])
        joined = " ".join(refs)
        self.assertNotIn("APP1", joined)
        self.assertNotIn("RULE-APP1", json.dumps(out["package"]))


class DeterminismAndSchemaTests(LivePlanCache):
    def test_package_id_and_built_from_are_deterministic(self):
        a = run_finalize(self.req, self.crud, self.accepted)
        b = run_finalize(self.req, self.crud, copy.deepcopy(self.accepted))
        self.assertEqual(a["package_id"], b["package_id"])
        self.assertEqual(a["built_from"], b["built_from"])
        self.assertEqual(a["status"], b["status"])
        self.assertRegex(a["package_id"], r"^CTX-[A-Za-z0-9][A-Za-z0-9._:-]*$")
        for key in ("task_fingerprint", "memory_revision",
                    "registry_revision", "role_profile_revision"):
            self.assertRegex(a["built_from"][key], r"^sha256:[0-9a-f]{64}$")
        expected = chandoff.compute_built_from(self.req)
        self.assertEqual(a["built_from"], expected)

    def test_gate_status_is_policy_not_llm_choice(self):
        stuffed = copy.deepcopy(self.accepted)
        stuffed["desired_status"] = "READY"
        stuffed["gate_status"] = "READY"
        stuffed["status"] = "ACCEPTED"
        hidden = copy.deepcopy(self.conflict_accepted)
        hidden["desired_status"] = "READY"
        hidden["result"] = copy.deepcopy(self.conflict_accepted["result"])
        hidden["result"]["conflict_ids"] = []
        out = run_finalize(self.conflict_req, self.conflicted, hidden)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["escalation"]["reason"], "HIDDEN_CONFLICT")

    def test_package_and_result_schema_failures_block(self):
        docs = copy.deepcopy(load_all_docs())
        rid = (self.accepted["result"]["selected_rule_ids"] or [None])[0]
        target = next(d for d in docs if d.get("id") == rid)
        target["statement"] = None
        target["modality"] = "not-a-modality"
        out = run_finalize(self.req, self.crud, self.accepted, docs=docs)
        self.assertEqual(out["status"], "BLOCKED")
        self.assertIn(out["escalation"]["reason"], {
            "PACKAGE_SCHEMA_INVALID", "INVALID_RULE_AUTHORITY", "ILLEGAL_LIFECYCLE",
            "ILLEGAL_VERIFICATION",
        })
        self.assertEqual(validate_schema(RES_S, out), [])
        self.assertEqual(validate_schema(PKG_S, out["package"]), [])
        broken = copy.deepcopy(out)
        broken["status"] = "DONE"
        self.assertTrue(validate_schema(RES_S, broken))

    def test_real_builder_ref_grammar_regression(self):
        out = run_finalize(self.req, self.crud, self.accepted)
        self.assertEqual(out["status"], "READY")
        self.assertEqual(validate_schema(PKG_S, out["package"]), [])
        self.assertEqual(validate_schema(RES_S, out), [])
        refs = finalize.package_refs(out["package"])
        self.assertTrue(refs)
        self.assertEqual(finalize.pseudo_scheme_refs(out["package"]), [])
        self.assertEqual(finalize.grammar_invalid_refs(out["package"]), [])
        for ref in refs:
            self.assertRegex(ref, r"^(multica|adr|doc|repo|registry|project|git)://\S+$")
            self.assertFalse(ref.startswith("rule:"))
            self.assertFalse(ref.startswith("fact:"))
            self.assertFalse(ref.startswith("case:"))
        case_out = run_finalize(self.case_req, self.case_match, self.case_accepted)
        self.assertIn(case_out["status"], {"READY", "PARTIAL"})
        self.assertEqual(validate_schema(PKG_S, case_out["package"]), [])
        self.assertEqual(finalize.pseudo_scheme_refs(case_out["package"]), [])
        self.assertTrue(case_out["package"]["cases"])
        for item in case_out["package"]["cases"]:
            self.assertRegex(item["ref"], r"^repo://multica-memory/")
            self.assertFalse(item["ref"].startswith("case:"))


class FindingClosureTests(LivePlanCache):
    def test_find_wimg_ho00_000001_processed_without_canonical_write(self):
        closure = finalize.finding_ho00_000001_closure()
        self.assertEqual(closure["finding_id"], "FIND-WIMG-HO00-000001")
        self.assertEqual(closure["status"], "processed")
        self.assertEqual(closure["disposition"], "absorbed_by_existing")
        self.assertFalse(closure["canonical_write"])
        self.assertFalse(closure["llm_called"])
        finding_doc = {
            "schema_version": "1.1",
            "kind": "finding",
            "finding_id": "FIND-WIMG-HO00-000001",
            "project_id": "web-imagegen",
            "task_id": "multica://issue/YZT-55",
            "summary": "package builder emits rule:/fact:/case: pseudo-scheme refs",
            "intent": "observation",
            "source_refs": ["repo://multica-memory/tools/cbuild.py"],
            "discovered_by": "context-engineer",
            "status": "open",
            "verification": "verified",
            "created_at": "2026-09-09T00:00:00Z",
        }
        store = plan.MemoryFindingStore([finding_doc])
        processed = store.mark_processed(
            finding_doc, closure["disposition"], closure["disposition_note"])
        self.assertEqual(processed["status"], "processed")
        self.assertEqual(store.load_open(), [])
        self.assertEqual(finalize.CANONICAL_WRITES, 0)
        out = run_finalize(self.req, self.crud, self.accepted)
        self.assertEqual(finalize.pseudo_scheme_refs(out["package"]), [])


class ProtocolBoundaryTests(LivePlanCache):
    def test_finalize_never_calls_a_model(self):
        src = inspect.getsource(finalize)
        for needle in ("openai", "anthropic", "litellm", "chat.completions",
                       "invoke_llm", "completion("):
            self.assertNotIn(needle, src)
        self.assertFalse(finalize.LLM_CALLED)
        out = run_finalize(self.req, self.crud, self.accepted)
        self.assertIn(out["status"], {"READY", "PARTIAL", "BLOCKED"})

    def test_finalize_does_not_rerun_finding_gate_or_compose(self):
        src = Path(finalize.__file__).read_text(encoding="utf-8")
        self.assertNotIn("finding_gate(", src)
        self.assertNotIn("import chandoff_plan", src)
        self.assertNotIn("prepare_handoff_plan", src)
        self.assertNotIn("import chandoff_compose", src)
        self.assertNotIn("compose_semantic", src)

    def test_finalize_never_writes_canonical(self):
        src = Path(finalize.__file__).read_text(encoding="utf-8")
        self.assertNotIn(".write_text(", src)
        self.assertNotIn("Path.write", src)
        self.assertEqual(finalize.CANONICAL_WRITES, 0)

    def test_no_multica_runtime_dependencies(self):
        src = Path(finalize.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from multica", src)
        self.assertNotIn("import multica", src)
        self.assertEqual(finalize.MULTICA_RUNTIME_DEPENDENCIES, 0)
        self.assertEqual(chandoff.forbidden_concept_scan(src), [])

    def test_finalize_module_is_in_boundary_scan(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})

    def test_cli_finalize_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plan_file = tmp_path / "plan.json"
            result_file = tmp_path / "result.json"
            request_file = tmp_path / "request.json"
            plan_file.write_text(json.dumps(self.crud), encoding="utf-8")
            result_file.write_text(json.dumps(self.accepted), encoding="utf-8")
            request_file.write_text(json.dumps(self.req), encoding="utf-8")
            out = finalize.finalize_handoff(
                json.loads(plan_file.read_text(encoding="utf-8")),
                json.loads(result_file.read_text(encoding="utf-8")),
                json.loads(request_file.read_text(encoding="utf-8")),
                clock=CLOCK,
            )
            self.assertEqual(out["status"], "READY")
            self.assertEqual(validate_schema(RES_S, out), [])


class DoneCriteriaTests(LivePlanCache):
    def test_done_matrix(self):
        out = run_finalize(self.req, self.crud, self.accepted)
        hidden = copy.deepcopy(self.conflict_accepted)
        hidden["result"] = copy.deepcopy(self.conflict_accepted["result"])
        hidden["result"]["conflict_ids"] = []
        hidden_out = run_finalize(self.conflict_req, self.conflicted, hidden)
        docs = copy.deepcopy(load_all_docs())
        rid = (self.accepted["result"]["selected_rule_ids"] or [None])[0]
        next(d for d in docs if d.get("id") == rid)["authority_refs"] = []
        auth = run_finalize(self.req, self.crud, self.accepted, docs=docs)
        matrix = {
            "invalid_rule_authority": 0 if auth["status"] == "BLOCKED" else 1,
            "hidden_conflict": 0 if hidden_out["status"] == "BLOCKED" else 1,
            "package_schema_valid": validate_schema(PKG_S, out["package"]) == [],
            "result_schema_valid": validate_schema(RES_S, out) == [],
            "task_and_role_bound": (
                out["task_ref"] == self.req["task_ref"]
                and out["role"] == self.req["target"]["role"]
            ),
            "built_from_present": set(out["built_from"]) == {
                "task_fingerprint", "memory_revision",
                "registry_revision", "role_profile_revision",
            },
            "package_ref_grammar_valid": not finalize.grammar_invalid_refs(out["package"]),
            "pseudo_scheme_refs": len(finalize.pseudo_scheme_refs(out["package"])),
            "final_gate_deterministic": (
                run_finalize(self.req, self.crud, self.accepted)["status"] == out["status"]
            ),
            "canonical_writes": finalize.CANONICAL_WRITES,
            "llm_calls": 0 if not finalize.LLM_CALLED else 1,
        }
        self.assertEqual(matrix, finalize.done_criteria())
        self.assertEqual(out["status"], "READY")
        self.assertEqual(auth["escalation"]["reason"], "INVALID_RULE_AUTHORITY")
        self.assertEqual(hidden_out["escalation"]["reason"], "HIDDEN_CONFLICT")


if __name__ == "__main__":
    unittest.main()
