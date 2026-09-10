#!/usr/bin/env python3
"""T09 focused tests — assignment handoff main path (YZT-64).

Every path runs against fixture/injected runners only: no live issue
create/comment/assign/mention/run, no Canonical write, no model call. The
fake runner keeps an in-memory platform state so publish -> discovery ->
trigger round-trips deterministically.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import chandoff_assignment as asm  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_note as note  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402

CLOCK = lambda: "2026-09-10T09:00:00Z"  # noqa: E731

AGENT_SE = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
AGENT_LEAD = "24f04aba-7da9-4371-bf89-685d7505a411"
AGENT_SA = "1303827b-73d1-4d71-a461-00b93e4b4418"
PARENT_UUID = "99999999-0000-0000-0000-000000000039"

TITLE = "T09 assignment handoff main path drill"
DESCRIPTION = (
    "Drill the fail-closed assignment orchestrator main path in the isolated "
    "worktree: create unassigned issue, prepare, publish, confirm, assign "
    "exactly once, self-check before work."
)

COMPOSE = lambda plan_obj, request, errors=None: compose.subset_result(plan_obj)  # noqa: E731
MAIN_TRANSITIONS = ["INIT", "ISSUE_CREATED", "TARGET_RESOLVED",
                    "HANDOFF_PREPARED", "HANDOFF_PUBLISHED",
                    "HANDOFF_READY_CONFIRMED", "ASSIGNMENT_TRIGGERED",
                    "TARGET_SELF_CHECKED", "COMPLETED"]


def base_spec(**overrides) -> dict:
    spec = {
        "title": TITLE,
        "description": DESCRIPTION,
        "project_id": "web-imagegen",
        "purpose": "implementation",
        "options": {"limit": 8},
    }
    spec.update(overrides)
    return {k: v for k, v in spec.items() if v is not None}


def fake_store():
    from chandoff_plan import MemoryFindingStore
    return MemoryFindingStore([])


class FakeMultica:
    """Stateful in-memory stand-in for the deployed multica CLI."""

    def __init__(self, *, version="v0.4.42", create_fails=False,
                 create_malformed=False, create_exit=2,
                 create_assignee=None, assign_exit=0, assign_stdout=None,
                 assign_applies=True, drop_note=False, note_corruptor=None,
                 extra_record_body=None):
        self.version = version
        self.create_fails = create_fails
        self.create_malformed = create_malformed
        self.create_assignee_id = None
        self.assign_exit = assign_exit
        self.assign_stdout = assign_stdout
        self.assign_applies = assign_applies
        self.drop_note = drop_note
        self.note_corruptor = note_corruptor
        self.extra_record_body = extra_record_body
        self.issues: dict = {}
        self.by_identifier: dict = {}
        self.comments: dict = {}
        self.assign_calls: list = []
        self.create_calls: list = []
        self.created_response: dict | None = None
        self.seq = 0
        self.issues[PARENT_UUID] = {
            "id": PARENT_UUID,
            "identifier": "YZT-39X",
            "title": "Umbrella parent issue",
            "description": "Parent umbrella for the drill.",
            "parent_issue_id": None,
            "project_id": None,
            "assignee_id": None,
            "assignee": None,
        }
        self.by_identifier["YZT-39X"] = self.issues[PARENT_UUID]

    def __call__(self, argv):
        tail = [str(a) for a in argv[1:]]
        if tail[:1] == ["version"]:
            return 0, json.dumps({"version": self.version}), ""
        if tail[:2] == ["issue", "get"]:
            doc = self.issues.get(tail[2]) or self.by_identifier.get(tail[2])
            if doc is None:
                return 2, "", "issue not found"
            return 0, json.dumps(doc), ""
        if tail[:3] == ["issue", "comment", "list"]:
            return 0, json.dumps(self.comments.get(tail[3], [])), ""
        if tail[:3] == ["issue", "comment", "add"]:
            issue_id = tail[3]
            body = Path(tail[tail.index("--content-file") + 1]).read_bytes() \
                .decode("utf-8")
            self.seq += 1
            comment = {
                "id": f"c-{self.seq:04d}",
                "content": body,
                "created_at": f"2026-09-10T08:00:{self.seq % 60:02d}Z",
                "parent_id": None,
                "author_id": "caller",
                "author_type": "agent",
                "issue_id": issue_id,
            }
            self.comments.setdefault(issue_id, []).append(comment)
            if self.note_corruptor:
                comment["content"] = self.note_corruptor(body)
            if self.extra_record_body is not None:
                self.seq += 1
                self.comments[issue_id].append({
                    "id": f"c-{self.seq:04d}",
                    "content": self.extra_record_body,
                    "created_at": f"2026-09-10T08:00:{self.seq % 60:02d}Z",
                    "parent_id": None, "author_id": "other",
                    "author_type": "agent", "issue_id": issue_id,
                })
            if self.drop_note:
                self.comments[issue_id] = []
            return 0, json.dumps({"id": f"c-{self.seq:04d}" if not self.drop_note
                                  else "c-lost", "created_at":
                                  f"2026-09-10T08:00:{self.seq % 60:02d}Z",
                                  "parent_id": None}), ""
        if tail[:2] == ["issue", "create"]:
            self.seq += 1
            self.create_calls.append(list(tail))
            if self.create_fails:
                return 2, "", "create failed"
            if self.create_malformed:
                return 0, "<not json>", ""
            uuid_hex = f"11111111-2222-3333-4444-{self.seq:012d}"
            identifier = f"YZT-{9000 + self.seq}"
            issue = {
                "id": uuid_hex,
                "identifier": identifier,
                "title": tail[tail.index("--title") + 1],
                "description": Path(tail[tail.index("--description-file") + 1]) \
                    .read_bytes().decode("utf-8"),
                "parent_issue_id": None,
                "project_id": None,
                "assignee_id": self.create_assignee_id,
                "assignee": None,
                "status": "todo",
            }
            if "--parent" in tail:
                issue["parent_issue_id"] = tail[tail.index("--parent") + 1]
            if "--project" in tail:
                issue["project_id"] = tail[tail.index("--project") + 1]
            self.issues[uuid_hex] = issue
            self.by_identifier[identifier] = issue
            self.created_response = dict(issue)
            return 0, json.dumps(issue), ""
        if tail[:2] == ["issue", "assign"]:
            issue_id = tail[2]
            agent_id = tail[tail.index("--to-id") + 1]
            self.assign_calls.append((issue_id, agent_id))
            if self.assign_exit:
                return self.assign_exit, "", "assign failed"
            if self.assign_applies and issue_id in self.issues:
                self.issues[issue_id]["assignee_id"] = agent_id
            out = self.assign_stdout if self.assign_stdout is not None \
                else json.dumps({"id": issue_id, "assignee_id": agent_id})
            return 0, out, ""
        return 2, "", "unexpected command"


def run_tx(fake=None, *, spec=None, caller="engineering-lead",
           target="software-engineer", tx="tx-t09-0001", parent=PARENT_UUID,
           ledger=None, policy=None, world=None, finding_store=None,
           bundle_dir=None):
    spec = dict(spec if spec is not None else base_spec())
    if parent is not None and "parent_issue_id" not in spec:
        spec["parent_issue_id"] = parent
    ledger = ledger if ledger is not None else dispatch.TransactionLedger()
    fake = fake if fake is not None else FakeMultica()
    result = asm.run_assignment_handoff(
        spec, caller_role=caller, target_role_spec=target, runner=fake,
        ledger=ledger, compose_fn=COMPOSE, transaction_id=tx,
        policy=policy, clock=CLOCK, finding_store=finding_store or fake_store(),
        world=world, bundle_dir=bundle_dir)
    return result, fake, ledger


class DispatcherUnitTests(unittest.TestCase):
    def test_command_classification_is_exact(self):
        cases = {
            ("multica", "issue", "create", "--title", "t"): "issue_create",
            ("multica", "issue", "assign", "I", "--to-id", "A"): "assignment_trigger",
            ("multica", "issue", "comment", "add", "I", "--content-file", "p"): "comment_publish",
            ("multica", "issue", "get", "I"): "read",
            ("multica", "issue", "comment", "list", "I"): "read",
            ("multica", "version"): "read",
            ("multica", "issue", "update", "I"): "other",
        }
        for argv, expected in cases.items():
            self.assertEqual(dispatch.classify_command(list(argv)), expected)

    def test_audit_flags_forbidden_assignment_flags(self):
        good = ["multica", "issue", "assign", "I", "--to-id",
                "00000000-0000-0000-0000-000000000001", "--output", "json"]
        bad = ["multica", "issue", "assign", "I", "--no-start", "--to-id",
               "00000000-0000-0000-0000-000000000001"]
        self.assertTrue(dispatch._assign_argv_ok(good, "multica"))
        self.assertFalse(dispatch._assign_argv_ok(bad, "multica"))

    def test_audit_counts_mention_hits(self):
        records = [{"kind": "command", "seq": 1, "command_class": "read",
                    "argv": ["multica", "issue", "get", "mention://agent/x"]}]
        audit = dispatch.audit_ledger(records)
        self.assertEqual(audit["mention_hits"], [1])
        self.assertFalse(audit["ok"])

    def test_ledger_jsonl_round_trip(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({"kind": "command", "argv": ["multica", "version"],
                       "command_class": "read"})
        ledger.append({"kind": "command_result", "exit_code": 0})
        text = ledger.to_jsonl()
        loaded = dispatch.TransactionLedger.from_jsonl(text)
        self.assertEqual(loaded.to_jsonl(), text)
        self.assertEqual(len(loaded.records), 2)

    def test_authorization_gate_is_explicit_and_separate(self):
        self.assertFalse(dispatch.authorization_ok(None))
        self.assertFalse(dispatch.authorization_ok({}))
        self.assertFalse(dispatch.authorization_ok(
            {"authorize_live_mutations": True}))
        self.assertFalse(dispatch.authorization_ok(
            {"authorize_live_mutations": True, "authorized_by": "x",
             "authorization_ref": "y"}))
        self.assertTrue(dispatch.authorization_ok(
            {"authorize_live_mutations": True, "authorized_by": "human",
             "authorization_ref": "YZT-64", "scope": "one handoff"}))
        with self.assertRaises(dispatch.NotAuthorizedError):
            dispatch.authorized_runner(authorization=None)
        with self.assertRaises(dispatch.NotAuthorizedError):
            dispatch.authorized_runner(authorization={"authorize_live_mutations": True})

    def test_fixture_runner_matches_first_prefix(self):
        runner = dispatch.FixtureRunner([
            {"match": ["version"], "stdout": json.dumps({"version": "vX"})},
        ])
        code, out, err = runner(["multica", "version", "--output", "json"])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("vX", out)
        code, out, err = runner(["multica", "issue", "get", "X"])
        self.assertEqual(code, 2)


class CreateBoundaryTests(unittest.TestCase):
    def test_mention_in_title_refused_with_zero_commands(self):
        ledger = dispatch.TransactionLedger()
        issued = []

        def runner(argv):
            issued.append(argv)
            return 0, "{}", ""

        result = asm.run_assignment_handoff(
            base_spec(title="do it [mention://agent/x] now"),
            caller_role="engineering-lead", target_role_spec="software-engineer",
            runner=runner, ledger=ledger, compose_fn=COMPOSE,
            transaction_id="tx-bad-mention", finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        self.assertEqual(issued, [])
        self.assertEqual(ledger.commands(), [])

    def test_blank_spec_fields_refused(self):
        for spec in (base_spec(title="   "), base_spec(description=""),
                     base_spec(project_id=""), {}):
            ledger = dispatch.TransactionLedger()
            result, _, _ = run_tx(
                FakeMultica(), spec=spec, ledger=ledger,
                tx="tx-bad-spec")
            self.assertEqual(result["terminal_status"], "INVALID_INPUT")
            self.assertEqual(ledger.commands(), [])

    def test_unknown_caller_role_refused(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), caller="intern", ledger=ledger)
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        self.assertEqual(ledger.commands(), [])

    def test_create_carries_no_assignee_flag_ever(self):
        result, fake, ledger = run_tx()
        argv = ledger.commands()[0]["argv"]
        self.assertNotIn("--assignee", argv)
        self.assertNotIn("--assignee-id", argv)
        self.assertIn("--description-file", argv)
        self.assertNotIn("--description", argv)
        self.assertEqual(result["issue"]["identifier"], "YZT-9001")
        self.assertEqual(fake.created_response["assignee_id"], None)

    def test_created_issue_with_assignee_fails_closed(self):
        fake = FakeMultica()
        fake.create_assignee_id = AGENT_SE
        result, _, ledger = run_tx(fake)
        self.assertEqual(result["terminal_status"], "CREATE_NOT_UNASSIGNED")
        self.assertEqual(len(ledger.commands()), 1)
        self.assertEqual(result["audit"]["assignment_trigger"]["count"], 0)

    def test_create_malformed_response_is_not_guessed(self):
        fake = FakeMultica(create_malformed=True)
        result, _, _ = run_tx(fake)
        self.assertEqual(result["terminal_status"], "CREATE_RESPONSE_INVALID")
        self.assertIsNone(result["issue"])

    def test_create_command_failure_is_unverified_never_retried(self):
        fake = FakeMultica(create_fails=True)
        result, _, ledger = run_tx(fake)
        self.assertEqual(result["terminal_status"], "CREATE_UNVERIFIED")
        self.assertEqual(len(ledger.commands()), 1)

    def test_description_temp_file_deleted(self):
        result, fake, _ = run_tx()
        stray = [p for p in Path.cwd().glob(".t09-create-*.md")]
        self.assertEqual(stray, [])


class MainPathTests(unittest.TestCase):
    def test_lead_creates_engineer_child_issue(self):
        result, fake, ledger = run_tx()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["terminal_status"], "COMPLETED")
        self.assertEqual(result["transitions"], MAIN_TRANSITIONS)
        self.assertEqual(result["issue"]["identifier"], "YZT-9001")
        self.assertEqual(result["target"]["agent_id"], AGENT_SE)
        self.assertEqual(result["caller"]["agent_id"], AGENT_LEAD)
        self.assertTrue(result["handoff_ready"])
        self.assertEqual(result["trigger"]["count"], 1)
        self.assertTrue(result["trigger"]["confirmed"])
        self.assertEqual(result["trigger"]["argv"],
                         ["multica", "issue", "assign", "11111111-2222-3333-4444-000000000001",
                          "--to-id", AGENT_SE, "--output", "json"])
        self.assertEqual(fake.assign_calls,
                         [("11111111-2222-3333-4444-000000000001", AGENT_SE)])
        self.assertEqual(result["self_check"]["status"], "READY")
        self.assertEqual(result["self_check"]["attempts"], 1)
        audit = result["audit"]
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["command_counts"].get("issue_create"), 1)
        self.assertEqual(audit["command_counts"].get("assignment_trigger"), 1)
        self.assertEqual(audit["command_counts"].get("comment_publish"), 1)
        self.assertEqual(audit["mention_hits"], [])
        evidence = asm.acceptance_evidence(result, ledger)
        self.assertEqual(evidence["run_before_handoff"], 0)
        self.assertEqual(evidence["intended_run_count_per_handoff"], 1)
        self.assertTrue(evidence["assignment_is_only_trigger"])
        self.assertTrue(evidence["published_handoff_confirmed_before_assignment"])
        self.assertTrue(evidence["self_check_ready_before_work"])
        self.assertEqual(evidence["live_mutations"], 0)

    def test_architect_creates_engineer_issue_without_parent(self):
        result, fake, _ = run_tx(caller="solution-architect", parent=None,
                                 tx="tx-sa-se")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["caller"]["agent_id"], AGENT_SA)
        self.assertIsNone(fake.issues["11111111-2222-3333-4444-000000000001"]
                          ["parent_issue_id"])

    def test_publish_uses_content_file_and_body_is_non_trigger(self):
        result, fake, _ = run_tx()
        self.assertTrue(result["ok"], result)
        body = fake.comments["11111111-2222-3333-4444-000000000001"][0]["content"]
        self.assertEqual(body.split("\n")[0], "/note")
        self.assertNotIn("mention://", body)
        self.assertIn("CONTEXT_HANDOFF_RECORD", body)

    def test_ledger_is_deterministic_across_identical_runs(self):
        _, _, ledger_a = run_tx(tx="tx-det")
        _, _, ledger_b = run_tx(tx="tx-det")

        def normalize(text: str) -> str:
            import re
            return re.sub(r"\.t06-note-[0-9a-f]+\.md", "<t06-temp>",
                          text.replace("\\", "/"))

        self.assertEqual(normalize(ledger_a.to_jsonl()),
                         normalize(ledger_b.to_jsonl()))

    def test_commands_are_strictly_ordered_in_ledger(self):
        result, _, ledger = run_tx()
        classes = [r["command_class"] for r in ledger.commands()]
        self.assertEqual(classes[0], "issue_create")
        self.assertEqual(classes[-1], "assignment_trigger")
        publishes = [i for i, c in enumerate(classes)
                     if c == "comment_publish"]
        assigns = [i for i, c in enumerate(classes) if c == "assignment_trigger"]
        self.assertEqual(len(assigns), 1)
        self.assertTrue(all(p < assigns[0] for p in publishes))
        transitions = [r["to"] for r in ledger.records
                       if r.get("kind") == "state_transition"]
        self.assertEqual(transitions, MAIN_TRANSITIONS[1:])


class RoutingTests(unittest.TestCase):
    def test_unknown_role_routes_with_created_unassigned_issue(self):
        result, fake, ledger = run_tx(target="qa-engineer", tx="tx-route")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertIsNotNone(result["issue"])
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("issue_create"), 1)
        self.assertEqual(counts.get("assignment_trigger", 0), 0)
        self.assertEqual(counts.get("comment_publish", 0), 0)
        self.assertEqual(result["escalation"]["route_to"],
                         "engineering-lead-or-squad")

    def test_context_engineer_is_never_a_dispatch_target(self):
        result, _, _ = run_tx(target="context-engineer", tx="tx-ce")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")

    def test_stale_bundle_without_baseline_fails_closed(self):
        result, _, _ = run_tx(bundle_dir=Path(tempfile.mkdtemp()),
                              tx="tx-stale-bundle")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")

    def test_stale_bundle_missing_role_mapping_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "baseline.json").write_text(json.dumps({
                "schema_version": "T08-baseline/1.0",
                "agents": [{"role": "engineering-lead", "agent_id": AGENT_LEAD,
                            "agent_name": "01 Engineering Lead"}],
            }), encoding="utf-8")
            (bundle / "bundle.json").write_text(json.dumps({
                "schema_version": "T08-bundle/1.0", "status": "x"}),
                encoding="utf-8")
            (bundle / "binding-plan.json").write_text(json.dumps({
                "schema_version": "T08-binding-plan/1.0",
                "bindings": [{"agent_role": "engineering-lead"},
                             {"agent_role": "software-engineer"}]}),
                encoding="utf-8")
            result, _, _ = run_tx(bundle_dir=bundle, tx="tx-stale-role")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertIn("baseline", (result["stop_reason"] or "").lower())

    def test_role_not_bound_in_t08_plan_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            (bundle / "baseline.json").write_text(json.dumps({
                "schema_version": "T08-baseline/1.0",
                "agents": [{"role": "feature-reviewer",
                            "agent_id": "b6335f8e-8147-45f7-aac0-8079d85423b5",
                            "agent_name": "05 Feature Reviewer"}],
            }), encoding="utf-8")
            (bundle / "bundle.json").write_text(json.dumps({
                "schema_version": "T08-bundle/1.0"}), encoding="utf-8")
            (bundle / "binding-plan.json").write_text(json.dumps({
                "schema_version": "T08-binding-plan/1.0", "bindings": []}),
                encoding="utf-8")
            result, _, _ = run_tx(bundle_dir=bundle, tx="tx-unbound")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")


def world_rule(**overrides) -> dict:
    doc = {
        "_kind": "rule",
        "id": "RULE-T09000001",
        "kind": "rule",
        "schema_version": "1.1",
        "title": "assignment handoff fixture rule",
        "statement": "assignment orchestrator provider boundary rule",
        "modality": "MUST",
        "status": "active",
        "verification": "verified",
        "authority_refs": ["multica://issue/YZT-40"],
        "scope": {"type": "project", "project_id": "web-imagegen",
                  "projects": [], "task_id": None},
    }
    doc.update(overrides)
    return doc


def blocking_finding(task_ref: str) -> dict:
    return {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": "FIND-WIMG-T09-000001",
        "project_id": "web-imagegen",
        "task_id": task_ref,
        "summary": "unresolved durable candidate on the handoff task",
        "detail": None,
        "intent": "durable_candidate",
        "source_refs": ["repo://web-imagegen@main/README.md"],
        "discovered_by": "software-engineer",
        "status": "open",
        "verification": "unverified",
        "created_at": "2026-09-10T00:00:00Z",
    }


class PrepareStopTests(unittest.TestCase):
    def test_blocked_finding_stops_before_publish_and_assignment(self):
        ledger = dispatch.TransactionLedger()
        finding = blocking_finding("multica://issue/YZT-9001")
        result, _, _ = run_tx(FakeMultica(), world={"findings": [finding]},
                              ledger=ledger, tx="tx-blocked")
        self.assertEqual(result["terminal_status"], "PREPARE_BLOCKED")
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("issue_create"), 1)
        self.assertEqual(counts.get("comment_publish", 0), 0)
        self.assertEqual(counts.get("assignment_trigger", 0), 0)
        self.assertTrue(result["escalation"]["required"])

    def test_partial_stops_by_default(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(
            FakeMultica(),
            world={"docs": [world_rule(status="review_needed")]},
            ledger=ledger, tx="tx-partial")
        self.assertEqual(result["terminal_status"], "PREPARE_PARTIAL_STOPPED")
        self.assertTrue(result["extra"]["gaps"])
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("comment_publish", 0), 0)
        self.assertEqual(counts.get("assignment_trigger", 0), 0)

    def test_partial_continues_only_with_explicit_policy_and_one_trigger(self):
        policy = {"allow_partial_publication": True,
                  "authorized_by": "engineering-lead"}
        result, fake, ledger = run_tx(
            FakeMultica(),
            world={"docs": [world_rule(status="review_needed")]},
            policy=policy, tx="tx-partial-ok")
        self.assertNotEqual(result["terminal_status"], "PREPARE_PARTIAL_STOPPED")
        decisions = [r for r in ledger.records
                     if r.get("kind") == "policy_decision"]
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0]["gaps"])
        body = fake.comments["11111111-2222-3333-4444-000000000001"][0]["content"]
        self.assertEqual(body.split("\n")[0], "/note")
        self.assertEqual(result["trigger"]["count"], 1)
        self.assertNotIn(result["terminal_status"], (asm.COMPLETED,))
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("assignment_trigger"), 1)


class PublishConfirmTests(unittest.TestCase):
    def test_lost_note_stops_before_assignment(self):
        fake = FakeMultica(drop_note=True)
        result, _, _ = run_tx(fake, tx="tx-drop")
        self.assertEqual(result["terminal_status"], "CONFIRMATION_FAILED")
        self.assertEqual(result["audit"]["command_counts"]
                         .get("assignment_trigger", 0), 0)

    def test_newer_foreign_package_fails_confirmation(self):
        ledger = dispatch.TransactionLedger()
        result_a, fake_a, _ = run_tx(tx="tx-env-source")
        self.assertTrue(result_a["ok"])
        env = _captured_envelope(fake_a)
        env2 = copy.deepcopy(env)
        env2["package_id"] = "CTX-software-engineer-ffff0000ffff0000"
        body2, _ = note.render_note_record(
            env2, prepared_by=AGENT_LEAD, prepared_at="2026-09-10T08:00:59Z")
        fake = FakeMultica(extra_record_body=body2)
        result, _, _ = run_tx(fake, ledger=ledger, tx="tx-foreign")
        self.assertEqual(result["terminal_status"], "CONFIRMATION_FAILED")
        self.assertEqual(result["audit"]["command_counts"]
                         .get("assignment_trigger", 0), 0)

    def test_corrupted_published_record_fails_closed(self):
        fake = FakeMultica(
            note_corruptor=lambda body: body.replace("software-engineer", "qa"))
        result, _, _ = run_tx(fake, tx="tx-corrupt")
        self.assertEqual(result["terminal_status"], "CONFIRMATION_FAILED")
        self.assertEqual(result["audit"]["command_counts"]
                         .get("assignment_trigger", 0), 0)


def _captured_envelope(fake: FakeMultica) -> dict:
    body = fake.comments["11111111-2222-3333-4444-000000000001"][0]["content"]
    parsed = note._parse_record(body)
    return parsed["envelope"]


class TriggerFailureTests(unittest.TestCase):
    def test_assignment_command_failure_stops_without_retry(self):
        fake = FakeMultica(assign_exit=3)
        result, _, ledger = run_tx(fake, tx="tx-assign-fail")
        self.assertEqual(result["terminal_status"], "TRIGGER_COMMAND_FAILED")
        self.assertEqual(len(fake.assign_calls), 1)
        assign_records = [r for r in ledger.commands()
                          if r["command_class"] == "assignment_trigger"]
        self.assertEqual(len(assign_records), 1)
        self.assertFalse(result["trigger"]["confirmed"])

    def test_ambiguous_assignment_response_fails_closed(self):
        fake = FakeMultica(assign_stdout="OK\n")
        result, _, _ = run_tx(fake, tx="tx-ambiguous")
        self.assertEqual(result["terminal_status"],
                         "TRIGGER_CONFIRMATION_REQUIRED")
        self.assertEqual(len(fake.assign_calls), 1)
        self.assertFalse(result["trigger"]["confirmed"])
        self.assertTrue(result["stop_reason"].startswith("assignment response"))

    def test_reconcile_read_only_confirms_or_reports(self):
        fake = FakeMultica(assign_stdout="OK\n")
        result, _, ledger = run_tx(fake, tx="tx-amb-rec")
        self.assertEqual(result["terminal_status"],
                         "TRIGGER_CONFIRMATION_REQUIRED")
        recon = asm.reconcile_trigger(
            "11111111-2222-3333-4444-000000000001", AGENT_SE, runner=fake,
            ledger=ledger, transaction_id="reconcile-1")
        self.assertTrue(recon["ok"])
        self.assertTrue(recon["trigger_confirmed"])
        assign_after = [r for r in ledger.commands()
                        if r["command_class"] == "assignment_trigger"
                        and r.get("transaction_id") == "reconcile-1"]
        self.assertEqual(assign_after, [])

        fresh_ledger = dispatch.TransactionLedger()
        recon_fake = FakeMultica()
        recon_fake.issues["11111111-2222-3333-4444-000000000001"] = {
            "id": "11111111-2222-3333-4444-000000000001",
            "identifier": "YZT-9001", "title": "t", "description": "d",
            "parent_issue_id": None, "project_id": None,
            "assignee_id": None, "assignee": None,
        }
        recon2 = asm.reconcile_trigger(
            "11111111-2222-3333-4444-000000000001", AGENT_SE,
            runner=recon_fake, ledger=fresh_ledger,
            transaction_id="reconcile-2")
        self.assertTrue(recon2["ok"])
        self.assertFalse(recon2["trigger_confirmed"])


class ReplayTests(unittest.TestCase):
    def test_completed_replay_issues_zero_commands(self):
        result, _, ledger = run_tx(tx="tx-replay")
        self.assertTrue(result["ok"])
        commands_before = len(ledger.commands())
        replayed, fake2, _ = run_tx(FakeMultica(), ledger=ledger,
                                    tx="tx-replay")
        self.assertTrue(replayed.get("replayed"))
        self.assertEqual(replayed["commands"], [])
        self.assertEqual(replayed["trigger"]["count"], 1)
        self.assertEqual(len(ledger.commands()), commands_before)
        self.assertEqual(fake2.assign_calls, [])

    def test_incomplete_replay_is_refused_not_retried(self):
        fake = FakeMultica(assign_exit=3)
        result, _, ledger = run_tx(fake, tx="tx-incomplete")
        self.assertEqual(result["terminal_status"], "TRIGGER_COMMAND_FAILED")
        commands_before = len(ledger.commands())
        refused, _, _ = run_tx(FakeMultica(), ledger=ledger,
                               tx="tx-incomplete")
        self.assertEqual(refused["terminal_status"], "REPLAY_REFUSED")
        self.assertEqual(len(ledger.commands()), commands_before)

    def test_ledger_file_round_trip_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            result, _, ledger = run_tx(tx="tx-save")
            self.assertTrue(result["ok"])
            ledger.save(path)
            loaded = dispatch.TransactionLedger.load(path)
            self.assertEqual(loaded.to_jsonl(), ledger.to_jsonl())
            replayed = asm.replay_transaction("tx-save", loaded)
            self.assertEqual(replayed["terminal_status"], "COMPLETED")
            self.assertEqual(replayed["result"]["trigger"]["count"], 1)


class SelfCheckTests(unittest.TestCase):
    def test_refresh_required_recovers_within_bound(self):
        live = selfcheck.current_revisions()
        stale = dict(live)
        stale["memory_revision"] = "sha256:" + "0" * 64
        calls = {"n": 0}

        def provider():
            calls["n"] += 1
            return stale if calls["n"] == 1 else live

        result, fake, ledger = run_tx(world={"current": provider},
                                      tx="tx-refresh-ok")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["self_check"]["status"], "READY")
        self.assertEqual(result["self_check"]["attempts"], 2)
        self.assertEqual(result["self_check"]["refreshes"], 1)
        self.assertEqual(len(fake.assign_calls), 1)
        comments = fake.comments["11111111-2222-3333-4444-000000000001"]
        self.assertEqual(len(comments), 1)
        skipped = [r for r in ledger.records
                   if r.get("kind") == "refresh_publish_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(asm.acceptance_evidence(result, ledger)
                         ["intended_run_count_per_handoff"], 1)

    def test_second_unresolved_refresh_stops_work(self):
        live = selfcheck.current_revisions()
        stale = dict(live)
        stale["memory_revision"] = "sha256:" + "f" * 64
        result, fake, _ = run_tx(world={"current": lambda: stale},
                                 tx="tx-refresh-loop")
        self.assertEqual(result["terminal_status"], "SELF_REFRESH_EXHAUSTED")
        self.assertEqual(result["self_check"]["attempts"], 2)
        self.assertEqual(len(fake.assign_calls), 1)

    def test_self_check_blocked_stops_before_work(self):
        task_ref = "multica://issue/YZT-9001"
        finding = blocking_finding(task_ref)
        result, fake, _ = run_tx(tx="tx-self-blocked")
        self.assertTrue(result["ok"], result)

        class LateStore:
            def __init__(self):
                self.calls = 0

            def load_open(self):
                self.calls += 1
                return [] if self.calls == 1 else [dict(finding)]

            def save(self, finding):
                pass

            def mark_processed(self, finding, disposition, note):
                return finding

        late = LateStore()
        result2, _, _ = run_tx(finding_store=late, tx="tx-self-blocked2")
        self.assertEqual(result2["terminal_status"], "SELF_CHECK_BLOCKED")
        self.assertEqual(result2["audit"]["command_counts"]
                         .get("assignment_trigger"), 1)
        self.assertFalse(result2["ok"])


class CliTests(unittest.TestCase):
    def _run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOLS / "chandoff_assignment.py"), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
            cwd=str(TOOLS.parent))

    def test_cli_resolve_role(self):
        proc = self._run_cli("resolve-role", "--role-spec", "software-engineer")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        doc = json.loads(proc.stdout)
        self.assertEqual(doc["target"]["agent_id"], AGENT_SE)

    def test_cli_resolve_unknown_role_routes(self):
        proc = self._run_cli("resolve-role", "--role-spec", "astronaut")
        self.assertEqual(proc.returncode, 3)
        doc = json.loads(proc.stdout)
        self.assertTrue(doc["routing_required"])

    def test_cli_run_invalid_spec_zero_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps({"description": "no title"}),
                                 encoding="utf-8")
            fixture_path = Path(tmp) / "fixture.json"
            fixture_path.write_text("[]", encoding="utf-8")
            ledger_path = Path(tmp) / "ledger.jsonl"
            proc = self._run_cli(
                "run", "--spec-file", str(spec_path),
                "--fixture-runner-file", str(fixture_path),
                "--transaction-id", "tx-cli-invalid",
                "--caller-role", "engineering-lead",
                "--target-role", "software-engineer",
                "--ledger-file", str(ledger_path))
            self.assertEqual(proc.returncode, 2, proc.stdout)
            doc = json.loads(proc.stdout)
            self.assertEqual(doc["result"]["terminal_status"], "INVALID_INPUT")
            ledger = dispatch.TransactionLedger.load(ledger_path)
            self.assertEqual(ledger.commands(), [])

    def test_cli_audit_and_replay_on_recorded_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = dispatch.TransactionLedger()
            result, _, _ = run_tx(ledger=ledger, tx="tx-cli-evidence")
            self.assertTrue(result["ok"], result)
            path = Path(tmp) / "ledger.jsonl"
            ledger.save(path)
            proc = self._run_cli("audit", "--ledger-file", str(path))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["ok"])
            proc = self._run_cli("replay", "--transaction-id", "tx-cli-evidence",
                                 "--ledger-file", str(path))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["terminal_status"],
                             "COMPLETED")


if __name__ == "__main__":
    unittest.main()
