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


if __name__ == "__main__":
    unittest.main()
