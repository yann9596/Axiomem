#!/usr/bin/env python3
"""U05 focused tests — V2.2 Agent/Squad instruction staging (YZT-73).

All Multica interaction is offline: committed capture fixtures and canned
runners. No network, no model call, no Canonical write, no Memory rebuild,
no workspace skill import, no agent/squad instruction write, no binding
mutation, no assignment/mention/run trigger.
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
from chandoff_instruction_texts import OWNER_SENTENCES  # noqa: E402

ART = REPO / "adapters" / "multica" / "agent-instructions"
BASELINE = ART / "baseline.json"
SKILL_DIR = REPO / "skills" / "multica-context-handoff"
LEAD_ID = instr.EXPECTED_AGENT_IDS["engineering-lead"]
CTX_ID = instr.EXPECTED_AGENT_IDS["context-engineer"]
FAKE_VERSION = {"version": "v0.9.9-test", "os": "test", "arch": "test"}
SQUAD_ID = "ee895c79-ca0a-498b-abe9-5af537736a30"


def _instr(role: str, name: str) -> str:
    body = f"你是 {name}。现有契约文本 {role}。\n"
    if role == "engineering-lead":
        body += "按需调用 Context、Architect、Engineer、Feature Reviewer；只有你能正式触发 QA Gate。"
        body += "最终 Merge 始终交给 Human。\n"
    elif role == "context-engineer":
        body += "产品仓默认只读。\n"
    elif role == "solution-architect":
        body += "用户行为疑问交 Feature Reviewer。产品仓只读。\n"
    elif role == "software-engineer":
        body += "用户行为交 05。不得写 Canonical Memory。\n"
    elif role == "qa":
        body += "产品仓只读。不得修改产品实现后给自己 PASS。\n"
    elif role == "delivery-reviewer":
        body += "你是 Feature Reviewer，是 User and External Environment Sensor。\n"
    return body


def fake_raw_agents() -> list:
    bindings = {
        "engineering-lead": [{"id": "b-1", "name": "planning", "enabled": True}],
        "context-engineer": [{"id": "b-2", "name": "memory", "enabled": True}],
        "solution-architect": [{"id": "b-3", "name": "design", "enabled": True}],
        "software-engineer": [{"id": "b-4", "name": "implementation",
                               "enabled": True}],
        "delivery-reviewer": [
            {"id": "aab482f9-b9ff-4b78-8394-1f13e2b319c0",
             "name": "parent-handoff-wake", "enabled": True},
            {"id": "db4b6822-5b69-4958-b33b-7ecb3261f151",
             "name": "external-signal-research", "enabled": True},
            {"id": "e964f7b1-3cb4-4bc7-902b-16c57e73cefe",
             "name": "feature-correctness-review", "enabled": True},
        ],
        "qa": [
            {"id": "29f68c43-0772-47a8-a860-d946a100c789",
             "name": "milestone-quality-gate", "enabled": True},
            {"id": "b-6", "name": "parent-handoff-wake", "enabled": True},
        ],
    }
    agents = []
    for role, staged in instr.ROLES:
        agents.append({
            "id": instr.EXPECTED_AGENT_IDS[role],
            "name": instr.LIVE_DISPLAY_NAME[role],
            "instructions": _instr(role, staged),
            "skills": copy.deepcopy(bindings[role]),
            "updated_at": "2026-01-01T00:00:00Z",
            "custom_env_key_count": 1,
            "has_custom_env": True,
            "workspace_id": "ws-test",
        })
    agents.append({
        "id": "d208ecd2-ac6a-4d6c-84b7-91136acf8297",
        "name": "Mika",
        "instructions": "",
        "skills": [],
        "updated_at": "2026-01-01T00:00:00Z",
        "custom_env_key_count": 0,
        "has_custom_env": False,
    })
    return agents


def fake_squad() -> dict:
    members = [
        {"id": f"m-{role}", "member_id": uid, "member_type": "agent",
         "role": ("Feature Reviewer" if role == "delivery-reviewer"
                  else instr.SQUAD_ROLE_AFTER[role])}
        for role, uid in instr.EXPECTED_AGENT_IDS.items()
    ]
    return {
        "id": SQUAD_ID,
        "name": "Engineering Team",
        "leader_id": LEAD_ID,
        "instructions": "You are the sole routing entrypoint. Feature Reviewer owns user signals.",
        "description": "six-role team",
        "members": members,
    }


def fake_raw_skills(present: bool = False) -> list:
    skills = [{"id": f"s-{i}", "name": f"skill-{i}"} for i in range(3)]
    if present:
        skills.append({"id": "s-new", "name": instr.SKILL_NAME})
    return skills


def canned_capture_runner(agents=None, skills=None, version=None,
                          squad=None, tasks=None):
    agents = fake_raw_agents() if agents is None else agents
    skills = fake_raw_skills() if skills is None else skills
    version = FAKE_VERSION if version is None else version
    squad = fake_squad() if squad is None else squad
    tasks = [] if tasks is None else tasks

    def runner(argv):
        payload = argv[1:]
        if payload == ["agent", "list", "--output", "json"]:
            return 0, json.dumps(agents), ""
        if payload == ["skill", "list", "--output", "json"]:
            return 0, json.dumps(skills), ""
        if payload == ["squad", "list", "--output", "json"]:
            return 0, json.dumps([squad]), ""
        if payload[:2] == ["squad", "get"] and payload[-2:] == ["--output", "json"]:
            return 0, json.dumps(squad), ""
        if payload[:3] == ["squad", "member", "list"]:
            return 0, json.dumps(squad["members"]), ""
        if payload[:2] == ["agent", "tasks"]:
            return 0, json.dumps(tasks), ""
        if payload == ["version", "--output", "json"]:
            return 0, json.dumps(version), ""
        raise AssertionError(f"unexpected argv {argv}")

    return runner


def write_capture_dir(agents, skills, tmp: Path, squad=None) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    squad = fake_squad() if squad is None else squad
    (tmp / "agent-list.json").write_text(
        json.dumps(agents, ensure_ascii=False), encoding="utf-8")
    (tmp / "skill-list.json").write_text(
        json.dumps(skills, ensure_ascii=False), encoding="utf-8")
    (tmp / "squad-get.json").write_text(
        json.dumps(squad, ensure_ascii=False), encoding="utf-8")
    return tmp


class CaptureBoundaryTests(unittest.TestCase):
    def test_allowlist_refuses_every_mutation_argv(self):
        cli = instr.CaptureCli(runner=canned_capture_runner())
        forbidden = [
            ["agent", "update", LEAD_ID, "--instructions", "x"],
            ["agent", "skills", "add", LEAD_ID, "--skill-ids", "s"],
            ["agent", "skills", "set", LEAD_ID, "--skill-ids", "s"],
            ["squad", "update", SQUAD_ID, "--instructions", "x"],
            ["skill", "import", "x"],
            ["issue", "assign", "YZT-1", "--to", "a"],
            ["issue", "create", "--title", "x"],
        ]
        for argv in forbidden:
            with self.assertRaises(instr.StagingError):
                cli._run(argv)
        self.assertEqual(cli.commands, [])

    def test_capture_records_only_allowlisted_argv_and_zero_guarantees(self):
        with tempfile.TemporaryDirectory() as td:
            cli = instr.CaptureCli(runner=canned_capture_runner())
            baseline = instr.capture(Path(td), cli=cli)
            self.assertTrue(baseline["trace"]["readonly_allowlist_enforced"])
            self.assertEqual(baseline["trace"]["guarantees"], instr.GUARANTEES)
            self.assertEqual([a["role"] for a in baseline["agents"]],
                             list(instr.V22_ROLES))
            dr = baseline["agents"][4]
            self.assertEqual(dr["role"], "delivery-reviewer")
            self.assertEqual(dr["agent_name"], "05 Feature Reviewer")
            self.assertEqual(dr["staged_agent_name"], "05 Delivery Reviewer")
            self.assertNotIn("feature-reviewer",
                             [a["role"] for a in baseline["agents"]])

    def test_capture_fails_closed_on_05_display_name_drift(self):
        agents = fake_raw_agents()
        for a in agents:
            if a["id"] == instr.AGENT05_UUID:
                a["name"] = "05 Delivery Reviewer"
        with self.assertRaises(instr.CaptureError):
            instr.capture(
                Path(tempfile.mkdtemp()),
                cli=instr.CaptureCli(runner=canned_capture_runner(agents)))

    def test_capture_fails_closed_on_missing_agent_uuid(self):
        agents = [a for a in fake_raw_agents()
                  if a["id"] != instr.EXPECTED_AGENT_IDS["qa"]]
        with self.assertRaises(instr.CaptureError):
            instr.capture(
                Path(tempfile.mkdtemp()),
                cli=instr.CaptureCli(runner=canned_capture_runner(agents)))


class RoleRetirementTests(unittest.TestCase):
    def test_six_roles_resolve_and_feature_reviewer_does_not(self):
        for role in instr.V22_ROLES:
            self.assertTrue(instr.resolve_logical_role(role)["ok"], role)
        retired = instr.resolve_logical_role("feature-reviewer")
        self.assertFalse(retired["ok"])
        self.assertIsNone(retired["role"])
        self.assertIsNone(retired["alias_to"])
        self.assertEqual(retired["code"], "retired_no_alias")

    def test_05_uuid_maps_only_to_delivery_reviewer(self):
        mapped = instr.uuid_to_logical_role(instr.AGENT05_UUID)
        self.assertEqual(mapped["role"], "delivery-reviewer")
        self.assertNotEqual(mapped["role"], "feature-reviewer")

    def test_old_package_cannot_activate_new_05(self):
        result = instr.package_activation_check(
            {"role": "feature-reviewer", "identity": "feature-reviewer",
             "skill_names": list(instr.LEGACY_05_SKILLS)},
            "delivery-reviewer")
        self.assertFalse(result["ok"])
        self.assertEqual(result["self_check"], "REFRESH_REQUIRED")
        self.assertFalse(result["trigger_eligible"])
        self.assertIn("old_role_package_rejected", result["reasons"])
        self.assertIn("no_alias_rewrite", result["reasons"])

    def test_runtime_adapter_rejects_feature_reviewer(self):
        proof = instr.runtime_role_vocabulary_proof()
        self.assertTrue(proof["ok"], proof)
        self.assertTrue(proof["adapter_rejects_feature_reviewer"])
        self.assertTrue(proof["feature_reviewer_profile_absent"])
        self.assertTrue(proof["delivery_reviewer_profile_present"])


class BundleContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "cap"
        cli = instr.CaptureCli(runner=canned_capture_runner())
        self.baseline = instr.capture(self.out, cli=cli)
        self.bundle = instr.build_bundle(self.baseline, REPO)

    def test_05_is_full_replacement(self):
        after = self.bundle["after"]["delivery-reviewer"]
        base = self.baseline["agents"][4]["instruction_text"]
        self.assertFalse(after.startswith(base))
        self.assertIn("05 Delivery Reviewer", after)
        self.assertNotIn("User and External Environment Sensor", after)
        self.assertIn("targeted external fact verification", after)
        self.assertIn("Artifact Lens", after)

    def test_01_04_06_keep_owner_sentences(self):
        by_role = {a["role"]: a for a in self.baseline["agents"]}
        for role, sentence in OWNER_SENTENCES.items():
            self.assertIn(sentence, self.bundle["after"][role], role)
            if role != "delivery-reviewer":
                self.assertIn(sentence, by_role[role]["instruction_text"])

    def test_common_protocol_on_all_six(self):
        for role in instr.V22_ROLES:
            self.assertIn("artifact_ready_check", self.bundle["after"][role], role)
            self.assertIn("SELF_CHECK", self.bundle["after"][role], role)
            self.assertIn("恰好一次 Multica 触发", self.bundle["after"][role], role)

    def test_02_exception_only_even_when_bound(self):
        ctx = self.bundle["after"]["context-engineer"]
        self.assertIn("不使你成为普通路径目标", ctx)
        self.assertIn("handoff hop", ctx)
        adds = [b for b in self.bundle["plan"]["bindings"]
                if b.get("op") == "add"
                and b.get("agent_role") == "context-engineer"
                and (b.get("skill") or {}).get("name") == instr.SKILL_NAME]
        self.assertEqual(len(adds), 1)

    def test_binding_plan_all_six_add_remove_never_set(self):
        plan = self.bundle["plan"]
        self.assertTrue(plan["never_replace_all_set"])
        self.assertTrue(plan["context_handoff_planned_for_all_six"])
        self.assertFalse(any(b.get("op") == "set" for b in plan["bindings"]))
        roles = sorted({b["agent_role"] for b in plan["bindings"]
                        if b.get("op") == "add"
                        and (b.get("skill") or {}).get("name") == instr.SKILL_NAME})
        self.assertEqual(roles, sorted(instr.V22_ROLES))
        removed = {b["skill_name"] for b in plan["bindings"]
                   if b.get("op") == "remove"
                   and b.get("agent_role") == "delivery-reviewer"}
        self.assertEqual(removed, set(instr.LEGACY_05_SKILLS))
        after_names = {s["name"] for s in
                       plan["after_bindings"]["delivery-reviewer"]}
        self.assertNotIn("external-signal-research", after_names)
        self.assertNotIn("feature-correctness-review", after_names)
        self.assertIn("parent-handoff-wake", after_names)

    def test_placeholders_have_no_invented_uuid(self):
        for b in self.bundle["plan"]["bindings"]:
            if b.get("op") != "add":
                continue
            skill = b["skill"]
            self.assertIsNone(skill["workspace_skill_id"])
            self.assertEqual(skill["id_status"],
                             "enablement_placeholder_until_u12_import")
            self.assertTrue(skill["content_digest"].startswith("sha256:"))

    def test_squad_option_a_and_no_fanout(self):
        squad = self.bundle["after"]["squad"]
        self.assertIn("never assume member fan-out", squad)
        self.assertIn("producer auto-trigger of 05", squad)
        self.assertIn("Delivery Reviewer auto-trigger of 06", squad)
        self.assertIn("retired with no alias", squad)

    def test_u04_digests_pinned(self):
        self.assertEqual(self.bundle["skill"]["skill_md_digest"],
                         instr.PINNED_SKILL_MD)
        self.assertEqual(self.bundle["artifact_contract_revision"],
                         instr.PINNED_ARTIFACT_CONTRACT)

    def test_rollback_restores_exact_baseline(self):
        rb = self.bundle["rollback"]
        by_role = {a["role"]: a for a in self.baseline["agents"]}
        self.assertEqual(len(rb["restores"]), 6)
        for r in rb["restores"]:
            a = by_role[r["agent_role"]]
            self.assertEqual(r["baseline_instruction_text"],
                             a["instruction_text"])
            self.assertEqual(r["baseline_binding_ids"], a["skill_binding_ids"])
        self.assertIn("`set` appears only in this declarative rollback",
                      rb["notes"]["binding_set_usage"])


class CommittedBundleTests(unittest.TestCase):
    def test_committed_baseline_is_v22(self):
        if not BASELINE.is_file():
            self.skipTest("committed baseline not generated yet")
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["schema_version"], instr.BASELINE_SCHEMA)
        self.assertEqual([a["role"] for a in baseline["agents"]],
                         list(instr.V22_ROLES))
        self.assertEqual(baseline["agents"][4]["agent_id"], instr.AGENT05_UUID)
        self.assertEqual(baseline["agents"][4]["role"], "delivery-reviewer")

    def test_build_is_byte_deterministic_and_matches_committed_bundle(self):
        if not BASELINE.is_file():
            self.skipTest("committed baseline not generated yet")
        with tempfile.TemporaryDirectory() as td1, \
                tempfile.TemporaryDirectory() as td2:
            instr.build(Path(td1), BASELINE, REPO)
            instr.build(Path(td2), BASELINE, REPO)
            for sub in ("candidates", "after"):
                for p in sorted((Path(td1) / sub).iterdir()):
                    self.assertEqual(
                        p.read_text(encoding="utf-8"),
                        (Path(td2) / sub / p.name).read_text(encoding="utf-8"))
                    committed = ART / sub / p.name
                    self.assertTrue(committed.is_file(), committed)
                    self.assertEqual(p.read_text(encoding="utf-8"),
                                     committed.read_text(encoding="utf-8"))
            for name in ("binding-plan.json", "preconditions.json",
                         "rollback.json", "bundle.json", "role-mapping.json",
                         "package-invalidation.json"):
                a = (Path(td1) / name).read_text(encoding="utf-8")
                b = (Path(td2) / name).read_text(encoding="utf-8")
                c = (ART / name).read_text(encoding="utf-8")
                self.assertEqual(a, b, name)
                self.assertEqual(a, c, name)

    def test_verify_passes_against_unchanged_live_state(self):
        if not BASELINE.is_file():
            self.skipTest("committed baseline not generated yet")
        report = instr.verify(BASELINE, ART, mode="stage")
        self.assertTrue(report["ok"], report["checks_failed"])
        self.assertEqual(report["drift"], [])

    def test_verify_fails_closed_on_instruction_digest_drift(self):
        if not BASELINE.is_file():
            self.skipTest("committed baseline not generated yet")
        agents = json.loads((ART / "agent-list.json").read_text(encoding="utf-8"))
        for a in agents:
            if a["name"] == "03 Solution Architect":
                a["instructions"] += "\n\n尾部被篡改的一句。\n"
        tmp = Path(tempfile.mkdtemp())
        cap = write_capture_dir(
            agents,
            json.loads((ART / "skill-list.json").read_text(encoding="utf-8")),
            tmp)
        squad = json.loads((ART / "squad-get.json").read_text(encoding="utf-8"))
        (cap / "squad-get.json").write_text(
            json.dumps(squad), encoding="utf-8")
        report = instr.verify(BASELINE, cap)
        self.assertFalse(report["ok"])
        self.assertIn("instruction_digest_mismatch", report["drift"])

    def test_audit_proves_non_activation_and_done_criteria(self):
        if not BASELINE.is_file():
            self.skipTest("committed baseline not generated yet")
        audit = instr.audit(BASELINE, ART)
        self.assertTrue(audit["ok"], audit["failed_checks"])
        done = audit["done"]
        self.assertTrue(done["six_v2_roles_resolve"])
        self.assertTrue(done["feature_reviewer_retired_without_alias"])
        self.assertFalse(done["old_05_package_accepted"])
        self.assertEqual(done["feature_reviewer_activation"], 0)
        self.assertEqual(done["live_writes_or_triggers"], 0)
        self.assertEqual(done["unaccounted_open_findings_at_completion"], 0)

    def test_no_feature_reviewer_after_file(self):
        self.assertFalse((ART / "after" / "feature-reviewer.md").exists())
        self.assertFalse((ART / "candidates" / "feature-reviewer.md").exists())
        self.assertTrue((ART / "after" / "delivery-reviewer.md").is_file()
                        or not BASELINE.is_file())


class ScanExemptionTests(unittest.TestCase):
    def test_frozen_boundary_scan_stays_clean_and_u05_stays_out(self):
        self.assertEqual(t00.scan_handoff_contracts(), {})
        source = (TOOLS / "chandoff.py").read_text(encoding="utf-8")
        self.assertNotIn("chandoff_instructions", source)

    def test_no_drive_letter_in_generated_deltas(self):
        if not (ART / "candidates" / "engineering-lead.md").is_file():
            self.skipTest("bundle not generated yet")
        import re as _re
        pattern = _re.compile(r"\b[A-Za-z]:[\\/]|/Users/|/home/", _re.I)
        for p in sorted(ART.glob("candidates/*.md")):
            text = p.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), p.name)


class CliExitTests(unittest.TestCase):
    def test_resolve_role_exit_codes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(instr.main(["resolve-role", "delivery-reviewer"]), 0)
            self.assertEqual(instr.main(["resolve-role", "feature-reviewer"]), 2)

    def test_verify_cli_exit_codes_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            baseline_dir = Path(td) / "base"
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


if __name__ == "__main__":
    unittest.main()
