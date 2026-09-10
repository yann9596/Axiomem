#!/usr/bin/env python3
"""T10 focused tests — mention handoff path (YZT-65).

Every path runs against fixture/injected runners only: no live issue
create/comment/assign/status write, no native mention, no run trigger, no
Canonical write, no model call. The fake runner keeps an in-memory platform
state so publish -> discovery -> evidence round-trips deterministically.
"""
from __future__ import annotations

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
import chandoff_mention as men  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402

CLOCK = lambda: "2026-09-10T09:00:00Z"  # noqa: E731

AGENT_LEAD = "24f04aba-7da9-4371-bf89-685d7505a411"
AGENT_SA = "1303827b-73d1-4d71-a461-00b93e4b4418"
AGENT_SE = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
AGENT_FR = "b6335f8e-8147-45f7-aac0-8079d85423b5"
AGENT_QA = "30ce43d4-97a7-42a8-ab3e-78df0d894702"
OTHER_AGENT = "77777777-7777-7777-7777-777777777777"

ISSUE_ID = "22222222-0000-0000-0000-000000000065"
IDENTIFIER = "YZT-900"
TASK_REF = "multica://issue/" + IDENTIFIER

FULL_TRANSITIONS = ["INIT", "ISSUE_BOUND", "TARGET_RESOLVED", "ROUTE_FROZEN",
                    "HANDOFF_PREPARED", "HANDOFF_PUBLISHED",
                    "HANDOFF_READY_CONFIRMED", "MENTION_READY",
                    "MENTION_EVIDENCE_ACCEPTED", "TARGET_RUN_CORRELATED",
                    "TARGET_SELF_CHECKED", "COMPLETED"]

COMPOSE = lambda plan_obj, request, errors=None: compose.subset_result(plan_obj)  # noqa: E731

CALLER_AGENTS = {
    "engineering-lead": AGENT_LEAD,
    "solution-architect": AGENT_SA,
    "software-engineer": AGENT_SE,
    "feature-reviewer": AGENT_FR,
    "qa": AGENT_QA,
}

TARGET_AGENTS = {
    "engineering-lead": AGENT_LEAD,
    "software-engineer": AGENT_SE,
    "feature-reviewer": AGENT_FR,
    "qa": AGENT_QA,
}


def base_spec(**overrides) -> dict:
    spec = {
        "issue_id": ISSUE_ID,
        "project_id": "web-imagegen",
        "purpose": "implementation",
        "options": {"limit": 8},
    }
    spec.update(overrides)
    return {k: v for k, v in spec.items() if v is not None}


def fake_store():
    from chandoff_plan import MemoryFindingStore
    return MemoryFindingStore([])


def mention_evidence(tx, *, author=AGENT_SA, agent_id=AGENT_SE,
                     role="software-engineer", comment_id="c-0002",
                     created_at="2026-09-10T10:00:00Z", link=None,
                     mentions=None, handles=None, mutation=None,
                     surface="native_agent_reply", issue_id=ISSUE_ID,
                     kind="native_mention_evidence",
                     schema="T10-mention-evidence/1.0") -> dict:
    if mentions is None:
        mentions = [{"agent_id": agent_id, "role": role,
                     "link": link if link is not None
                     else "mention://agent/" + agent_id}]
    doc = {
        "kind": kind,
        "schema_version": schema,
        "transaction_id": tx,
        "issue_id": issue_id,
        "comment_id": comment_id,
        "created_at": created_at,
        "author_agent_id": author,
        "author_surface": surface,
        "mentions": mentions,
        "plain_text_handles": handles or [],
        "assignment_mutation": mutation,
    }
    return doc


def run_evidence(tx, *, agent_id=AGENT_SE, run_id="r-1", source="mention",
                 mention_comment_id="c-0002", runs=None, issue_id=ISSUE_ID,
                 kind="target_run_evidence",
                 schema="T10-run-evidence/1.0") -> dict:
    if runs is None:
        runs = [{"agent_id": agent_id, "run_id": run_id, "source": source}]
    return {
        "kind": kind,
        "schema_version": schema,
        "transaction_id": tx,
        "issue_id": issue_id,
        "mention_comment_id": mention_comment_id,
        "runs": runs,
    }


