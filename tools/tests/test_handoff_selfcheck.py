#!/usr/bin/env python3
"""T04 deterministic SELF_CHECK tests (YZT-57)."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from schema_mini import Schema, load_schema_file  # noqa: E402
import chandoff  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as sc  # noqa: E402
from cdata import load_all_docs  # noqa: E402
from cutil import now_iso  # noqa: E402

REQ_S = "context-handoff/self-check-request.schema.json"
RES_S = "context-handoff/self-check-result.schema.json"
CLOCK = lambda: "2026-09-09T12:00:00Z"  # noqa: E731


def validate_schema(schema_name: str, instance: dict) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def sample_request(**overrides) -> dict:
    req = {
        "schema_version": "1.1",
        "kind": "self_check_request",
        "task_ref": "multica://issue/YZT-55",
        "role": "software-engineer",
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
    }
    req.update(overrides)
    return req


def prepare_envelope(**overrides) -> dict:
    """Build a real READY prepare_handoff_result through T01->T02->T03."""
    preq = {
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
    preq.update(overrides)
    env = plan.prepare_handoff_plan(preq)
    accepted = compose.compose_semantic(env, compose.subset_result(env["plan"]))
    if accepted["status"] != "ACCEPTED":
        raise AssertionError(accepted["errors"])
    out = finalize.finalize_handoff(env, accepted, preq, clock=CLOCK)
    if out["status"] != "READY":
        raise AssertionError(out)
    return out


def self_consistent(envelope: dict) -> dict:
    """Current revisions pinned to the envelope's own provenance."""
    return dict(envelope["built_from"])


class CompatibilityTests(unittest.TestCase):
    def test_frozen_t00_can_host_self_check_without_amendment(self):
        report = sc.frozen_contract_supports_self_check()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["frozen_reason_vocabulary_unchanged"])
        self.assertTrue(report["frozen_status_enum_unchanged"])
        self.assertTrue(report["frozen_action_enum_unchanged"])
        self.assertTrue(report["frozen_fingerprint_inputs_unchanged"])
        self.assertTrue(report["frozen_self_check_request_shape"])
        self.assertTrue(report["request_has_no_project_field"])

    def test_reason_vocabulary_matches_chandoff(self):
        self.assertEqual(
            set(sc._reason_enum(
                sc.chandoff_canonical_schema(RES_S))),
            set(chandoff.SELF_CHECK_REASONS))

    def test_done_criteria_declared(self):
        self.assertEqual(
            sc.done_criteria(),
            {
                "normal_check_requires_llm": False,
                "missing_package_detected": True,
                "role_mismatch_detected": True,
                "changed_task_detected": True,
                "stale_package_detected": True,
            })

    def test_no_llm_no_writes(self):
        self.assertFalse(sc.LLM_CALLED)
        self.assertEqual(sc.CANONICAL_WRITES, 0)
        self.assertEqual(sc.MULTICA_RUNTIME_DEPENDENCIES, 0)


class HappyPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.request = sample_request()

    def test_ready_uses_existing(self):
        out = sc.self_check(self.request, packages=[self.envelope],
                            current=self_consistent(self.envelope))
        self.assertEqual(out["status"], "READY")
        self.assertEqual(out["action"], "USE_EXISTING")
        self.assertEqual(out["reasons"], [])
        self.assertEqual(out["package_id"], self.envelope["package_id"])
        self.assertEqual(validate_schema(RES_S, out), [])

    def test_deterministic_repeats(self):
        a = sc.self_check(self.request, packages=[self.envelope],
                          current=self_consistent(self.envelope))
        b = sc.self_check(self.request, packages=[self.envelope],
                          current=self_consistent(self.envelope))
        self.assertEqual(a, b)

    def test_parent_task_ref_and_list_order_do_not_invalidate(self):
        """Chatter and non-definition fields never enter the fingerprint."""
        chatty = sample_request()
        chatty["task_snapshot"] = dict(chatty["task_snapshot"],
                                       parent_task_ref="multica://issue/YZT-39")
        reordered = sample_request()
        reordered["task_snapshot"] = dict(reordered["task_snapshot"])
        reordered["task_snapshot"]["requirements"] = list(
            reversed(reordered["task_snapshot"]["requirements"]))
        for variant in (chatty, reordered):
            out = sc.self_check(variant, packages=[self.envelope],
                                current=self_consistent(self.envelope))
            self.assertEqual(out["status"], "READY", out)
            self.assertEqual(out["reasons"], [])

    def test_live_revisions_match_helpers(self):
        """A package built this session stays READY under live revisions."""
        out = sc.self_check(self.request, packages=[self.envelope])
        self.assertEqual(out["status"], "READY", out)


