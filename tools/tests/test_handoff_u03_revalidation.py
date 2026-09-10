#!/usr/bin/env python3
"""U03 / YZT-70 — T00–T06 revalidation against V2.2 roles and frozen T00.

Covers: duplicate checkpoint-id inventory and namespaced candidate identity,
artifact-handoff C0 expressibility, V2 logical-role pipeline including
delivery-reviewer, prohibited-hit classification in T00–T06, and
FIND-WIMG-HO00-000001 task-completion accounting.
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from schema_mini import Schema, load_schema_file  # noqa: E402
import chandoff  # noqa: E402
import chandoff_adapter as adapter  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_note as note  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as sc  # noqa: E402
from cdata import load_role_profile  # noqa: E402

CLOCK = lambda: "2026-09-10T12:00:00Z"  # noqa: E731
PKG_S = "context-package.schema.json"
RES_S = "context-handoff/prepare-handoff-result.schema.json"
SELF_RES_S = "context-handoff/self-check-result.schema.json"
V2_ROLES = (
    "engineering-lead",
    "context-engineer",
    "solution-architect",
    "software-engineer",
    "delivery-reviewer",
    "qa",
)
T00_T06_PY = (
    "chandoff.py",
    "chandoff_plan.py",
    "chandoff_compose.py",
    "chandoff_finalize.py",
    "chandoff_selfcheck.py",
    "chandoff_adapter.py",
    "chandoff_note.py",
)
CORE_PY = (
    "chandoff.py",
    "chandoff_plan.py",
    "chandoff_compose.py",
    "chandoff_finalize.py",
    "chandoff_selfcheck.py",
)


def validate(schema_name: str, instance: dict) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def sample_request(role: str = "software-engineer", **overrides) -> dict:
    req = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": "multica://issue/YZT-70",
        "project": {"project_id": "web-imagegen"},
        "target": {"role": role},
        "purpose": "implementation",
        "task_snapshot": {
            "title": "Revalidate T00-T06 handoff against V2 roles",
            "description": (
                "Prove logical-role pipeline, namespaced checkpoint candidates, "
                "and frozen T00 artifact-handoff expressibility."
            ),
            "requirements": [
                "never clean the product repo",
                "write only in an isolated worktree",
                "preserve frozen T00 public schema",
            ],
            "acceptance_criteria": [
                "checkpoint candidates are globally unambiguous",
                "delivery-reviewer package is contract valid",
            ],
            "relevant_decisions": ["U02 six-role roster is the current profile data"],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }
    req.update(overrides)
    return req


def pipeline(role: str):
    req = sample_request(role)
    envelope = plan.prepare_handoff_plan(req, findings=[])
    if envelope["status"] != "PLAN_READY":
        raise AssertionError(envelope)
    accepted = compose.compose_semantic(
        envelope, compose.subset_result(envelope["plan"]))
    if accepted["status"] != "ACCEPTED":
        raise AssertionError(accepted["errors"])
    result = finalize.finalize_handoff(envelope, accepted, req, clock=CLOCK)
    return req, envelope, accepted, result


class CheckpointIdentityTests(unittest.TestCase):
    def test_inventory_finds_duplicate_local_ids_without_rewriting_canonical(self):
        inv = plan.inventory_canonical_checkpoint_ids()
        self.assertGreater(inv["entry_count"], 0)
        self.assertIn("cp-confirmed-001", inv["duplicate_local_ids"])
        sources = {row["checkpoint"]
                   for row in inv["duplicate_local_ids"]["cp-confirmed-001"]}
        self.assertIn("team", sources)
        self.assertTrue(sources & {"web-imagegen", "app1"})
        self.assertFalse(inv["canonical_content_rewritten"])
        self.assertFalse(inv["candidate_last_wins_resolution"])
        team_cp = (ROOT / "team-context" / "checkpoint.yaml").read_text(encoding="utf-8")
        proj_cp = (ROOT / "project-context" / "web-imagegen" / "checkpoint.yaml").read_text(
            encoding="utf-8")
        self.assertIn("id: cp-confirmed-001", team_cp)
        self.assertIn("id: cp-confirmed-001", proj_cp)

    def test_context_engineer_plan_keeps_team_and_project_same_local_id(self):
        req = sample_request("context-engineer")
        result = plan.prepare_handoff_plan(req, findings=[])
        self.assertEqual(result["status"], "PLAN_READY")
        entries = result["plan"]["candidates"]["checkpoint_entries"]
        ids = [e["id"] for e in entries]
        self.assertEqual(len(ids), len(set(ids)), ids)
        self.assertIn("team:cp-confirmed-001", ids)
        self.assertIn("web-imagegen:cp-confirmed-001", ids)
        team_entry = next(e for e in entries if e["id"] == "team:cp-confirmed-001")
        proj_entry = next(e for e in entries if e["id"] == "web-imagegen:cp-confirmed-001")
        self.assertEqual(team_entry["checkpoint"], "team")
        self.assertEqual(proj_entry["checkpoint"], "web-imagegen")
        self.assertNotEqual(team_entry["summary"], proj_entry["summary"])
        source, local = chandoff.split_checkpoint_candidate_id("team:cp-confirmed-001")
        self.assertEqual((source, local), ("team", "cp-confirmed-001"))

    def test_compose_and_finalize_address_both_duplicate_local_ids(self):
        req, envelope, accepted, out = pipeline("context-engineer")
        selected = accepted["result"]["checkpoint_entry_ids"]
        self.assertIn("team:cp-confirmed-001", selected)
        self.assertIn("web-imagegen:cp-confirmed-001", selected)
        team_ids = [e["id"] for e in out["package"]["team_state_slice"]]
        proj_ids = [e["id"] for e in out["package"]["project_state_slice"]]
        self.assertIn("team:cp-confirmed-001", team_ids)
        self.assertIn("web-imagegen:cp-confirmed-001", proj_ids)
        self.assertEqual(validate(PKG_S, out["package"]), [])
        self.assertEqual(validate(RES_S, out), [])

    def test_finalize_rejects_last_wins_collision(self):
        req = sample_request("context-engineer")
        envelope = plan.prepare_handoff_plan(req, findings=[])
        broken = [dict(e) for e in envelope["plan"]["candidates"]["checkpoint_entries"]]
        if len(broken) < 2:
            self.skipTest("need two checkpoint candidates")
        broken[1] = dict(broken[1], id=broken[0]["id"])
        with self.assertRaisesRegex(ValueError, "last-wins is forbidden"):
            finalize._checkpoint_index(broken)


class ArtifactHandoffCompatibilityTests(unittest.TestCase):
    def test_c0_frozen_t00_can_express_artifact_handoff(self):
        report = chandoff.artifact_handoff_compatibility()
        self.assertTrue(report["ok"], report)
        self.assertTrue(report["frozen_package_can_reference_authoritative_artifacts"])
        self.assertTrue(report["frozen_validity_model_can_detect_artifact_dependency_change"])
        self.assertTrue(report["frozen_self_check_can_detect_stale_required_artifact"])
        self.assertTrue(report["can_resolve_exact_artifact_version"])
        self.assertTrue(report["can_detect_superseded_artifact"])
        self.assertTrue(report["can_detect_stale_baseline"])
        self.assertTrue(report["can_bind_role_profile_revision"])
        self.assertTrue(report["extensible_payload_without_schema_amendment"])
        self.assertFalse(report["t00_public_schema_amended"])

    def test_package_task_evidence_can_carry_artifact_dependency_records(self):
        req, envelope, accepted, out = pipeline("delivery-reviewer")
        pkg = out["package"]
        pkg["task_evidence"] = [{
            "artifact_id": "PE-004",
            "artifact_type": "product_expectation",
            "version": "4",
            "required": False,
            "ref": "repo://web-imagegen@600225a/DESIGN.md",
        }]
        pkg["source_refs"] = list(pkg.get("source_refs") or []) + [
            "repo://web-imagegen@600225a/DESIGN.md",
        ]
        self.assertEqual(validate(PKG_S, pkg), [])
        self.assertRegex(
            "repo://web-imagegen@600225a/DESIGN.md",
            r"^(multica|adr|doc|repo|registry|project|git)://\S+$",
        )


class LogicalRolePipelineTests(unittest.TestCase):
    def test_all_v2_roles_resolve_current_profile_and_finalize(self):
        fingerprints = {}
        for role in V2_ROLES:
            profile = load_role_profile(role)
            self.assertEqual(profile["role"], role)
            req, envelope, accepted, out = pipeline(role)
            self.assertEqual(envelope["plan"]["role"], role)
            self.assertEqual(out["role"], role)
            self.assertIn(out["status"], {"READY", "PARTIAL"})
            self.assertEqual(validate(PKG_S, out["package"]), [])
            self.assertEqual(validate(RES_S, out), [])
            self.assertTrue(out["built_from"]["role_profile_revision"].startswith("sha256:"))
            self.assertTrue(out["built_from"]["task_fingerprint"].startswith("sha256:"))
            fingerprints[role] = out["built_from"]["task_fingerprint"]
            check_req = {
                "schema_version": "1.1",
                "kind": "self_check_request",
                "task_ref": req["task_ref"],
                "role": role,
                "task_snapshot": req["task_snapshot"],
            }
            checked = sc.self_check(
                check_req, packages=[out], current=dict(out["built_from"]))
            self.assertEqual(validate(SELF_RES_S, checked), [])
            if out["status"] == "READY":
                self.assertEqual(checked["status"], "READY", (role, checked))
            else:
                self.assertEqual(checked["status"], "REFRESH_REQUIRED", (role, checked))
                self.assertIn("package_not_ready", checked["reasons"])
        self.assertEqual(len(set(fingerprints.values())), 1)

    def test_self_check_rejects_wrong_role_package(self):
        _req, _env, _acc, se_pkg = pipeline("software-engineer")
        check_req = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": "multica://issue/YZT-70",
            "role": "delivery-reviewer",
            "package_ref": se_pkg["package_id"],
            "task_snapshot": sample_request("delivery-reviewer")["task_snapshot"],
        }
        out = sc.self_check(check_req, packages=[se_pkg],
                            current=dict(se_pkg["built_from"]))
        self.assertEqual(out["status"], "REFRESH_REQUIRED")
        self.assertIn("role_mismatch", out["reasons"])

    def test_retired_feature_reviewer_fails_closed(self):
        with self.assertRaises(ValueError):
            load_role_profile("feature-reviewer")
        with self.assertRaises(adapter.SchemaViolationError):
            adapter._validate_role_vocabulary({
                "target": {"role": "feature-reviewer"},
                "caller": {"role": "engineering-lead"},
            })


class ProhibitedHitTests(unittest.TestCase):
    def test_t00_t06_core_has_no_hard_coded_feature_reviewer_or_roster(self):
        hits = []
        for name in CORE_PY:
            text = (TOOLS / name).read_text(encoding="utf-8")
            if re.search(r"feature-reviewer", text):
                hits.append((name, "feature-reviewer"))
            if re.search(r"\bops-sre\b", text):
                hits.append((name, "ops-sre"))
        self.assertEqual(hits, [])

    def test_native_api_scan_stays_framework_neutral(self):
        report = chandoff.scan_handoff_contracts()
        self.assertEqual(report, {})

    def test_core_has_no_agent_uuid_literals(self):
        uuid_re = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        hits = []
        for name in CORE_PY:
            text = (TOOLS / name).read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if uuid_re.fullmatch(node.value):
                        hits.append((name, node.value))
        self.assertEqual(hits, [])

    def test_adapter_and_publisher_are_the_framework_boundary(self):
        self.assertEqual(plan.LLM_CALLED, False)
        self.assertEqual(finalize.MULTICA_RUNTIME_DEPENDENCIES, 0)
        self.assertEqual(sc.MULTICA_RUNTIME_DEPENDENCIES, 0)
        adapter_src = Path(adapter.__file__).read_text(encoding="utf-8")
        note_src = Path(note.__file__).read_text(encoding="utf-8")
        self.assertIn("multica issue get", adapter_src)
        self.assertIn("issue comment add", note_src)
        self.assertIn("idempotent", note_src.lower())
        self.assertNotIn("issue comment add", Path(plan.__file__).read_text(encoding="utf-8"))


class FindingDrainTests(unittest.TestCase):
    def test_find_wimg_ho00_000001_accounted_at_completion(self):
        closure = finalize.finding_ho00_000001_closure()
        self.assertEqual(closure["finding_id"], "FIND-WIMG-HO00-000001")
        self.assertEqual(closure["status"], "processed")
        self.assertEqual(closure["disposition"], "absorbed_by_existing")
        self.assertFalse(closure["canonical_write"])
        _req, _env, _acc, out = pipeline("software-engineer")
        self.assertEqual(finalize.pseudo_scheme_refs(out["package"]), [])
        self.assertEqual(finalize.grammar_invalid_refs(out["package"]), [])
        accounting = {
            "FIND-WIMG-HO00-000001": closure["status"],
            "unaccounted_open_findings": [],
        }
        self.assertEqual(accounting["unaccounted_open_findings"], [])


if __name__ == "__main__":
    unittest.main()