class FakeMultica:
    """Stateful in-memory stand-in for the deployed multica CLI."""

    def __init__(self, *, version="v0.4.42", issue_id=ISSUE_ID,
                 identifier=IDENTIFIER, assignee=AGENT_SA, project=None,
                 get_fails=False, get_malformed=False, drop_note=False,
                 note_corruptor=None, extra_record_body=None,
                 flip_assignee_on_write=None, flip_to=OTHER_AGENT):
        self.version = version
        self.get_fails = get_fails
        self.get_malformed = get_malformed
        self.drop_note = drop_note
        self.note_corruptor = note_corruptor
        self.extra_record_body = extra_record_body
        self.flip_assignee_on_write = flip_assignee_on_write
        self.flip_to = flip_to
        self.issues: dict = {}
        self.by_identifier: dict = {}
        self.comments: dict = {}
        self.comment_add_calls: list = []
        self.get_calls: list = []
        self.seq = 0
        doc = {
            "id": issue_id, "identifier": identifier,
            "title": "Existing drill issue",
            "description": "Drill: - Requirement: build X\n- Acceptance: works",
            "parent_issue_id": None, "project_id": project,
            "assignee_id": assignee, "assignee": None,
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
            return 0, json.dumps({"id": f"c-{self.seq:04d}"
                                  if not self.drop_note else "c-lost",
                                  "created_at": comment["created_at"],
                                  "parent_id": None}), ""
        return 2, "", "unexpected command"


UNSET = object()


def run_tx(fake=None, *, spec=None, caller="solution-architect",
           target="software-engineer", tx="tx-t10-0001", ledger=None,
           policy=None, world=None, finding_store=None, bundle_dir=None,
           stage="full", mention=UNSET, run_ev=UNSET):
    spec = dict(spec if spec is not None else base_spec())
    ledger = ledger if ledger is not None else dispatch.TransactionLedger()
    fake = fake if fake is not None else FakeMultica()
    if mention is UNSET:
        mention = mention_evidence(
            tx, author=CALLER_AGENTS.get(caller, AGENT_SA),
            agent_id=TARGET_AGENTS.get(target, AGENT_SE), role=target)
    if run_ev is UNSET:
        run_ev = run_evidence(tx, agent_id=TARGET_AGENTS.get(target, AGENT_SE))
    result = men.run_mention_handoff(
        spec, caller_role=caller, target_role_spec=target, runner=fake,
        ledger=ledger, compose_fn=COMPOSE, transaction_id=tx,
        policy=policy, clock=CLOCK, finding_store=finding_store or fake_store(),
        world=world, bundle_dir=bundle_dir, stage=stage,
        mention_evidence=mention, run_evidence=run_ev)
    return result, fake, ledger


def world_rule(**overrides) -> dict:
    doc = {
        "_kind": "rule",
        "id": "RULE-T10000001",
        "kind": "rule",
        "schema_version": "1.1",
        "title": "mention handoff fixture rule",
        "statement": "mention orchestrator provider boundary rule",
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
        "finding_id": "FIND-WIMG-T10-000001",
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


def assignment_result_record(tx="tx-assign-1", issue_id=ISSUE_ID,
                             identifier=IDENTIFIER, role="software-engineer",
                             package_id="pkg-assign-1") -> dict:
    return {
        "kind": "transaction_result",
        "transaction_id": tx,
        "terminal_status": "COMPLETED",
        "result": {
            "ok": True,
            "orchestrator": asm.ORCHESTRATOR_VERSION,
            "transaction_id": tx,
            "terminal_status": "COMPLETED",
            "issue": {"id": issue_id, "identifier": identifier},
            "task_ref": "multica://issue/" + identifier,
            "target": {"role": role, "agent_id": AGENT_SE},
            "package": {"package_id": package_id, "status": "READY"},
            "trigger": {"type": "issue_assign", "count": 1,
                        "confirmed": True, "argv": ["multica", "issue",
                                                    "assign", issue_id,
                                                    "--to-id", AGENT_SE]},
        },
    }


class SpecValidationTests(unittest.TestCase):
    def test_invalid_specs_are_zero_command(self):
        cases = [
            ({}, "missing issue_id"),
            (base_spec(issue_id=""), "blank issue_id"),
            (base_spec(issue_id="YZT 900"), "whitespace issue_id"),
            (base_spec(issue_id="--output"), "flag-looking issue_id"),
            (base_spec(project_id=None), "missing project"),
            (base_spec(purpose=""), "blank purpose"),
            (base_spec(options=[]), "options not a dict"),
            (base_spec(decision_markers="x"), "markers not a list"),
            (base_spec(issue_id=ISSUE_ID + " mention://agent/x"),
             "mention link smuggled in spec"),
        ]
        for spec, label in cases:
            ledger = dispatch.TransactionLedger()
            result = men.run_mention_handoff(
                spec, caller_role="solution-architect",
                target_role_spec="software-engineer", runner=FakeMultica(),
                ledger=ledger, compose_fn=COMPOSE, transaction_id="tx-invalid",
                clock=CLOCK, finding_store=fake_store())
            self.assertEqual(result["terminal_status"], "INVALID_INPUT", label)
            self.assertEqual(ledger.commands(), [], label)
            self.assertFalse(result["ok"])

    def test_caller_and_transaction_validation(self):
        ledger = dispatch.TransactionLedger()
        result = men.run_mention_handoff(
            base_spec(), caller_role="astronaut",
            target_role_spec="software-engineer", runner=FakeMultica(),
            ledger=ledger, compose_fn=COMPOSE, transaction_id="tx-bad-caller",
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        self.assertEqual(ledger.commands(), [])
        result = men.run_mention_handoff(
            base_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=FakeMultica(),
            ledger=ledger, compose_fn=None, transaction_id="tx-no-compose",
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        result = men.run_mention_handoff(
            base_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=FakeMultica(),
            ledger=ledger, compose_fn=COMPOSE, transaction_id="tx has space",
            clock=CLOCK, finding_store=fake_store())
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")

    def test_unknown_stage_is_invalid_input(self):
        ledger = dispatch.TransactionLedger()
        result = men.run_mention_handoff(
            base_spec(), caller_role="solution-architect",
            target_role_spec="software-engineer", runner=FakeMultica(),
            ledger=ledger, compose_fn=COMPOSE, transaction_id="tx-stage",
            clock=CLOCK, finding_store=fake_store(), stage="yolo")
        self.assertEqual(result["terminal_status"], "INVALID_INPUT")
        self.assertEqual(ledger.commands(), [])


class EnvelopeAndEvidenceUnitTests(unittest.TestCase):
    def ready(self, tx="tx-env-1"):
        return men.mention_ready_envelope(
            transaction_id=tx,
            issue={"id": ISSUE_ID, "identifier": IDENTIFIER},
            caller={"role": "solution-architect", "agent_id": AGENT_SA},
            target={"role": "software-engineer", "role_name":
                    "04 Software Engineer", "agent_id": AGENT_SE,
                    "agent_name": "04 Software Engineer"},
            package={"package_id": "pkg-1", "status": "READY"},
            published_comment_id="c-0001",
            published_comment_created_at="2026-09-10T08:00:01Z")

    def test_mention_ready_envelope_shape(self):
        ready = self.ready()
        self.assertEqual(ready["kind"], "mention_ready_envelope")
        self.assertTrue(ready["schema_version"].startswith("T10-mention-ready/"))
        self.assertEqual(ready["route"], "mention")
        self.assertTrue(ready["handoff_ready"])
        self.assertEqual(ready["trigger"],
                         {"type": "mention", "authorized_mentions": 1})
        constraints = ready["constraints"]
        self.assertTrue(constraints["emit_exactly_one_native_mention"])
        self.assertFalse(constraints["adapter_constructs_mention_markdown"])
        self.assertEqual(constraints["mention_target_agent_id"], AGENT_SE)
        self.assertTrue(constraints["assignment_forbidden"])
        self.assertTrue(constraints["second_mention_never_authorized"])

    def test_valid_mention_evidence_accepted(self):
        accepted = men.validate_mention_evidence(
            mention_evidence("tx-env-1"), ready=self.ready())
        self.assertTrue(accepted["accepted"])
        self.assertEqual(accepted["comment_id"], "c-0002")
        self.assertEqual(accepted["target_agent_id"], AGENT_SE)

    def test_plain_text_handle_is_rejected(self):
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", mentions=[]),
                ready=self.ready())
        with self.assertRaises(men.AmbiguousMentionError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", mentions=[{
                    "agent_id": AGENT_SE, "role": "software-engineer",
                    "link": "mention://agent/" + AGENT_SE}],
                    handles=["@SoftwareEngineer"]),
                ready=self.ready())

    def test_fabricated_link_text_is_rejected(self):
        for link in ("mention://agent/someone-else",
                     "[04 Software Engineer](not-a-mention)",
                     "mention://issue/" + AGENT_SE):
            with self.assertRaises(men.EvidenceError):
                men.validate_mention_evidence(
                    mention_evidence("tx-env-1", link=link),
                    ready=self.ready())

    def test_wrong_target_or_author_or_issue_or_tx_is_rejected(self):
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", agent_id=OTHER_AGENT),
                ready=self.ready())
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", role="qa"),
                ready=self.ready())
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", author=AGENT_LEAD),
                ready=self.ready())
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", issue_id="33333333-0000-0000-"
                                 "0000-000000000099"),
                ready=self.ready())
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-other"), ready=self.ready())

    def test_multiple_mentions_are_rejected(self):
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", mentions=[
                    {"agent_id": AGENT_SE, "role": "software-engineer",
                     "link": "mention://agent/" + AGENT_SE},
                    {"agent_id": AGENT_QA, "role": "qa",
                     "link": "mention://agent/" + AGENT_QA}]),
                ready=self.ready())

    def test_adapter_surface_or_note_comment_is_rejected(self):
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", surface="adapter_command"),
                ready=self.ready())
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", comment_id="c-0001"),
                ready=self.ready())

    def test_mention_preceding_publication_is_rejected(self):
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", created_at="2026-09-10T08:00:01Z"),
                ready=self.ready())

    def test_missing_created_at_is_ambiguous(self):
        evidence = mention_evidence("tx-env-1")
        evidence.pop("created_at")
        with self.assertRaises(men.AmbiguousMentionError):
            men.validate_mention_evidence(evidence, ready=self.ready())

    def test_plain_text_alongside_link_is_ambiguous(self):
        with self.assertRaises(men.AmbiguousMentionError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", handles=["@06 QA"]),
                ready=self.ready())

    def test_assignment_mutation_is_rejected(self):
        with self.assertRaises(men.EvidenceError):
            men.validate_mention_evidence(
                mention_evidence("tx-env-1", mutation={
                    "command": "issue assign"}),
                ready=self.ready())

    def test_non_dict_or_wrong_kind_is_rejected(self):
        for evidence in (None, 7, [], {"kind": "other"}):
            with self.assertRaises(men.EvidenceError):
                men.validate_mention_evidence(evidence, ready=self.ready())

    def test_run_evidence_valid(self):
        correlated = men.validate_run_evidence(
            run_evidence("tx-env-1"), ready=self.ready(),
            mention={"comment_id": "c-0002"})
        self.assertTrue(correlated["correlated"])
        self.assertEqual(correlated["run_count"], 1)

    def test_run_evidence_failures(self):
        ready = self.ready()
        with self.assertRaises(men.RunCorrelationError):
            men.validate_run_evidence(run_evidence("tx-env-1", runs=[]),
                                      ready=ready,
                                      mention={"comment_id": "c-0002"})
        with self.assertRaises(men.RunCorrelationError):
            men.validate_run_evidence(run_evidence("tx-env-1", runs=[
                {"agent_id": AGENT_SE, "run_id": "r-1", "source": "mention"},
                {"agent_id": AGENT_SE, "run_id": "r-2", "source": "mention"}]),
                ready=ready, mention={"comment_id": "c-0002"})
        with self.assertRaises(men.RunCorrelationError):
            men.validate_run_evidence(run_evidence("tx-env-1",
                                                   agent_id=OTHER_AGENT),
                                      ready=ready,
                                      mention={"comment_id": "c-0002"})
        with self.assertRaises(men.RunCorrelationError):
            men.validate_run_evidence(run_evidence("tx-env-1", source="assign"),
                                      ready=ready,
                                      mention={"comment_id": "c-0002"})
        with self.assertRaises(men.RunCorrelationError):
            men.validate_run_evidence(run_evidence("tx-env-1",
                                                   mention_comment_id="c-9999"),
                                      ready=ready,
                                      mention={"comment_id": "c-0002"})
        for bad in (None, 3, {"kind": "other"}):
            with self.assertRaises(men.RunCorrelationError):
                men.validate_run_evidence(bad, ready=ready,
                                          mention={"comment_id": "c-0002"})