class MissingPackageTests(unittest.TestCase):
    def test_no_candidates_reports_missing(self):
        out = sc.self_check(sample_request(), packages=[])
        self.assertEqual(out["status"], "REFRESH_REQUIRED")
        self.assertEqual(out["action"], "REFRESH")
        self.assertEqual(out["reasons"], ["package_missing"])
        self.assertNotIn("package_id", out)
        self.assertEqual(validate_schema(RES_S, out), [])

    def test_unknown_package_ref_reports_missing(self):
        out = sc.self_check(sample_request(package_ref="CTX-nobody-000000"),
                            packages=[prepare_envelope()])
        self.assertEqual(out["status"], "REFRESH_REQUIRED")
        self.assertEqual(out["reasons"], ["package_missing"])

    def test_request_schema_rejects_unknown_fields(self):
        bad = sample_request(project={"project_id": "app1"})
        with self.assertRaises(ValueError):
            sc.self_check(bad)


class StalenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.request = sample_request()

    def test_memory_revision_changed(self):
        cur = self_consistent(self.envelope)
        cur["memory_revision"] = "sha256:" + "0" * 64
        out = sc.self_check(self.request, packages=[self.envelope], current=cur)
        self.assertEqual(out["status"], "REFRESH_REQUIRED")
        self.assertIn("memory_revision_changed", out["reasons"])

    def test_registry_and_role_profile_revisions_changed(self):
        cur = self_consistent(self.envelope)
        cur["registry_revision"] = "sha256:" + "1" * 64
        cur["role_profile_revision"] = "sha256:" + "2" * 64
        out = sc.self_check(self.request, packages=[self.envelope], current=cur)
        self.assertIn("registry_revision_changed", out["reasons"])
        self.assertIn("role_profile_revision_changed", out["reasons"])

    def test_task_changed_on_definition_change(self):
        changed = sample_request()
        changed["task_snapshot"] = dict(changed["task_snapshot"],
                                        title="A different task definition")
        out = sc.self_check(changed, packages=[self.envelope],
                            current=self_consistent(self.envelope))
        self.assertEqual(out["status"], "REFRESH_REQUIRED")
        self.assertIn("task_changed", out["reasons"])

    def test_missing_provenance_is_stale(self):
        broken = copy.deepcopy(self.envelope)
        del broken["built_from"]["memory_revision"]
        out = sc.self_check(self.request, packages=[broken],
                            current=self_consistent(self.envelope))
        self.assertIn("package_stale", out["reasons"])
        self.assertEqual(out["status"], "REFRESH_REQUIRED")

    def test_reasons_dedupe_and_freeze_order(self):
        cur = self_consistent(self.envelope)
        cur["memory_revision"] = "sha256:" + "3" * 64
        cur["registry_revision"] = "sha256:" + "4" * 64
        changed = sample_request()
        changed["task_snapshot"] = dict(changed["task_snapshot"],
                                        title="Changed definition again")
        out = sc.self_check(changed, packages=[self.envelope], current=cur)
        frozen = list(chandoff.SELF_CHECK_REASONS)
        self.assertEqual(out["reasons"], sorted(set(out["reasons"]), key=frozen.index))
        self.assertIn("task_changed", out["reasons"])
        self.assertIn("memory_revision_changed", out["reasons"])
        self.assertIn("registry_revision_changed", out["reasons"])


class RoleTaskMismatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()

    def test_explicit_ref_role_mismatch(self):
        out = sc.self_check(
            sample_request(role="qa", package_ref=self.envelope["package_id"]),
            packages=[self.envelope], current=self_consistent(self.envelope))
        self.assertIn("role_mismatch", out["reasons"])
        self.assertEqual(out["status"], "REFRESH_REQUIRED")
        self.assertEqual(out["package_id"], self.envelope["package_id"])

    def test_no_ref_wrong_role_is_package_missing(self):
        """Without package_ref the resolution is strictly task_ref+role."""
        out = sc.self_check(sample_request(role="qa"), packages=[self.envelope])
        self.assertEqual(out["reasons"], ["package_missing"])
        self.assertEqual(out["action"], "REFRESH")

    def test_explicit_ref_to_other_task_reports_task_changed(self):
        other = prepare_envelope(
            task_ref="multica://issue/YZT-OTHER",
            task_snapshot={
                "title": "Other task", "description": "",
                "requirements": [], "acceptance_criteria": [],
                "relevant_decisions": [],
            },
        )
        out = sc.self_check(sample_request(package_ref=other["package_id"]),
                            packages=[other],
                            current=self_consistent(other))
        self.assertIn("task_changed", out["reasons"])
        self.assertEqual(out["package_id"], other["package_id"])


class PackageStatusTests(unittest.TestCase):
    def test_partial_requires_refresh(self):
        preq = {
            "schema_version": "1.1",
            "kind": "prepare_handoff_request",
            "task_ref": "multica://issue/YZT-55",
            "project": {"project_id": "web-imagegen"},
            "target": {"role": "software-engineer"},
            "purpose": "implementation",
            "task_snapshot": {
                "title": "Partial-context probe",
                "description": "Conflict visibility probe for PARTIAL handling.",
                "requirements": ["provider switch"],
                "acceptance_criteria": ["conflicts visible"],
                "relevant_decisions": [],
            },
            "caller": {"role": "engineering-lead"},
            "options": {"limit": 8},
        }
        docs = copy.deepcopy(load_all_docs())
        target = next(d for d in docs if d.get("id") == "RULE-WIMG-000001")
        target["status"] = "review_needed"
        env = plan.prepare_handoff_plan(preq, docs=docs)
        accepted = compose.compose_semantic(env, compose.subset_result(env["plan"]))
        out = finalize.finalize_handoff(env, accepted, preq, clock=CLOCK)
        self.assertEqual(out["status"], "PARTIAL")
        result = sc.self_check(sample_request(), packages=[out],
                               current=self_consistent(out))
        self.assertEqual(result["status"], "REFRESH_REQUIRED")
        self.assertIn("package_not_ready", result["reasons"])
        self.assertEqual(validate_schema(RES_S, result), [])

    def test_blocked_package_escalates(self):
        preq = {
            "schema_version": "1.1",
            "kind": "prepare_handoff_request",
            "task_ref": "multica://issue/YZT-55",
            "project": {"project_id": "web-imagegen"},
            "target": {"role": "software-engineer"},
            "purpose": "implementation",
            "task_snapshot": {
                "title": "Blocked probe", "description": "d",
                "requirements": ["r"], "acceptance_criteria": ["a"],
                "relevant_decisions": [],
            },
            "caller": {"role": "engineering-lead"},
        }
        env = plan.prepare_handoff_plan(preq)
        rejected = {
            "schema_version": "1.1",
            "kind": "semantic_compose_validation",
            "status": "REJECTED", "plan_id": None, "result": None,
            "errors": [], "jobs_executed": [],
        }
        blocked = finalize.finalize_handoff(env, rejected, preq, clock=CLOCK)
        self.assertEqual(blocked["status"], "BLOCKED")
        result = sc.self_check(sample_request(), packages=[blocked],
                               current=self_consistent(blocked))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["action"], "ESCALATE")
        self.assertIn("package_not_ready", result["reasons"])
        self.assertEqual(validate_schema(RES_S, result), [])


class ScopeRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.request = sample_request()

    def test_unregistered_project_scope_mismatch(self):
        registry = {"projects": [{"id": "app1", "phase": "incubation"}]}
        out = sc.self_check(self.request, packages=[self.envelope],
                            registry=registry,
                            current=self_consistent(self.envelope))
        self.assertIn("scope_mismatch", out["reasons"])

    def test_archived_project_scope_mismatch(self):
        registry = {"projects": [
            {"id": "web-imagegen", "phase": "archived"},
            {"id": "app1", "phase": "incubation"},
        ]}
        out = sc.self_check(self.request, packages=[self.envelope],
                            registry=registry,
                            current=self_consistent(self.envelope))
        self.assertIn("scope_mismatch", out["reasons"])

    def test_cross_project_fingerprint_fails_closed_as_stale(self):
        env = copy.deepcopy(self.envelope)
        env["package"]["scope"] = {"type": "cross_project", "project_id": None,
                                   "projects": ["app1", "web-imagegen"],
                                   "task_id": None}
        out = sc.self_check(self.request, packages=[env],
                            current=self_consistent(env))
        self.assertIn("package_stale", out["reasons"])
        self.assertEqual(out["status"], "REFRESH_REQUIRED")


class StoreResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.older = copy.deepcopy(cls.envelope)
        cls.older["generated_at"] = "2026-09-09T00:00:00Z"
        cls.envelope["generated_at"] = "2026-09-09T12:00:00Z"

    def _store(self, tmp, *envelopes, junk=False):
        d = Path(tmp) / "handoff-packages"
        d.mkdir(parents=True, exist_ok=True)
        for i, env in enumerate(envelopes):
            (d / f"{env['package_id']}.json").write_text(
                json.dumps(env, ensure_ascii=False), encoding="utf-8")
        if junk:
            (d / "junk.txt").write_text("not json", encoding="utf-8")
            (d / "bad.json").write_text("{broken", encoding="utf-8")
        return str(d)

    def test_store_resolves_latest_for_task_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, self.older, self.envelope, junk=True)
            out = sc.self_check(sample_request(), store_dir=store,
                                current=self_consistent(self.envelope))
            self.assertEqual(out["status"], "READY", out)
            self.assertEqual(out["package_id"], self.envelope["package_id"])

    def test_package_ref_by_id_picks_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, self.older, self.envelope)
            out = sc.self_check(
                sample_request(package_ref=self.older["package_id"]),
                store_dir=store, current=self_consistent(self.older))
            self.assertEqual(out["package_id"], self.older["package_id"])
            self.assertEqual(out["status"], "READY", out)

    def test_package_ref_by_locator(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, self.envelope)
            ref = f"{self.envelope['package_id']}.json"
            out = sc.self_check(sample_request(package_ref=ref),
                                store_dir=store,
                                current=self_consistent(self.envelope))
            self.assertEqual(out["status"], "READY", out)


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()

    def _run_cli(self, args):
        return subprocess.run(
            [sys.executable, str(TOOLS / "context_cli.py"), "self-check", *args],
            capture_output=True, text=True, cwd=str(TOOLS.parent))

    def test_cli_ready_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "req.json"
            pkg = Path(tmp) / "pkg.json"
            req.write_text(json.dumps(sample_request()), encoding="utf-8")
            pkg.write_text(json.dumps(self.envelope), encoding="utf-8")
            proc = self._run_cli(["--request-file", str(req),
                                  "--package", str(pkg)])
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "READY")
            self.assertEqual(proc.returncode, 0)

    def test_cli_refresh_exit_two_and_blocked_exit_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "req.json"
            req.write_text(json.dumps(sample_request()), encoding="utf-8")
            proc = self._run_cli(["--request-file", str(req)])
            self.assertEqual(json.loads(proc.stdout)["status"], "REFRESH_REQUIRED")
            self.assertEqual(proc.returncode, 2)
            blocked = dict(self.envelope)
            blocked["status"] = "BLOCKED"
            pkg = Path(tmp) / "blocked.json"
            pkg.write_text(json.dumps(blocked), encoding="utf-8")
            proc = self._run_cli(["--request-file", str(req),
                                  "--package", str(pkg)])
            self.assertEqual(json.loads(proc.stdout)["status"], "BLOCKED")
            self.assertEqual(proc.returncode, 3)


