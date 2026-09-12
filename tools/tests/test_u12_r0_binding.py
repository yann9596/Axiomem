#!/usr/bin/env python3
"""YZT-84 — isolated lifecycle tests for the U12-R0B forward adapter.

Every test uses its own temp ledger, its own fixture CLI and real frozen
package construction (T01 PLAN -> T02 COMPOSE -> T03 FINALIZE -> T04
SELF_CHECK). No live Multica write, no production ledger access.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import chandoff_intent as o2  # noqa: E402
import chandoff_note as note  # noqa: E402
import u12_r0_binding as u12  # noqa: E402

CLOCK = "2026-09-11T06:30:00Z"
PARENT_ID = "01a08921-207c-7a39-8a99-b431e75bed9f"
PARENT_REF = "multica://issue/YZT-66"
TARGET_ID = "01a0aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
TARGET_IDENTIFIER = "YTST-84"
TARGET_AGENT = u12.CANARY_AGENT_ID
PUBLISHER_AGENT = "24f04aba-7da9-4371-bf89-685d7505a411"
PUBLISHER_RUN = "01a08f14-0000-7000-8000-000000000001"
SOURCE_RUN = "01a08f14-0000-7000-8000-000000000002"
DISPATCHER = "dispatcher-actor"


# ---------------------------------------------------------------------------
# fixture CLI
# ---------------------------------------------------------------------------
class FakeCli:
    """Minimal read/write Multica surface used by the R0B lifecycle."""

    simulation_transport = True

    def __init__(self, *, bump_revision_on_comment: bool = True):
        self.issues = {
            PARENT_ID: self._issue(PARENT_ID, "YZT-66", "parent",
                                   parent_issue_id=None, status="in_progress"),
        }
        self.children = {PARENT_ID: []}
        self.comments = {}
        self.activities = {PARENT_ID: []}
        self.runs = {PARENT_ID: []}
        self.commands: list = []
        self.bump_revision_on_comment = bump_revision_on_comment
        self.comment_counter = 0
        self.run_counter = 0
        self.on_comment_add = None
        self.drop_comment_add = False
        self.fail_comment_add_after_store = False
        self.duplicate_note = False
        self.edit_note = False
        self.drop_source_task_id = False
        self.truncate_comment_list = False
        self.truncate_timeline = False
        self.truncate_runs = False
        self.cursor_comment_list = False

    def _issue(self, issue_id, identifier, title, *, parent_issue_id=None,
               status="backlog", description="", project_id=None,
               assignee_id=None, revision=1):
        return {
            "id": issue_id,
            "identifier": identifier,
            "title": title,
            "description": description,
            "parent_issue_id": parent_issue_id,
            "project_id": project_id,
            "assignee_id": assignee_id,
            "assignee_type": "agent" if assignee_id else None,
            "status": status,
            "status_category": status,
            "status_name": "",
            "priority": "urgent",
            "revision": revision,
            "created_at": CLOCK,
            "updated_at": CLOCK,
            "last_activity_at": CLOCK,
            "creator_id": PUBLISHER_AGENT,
            "creator_type": "agent",
            "stage": None,
            "position": 0,
            "number": 84,
            "start_date": None,
            "due_date": None,
            "labels": [],
            "metadata": {},
            "properties": {},
            "workspace_id": "ws",
        }

    # -- helpers -------------------------------------------------------------
    def add_activity(self, issue_id, action, *, details=None):
        self.activities.setdefault(issue_id, []).append({
            "id": f"ACT-{len(self.activities.get(issue_id, [])) + 1}",
            "action": action,
            "actor_id": DISPATCHER,
            "actor_type": "agent",
            "created_at": CLOCK,
            "details": details or {},
            "type": "activity",
        })

    def issue_of(self, issue_id):
        return self.issues[issue_id]

    def commands_of(self, prefix):
        return [argv for argv in self.commands if tuple(argv[1:1 + len(prefix)])
                == tuple(prefix)]

    @staticmethod
    def _json(payload):
        return (0, json.dumps(payload, ensure_ascii=False), "")

    def _argv(self, argv):
        return [str(a) for a in argv]

    # -- runner --------------------------------------------------------------
    def __call__(self, argv):
        argv = self._argv(argv)
        self.commands.append(argv)
        core = argv[1:]
        try:
            if core[:2] == ["issue", "get"]:
                return self._json(copy.deepcopy(self.issues[core[2]]))
            if core[:2] == ["issue", "create"]:
                return self._create(core)
            if core[:2] == ["issue", "assign"]:
                return self._assign(core)
            if core[:3] == ["issue", "comment", "add"]:
                return self._comment_add(core)
            if core[:3] == ["issue", "comment", "list"]:
                return self._comment_list(core)
            if core[:2] == ["issue", "timeline"]:
                return self._timeline(core)
            if core[:2] == ["issue", "runs"]:
                return self._runs(core)
            if core[:2] == ["issue", "rerun"]:
                return self._rerun(core)
            if core[:2] == ["issue", "children"]:
                return self._json({"stages": [
                    {"issues": copy.deepcopy(self.children.get(core[2], []))}]})
            if core[:1] == ["version"]:
                return self._json({"version": "v0.4.42"})
        except Exception as exc:  # pragma: no cover - surfaced as failure
            return (1, "", f"fake failure: {exc}")
        return (1, "", f"unsupported argv: {argv}")

    # -- commands ------------------------------------------------------------
    def _create(self, core):
        title = core[core.index("--title") + 1]
        desc_path = core[core.index("--description-file") + 1]
        description = Path(desc_path).read_text(encoding="utf-8")
        parent = core[core.index("--parent") + 1] if "--parent" in core else None
        project = core[core.index("--project") + 1] if "--project" in core else None
        status = core[core.index("--status") + 1]
        issue = self._issue(TARGET_ID, TARGET_IDENTIFIER, title,
                            parent_issue_id=parent, status=status,
                            description=description, project_id=project)
        self.issues[TARGET_ID] = issue
        self.comments[TARGET_ID] = []
        self.activities[TARGET_ID] = []
        self.runs[TARGET_ID] = []
        self.children.setdefault(parent, []).append(copy.deepcopy(issue))
        self.add_activity(TARGET_ID, "created")
        return self._json(copy.deepcopy(issue))

    def _assign(self, core):
        issue_id = core[2]
        agent_id = core[core.index("--to-id") + 1]
        issue = self.issues[issue_id]
        issue["assignee_id"] = agent_id
        issue["assignee_type"] = "agent"
        issue["revision"] += 1
        self.add_activity(issue_id, "assignee_changed",
                          details={"to": agent_id})
        return self._json(copy.deepcopy(issue))

    def _comment_add(self, core):
        issue_id = core[3]
        content = Path(core[core.index("--content-file") + 1]).read_bytes()
        content = content.decode("utf-8")
        if self.fail_comment_add_after_store:
            self._store_comment(issue_id, content)
            raise OSError("simulated lost response")
        if self.drop_comment_add:
            return self._json({"id": "CMT-DROPPED", "created_at": CLOCK,
                               "parent_id": None})
        comment = self._store_comment(issue_id, content)
        return self._json({"id": comment["id"],
                           "created_at": comment["created_at"],
                           "parent_id": comment["parent_id"]})

    def _store_comment(self, issue_id, content):
        self.comment_counter += 1
        comment = {
            "id": f"CMT-{self.comment_counter}",
            "content": content,
            "created_at": CLOCK,
            "updated_at": CLOCK,
            "parent_id": None,
            "author_id": PUBLISHER_AGENT,
            "author_type": "agent",
            "source_task_id": (None if self.drop_source_task_id
                               else PUBLISHER_RUN),
            "issue_id": issue_id,
            "resolved_at": None,
            "resolved_by_id": None,
            "resolved_by_type": None,
            "revision": 1,
            "type": "comment",
            "attachments": [],
            "reactions": [],
        }
        self.comments.setdefault(issue_id, []).append(comment)
        if self.duplicate_note:
            duplicate = copy.deepcopy(comment)
            duplicate["id"] = f"{comment['id']}-DUP"
            self.comments[issue_id].append(duplicate)
        if self.edit_note:
            comment["updated_at"] = "2026-09-11T06:40:00Z"
            comment["revision"] = 2
        if self.bump_revision_on_comment:
            issue = self.issues[issue_id]
            issue["revision"] += 1
            issue["updated_at"] = "2026-09-11T06:31:00Z"
            issue["last_activity_at"] = "2026-09-11T06:31:00Z"
        if callable(self.on_comment_add):
            self.on_comment_add(self)
        return comment

    def _comment_list(self, core):
        if self.truncate_comment_list:
            return (0, "[]", "warning: response was truncated")
        if self.cursor_comment_list:
            return (0, "[]", "Next thread cursor: 123")
        issue_id = core[3]
        return self._json(copy.deepcopy(self.comments.get(issue_id, [])))

    def _timeline(self, core):
        if self.truncate_timeline:
            return (0, "[]", "warning: truncated history")
        issue_id = core[2]
        return self._json(copy.deepcopy(self.activities.get(issue_id, [])))

    def _runs(self, core):
        if self.truncate_runs:
            return (0, "[]", "run listing truncated")
        issue_id = core[2]
        return self._json(copy.deepcopy(self.runs.get(issue_id, [])))

    def _rerun(self, core):
        issue_id = core[2]
        self.run_counter += 1
        run = {
            "id": f"RUN-{self.run_counter}",
            "issue_id": issue_id,
            "agent_id": TARGET_AGENT,
            "status": "queued",
            "attempt": 1,
        }
        self.runs.setdefault(issue_id, []).append(run)
        return self._json(copy.deepcopy(run))


# ---------------------------------------------------------------------------
# real package construction (cached across tests)
# ---------------------------------------------------------------------------
_CACHE: dict = {}


def _request(task_ref, role, title, description, project="web-imagegen"):
    return {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": task_ref,
        "project": {"project_id": project},
        "target": {"role": role},
        "purpose": "implementation",
        "task_snapshot": {
            "title": title,
            "description": description,
            "requirements": ["keep the accepted bytes unchanged"],
            "acceptance_criteria": ["frozen package is READY"],
            "relevant_decisions": ["reuse frozen T00"],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }


def cached_context(key, request):
    if key not in _CACHE:
        with tempfile.TemporaryDirectory() as tmp:
            store = o2.DurableIntentStore(Path(tmp) / "ledger.jsonl")
            factory = u12.build_r0b_factory(
                store, runner=FakeCli(), require_findings_source=False)
            _CACHE[key] = factory.build_context_package(
                request, clock=lambda: CLOCK, findings=[])
    return copy.deepcopy(_CACHE[key])


def creation_package():
    return cached_context("C", _request(
        PARENT_REF, u12.CREATION_ROLE, "Dispatch the R0 canary target",
        "Create one unassigned backlog target under YZT-66."))


def target_execution_request(title, description, relevant_decisions=None):
    """The E request derived from the actual target snapshot (YZT-83)."""
    request = _request(f"multica://issue/{TARGET_IDENTIFIER}",
                       u12.EXECUTION_ROLE, title, description)
    if relevant_decisions is not None:
        request["task_snapshot"]["relevant_decisions"] = \
            [str(item) for item in relevant_decisions]
    return request


def snapshot_derived_request(cli, *, relevant_decisions=None):
    """Build the request exactly as the accepted fresh-snapshot derivation.

    Publication recovery re-derives the E request from the freshly read target
    body plus the accepted explicit decisions and compares it verbatim with
    the attached request, so fixture requests use the same derivation.
    """
    issue = cli.issues[TARGET_ID]
    base = target_execution_request(issue["title"], issue["description"] or "",
                                    relevant_decisions)
    return u12.reconstruct_request_from_fresh_snapshot(issue, base)


def execution_context_for(cli, *, relevant_decisions=None):
    request = snapshot_derived_request(
        cli, relevant_decisions=relevant_decisions)
    key = "E:" + hashlib.sha256(
        json.dumps(request, ensure_ascii=False,
                   sort_keys=True).encode("utf-8")).hexdigest()
    e = cached_context(key, request)
    return {"result": e["result"], "request": e["request"],
            "self_check": e["self_check"],
            "target_task_ref": f"multica://issue/{TARGET_IDENTIFIER}"}


def make_execution_context(harness, *, task_ref=None):
    context = harness.execution_context()
    if task_ref is not None:
        context["target_task_ref"] = task_ref
    return context


def default_artifact_dependency():
    if "artifact" not in _CACHE:
        _CACHE["artifact"] = u12.build_artifact_dependency_digest()
    return copy.deepcopy(_CACHE["artifact"])


def blocking_finding():
    """One task-associated material Finding the gate must escalate on."""
    return {
        "finding_id": "FIND-TEST-0001",
        "kind": "finding",
        "schema_version": "1.1",
        "status": "open",
        "task_id": TARGET_IDENTIFIER,
        "project_id": "web-imagegen",
        "intent": "context_challenge",
        "verification": "conflicted",
        "summary": "blocking conflict on the canary subject",
        "detail": "the accepted artifact set conflicts with a newer decision",
        "discovered_by": u12.EXECUTION_ROLE,
    }


def make_spec(*, artifact=None, intent_id=None):
    c = creation_package()
    marker = u12.new_marker("yzt-84-canary", nonce="f" * 32)
    intent_id = intent_id or o2.new_intent_id(
        source_task_id=SOURCE_RUN, logical_task_key="YZT-84-CANARY-1",
        target_agent_id=TARGET_AGENT, package_id=c["result"]["package_id"])
    body = (f"Canary task body.\n\nIntent marker: {marker}\n"
            f"Intent: {intent_id}\n")
    artifact = artifact or default_artifact_dependency()
    return {
        "title": "R0 canary target under YZT-66",
        "body": body,
        "parent_issue_id": PARENT_ID,
        "project_id": "web-imagegen",
        "priority": "urgent",
        "logical_task_key": "YZT-84-CANARY-1",
        "target_role": u12.EXECUTION_ROLE,
        "target_agent_id": TARGET_AGENT,
        "publisher_agent_id": PUBLISHER_AGENT,
        "status": u12.BACKLOG_STATUS,
        "marker": marker,
        "body_digest": u12.digest_text_lf(body),
        "creation_task_ref": PARENT_REF,
        "authority_refs": [
            {"ref": "multica://comment/01a08e93-0a1a-7074-9dda-912d1237bf56",
             "digest": "sha256:" + "a" * 64},
            {"ref": "attachment/01a08f10-fc8c-778c-a150-2ed54825515a",
             "digest": "sha256:" + "b" * 64},
        ],
        "artifact_dependency": artifact,
    }, intent_id


class CountingAuthorityReader:
    """Wraps the concrete authority reader and logs each fresh invocation."""

    def __init__(self, inner, log):
        self.inner = inner
        self.log = log

    def read(self, *, path=u12.AUTHORITY_ARTIFACT_PATH):
        self.log.append(path)
        return self.inner.read(path=path)


class FixedAuthorityReader:
    """Test double serving one explicit authority record (never a live source)."""

    def __init__(self, record=None, *, disposition="READY", sha256=None,
                 ref=None, path=None):
        self.record = record
        self.disposition = disposition
        self.sha256 = sha256
        self.ref = ref or u12.AUTHORITY_REF
        self.path = path or u12.AUTHORITY_ARTIFACT_PATH

    def read(self, *, path=u12.AUTHORITY_ARTIFACT_PATH):
        evidence = {
            "schema": u12.AUTHORITY_EVIDENCE_SCHEMA,
            "ref": self.ref,
            "path": self.path,
            "digest_method": u12.AUTHORITY_DIGEST_METHOD,
            "disposition": self.disposition,
        }
        if self.sha256 is not None:
            evidence["sha256"] = self.sha256
        if self.record is not None:
            evidence["record"] = self.record
            evidence.setdefault("sha256", u12.digest(self.record))
        return evidence


def real_readiness_manifest():
    path = TOOLS.parent / u12.AUTHORITY_ARTIFACT_PATH
    return json.loads(path.read_text(encoding="utf-8"))


class LifecycleHarness:
    def __init__(self, tmp: Path, *, bump=True, blob_reader=None,
                 authority_reader="default", findings_source=None,
                 require_findings_source=False,
                 findings_baseline_observation=None):
        self.cli = FakeCli(bump_revision_on_comment=bump)
        self.store = o2.DurableIntentStore(tmp / "ledger.jsonl")
        self.artifact_reads: list = []
        base_reader = (blob_reader if blob_reader is not None
                       else u12._git_blob_reader(u12.ROOT))

        def counting_reader(commit, path):
            self.artifact_reads.append((commit, path))
            return base_reader(commit, path)

        self.artifact_reader = base_reader
        self.authority_reads: list = []
        if authority_reader == "default":
            authority = u12.ReadinessManifestAuthorityReader()
        else:
            authority = authority_reader
        if authority is not None:
            authority = CountingAuthorityReader(authority,
                                                self.authority_reads)
        self.factory = u12.build_r0b_factory(
            self.store, runner=self.cli, artifact_blob_reader=counting_reader,
            authority_reader=authority, findings_source=findings_source,
            require_findings_source=require_findings_source,
            findings_baseline_observation=findings_baseline_observation)
        self.spec, self.intent_id = make_spec()

    def record(self):
        return self.factory.record_creation_intent(
            creation_context={"result": creation_package()["result"],
                              "request": creation_package()["request"],
                              "self_check": creation_package()["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=self.spec,
            authority="Human U12 approval + YZT-83 design",
            actor=DISPATCHER, source_run=SOURCE_RUN,
            intent_id=self.intent_id)

    def execution_context(self):
        return execution_context_for(self.cli)

    def to_prepared(self):
        self.record()
        self.factory.create_target_once(self.intent_id, actor=DISPATCHER)
        self.factory.assign_ownership_once(self.intent_id, actor=DISPATCHER)
        return self.factory.bind_execution_package(
            self.intent_id, execution_context=self.execution_context(),
            actor=DISPATCHER)

    def publish(self, **overrides):
        kwargs = {"actor": DISPATCHER, "publisher_run_id": PUBLISHER_RUN,
                  "prepared_by": "01 Engineering Lead",
                  "prepared_at": CLOCK}
        kwargs.update(overrides)
        return self.factory.publish_handoff_once(self.intent_id, **kwargs)

    def current_request(self):
        return snapshot_derived_request(self.cli)

    def arm(self, **overrides):
        kwargs = {"actor": DISPATCHER, "current_request": self.current_request(),
                  "current_findings": []}
        kwargs.update(overrides)
        return self.factory.arm(self.intent_id, **kwargs)

    def trigger(self, **overrides):
        kwargs = {"actor": DISPATCHER, "current_request": self.current_request(),
                  "current_findings": []}
        kwargs.update(overrides)
        return self.factory.trigger(self.intent_id, **kwargs)


# ---------------------------------------------------------------------------
# 1. happy path and routing
# ---------------------------------------------------------------------------
class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = LifecycleHarness(Path(self.tmp.name))

    def test_full_lifecycle_binds_observed_revision_and_one_rerun(self):
        self.h.record()
        created = self.h.factory.create_target_once(self.h.intent_id,
                                                    actor=DISPATCHER)
        self.assertEqual(created["issue_id"], TARGET_ID)
        self.h.factory.assign_ownership_once(self.h.intent_id,
                                             actor=DISPATCHER)
        bound = self.h.factory.bind_execution_package(
            self.h.intent_id, execution_context=make_execution_context(self.h),
            actor=DISPATCHER)
        self.assertEqual(bound["package_id"],
                         self.h.execution_context()["result"]["package_id"])
        published = self.h.publish()
        self.assertEqual(published["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(published["post_publication_revision"],
                         self.h.cli.issue_of(TARGET_ID)["revision"])
        artifact_before = len(self.h.artifact_reads)
        authority_before = len(self.h.authority_reads)
        comments_before = len(
            self.h.cli.commands_of(["issue", "comment", "list"]))
        armed = self.h.arm()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        self.assertEqual(armed["plan"]["selected_trigger"], o2.TRIGGER_RERUN)
        self.assertIsNone(armed["plan"]["ownership_binding"])
        self.assertGreater(len(self.h.artifact_reads), artifact_before)
        self.assertGreater(len(self.h.authority_reads), authority_before)
        self.assertGreater(
            len(self.h.cli.commands_of(["issue", "comment", "list"])),
            comments_before)
        artifact_mid = len(self.h.artifact_reads)
        authority_mid = len(self.h.authority_reads)
        triggered = self.h.trigger()
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED)
        self.assertGreater(len(self.h.artifact_reads), artifact_mid)
        self.assertGreater(len(self.h.authority_reads), authority_mid)
        self.h.factory.record_self_check(self.h.intent_id, status="CLEAR",
                                         actor=DISPATCHER)
        done = self.h.factory.complete(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(done["status"], o2.S_COMPLETED)

        self.assertEqual(len(self.h.cli.commands_of(["issue", "create"])), 1)
        creates = self.h.cli.commands_of(["issue", "create"])[0]
        self.assertIn("--status", creates)
        self.assertEqual(creates[creates.index("--status") + 1], "backlog")
        self.assertNotIn("--assignee", creates)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "assign"])), 1)
        self.assertIn("--no-start",
                      self.h.cli.commands_of(["issue", "assign"])[0])
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 1)
        self.assertEqual(len(self.h.cli.commands_of(["issue", "rerun"])), 1)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "status"])), 0)
        intent = self.h.store.get(self.h.intent_id)
        binding = intent["fields"][u12.R0B_FIELD]["publication_binding"]
        self.assertEqual(binding["revision_attribution"],
                         "single_delta_exclusion")
        self.assertEqual(
            intent["fields"][u12.R0B_FIELD]["creation_context"]["package_id"],
            creation_package()["result"]["package_id"])
        checkpoints = [e["data"]["checkpoint"] for e in intent["events"]
                       if e.get("name") == u12.E_PREFLIGHT]
        self.assertEqual(checkpoints, ["ARM", "TRIGGER"])

    def test_unchanged_revision_publication_is_accepted(self):
        self.h = LifecycleHarness(Path(self.tmp.name), bump=False)
        self.h.to_prepared()
        before = self.h.cli.issue_of(TARGET_ID)["revision"]
        published = self.h.publish()
        self.assertEqual(published["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(published["revision_attribution"], "unchanged")
        self.assertEqual(published["post_publication_revision"], before)
        self.assertEqual(self.h.arm()["status"], o2.S_TRIGGER_READY)
        self.assertEqual(self.h.trigger()["status"], o2.S_RUN_CORRELATED)

    def test_assignment_trigger_is_unreachable_from_the_factory(self):
        self.h.to_prepared()
        with self.assertRaises(o2.IntentError):
            self.h.factory.boundary.assign_trigger(TARGET_ID, TARGET_AGENT)

    def test_plain_o2_transitions_are_refused_for_tagged_intents(self):
        self.h.to_prepared()
        with self.assertRaises(u12.R0BDowngradeRefused):
            self.h.factory.mark_prepared(
                self.h.intent_id, package_id="CTX-x-0123456789abcdef",
                artifact_dependency_digest="sha256:" + "0" * 64,
                actor=DISPATCHER)
        with self.assertRaises(u12.R0BDowngradeRefused):
            self.h.factory.mark_published(
                self.h.intent_id, note_comment_id="CMT-1",
                receipt_digest=None, actor=DISPATCHER)

    def test_untagged_intent_is_refused_by_the_factory(self):
        record = self.h.store.record_intent({
            "intent_id": "DI-" + "1" * 16,
            "schema_version": o2.O2_SCHEMA,
            "source_task_id": "src",
            "logical_task_key": "other-key",
            "parent_issue_id": PARENT_ID,
            "target_role": u12.EXECUTION_ROLE,
            "target_agent_id": TARGET_AGENT,
            "package_id": "CTX-software-engineer-0123456789abcdef",
            "artifact_dependency_digest": "sha256:" + "0" * 64,
            "creation_authority": "test",
            "provenance": {"adapter": "plain-o2"},
            "issue_id": None,
        })
        with self.assertRaises(u12.R0BContractError):
            self.h.factory.validate(record["intent_id"])

    def test_adapter_digest_change_refuses_resume(self):
        self.h.to_prepared()
        with mock.patch.object(u12, "adapter_digest",
                               return_value="sha256:" + "f" * 64):
            with self.assertRaises(u12.R0BDowngradeRefused):
                self.h.factory.validate(self.h.intent_id)


# ---------------------------------------------------------------------------
# 2. rejection matrix: creation and execution context
# ---------------------------------------------------------------------------
class RejectionMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = LifecycleHarness(Path(self.tmp.name))

    def test_wrong_creation_role_is_refused_before_create(self):
        ctx = creation_package()
        bad = copy.deepcopy(ctx)
        bad["result"]["role"] = "qa"
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": bad["result"],
                                  "request": bad["request"],
                                  "self_check": bad["self_check"]},
                creation_spec=self.h.spec,
                authority="authority", actor=DISPATCHER)
        self.assertEqual(self.h.cli.commands, [])

    def test_partial_creation_package_is_refused(self):
        ctx = creation_package()
        bad = copy.deepcopy(ctx)
        bad["result"]["status"] = "PARTIAL"
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": bad["result"],
                                  "request": bad["request"],
                                  "self_check": bad["self_check"]},
                creation_spec=self.h.spec,
                authority="authority", actor=DISPATCHER)

    def test_failed_self_check_is_refused(self):
        ctx = creation_package()
        bad = copy.deepcopy(ctx)
        bad["self_check"]["status"] = "REFRESH_REQUIRED"
        bad["self_check"]["action"] = "REFRESH"
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": bad["result"],
                                  "request": bad["request"],
                                  "self_check": bad["self_check"]},
                creation_spec=self.h.spec,
                authority="authority", actor=DISPATCHER)

    def test_missing_authority_or_spec_pin_is_refused(self):
        spec = copy.deepcopy(self.h.spec)
        spec["authority_refs"] = []
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": creation_package()["result"],
                                  "request": creation_package()["request"],
                                  "self_check": creation_package()["self_check"]},
                creation_spec=spec, authority="authority", actor=DISPATCHER)
        spec = copy.deepcopy(self.h.spec)
        spec["body_digest"] = "sha256:" + "9" * 64
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": creation_package()["result"],
                                  "request": creation_package()["request"],
                                  "self_check": creation_package()["self_check"]},
                creation_spec=spec, authority="authority", actor=DISPATCHER)

    def test_tampered_artifact_dependency_digest_is_refused(self):
        spec = copy.deepcopy(self.h.spec)
        spec["artifact_dependency"]["digest"] = "sha256:" + "7" * 64
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": creation_package()["result"],
                                  "request": creation_package()["request"],
                                  "self_check": creation_package()["self_check"]},
                creation_spec=spec, authority="authority", actor=DISPATCHER)
        self.assertEqual(self.h.cli.commands, [])

    def test_foreign_execution_task_ref_is_refused_before_publication(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        self.h.factory.assign_ownership_once(self.h.intent_id,
                                             actor=DISPATCHER)
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.bind_execution_package(
                self.h.intent_id,
                execution_context=make_execution_context(
                    self.h, task_ref="multica://issue/YTST-99"),
                actor=DISPATCHER)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 0)

    def test_stale_creation_package_is_refused(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        self.h.factory.assign_ownership_once(self.h.intent_id,
                                             actor=DISPATCHER)
        # Target revision moves after ownership: E binding must refresh.
        self.h.cli.issues[TARGET_ID]["revision"] += 1
        result = self.h.factory.bind_execution_package(
            self.h.intent_id, execution_context=make_execution_context(self.h),
            actor=DISPATCHER)
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(self.h.store.get(self.h.intent_id)["state"],
                         o2.S_REFRESH_REQUIRED)


# ---------------------------------------------------------------------------
# 3. publication predicate rejections
# ---------------------------------------------------------------------------
class PublicationRejectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, *, bump=True):
        self.h = LifecycleHarness(Path(self.tmp.name), bump=bump)
        self.h.to_prepared()
        return self.h

    def test_description_change_around_publication_fails_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))

        def mutate(cli):
            cli.issues[TARGET_ID]["description"] = "tampered"

        self.h.cli.on_comment_add = mutate
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"],
                         u12.REASON_PUBLICATION_CONFLICT)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "rerun"])), 0)

    def test_assignee_and_status_change_fail_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))

        def mutate(cli):
            cli.issues[TARGET_ID]["assignee_id"] = "00000000-0000-0000-0000-000000000000"
            cli.add_activity(TARGET_ID, "assignee_changed")

        self.h.cli.on_comment_add = mutate
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_CONFLICT)

    def test_unrelated_activity_delta_fails_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))

        def mutate(cli):
            cli.add_activity(TARGET_ID, "description_updated")

        self.h.cli.on_comment_add = mutate
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_CONFLICT)

    def test_edited_note_fails_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))
        self.h.cli.edit_note = True
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)

    def test_duplicate_identical_note_fails_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))
        self.h.cli.duplicate_note = True
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)

    def test_missing_source_run_fails_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))
        self.h.cli.drop_source_task_id = True
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)

    def test_truncated_comment_evidence_is_incomplete(self):
        self.h = LifecycleHarness(Path(self.tmp.name))
        self.h.cli.truncate_comment_list = True
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 0)

    def test_truncated_timeline_evidence_is_incomplete(self):
        self.h = LifecycleHarness(Path(self.tmp.name))
        self.h.cli.truncate_timeline = True
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)

    def test_truncated_run_evidence_is_incomplete(self):
        self.h = LifecycleHarness(Path(self.tmp.name))
        self.h.cli.truncate_runs = True
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)

    def test_unexpected_run_around_publication_fails_closed(self):
        self.h = LifecycleHarness(Path(self.tmp.name))

        def mutate(cli):
            cli.runs[TARGET_ID].append({
                "id": "RUN-X", "issue_id": TARGET_ID,
                "agent_id": TARGET_AGENT, "status": "queued"})

        self.h.cli.on_comment_add = mutate
        self.h.to_prepared()
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_CONFLICT)


# ---------------------------------------------------------------------------
# 4. crashes, recovery and leases
# ---------------------------------------------------------------------------
class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = LifecycleHarness(Path(self.tmp.name))

    def test_lost_create_response_recovers_read_only(self):
        self.h.record()
        original = self.h.factory.boundary.create_backlog_issue

        def ambiguous(**kwargs):
            original(**kwargs)
            raise o2.ReceiptAmbiguousError("lost response")

        self.h.factory.boundary.create_backlog_issue = ambiguous
        result = self.h.factory.create_target_once(self.h.intent_id,
                                                   actor=DISPATCHER)
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        self.assertTrue(result["revision"])
        self.assertEqual(len(self.h.cli.commands_of(["issue", "create"])), 1)
        intent = self.h.store.get(self.h.intent_id)
        binding = intent["fields"][u12.R0B_FIELD]["target_binding"]
        self.assertTrue(binding["discovered_by_read_only_proof"])

    def test_ambiguous_create_without_discovery_stops_and_never_duplicates(self):
        self.h.record()
        self.h.factory.boundary.create_backlog_issue = mock.Mock(
            side_effect=o2.ReceiptAmbiguousError("lost response"))
        result = self.h.factory.create_target_once(self.h.intent_id,
                                                   actor=DISPATCHER)
        self.assertEqual(result["status"], o2.S_CREATE_AMBIGUOUS)
        retry = self.h.factory.create_target_once(self.h.intent_id,
                                                  actor=DISPATCHER)
        self.assertEqual(retry["status"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(self.h.factory.boundary.create_backlog_issue.call_count,
                         1)
        self.assertEqual(len(self.h.cli.commands_of(["issue", "create"])), 0)

    def test_uncertain_publication_then_read_only_confirmation(self):
        self.h.to_prepared()
        self.h.cli.fail_comment_add_after_store = True
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_HANDOFF_PREPARED)
        self.assertEqual(result["outcome"], "PUBLICATION_UNCERTAIN")
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_AMBIGUOUS)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 1)
        self.h.cli.fail_comment_add_after_store = False
        recovered = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(recovered["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 1)

    def test_attempt_without_visible_note_stays_blocked(self):
        self.h.to_prepared()
        self.h.cli.drop_comment_add = True
        result = self.h.publish()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PUBLICATION_PROVENANCE)
        again = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(again["status"], o2.S_BLOCKED)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 1)

    def test_competing_lease_refuses_before_any_external_call(self):
        self.h.record()
        self.h.store.claim(self.h.intent_id, "other-worker", ttl_seconds=300)
        with self.assertRaises(o2.LeaseHeldError):
            self.h.factory.create_target_once(self.h.intent_id,
                                              actor=DISPATCHER)
        self.assertEqual(self.h.cli.commands, [])

    def test_expired_lease_can_resume_without_duplicate_attempt(self):
        self.h.record()
        self.h.store.claim(self.h.intent_id, "dead-worker", now="2026-09-11T06:00:00Z",
                           ttl_seconds=1)
        self.h.factory.now = lambda: "2026-09-11T07:00:00Z"
        created = self.h.factory.create_target_once(self.h.intent_id,
                                                    actor=DISPATCHER)
        self.assertEqual(created["status"], o2.S_TARGET_BOUND)
        self.assertEqual(len(self.h.cli.commands_of(["issue", "create"])), 1)

    def test_ownership_attempt_without_proof_stops(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        self.h.factory.boundary.assign_ownership_no_start = mock.Mock(
            return_value={"outcome": "binding_issued"})
        result = self.h.factory.assign_ownership_once(self.h.intent_id,
                                                      actor=DISPATCHER)
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_OWNERSHIP_UNPROVEN)
        again = self.h.factory.assign_ownership_once(self.h.intent_id,
                                                     actor=DISPATCHER)
        self.assertEqual(again["status"], o2.S_BLOCKED)
        self.assertEqual(
            self.h.factory.boundary.assign_ownership_no_start.call_count, 1)

    def test_no_ownership_attempt_allows_one_authorized_call(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        planned = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(planned["action"], "RESUME_OWNERSHIP")
        self.assertFalse(planned["performed"])
        done = self.h.factory.assign_ownership_once(self.h.intent_id,
                                                    actor=DISPATCHER)
        self.assertTrue(done["ownership_bound"])

    def test_recover_never_republishes_after_attempt(self):
        self.h.to_prepared()
        self.h.cli.drop_comment_add = True
        self.h.publish()
        intent = self.h.store.get(self.h.intent_id)
        attempts = [e for e in intent["events"]
                    if e.get("name") == u12.E_PUBLICATION_ISSUING]
        self.assertEqual(len(attempts), 1)
        again = self.h.factory.publish_handoff_once(
            self.h.intent_id, actor=DISPATCHER,
            publisher_run_id=PUBLISHER_RUN,
            prepared_by="01 Engineering Lead", prepared_at=CLOCK)
        self.assertEqual(again["status"], o2.S_BLOCKED)
        self.assertEqual(again["reason"], u12.REASON_PUBLICATION_AMBIGUOUS)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 1)


# ---------------------------------------------------------------------------
# 5. predicate unit matrix (pure)
# ---------------------------------------------------------------------------
def _evidence(issue_revision=2, comments=None, activities=None, runs=None):
    issue = {"id": TARGET_ID, "revision": issue_revision, "title": "t",
             "description": "d", "status": "backlog",
             "status_category": "backlog", "updated_at": "u",
             "last_activity_at": "u"}
    return {"issue": issue, "comments": comments or [],
            "activities": activities or [], "runs": runs or []}


class PredicateMatrixTests(unittest.TestCase):
    def expected(self, body_digest="sha256:" + "1" * 64):
        return {
            "body_digest": body_digest,
            "envelope_digest": "sha256:" + "2" * 64,
            "envelope_canonical": "{}",
            "package_id": "CTX-software-engineer-0123456789abcdef",
            "task_ref": f"multica://issue/{TARGET_IDENTIFIER}",
            "role": u12.EXECUTION_ROLE,
            "artifact_dependency_digest": "sha256:" + "3" * 64,
            "author_id": PUBLISHER_AGENT,
            "author_type": "agent",
            "source_task_id": PUBLISHER_RUN,
            "parent_id": None,
            "note_comment_id": "CMT-1",
        }

    def note_comment(self, body_digest):
        return {"id": "CMT-1", "revision": 1, "created_at": "c",
                "updated_at": "c", "parent_id": None,
                "author_id": PUBLISHER_AGENT, "author_type": "agent",
                "source_task_id": PUBLISHER_RUN, "resolved_at": None,
                "content_digest": body_digest}

    def evaluate(self, *, before, after, expected, recheck=None,
                 artifact_ok=True, fingerprint_ok=True, self_check_ok=True,
                 attempt_count=1):
        return u12.evaluate_publication_predicate(
            attempt_count=attempt_count, attempt={}, before=before,
            after=after, recheck=recheck or after["issue"], expected=expected,
            artifact_ok=artifact_ok, fingerprint_ok=fingerprint_ok,
            self_check_ok=self_check_ok)

    def test_missing_note_is_not_a_pass(self):
        exp = self.expected()
        verdict = self.evaluate(before=_evidence(), after=_evidence(2),
                                expected=exp)
        self.assertFalse(verdict["ok"])
        self.assertEqual(verdict["code"], u12.PUB_NOTE_NOT_FOUND)

    def test_multiple_attempts_are_incomplete(self):
        exp = self.expected()
        verdict = self.evaluate(before=_evidence(), after=_evidence(2),
                                expected=exp, attempt_count=2)
        self.assertEqual(verdict["code"], u12.PUB_INCOMPLETE)

    def test_artifact_or_fingerprint_failure_is_incomplete(self):
        exp = self.expected()
        verdict = self.evaluate(before=_evidence(), after=_evidence(2),
                                expected=exp, artifact_ok=False)
        self.assertEqual(verdict["code"], u12.PUB_INCOMPLETE)

    def test_moving_revision_during_collection_is_refused(self):
        exp = self.expected()
        after = _evidence(2, comments=[self.note_comment(exp["body_digest"])])
        recheck = {"id": TARGET_ID, "revision": 3, "title": "t",
                   "description": "d", "status": "backlog",
                   "status_category": "backlog", "updated_at": "u",
                   "last_activity_at": "u"}
        verdict = self.evaluate(before=_evidence(), after=after,
                                expected=exp, recheck=recheck)
        self.assertEqual(verdict["code"], u12.PUB_MOVING_REVISION)

    def test_unknown_changed_field_is_refused(self):
        exp = self.expected()
        after = _evidence(2, comments=[self.note_comment(exp["body_digest"])])
        after["issue"]["title"] = "changed"
        verdict = self.evaluate(before=_evidence(), after=after, expected=exp)
        self.assertEqual(verdict["code"], u12.PUB_UNAUTHORIZED_DELTA)

    def test_run_evidence_is_refused(self):
        exp = self.expected()
        after = _evidence(2, comments=[self.note_comment(exp["body_digest"])],
                          runs=[{"id": "RUN-1"}])
        verdict = self.evaluate(before=_evidence(), after=after, expected=exp)
        self.assertEqual(verdict["code"], u12.PUB_UNEXPECTED_RUN)


# ---------------------------------------------------------------------------
# 6. recovery matrix, two workers, strict trigger boundary
# ---------------------------------------------------------------------------
class RecoveryMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = LifecycleHarness(Path(self.tmp.name))

    def test_classification_at_every_boundary(self):
        self.h.record()
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "RESUME_CREATE")
        self.assertFalse(step["performed"])
        self.assertEqual(step["status"], o2.S_INTENT_RECORDED)
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "RESUME_OWNERSHIP")
        self.h.factory.assign_ownership_once(self.h.intent_id,
                                             actor=DISPATCHER)
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "RESUME_PREPARE")
        self.h.factory.bind_execution_package(
            self.h.intent_id, execution_context=make_execution_context(self.h),
            actor=DISPATCHER)
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "RESUME_PUBLISH")
        self.h.publish()
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "RESUME_ARM_AND_TRIGGER")
        self.assertFalse(step["performed"])
        self.h.arm()
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "RESUME_TRIGGER")
        self.assertFalse(step["performed"])
        self.h.trigger()
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "NONE")

    def test_trigger_issuing_without_visible_run_is_read_only(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        self.h.factory.assign_ownership_once(self.h.intent_id,
                                             actor=DISPATCHER)
        self.h.factory.bind_execution_package(
            self.h.intent_id, execution_context=make_execution_context(self.h),
            actor=DISPATCHER)
        self.h.publish()
        self.h.arm()
        self.h.cli._rerun = lambda core: (
            0, json.dumps({"id": "RUN-HIDDEN", "issue_id": core[2],
                           "agent_id": TARGET_AGENT, "status": "queued"}), "")
        issued = self.h.trigger()
        self.assertEqual(issued["status"], o2.S_TRIGGER_ISSUING)
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["action"], "READ_ONLY_RECONCILE")
        self.assertFalse(step["performed"])

    def test_refresh_state_requires_lead_disposition(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        self.h.factory.assign_ownership_once(self.h.intent_id,
                                             actor=DISPATCHER)
        self.h.cli.issues[TARGET_ID]["revision"] += 1
        self.h.factory.bind_execution_package(
            self.h.intent_id, execution_context=make_execution_context(self.h),
            actor=DISPATCHER)
        step = self.h.factory.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(step["classification"], "REFRESH_REQUIRED_STOP")
        self.assertEqual(step["action"], "LEAD_DISPOSITION")

    def test_two_workers_never_duplicate_the_create(self):
        self.h.record()
        self.h.factory.create_target_once(self.h.intent_id, actor=DISPATCHER)
        other = u12.build_r0b_factory(
            o2.DurableIntentStore(Path(self.tmp.name) / "ledger.jsonl"),
            runner=self.h.cli,
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            require_findings_source=False)
        replay = other.create_target_once(self.h.intent_id, actor="worker-b")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["issue_id"], TARGET_ID)
        self.assertEqual(len(self.h.cli.commands_of(["issue", "create"])), 1)

    def test_malformed_rerun_receipt_is_trigger_ambiguous_and_never_retried(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.arm()
        # Unauthorized {"run": {...}} wrapper: the strict gate must refuse it.
        original = self.h.cli._rerun
        self.h.cli._rerun = lambda core: (
            0, json.dumps({"run": {"id": "RUN-1", "issue_id": core[2],
                                   "agent_id": TARGET_AGENT,
                                   "status": "queued"}}), "")
        result = self.h.trigger()
        self.assertEqual(result["status"], o2.S_TRIGGER_AMBIGUOUS)
        retry = self.h.trigger()
        self.assertEqual(retry["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertTrue(retry["replayed"])
        self.assertEqual(len(self.h.cli.commands_of(["issue", "rerun"])), 1)
        self.h.cli._rerun = original

    def test_caller_supplied_snapshots_cannot_bypass_preflight(self):
        self.h.to_prepared()
        self.h.publish()
        data = self.h.store.get(self.h.intent_id)["fields"][u12.R0B_FIELD]
        issue = self.h.cli.issue_of(TARGET_ID)
        snapshot = self.h.factory._build_snapshot(data, issue=issue, runs=[])
        snapshot["artifact_ready"] = True
        snapshot["ready_note_id"] = "CMT-FABRICATED"
        with self.assertRaises(u12.R0BDowngradeRefused):
            self.h.factory.plan_and_arm(self.h.intent_id, snapshot,
                                        actor=DISPATCHER)
        with self.assertRaises(u12.R0BDowngradeRefused):
            self.h.factory.issue_trigger(self.h.intent_id, snapshot,
                                         actor=DISPATCHER)
        with self.assertRaises(u12.R0BDowngradeRefused):
            self.h.factory.resume(self.h.intent_id, {"snapshot": snapshot},
                                  actor=DISPATCHER)
        self.assertEqual(len(self.h.cli.commands_of(["issue", "rerun"])), 0)
        self.assertEqual(self.h.store.get(self.h.intent_id)["state"],
                         o2.S_HANDOFF_PUBLISHED)


# ---------------------------------------------------------------------------
# 6b. fresh material preflight (YZT-83 forward repair acceptance matrix)
# ---------------------------------------------------------------------------
class PreflightRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.h = LifecycleHarness(Path(self.tmp.name))

    def reruns(self):
        return len(self.h.cli.commands_of(["issue", "rerun"]))

    def test_authority_path_must_be_bound_in_creation_spec(self):
        entries = [dict(e) for e in u12.DEFAULT_ARTIFACT_ENTRIES
                   if e["path"] != u12.AUTHORITY_ARTIFACT_PATH]
        artifact = u12.build_artifact_dependency_digest(entries)
        spec, intent_id = make_spec(artifact=artifact)
        with self.assertRaises(u12.R0BValidationRefused):
            self.h.factory.record_creation_intent(
                creation_context={"result": creation_package()["result"],
                                  "request": creation_package()["request"],
                                  "self_check": creation_package()["self_check"]},
                creation_spec=spec, authority="authority", actor=DISPATCHER,
                intent_id=intent_id)

    def test_changed_artifact_bytes_after_arm_zero_reruns(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.arm()
        reads = []
        self.h.factory.artifact_blob_reader = (
            lambda commit, path: reads.append((commit, path)) or b"changed")
        result = self.h.trigger()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertTrue(reads)
        self.assertEqual(self.reruns(), 0)

    def test_changed_artifact_bytes_before_arm_zero_reruns(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.factory.artifact_blob_reader = lambda c, p: b"changed"
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_artifact_reader_error_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()

        def broken(commit, path):
            raise OSError("blob store unavailable")

        self.h.factory.artifact_blob_reader = broken
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(self.reruns(), 0)

    def test_missing_artifact_root_and_reader_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.factory.artifact_blob_reader = None
        self.h.factory.artifact_root = None
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(self.reruns(), 0)

    def test_reconstructed_factory_resume_recollects_and_fails_closed(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.arm()
        other = u12.build_r0b_factory(
            o2.DurableIntentStore(Path(self.tmp.name) / "ledger.jsonl"),
            runner=self.h.cli,
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            authority_reader=u12.ReadinessManifestAuthorityReader(),
            require_findings_source=False)
        before = self.reruns()
        recovered = other.recover(self.h.intent_id, actor=DISPATCHER)
        self.assertEqual(recovered["action"], "RESUME_TRIGGER")
        self.assertFalse(recovered["performed"])
        other.artifact_blob_reader = lambda c, p: b"changed after restart"
        result = other.trigger(self.h.intent_id, actor=DISPATCHER,
                               current_request=self.h.current_request(),
                               current_findings=[])
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(self.reruns(), before)
        self.assertEqual(
            len(self.h.cli.commands_of(["issue", "comment", "add"])), 1)

    def test_note_body_edit_with_held_revision_is_refused(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.cli.comments[TARGET_ID][0]["content"] = "tampered note body"
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_missing_note_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.cli.comments[TARGET_ID] = []
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(self.reruns(), 0)

    def test_duplicate_note_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()
        duplicate = copy.deepcopy(self.h.cli.comments[TARGET_ID][0])
        duplicate["id"] = "CMT-DUPLICATE"
        self.h.cli.comments[TARGET_ID].append(duplicate)
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(self.reruns(), 0)

    def test_issue_description_change_with_held_revision_is_refused(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.cli.issues[TARGET_ID]["description"] = "drifted, revision held"
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_issue_projection_change_against_bound_request_is_refused(self):
        self.h.to_prepared()
        self.h.publish()
        bound = copy.deepcopy(
            self.h.store.get(self.h.intent_id)["fields"][u12.R0B_FIELD]
            ["execution_context"]["request"])
        self.h.cli.issues[TARGET_ID]["description"] = "drifted, revision held"
        result = self.h.arm(current_request=bound)
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_project_and_decision_selection_drift_are_refused(self):
        self.h.to_prepared()
        self.h.publish()
        moved_project = self.h.current_request()
        moved_project["project"] = {"project_id": "other-project"}
        result = self.h.arm(current_request=moved_project)
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        moved_decisions = self.h.current_request()
        moved_decisions["task_snapshot"]["relevant_decisions"] = ["new decision"]
        result = self.h.arm(current_request=moved_decisions)
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(self.reruns(), 0)

    def test_incidental_revision_only_keeps_issue_revision_drift(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.cli.issues[TARGET_ID]["revision"] += 1
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], o2.R_REVISION_DRIFT)
        self.assertEqual(self.reruns(), 0)

    def test_role_profile_revision_drift_is_refused(self):
        self.h.to_prepared()
        self.h.publish()
        with mock.patch.object(u12.chandoff, "role_profile_revision",
                               return_value="sha256:" + "8" * 64):
            result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_memory_and_registry_revision_drift_are_refused(self):
        self.h.to_prepared()
        self.h.publish()
        with mock.patch.object(u12.chandoff, "memory_revision",
                               return_value="sha256:" + "6" * 64), \
                mock.patch.object(u12.chandoff, "registry_revision",
                                  return_value="sha256:" + "7" * 64):
            result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_blocking_finding_is_refused_without_empty_findings_bypass(self):
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm(current_findings=[blocking_finding()])
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertIn("BLOCKED", result["detail"])
        self.assertEqual(self.reruns(), 0)

    def test_missing_finding_source_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm(current_findings=None)
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PREFLIGHT_INPUT_MISSING)
        self.assertEqual(self.reruns(), 0)

    def test_missing_current_request_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm(current_request=None)
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PREFLIGHT_INPUT_MISSING)
        self.assertEqual(self.reruns(), 0)

    def test_issue_moves_during_collection_is_blocked(self):
        self.h.to_prepared()
        self.h.publish()
        original = self.h.factory.reader.comments_full

        def moving(issue_id):
            rows = original(issue_id)
            self.h.cli.issues[TARGET_ID]["revision"] += 1
            return rows

        self.h.factory.reader.comments_full = moving
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(self.reruns(), 0)

    def test_superseded_authority_record_is_refused(self):
        altered = real_readiness_manifest()
        altered["generated_at"] = "2026-09-12T00:00:00Z"
        self.h = LifecycleHarness(
            Path(self.tmp.name),
            authority_reader=FixedAuthorityReader(altered))
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_revoked_authority_disposition_is_refused(self):
        self.h = LifecycleHarness(
            Path(self.tmp.name),
            authority_reader=FixedAuthorityReader(
                disposition="SUPERSEDED"))
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_authority_reader_failure_is_blocked(self):
        class BrokenAuthority:
            def read(self, *, path=u12.AUTHORITY_ARTIFACT_PATH):
                raise OSError("authority store unavailable")

        self.h = LifecycleHarness(Path(self.tmp.name),
                                  authority_reader=BrokenAuthority())
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_UNAVAILABLE)
        self.assertEqual(self.reruns(), 0)

    def test_no_authority_source_is_blocked(self):
        self.h = LifecycleHarness(Path(self.tmp.name), authority_reader=None)
        self.h.to_prepared()
        self.h.publish()
        result = self.h.arm()
        self.assertEqual(result["status"], o2.S_BLOCKED)
        self.assertEqual(result["reason"], u12.REASON_PREFLIGHT_INPUT_MISSING)
        self.assertEqual(self.reruns(), 0)

    def test_fabricated_authority_evidence_cannot_bypass(self):
        self.h = LifecycleHarness(Path(self.tmp.name), authority_reader=None)
        self.h.to_prepared()
        self.h.publish()
        fabricated = {
            "schema": u12.AUTHORITY_EVIDENCE_SCHEMA,
            "ref": u12.AUTHORITY_REF,
            "path": u12.AUTHORITY_ARTIFACT_PATH,
            "digest_method": u12.AUTHORITY_DIGEST_METHOD,
            "sha256": "sha256:" + "0" * 64,
            "disposition": "READY",
        }
        result = self.h.arm(authority_evidence=fabricated)
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_unexpected_run_is_refused(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.arm()
        self.h.cli.runs[TARGET_ID].append(
            {"id": "RUN-UNEXPECTED", "issue_id": TARGET_ID,
             "agent_id": TARGET_AGENT, "status": "running"})
        result = self.h.trigger()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(result["reason"], u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.reruns(), 0)

    def test_issued_states_never_recheck_or_retrigger(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.arm()
        self.assertEqual(self.h.trigger()["status"], o2.S_RUN_CORRELATED)
        commands_before = len(self.h.cli.commands)
        self.h.factory.artifact_blob_reader = lambda c, p: b"changed"
        replay = self.h.trigger()
        self.assertEqual(replay["status"], o2.S_RUN_CORRELATED)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.h.cli.commands), commands_before)
        self.assertEqual(self.reruns(), 1)

    def test_trigger_issuing_reentry_is_read_only(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.arm()
        self.h.cli._rerun = lambda core: (
            0, json.dumps({"id": "RUN-HIDDEN", "issue_id": core[2],
                           "agent_id": TARGET_AGENT, "status": "queued"}), "")
        self.assertEqual(self.h.trigger()["status"], o2.S_TRIGGER_ISSUING)
        commands_before = len(self.h.cli.commands)
        replay = self.h.trigger()
        self.assertEqual(replay["reason"], u12.REASON_TRIGGER_ALREADY_ISSUED)
        self.assertEqual(replay["outcome"], "READ_ONLY_RECONCILE_REQUIRED")
        self.assertEqual(len(self.h.cli.commands), commands_before)
        self.assertEqual(self.reruns(), 1)

    def test_repeated_stop_calls_stay_zero_call(self):
        self.h.to_prepared()
        self.h.publish()
        self.h.factory.artifact_blob_reader = lambda c, p: b"changed"
        first = self.h.arm()
        self.assertEqual(first["status"], o2.S_REFRESH_REQUIRED)
        commands_before = len(self.h.cli.commands)
        again = self.h.arm()
        self.assertEqual(again["status"], o2.S_REFRESH_REQUIRED)
        self.assertTrue(again["replayed"])
        self.assertEqual(len(self.h.cli.commands), commands_before)
        self.assertEqual(self.reruns(), 0)


# ---------------------------------------------------------------------------
# 7. immutable predecessor bytes and production-ledger non-write
# ---------------------------------------------------------------------------
class ImmutabilityTests(unittest.TestCase):
    STRICT_GATE_LF = ("sha256:f37ed0912ecbdc3944529cffc6d7a28bc9a41d1faba8cd1e2"
                      "84eab6b0ccf4bed")
    CHANDOFF_INTENT_LF = ("sha256:0544046fa2ca97c12e6c48de574074df9de5715a950a"
                          "d75a783cf31853037032")
    READINESS_MANIFEST = ("sha256:64a5c449a9639979cb6a87627c583683cb99fb0f23"
                          "7ceff6634fc4b5c7e3bc93")
    BASE_COMMIT = "fcf63534e74b70220421c7b83c5447242d9a124b"

    def test_accepted_predecessor_pins_reproduce(self):
        root = TOOLS.parent
        self.assertEqual(u12.adapter_digest(TOOLS / "u12_strict_receipt.py"),
                         self.STRICT_GATE_LF)
        # The accepted artifact-dependency pin for tools/chandoff_intent.py is
        # bound to the pinned commit blob, not to the executing working file:
        # the approved YZT-84 publication-recovery exception changes those
        # executing bytes and binds them through the committed execution
        # migration (see test_u12_r0_publication_recovery.py). The frozen
        # artifact pin must still reproduce exactly.
        blob = u12._git_blob_reader(root)(
            "49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9",
            "tools/chandoff_intent.py")
        self.assertEqual(
            "sha256:" + hashlib.sha256(
                bytes(blob).replace(b"\r\n", b"\n")).hexdigest(),
            self.CHANDOFF_INTENT_LF)
        manifest = json.loads(
            (root / "adapters/multica/u12-p0r/readiness-manifest.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_digest"],
                         self.READINESS_MANIFEST)
        self.assertEqual(
            u12.digest({k: v for k, v in manifest.items()
                        if k != "manifest_digest"}),
            self.READINESS_MANIFEST)

    def test_artifact_dependency_digest_matches_accepted_pins(self):
        rebuilt = u12.build_artifact_dependency_digest()
        entries = rebuilt["entries"]
        self.assertEqual(entries["tools/u12_strict_receipt.py"]["sha256"],
                         self.STRICT_GATE_LF)
        self.assertEqual(entries["tools/chandoff_intent.py"]["sha256"],
                         self.CHANDOFF_INTENT_LF)
        self.assertEqual(
            entries["adapters/multica/u12-p0r/proposed-r0-canary-plan.json"]
            ["sha256"],
            "sha256:d31ddd8e0613152e420be8602b13a7fc1279f27dd32e084560d14e747"
            "506c5c2")
        self.assertEqual(
            entries["adapters/multica/joint-replay/final-gate-matrix.json"]
            ["sha256"],
            "sha256:a9ecc24c0ee1ff8965b534ba3b317b5e1c31e541458bb9fb06d88d59"
            "eefe946d")
        self.assertRegex(rebuilt["digest"], r"^sha256:[0-9a-f]{64}$")

    def test_strict_gate_wiring_and_version_unchanged(self):
        descriptor = u12.strict.gate_descriptor()
        self.assertEqual(descriptor["version"], "U12-P0R/1.2")
        self.assertEqual(descriptor["sha256_lf"], self.STRICT_GATE_LF)
        self.assertEqual(u12.strict.GATE_VERSION, "U12-P0R/1.2")
        self.assertTrue(u12.strict.wiring_proof()["ok"])
        self.assertEqual(u12.strict.AUTHORIZED_SHAPES,
                         ("run_object", "run_list", "runs_wrapper"))
        run = {"id": "r", "issue_id": "i", "agent_id": "a", "status": "s"}
        self.assertTrue(u12.strict.classify_strict_receipt(
            json.dumps(run))["accepted"])
        self.assertTrue(u12.strict.classify_strict_receipt(
            json.dumps([run]))["accepted"])
        self.assertTrue(u12.strict.classify_strict_receipt(
            json.dumps({"runs": [run]}))["accepted"])
        wrapper = u12.strict.classify_strict_receipt(
            json.dumps({"run": run}))
        self.assertFalse(wrapper["accepted"])
        duplicate = u12.strict.classify_strict_receipt(
            '{"id": "a", "id": "b", %s}' % json.dumps(run)[1:-1])
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["reason"], "duplicate_json_key")
        non_json = u12.strict.classify_strict_receipt(
            json.dumps(run)[:-1] + ', "extra": NaN}')
        self.assertFalse(non_json["accepted"])
        self.assertEqual(non_json["reason"], "non_json_constant")

    def test_imported_implementations_are_byte_unchanged_vs_base_commit(self):
        paths = [
            "tools/chandoff_dispatch.py", "tools/chandoff_adapter.py",
            "tools/chandoff.py", "tools/u12_strict_receipt.py",
            "tools/chandoff_compose.py",
            "tools/chandoff_finalize.py",
            "schemas", "adapters/multica/u12-p0r",
            "adapters/multica/u12-p0",
        ]
        root = str(TOOLS.parent)
        try:
            proc = subprocess.run(
                ["git", "-C", root, "diff", "--name-only", self.BASE_COMMIT,
                 "--"] + paths, capture_output=True, text=True)
        except OSError:
            self.skipTest("git unavailable")
        if proc.returncode != 0:
            self.skipTest("base commit unavailable in this checkout")
        self.assertEqual(proc.stdout.strip(), "")
        # The approved YZT-84 publication-recovery exception changes exactly
        # the two dependency files it opens (the O2 store/fold increment and
        # the note publisher's exact-transport send). The approved YZT-88
        # findings-source-binding exception adds the strict reader and opens
        # the T01 PLAN / SELF_CHECK orchestration seams; nothing else.
        proc = subprocess.run(
            ["git", "-C", root, "diff", "--name-only", self.BASE_COMMIT,
             "--", "tools"], capture_output=True, text=True)
        changed_tools = set(
            line for line in proc.stdout.splitlines() if line.strip())
        self.assertTrue(
            changed_tools <= {
                "tools/chandoff_intent.py", "tools/chandoff_note.py",
                "tools/u12_r0_binding.py", "tools/u12_preflight.py",
                "tools/chandoff_plan.py", "tools/chandoff_selfcheck.py",
                "tools/chandoff_findings_source.py",
                "tools/chandoff_assignment.py", "tools/chandoff_mention.py",
                "tools/context_cli.py",
                "tools/tests/test_u12_r0_binding.py",
                "tools/tests/test_u12_r0_create_recovery.py",
                "tools/tests/test_u12_r0_recovery_evidence.py",
                "tools/tests/test_u12_r0_publication_recovery.py",
                "tools/tests/test_handoff_skill.py",
                "tools/tests/test_handoff_artifact_readiness.py",
                "tools/tests/test_handoff_findings_source.py",
                "tools/tests/test_handoff_finding.py",
                "tools/tests/findings_fixture.py",
                "tools/tests/test_handoff_assignment.py",
                "tools/tests/test_handoff_mention.py",
                "tools/tests/test_handoff_fallback.py"},
            f"unexpected changed tools files: {sorted(changed_tools)}")

    def test_adapter_has_no_production_ledger_reference(self):
        source = (TOOLS / "u12_r0_binding.py").read_text(encoding="utf-8")
        self.assertNotIn("multica-state", source)
        self.assertNotIn(r"ledger.jsonl", source.replace(
            "unused-r0b-probe-ledger.jsonl", ""))


class FindingsSourceBindingR0Tests(unittest.TestCase):
    """YZT-88 R0 integration: bound source, conflict refusal, ARM->TRIGGER drift.

    Uses a simulation-only in-memory source; no live root and no production
    ledger is touched. The production CLI builds a BoundFindingsSource from
    the verified binding instead.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = u12.cfs.SyntheticFindingsSource([])
        self.h = LifecycleHarness(Path(self.tmp.name),
                                  findings_source=self.source,
                                  require_findings_source=True)

    def _arm_bound(self, **overrides):
        kwargs = {"actor": DISPATCHER,
                  "current_request": self.h.current_request()}
        kwargs.update(overrides)
        return self.h.factory.arm(self.h.intent_id, **kwargs)

    def _trigger_bound(self, **overrides):
        kwargs = {"actor": DISPATCHER,
                  "current_request": self.h.current_request()}
        kwargs.update(overrides)
        return self.h.factory.trigger(self.h.intent_id, **kwargs)

    def test_bound_source_arms_and_triggers_once(self):
        self.h.to_prepared()
        self.assertEqual(self.h.publish()["status"], o2.S_HANDOFF_PUBLISHED)
        armed = self._arm_bound()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY, armed)
        events = [e["data"] for e in self.h.store.get(
            self.h.intent_id)["events"] if e.get("name") == u12.E_PREFLIGHT]
        self.assertEqual(events[0]["checkpoint"], "ARM")
        self.assertTrue(events[0]["findings_observation_digest"])
        triggered = self._trigger_bound()
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED, triggered)
        self.assertEqual(len(self.h.cli.commands_of(["issue", "rerun"])), 1)

    def test_naked_findings_list_refused_when_source_configured(self):
        self.h.to_prepared()
        self.assertEqual(self.h.publish()["status"], o2.S_HANDOFF_PUBLISHED)
        result = self.h.factory.arm(
            self.h.intent_id, actor=DISPATCHER,
            current_request=self.h.current_request(), current_findings=[])
        self.assertEqual(result["status"], o2.S_BLOCKED, result)
        self.assertEqual(self.h.cli.commands_of(["issue", "rerun"]), [])

    def test_source_drift_after_arm_blocks_trigger(self):
        self.h.to_prepared()
        self.assertEqual(self.h.publish()["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(self._arm_bound()["status"], o2.S_TRIGGER_READY)
        self.source.records.append({
            "finding_id": "FIND-TEST-DRIFT-000001", "status": "open",
            "task_id": TARGET_IDENTIFIER, "project_id": "web-imagegen",
            "intent": "observation", "verification": "verified",
            "discovered_by": u12.EXECUTION_ROLE})
        result = self._trigger_bound()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED, result)
        self.assertEqual(result.get("reason"), u12.REASON_MATERIAL_STALE)
        self.assertEqual(self.h.cli.commands_of(["issue", "rerun"]), [])

    def test_required_source_without_one_fails_publication_predicate(self):
        nested = Path(self.tmp.name) / "nosrc"
        nested.mkdir()
        h = LifecycleHarness(nested, require_findings_source=True)
        h.to_prepared()
        published = h.publish()
        self.assertEqual(published["status"], o2.S_BLOCKED, published)
        self.assertEqual(h.cli.commands_of(["issue", "comment", "add"]), [])
        self.assertEqual(h.cli.commands_of(["issue", "rerun"]), [])

    def test_trigger_invokes_verify_worker_entry(self):
        self.h.to_prepared()
        self.assertEqual(self.h.publish()["status"], o2.S_HANDOFF_PUBLISHED)
        armed = self._arm_bound()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY, armed)
        triggered = self._trigger_bound()
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED, triggered)
        entry = self.h.factory._last_worker_entry
        self.assertIsInstance(entry, dict)
        self.assertEqual(entry["observation"]["boundary"], "WORKER_START")
        self.assertEqual(len(self.h.cli.commands_of(["issue", "rerun"])), 1)

    def test_inferred_require_on_non_simulation_runner(self):
        class LiveRunner:
            def __init__(self):
                self.commands = []

            def __call__(self, argv):
                self.commands.append(list(argv))
                return 1, "", "live runner must not be used"

        runner = LiveRunner()
        factory = u12.build_r0b_factory(
            o2.DurableIntentStore(Path(self.tmp.name) / "live-ledger.jsonl"),
            runner=runner)
        self.assertTrue(factory.require_findings_source)
        with self.assertRaises(u12.cfs.FindingsSourceRefusal) as ctx:
            factory._gate_findings_effects("R0 trigger")
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertEqual(runner.commands, [])