class MentionLedgerAuditTests(unittest.TestCase):
    def tx_records(self, result, ledger):
        return [r for r in ledger.records
                if r.get("transaction_id") == result.get("transaction_id")]

    def test_happy_path_audit_is_clean(self):
        result, _, ledger = run_tx()
        audit = men.audit_mention_ledger(self.tx_records(result, ledger))
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["issue_create"]["count"], 0)
        self.assertEqual(audit["assignment_trigger"]["count"], 0)
        self.assertEqual(audit["comment_publish"]["count"], 1)
        self.assertEqual(audit["mention_authorizations"], 1)
        self.assertEqual(audit["run_correlations"], 1)
        self.assertEqual(audit["mention_hits"], [])

    def test_audit_rejects_assignment_and_create_and_mention_argv(self):
        base = [{"kind": "command", "seq": 1, "command_class": "read",
                 "argv": ["multica", "issue", "get", "I"],
                 "transaction_id": "tx-a"},
                {"kind": "command", "seq": 2, "command_class": "comment_publish",
                 "argv": ["multica", "issue", "comment", "add", "I",
                          "--content-file", "p", "--output", "json"],
                 "transaction_id": "tx-a"},
                {"kind": "mention_outcome", "seq": 3, "transaction_id": "tx-a",
                 "package_id": "pkg-1", "count": 1}]
        for extra in (
                {"kind": "command", "seq": 4, "command_class":
                 "assignment_trigger",
                 "argv": ["multica", "issue", "assign", "I", "--to-id",
                          AGENT_SE], "transaction_id": "tx-a"},
                {"kind": "command", "seq": 5, "command_class": "issue_create",
                 "argv": ["multica", "issue", "create", "--title", "t"],
                 "transaction_id": "tx-a"},
                {"kind": "command", "seq": 6, "command_class": "read",
                 "argv": ["multica", "issue", "get",
                          "mention://agent/" + AGENT_SE],
                 "transaction_id": "tx-a"},
                {"kind": "mention_outcome", "seq": 8, "transaction_id": "tx-a",
                 "package_id": "pkg-1", "count": 1}):
            audit = men.audit_mention_ledger(base + [extra])
            self.assertFalse(audit["ok"], extra)
        audit = men.audit_mention_ledger(base + [
            {"kind": "run_outcome", "seq": 7, "transaction_id": "tx-a",
             "mention_comment_id": "c-1", "run_count": 1}])
        self.assertTrue(audit["ok"], audit)

    def test_cross_route_audit_flags_overlaps(self):
        conflict = [
            {"kind": "command", "seq": 1, "command_class": "assignment_trigger",
             "argv": ["multica", "issue", "assign", "I", "--to-id", AGENT_SE],
             "transaction_id": "tx-assign"},
            {"kind": "command", "seq": 2, "command_class": "comment_publish",
             "argv": ["multica", "issue", "comment", "add", "I",
                      "--content-file", "p", "--output", "json"],
             "transaction_id": "tx-mention"},
            {"kind": "publish_outcome", "seq": 3, "transaction_id":
             "tx-assign", "package_id": "pkg-shared", "published": True},
            {"kind": "publish_outcome", "seq": 4, "transaction_id":
             "tx-mention", "package_id": "pkg-shared", "published": True},
            {"kind": "mention_outcome", "seq": 5, "transaction_id":
             "tx-mention", "package_id": "pkg-shared", "count": 1},
        ]
        audit = men.cross_route_audit(conflict)
        self.assertFalse(audit["ok"])
        reasons = {c["reason"] for c in audit["conflicts"]}
        self.assertIn("package_bound_to_both_routes", reasons)

    def test_cross_route_audit_flags_handoff_double_trigger(self):
        records = [
            assignment_result_record(tx="tx-assign-1", role="qa"),
            {"kind": "transaction_result", "transaction_id": "tx-mention",
             "terminal_status": "COMPLETED",
             "result": {"ok": True, "issue": {"id": ISSUE_ID},
                        "task_ref": TASK_REF,
                        "target": {"role": "qa", "agent_id": AGENT_QA},
                        "trigger": {"type": "mention", "count": 1}}},
        ]
        audit = men.cross_route_audit(records)
        self.assertFalse(audit["ok"])
        reasons = {c["reason"] for c in audit["conflicts"]}
        self.assertIn("handoff_triggered_by_both_routes", reasons)

    def test_cross_route_audit_flags_multiple_mentions_per_package(self):
        records = [
            {"kind": "mention_outcome", "transaction_id": "tx-1",
             "package_id": "pkg-1", "count": 1},
            {"kind": "mention_outcome", "transaction_id": "tx-2",
             "package_id": "pkg-1", "count": 1},
        ]
        audit = men.cross_route_audit(records)
        self.assertFalse(audit["ok"])
        reasons = {c["reason"] for c in audit["conflicts"]}
        self.assertIn("more_than_one_mention", reasons)

    def test_cross_route_audit_flags_multiple_runs_per_mention(self):
        records = [
            {"kind": "mention_outcome", "transaction_id": "tx-1",
             "package_id": "pkg-1", "count": 1},
            {"kind": "run_outcome", "transaction_id": "tx-1",
             "mention_comment_id": "c-1", "run_count": 1},
            {"kind": "run_outcome", "transaction_id": "tx-1",
             "mention_comment_id": "c-1", "run_count": 1},
        ]
        audit = men.cross_route_audit(records)
        self.assertFalse(audit["ok"])
        reasons = {c["reason"] for c in audit["conflicts"]}
        self.assertIn("more_than_one_target_run", reasons)

    def test_cross_route_audit_clean_ledger_is_ok(self):
        records = [
            assignment_result_record(tx="tx-assign-1"),
            {"kind": "command", "seq": 1, "command_class": "assignment_trigger",
             "argv": ["multica", "issue", "assign", ISSUE_ID, "--to-id",
                      AGENT_SE], "transaction_id": "tx-assign-1"},
        ]
        audit = men.cross_route_audit(records)
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["route_transactions"]["assignment"],
                         ["tx-assign-1"])