class ControlledMutator:
    """Controlled injected mutator: proves only that a safely-applied
    Canonical-changing disposition maps to REFRESH (supplement §7.1).
    It never bypasses governance: unsafe dispositions stay blocked."""

    def apply(self, finding_doc, disposition):
        if disposition == "carry_to_checkpoint":
            return {"applied": True, "canonical_changed": True,
                    "block_reason": None}
        if disposition in plan.SAFE_DISPOSITIONS:
            return {"applied": True, "canonical_changed": False,
                    "block_reason": None}
        return {"applied": False, "canonical_changed": False,
                "block_reason": "unsafe_in_controlled_test"}


class FindingGateSelfCheckTests(unittest.TestCase):
    """T04B corrective delta (YZT-56): current-role Finding Gate inside
    SELF_CHECK (Runtime Finding Processing Trigger supplement §7.1 / §19)."""

    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.request = sample_request()
        cls.consistent = dict(cls.envelope["built_from"])

    def _store(self, *findings) -> "plan.MemoryFindingStore":
        return plan.MemoryFindingStore([copy.deepcopy(f) for f in findings])

    def test_frozen_contract_supports_current_role_finding_gate(self):
        report = sc.frozen_contract_supports_self_check()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["frozen_self_check_contract_can_support_finding_gate"])

    def test_no_open_findings_is_clear_and_ready(self):
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[])
        self.assertEqual(trace["result"]["status"], "READY")
        self.assertEqual(trace["result"]["reasons"], [])
        self.assertTrue(trace["gate_ran"])
        self.assertEqual(trace["finding_gate"]["status"], "CLEAR")
        self.assertEqual(trace["finding_gate"]["relevant_open_findings"], [])
        self.assertFalse(trace["context_engineer_woken"])
        self.assertEqual(trace["scope_pollution_from_findings"], 0)

    def test_task_unrelated_finding_is_not_context(self):
        other_task = finding(
            "FIND-WIMG-T56-000001",
            task_id="multica://issue/YZT-99",
            summary="provider architecture durable candidate for another task",
            intent="durable_candidate",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[other_task])
        self.assertEqual(trace["result"]["status"], "READY")
        self.assertEqual(trace["finding_gate"]["relevant_open_findings"], [])
        self.assertFalse(trace["finding_gate"]["escalation"]["required"])
        self.assertFalse(trace["context_engineer_woken"])

    def test_role_unrelated_finding_is_ignored(self):
        qa_obs = finding(
            "FIND-WIMG-T56-000002",
            summary="qa milestone coverage pytest count",
            intent="observation",
            discovered_by="qa",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[qa_obs])
        self.assertEqual(trace["result"]["status"], "READY")
        self.assertEqual(trace["result"]["reasons"], [])
        self.assertEqual(trace["finding_gate"]["relevant_open_findings"], [])
        self.assertFalse(trace["context_engineer_woken"])

    def test_safe_processing_with_unchanged_revision_stays_ready(self):
        obs = finding(
            "FIND-WIMG-T56-000003",
            summary="worktree pytest coverage count note",
            intent="observation",
            verification="verified",
            discovered_by="software-engineer",
        )
        store = self._store(obs)
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=None, finding_store=store)
        self.assertEqual(trace["result"]["status"], "READY", trace["result"])
        self.assertEqual(trace["result"]["action"], "USE_EXISTING")
        self.assertEqual(trace["result"]["reasons"], [])
        gate = trace["finding_gate"]
        self.assertEqual(gate["status"], "CLEAR")
        self.assertFalse(gate["canonical_changed"])
        processed = [f["finding_id"] for f in gate["processed_findings"]]
        self.assertEqual(processed, ["FIND-WIMG-T56-000003"])
        self.assertEqual(store.load_open(), [])
        # injected T01 store seam: the processed status persists there
        saved = store._items[0]
        self.assertEqual(saved["status"], "processed")
        self.assertEqual(saved["disposition"], "pointer")
        self.assertFalse(gate["escalation"]["required"])
        self.assertFalse(gate["context_engineer_woken"])

    def test_controlled_revision_change_requires_refresh(self):
        material = finding(
            "FIND-WIMG-T56-000004",
            summary="verified provider worktree constraint carried to checkpoint",
            detail="This verified constraint belongs in the project checkpoint.",
            intent="durable_candidate",
            verification="verified",
            disposition="carry_to_checkpoint",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[material], mutator=ControlledMutator())
        result = trace["result"]
        self.assertEqual(result["status"], "REFRESH_REQUIRED")
        self.assertEqual(result["action"], "REFRESH")
        self.assertEqual(result["reasons"], ["memory_revision_changed"])
        self.assertEqual(validate_schema(RES_S, result), [])
        gate = trace["finding_gate"]
        self.assertEqual(gate["status"], "REFRESH_REQUIRED")
        self.assertTrue(gate["canonical_changed"])
        self.assertFalse(gate["escalation"]["required"])
        self.assertFalse(gate["context_engineer_woken"])

    def test_unverified_durable_finding_blocks_and_escalates(self):
        material = finding(
            "FIND-WIMG-T56-000005",
            summary="architecture ownership of provider routing must become a Rule",
            detail="This unverified durable candidate would change canonical constraints.",
            intent="durable_candidate",
            verification="unverified",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[material])
        result = trace["result"]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["action"], "ESCALATE")
        self.assertEqual(result["reasons"], ["package_not_ready"])
        self.assertEqual(validate_schema(RES_S, result), [])
        gate = trace["finding_gate"]
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertTrue(gate["escalation"]["required"])
        self.assertEqual(gate["escalation"]["reason"], "authority_gap")
        self.assertFalse(gate["context_engineer_woken"])

    def test_context_challenge_conflict_blocks_and_escalates(self):
        challenge = finding(
            "FIND-WIMG-T56-000006",
            summary="context challenge: recorded provider fact conflicts with repo",
            detail="The current_fact CONTRADICTION-001 evidence conflicts with the repo.",
            intent="context_challenge",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[challenge])
        result = trace["result"]
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["action"], "ESCALATE")
        self.assertEqual(result["reasons"], ["package_not_ready"])
        self.assertEqual(validate_schema(RES_S, result), [])
        gate = trace["finding_gate"]
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertTrue(gate["escalation"]["required"])
        self.assertEqual(gate["escalation"]["reason"], "evidence_conflict")
        self.assertFalse(gate["context_engineer_woken"])

    def test_multiple_findings_only_one_relevant(self):
        relevant = finding(
            "FIND-WIMG-T56-000007",
            summary="provider worktree note for this software engineer",
            intent="observation",
            discovered_by="software-engineer",
        )
        qa_obs = finding(
            "FIND-WIMG-T56-000008",
            summary="qa milestone coverage pytest count",
            intent="observation",
            discovered_by="qa",
        )
        other_task = finding(
            "FIND-WIMG-T56-000009",
            task_id="multica://issue/YZT-99",
            summary="provider durable candidate for another task",
            intent="durable_candidate",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[relevant, qa_obs, other_task])
        gate = trace["finding_gate"]
        rel_ids = [f["finding_id"] for f in gate["relevant_open_findings"]]
        self.assertEqual(rel_ids, ["FIND-WIMG-T56-000007"])
        self.assertEqual(trace["result"]["status"], "READY")
        self.assertFalse(gate["escalation"]["required"])
        self.assertFalse(gate["context_engineer_woken"])

    def test_cross_scope_finding_pollution_is_zero(self):
        app1_finding = finding(
            "FIND-APP1-T56-000001",
            project_id="app1",
            summary="App1 teacher VOC follow-up reminder hypothesis",
            intent="observation",
            discovered_by="software-engineer",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[app1_finding])
        self.assertEqual(trace["result"]["status"], "READY")
        self.assertEqual(trace["finding_gate"]["relevant_open_findings"], [])
        self.assertEqual(trace["scope_pollution_from_findings"], 0)

    def test_gate_boundary_is_exactly_current_role(self):
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[])
        self.assertEqual(sc.GATE_BOUNDARY, "current_role")
        self.assertEqual(trace["finding_gate"]["boundary"], "current_role")
        gate_req = sc.internal_gate_request(self.request)
        self.assertEqual(gate_req["target"]["role"], self.request["role"])
        self.assertEqual(gate_req["task_ref"], self.request["task_ref"])

    def test_gate_diagnostics_stay_out_of_frozen_result(self):
        material = finding(
            "FIND-WIMG-T56-000005",
            summary="architecture ownership of provider routing must become a Rule",
            intent="durable_candidate",
            verification="unverified",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=[material])
        result = trace["result"]
        self.assertLessEqual(
            set(result),
            {"schema_version", "kind", "status", "reasons", "action",
             "package_id"})
        self.assertTrue(set(result["reasons"]).issubset(set(chandoff.SELF_CHECK_REASONS)))
        self.assertNotIn("FIND-WIMG-T56-000005", json.dumps(result))
        self.assertNotIn("authority_gap", json.dumps(result))
        # diagnostics survive only in the internal trace
        self.assertEqual(trace["finding_gate"]["escalation"]["reason"],
                         "authority_gap")

    def test_gate_skipped_without_valid_package(self):
        material = finding(
            "FIND-WIMG-T56-000010",
            summary="provider routing durable candidate",
            intent="durable_candidate",
        )
        trace = sc.self_check_with_trace(
            self.request, packages=[], findings=[material])
        self.assertFalse(trace["gate_ran"])
        self.assertIsNone(trace["finding_gate"])
        self.assertEqual(trace["result"]["status"], "REFRESH_REQUIRED")
        self.assertEqual(trace["result"]["reasons"], ["package_missing"])
        self.assertFalse(trace["context_engineer_woken"])

    def test_gate_skipped_without_verified_scope(self):
        material = finding(
            "FIND-WIMG-T56-000011",
            summary="provider routing durable candidate",
            intent="durable_candidate",
        )
        registry = {"projects": [{"id": "app1", "phase": "incubation"}]}
        trace = sc.self_check_with_trace(
            self.request, packages=[self.envelope], registry=registry,
            current=self.consistent, findings=[material])
        self.assertFalse(trace["gate_ran"])
        self.assertIsNone(trace["finding_gate"])
        self.assertIsNone(trace["verified_scope"])
        self.assertNotEqual(trace["result"]["status"], "BLOCKED")
        self.assertEqual(trace["result"]["status"], "REFRESH_REQUIRED")
        self.assertIn("scope_mismatch", trace["result"]["reasons"])

    def test_gate_result_is_deterministic(self):
        def findings():
            return [
                finding("FIND-WIMG-T56-000012",
                        summary="provider worktree note for this software engineer",
                        intent="observation", discovered_by="software-engineer"),
                finding("FIND-WIMG-T56-000013",
                        summary="qa milestone coverage pytest count",
                        intent="observation", discovered_by="qa"),
            ]

        first = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=findings())
        second = sc.self_check_with_trace(
            self.request, packages=[self.envelope], current=self.consistent,
            findings=findings())
        self.assertEqual(first, second)
        self.assertEqual(first["result"]["reasons"], [])
        self.assertEqual(
            [f["finding_id"] for f in first["finding_gate"]["relevant_open_findings"]],
            ["FIND-WIMG-T56-000012"])


def finding(fid: str, **kwargs) -> dict:
    doc = {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": fid,
        "project_id": "web-imagegen",
        "task_id": "multica://issue/YZT-55",
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


if __name__ == "__main__":
    unittest.main()