class ProductionLedgerNonWriteTests(unittest.TestCase):
    PRODUCTION = Path(r"D:\AI\multica-state\web-imagegen\dispatch\ledger.jsonl")
    LIVE_INTENT = "DI-e5e2b856f6b7cd36"

    @classmethod
    def setUpClass(cls):
        try:
            cls.before = cls.PRODUCTION.read_bytes()
        except OSError:
            cls.before = None

    @classmethod
    def tearDownClass(cls):
        if cls.before is None:
            return
        try:
            after = cls.PRODUCTION.read_bytes()
        except OSError:
            raise AssertionError("production ledger became unreadable")
        if after != cls.before:
            raise AssertionError(
                "production ledger changed during the isolated test run")

    def test_isolated_tests_do_not_touch_the_production_ledger(self):
        """The isolated run must never write the production ledger.

        The former whole-file digest pin was invalidated by the authorized
        live R0 create (the ledger now carries the real DI-e5e2b856f6b7cd36
        intent) and would be invalidated again by the Lead's later live
        recovery, so the stable invariants are checked instead: the file is
        intact append-only JSONL, it folds without corruption, the one live
        intent still carries exactly one durable create attempt, and the
        bytes are unchanged across the whole test run.
        """
        if self.before is None:
            self.skipTest("production ledger not readable in this runtime")
        import hashlib
        self.assertRegex(
            "sha256:" + hashlib.sha256(self.before).hexdigest(),
            r"^sha256:[0-9a-f]{64}$")
        records = []
        for line in self.before.decode("utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            self.assertIsInstance(record, dict)
            records.append(record)
        folded = o2.fold_records(records)
        live = folded["intents"].get(self.LIVE_INTENT)
        self.assertIsNotNone(
            live, "the single live R0 intent must remain in the ledger")
        attempts = [event for event in live["events"]
                    if event.get("name") == u12.E_CREATE_ISSUING]
        self.assertEqual(
            len(attempts), 1,
            "the live intent must keep exactly one durable create attempt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