class HappyPathTests(unittest.TestCase):
    def test_architect_invokes_engineer_assignee_stays(self):
        result, fake, ledger = run_tx(tx="tx-happy-sa")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["terminal_status"], "COMPLETED")
        self.assertEqual(result["transitions"], FULL_TRANSITIONS)
        self.assertEqual(result["route"], "mention")
        self.assertEqual(result["issue"],
                         {"id": ISSUE_ID, "identifier": IDENTIFIER})
        self.assertEqual(result["assignee"]["unchanged"], True)
        self.assertEqual(result["assignee"]["before"]["assignee_id"], AGENT_SA)
        self.assertEqual(result["trigger"],
                         {"type": "mention", "count": 1, "confirmed": True,
                          "runs": 1})
        self.assertEqual(fake.comment_add_calls, [ISSUE_ID])
        self.assertEqual(len(fake.comments[ISSUE_ID]), 1)
        audit = result["audit"]
        self.assertEqual(audit["issue_create"]["count"], 0)
        self.assertEqual(audit["assignment_trigger"]["count"], 0)
        self.assertEqual(result["self_check"]["status"], "READY")
        evidence = men.acceptance_evidence(result, ledger)
        for key in ("existing_issue_reused", "assignee_unchanged",
                    "handoff_confirmed_before_mention",
                    "mention_is_only_trigger", "assignment_not_used",
                    "assignment_plus_mention_rejected",
                    "target_self_check_ready_before_work",
                    "retry_does_not_duplicate_trigger",
                    "ambiguous_trigger_response_fails_closed"):
            self.assertTrue(evidence[key], key)
        self.assertIs(evidence["adapter_constructs_mention_markdown"], False)
        self.assertEqual(evidence["intended_mention_count_per_handoff"], 1)
        self.assertEqual(evidence["intended_run_count_per_handoff"], 1)
        self.assertEqual(evidence["live_mutations"], 0)

    def test_retired_feature_reviewer_fails_closed_no_transfer(self):
        # U02/YZT-69 + U05/YZT-73: feature-reviewer is retired without an
        # alias. Routing a mention to it fails closed at role-table resolve
        # (no alias to delivery-reviewer), with zero triggers.
        result, fake, ledger = run_tx(caller="engineering-lead",
                                      target="feature-reviewer",
                                      tx="tx-retired-fr")
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertIsNone(result.get("target"))
        self.assertEqual(result["trigger"]["count"], 0)
        self.assertEqual(result["guarantees"]["live_mutations"], 0)
        self.assertIn("feature-reviewer", result["stop_reason"] or "")

    def test_lead_invokes_qa_no_transfer(self):
        result, fake, ledger = run_tx(caller="engineering-lead", target="qa",
                                      tx="tx-happy-qa")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["target"]["role"], "qa")
        self.assertEqual(result["trigger"]["count"], 1)

    def test_mention_ready_envelope_in_result_and_ledger(self):
        result, _, ledger = run_tx(tx="tx-envelope")
        ready = result["mention_ready_envelope"]
        self.assertIsNotNone(ready)
        self.assertEqual(ready["transaction_id"], "tx-envelope")
        self.assertEqual(ready["issue"]["id"], ISSUE_ID)
        self.assertEqual(ready["caller"]["agent_id"], AGENT_SA)
        self.assertEqual(ready["target"]["agent_id"], AGENT_SE)
        self.assertTrue(ready["published_comment_id"])
        recorded = [r for r in ledger.records if r.get("kind") ==
                    "mention_ready"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["route"], "mention")
        self.assertEqual(recorded[0]["authorized_mentions"], 1)

    def test_published_note_is_non_trigger(self):
        result, fake, ledger = run_tx(tx="tx-note")
        body = fake.comments[ISSUE_ID][0]["content"]
        self.assertEqual(body.splitlines()[0], "/note")
        self.assertNotIn("mention://", body)
        for number, line in enumerate(body.splitlines()[1:], start=2):
            self.assertFalse(line.startswith("/"), number)


