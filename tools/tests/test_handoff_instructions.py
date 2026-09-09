#!/usr/bin/env python3
"""T08 focused tests — Agent Instruction Contract staging (YZT-63).

All Multica interaction is offline: committed capture fixtures and canned
runners. No network, no model call, no Canonical write, no Memory rebuild,
no workspace skill import, no agent instruction write, no binding mutation,
no assignment/mention/run trigger — the staging tool is verified to be
read-only against the deployed CLI boundary, and the staged bundle is
verified to be a pure, reviewable candidate.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import chandoff_instructions as instr  # noqa: E402
import chandoff as t00  # noqa: E402

ART = REPO / "adapters" / "multica" / "agent-instructions"
BASELINE = ART / "baseline.json"
SKILL_DIR = REPO / "skills" / "multica-context-handoff"
LEAD_ID = "24f04aba-7da9-4371-bf89-685d7505a411"
CTX_ID = "8bc546ab-ffd8-4aa6-ad30-58583346c065"

FAKE_VERSION = {"version": "v0.9.9-test", "os": "test", "arch": "test"}


def fake_raw_agents() -> list:
    """Synthetic live agent list with the six roles (plus one out-of-scope)."""
    bindings = {
        "01 Engineering Lead": [{"id": "b-1", "name": "planning",
                                 "enabled": True}],
        "02 Context Engineer": [{"id": "b-2", "name": "memory",
                                 "enabled": True}],
        "03 Solution Architect": [{"id": "b-3", "name": "design",
                                   "enabled": True}],
        "04 Software Engineer": [{"id": "b-4", "name": "implementation",
                                  "enabled": True}],
        "05 Feature Reviewer": [{"id": "b-5", "name": "review",
                                 "enabled": True}],
        "06 QA": [{"id": "b-6", "name": "quality", "enabled": True}],
    }
    agents = []
    for i, (role, name) in enumerate(instr.ROLES):
        agents.append({
            "id": f"00000000-0000-0000-0000-{i:012d}",
            "name": name,
            "instructions": f"你是 {name}。现有契约文本 {role}。\n",
            "skills": copy.deepcopy(bindings[name]),
            "updated_at": "2026-01-01T00:00:00Z",
            "custom_env_key_count": 1,
            "has_custom_env": True,
        })
    agents.append({
        "id": "99999999-9999-9999-9999-999999999999",
        "name": "Mika",
        "instructions": "",
        "skills": [],
        "updated_at": "2026-01-01T00:00:00Z",
        "custom_env_key_count": 0,
        "has_custom_env": False,
    })
    return agents


def fake_raw_skills(present: bool = False) -> list:
    skills = [{"id": f"s-{i}", "name": f"skill-{i}"}
              for i in range(3)]
    if present:
        skills.append({"id": "s-new", "name": instr.SKILL_NAME})
    return skills


def canned_capture_runner(agents=None, skills=None, version=None):
    agents = fake_raw_agents() if agents is None else agents
    skills = fake_raw_skills() if skills is None else skills
    version = FAKE_VERSION if version is None else version

    def runner(argv):
        payload = argv[1:]
        if payload == ["agent", "list", "--output", "json"]:
            return 0, json.dumps(agents), ""
        if payload == ["skill", "list", "--output", "json"]:
            return 0, json.dumps(skills), ""
        if payload == ["version", "--output", "json"]:
            return 0, json.dumps(version), ""
        raise AssertionError(f"unexpected argv {argv}")

    return runner


def write_capture_dir(agents, skills, tmp: Path) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "agent-list.json").write_text(
        json.dumps(agents, ensure_ascii=False), encoding="utf-8")
    (tmp / "skill-list.json").write_text(
        json.dumps(skills, ensure_ascii=False), encoding="utf-8")
    return tmp


class CaptureBoundaryTests(unittest.TestCase):
    def test_allowlist_refuses_every_mutation_argv(self):
        cli = instr.CaptureCli(runner=canned_capture_runner())
        forbidden = [
            ["agent", "update", LEAD_ID, "--instructions", "x"],
            ["agent", "skills", "add", LEAD_ID, "--skill-ids", "s"],
            ["agent", "skills", "set", LEAD_ID, "--skill-ids", "s"],
            ["agent", "create"],
            ["skill", "import", "x"],
            ["skill", "refresh", "s-1"],
            ["issue", "assign", "YZT-1", "--to", "a"],
            ["issue", "create", "--title", "x"],
            ["issue", "comment", "add", "YZT-1", "--content", "x"],
            ["issue", "update", "YZT-1", "--status", "todo"],
            ["mention", "send"],
        ]
        for argv in forbidden:
            with self.assertRaises(instr.StagingError):
                cli._run(argv)
        self.assertEqual(cli.commands, [])

    def test_capture_records_only_allowlisted_argv_and_zero_guarantees(self):
        with tempfile.TemporaryDirectory() as td:
            cli = instr.CaptureCli(runner=canned_capture_runner())
            baseline = instr.capture(Path(td), cli=cli)
            self.assertEqual(
                list(cli.commands),
                [["agent", "list", "--output", "json"],
                 ["skill", "list", "--output", "json"],
                 ["version", "--output", "json"]])
            self.assertTrue(baseline["trace"]["readonly_allowlist_enforced"])
            self.assertEqual(baseline["trace"]["guarantees"],
                             instr.GUARANTEES)
            self.assertTrue(all(v == 0 for v in
                                baseline["trace"]["guarantees"].values()))

    def test_capture_fails_closed_on_missing_or_ambiguous_agent(self):
        agents = fake_raw_agents()
        agents = [a for a in agents if a["name"] != "06 QA"]
        with self.assertRaises(instr.CaptureError):
            instr.capture(
                Path(tempfile.mkdtemp()),
                cli=instr.CaptureCli(runner=canned_capture_runner(agents)))
        dup = fake_raw_agents()
        dup.append({"id": "extra", "name": "06 QA", "instructions": "x",
                    "skills": []})
        with self.assertRaises(instr.CaptureError):
            instr.capture(
                Path(tempfile.mkdtemp()),
                cli=instr.CaptureCli(runner=canned_capture_runner(dup)))

    def test_capture_records_no_secret_fields(self):
        with tempfile.TemporaryDirectory() as td:
            baseline = instr.capture(
                Path(td), cli=instr.CaptureCli(runner=canned_capture_runner()))
            doc = json.loads((Path(td) / "baseline.json").read_text(
                encoding="utf-8"))
            for agent in doc["agents"]:
                serialized = json.dumps(agent, ensure_ascii=False)
                self.assertNotIn("custom_env", serialized.replace(
                    "custom_env_key_count", "").replace("has_custom_env", ""))
                self.assertNotIn("mcp", serialized)
                self.assertNotIn("system_instructions", serialized)
                self.assertNotIn("model", serialized)
            self.assertIn("excluded_fields", baseline["trace"])
            for field in ("custom_env values", "mcp_config",
                          "system_instructions"):
                self.assertIn(field, baseline["trace"]["excluded_fields"])

    def test_captured_baseline_has_skill_absent_and_binding_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            baseline = instr.capture(
                Path(td), cli=instr.CaptureCli(runner=canned_capture_runner()))
            self.assertFalse(baseline["skill_catalog"][
                "multica_context_handoff_present"])
            self.assertEqual(baseline["agents_with_skill_bound"], [])
            self.assertEqual(baseline["out_of_scope_agents"][0]["agent_name"],
                             "Mika")
            lead = baseline["agents"][0]
            self.assertEqual(lead["skill_binding_ids"], ["b-1"])


class BaselineInventoryTests(unittest.TestCase):
    """The committed baseline is a faithful read-only live-state record."""

    def test_six_roles_with_stable_agent_ids(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["schema_version"], instr.BASELINE_SCHEMA)
        self.assertEqual([a["role"] for a in baseline["agents"]],
                         [r for r, _ in instr.ROLES])
        ids = {a["role"]: a["agent_id"] for a in baseline["agents"]}
        self.assertEqual(ids["engineering-lead"], LEAD_ID)
        self.assertEqual(ids["context-engineer"], CTX_ID)
        self.assertEqual(len(set(ids.values())), 6)

    def test_instruction_digests_recompute_from_recorded_text(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for a in baseline["agents"]:
            text = a["instruction_text"]
            self.assertEqual(t00.sha256_text(text), a["instruction_sha256"])
            self.assertEqual(instr.anchor_tail(text), a["anchor_tail"])
            self.assertEqual(a["instruction_length"], len(text))
            self.assertTrue(text.endswith("\n"))

    def test_skill_catalog_matches_raw_capture_and_records_absence(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        raw = json.loads((ART / "skill-list.json").read_text(encoding="utf-8"))
        raw_names = sorted(s["name"] for s in raw)
        catalog = baseline["skill_catalog"]
        self.assertEqual(catalog["count"], len(raw))
        self.assertEqual(sorted(s["name"] for s in catalog["skills"]),
                         raw_names)
        self.assertFalse(catalog["multica_context_handoff_present"])
        self.assertNotIn(instr.SKILL_NAME, raw_names)

    def test_binding_ids_match_raw_agent_capture(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        raw = json.loads((ART / "agent-list.json").read_text(encoding="utf-8"))
        raw_by_name = {a["name"]: a for a in raw}
        for a in baseline["agents"]:
            expected = sorted(s["id"] for s in
                              raw_by_name[a["agent_name"]]["skills"])
            self.assertEqual(a["skill_binding_ids"], expected)
            self.assertEqual(a["agent_id"], raw_by_name[a["agent_name"]]["id"])
            self.assertEqual(
                a["instruction_sha256"],
                t00.sha256_text(raw_by_name[a["agent_name"]]["instructions"]))

    def test_trace_is_read_only_with_zero_guarantees(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        trace = baseline["trace"]
        self.assertTrue(trace["readonly_allowlist_enforced"])
        self.assertEqual(trace["guarantees"], instr.GUARANTEES)
        for argv in trace["commands"]:
            self.assertTrue(instr._allowlisted(argv), argv)
        self.assertEqual(baseline["agents_with_skill_bound"], [])


class CandidateContractTests(unittest.TestCase):
    def test_lead_block_carries_full_pre_dispatch_gate(self):
        block = (ART / "candidates" / "engineering-lead.md").read_text(
            encoding="utf-8")
        missing = [m for m in instr.LEAD_MARKERS if m not in block]
        self.assertEqual(missing, [])
        self.assertIn(instr.NON_TRANSFER_MARKERS["engineering-lead"], block)

    def test_professional_blocks_carry_run_and_handoff_gates(self):
        for role in instr.PROF_ROLES:
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            self.assertEqual([m for m in instr.PROF_MARKERS if m not in block],
                             [], role)
            self.assertIn(
                instr.NON_TRANSFER_MARKERS[role], block, role)

    def test_professional_refresh_stays_same_task_and_role(self):
        for role in instr.PROF_ROLES:
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            self.assertIn("对同一 task 与当前角色重新 PREPARE_HANDOFF", block)

    def test_ctx_block_is_exception_only(self):
        block = (ART / "candidates" / "context-engineer.md").read_text(
            encoding="utf-8")
        self.assertEqual([m for m in instr.CTX_MARKERS if m not in block], [])
        self.assertIn("仅当某角色", block)
        self.assertIn("有界自刷新路径（SELF_CHECK REFRESH_REQUIRED）无法安全解决",
                      block)

    def test_ordinary_handoffs_never_route_through_02(self):
        ordinary_routing = ("路由给 02", "route to 02",
                            "send ordinary handoffs to 02")
        boundary = "普通 READY/REFRESH 路径不经过 02"
        for role in ("engineering-lead",) + instr.PROF_ROLES:
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            for marker in ordinary_routing:
                self.assertNotIn(marker, block, role)
        prof = (ART / "candidates" / "software-engineer.md").read_text(
            encoding="utf-8")
        self.assertIn(boundary, prof)
        ctx = (ART / "candidates" / "context-engineer.md").read_text(
            encoding="utf-8")
        self.assertIn("普通成功路径与普通 Finding 均不唤醒 02", ctx)
        self.assertIn("必经环节（handoff hop）", ctx)

    def test_no_block_claims_premature_activation(self):
        for role, _ in instr.ROLES:
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            found = [m for m in instr.LIVENESS_FORBIDDEN if m in block]
            self.assertEqual(found, [], role)

    def test_candidate_deltas_carry_no_trigger_surface(self):
        for role, _ in instr.ROLES:
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            found = [m for m in instr.DELTA_FORBIDDEN if m in block]
            self.assertEqual(found, [], role)
            self.assertNotIn("mention://", block, role)

    def test_blocks_reference_shared_skill_without_duplicating_policy(self):
        policy_markers = ("semantic_jobs", "semantic_compose_result",
                          "bounded repair", "repair", "fingerprint",
                          "memory_revision", "PLAN_READY", "FINALIZE",
                          "compose")
        # 01 + 03/04/05/06 route through the shared Skill explicitly.
        for role in ("engineering-lead",) + instr.PROF_ROLES:
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            self.assertIn("multica-context-handoff", block, role)
            found = [m for m in policy_markers if m in block]
            self.assertEqual(found, [], role)
        # 02 stays outside the normal Skill flow: the exception path is
        # issue-based escalation, and the not-a-hop boundary stays stated.
        ctx = (ART / "candidates" / "context-engineer.md").read_text(
            encoding="utf-8")
        self.assertNotIn("multica-context-handoff", ctx)
        self.assertIn("必经环节（handoff hop）", ctx)


class AuthorityPreservationTests(unittest.TestCase):
    def test_post_apply_text_preserves_baseline_bytes_exactly(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for a in baseline["agents"]:
            after = (ART / "after" / f"{a['role']}.md").read_text(
                encoding="utf-8")
            block = (ART / "candidates" / f"{a['role']}.md").read_text(
                encoding="utf-8")
            self.assertTrue(after.startswith(a["instruction_text"]), a["role"])
            self.assertEqual(
                after, instr.applied_text(a["instruction_text"], block))
            self.assertEqual(t00.sha256_text(
                after[:a["instruction_length"]]), a["instruction_sha256"])

    def test_existing_ownership_sentences_survive_verbatim(self):
        expectations = {
            "engineering-lead": "最终 Merge 始终交给 Human",
            "solution-architect": "产品仓只读",
            "software-engineer": "不得写 Canonical Memory",
            "feature-reviewer": "产品仓只读",
            "qa": "产品仓只读",
            "context-engineer": "产品仓默认只读",
        }
        for role, sentence in expectations.items():
            after = (ART / "after" / f"{role}.md").read_text(
                encoding="utf-8")
            self.assertIn(sentence, after, role)

    def test_every_block_states_its_non_transfer_boundary(self):
        for role, marker in instr.NON_TRANSFER_MARKERS.items():
            block = (ART / "candidates" / f"{role}.md").read_text(
                encoding="utf-8")
            self.assertIn(marker, block, role)


class BindingPlanTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads(
            (ART / "binding-plan.json").read_text(encoding="utf-8"))

    def test_additive_ops_for_exactly_five_roles(self):
        self.assertEqual(self.plan["schema_version"], instr.PLAN_SCHEMA)
        roles = sorted(b["agent_role"] for b in self.plan["bindings"])
        self.assertEqual(roles, sorted(instr.BOUND_ROLES))
        self.assertTrue(all(b["op"] == "add" for b in self.plan["bindings"]))
        self.assertFalse(any(b["op"] == "set" for b in self.plan["bindings"]))
        self.assertTrue(self.plan["additive_only"])
        self.assertTrue(self.plan["never_replace_all_set"])

    def test_context_engineer_excluded_with_rationale(self):
        excluded = self.plan["excluded"]
        self.assertEqual([e["agent_role"] for e in excluded],
                         ["context-engineer"])
        self.assertIn("exception-path", excluded[0]["reason"])

    def test_baseline_bindings_recorded_intact(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        by_role = {a["role"]: a for a in baseline["agents"]}
        for b in self.plan["bindings"]:
            a = by_role[b["agent_role"]]
            self.assertEqual(b["baseline_binding_ids"], a["skill_binding_ids"])
            self.assertEqual(b["baseline_bindings"], a["skill_bindings"])
        self.assertEqual([b["agent_id"] for b in self.plan["bindings"]],
                         [by_role[r]["agent_id"] for r in instr.BOUND_ROLES])

    def test_plan_is_explicitly_not_active(self):
        self.assertEqual(self.plan["status"], "staged_candidate_not_active")
        self.assertIn("T13", self.plan["activation"])


class PreconditionManifestTests(unittest.TestCase):
    def setUp(self):
        self.baseline = BASELINE
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _capture_dir(self, agents=None, skills=None) -> Path:
        agents = (json.loads((ART / "agent-list.json").read_text(
            encoding="utf-8")) if agents is None else agents)
        skills = (json.loads((ART / "skill-list.json").read_text(
            encoding="utf-8")) if skills is None else skills)
        return write_capture_dir(agents, skills, Path(self.tmp.name) /
                                 f"cap-{len(str(self.tmp))}")

    def test_verify_passes_against_unchanged_live_state(self):
        report = instr.verify(self.baseline, ART, mode="stage")
        self.assertTrue(report["ok"], report["checks_failed"])
        self.assertEqual(report["drift"], [])
        self.assertEqual(report["checks_passed_count"], 32)

    def test_verify_fails_closed_on_instruction_digest_drift(self):
        agents = json.loads((ART / "agent-list.json").read_text(
            encoding="utf-8"))
        for a in agents:
            if a["name"] == "03 Solution Architect":
                a["instructions"] += "\n\n尾部被篡改的一句。\n"
        report = instr.verify(self.baseline, self._capture_dir(agents))
        self.assertFalse(report["ok"])
        self.assertIn("instruction_digest_mismatch", report["drift"])
        self.assertIn("anchor_tail_mismatch", report["drift"])

    def test_verify_fails_closed_on_agent_id_drift(self):
        agents = json.loads((ART / "agent-list.json").read_text(
            encoding="utf-8"))
        for a in agents:
            if a["name"] == "06 QA":
                a["id"] = "00000000-0000-0000-0000-ffffffffffff"
        report = instr.verify(self.baseline, self._capture_dir(agents))
        self.assertFalse(report["ok"])
        self.assertIn("agent_id_changed", report["drift"])

    def test_verify_fails_closed_on_binding_set_drift(self):
        agents = json.loads((ART / "agent-list.json").read_text(
            encoding="utf-8"))
        for a in agents:
            if a["name"] == "01 Engineering Lead":
                a["skills"] = a["skills"] + [
                    {"id": "extra-skill", "name": "extra", "enabled": True}]
        report = instr.verify(self.baseline, self._capture_dir(agents))
        self.assertFalse(report["ok"])
        self.assertIn("binding_set_drift", report["drift"])

    def test_verify_fails_closed_on_skill_catalog_drift(self):
        skills = json.loads((ART / "skill-list.json").read_text(
            encoding="utf-8"))
        skills.append({"id": "s-future", "name": instr.SKILL_NAME})
        report = instr.verify(self.baseline,
                              self._capture_dir(skills=skills), mode="stage")
        self.assertFalse(report["ok"])
        self.assertIn("skill_state_drift", report["drift"])

    def test_verify_fails_closed_on_missing_agent(self):
        agents = json.loads((ART / "agent-list.json").read_text(
            encoding="utf-8"))
        agents = [a for a in agents if a["name"] != "05 Feature Reviewer"]
        report = instr.verify(self.baseline, self._capture_dir(agents))
        self.assertFalse(report["ok"])
        self.assertIn("agent_missing", report["drift"])

    def test_apply_mode_requires_skill_and_matching_content(self):
        report = instr.verify(self.baseline, ART, mode="apply")
        self.assertFalse(report["ok"])
        self.assertIn("skill_state_drift", report["drift"])
        skills = json.loads((ART / "skill-list.json").read_text(
            encoding="utf-8"))
        skills.append({"id": "s-future", "name": instr.SKILL_NAME})
        cap = self._capture_dir(skills=skills)
        fixture = cap / "skill-get.json"
        fixture.write_text(json.dumps({
            "name": instr.SKILL_NAME,
            "content": (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"),
        }, ensure_ascii=False), encoding="utf-8")
        ok = instr.verify(self.baseline, cap, mode="apply", skill_get_file=fixture)
        self.assertTrue(ok["ok"], ok["drift"])
        bad = cap / "skill-get-bad.json"
        bad.write_text(json.dumps({
            "name": instr.SKILL_NAME,
            "content": (SKILL_DIR / "SKILL.md").read_text(
                encoding="utf-8") + "\ndrift\n",
        }, ensure_ascii=False), encoding="utf-8")
        drifted = instr.verify(self.baseline, cap, mode="apply",
                               skill_get_file=bad)
        self.assertFalse(drifted["ok"])
        self.assertIn("skill_state_drift", drifted["drift"])

    def test_manifest_records_digests_and_bindings_fail_closed(self):
        doc = json.loads((ART / "preconditions.json").read_text(
            encoding="utf-8"))
        self.assertTrue(doc["fail_closed"])
        self.assertTrue(doc["drift_is_fatal"])
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for entry, a in zip(doc["agents"], baseline["agents"]):
            self.assertEqual(entry["instruction_sha256"],
                             a["instruction_sha256"])
            self.assertEqual(entry["baseline_binding_ids"],
                             a["skill_binding_ids"])
            self.assertEqual(entry["agent_id"], a["agent_id"])
        skill = json.loads((ART / "bundle.json").read_text(
            encoding="utf-8"))["skill"]
        self.assertEqual(doc["skill"]["bundle_digest"], skill["bundle_digest"])
        self.assertEqual(doc["skill"]["skill_md_digest"],
                         instr.skill_bundle_digests(REPO)["skill_md_digest"])


class RollbackBundleTests(unittest.TestCase):
    def setUp(self):
        self.rollback = json.loads(
            (ART / "rollback.json").read_text(encoding="utf-8"))
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    def test_covers_all_six_agents_exactly(self):
        self.assertEqual(self.rollback["schema_version"],
                         instr.ROLLBACK_SCHEMA)
        self.assertEqual(len(self.rollback["restores"]), 6)
        by_role = {a["role"]: a for a in self.baseline["agents"]}
        for r in self.rollback["restores"]:
            a = by_role[r["agent_role"]]
            self.assertEqual(r["baseline_instruction_text"],
                             a["instruction_text"])
            self.assertEqual(r["baseline_instruction_sha256"],
                             a["instruction_sha256"])
            self.assertEqual(r["baseline_binding_ids"], a["skill_binding_ids"])
            self.assertEqual(r["baseline_bindings"], a["skill_bindings"])

    def test_restore_commands_are_instruction_and_binding_only(self):
        forbidden = ("mention://", "issue assign", "issue create",
                     "issue comment", "agent run", "rerun", "assign --to")
        for r in self.rollback["restores"]:
            self.assertEqual(len(r["restore_instructions_declarative"]), 2)
            for cmd in r["restore_instructions_declarative"]:
                for marker in forbidden:
                    self.assertNotIn(marker, cmd)

    def test_rollback_is_declarative_only(self):
        self.assertEqual(self.rollback["status"],
                         "declarative_only_not_executed_by_T08")


class DeterminismTests(unittest.TestCase):
    def test_build_is_byte_deterministic_and_matches_committed_bundle(self):
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as td1, \
                tempfile.TemporaryDirectory() as td2:
            instr.build(Path(td1), BASELINE, REPO)
            instr.build(Path(td2), BASELINE, REPO)
            for sub in ("candidates", "after"):
                for p in sorted((Path(td1) / sub).iterdir()):
                    self.assertEqual(
                        p.read_text(encoding="utf-8"),
                        (Path(td2) / sub / p.name).read_text(
                            encoding="utf-8"))
                    committed = (ART / sub / p.name).read_text(
                        encoding="utf-8")
                    self.assertEqual(p.read_text(encoding="utf-8"), committed)
            for name in ("binding-plan.json", "preconditions.json",
                         "rollback.json", "bundle.json"):
                a = (Path(td1) / name).read_text(encoding="utf-8")
                b = (Path(td2) / name).read_text(encoding="utf-8")
                c = (ART / name).read_text(encoding="utf-8")
                self.assertEqual(a, b, name)
                self.assertEqual(a, c, name)

    def test_bundle_manifest_digests_match_committed_files(self):
        manifest = json.loads((ART / "bundle.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["status"], "staged_candidate_not_active")
        for rel, digest in manifest["files"].items():
            self.assertEqual(
                t00.sha256_text((ART / rel).read_text(encoding="utf-8")),
                digest, rel)


class ReportTests(unittest.TestCase):
    def test_report_regenerates_byte_identically(self):
        out = Path(tempfile.mkdtemp()) / "T08_REPORT.md"
        argv = ["--baseline", str(BASELINE), "--bundle-dir", str(ART),
                "--markdown", str(out)]
        if (ART / "final-verify.json").is_file():
            argv += ["--final-verify", str(ART / "final-verify.json")]
        with contextlib.redirect_stdout(io.StringIO()) as _sink:
            rc = instr.main(["audit"] + argv)
        self.assertEqual(rc, 0)
        self.assertTrue(out.is_file())
        committed = (ART / "T08_REPORT.md").read_text(encoding="utf-8")
        self.assertEqual(out.read_text(encoding="utf-8"), committed)


class LocationFreeTests(unittest.TestCase):
    """No machine-specific worktree paths anywhere; delta artifacts carry no
    trigger surface at all. Quoted live-state copies (baseline/raw/rollback/
    after) are exempt from the word-level scan but are byte-verified against
    the captured live state elsewhere."""

    def test_no_drive_letter_or_unix_home_paths_in_any_artifact(self):
        import re as _re
        pattern = _re.compile(r"\b[A-Za-z]:[\\/]|/Users/|/home/", _re.I)
        for p in sorted(ART.rglob("*")):
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text),
                              f"machine path in {p.relative_to(ART)}")

    def test_no_worktree_words_in_generated_delta_artifacts(self):
        import re as _re
        pattern = _re.compile(
            r"worktree|multica-memory|[A-Za-z]:\\AI", _re.I)
        scoped = [(ART / n).read_text(encoding="utf-8") for n in (
            "binding-plan.json", "preconditions.json", "bundle.json",
            "T08_REPORT.md")]
        scoped += [(ART / "candidates" / f"{r}.md").read_text(
            encoding="utf-8") for r, _ in instr.ROLES]
        for i, text in enumerate(scoped):
            self.assertIsNone(pattern.search(text), f"artifact #{i}")


class NoLiveMutationTests(unittest.TestCase):
    def test_audit_proves_non_activation_and_done_criteria(self):
        audit = instr.audit(BASELINE, ART)
        self.assertTrue(audit["ok"], audit["failed_checks"])
        done = audit["done"]
        for key in ("lead_has_pre_dispatch_gate",
                    "professional_agents_have_self_gate",
                    "professional_agents_have_pre_handoff_gate",
                    "context_engineer_not_normal_path",
                    "existing_role_authority_preserved",
                    "exactly_one_trigger_preserved",
                    "rollback_complete"):
            self.assertTrue(done[key], key)
        self.assertEqual(done["live_skill_imports"], 0)
        self.assertEqual(done["live_agent_instruction_writes"], 0)
        self.assertEqual(done["live_skill_binding_writes"], 0)
        self.assertEqual(done["triggered_runs"], 0)

    def test_verify_cli_exit_codes_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_dir = Path(td) / "base"
            baseline_dir.mkdir()
            with contextlib.redirect_stdout(io.StringIO()):
                instr.capture(
                    baseline_dir,
                    cli=instr.CaptureCli(runner=canned_capture_runner()))
            clean = write_capture_dir(fake_raw_agents(), fake_raw_skills(),
                                      Path(td) / "cap-clean")
            drifted = copy.deepcopy(fake_raw_agents())
            for a in drifted:
                if a["name"] == "01 Engineering Lead":
                    a["instructions"] += "drift"
            cap_bad = write_capture_dir(drifted, fake_raw_skills(),
                                        Path(td) / "cap-bad")
            with contextlib.redirect_stdout(io.StringIO()):
                rc_clean = instr.main([
                    "verify", "--baseline",
                    str(baseline_dir / "baseline.json"),
                    "--capture-dir", str(clean), "--mode", "stage"])
                rc_bad = instr.main([
                    "verify", "--baseline",
                    str(baseline_dir / "baseline.json"),
                    "--capture-dir", str(cap_bad), "--mode", "stage"])
            self.assertEqual(rc_clean, 0)
            self.assertEqual(rc_bad, 2)


class ScanExemptionTests(unittest.TestCase):
    def test_frozen_boundary_scan_stays_clean_and_t08_stays_out(self):
        self.assertEqual(t00.scan_handoff_contracts(), {})
        source = (TOOLS / "chandoff.py").read_text(encoding="utf-8")
        self.assertNotIn(
            "chandoff_instructions", source,
            "T08 is framework-specific (adapter-class) and must not be "
            "added to the frozen framework-neutral boundary scan")


if __name__ == "__main__":
    unittest.main()
