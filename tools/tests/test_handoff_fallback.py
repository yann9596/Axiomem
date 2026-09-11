#!/usr/bin/env python3
"""U08 focused tests — cross-route reconciliation + Assignment fallback.

Every path runs against fixture/injected runners only: no live
issue/comment/assignment/status/run write, no native mention, no Canonical
write, no model call. The fake runner keeps an in-memory platform state so
publish -> discovery -> assignment -> run correlation round-trips
deterministically.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import chandoff_assignment as asm  # noqa: E402
import chandoff  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_fallback as fb  # noqa: E402
import chandoff_instructions as instr  # noqa: E402
import chandoff_mention as men  # noqa: E402

CLOCK = lambda: "2026-09-10T09:00:00Z"  # noqa: E731

AGENT_LEAD = "24f04aba-7da9-4371-bf89-685d7505a411"
AGENT_SA = "1303827b-73d1-4d71-a461-00b93e4b4418"
AGENT_SE = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
AGENT_DR = "b6335f8e-8147-45f7-aac0-8079d85423b5"
AGENT_QA = "30ce43d4-97a7-42a8-ab3e-78df0d894702"
AGENT_CE = "8bc546ab-ffd8-4aa6-ad30-58583346c065"
OTHER_AGENT = "77777777-7777-7777-7777-777777777777"

ISSUE_ID = "22222222-0000-0000-0000-000000000065"
IDENTIFIER = "YZT-900"
TASK_REF = "multica://issue/" + IDENTIFIER
TITLE = "Existing drill issue"
DESCRIPTION = "Drill: - Requirement: build X\n- Acceptance: works"

RETIRED_FR = next(k for k in instr.RETIRED_ROLES if "feature" in k)
LIVE_FR_NAME = instr.LIVE_DISPLAY_NAME["delivery-reviewer"]

COMPOSE = lambda plan_obj, request, errors=None: compose.subset_result(plan_obj)  # noqa: E731

UNSET = object()


class FakeMultica:
    """Stateful in-memory stand-in for the deployed multica CLI."""

    simulation_transport = True

    def __init__(self, *, version="v0.4.42", issue_id=ISSUE_ID,
                 identifier=IDENTIFIER, title=TITLE, description=DESCRIPTION,
                 assignee=None, project="web-imagegen", get_fails=False,
                 get_malformed=False, drop_note=False, note_corruptor=None,
                 extra_runs=None, runs_fail=False, runs_malformed=False,
                 runs_fail_after=None, truncate_runs=False,
                 assign_exit=0, assign_stdout=None, assign_applies=True,
                 duplicate_assign_run=False, flip_assignee_on_write=None,
                 flip_to=OTHER_AGENT, auto_target_run=True):
        self.version = version
        self.get_fails = get_fails
        self.get_malformed = get_malformed
        self.drop_note = drop_note
        self.note_corruptor = note_corruptor
        self.issues: dict = {}
        self.by_identifier: dict = {}
        self.comments: dict = {}
        self.comment_add_calls: list = []
        self.get_calls: list = []
        self.create_calls: list = []
        self.update_calls: list = []
        self.assign_calls: list = []
        self.seq = 0
        self.runs: list = list(extra_runs or [])
        self.runs_fail = runs_fail
        self.runs_malformed = runs_malformed
        self.runs_fail_after = runs_fail_after
        self.runs_calls = 0
        self.truncate_runs = truncate_runs
        self.assign_exit = assign_exit
        self.assign_stdout = assign_stdout
        self.assign_applies = assign_applies
        self.duplicate_assign_run = duplicate_assign_run
        self.flip_assignee_on_write = flip_assignee_on_write
        self.flip_to = flip_to
        self.auto_target_run = auto_target_run
        doc = {
            "id": issue_id, "identifier": identifier, "title": title,
            "description": description, "parent_issue_id": None,
            "project_id": project, "assignee_id": assignee, "assignee": None,
            "status": "in_progress",
        }
        self.issues[issue_id] = doc
        self.by_identifier[identifier] = doc

    def __call__(self, argv):
        tail = [str(a) for a in argv[1:]]
        if tail[:1] == ["version"]:
            return 0, json.dumps({"version": self.version}), ""
        if tail[:2] == ["issue", "get"]:
            self.get_calls.append(tail[2])
            if self.get_fails:
                return 2, "", "issue not found"
            doc = self.issues.get(tail[2]) or self.by_identifier.get(tail[2])
            if doc is None:
                return 2, "", "issue not found"
            if self.get_malformed:
                return 0, "<not json>", ""
            return 0, json.dumps(doc), ""
        if tail[:3] == ["issue", "comment", "list"]:
            return 0, json.dumps(self.comments.get(tail[3], [])), ""
        if tail[:3] == ["issue", "comment", "add"]:
            issue_id = tail[3]
            body = Path(tail[tail.index("--content-file") + 1]) \
                .read_bytes().decode("utf-8")
            self.seq += 1
            self.comment_add_calls.append(issue_id)
            if self.flip_assignee_on_write is not None \
                    and self.seq >= self.flip_assignee_on_write:
                self.issues[issue_id]["assignee_id"] = self.flip_to
                self.issues[issue_id]["assignee"] = self.flip_to
            comment = {
                "id": f"c-{self.seq:04d}",
                "content": body,
                "created_at": f"2026-09-10T08:00:{self.seq % 60:02d}Z",
                "parent_id": None, "author_id": "caller",
                "author_type": "agent", "issue_id": issue_id,
            }
            self.comments.setdefault(issue_id, []).append(comment)
            if self.note_corruptor:
                comment["content"] = self.note_corruptor(body)
            if self.drop_note:
                self.comments[issue_id] = []
            return 0, json.dumps({
                "id": f"c-{self.seq:04d}" if not self.drop_note else "c-lost",
                "created_at": comment["created_at"],
                "parent_id": None}), ""
        if tail[:2] == ["issue", "runs"]:
            self.runs_calls += 1
            if self.runs_fail or (
                    self.runs_fail_after is not None
                    and self.runs_calls > self.runs_fail_after):
                return 2, "", "runs failed"
            if self.runs_malformed:
                return 0, '{"not":"a list"}', ""
            issue_id = tail[2]
            active = "--active" in tail
            siblings = "--siblings" in tail
            rows = []
            for run in self.runs:
                if run["issue_id"] == issue_id or (
                        siblings and run.get("sibling")):
                    rows.append(run)
            if active:
                rows = [r for r in rows if r["status"] in (
                    "queued", "dispatched", "running",
                    "waiting_local_directory")]
            err = "truncated at cap" if self.truncate_runs else ""
            return 0, json.dumps(rows), err
        if tail[:2] == ["issue", "assign"]:
            issue_id = tail[2]
            agent_id = tail[tail.index("--to-id") + 1]
            self.assign_calls.append((issue_id, agent_id))
            if self.assign_exit:
                return self.assign_exit, "", "assign failed"
            if self.assign_applies and issue_id in self.issues:
                self.issues[issue_id]["assignee_id"] = agent_id
                self.issues[issue_id]["assignee"] = agent_id
            if self.auto_target_run:
                self.seq += 1
                run = {
                    "id": f"run-{self.seq:04d}",
                    "issue_id": issue_id,
                    "agent_id": agent_id,
                    "status": "queued",
                }
                self.runs.append(run)
                if self.duplicate_assign_run:
                    self.seq += 1
                    duplicate = dict(run)
                    duplicate["id"] = f"run-{self.seq:04d}"
                    self.runs.append(duplicate)
            out = self.assign_stdout if self.assign_stdout is not None \
                else json.dumps({"id": issue_id, "assignee_id": agent_id})
            return 0, out, ""
        if tail[:2] == ["issue", "update"]:
            self.update_calls.append(list(tail))
            issue_id = tail[2]
            doc = self.issues.get(issue_id) or self.by_identifier.get(issue_id)
            if doc is None:
                return 2, "", "issue not found"
            return 0, json.dumps(doc), ""
        if tail[:2] == ["issue", "create"]:
            self.create_calls.append(list(tail))
            return 2, "", "create must never run in the fallback"
        return 2, "", "unexpected command"


def fake_store():
    from chandoff_plan import MemoryFindingStore
    return MemoryFindingStore([])


def base_source_spec(**overrides) -> dict:
    spec = {
        "issue_id": ISSUE_ID,
        "project_id": "web-imagegen",
        "purpose": "implementation",
        "options": {"limit": 8},
    }
    spec.update(overrides)
    return {k: v for k, v in spec.items() if v is not None}


def flaky_compose(calls: dict):
    def compose_fn(plan_obj, request, errors=None):
        calls["n"] = calls.get("n", 0) + 1
        if calls["n"] == 1:
            raise RuntimeError("transient prepare failure")
        return compose.subset_result(plan_obj)
    return compose_fn


def seed_mention_crash(*, fake=None, ledger=None, tx="tx-src-crash",
                       crash_at="HANDOFF_PREPARED", caller="solution-architect",
                       target="software-engineer"):
    ledger = ledger if ledger is not None else dispatch.TransactionLedger()
    fake = fake if fake is not None else FakeMultica()
    result = men.run_mention_handoff(
        base_source_spec(), caller_role=caller, target_role_spec=target,
        runner=fake, ledger=ledger, compose_fn=COMPOSE, transaction_id=tx,
        clock=CLOCK, finding_store=fake_store(), stage="ready",
        crash_at=crash_at)
    assert result["terminal_status"] == "CRASH_SIMULATED", result
    return ledger, fake


def seed_mention_stop(*, mode="pre_publish", fake=None, ledger=None,
                      tx="tx-src-stop", caller="solution-architect",
                      target="software-engineer"):
    ledger = ledger if ledger is not None else dispatch.TransactionLedger()
    fake = fake if fake is not None else FakeMultica()
    calls: dict = {}
    compose_fn = flaky_compose(calls) if mode == "pre_publish" else COMPOSE
    if mode == "post_publish":
        fake.runs_fail_after = 2
    result = men.run_mention_handoff(
        base_source_spec(), caller_role=caller, target_role_spec=target,
        runner=fake, ledger=ledger, compose_fn=compose_fn, transaction_id=tx,
        clock=CLOCK, finding_store=fake_store(), stage="ready")
    expected = {"pre_publish": "PREPARE_FAILED",
                "post_publish": "RUN_STATE_UNDETERMINED"}[mode]
    assert result["terminal_status"] == expected, (result, calls)
    if mode == "post_publish":
        fake.runs_fail_after = None
    return ledger, fake


def reconcile_for(fake, ledger, *, source_tx, target="software-engineer",
                  runs=None, observed_assignee=UNSET, tx="tx-recon"):
    issue = fake.issues[ISSUE_ID]
    if observed_assignee is UNSET:
        observed_assignee = {"assignee_id": issue.get("assignee_id"),
                             "assignee": issue.get("assignee")}
    target_entry = asm.resolve_assignment_target(target)
    run_evidence = {"trusted": True, "reason": None,
                    "runs": list(runs or []),
                    "runs_digest": fb._digest(list(runs or []))}
    return fb.reconcile_source_route(
        ledger.records, transaction_id=tx, source_transaction_id=source_tx,
        issue=issue, task_ref=TASK_REF, target=target_entry,
        caller={"role": "engineering-lead", "agent_id": AGENT_LEAD},
        observed_assignee=observed_assignee, run_evidence=run_evidence,
        artifact_dependency_digest="sha256:" + "0" * 64, clock=CLOCK)


def make_authorization(recon, *, reassignment=None, expected_before=UNSET,
                       **overrides):
    source = recon["source"]
    if expected_before is UNSET:
        expected_before = recon["observed_assignee"].get("assignee_id")
    auth = {
        "kind": "cross_route_authorization",
        "schema_version": "U08-cross-route-authorization/2.2",
        "authorization_ref": "YZT-66#u08-auth-0001",
        "authorized_by_role": "engineering-lead",
        "authorized_by_agent_id": AGENT_LEAD,
        "issue_id": recon["issue"]["id"],
        "issue_identifier": recon["issue"]["identifier"],
        "source_transaction_id": recon["source_transaction_id"],
        "source_route": "mention",
        "source_terminal_evidence": {
            "transaction_id": recon["source_transaction_id"],
            "terminal_status": source.get("terminal_status"),
            "package_id": source.get("package_id"),
            "source_evidence_digest": recon["source_evidence_digest"],
        },
        "supersedes_package_id": source.get("package_id"),
        "target_role": recon["target"]["role"],
        "target_agent_id": recon["target"]["agent_id"],
        "expected_assignee_before": expected_before,
        "fallback_reason": "mention route stopped with zero trigger/run; "
                           "direct assignment fallback authorized",
        "reassignment": reassignment,
        "created_at": "2026-09-10T12:00:00Z",
    }
    auth.update(overrides)
    return auth


def fallback_spec(**overrides) -> dict:
    spec = {
        "issue_id": ISSUE_ID,
        "project_id": "web-imagegen",
        "purpose": "implementation",
        "options": {"limit": 8},
    }
    spec.update(overrides)
    return {k: v for k, v in spec.items() if v is not None}


def run_fallback(fake=None, *, spec=None, ledger=None, caller="engineering-lead",
                 target="software-engineer", tx="tx-u08-0001",
                 source_tx="tx-src-stop", authorization=UNSET,
                 policy=None, compose_fn=None, crash_at=None):
    spec = dict(spec if spec is not None else fallback_spec())
    ledger = ledger if ledger is not None else dispatch.TransactionLedger()
    fake = fake if fake is not None else FakeMultica()
    if authorization is UNSET:
        recon = reconcile_for(fake, ledger, source_tx=source_tx)
        authorization = make_authorization(recon)
    result = fb.run_assignment_fallback(
        spec, caller_role=caller, target_role_spec=target, runner=fake,
        ledger=ledger, compose_fn=compose_fn or COMPOSE,
        transaction_id=tx, source_transaction_id=source_tx,
        authorization=authorization, policy=policy, clock=CLOCK,
        finding_store=fake_store(), crash_at=crash_at)
    return result, fake, ledger


def assignment_records(ledger, tx):
    return [r for r in ledger.records if r.get("transaction_id") == tx]


class ClassificationTests(unittest.TestCase):
    def test_pre_publish_stop_is_no_trigger_proven(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        self.assertEqual(recon["classification"], "NO_TRIGGER_PROVEN")
        self.assertEqual(recon["boundary"], "source_pre_publish")
        self.assertEqual(recon["source"]["trigger_count"], 0)
        self.assertEqual(recon["source"]["run_count"], 0)
        self.assertTrue(recon["source_evidence_digest"].startswith("sha256:"))

    def test_post_publish_stop_is_no_trigger_proven(self):
        ledger, fake = seed_mention_stop(mode="post_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        self.assertEqual(recon["classification"], "NO_TRIGGER_PROVEN")
        self.assertEqual(recon["boundary"], "source_post_publish_pre_ready")
        self.assertTrue(recon["source"]["package_id"])
        self.assertTrue(recon["source"]["comment_id"])

    def test_crash_without_result_is_route_resumable(self):
        ledger, fake = seed_mention_crash()
        recon = reconcile_for(fake, ledger, source_tx="tx-src-crash")
        self.assertEqual(recon["classification"], "ROUTE_RESUMABLE")
        self.assertEqual(recon["next_action"], "resume_mention_route")

    def test_staged_mention_ready_is_ambiguous(self):
        ledger = dispatch.TransactionLedger()
        fake = FakeMultica()
        result = men.run_mention_handoff(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-staged", clock=CLOCK,
            finding_store=fake_store(), stage="ready")
        self.assertEqual(result["terminal_status"], "MENTION_READY")
        recon = reconcile_for(fake, ledger, source_tx="tx-staged")
        self.assertEqual(recon["classification"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(recon["boundary"], "mention_ready_pre_native_send")

    def test_confirmed_receipt_pre_run_is_triggered(self):
        tx = "tx-receipt"
        ledger2 = dispatch.TransactionLedger()
        fake2 = FakeMultica()
        men.run_mention_handoff(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake2, ledger=ledger2,
            compose_fn=COMPOSE, transaction_id=tx, clock=CLOCK,
            finding_store=fake_store(), stage="ready")
        executed = men.run_mention_handoff(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake2, ledger=ledger2,
            compose_fn=COMPOSE, transaction_id=tx, clock=CLOCK,
            finding_store=fake_store(), stage="execute",
            mention_evidence={
                "kind": "native_mention_evidence",
                "schema_version": "U07-mention-evidence/2.2",
                "transaction_id": tx, "issue_id": ISSUE_ID,
                "comment_id": "c-0002",
                "created_at": "2026-09-10T10:00:00Z",
                "author_agent_id": AGENT_SA,
                "author_surface": "native_agent_reply",
                "mentions": [{"agent_id": AGENT_SE,
                              "role": "software-engineer",
                              "link": "mention://agent/" + AGENT_SE}],
                "plain_text_handles": [],
                "assignment_mutation": None,
            })
        self.assertEqual(executed["terminal_status"], "RUN_CORRELATION_FAILED")
        recon = reconcile_for(fake2, ledger2, source_tx=tx)
        self.assertEqual(recon["classification"], "TRIGGERED")
        self.assertEqual(recon["boundary"],
                         "native_receipt_confirmed_pre_run")

    def test_completed_source_is_completed(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({
            "kind": "transaction_result",
            "transaction_id": "tx-done",
            "terminal_status": "COMPLETED",
            "stage": "full",
            "result": {
                "ok": True, "issue": {"id": ISSUE_ID,
                                      "identifier": IDENTIFIER},
                "task_ref": TASK_REF,
                "target": {"role": "software-engineer", "agent_id": AGENT_SE},
                "trigger": {"type": "mention", "count": 1, "confirmed": True},
            },
        })
        recon = reconcile_for(FakeMultica(), ledger, source_tx="tx-done")
        self.assertEqual(recon["classification"], "COMPLETED")
        self.assertEqual(recon["next_action"], "replay_zero_side_effects")

    def test_unknown_source_transaction(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-never-existed")
        self.assertEqual(recon["classification"], "SOURCE_UNKNOWN")

    def test_untrusted_run_evidence_never_proves_zero_trigger(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        issue = fake.issues[ISSUE_ID]
        target = asm.resolve_assignment_target("software-engineer")
        recon = fb.reconcile_source_route(
            ledger.records, transaction_id="tx-r", source_transaction_id=
            "tx-src-stop", issue=issue, task_ref=TASK_REF, target=target,
            caller={"role": "engineering-lead", "agent_id": AGENT_LEAD},
            observed_assignee={"assignee_id": None, "assignee": None},
            run_evidence={"trusted": False, "reason": "run_state_undetermined",
                          "runs": []},
            clock=CLOCK)
        self.assertEqual(recon["classification"], "EVIDENCE_UNTRUSTED")
        self.assertEqual(recon["suggested_status"], "RUN_STATE_UNDETERMINED")

    def test_truncated_run_evidence_blocks_before_trigger(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.truncate_runs = True
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertEqual(result["terminal_status"], "EVIDENCE_UNTRUSTED")
        self.assertFalse(result["ok"])
        self.assertEqual(fake.assign_calls, [])

    def test_unexpected_active_run_blocks(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        runs = [{"id": "run-x", "issue_id": ISSUE_ID, "agent_id": AGENT_SE,
                 "status": "running"}]
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop",
                              runs=runs)
        self.assertEqual(recon["classification"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(recon["boundary"], "unexpected_active_run")
        self.assertEqual(recon["suggested_status"], "UNEXPECTED_RUN")

    def test_target_run_blocks_even_when_completed(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        runs = [{"id": "run-old", "issue_id": ISSUE_ID, "agent_id": AGENT_SE,
                 "status": "completed"}]
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop",
                              runs=runs)
        self.assertEqual(recon["classification"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(recon["boundary"],
                         "target_run_may_derive_from_mention")

    def test_wrong_agent_run_blocks(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        runs = [{"id": "run-other", "issue_id": ISSUE_ID, "agent_id": AGENT_QA,
                 "status": "completed"}]
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop",
                              runs=runs)
        self.assertEqual(recon["classification"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(recon["suggested_status"], "UNEXPECTED_RUN")

    def test_pending_mutating_command_is_untrusted(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({"kind": "assignee_observation",
                       "transaction_id": "tx-pending", "phase": "before",
                       "issue_id": ISSUE_ID, "assignee_id": None})
        ledger.append({"kind": "command", "transaction_id": "tx-pending",
                       "command_class": "comment_publish",
                       "argv": ["multica", "issue", "comment", "add"]})
        ledger.append({"kind": "state_transition", "transaction_id":
                       "tx-pending", "from": "HANDOFF_PUBLISHED",
                       "to": "HANDOFF_PUBLISHED"})
        recon = reconcile_for(FakeMultica(), ledger, source_tx="tx-pending")
        self.assertEqual(recon["classification"], "EVIDENCE_UNTRUSTED")
        self.assertEqual(recon["boundary"], "pending_mutating_command")

    def test_route_collision_is_ambiguous(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({"kind": "command", "transaction_id": "tx-both",
                       "command_class": "assignment_trigger",
                       "argv": ["multica", "issue", "assign", ISSUE_ID]})
        ledger.append({"kind": "mention_outcome", "transaction_id": "tx-both",
                       "outcome": "confirmed",
                       "mention_comment_id": "c-0002"})
        recon = reconcile_for(FakeMultica(), ledger, source_tx="tx-both")
        self.assertEqual(recon["classification"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(recon["boundary"], "route_collision")

    def test_assignment_uncertain_issuance(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({"kind": "command", "transaction_id": "tx-assign",
                       "command_class": "assignment_trigger",
                       "argv": ["multica", "issue", "assign", ISSUE_ID]})
        ledger.append({"kind": "command_result", "transaction_id": "tx-assign",
                       "exit_code": 0})
        recon = reconcile_for(FakeMultica(), ledger, source_tx="tx-assign")
        self.assertEqual(recon["classification"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(recon["suggested_status"],
                         "ASSIGNMENT_CONFIRMATION_REQUIRED")

    def test_assignment_route_stopped_never_falls_back_to_mention(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({"kind": "recovery_context", "transaction_id":
                       "tx-assign-stop", "caller_role": "engineering-lead"})
        ledger.append({"kind": "transaction_result", "transaction_id":
                       "tx-assign-stop", "terminal_status": "PACKAGE_STALE",
                       "result": {"ok": False,
                                  "trigger": {"type": "issue_assign",
                                              "count": 0}}})
        recon = reconcile_for(FakeMultica(), ledger, source_tx="tx-assign-stop")
        self.assertEqual(recon["classification"],
                         "SOURCE_ROUTE_UNSUPPORTED")
        self.assertEqual(recon["suggested_status"], "V2_2_REBASE_BLOCKED")


class AuthorizationTests(unittest.TestCase):
    def build(self, *, mode="pre_publish", **auth_overrides):
        ledger, fake = seed_mention_stop(mode=mode)
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon, **auth_overrides)
        return recon, auth

    def test_valid_authorization_accepts(self):
        recon, auth = self.build()
        doc = fb.validate_cross_route_authorization(
            auth, reconciliation=recon)
        self.assertFalse(doc["intentional_reassignment"])
        self.assertTrue(doc["digest"].startswith("sha256:"))

    def test_non_lead_authorization_is_rejected(self):
        recon, auth = self.build()
        auth["authorized_by_role"] = "solution-architect"
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)

    def test_wrong_lead_agent_is_rejected(self):
        recon, auth = self.build()
        auth["authorized_by_agent_id"] = AGENT_SA
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)

    def test_mention_ready_envelope_is_never_authorization(self):
        recon, _ = self.build()
        ready = men.mention_ready_envelope(
            transaction_id="tx-src-stop",
            issue={"id": ISSUE_ID, "identifier": IDENTIFIER},
            caller={"role": "solution-architect", "agent_id": AGENT_SA},
            target={"role": "software-engineer", "role_name":
                    "04 Software Engineer", "agent_id": AGENT_SE,
                    "agent_name": "04 Software Engineer"},
            package={"package_id": "pkg-1", "status": "READY"},
            published_comment_id="c-0001",
            published_comment_created_at="2026-09-10T08:00:01Z")
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(ready, reconciliation=recon)

    def test_stale_evidence_digest_is_rejected(self):
        recon, auth = self.build()
        auth["source_terminal_evidence"]["source_evidence_digest"] = \
            "sha256:" + "f" * 64
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)

    def test_wrong_issue_target_or_package_is_rejected(self):
        recon, auth = self.build()
        for field, value in (
                ("issue_id", "33333333-0000-0000-0000-000000000099"),
                ("issue_identifier", "YZT-999"),
                ("target_agent_id", OTHER_AGENT),
                ("target_role", "qa"),
                ("supersedes_package_id", "CTX-other-0000")):
            modified = json.loads(json.dumps(auth))
            modified[field] = value
            with self.assertRaises(fb.AuthorizationError, msg=field):
                fb.validate_cross_route_authorization(
                    modified, reconciliation=recon)

    def test_assignee_drift_is_rejected(self):
        recon, auth = self.build()
        auth["expected_assignee_before"] = OTHER_AGENT
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)

    def test_reassignment_requires_exact_prior_assignee_and_reason(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.issues[ISSUE_ID]["assignee_id"] = AGENT_SA
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)
        auth["reassignment"] = {"prior_assignee_id": OTHER_AGENT,
                                "reason": "route switch"}
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)
        auth["reassignment"] = {"prior_assignee_id": AGENT_SA,
                                "reason": "Lead authorized route switch"}
        doc = fb.validate_cross_route_authorization(auth, reconciliation=recon)
        self.assertTrue(doc["intentional_reassignment"])

    def test_already_assigned_to_target_is_rejected(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.issues[ISSUE_ID]["assignee_id"] = AGENT_SE
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)

    def test_mention_link_in_authorization_is_rejected(self):
        recon, auth = self.build()
        auth["fallback_reason"] = "route via mention://agent/" + AGENT_SE
        with self.assertRaises(fb.AuthorizationError):
            fb.validate_cross_route_authorization(auth, reconciliation=recon)


class MainPathTests(unittest.TestCase):
    def test_pre_publish_source_fallback_publishes_one_new_note(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        spec = fallback_spec()
        result, _, _ = run_fallback(fake=fake, ledger=ledger, spec=spec)
        self.assertEqual(result["terminal_status"], "COMPLETED", result)
        self.assertTrue(result["ok"])
        self.assertEqual(len(fake.comment_add_calls), 1)
        self.assertEqual(len(fake.assign_calls), 1)
        self.assertEqual(fake.update_calls, [])
        self.assertEqual(fake.create_calls, [])
        self.assertEqual(result["trigger"]["count"], 1)
        self.assertTrue(result["trigger"]["confirmed"])
        self.assertEqual(result["intended_run"]["count"], 1)
        self.assertEqual(result["self_check"]["status"], "READY")
        audit = result["audit"]["fallback_audit"]
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["comment_publish"]["count"], 1)
        self.assertEqual(audit["assignment_trigger"]["count"], 1)
        self.assertEqual(audit["mention_authorizations"], 0)
        self.assertEqual(result["classification"], "NO_TRIGGER_PROVEN")
        self.assertTrue(result["cross_route_audit"]["ok"])

    def test_post_publish_source_fallback_reuses_note_without_duplicate(self):
        ledger, fake = seed_mention_stop(mode="post_publish")
        source_note = fake.comments[ISSUE_ID][0]["id"]
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        result = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-fb-post",
            source_transaction_id="tx-src-stop", authorization=auth,
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "COMPLETED", result)
        self.assertEqual(len(fake.comment_add_calls), 1)
        self.assertEqual(fake.comments[ISSUE_ID][0]["id"], source_note)
        self.assertEqual(result["fallback_result"]["package"]["package_id"],
                         recon["source"]["package_id"])
        self.assertEqual(len(fake.assign_calls), 1)
        audit = result["audit"]["fallback_audit"]
        self.assertEqual(audit["comment_publish"]["count"], 0)

    def test_closed_source_cannot_be_resumed(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        result, _, _ = run_fallback(ledger=ledger, fake=fake)
        self.assertEqual(result["terminal_status"], "COMPLETED", result)
        resumed = fb.resume_source_route(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, source_transaction_id="tx-src-stop")
        self.assertEqual(resumed["terminal_status"], "ROUTE_CONFLICT")

    def test_fallback_result_is_deterministic(self):
        ledger1, fake1 = seed_mention_stop(mode="pre_publish")
        first, _, _ = run_fallback(fake=fake1, ledger=ledger1)
        ledger2, fake2 = seed_mention_stop(mode="pre_publish")
        second, _, _ = run_fallback(fake=fake2, ledger=ledger2)
        self.assertEqual(
            chandoff.canonical_json(first), chandoff.canonical_json(second),
            "same evidence + authorization must produce byte-identical "
            "decision/result artifacts")
        replayed = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake2,
            ledger=ledger2, compose_fn=COMPOSE, transaction_id="tx-u08-0001",
            source_transaction_id="tx-src-stop", authorization={},
            clock=CLOCK, finding_store=fake_store())
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["commands"], [])

    def test_completed_replay_is_idempotent(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertEqual(result["terminal_status"], "COMPLETED")
        before_records = len(ledger.records)
        before_adds = list(fake.comment_add_calls)
        before_assigns = list(fake.assign_calls)
        replayed = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-u08-0001",
            source_transaction_id="tx-src-stop",
            authorization=make_authorization(
                reconcile_for(fake, ledger, source_tx="tx-src-stop")),
            clock=CLOCK, finding_store=fake_store())
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["commands"], [])
        self.assertEqual(len(ledger.records), before_records)
        self.assertEqual(fake.comment_add_calls, before_adds)
        self.assertEqual(fake.assign_calls, before_assigns)

    def test_decision_replay_is_idempotent(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        first, _, _ = run_fallback(fake=fake, ledger=ledger,
                                   authorization=None, tx="tx-decision")
        self.assertEqual(first["terminal_status"], "AUTHORIZATION_REQUIRED")
        before = len(ledger.records)
        second, _, _ = run_fallback(fake=fake, ledger=ledger,
                                    authorization=None, tx="tx-decision")
        self.assertTrue(second["replayed"])
        self.assertEqual(second["commands"], [])
        self.assertEqual(len(ledger.records), before)

    def test_transaction_id_must_differ_from_source(self):
        result, fake, _ = run_fallback(tx="tx-src-stop")
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        self.assertEqual(fake.assign_calls, [])

    def test_completed_source_opens_no_fallback(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({
            "kind": "transaction_result", "transaction_id": "tx-done",
            "terminal_status": "COMPLETED", "stage": "full",
            "result": {"ok": True, "issue": {"id": ISSUE_ID,
                                             "identifier": IDENTIFIER},
                       "task_ref": TASK_REF,
                       "target": {"role": "software-engineer",
                                  "agent_id": AGENT_SE},
                       "trigger": {"type": "mention", "count": 1,
                                   "confirmed": True}}})
        fake = FakeMultica()
        result, _, _ = run_fallback(fake=fake, ledger=ledger,
                                    source_tx="tx-done",
                                    authorization=None, tx="tx-fb")
        self.assertEqual(result["terminal_status"], "COMPLETED")
        self.assertEqual(fake.assign_calls, [])
        self.assertEqual(fake.comment_add_calls, [])


class AmbiguityTests(unittest.TestCase):
    def test_staged_mention_never_falls_back(self):
        ledger = dispatch.TransactionLedger()
        fake = FakeMultica()
        men.run_mention_handoff(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-staged", clock=CLOCK,
            finding_store=fake_store(), stage="ready")
        result, _, _ = run_fallback(
            fake=fake, ledger=ledger, source_tx="tx-staged",
            authorization=None, tx="tx-fb")
        self.assertEqual(result["terminal_status"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(fake.assign_calls, [])
        self.assertEqual(len(fake.comment_add_calls), 1)
        self.assertFalse(any(r.get("kind") == "cross_route_closure"
                             for r in ledger.records))

    def test_mention_ready_execute_without_receipt_stays_ambiguous(self):
        ledger = dispatch.TransactionLedger()
        fake = FakeMultica()
        men.run_mention_handoff(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-noreceipt", clock=CLOCK,
            finding_store=fake_store(), stage="ready")
        men.run_mention_handoff(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-noreceipt", clock=CLOCK,
            finding_store=fake_store(), stage="execute", mention_evidence=None)
        result, _, _ = run_fallback(
            fake=fake, ledger=ledger, source_tx="tx-noreceipt",
            authorization=None, tx="tx-fb")
        self.assertEqual(result["terminal_status"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(fake.assign_calls, [])

    def test_mention_correlated_run_never_falls_back(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({
            "kind": "mention_outcome", "transaction_id": "tx-run",
            "outcome": "confirmed", "mention_comment_id": "c-0002",
            "package_id": "CTX-software-engineer-x"})
        ledger.append({
            "kind": "run_outcome", "transaction_id": "tx-run",
            "outcome": "correlated", "run_id": "run-1",
            "mention_comment_id": "c-0002"})
        result, _, _ = run_fallback(
            fake=FakeMultica(), ledger=ledger, source_tx="tx-run",
            authorization=None, tx="tx-fb")
        self.assertEqual(result["terminal_status"], "TRIGGERED")
        self.assertEqual(result["next_action"],
                         "continue_readonly_reconciliation")

    def test_assignment_uncertain_never_retries_or_switches(self):
        ledger = dispatch.TransactionLedger()
        ledger.append({"kind": "command", "transaction_id": "tx-a",
                       "command_class": "assignment_trigger",
                       "argv": ["multica", "issue", "assign", ISSUE_ID]})
        ledger.append({"kind": "command_result", "transaction_id": "tx-a",
                       "exit_code": 0})
        fake = FakeMultica()
        result, _, _ = run_fallback(fake=fake, ledger=ledger, source_tx="tx-a",
                                    authorization=None, tx="tx-fb")
        self.assertEqual(result["terminal_status"], "TRIGGER_AMBIGUOUS")
        self.assertEqual(result["suggested_status"]
                         if "suggested_status" in result
                         else result["reconciliation"]["suggested_status"],
                         "ASSIGNMENT_CONFIRMATION_REQUIRED")
        self.assertEqual(fake.assign_calls, [])
        self.assertEqual(fake.comment_add_calls, [])

    def test_untrusted_run_listing_blocks_with_zero_trigger(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.runs_malformed = True
        result, _, _ = run_fallback(fake=fake, ledger=ledger,
                                    source_tx="tx-src-stop",
                                    tx="tx-fb")
        self.assertEqual(result["terminal_status"], "EVIDENCE_UNTRUSTED")
        self.assertEqual(fake.assign_calls, [])


class FailClosedTests(unittest.TestCase):
    def test_assign_response_ambiguous_never_retried(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.assign_stdout = "<not json>"
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertEqual(result["terminal_status"],
                         "ASSIGNMENT_CONFIRMATION_REQUIRED")
        self.assertEqual(len(fake.assign_calls), 1)
        self.assertEqual(fake.comment_add_calls[0], ISSUE_ID)
        # a second attempt at the same tx is refused, never retried
        replayed = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-u08-0001",
            source_transaction_id="tx-src-stop", authorization={},
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(replayed["terminal_status"], "REPLAY_REFUSED")
        self.assertEqual(len(fake.assign_calls), 1)

    def test_assign_command_failure_is_confirmation_required(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.assign_exit = 2
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertEqual(result["terminal_status"],
                         "ASSIGNMENT_CONFIRMATION_REQUIRED")
        self.assertEqual(len(fake.assign_calls), 1)

    def test_duplicate_target_run_fails_correlation(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.duplicate_assign_run = True
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertEqual(result["terminal_status"],
                         "RUN_CORRELATION_FAILED")
        self.assertEqual(len(fake.assign_calls), 1)

    def test_unexpected_run_before_trigger_blocks(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        fake.runs.append({"id": "run-x", "issue_id": ISSUE_ID,
                          "agent_id": AGENT_SE, "status": "running"})
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertIn(result["terminal_status"],
                      ("UNEXPECTED_RUN", "EVIDENCE_UNTRUSTED",
                       "TRIGGER_AMBIGUOUS", "RUN_STATE_UNDETERMINED"))
        self.assertEqual(fake.assign_calls, [])

    def test_artifact_missing_blocks_before_publish(self):
        spec = fallback_spec(required_artifacts=[{
            "artifact_type": "implementation", "artifact_id": "ART-X",
            "version": "missing-version", "required": True}])
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        result = fb.run_assignment_fallback(
            spec, caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-fb",
            source_transaction_id="tx-src-stop", authorization=auth,
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "PACKAGE_STALE")
        self.assertEqual(fake.assign_calls, [])
        self.assertEqual(fake.comment_add_calls, [])

    def test_assignee_drift_between_reconcile_and_trigger_blocks(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        fake.issues[ISSUE_ID]["assignee_id"] = OTHER_AGENT
        result = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-fb",
            source_transaction_id="tx-src-stop", authorization=auth,
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "AUTHORIZATION_INVALID")

    def test_closure_conflict_for_second_fallback(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        first = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-fb-1",
            source_transaction_id="tx-src-stop", authorization=auth,
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(first["terminal_status"], "COMPLETED", first)
        second = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-fb-2",
            source_transaction_id="tx-src-stop", authorization=auth,
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(second["terminal_status"], "CLOSURE_CONFLICT")
        self.assertEqual(len(fake.assign_calls), 1)

    def test_authorization_missing_stops_before_fallback(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger,
                                    authorization=None)
        self.assertEqual(result["terminal_status"], "AUTHORIZATION_REQUIRED")
        self.assertEqual(fake.assign_calls, [])
        self.assertFalse(any(r.get("kind") == "cross_route_closure"
                             for r in ledger.records))

    def test_wrong_authorization_never_opens_fallback(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger,
                                    authorization={"kind": "other"})
        self.assertEqual(result["terminal_status"], "AUTHORIZATION_INVALID")
        self.assertEqual(fake.assign_calls, [])
        self.assertFalse(any(r.get("kind") == "cross_route_closure"
                             for r in ledger.records))

    def test_producer_to_05_fallback_is_rejected(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(
            fake=fake, ledger=ledger, target="delivery-reviewer",
            tx="tx-fb")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertEqual(fake.assign_calls, [])

    def test_qa_without_lead_gate_is_rejected(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger, target="qa",
                                    tx="tx-fb")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertEqual(fake.assign_calls, [])

    def test_context_engineer_ordinary_fallback_is_rejected(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger,
                                    target="context-engineer", tx="tx-fb")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertEqual(fake.assign_calls, [])

    def test_retired_and_live_old_role_never_resolve(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        for role in (RETIRED_FR, LIVE_FR_NAME):
            result, _, _ = run_fallback(
                fake=fake, ledger=ledger, target=role, tx=f"tx-{len(role)}")
            self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED",
                             role)
            self.assertEqual(fake.assign_calls, [])

    def test_stage_wake_lead_fallback_is_rejected(self):
        ledger, fake = seed_mention_stop(mode="pre_publish",
                                         target="engineering-lead")
        spec = fallback_spec(stage_wake_applies=True)
        result, _, _ = run_fallback(fake=fake, ledger=ledger, spec=spec,
                                    target="engineering-lead", tx="tx-fb")
        self.assertEqual(result["terminal_status"], "STAGE_WAKE_DUPLICATE")
        self.assertEqual(fake.assign_calls, [])

    def test_invalid_spec_is_zero_command(self):
        cases = [
            ({}, "missing issue_id"),
            (fallback_spec(issue_id=""), "blank issue_id"),
            (fallback_spec(issue_id="YZT 900"), "whitespace issue_id"),
            (fallback_spec(project_id=None), "missing project"),
            (fallback_spec(options=[]), "options not a dict"),
            (fallback_spec(required_artifacts="x"), "artifacts not a list"),
            (fallback_spec(issue_id=ISSUE_ID + " mention://agent/x"),
             "mention link smuggled"),
        ]
        for spec, label in cases:
            ledger = dispatch.TransactionLedger()
            fake = FakeMultica()
            result = fb.run_assignment_fallback(
                spec, caller_role="engineering-lead",
                target_role_spec="software-engineer", runner=fake,
                ledger=ledger, compose_fn=COMPOSE, transaction_id="tx-bad",
                source_transaction_id="tx-src", authorization={},
                clock=CLOCK, finding_store=fake_store())
            self.assertEqual(result["terminal_status"], "INVALID_INPUT", label)
            self.assertEqual(fake.assign_calls, [], label)
            self.assertEqual(fake.comment_add_calls, [], label)

    def test_policy_not_dict_or_compose_missing(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger, policy="x")
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        result2 = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=None, transaction_id="tx-nocompose",
            source_transaction_id="tx-src-stop", authorization={},
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result2["terminal_status"], "INVALID_INPUT")


class AuditTests(unittest.TestCase):
    def test_fallback_audit_detects_wrong_issue_records(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        audit = fb.audit_fallback_ledger(
            ledger.records, transaction_id="tx-u08-0001",
            source_transaction_id="tx-src-stop")
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["closures"], 1)
        self.assertEqual(audit["supersessions"], 1)
        self.assertTrue(audit["closure_before_fallback_actions"])

    def test_cross_route_audit_flags_closed_source_reactivation(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertTrue(fb.cross_route_audit_v2(ledger.records)["ok"])
        ledger.append({
            "kind": "mention_outcome", "transaction_id": "tx-src-stop",
            "outcome": "confirmed", "mention_comment_id": "c-9999"})
        audit = fb.cross_route_audit_v2(ledger.records)
        reasons = [c.get("reason") for c in audit["conflicts"]]
        self.assertIn("closed_source_route_reactivated", reasons)

    def test_cross_route_audit_flags_duplicate_closure(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        run_fallback(fake=fake, ledger=ledger)
        ledger.append({"kind": "cross_route_closure",
                       "transaction_id": "tx-u08-0001",
                       "closed_transaction_id": "tx-src-stop",
                       "closure": "NO_TRIGGER_TERMINAL"})
        audit = fb.cross_route_audit_v2(ledger.records)
        reasons = [c.get("reason") for c in audit["conflicts"]]
        self.assertIn("more_than_one_cross_route_closure", reasons)

    def test_cross_route_audit_flags_fallback_mention_evidence(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        run_fallback(fake=fake, ledger=ledger)
        ledger.append({"kind": "mention_ready",
                       "transaction_id": "tx-u08-0001",
                       "authorized_mentions": 1})
        audit = fb.cross_route_audit_v2(ledger.records)
        reasons = [c.get("reason") for c in audit["conflicts"]]
        self.assertIn("fallback_transaction_carries_mention_evidence",
                      reasons)

    def test_acceptance_evidence_reports_done_criteria(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        evidence = fb.acceptance_evidence(result, ledger)
        self.assertEqual(evidence["cross_route"]["source_trigger_count"], 0)
        self.assertEqual(evidence["cross_route"]["source_run_count"], 0)
        self.assertTrue(evidence["cross_route"][
            "source_transaction_closed_before_fallback"])
        self.assertTrue(evidence["cross_route"][
            "fallback_transaction_is_new_and_linked"])
        self.assertFalse(evidence["cross_route"][
            "route_mutation_or_package_reuse"])
        self.assertEqual(evidence["assignment_fallback"][
            "intended_assignment_count"], 1)
        self.assertEqual(evidence["assignment_fallback"]["intended_run_count"],
                         1)
        self.assertTrue(evidence["assignment_fallback"][
            "self_check_ready_before_work"])
        self.assertEqual(evidence["safety"]["live_mutations_or_triggers"], 0)


class RecoveryTests(unittest.TestCase):
    def test_resumable_source_never_falls_back(self):
        ledger, fake = seed_mention_crash()
        result, _, _ = run_fallback(
            fake=fake, ledger=ledger, source_tx="tx-src-crash",
            authorization=None, tx="tx-fb")
        self.assertEqual(result["terminal_status"], "ROUTE_RESUMABLE")
        self.assertEqual(result["next_action"], "resume_mention_route")
        self.assertEqual(fake.assign_calls, [])
        self.assertEqual(fake.comment_add_calls, [])
        self.assertFalse(any(r.get("kind") == "cross_route_closure"
                             for r in ledger.records))

    def test_resume_source_route_continues_the_mention_route(self):
        ledger, fake = seed_mention_crash(crash_at="RUN_PRECHECK_PREPARE")
        resumed = fb.resume_source_route(
            base_source_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, source_transaction_id="tx-src-crash",
            clock=CLOCK, finding_store=fake_store(), stage="ready")
        self.assertIn("recovery", resumed)
        self.assertEqual(resumed.get("terminal_status"), "MENTION_READY")
        self.assertEqual(fake.assign_calls, [])

    def test_fallback_crash_pre_publish_recovers_uniquely(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        crashed, _, _ = run_fallback(fake=fake, ledger=ledger,
                                     tx="tx-fb-crash",
                                     crash_at="HANDOFF_PREPARED")
        self.assertEqual(crashed["terminal_status"], "CRASH_SIMULATED")
        self.assertEqual(fake.comment_add_calls, [])
        self.assertEqual(fake.assign_calls, [])
        recovered = fb.recover_assignment_fallback(
            ledger, "tx-fb-crash", spec=_u06_spec(),
            caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake,
            compose_fn=COMPOSE, source_transaction_id="tx-src-stop",
            authorization=None, clock=CLOCK, finding_store=fake_store())
        self.assertEqual(recovered["terminal_status"], "COMPLETED", recovered)
        self.assertEqual(len(fake.comment_add_calls), 1)
        self.assertEqual(len(fake.assign_calls), 1)

    def test_fallback_crash_post_publish_reuses_note(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        crashed, _, _ = run_fallback(fake=fake, ledger=ledger,
                                     tx="tx-fb-crash2",
                                     crash_at="RUN_PRECHECK_TRIGGER")
        self.assertEqual(crashed["terminal_status"], "CRASH_SIMULATED")
        self.assertEqual(len(fake.comment_add_calls), 1)
        self.assertEqual(fake.assign_calls, [])
        recovered = fb.recover_assignment_fallback(
            ledger, "tx-fb-crash2", spec=_u06_spec(),
            caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake,
            compose_fn=COMPOSE, source_transaction_id="tx-src-stop",
            authorization=None, clock=CLOCK, finding_store=fake_store())
        self.assertEqual(recovered["terminal_status"], "COMPLETED", recovered)
        self.assertEqual(len(fake.comment_add_calls), 1)
        self.assertEqual(len(fake.assign_calls), 1)


    def test_fallback_crash_post_trigger_never_retries(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        crashed, _, _ = run_fallback(fake=fake, ledger=ledger,
                                     tx="tx-fb-crash3",
                                     crash_at="ASSIGNMENT_TRIGGERED")
        self.assertEqual(crashed["terminal_status"], "CRASH_SIMULATED")
        self.assertEqual(len(fake.assign_calls), 1)
        recovered = fb.recover_assignment_fallback(
            ledger, "tx-fb-crash3", spec=_u06_spec(),
            caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake,
            compose_fn=COMPOSE, source_transaction_id="tx-src-stop",
            authorization=None, clock=CLOCK, finding_store=fake_store())
        self.assertEqual(recovered["terminal_status"],
                         "ASSIGNMENT_CONFIRMATION_REQUIRED")
        self.assertEqual(len(fake.assign_calls), 1)
        self.assertEqual(len(fake.comment_add_calls), 1)

    def test_recorded_incomplete_fallback_is_refused(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(fake=fake, ledger=ledger)
        self.assertEqual(result["terminal_status"], "COMPLETED")
        self.assertEqual(len(fake.assign_calls), 1)
        second = fb.run_assignment_fallback(
            fallback_spec(), caller_role="engineering-lead",
            target_role_spec="software-engineer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-u08-0001",
            source_transaction_id="tx-src-stop", authorization={},
            clock=CLOCK, finding_store=fake_store())
        self.assertTrue(second["replayed"])

    def test_source_records_are_never_mutated_by_fallback(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        before = len(assignment_records(ledger, "tx-src-stop"))
        run_fallback(fake=fake, ledger=ledger)
        after = assignment_records(ledger, "tx-src-stop")
        self.assertEqual(len(after), before)
        self.assertFalse(any(r.get("kind", "").startswith("cross_route")
                             for r in after))


class ArtifactPathTests(unittest.TestCase):
    def test_lead_owned_review_fallback_binds_exact_artifacts(self):
        art = TOOLS / "fixtures" / "artifact-contract"
        store_file = str(art / "store-chain.json")
        requirements = json.loads(
            (art / "ready-r1.json").read_text(encoding="utf-8")
        )["requirements"]
        ledger = dispatch.TransactionLedger()
        fake = FakeMultica()
        calls: dict = {}
        source = men.run_mention_handoff(
            base_source_spec(required_artifacts=list(requirements),
                             review_level="R1",
                             artifact_store_file=store_file),
            caller_role="engineering-lead",
            target_role_spec="delivery-reviewer", runner=fake, ledger=ledger,
            compose_fn=flaky_compose(calls), transaction_id="tx-src-05",
            policy={"lead_owned_review_routing": True}, clock=CLOCK,
            finding_store=fake_store(), stage="ready")
        self.assertEqual(source["terminal_status"], "PREPARE_FAILED")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-05",
                              target="delivery-reviewer")
        auth = make_authorization(recon)
        result = fb.run_assignment_fallback(
            fallback_spec(required_artifacts=list(requirements),
                          review_level="R1", artifact_store_file=store_file),
            caller_role="engineering-lead",
            target_role_spec="delivery-reviewer", runner=fake, ledger=ledger,
            compose_fn=COMPOSE, transaction_id="tx-fb-05",
            source_transaction_id="tx-src-05", authorization=auth,
            policy={"lead_owned_review_routing": True}, clock=CLOCK,
            finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "COMPLETED", result)
        self.assertEqual(result["target"]["role"], "delivery-reviewer")
        self.assertEqual(result["fallback_result"]["artifacts"]["status"],
                         "ARTIFACT_READY")
        self.assertEqual(result["self_check"]["status"], "READY")
        self.assertEqual(len(fake.assign_calls), 1)

    def test_qa_without_gate_artifacts_is_rejected_even_when_authorized(self):
        ledger, fake = seed_mention_stop(mode="pre_publish")
        result, _, _ = run_fallback(
            fake=fake, ledger=ledger, target="qa", tx="tx-fb",
            policy={"lead_owned_qa_routing": True})
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertEqual(fake.assign_calls, [])


class PinTests(unittest.TestCase):
    def test_accepted_runtime_pins(self):
        self.assertEqual(asm.ORCHESTRATOR_VERSION, "U06/2.2")
        self.assertEqual(men.ORCHESTRATOR_VERSION, "U07/2.2")
        self.assertEqual(men.PINNED_U06_COMMIT,
                         "f35afdf91133c3ea21432684fa62f34f0eae12c1")
        self.assertEqual(men.PINNED_U05_COMMIT,
                         "b43b68b5deae6d8636f02293f2abb52ecb5f65e3")
        self.assertEqual(
            asm.PINNED_INSTRUCTION_BUNDLE,
            "sha256:a93e146d6586cf6f474148aeed092032e3a9c871ad3138ee1152d28108b5b98f")
        self.assertEqual(
            asm.PINNED_BINDING_PLAN,
            "sha256:7f83bfd523e2c0da3cd0dac4568869f3c9937e2d3ae6c0dc66b9853094a27c22")
        self.assertEqual(
            asm.PINNED_SKILL_BUNDLE,
            "sha256:7f861c320c115b328fb45db7356573ae449a5e443ac08b3572a764c79934fce9")

    def test_u04_u05_pins_reproduce(self):
        mapping = asm.u05_mapping()
        self.assertEqual(
            mapping["instruction_bundle_revision"],
            asm.PINNED_INSTRUCTION_BUNDLE)
        self.assertEqual(mapping["binding_plan_revision"],
                         asm.PINNED_BINDING_PLAN)
        self.assertIsNone(mapping["feature_reviewer_resolves_to"])
        self.assertFalse(mapping["old_05_package_accepted"])

    def test_pin_drift_fails_closed_with_zero_trigger(self):
        from unittest import mock
        ledger, fake = seed_mention_stop(mode="pre_publish")
        recon = reconcile_for(fake, ledger, source_tx="tx-src-stop")
        auth = make_authorization(recon)
        with mock.patch.object(asm, "PINNED_INSTRUCTION_BUNDLE",
                               "sha256:tampered"):
            result = fb.run_assignment_fallback(
                fallback_spec(), caller_role="engineering-lead",
                target_role_spec="software-engineer", runner=fake,
                ledger=ledger, compose_fn=COMPOSE, transaction_id="tx-drift",
                source_transaction_id="tx-src-stop", authorization=auth,
                clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "U05_DIGEST_DRIFT")
        self.assertEqual(fake.assign_calls, [])
        self.assertEqual(fake.comment_add_calls, [])


class CliTests(unittest.TestCase):
    def test_cli_audit_and_cross_route_audit(self):
        import subprocess
        import tempfile
        ledger, fake = seed_mention_stop(mode="pre_publish")
        run_fallback(fake=fake, ledger=ledger)
        with tempfile.TemporaryDirectory() as tmp:
            ledger_file = str(Path(tmp) / "ledger.jsonl")
            ledger.save(ledger_file)
            tool = str(TOOLS / "chandoff_fallback.py")
            audit = subprocess.run(
                [sys.executable, tool, "audit", "--ledger-file", ledger_file,
                 "--transaction-id", "tx-u08-0001",
                 "--source-transaction-id", "tx-src-stop"],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertTrue(json.loads(audit.stdout)["ok"])
            cross = subprocess.run(
                [sys.executable, tool, "cross-route-audit",
                 "--ledger-file", ledger_file],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(cross.returncode, 0, cross.stderr)
            self.assertTrue(json.loads(cross.stdout)["ok"])


def _u06_spec() -> dict:
    return {"title": TITLE, "description": DESCRIPTION,
            "project_id": "web-imagegen",
            "existing_issue_id": ISSUE_ID,
            "purpose": "implementation", "options": {"limit": 8}}


if __name__ == "__main__":
    unittest.main()