class StageSemanticsTests(unittest.TestCase):
    def test_ready_stage_never_triggers(self):
        result, fake, ledger = run_tx(stage="ready", tx="tx-ready-only")
        self.assertEqual(result["terminal_status"], "MENTION_READY")
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["mention_ready_envelope"])
        self.assertEqual(len(fake.comments[ISSUE_ID]), 1)
        self.assertEqual(result["trigger"], {"type": "mention", "count": 0,
                                             "confirmed": False, "runs": 0})
        records = [r for r in ledger.records
                   if r.get("transaction_id") == "tx-ready-only"]
        audit = men.audit_mention_ledger(records)
        self.assertTrue(audit["ok"], audit)
        self.assertEqual(audit["mention_authorizations"], 0)
        self.assertEqual(audit["run_correlations"], 0)

    def test_staged_execute_completes_from_ledger(self):
        ledger = dispatch.TransactionLedger()
        fake = FakeMultica()
        ready, _, _ = run_tx(fake, stage="ready", ledger=ledger,
                             tx="tx-staged")
        self.assertEqual(ready["terminal_status"], "MENTION_READY")
        done, _, _ = run_tx(fake=fake, ledger=ledger,
                            tx="tx-staged", stage="full")
        self.assertEqual(done["terminal_status"], "COMPLETED", done)
        self.assertEqual(done["transitions"], FULL_TRANSITIONS)
        self.assertEqual(done["trigger"]["count"], 1)
        self.assertEqual(done["trigger"]["runs"], 1)

    def test_staged_run_execute_stage_helper(self):
        ledger = dispatch.TransactionLedger()
        fake = FakeMultica()
        ready, _, _ = run_tx(fake, stage="ready", ledger=ledger,
                             tx="tx-helper")
        self.assertEqual(ready["terminal_status"], "MENTION_READY")
        done = men.run_execute_stage(
            ledger, "tx-helper", runner=fake,
            mention_evidence=mention_evidence(
                "tx-helper", author=AGENT_SA),
            run_evidence=run_evidence("tx-helper"))
        self.assertEqual(done["terminal_status"], "COMPLETED", done)

    def test_execute_without_staged_ready_is_refused(self):
        ledger = dispatch.TransactionLedger()
        refused = men.run_execute_stage(
            ledger, "tx-no-ready", runner=FakeMultica(),
            mention_evidence=mention_evidence("tx-no-ready"),
            run_evidence=run_evidence("tx-no-ready"))
        self.assertEqual(refused["terminal_status"], "REPLAY_REFUSED")
        self.assertFalse(refused["ok"])
        self.assertEqual(ledger.commands(), [])

    def test_ready_stage_replay_is_idempotent(self):
        ledger = dispatch.TransactionLedger()
        first, fake, _ = run_tx(stage="ready", ledger=ledger, tx="tx-ready-re")
        self.assertEqual(first["terminal_status"], "MENTION_READY")
        before = len(ledger.records)
        second, _, _ = run_tx(fake=FakeMultica(), stage="ready",
                              ledger=ledger, tx="tx-ready-re")
        self.assertTrue(second.get("replayed"))
        self.assertEqual(second["terminal_status"], "MENTION_READY")
        self.assertEqual(len(ledger.records), before)

    def test_full_replay_is_idempotent(self):
        ledger = dispatch.TransactionLedger()
        first, fake, _ = run_tx(ledger=ledger, tx="tx-full-re")
        self.assertEqual(first["terminal_status"], "COMPLETED")
        before = len(ledger.records)
        second, _, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                              tx="tx-full-re")
        self.assertTrue(second.get("replayed"))
        self.assertEqual(second["terminal_status"], "COMPLETED")
        self.assertEqual(second["commands"], [])
        self.assertEqual(len(ledger.records), before)

    def test_incomplete_replay_is_refused_without_new_commands(self):
        ledger = dispatch.TransactionLedger()
        stopped, fake, _ = run_tx(ledger=ledger, tx="tx-incomplete",
                                  mention=mention_evidence(
                                      "tx-incomplete", mentions=[]))
        self.assertEqual(stopped["terminal_status"],
                         "MENTION_EVIDENCE_REJECTED")
        before = len(ledger.records)
        refused, _, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                               tx="tx-incomplete")
        self.assertEqual(refused["terminal_status"], "REPLAY_REFUSED")
        self.assertEqual(len(ledger.records), before)


