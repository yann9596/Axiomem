#!/usr/bin/env python3
"""O2 focused tests — durable dispatch intent and reconciliation (YZT-79).

Every path runs against an isolated temporary ledger. A fixture runner serves
canned responses; the module under test never constructs a live runner. The
cross-process proof spawns real, independent Python processes (no platform
call). Nothing here mutates any issue, run, comment, Canonical Memory or the
product repository.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_intent as o2  # noqa: E402
from chandoff_intent import O2FixtureRunner, RefusingRunner  # noqa: E402

PROBE = TOOLS / "o2_store_probe.py"

TARGET_AGENT = "fa7d16a7-2dae-4994-80b8-7435b3fcca47"
OTHER_AGENT = "1303827b-73d1-4d71-a461-00b93e4b4418"
LEAD_AGENT = "24f04aba-7da9-4371-bf89-685d7505a411"
PARENT = "01a08921-207c-7a39-8a99-b431e75bed9f"
ISSUE_ID = "01a08c2e-596c-78df-8865-d00d10fdad9c"
CREATED_ID = "01a08f00-0000-7000-8000-000000000001"
PACKAGE = "CTX-software-engineer-9f2c41d7b8e05a13"
PACKAGE_B = "CTX-software-engineer-0b1c2d3e4f5a6b7c"
ARTIFACT = "sha256:" + "a" * 64
ARTIFACT_B = "sha256:" + "b" * 64
NOTE = "01a08e36-3421-76a7-9ccc-ccb56634746b"
NOTE_B = "01a08e37-0000-7000-8000-000000000002"
INTENT_ID = "DI-" + "1" * 16
RUN_ID = "01a08ddc-7698-7c18-943b-f354ee6ec8fb"
RUN_ID_B = "01a08ddd-0000-7000-8000-000000000003"
BASE_TIME = "2026-09-11T00:00:00Z"


def iso(value: str, seconds: int) -> str:
    parsed = o2.parse_ts(value) + timedelta(seconds=seconds)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock:
    def __init__(self, value: str = BASE_TIME):
        self.value = value

    def __call__(self) -> str:
        return self.value

    def advance(self, seconds: int) -> str:
        self.value = iso(self.value, seconds)
        return self.value


def store_at(tmp: Path, name: str = "shared-ledger.jsonl") -> o2.DurableIntentStore:
    return o2.DurableIntentStore(tmp / name)


def run_json(run_id: str = RUN_ID, *, agent: str = TARGET_AGENT,
             issue: str = ISSUE_ID, status: str = "queued") -> dict:
    return {"id": run_id, "issue_id": issue, "agent_id": agent,
            "status": status}


def run_payload(run_id: str = RUN_ID, **over) -> str:
    return json.dumps(run_json(run_id, **over), sort_keys=True)


def intent_fields(**over) -> dict:
    fields = {
        "intent_id": INTENT_ID, "schema_version": o2.O2_SCHEMA,
        "source_task_id": "multica://issue/YZT-66",
        "logical_task_key": "YZT-90-dispatch",
        "parent_issue_id": PARENT,
        "target_role": "software-engineer",
        "target_agent_id": TARGET_AGENT,
        "package_id": PACKAGE,
        "artifact_dependency_digest": ARTIFACT,
        "creation_authority": "01 engineering-lead via YZT-66 SAFE_DISPATCH",
        "provenance": {"issue": "YZT-79"},
    }
    fields.update(over)
    return fields


def issue_payload(**over) -> dict:
    payload = {"id": ISSUE_ID, "identifier": "YZT-77", "title": "T",
               "description": "", "parent_issue_id": PARENT,
               "project_id": None, "revision": 1,
               "status_category": "backlog", "assignee_id": TARGET_AGENT}
    payload.update(over)
    return payload


def snapshot(**over) -> dict:
    status = over.pop("status", "backlog")
    assignee = over.pop("assignee", TARGET_AGENT)
    revision = over.pop("revision", 1)
    runs = over.pop("runs", [])
    package_id = over.pop("package_id", PACKAGE)
    artifact = over.pop("artifact", ARTIFACT)
    artifact_ready = over.pop("artifact_ready", True)
    ready = over.pop("ready", True)
    used_routes = over.pop("used_routes", None)
    trusted = over.pop("trusted", True)
    target_agent = over.pop("target_agent", TARGET_AGENT)
    ready_note = ({"comment_id": NOTE, "package_id": package_id,
                   "artifact_dependency_digest": artifact} if ready else None)
    return o2.build_snapshot(
        issue={"id": ISSUE_ID, "revision": revision,
               "status_category": status, "assignee_id": assignee},
        runs=runs,
        package={"package_id": package_id,
                 "artifact_dependency_digest": artifact,
                 "artifact_ready": artifact_ready},
        ready_note=ready_note, target_role="software-engineer",
        target_agent_id=target_agent, runs_trusted=trusted,
        used_routes=used_routes)


def orchestrator(store, table, clock=None):
    runner = O2FixtureRunner(table)
    orch = o2.O2Orchestrator(store, runner=runner,
                             clock=clock or Clock())
    return orch, runner


def bind_flow(store, table, *, clock=None, intent_over=None,
              issue_over=None, actor="source-run-1", prepared=True):
    orch, runner = orchestrator(store, table, clock)
    orch.record_intent(intent_fields(**(intent_over or {})))
    bound = orch.bind_target(INTENT_ID, issue=o2_issue(issue_over),
                             actor=actor)
    marked = None
    if prepared:
        marked = orch.mark_prepared(INTENT_ID, package_id=PACKAGE,
                                    artifact_dependency_digest=ARTIFACT,
                                    actor=actor)
    return orch, runner, bound, marked


def o2_issue(over=None) -> dict:
    payload = issue_payload()
    payload.update(over or {})
    return payload


def _issue_payload_json(**over) -> str:
    return json.dumps(issue_payload(**over), sort_keys=True)


def ready_table(*, rerun=True, assign=None, runs_post=None,
                get_responses=None, binding=False, binding_response=None,
                children=None, create=None):
    table = []
    if create is not None:
        table.append({"match": ["issue", "create"], **create})
    if children is not None:
        table.append({"match": ["issue", "children", PARENT], **children})
    if binding:
        table.append({
            "match": ["issue", "assign", ISSUE_ID, "--to-id", TARGET_AGENT,
                      "--no-start"],
            "stdout": json.dumps(binding_response or {
                "id": ISSUE_ID, "assignee_id": TARGET_AGENT}),
        })
        if not get_responses:
            get_responses = [_issue_payload_json(assignee_id=TARGET_AGENT)]
    if get_responses:
        table.append({"match": ["issue", "get", ISSUE_ID],
                      "responses": get_responses})
    if assign is not None:
        table.append({"match": ["issue", "assign", ISSUE_ID, "--to-id",
                                TARGET_AGENT, "--output", "json"],
                      **assign})
    if rerun is not False:
        table.append({"match": ["issue", "rerun", ISSUE_ID],
                      **(rerun if isinstance(rerun, dict) else
                          {"stdout": run_payload()})})
    table.append({"match": ["issue", "runs", ISSUE_ID],
                  "stdout": json.dumps(
                      runs_post if runs_post is not None else [run_json()])})
    return table


class StoreFoundationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_record_and_enumerate(self):
        record = self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.assertEqual(record["seq"], 1)
        folded = self.store.fold()
        self.assertEqual(len(folded["intents"]), 1)
        intent = folded["intents"][INTENT_ID]
        self.assertEqual(intent["state"], o2.S_INTENT_RECORDED)
        self.assertEqual(intent["fields"]["target_role"], "software-engineer")

    def test_enumeration_from_independent_store_instance(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        other = store_at(self.tmp)
        self.assertIn(INTENT_ID, other.fold()["intents"])

    def test_sequence_is_monotonic_across_appends(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.append({"kind": "command", "argv": ["issue", "get", "x"]})
        records = self.store.read_records()
        self.assertEqual([r["seq"] for r in records], [1, 2])

    def test_duplicate_intent_fails_closed(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        with self.assertRaises(o2.DuplicateIntentError):
            self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.assertEqual(len(self.store.read_records()), 1)

    def test_duplicate_open_logical_key_fails_closed(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        with self.assertRaises(o2.DuplicateLogicalKeyError):
            self.store.record_intent(
                intent_fields(intent_id="DI-" + "2" * 16), now=BASE_TIME)

    def test_logical_key_reusable_after_terminal(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        self.store.transition(INTENT_ID, o2.S_PARKED_NOT_DUE,
                              expected_revision=0, actor="a", now=BASE_TIME,
                              reason="PARKED_NOT_DUE",
                              fields={"dependency": "dep", "next_owner": "01",
                                      "wake_boundary": "U11"})
        self.store.record_intent(
            intent_fields(intent_id="DI-" + "3" * 16), now=BASE_TIME)
        self.assertEqual(len(self.store.fold()["intents"]), 2)

    def test_partial_record_fails_closed(self):
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text(
            '{"kind": "command", "argv": ["issue", "get"\n', encoding="utf-8")
        with self.assertRaises(o2.LedgerCorruptionError):
            self.store.fold()

    def test_corrupt_record_blocks_further_appends(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        with open(self.store.path, "a", encoding="utf-8") as handle:
            handle.write("not json at all\n")
        with self.assertRaises(o2.LedgerCorruptionError):
            self.store.append({"kind": "command", "argv": ["issue", "get"]})

    def test_non_object_record_fails_closed(self):
        self.store.path.write_text("[1, 2, 3]\n", encoding="utf-8")
        with self.assertRaises(o2.LedgerCorruptionError):
            self.store.fold()

    def test_unknown_schema_version_is_ignored_not_inferred(self):
        self.store.path.write_text(json.dumps({
            "kind": "intent", "record_type": o2.INTENT_RECORD_TYPE,
            "schema_version": "O2-dispatch-intent/9.9", "op": "recorded",
            "intent_id": INTENT_ID, "intent": intent_fields(),
        }) + "\n", encoding="utf-8")
        folded = self.store.fold()
        self.assertEqual(folded["intents"], {})
        self.assertEqual(folded["ignored_records"], 1)

    def test_unknown_op_in_known_schema_fails_closed(self):
        record = {"kind": "intent", "record_type": o2.INTENT_RECORD_TYPE,
                  "schema_version": o2.O2_SCHEMA, "op": "explode",
                  "intent_id": INTENT_ID}
        self.store.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaises(o2.LedgerCorruptionError):
            self.store.fold()

    def test_transition_requires_lease(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        with self.assertRaises(o2.LeaseNotHeldError):
            self.store.transition(INTENT_ID, o2.S_TARGET_BOUND,
                                  expected_revision=0, actor="a",
                                  now=BASE_TIME)

    def test_lease_held_by_other_fails_closed(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        with self.assertRaises(o2.LeaseHeldError):
            self.store.claim(INTENT_ID, "b", now=BASE_TIME, ttl_seconds=300)

    def test_lease_same_holder_is_idempotent(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        again = self.store.claim(INTENT_ID, "a", now=BASE_TIME,
                                 ttl_seconds=300)
        self.assertEqual(again["outcome"], "already_held")

    def test_stale_lease_recovers_with_evidence(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=1)
        later = iso(BASE_TIME, 10)
        result = self.store.claim(INTENT_ID, "b", now=later, ttl_seconds=300)
        self.assertTrue(result["record"]["expired_previous"])
        self.assertEqual(result["record"]["previous_holder"], "a")
        self.assertEqual(self.store.lease_view(INTENT_ID, now=later)["holder"],
                         "b")

    def test_release_then_reclaim_by_other(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        self.store.release(INTENT_ID, "a", now=BASE_TIME)
        result = self.store.claim(INTENT_ID, "b", now=BASE_TIME,
                                  ttl_seconds=300)
        self.assertEqual(result["outcome"], "claim")

    def test_renew_requires_holder(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        with self.assertRaises(o2.LeaseNotHeldError):
            self.store.renew(INTENT_ID, "b", now=BASE_TIME, ttl_seconds=300)

    def test_cas_conflict_fails_closed(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        self.store.transition(INTENT_ID, o2.S_TARGET_BOUND,
                              expected_revision=0, actor="a", now=BASE_TIME)
        with self.assertRaises(o2.CasConflictError):
            self.store.transition(INTENT_ID, o2.S_TARGET_BOUND,
                                  expected_revision=0, actor="a",
                                  now=BASE_TIME)

    def test_illegal_transition_fails_closed(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        with self.assertRaises(o2.IllegalTransitionError):
            self.store.transition(INTENT_ID, o2.S_COMPLETED,
                                  expected_revision=0, actor="a",
                                  now=BASE_TIME)

    def test_parked_transition_requires_parked_fields(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        with self.assertRaises(o2.IntentError):
            self.store.transition(INTENT_ID, o2.S_PARKED_NOT_DUE,
                                  expected_revision=0, actor="a",
                                  now=BASE_TIME, reason="PARKED_NOT_DUE")

    def test_stop_reason_is_required(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        with self.assertRaises(o2.IntentError):
            self.store.transition(INTENT_ID, o2.S_BLOCKED,
                                  expected_revision=0, actor="a",
                                  now=BASE_TIME)

    def test_committed_record_shape_is_shared_ledger_readable(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        text = self.store.path.read_text(encoding="utf-8")
        ledger = dispatch.TransactionLedger.from_jsonl(text)
        self.assertEqual(len(ledger.records), 1)
        self.assertEqual(ledger.records[0]["record_type"],
                         o2.INTENT_RECORD_TYPE)


class CrossProcessTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store_path = self.tmp / "shared-ledger.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _probe(self, command, cwd):
        return subprocess.run(
            [sys.executable, str(PROBE)] + command,
            capture_output=True, text=True, encoding="utf-8", cwd=str(cwd),
            timeout=120)

    def _intent_file(self, name: str, intent_id: str, key: str) -> Path:
        path = self.tmp / name
        path.write_text(json.dumps(intent_fields(
            intent_id=intent_id, logical_task_key=key), sort_keys=True),
            encoding="utf-8")
        return path

    def test_capability_proof_all_passed(self):
        proof = o2.capability_proof(self.tmp)
        self.assertTrue(proof["all_passed"], proof)

    def test_independent_workdir_enumeration(self):
        cwd_a = self.tmp / "a"
        cwd_b = self.tmp / "b"
        cwd_a.mkdir()
        cwd_b.mkdir()
        intent_file = self._intent_file("intent.json", INTENT_ID,
                                        "probe-key")
        first = self._probe(
            ["append-intent", "--store", str(self.store_path),
             "--intent-file", str(intent_file), "--now", BASE_TIME], cwd_a)
        self.assertEqual(first.returncode, 0)
        second = self._probe(
            ["enumerate", "--store", str(self.store_path)], cwd_b)
        payload = json.loads(second.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual([row["intent_id"] for row in payload["intents"]],
                         [INTENT_ID])

    def test_probe_duplicate_and_corruption_codes(self):
        intent_file = self._intent_file("intent.json", INTENT_ID, "probe-key")
        self._probe(["append-intent", "--store", str(self.store_path),
                     "--intent-file", str(intent_file), "--now", BASE_TIME],
                    self.tmp)
        dup = self._probe(
            ["append-intent", "--store", str(self.store_path),
             "--intent-file", str(intent_file), "--now", BASE_TIME], self.tmp)
        self.assertNotEqual(dup.returncode, 0)
        self.assertEqual(json.loads(dup.stdout.strip().splitlines()[-1])["code"],
                         "duplicate_intent")
        with open(self.store_path, "a", encoding="utf-8") as handle:
            handle.write("{broken\n")
        corrupt = self._probe(
            ["enumerate", "--store", str(self.store_path)], self.tmp)
        self.assertEqual(corrupt.returncode, 4)
        self.assertEqual(
            json.loads(corrupt.stdout.strip().splitlines()[-1])["code"],
            "ledger_corruption")


class PlannerTests(unittest.TestCase):
    def test_backlog_assigned_selects_one_rerun(self):
        plan = o2.plan_trigger(snapshot())
        self.assertEqual(plan["decision"], o2.DECISION_TRIGGER_READY)
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_RERUN)
        self.assertIsNone(plan["ownership_binding"])

    def test_backlog_unassigned_binds_then_reruns(self):
        plan = o2.plan_trigger(snapshot(assignee=None))
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_RERUN)
        self.assertEqual(plan["ownership_binding"],
                         o2.BINDING_ASSIGN_NO_START)

    def test_backlog_different_assignee_binds_then_reruns(self):
        plan = o2.plan_trigger(snapshot(assignee=OTHER_AGENT))
        self.assertEqual(plan["ownership_binding"],
                         o2.BINDING_ASSIGN_NO_START)

    def test_active_unassigned_uses_assignment(self):
        plan = o2.plan_trigger(snapshot(status="todo", assignee=None))
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_ASSIGN)
        self.assertIsNone(plan["ownership_binding"])

    def test_active_different_assignee_uses_assignment(self):
        plan = o2.plan_trigger(snapshot(status="in_progress",
                                        assignee=OTHER_AGENT))
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_ASSIGN)

    def test_active_assigned_prefers_rerun(self):
        plan = o2.plan_trigger(snapshot(status="in_review",
                                        assignee=TARGET_AGENT))
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_RERUN)

    def test_status_promotion_route_is_rejected(self):
        plan = o2.plan_trigger(snapshot(),
                               route=o2.TRIGGER_STATUS_PROMOTION)
        self.assertEqual(plan["decision"], o2.DECISION_BLOCKED)
        self.assertEqual(plan["reason"], o2.R_STATUS_PROMOTION_ROUTE)

    def test_mention_route_is_its_own_trigger(self):
        plan = o2.plan_trigger(snapshot(), route=o2.ROUTE_MENTION)
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_MENTION)
        self.assertEqual(plan["native_receipt"], "comment")

    def test_mention_refused_when_assignment_used(self):
        plan = o2.plan_trigger(snapshot(used_routes=["assignment"]),
                               route=o2.ROUTE_MENTION)
        self.assertEqual(plan["decision"], o2.DECISION_BLOCKED)
        self.assertEqual(plan["reason"], o2.R_ROUTE_CONFLICT)

    def test_assignment_refused_when_mention_used(self):
        plan = o2.plan_trigger(snapshot(used_routes=["mention"]))
        self.assertEqual(plan["decision"], o2.DECISION_BLOCKED)
        self.assertEqual(plan["reason"], o2.R_ROUTE_CONFLICT)

    def test_route_argument_mismatch_fails_closed(self):
        with self.assertRaises(o2.RouteConflictError):
            o2.plan_trigger(snapshot(), route=o2.ROUTE_ASSIGNMENT,
                            requested_route=o2.ROUTE_MENTION)

    def test_artifact_not_ready_requires_refresh(self):
        plan = o2.plan_trigger(snapshot(artifact_ready=False))
        self.assertEqual(plan["decision"], o2.DECISION_REFRESH_REQUIRED)
        self.assertEqual(plan["reason"], o2.R_ARTIFACT_STALE)

    def test_missing_ready_note_requires_refresh(self):
        plan = o2.plan_trigger(snapshot(ready=False))
        self.assertEqual(plan["reason"], o2.R_READY_NOTE_MISSING)

    def test_ready_note_package_mismatch_requires_refresh(self):
        snap = snapshot()
        snap["ready_note_package_id"] = PACKAGE_B
        plan = o2.plan_trigger(snap)
        self.assertEqual(plan["reason"], o2.R_PACKAGE_STALE)

    def test_untrusted_runs_fail_closed(self):
        plan = o2.plan_trigger(snapshot(trusted=False))
        self.assertEqual(plan["reason"], o2.R_RUN_STATE_UNDETERMINED)

    def test_unexpected_active_run_blocks(self):
        plan = o2.plan_trigger(snapshot(runs=[run_json(
            run_id=RUN_ID_B, status="running")]))
        self.assertEqual(plan["reason"], o2.R_UNEXPECTED_RUN)

    def test_known_pre_trigger_run_is_ignored(self):
        snap = snapshot(runs=[run_json(run_id=RUN_ID_B, status="running")])
        snap["known_run_ids"] = [RUN_ID_B]
        plan = o2.plan_trigger(snap)
        self.assertEqual(plan["decision"], o2.DECISION_TRIGGER_READY)

    def test_terminal_target_status_blocks(self):
        plan = o2.plan_trigger(snapshot(status="done"))
        self.assertEqual(plan["decision"], o2.DECISION_BLOCKED)

    def test_snapshot_requires_exact_revision(self):
        with self.assertRaises(o2.SnapshotIncompleteError):
            o2.build_snapshot(
                issue={"id": ISSUE_ID, "status_category": "backlog"},
                runs=[], package={"package_id": PACKAGE},
                ready_note=None, target_role="software-engineer",
                target_agent_id=TARGET_AGENT)

    def test_snapshot_requires_known_status(self):
        with self.assertRaises(o2.SnapshotIncompleteError):
            o2.build_snapshot(
                issue={"id": ISSUE_ID, "revision": 1, "status_category": "???"},
                runs=[], package={"package_id": PACKAGE},
                ready_note=None, target_role="software-engineer",
                target_agent_id=TARGET_AGENT)

    def test_retired_role_refused(self):
        retired = next(k for k in o2.RETIRED_ROLES if "feature" in k)
        with self.assertRaises(o2.RetiredIdentityError):
            o2.build_snapshot(
                issue=issue_payload(), runs=[],
                package={"package_id": PACKAGE}, ready_note=None,
                target_role=retired, target_agent_id=TARGET_AGENT)

    def test_all_retired_identities_refuse_without_alias(self):
        self.assertEqual(len(o2.RETIRED_ROLES), 2)
        for retired in o2.RETIRED_ROLES:
            with self.assertRaises(o2.RetiredIdentityError):
                o2._require_role(retired)

    def test_unknown_role_refused(self):
        with self.assertRaises(o2.IntentError):
            o2._require_role("chief-vibes-officer")

    def test_live_runner_is_never_implicit(self):
        with self.assertRaises(o2.NotAuthorizedError):
            o2.O2DispatchBoundary(None, store_at(Path(tempfile.mkdtemp())))

    def test_matrix_document_never_combines_triggers(self):
        for row in o2.trigger_matrix():
            self.assertLessEqual(len([t for t in
                                      (row["selected_trigger"],
                                       row["ownership_binding"])
                                      if t and t in o2.NATIVE_TRIGGERS]), 1)

    def test_plan_is_exactly_one_trigger(self):
        plan = o2.plan_trigger(snapshot(assignee=None))
        self.assertEqual(plan["selected_trigger"], o2.TRIGGER_RERUN)
        selected = [plan["selected_trigger"], plan["ownership_binding"]]
        self.assertEqual(len([t for t in selected if t in o2.NATIVE_TRIGGERS]),
                         1)


class OrchestratorCreateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_target_creation_before_intent_is_refused(self):
        orch, runner = orchestrator(self.store, [])
        with self.assertRaises(o2.IntentNotFoundError):
            orch.create_target("DI-" + "f" * 16, title="t",
                               description="d", parent_issue_id=PARENT,
                               actor="a")
        self.assertEqual(runner.issued, [])

    def test_intent_is_persisted_before_create_command(self):
        table = [
            {"match": ["issue", "create"],
             "stdout": json.dumps({"id": CREATED_ID, "identifier": "YZT-90",
                                   "title": "T", "description":
                                   f"body O2-INTENT {INTENT_ID}",
                                   "assignee_id": None})},
            {"match": ["issue", "get", CREATED_ID],
             "stdout": json.dumps(issue_payload(id=CREATED_ID, revision=1,
                                                assignee_id=None),
                                  sort_keys=True)},
        ]
        orch, runner = orchestrator(self.store, table)
        orch.record_intent(intent_fields())
        result = orch.create_target(
            INTENT_ID, title="T",
            description=f"body O2-INTENT {INTENT_ID}",
            parent_issue_id=PARENT, actor="a")
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        records = self.store.read_records()
        intent_seq = next(r["seq"] for r in records
                          if r.get("op") == "recorded")
        marker_seq = next(r["seq"] for r in records
                          if r.get("name") == o2.E_CREATE_ISSUING)
        create_seq = next(r["seq"] for r in records
                          if r.get("kind") == "command"
                          and r.get("command_class") == o2.C_ISSUE_CREATE)
        self.assertLess(intent_seq, marker_seq)
        self.assertLess(marker_seq, create_seq)

    def test_create_description_must_embed_intent_id(self):
        orch, runner = orchestrator(self.store, [])
        orch.record_intent(intent_fields())
        with self.assertRaises(o2.IntentError):
            orch.create_target(INTENT_ID, title="T", description="no marker",
                               parent_issue_id=PARENT, actor="a")
        self.assertEqual(runner.issued, [])

    def test_ambiguous_create_without_discovery_is_blocked(self):
        table = [
            {"match": ["issue", "create"], "code": 2, "stderr": "boom"},
            {"match": ["issue", "children", PARENT],
             "stdout": json.dumps({"stages": []})},
        ]
        orch, runner = orchestrator(self.store, table)
        orch.record_intent(intent_fields())
        result = orch.create_target(
            INTENT_ID, title="T",
            description=f"body O2-INTENT {INTENT_ID}",
            parent_issue_id=PARENT, actor="a")
        self.assertEqual(result["status"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(result["reason"], o2.R_CREATE_AMBIGUOUS)
        self.assertEqual(len([a for a in runner.issued
                              if o2.classify_o2_command(a)
                              == o2.C_ISSUE_CREATE]), 1)

    def test_ambiguous_create_discovers_exactly_one_target(self):
        table = [
            {"match": ["issue", "create"], "code": 2, "stderr": "lost"},
            {"match": ["issue", "children", PARENT],
             "stdout": json.dumps({"stages": [{"issues": [
                 {"id": CREATED_ID, "identifier": "YZT-90",
                  "description": f"body O2-INTENT {INTENT_ID}"}]}]})},
            {"match": ["issue", "get", CREATED_ID],
             "stdout": json.dumps(issue_payload(id=CREATED_ID,
                                                assignee_id=None),
                                  sort_keys=True)},
        ]
        orch, runner = orchestrator(self.store, table)
        orch.record_intent(intent_fields())
        result = orch.create_target(
            INTENT_ID, title="T",
            description=f"body O2-INTENT {INTENT_ID}",
            parent_issue_id=PARENT, actor="a")
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        self.assertEqual(result["issue_id"], CREATED_ID)
        intent = self.store.get(INTENT_ID)
        self.assertTrue(o2._latest_field(
            intent, "discovered_by_read_only_proof"))
        self.assertEqual(len([a for a in runner.issued
                              if o2.classify_o2_command(a)
                              == o2.C_ISSUE_CREATE]), 1)

    def test_ambiguous_create_two_candidates_never_binds(self):
        table = [
            {"match": ["issue", "create"], "code": 2, "stderr": "lost"},
            {"match": ["issue", "children", PARENT],
             "stdout": json.dumps({"stages": [{"issues": [
                 {"id": CREATED_ID, "description": INTENT_ID},
                 {"id": CREATED_ID + "2", "description": INTENT_ID}]}]})},
        ]
        orch, _ = orchestrator(self.store, table)
        orch.record_intent(intent_fields())
        result = orch.create_target(
            INTENT_ID, title="T",
            description=f"body O2-INTENT {INTENT_ID}",
            parent_issue_id=PARENT, actor="a")
        self.assertEqual(result["status"], o2.S_CREATE_AMBIGUOUS)

    def test_bind_target_revision_drift(self):
        orch, _ = orchestrator(self.store, [])
        orch.record_intent(intent_fields(expected_issue_revision=7))
        result = orch.bind_target(INTENT_ID, issue=o2_issue(), actor="a")
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], o2.R_REVISION_DRIFT)


class OrchestratorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def _full(self, *, status="backlog", assignee=TARGET_AGENT,
              table=None, snapshot_over=None, issue_over=None):
        table = table if table is not None else ready_table()
        orch, runner, bound, marked = bind_flow(
            self.store, table, issue_over=issue_over or {})
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest="sha256:" + "c" * 64,
                            actor="source-run-1")
        snap = snapshot(status=status, assignee=assignee,
                        **(snapshot_over or {}))
        armed = orch.plan_and_arm(INTENT_ID, snap, actor="source-run-1")
        return orch, runner, armed, snap

    def test_backlog_assigned_issues_one_rerun(self):
        orch, runner, armed, snap = self._full()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(result["run_id"], RUN_ID)
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)
        self.assertEqual(o2.classify_o2_command(triggers[0]),
                         o2.C_RERUN_TRIGGER)
        audit = o2.o2_audit_ledger(self.store.read_records())
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["ownership_bindings"], 0)

    def test_backlog_unassigned_binds_ownership_then_one_rerun(self):
        table = ready_table(binding=True)
        orch, runner, bound, marked = bind_flow(
            self.store, table, issue_over={"assignee_id": None})
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest="sha256:" + "c" * 64,
                            actor="source-run-1")
        snap = snapshot(assignee=None)
        self.assertEqual(
            orch.plan_and_arm(INTENT_ID, snap, actor="source-run-1")["status"],
            o2.S_TRIGGER_READY)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        classes = [o2.classify_o2_command(a) for a in runner.issued]
        self.assertEqual(classes.count(o2.C_OWNERSHIP_BINDING), 1)
        self.assertEqual(classes.count(o2.C_RERUN_TRIGGER), 1)
        self.assertEqual(classes.count(o2.C_ASSIGNMENT_TRIGGER), 0)
        audit = o2.o2_audit_ledger(self.store.read_records())
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["ownership_bindings"], 1)
        self.assertEqual(audit["triggers"], 1)

    def test_active_unassigned_uses_one_assignment(self):
        table = ready_table(assign={"stdout": json.dumps(
            {"id": ISSUE_ID, "assignee_id": TARGET_AGENT})})
        orch, runner, bound, marked = bind_flow(
            self.store, table, issue_over={"status_category": "todo",
                                           "assignee_id": None})
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest="sha256:" + "c" * 64,
                            actor="source-run-1")
        snap = snapshot(status="todo", assignee=None)
        orch.plan_and_arm(INTENT_ID, snap, actor="source-run-1")
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        classes = [o2.classify_o2_command(a) for a in runner.issued]
        self.assertEqual(classes.count(o2.C_ASSIGNMENT_TRIGGER), 1)
        self.assertEqual(classes.count(o2.C_RERUN_TRIGGER), 0)
        self.assertEqual(classes.count(o2.C_OWNERSHIP_BINDING), 0)

    def test_assignment_success_without_run_is_not_success(self):
        table = ready_table(assign={"stdout": json.dumps(
            {"id": ISSUE_ID, "assignee_id": TARGET_AGENT})},
            runs_post=[])
        orch, runner, bound, marked = bind_flow(
            self.store, table, issue_over={"status_category": "todo",
                                           "assignee_id": None})
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest="sha256:" + "c" * 64,
                            actor="source-run-1")
        snap = snapshot(status="todo", assignee=None)
        orch.plan_and_arm(INTENT_ID, snap, actor="source-run-1")
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_TRIGGER_ISSUING)
        self.assertTrue(result["awaiting_visibility"])
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_TRIGGER_ISSUING)
        audit = o2.o2_audit_ledger(self.store.read_records())
        self.assertTrue(audit["ok"])
        self.assertEqual(
            audit["issuance"][INTENT_ID]["correlated_transitions"], 0)

    def test_trigger_issuing_recorded_before_native_call(self):
        orch, runner, armed, snap = self._full()
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        records = self.store.read_records()
        issuing = next(r["seq"] for r in records
                       if r.get("op") == "transition"
                       and r.get("to") == o2.S_TRIGGER_ISSUING)
        rerun = next(r["seq"] for r in records
                     if r.get("kind") == "command"
                     and r.get("command_class") == o2.C_RERUN_TRIGGER)
        self.assertLess(issuing, rerun)

    def test_publish_is_idempotent_for_same_note(self):
        table = ready_table()
        orch, runner, bound, marked = bind_flow(self.store, table)
        first = orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                                    receipt_digest=None, actor="a")
        second = orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                                     receipt_digest=None, actor="a")
        self.assertFalse(first.get("replayed", False))
        self.assertTrue(second["replayed"])
        self.assertEqual(len(self.store.get(INTENT_ID)["transitions"]), 3)

    def test_duplicate_different_note_fails_closed(self):
        table = ready_table()
        orch, runner, bound, marked = bind_flow(self.store, table)
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest=None, actor="a")
        with self.assertRaises(o2.IntentError):
            orch.mark_published(INTENT_ID, note_comment_id=NOTE_B,
                                receipt_digest=None, actor="a")

    def test_package_drift_before_arming_requires_refresh(self):
        table = ready_table()
        orch, runner, bound, marked = bind_flow(self.store, table)
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest=None, actor="a")
        snap = snapshot(package_id=PACKAGE_B, artifact=ARTIFACT_B)
        result = orch.plan_and_arm(INTENT_ID, snap, actor="a")
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], o2.R_PACKAGE_STALE)

    def test_assignee_drift_before_issuance_requires_refresh(self):
        orch, runner, armed, snap = self._full()
        drifted = dict(snap)
        drifted["assignee_id"] = OTHER_AGENT
        drifted["snapshot_digest"] = o2.digest(
            o2._snapshot_projection(drifted))
        result = orch.issue_trigger(INTENT_ID, drifted, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], o2.R_ASSIGNEE_DRIFT)
        self.assertEqual([a for a in runner.issued
                          if o2.classify_o2_command(a)
                          in o2.TRIGGER_COMMAND_CLASSES], [])

    def test_untrusted_run_evidence_blocks_before_issuance(self):
        orch, runner, armed, snap = self._full()
        untrusted = dict(snap)
        untrusted["runs_trusted"] = False
        untrusted["snapshot_digest"] = o2.digest(
            o2._snapshot_projection(untrusted))
        result = orch.issue_trigger(INTENT_ID, untrusted,
                                    actor="source-run-1")
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], o2.R_RUN_STATE_UNDETERMINED)
        self.assertEqual([a for a in runner.issued
                          if o2.classify_o2_command(a)
                          in o2.TRIGGER_COMMAND_CLASSES], [])

    def test_failed_rerun_response_is_trigger_ambiguous(self):
        table = ready_table(rerun={"code": 2, "stderr": "connection lost"})
        orch, runner, armed, snap = self._full(table=table)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertEqual(result["reason"], o2.R_TRIGGER_AMBIGUOUS)
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_rerun_without_one_correlated_run_stays_pending(self):
        table = ready_table(runs_post=[])
        orch, runner, armed, snap = self._full(table=table)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_TRIGGER_ISSUING)
        self.assertTrue(result["awaiting_visibility"])

    def test_duplicate_runs_fail_closed(self):
        table = ready_table(runs_post=[run_json(RUN_ID),
                                       run_json(RUN_ID_B)])
        orch, runner, armed, snap = self._full(table=table)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], o2.R_RUN_CORRELATION_FAILED)

    def test_wrong_target_run_fails_closed(self):
        table = ready_table(runs_post=[run_json(RUN_ID_B,
                                                agent=OTHER_AGENT)])
        orch, runner, armed, snap = self._full(table=table)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_BLOCKED)

    def test_preexisting_run_is_not_correlated(self):
        table = ready_table(runs_post=[run_json(RUN_ID_B, status="completed")])
        orch, runner, armed, snap = self._full(
            table=table,
            snapshot_over={"runs": [run_json(RUN_ID_B, status="completed")]})
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_TRIGGER_ISSUING)

    def test_self_check_and_completion(self):
        orch, runner, armed, snap = self._full()
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        checked = orch.record_self_check(INTENT_ID, status="CLEAR",
                                         actor="target-run-1")
        self.assertEqual(checked["status"], o2.S_SELF_CHECKED)
        done = orch.complete(INTENT_ID, actor="source-run-1")
        self.assertEqual(done["status"], o2.S_COMPLETED)
        self.assertEqual(self.store.get(INTENT_ID)["state"], o2.S_COMPLETED)

    def test_self_check_blocked_marks_blocked(self):
        orch, runner, armed, snap = self._full()
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        result = orch.record_self_check(INTENT_ID, status="BLOCKED",
                                        actor="target-run-1")
        self.assertEqual(result["status"], o2.S_BLOCKED)

    def test_completed_replay_emits_zero_side_effects(self):
        orch, runner, armed, snap = self._full()
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        orch.record_self_check(INTENT_ID, status="CLEAR", actor="t")
        orch.complete(INTENT_ID, actor="a")
        issued_before = len(runner.issued)
        records_before = len(self.store.read_records())
        replay = orch.issue_trigger(INTENT_ID, snap, actor="a")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["side_effects"], 0)
        again = orch.complete(INTENT_ID, actor="a")
        self.assertTrue(again["replayed"])
        recon = orch.reconcile(INTENT_ID, {}, actor="a")
        self.assertEqual(recon["classification"], "TERMINAL_REPLAY")
        self.assertEqual(len(runner.issued), issued_before)
        self.assertEqual(len(self.store.read_records()), records_before)

    def test_provider_failure_is_execution_recovery_not_redispatch(self):
        orch, runner, armed, snap = self._full()
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        with self.assertRaises(o2.IntentError):
            orch.record_execution_recovery(INTENT_ID, run_id=RUN_ID_B,
                                           failure="quota", actor="a")
        result = orch.record_execution_recovery(
            INTENT_ID, run_id=RUN_ID, failure="provider_quota", actor="a")
        self.assertFalse(result["redispatch"])
        self.assertFalse(result["dispatch_orphan"])
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_RUN_CORRELATED)
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_parked_requires_dependency_owner_wake(self):
        orch, runner, armed, snap = self._full()
        result = orch.mark_parked(INTENT_ID, dependency="U11 replay",
                                  next_owner="01 engineering-lead",
                                  wake_boundary="U11 start", actor="a")
        self.assertEqual(result["status"], o2.S_PARKED_NOT_DUE)
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_PARKED_NOT_DUE)

    def test_issuance_requires_the_lease_owner(self):
        orch, runner, armed, snap = self._full()
        self.store.claim(INTENT_ID, "other-writer", now=BASE_TIME,
                         ttl_seconds=300)
        with self.assertRaises(o2.LeaseHeldError):
            orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual([a for a in runner.issued
                          if o2.classify_o2_command(a)
                          in o2.TRIGGER_COMMAND_CLASSES], [])
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_TRIGGER_READY)

    def test_one_correlated_run_is_maximum(self):
        orch, runner, armed, snap = self._full()
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        intent = self.store.get(INTENT_ID)
        correlated = [t for t in intent["transitions"]
                      if t.get("to") == o2.S_RUN_CORRELATED]
        self.assertEqual(len(correlated), 1)
        self.store.claim(INTENT_ID, "source-run-1", now=BASE_TIME,
                         ttl_seconds=300)
        with self.assertRaises(o2.CasConflictError):
            self.store.transition(
                INTENT_ID, o2.S_RUN_CORRELATED,
                expected_revision=intent["revision"] - 1,
                actor="source-run-1", now=BASE_TIME,
                fields={"correlated_run_id": RUN_ID_B})


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def _ambiguous(self):
        table = ready_table(rerun={"code": 2, "stderr": "lost"},
                            runs_post=[])
        orch, runner, armed, snap = self._flow(table)
        result = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        return orch, runner, snap

    def _flow(self, table):
        orch, runner, bound, marked = bind_flow(self.store, table)
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest="sha256:" + "c" * 64,
                            actor="source-run-1")
        snap = snapshot()
        orch.plan_and_arm(INTENT_ID, snap, actor="source-run-1")
        return orch, runner, None, snap

    def test_later_one_run_proof_attaches_without_retrigger(self):
        orch, runner, snap = self._ambiguous()
        evidence = {"runs": [run_json()], "runs_trusted": True}
        result = orch.reconcile(INTENT_ID, evidence, actor="reconciler")
        self.assertEqual(result["action"], "ATTACH_RUN")
        self.assertEqual(result["state"], o2.S_RUN_CORRELATED)
        self.assertFalse(result["trigger_reissued"])
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_RUN_CORRELATED)
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_lost_response_with_zero_runs_stays_ambiguous(self):
        orch, runner, snap = self._ambiguous()
        evidence = {"runs": [], "runs_trusted": True}
        result = orch.reconcile(INTENT_ID, evidence, actor="reconciler")
        self.assertEqual(result["classification"],
                         "TRIGGER_AMBIGUOUS_NO_RUN_PROOF")
        self.assertEqual(result["action"], "NONE")
        self.assertEqual(self.store.get(INTENT_ID)["state"],
                         o2.S_TRIGGER_AMBIGUOUS)
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_delayed_visibility_attaches_later(self):
        table = ready_table(runs_post=[])
        orch, runner, _, snap = self._flow(table)
        issued = orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        self.assertEqual(issued["status"], o2.S_TRIGGER_ISSUING)
        result = orch.reconcile(INTENT_ID,
                                {"runs": [run_json()], "runs_trusted": True},
                                actor="reconciler")
        self.assertEqual(result["classification"],
                         "DELAYED_RUN_VISIBILITY_ONE_RUN")
        self.assertEqual(result["action"], "ATTACH_RUN")
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_delayed_visibility_zero_runs_waits_read_only(self):
        table = ready_table(runs_post=[])
        orch, runner, _, snap = self._flow(table)
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        result = orch.reconcile(INTENT_ID,
                                {"runs": [], "runs_trusted": True},
                                actor="reconciler")
        self.assertEqual(result["classification"],
                         "DELAYED_RUN_VISIBILITY_ZERO")
        self.assertEqual(result["action"], "WAIT_READ_ONLY")

    def test_untrusted_evidence_waits_without_action(self):
        table = ready_table(runs_post=[])
        orch, runner, _, snap = self._flow(table)
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        result = orch.reconcile(INTENT_ID, {"runs": [], "runs_trusted": False},
                                actor="reconciler")
        self.assertEqual(result["classification"], "RUN_EVIDENCE_UNTRUSTED")
        self.assertEqual(result["action"], "WAIT_READ_ONLY")

    def test_provider_failure_classifies_execution_recovery(self):
        table = ready_table()
        orch, runner, _, snap = self._flow(table)
        orch.issue_trigger(INTENT_ID, snap, actor="source-run-1")
        result = orch.reconcile(INTENT_ID, {"provider_failure": True},
                                actor="reconciler")
        self.assertEqual(result["classification"], "PROVIDER_QUOTA_FAILURE")
        self.assertEqual(result["action"], "EXECUTION_RECOVERY")

    def test_post_publish_pre_trigger_resumes_one_trigger(self):
        table = ready_table()
        orch, runner, bound, marked = bind_flow(self.store, table)
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest=None, actor="a")
        snap = snapshot()
        orch.plan_and_arm(INTENT_ID, snap, actor="a")
        evidence = {"runs": [], "runs_trusted": True, "snapshot": snap}
        # Simulate a crash: TRIGGER_READY with no issuance marker.
        result = orch.resume(INTENT_ID, evidence, actor="reconciler")
        self.assertEqual(result["status"], o2.S_RUN_CORRELATED)
        triggers = [a for a in runner.issued
                    if o2.classify_o2_command(a) in o2.TRIGGER_COMMAND_CLASSES]
        self.assertEqual(len(triggers), 1)

    def test_pre_create_resume_needs_create_input(self):
        orch, runner = orchestrator(self.store, [])
        orch.record_intent(intent_fields())
        result = orch.resume(INTENT_ID, {}, actor="reconciler")
        self.assertEqual(result["classification"],
                         "POST_INTENT_PRE_CREATE_KILL")
        self.assertEqual(result["action"], "RESUME_CREATE")
        self.assertFalse(result["performed"])

    def test_ambiguous_create_resume_attaches_single_discovery(self):
        table = [
            {"match": ["issue", "create"], "code": 2, "stderr": "lost"},
            {"match": ["issue", "children", PARENT],
             "stdout": json.dumps({"stages": []})},
            {"match": ["issue", "get", CREATED_ID],
             "stdout": json.dumps(issue_payload(id=CREATED_ID,
                                                assignee_id=None),
                                  sort_keys=True)},
        ]
        orch, runner = orchestrator(self.store, table)
        orch.record_intent(intent_fields())
        orch.create_target(INTENT_ID, title="T",
                           description=f"O2-INTENT {INTENT_ID}",
                           parent_issue_id=PARENT, actor="a")
        result = orch.reconcile(
            INTENT_ID, {"discovered_targets": [{"id": CREATED_ID}]},
            actor="reconciler")
        self.assertEqual(result["action"], "ATTACH_DISCOVERED_TARGET")
        self.assertTrue(result["performed"])
        self.assertEqual(self.store.get(INTENT_ID)["state"], o2.S_TARGET_BOUND)

    def test_completed_replay_via_resume_is_inert(self):
        table = ready_table()
        orch, runner, bound, marked = bind_flow(self.store, table)
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest=None, actor="a")
        snap = snapshot()
        orch.plan_and_arm(INTENT_ID, snap, actor="a")
        orch.issue_trigger(INTENT_ID, snap, actor="a")
        orch.record_self_check(INTENT_ID, status="CLEAR", actor="t")
        orch.complete(INTENT_ID, actor="a")
        commands_before = len(runner.issued)
        result = orch.resume(INTENT_ID, {"runs": [run_json()]},
                             actor="reconciler")
        self.assertEqual(result["classification"], "COMPLETED_REPLAY")
        self.assertEqual(len(runner.issued), commands_before)


class ParentWakeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)
        self.orch, self.runner, _, _ = bind_flow(self.store, [])
        self.orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                                 receipt_digest=None, actor="a")

    def tearDown(self):
        self._tmp.cleanup()

    def test_delivered_wake_never_repeats(self):
        self.orch.record_parent_wake(INTENT_ID, outcome="delivered",
                                     lead_agent_id=LEAD_AGENT,
                                     run_id=RUN_ID_B, actor="a")
        decision = self.orch.plan_parent_wake(INTENT_ID, lead_runs=[])
        self.assertEqual(decision["decision"], "NO_NEW_WAKE")

    def test_failed_wake_with_active_lead_run_requires_repair(self):
        self.orch.record_parent_wake(INTENT_ID, outcome="failed",
                                     lead_agent_id=LEAD_AGENT,
                                     run_id=RUN_ID_B, actor="a")
        decision = self.orch.plan_parent_wake(
            INTENT_ID, lead_runs=[run_json(RUN_ID_B, agent=LEAD_AGENT,
                                           status="running")])
        self.assertEqual(decision["decision"], "WAKE_REPAIR_REQUIRED")

    def test_failed_wake_without_active_run_allows_one_more(self):
        self.orch.record_parent_wake(INTENT_ID, outcome="failed",
                                     lead_agent_id=LEAD_AGENT, actor="a")
        decision = self.orch.plan_parent_wake(INTENT_ID, lead_runs=[])
        self.assertEqual(decision["decision"], "WAKE_ONE")

    def test_two_failed_wakes_exhaust_the_budget(self):
        for _ in range(2):
            self.orch.record_parent_wake(INTENT_ID, outcome="failed",
                                         lead_agent_id=LEAD_AGENT, actor="a")
        decision = self.orch.plan_parent_wake(INTENT_ID, lead_runs=[])
        self.assertEqual(decision["decision"], "WAKE_EXHAUSTED")

    def test_wake_route_is_mention_and_never_combined(self):
        self.orch.record_parent_wake(INTENT_ID, outcome="delivered",
                                     lead_agent_id=LEAD_AGENT, actor="a")
        event = o2._latest_event(self.store.get(INTENT_ID),
                                 o2.E_PARENT_WAKE)
        self.assertEqual(event["data"]["route"], "mention")
        self.assertFalse(event["data"]["combined_with_trigger"])


class ExitGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def _intent_to(self, target_state):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        chains = {
            o2.S_TARGET_BOUND: [o2.S_TARGET_BOUND],
            o2.S_HANDOFF_PREPARED: [o2.S_TARGET_BOUND, o2.S_HANDOFF_PREPARED],
            o2.S_TRIGGER_ISSUING: [o2.S_TARGET_BOUND,
                                   o2.S_HANDOFF_PREPARED,
                                   o2.S_HANDOFF_PUBLISHED,
                                   o2.S_TRIGGER_READY,
                                   o2.S_TRIGGER_ISSUING],
            o2.S_TRIGGER_AMBIGUOUS: [o2.S_TARGET_BOUND,
                                     o2.S_HANDOFF_PREPARED,
                                     o2.S_HANDOFF_PUBLISHED,
                                     o2.S_TRIGGER_READY,
                                     o2.S_TRIGGER_ISSUING,
                                     o2.S_TRIGGER_AMBIGUOUS],
        }
        for state in chains[target_state]:
            intent = self.store.get(INTENT_ID)
            self.store.transition(
                INTENT_ID, state, expected_revision=intent["revision"],
                actor="a", now=BASE_TIME,
                reason=(o2.R_TRIGGER_AMBIGUOUS
                        if state == o2.S_TRIGGER_AMBIGUOUS else None))

    def test_due_target_without_intent_blocks_exit(self):
        result = o2.lead_exit_check(
            target_refs=[{"issue_id": ISSUE_ID, "intent_id": None}],
            store=self.store)
        self.assertFalse(result["ok"])
        self.assertEqual(result["due_target_without_intent_or_terminal_stop"],
                         1)

    def test_intent_in_progress_state_blocks_exit(self):
        self._intent_to(o2.S_TRIGGER_ISSUING)
        result = o2.lead_exit_check(
            target_refs=[{"issue_id": ISSUE_ID, "intent_id": INTENT_ID}],
            store=self.store)
        self.assertFalse(result["ok"])

    def test_correlated_run_satisfies_exit(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        for state in (o2.S_TARGET_BOUND, o2.S_HANDOFF_PREPARED,
                      o2.S_HANDOFF_PUBLISHED, o2.S_TRIGGER_READY,
                      o2.S_TRIGGER_ISSUING):
            intent = self.store.get(INTENT_ID)
            self.store.transition(INTENT_ID, state,
                                  expected_revision=intent["revision"],
                                  actor="a", now=BASE_TIME)
        intent = self.store.get(INTENT_ID)
        self.store.transition(INTENT_ID, o2.S_RUN_CORRELATED,
                              expected_revision=intent["revision"],
                              actor="a", now=BASE_TIME,
                              fields={"correlated_run_id": RUN_ID})
        result = o2.lead_exit_check(
            target_refs=[{"issue_id": ISSUE_ID, "intent_id": INTENT_ID}],
            store=self.store)
        self.assertTrue(result["ok"], result)

    def test_parked_claim_requires_all_three_fields(self):
        result = o2.lead_exit_check(
            target_refs=[{"issue_id": ISSUE_ID,
                          "parked": {"dependency": "U11"}}],
            store=self.store)
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["parked_target_without_dependency_owner_wake"], 1)

    def test_complete_parked_claim_satisfies_exit(self):
        result = o2.lead_exit_check(
            target_refs=[{"issue_id": ISSUE_ID,
                          "parked": {"dependency": "U11 replay",
                                     "next_owner": "01 engineering-lead",
                                     "wake_boundary": "U11 start"}}],
            store=self.store)
        self.assertTrue(result["ok"], result)

    def test_typed_stops_satisfy_exit(self):
        self._intent_to(o2.S_TRIGGER_AMBIGUOUS)
        result = o2.lead_exit_check(
            target_refs=[{"issue_id": ISSUE_ID, "intent_id": INTENT_ID}],
            store=self.store)
        self.assertTrue(result["ok"], result)


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.store = store_at(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_open_intent_summary_fields(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        later = iso(BASE_TIME, 600)
        view = o2.observe(self.store, now=later)
        self.assertEqual(view["open_count"], 1)
        row = view["open_intents"][0]
        self.assertEqual(row["intent_id"], INTENT_ID)
        self.assertEqual(row["age_seconds"], 600)
        self.assertEqual(row["state"], o2.S_INTENT_RECORDED)
        self.assertEqual(row["next_owner"], "source-run")
        self.assertEqual(row["next_action"], "create_or_bind_target")
        self.assertIsNone(row["lease"])

    def test_active_lease_is_visible(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "writer-1", now=BASE_TIME,
                         ttl_seconds=300)
        view = o2.observe(self.store, now=BASE_TIME)
        lease = view["open_intents"][0]["lease"]
        self.assertEqual(lease["holder"], "writer-1")
        self.assertTrue(lease["active"])

    def test_expired_lease_is_visible_as_expired(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "writer-1", now=BASE_TIME, ttl_seconds=1)
        view = o2.observe(self.store, now=iso(BASE_TIME, 60))
        lease = view["open_intents"][0]["lease"]
        self.assertFalse(lease["active"])
        self.assertEqual(lease["state"], "expired")

    def test_ambiguity_reason_is_exposed(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        for state in (o2.S_TARGET_BOUND, o2.S_HANDOFF_PREPARED,
                      o2.S_HANDOFF_PUBLISHED, o2.S_TRIGGER_READY,
                      o2.S_TRIGGER_ISSUING):
            intent = self.store.get(INTENT_ID)
            self.store.transition(INTENT_ID, state,
                                  expected_revision=intent["revision"],
                                  actor="a", now=BASE_TIME)
        intent = self.store.get(INTENT_ID)
        self.store.transition(INTENT_ID, o2.S_TRIGGER_AMBIGUOUS,
                              expected_revision=intent["revision"],
                              actor="a", now=BASE_TIME,
                              reason=o2.R_TRIGGER_AMBIGUOUS)
        view = o2.observe(self.store, now=BASE_TIME)
        row = view["open_intents"][0]
        self.assertEqual(row["ambiguity_reason"], o2.R_TRIGGER_AMBIGUOUS)
        self.assertEqual(row["next_owner"], "engineering-lead")

    def test_terminal_intents_are_not_open(self):
        self.store.record_intent(intent_fields(), now=BASE_TIME)
        self.store.claim(INTENT_ID, "a", now=BASE_TIME, ttl_seconds=300)
        self.store.transition(INTENT_ID, o2.S_CANCELLED,
                              expected_revision=0, actor="a", now=BASE_TIME,
                              reason="CANCELLED")
        view = o2.observe(self.store, now=BASE_TIME)
        self.assertEqual(view["open_count"], 0)
        self.assertIn(o2.S_CANCELLED, view["counts_by_state"])


class FixtureReplayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_yzt_77_replay_selects_one_rerun_not_assignment(self):
        store = store_at(self.tmp, "yzt-77.jsonl")
        replay = o2.fixture_replay(o2.yzt_77_fixture(), store)
        self.assertEqual(replay["state"], o2.S_RUN_CORRELATED)
        self.assertEqual(replay["selected_trigger"], o2.TRIGGER_RERUN)
        self.assertIsNone(replay["ownership_binding"])
        self.assertEqual(replay["correlated_run_id"], RUN_ID)
        self.assertEqual(len(replay["write_commands"]), 1)
        self.assertEqual(o2.classify_o2_command(replay["write_commands"][0]),
                         o2.C_RERUN_TRIGGER)
        self.assertTrue(replay["audit"]["ok"])
        self.assertEqual(replay["audit"]["ownership_bindings"], 0)

    def test_yzt_78_replay_binds_then_reruns(self):
        store = store_at(self.tmp, "yzt-78.jsonl")
        replay = o2.fixture_replay(o2.yzt_78_fixture(), store)
        self.assertEqual(replay["state"], o2.S_RUN_CORRELATED)
        self.assertEqual(
            replay["ownership_binding"], o2.BINDING_ASSIGN_NO_START)
        self.assertEqual(replay["correlated_run_id"],
                         "01a08de0-e0cb-7adc-890e-c87fcadce826")
        classes = [o2.classify_o2_command(a)
                   for a in replay["write_commands"]]
        self.assertEqual(classes.count(o2.C_OWNERSHIP_BINDING), 1)
        self.assertEqual(classes.count(o2.C_RERUN_TRIGGER), 1)
        self.assertTrue(replay["audit"]["ok"])

    def test_historical_assignment_attempt_is_never_replayed(self):
        for key, fixture in (("yzt-77", o2.yzt_77_fixture()),
                             ("yzt-78", o2.yzt_78_fixture())):
            store = store_at(self.tmp, f"{key}-hist.jsonl")
            replay = o2.fixture_replay(fixture, store)
            for argv in replay["write_commands"]:
                if o2.classify_o2_command(argv) == o2.C_ASSIGNMENT_TRIGGER:
                    self.fail(f"assignment trigger replayed in {key}: {argv}")

    def test_plan_only_replay_issues_no_native_call(self):
        refusing = RefusingRunner()
        store = store_at(self.tmp, "refuse.jsonl")
        orch = o2.O2Orchestrator(store, runner=refusing,
                                 clock=Clock())
        orch.record_intent(intent_fields())
        orch.bind_target(INTENT_ID, issue=o2_issue(), actor="a")
        orch.mark_prepared(INTENT_ID, package_id=PACKAGE,
                           artifact_dependency_digest=ARTIFACT, actor="a")
        orch.mark_published(INTENT_ID, note_comment_id=NOTE,
                            receipt_digest=None, actor="a")
        result = orch.plan_and_arm(INTENT_ID, snapshot(), actor="a")
        self.assertEqual(result["status"], o2.S_TRIGGER_READY)
        self.assertEqual(refusing.issued, [])

    def test_target_inventory_never_reruns_live_issues(self):
        inventory = o2.target_inventory()
        self.assertEqual(len(inventory), 3)
        for row in inventory:
            self.assertIn("fixture only", row["action"])

    def test_legacy_ledger_is_readable_and_unchanged(self):
        legacy_path = (ROOT / "adapters" / "multica" / "assignment-handoff" /
                       "sample-main-path-ledger.jsonl")
        legacy_bytes = legacy_path.read_bytes()
        combined = self.tmp / "combined.jsonl"
        combined.write_bytes(legacy_bytes)
        store = o2.DurableIntentStore(combined)
        store.record_intent(intent_fields(), now=BASE_TIME)
        after = combined.read_bytes()
        self.assertTrue(after.startswith(legacy_bytes))
        ledger = dispatch.TransactionLedger.load(combined)
        self.assertGreater(len(ledger.records), 1)
        folded = o2.fold_records(store.read_records())
        self.assertEqual(len(folded["intents"]), 1)
        self.assertGreater(folded["ignored_records"], 0)
        audit = dispatch.audit_ledger(ledger.records)
        self.assertTrue(audit["ok"], audit)

    def test_evidence_bundle_regenerates_byte_for_byte(self):
        committed = ROOT / "adapters" / "multica" / "dispatch-intent"
        self.assertTrue(committed.is_dir(), "evidence bundle missing")
        with tempfile.TemporaryDirectory() as tmp:
            result = o2.evidence_bundle(tmp, generated_at=BASE_TIME)
            generated = Path(tmp)
            for file in committed.rglob("*"):
                if not file.is_file():
                    continue
                relative = file.relative_to(committed).as_posix()
                self.assertFalse(relative.endswith(".lock"),
                                 f"stray lock file in evidence: {relative}")
                self.assertTrue((generated / relative).is_file(),
                                f"missing regenerated file {relative}")
                self.assertEqual(file.read_bytes(),
                                 (generated / relative).read_bytes(),
                                 relative)
            self.assertGreater(result["file_count"], 5)


class RepoGuardTests(unittest.TestCase):
    def lf_digest(self, rel: str) -> str:
        data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def test_predecessor_runtime_pins_reproduce(self):
        pins = {
            "tools/chandoff_finding.py":
                "sha256:efa29010b5a0b07aa76a329c107b903a54f342e8fa88cc3e99a384a121273848",
            "tools/chandoff_dispatch.py":
                "sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa",
            "tools/chandoff_assignment.py":
                "sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129",
            "tools/chandoff_mention.py":
                "sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1",
            "tools/chandoff_fallback.py":
                "sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b",
        }
        for rel, pin in pins.items():
            self.assertEqual(self.lf_digest(rel), pin, rel)

    def test_u09_evidence_bundle_unchanged(self):
        bundle = ROOT / "adapters" / "multica" / "finding-challenge"
        entries = []
        for path in sorted(bundle.rglob("*")):
            if path.is_file():
                entries.append([path.relative_to(bundle).as_posix(),
                                "sha256:" + hashlib.sha256(
                                    path.read_bytes().replace(b"\r\n", b"\n")
                                ).hexdigest()])
        self.assertEqual(
            chandoff.sha256_text(chandoff.canonical_json(entries)),
            "sha256:d598d2c5db96f3e16eed23627d1a9b8d007412fc21f29a5360f7addc81b005eb")

    def test_t00_scan_clean(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})

    def test_o2_module_declares_no_live_runner(self):
        source = (TOOLS / "chandoff_intent.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess.run([\"multica\"", source)
        self.assertNotIn("_subprocess_runner", source)

    def test_o2_files_exist(self):
        for rel in ("tools/chandoff_intent.py", "tools/o2_store_probe.py",
                    "tools/tests/test_handoff_intent.py"):
            self.assertTrue((ROOT / rel).is_file(), rel)


if __name__ == "__main__":
    unittest.main()