class RoutingAndReadFailureTests(unittest.TestCase):
    def test_unknown_role_routes(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), target="astronaut",
                              ledger=ledger, tx="tx-route-unknown")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("comment_publish", 0), 0)
        self.assertTrue(result["escalation"]["required"])

    def test_context_engineer_never_a_target(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), target="context-engineer",
                              ledger=ledger, tx="tx-route-02")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 0)

    def test_unbound_role_routes(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), target="context-engineer",
                              ledger=ledger, tx="tx-route-unbound")
        self.assertEqual(result["terminal_status"], "ROUTING_REQUIRED")

    def test_issue_read_failed_is_unverified(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(get_fails=True), ledger=ledger,
                              tx="tx-get-fail")
        self.assertEqual(result["terminal_status"], "ISSUE_UNVERIFIED")
        self.assertFalse(result["ok"])
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 0)

    def test_issue_response_invalid(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(get_malformed=True), ledger=ledger,
                              tx="tx-get-bad")
        self.assertEqual(result["terminal_status"], "ISSUE_RESPONSE_INVALID")
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 0)


class PrepareStopTests(unittest.TestCase):
    def test_blocked_finding_stops_before_publish_and_mention(self):
        ledger = dispatch.TransactionLedger()
        finding = blocking_finding(TASK_REF)
        result, _, _ = run_tx(FakeMultica(), world={"findings": [finding]},
                              ledger=ledger, tx="tx-blocked")
        self.assertEqual(result["terminal_status"], "PREPARE_BLOCKED")
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("comment_publish", 0), 0)
        self.assertTrue(result["escalation"]["required"])

    def test_partial_default_stops(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(
            FakeMultica(),
            world={"docs": [world_rule(status="review_needed")]},
            ledger=ledger, tx="tx-partial-stop",
            policy={"allow_partial_publication": False})
        self.assertEqual(result["terminal_status"], "PREPARE_PARTIAL_STOPPED")
        self.assertTrue(result["extra"]["gaps"])
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 0)

    def test_partial_with_policy_stops_one_trigger_before_ready_work(self):
        ledger = dispatch.TransactionLedger()
        result, fake, _ = run_tx(
            FakeMultica(),
            world={"docs": [world_rule(status="review_needed")]},
            ledger=ledger, tx="tx-partial-ok",
            policy={"allow_partial_publication": True,
                    "authorized_by": "engineering-lead"})
        decisions = [r for r in ledger.records
                     if r.get("kind") == "policy_decision"]
        self.assertEqual(len(decisions), 1)
        self.assertTrue(decisions[0]["gaps"])
        self.assertNotIn(result["terminal_status"],
                         ("COMPLETED", "PREPARE_PARTIAL_STOPPED"))
        self.assertEqual(result["trigger"]["count"], 1)
        self.assertEqual(result["trigger"]["runs"], 1)


class ConfirmationStopTests(unittest.TestCase):
    def test_missing_confirmation_stops_before_mention(self):
        ledger = dispatch.TransactionLedger()
        result, fake, _ = run_tx(FakeMultica(drop_note=True), ledger=ledger,
                                 tx="tx-confirm-missing")
        self.assertEqual(result["terminal_status"], "CONFIRMATION_FAILED")
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 1)
        self.assertEqual(len([r for r in ledger.records
                              if r.get("kind") == "mention_outcome"]), 0)

    def test_stale_foreign_newer_record_fails_confirmation(self):
        ledger = dispatch.TransactionLedger()
        foreign = ("/note\n\nCTX-HANDOFF/1\nmeta: "
                   "{\"package_id\":\"pkg-foreign\"}\n")
        result, fake, _ = run_tx(
            FakeMultica(note_corruptor=lambda body: foreign),
            ledger=ledger, tx="tx-confirm-stale")
        self.assertEqual(result["terminal_status"], "CONFIRMATION_FAILED")


class MentionEvidenceScenarioTests(unittest.TestCase):
    def test_missing_evidence_is_ambiguous_and_never_retried(self):
        ledger = dispatch.TransactionLedger()
        result, fake, _ = run_tx(FakeMultica(), ledger=ledger,
                                 tx="tx-amb", mention=None)
        self.assertEqual(result["terminal_status"],
                         "MENTION_CONFIRMATION_REQUIRED")
        self.assertFalse(result["ok"])
        before = len(ledger.records)
        refused, _, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                               tx="tx-amb")
        self.assertEqual(refused["terminal_status"], "REPLAY_REFUSED")
        self.assertEqual(len(ledger.records), before)

    def test_plain_text_mention_fails_closed(self):
        ledger = dispatch.TransactionLedger()
        result, fake, _ = run_tx(
            FakeMultica(), ledger=ledger, tx="tx-plain",
            mention=mention_evidence("tx-plain", mentions=[],
                                     handles=["@04 Software Engineer"]))
        self.assertEqual(result["terminal_status"], "MENTION_EVIDENCE_REJECTED")
        self.assertEqual(result["trigger"]["count"], 0)
        refused, _, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                               tx="tx-plain")
        self.assertEqual(refused["terminal_status"], "REPLAY_REFUSED")

    def test_fabricated_link_fails_closed(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-forge",
                              mention=mention_evidence(
                                  "tx-forge",
                                  link="mention://agent/fabricated"))
        self.assertEqual(result["terminal_status"], "MENTION_EVIDENCE_REJECTED")

    def test_wrong_target_fails_closed(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-wrong",
                              mention=mention_evidence("tx-wrong",
                                                       agent_id=AGENT_QA,
                                                       role="qa"))
        self.assertEqual(result["terminal_status"], "MENTION_EVIDENCE_REJECTED")

    def test_multiple_mentions_fail_closed(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-multi",
                              mention=mention_evidence("tx-multi", mentions=[
                                  {"agent_id": AGENT_SE,
                                   "role": "software-engineer",
                                   "link": "mention://agent/" + AGENT_SE},
                                  {"agent_id": AGENT_QA, "role": "qa",
                                   "link": "mention://agent/" + AGENT_QA}]))
        self.assertEqual(result["terminal_status"], "MENTION_EVIDENCE_REJECTED")

    def test_assignment_plus_mention_fails_closed(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(
            FakeMultica(), ledger=ledger, tx="tx-double",
            mention=mention_evidence("tx-double", mutation={
                "argv": ["multica", "issue", "assign", ISSUE_ID,
                         "--to-id", AGENT_SE]}))
        self.assertEqual(result["terminal_status"], "MENTION_EVIDENCE_REJECTED")
        audit = result["audit"]
        self.assertEqual(audit["assignment_trigger"]["count"], 0)

    def test_assignment_in_route_fails_audit(self):
        result, _, ledger = run_tx(tx="tx-mixed")
        records = [r for r in ledger.records
                   if r.get("transaction_id") == "tx-mixed"]
        records.append({
            "kind": "command", "seq": 999, "command_class":
            "assignment_trigger", "transaction_id": "tx-mixed",
            "argv": ["multica", "issue", "assign", ISSUE_ID,
                     "--to-id", AGENT_SE]})
        audit = men.audit_mention_ledger(records)
        self.assertFalse(audit["ok"])
        cross = men.cross_route_audit(records)
        self.assertFalse(cross["ok"])


class RunCorrelationScenarioTests(unittest.TestCase):
    def test_zero_runs_stops_without_second_mention(self):
        ledger = dispatch.TransactionLedger()
        result, fake, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-run0",
                                 run_ev=run_evidence("tx-run0", runs=[]))
        self.assertEqual(result["terminal_status"], "RUN_CORRELATION_FAILED")
        self.assertEqual(result["trigger"]["count"], 1)
        self.assertEqual(result["trigger"]["runs"], 0)
        before = len(ledger.records)
        refused, _, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                               tx="tx-run0")
        self.assertEqual(refused["terminal_status"], "REPLAY_REFUSED")
        self.assertEqual(len(ledger.records), before)

    def test_duplicate_runs_stops(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-run2",
                              run_ev=run_evidence("tx-run2", runs=[
                                  {"agent_id": AGENT_SE, "run_id": "r-1",
                                   "source": "mention"},
                                  {"agent_id": AGENT_SE, "run_id": "r-2",
                                   "source": "mention"}]))
        self.assertEqual(result["terminal_status"], "RUN_CORRELATION_FAILED")
        reasons = result["extra"]["error"]["details"].get("reason")
        self.assertEqual(reasons, "multiple_target_runs")

    def test_wrong_target_run_stops(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-run-wrong",
                              run_ev=run_evidence("tx-run-wrong",
                                                  agent_id=AGENT_QA))
        self.assertEqual(result["terminal_status"], "RUN_CORRELATION_FAILED")

    def test_uncorrelated_run_source_stops(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-run-src",
                              run_ev=run_evidence("tx-run-src",
                                                  source="assignment"))
        self.assertEqual(result["terminal_status"], "RUN_CORRELATION_FAILED")

    def test_missing_run_evidence_stops(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger, tx="tx-run-none",
                              run_ev=None)
        self.assertEqual(result["terminal_status"], "RUN_CORRELATION_FAILED")


class RouteMutualExclusionTests(unittest.TestCase):
    def test_assignment_route_holds_handoff(self):
        ledger = dispatch.TransactionLedger()
        ledger.append(assignment_result_record(tx="tx-assign-held"))
        result, fake, _ = run_tx(FakeMultica(), ledger=ledger,
                                 tx="tx-t10-conflict")
        self.assertEqual(result["terminal_status"], "ROUTE_CONFLICT")
        counts = result["audit"]["command_counts"]
        self.assertEqual(counts.get("comment_publish", 0), 0)
        self.assertTrue(result["escalation"]["required"])

    def test_assignment_route_on_same_package_is_refused(self):
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger,
                              tx="tx-pkg-conflict", stage="ready")
        self.assertEqual(result["terminal_status"], "MENTION_READY")
        package_id = result["package"]["package_id"]
        ledger.append(assignment_result_record(
            tx="tx-assign-pkg", package_id=package_id))
        again, fake, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                                tx="tx-pkg-conflict-2")
        self.assertEqual(again["terminal_status"], "ROUTE_CONFLICT")
        self.assertEqual(fake.comment_add_calls, [])

    def test_mention_route_never_creates_issues_or_assigns(self):
        result, fake, ledger = run_tx(tx="tx-purity")
        classes = {r["command_class"] for r in ledger.commands()}
        self.assertNotIn("issue_create", classes)
        self.assertNotIn("assignment_trigger", classes)


class SelfCheckScenarioTests(unittest.TestCase):
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
        self.assertEqual(result["trigger"]["count"], 1)
        self.assertEqual(result["trigger"]["runs"], 1)
        skipped = [r for r in ledger.records
                   if r.get("kind") == "refresh_publish_skipped"]
        self.assertEqual(len(skipped), 1)
        evidence = men.acceptance_evidence(result, ledger)
        self.assertEqual(evidence["intended_mention_count_per_handoff"], 1)

    def test_second_unresolved_refresh_stops_work(self):
        live = selfcheck.current_revisions()
        stale = dict(live)
        stale["memory_revision"] = "sha256:" + "f" * 64
        result, _, _ = run_tx(world={"current": lambda: stale},
                              tx="tx-refresh-loop")
        self.assertEqual(result["terminal_status"], "SELF_REFRESH_EXHAUSTED")
        self.assertEqual(result["self_check"]["attempts"], 2)
        self.assertEqual(result["trigger"]["count"], 1)

    def test_self_check_blocked_stops_before_work(self):
        task_ref = TASK_REF
        finding = blocking_finding(task_ref)
        ledger = dispatch.TransactionLedger()
        result, _, _ = run_tx(FakeMultica(), ledger=ledger,
                              tx="tx-self-check-ok")
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
        result2, _, _ = run_tx(ledger=dispatch.TransactionLedger(),
                               finding_store=late, tx="tx-self-blocked")
        self.assertEqual(result2["terminal_status"], "SELF_CHECK_BLOCKED")
        self.assertEqual(result2["trigger"]["count"], 1)
        self.assertFalse(result2["ok"])


class AssigneeStabilityTests(unittest.TestCase):
    def test_platform_mutation_fails_closed(self):
        ledger = dispatch.TransactionLedger()
        result, fake, _ = run_tx(
            FakeMultica(flip_assignee_on_write=1), ledger=ledger,
            tx="tx-mutation")
        self.assertEqual(result["terminal_status"],
                         "ASSIGNMENT_MUTATION_DETECTED")
        self.assertFalse(result["ok"])
        self.assertEqual(result["assignee"]["unchanged"], False)
        self.assertTrue(result["escalation"]["required"])

    def test_assignee_observed_before_and_after(self):
        result, fake, ledger = run_tx(tx="tx-observe")
        observations = [r for r in ledger.records
                        if r.get("kind") == "assignee_observation"]
        self.assertGreaterEqual(len(observations), 2)
        self.assertEqual(observations[0]["phase"], "before")
        self.assertEqual(observations[-1]["phase"], "after")
        self.assertTrue(observations[-1]["verified"])
        self.assertEqual(observations[-1]["assignee_id"], AGENT_SA)


class CliTests(unittest.TestCase):
    def _run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOLS / "chandoff_mention.py"), *args],
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

    def test_cli_validate_spec_rejects_smuggled_mention(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps(
                {"issue_id": ISSUE_ID + " mention://agent/x",
                 "project_id": "web-imagegen"}), encoding="utf-8")
            proc = self._run_cli(
                "validate-spec", "--spec-file", str(spec_path),
                "--caller-role", "solution-architect",
                "--transaction-id", "tx-cli-invalid")
            self.assertEqual(proc.returncode, 2, proc.stdout)
            doc = json.loads(proc.stdout)
            self.assertEqual(doc["terminal_status"], "INVALID_INPUT")

    def test_cli_ready_stage_fail_closed_and_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "spec.json"
            spec_path.write_text(json.dumps(base_spec()), encoding="utf-8")
            ledger_path = Path(tmp) / "ledger.jsonl"
            ready_proc = self._run_cli(
                "ready", "--spec-file", str(spec_path),
                "--fixture-runner-file", self._fixture_file(tmp),
                "--transaction-id", "tx-cli-staged",
                "--caller-role", "solution-architect",
                "--target-role", "software-engineer",
                "--ledger-file", str(ledger_path))
            self.assertEqual(ready_proc.returncode, 2, ready_proc.stdout)
            ready_doc = json.loads(ready_proc.stdout)
            self.assertEqual(ready_doc["result"]["terminal_status"],
                             "CONFIRMATION_FAILED")
            self.assertEqual(ready_doc["acceptance_evidence"]
                             ["mention_is_only_trigger"], True)
            ledger = dispatch.TransactionLedger.load(ledger_path)
            audit = men.audit_mention_ledger(ledger.records)
            self.assertTrue(audit["ok"], audit)
            self.assertEqual(audit["comment_publish"]["count"], 1)
            self.assertEqual(audit["mention_authorizations"], 0)
            mention_path = Path(tmp) / "mention.json"
            run_path = Path(tmp) / "run.json"
            mention_path.write_text(json.dumps(
                mention_evidence("tx-cli-staged")), encoding="utf-8")
            run_path.write_text(json.dumps(
                run_evidence("tx-cli-staged")), encoding="utf-8")
            exec_proc = self._run_cli(
                "execute", "--ledger-file", str(ledger_path),
                "--transaction-id", "tx-cli-staged",
                "--mention-evidence-file", str(mention_path),
                "--run-evidence-file", str(run_path),
                "--fixture-runner-file", self._fixture_file(tmp))
            self.assertEqual(exec_proc.returncode, 2, exec_proc.stdout)
            exec_doc = json.loads(exec_proc.stdout)
            self.assertEqual(exec_doc["result"]["terminal_status"],
                             "REPLAY_REFUSED")

    def _fixture_file(self, tmp: Path) -> str:
        fixture = [
            {"match": ["version"], "code": 0,
             "stdout": json.dumps({"version": "v0.4.42"})},
            {"match": ["issue", "get"], "code": 0, "stdout": json.dumps({
                "id": ISSUE_ID, "identifier": IDENTIFIER,
                "title": "Existing drill issue",
                "description": "Drill: - Requirement: build X\n"
                               "- Acceptance: works",
                "parent_issue_id": None, "project_id": None,
                "assignee_id": AGENT_SA, "assignee": None})},
            {"match": ["issue", "comment", "list"], "code": 0,
             "stdout": "[]"},
            {"match": ["issue", "comment", "add"], "code": 0,
             "stdout": json.dumps({"id": "c-0001",
                                   "created_at": "2026-09-10T08:00:01Z",
                                   "parent_id": None})},
        ]
        path = Path(tmp) / "fixture.json"
        path.write_text(json.dumps(fixture), encoding="utf-8")
        return str(path)

    def test_cli_audit_and_cross_route_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = dispatch.TransactionLedger()
            result, _, _ = run_tx(ledger=ledger, tx="tx-cli-evidence")
            self.assertTrue(result["ok"], result)
            path = Path(tmp) / "ledger.jsonl"
            ledger.save(path)
            proc = self._run_cli("audit", "--ledger-file", str(path))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["ok"])
            proc = self._run_cli("cross-route-audit",
                                 "--ledger-file", str(path))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["ok"])
            proc = self._run_cli("replay", "--transaction-id",
                                 "tx-cli-evidence", "--ledger-file", str(path))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["terminal_status"],
                             "COMPLETED")

    def test_cli_cross_route_audit_flags_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = dispatch.TransactionLedger()
            ledger.append(assignment_result_record())
            result, _, _ = run_tx(fake=FakeMultica(), ledger=ledger,
                                  tx="tx-cli-conflict")
            self.assertEqual(result["terminal_status"], "ROUTE_CONFLICT")
            path = Path(tmp) / "ledger.jsonl"
            ledger.save(path)
            proc = self._run_cli("cross-route-audit", "--ledger-file",
                                 str(path))
            self.assertEqual(proc.returncode, 2, proc.stdout)
            doc = json.loads(proc.stdout)
            self.assertFalse(doc["ok"])


if __name__ == "__main__":
    unittest.main()
