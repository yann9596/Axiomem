#!/usr/bin/env python3
"""YZT-84 — publication transport repair and proof-bearing O2 recovery tests.

Groups (approved exception, `U12_R0_PUBLICATION_TRANSPORT_DECISION.md`, raw
SHA256 0df4cec9..., Human approval 01a09003-f830):

1. the exact historical shape: a record genuinely produced by the accepted
   da99c11 bytes (create -> create recovery -> ownership -> E -> one real
   publication attempt that lost exactly its terminal LF -> BLOCKED rev4),
   then the one approved recovery commit to HANDOFF_PUBLISHED rev5 plus the
   execution-identity migration;
2. the future `publication-single-terminal-lf-v1` transport: T = R minus one
   terminal LF, O == T, exactly one comment_add, byte-verified content file;
3. the raw identity relation and renderer-shape refusal matrix;
4. the recovery refusal matrix (note/delta/evidence/material/authority);
5. migration fences: new loader without migration, old da99c11 readers on the
   new op, tampered proofs/decisions/migrations, changed dependency bytes;
6. crash/replay/lease/CAS/shared-tail/conflicting-intent behavior;
7. post-recovery lifecycle: O-bound note recheck, fresh preflight, one strict
   rerun at most.

Everything runs on temporary JSONL ledgers and fixture CLIs; no live Multica
write, no production-ledger access, no Canonical write, no note resend.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
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
sys.path.insert(0, str(TOOLS / "tests"))

import chandoff_intent as o2  # noqa: E402
import chandoff_note as note  # noqa: E402
import u12_r0_binding as u12  # noqa: E402
from test_u12_r0_binding import (  # noqa: E402
    CLOCK, DISPATCHER, PARENT_ID, PUBLISHER_AGENT, PUBLISHER_RUN, SOURCE_RUN,
    TARGET_AGENT, TARGET_ID, TARGET_IDENTIFIER, FixedAuthorityReader,
    creation_package, default_artifact_dependency, execution_context_for,
    make_spec, real_readiness_manifest)
from test_u12_r0_create_recovery import (  # noqa: E402
    LiveShapeCli, LostTerminalLfCli, assert_only_reads,
    assert_zero_native_writes, load_predecessor_module)

WRITE_PREFIXES = (["issue", "create"], ["issue", "assign"],
                  ["issue", "comment", "add"], ["issue", "rerun"],
                  ["issue", "status"], ["issue", "update"])
READ_PREFIXES = (["issue", "get"], ["issue", "comment", "list"],
                 ["issue", "timeline"], ["issue", "runs"],
                 ["issue", "children"], ["version"])


# ---------------------------------------------------------------------------
# the accepted da99c11 forward adapter bytes
# ---------------------------------------------------------------------------
_FORWARD: dict = {}


def load_forward_module():
    """Load the exact da99c11 adapter bytes from the accepted commit.

    The historical record must be produced by the real accepted bytes, not a
    lookalike. A missing git/commit/blob fails instead of skipping. Only the
    module's ROOT constant is pointed at this checkout so its own git blob
    resolver reads real objects.
    """
    if "module" in _FORWARD:
        return _FORWARD["module"]
    proc = subprocess.run(
        ["git", "-C", str(TOOLS.parent), "show",
         f"{u12.PREDECESSOR_FORWARD_COMMIT}:tools/u12_r0_binding.py"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise AssertionError(
            "the da99c11 adapter blob is unavailable; git and the exact "
            f"commit {u12.PREDECESSOR_FORWARD_COMMIT} are required")
    data = proc.stdout
    digest = "sha256:" + hashlib.sha256(
        data.replace(b"\r\n", b"\n")).hexdigest()
    if digest != u12.PREDECESSOR_FORWARD_ADAPTER_DIGEST:
        raise AssertionError(
            f"da99c11 blob digest {digest} does not match the accepted pin "
            f"{u12.PREDECESSOR_FORWARD_ADAPTER_DIGEST}")
    holder = Path(tempfile.mkdtemp(prefix="u12-r0b-forward-"))
    path = holder / "u12_r0_binding_forward.py"
    path.write_bytes(data)
    spec = importlib.util.spec_from_file_location(
        "u12_r0_binding_forward", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.ROOT = u12.ROOT
    _FORWARD["module"] = module
    _FORWARD["digest"] = digest
    return module


def load_forward_intent_module():
    """Load the accepted da99c11 `chandoff_intent.py` as an old reader."""
    if "intent" in _FORWARD:
        return _FORWARD["intent"]
    proc = subprocess.run(
        ["git", "-C", str(TOOLS.parent), "show",
         f"{u12.PREDECESSOR_FORWARD_COMMIT}:tools/chandoff_intent.py"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise AssertionError("the da99c11 chandoff_intent blob is unavailable")
    holder = Path(tempfile.mkdtemp(prefix="u12-r0b-forward-intent-"))
    path = holder / "chandoff_intent_forward.py"
    path.write_bytes(proc.stdout)
    spec = importlib.util.spec_from_file_location(
        "chandoff_intent_forward", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _FORWARD["intent"] = module
    return module


# ---------------------------------------------------------------------------
# fixture CLI: the real incident's terminal-LF loss on publication
# ---------------------------------------------------------------------------
_REAL_NOTE_ID = "01a08ff0-bce4-79b6-9116-a830baca4049"


class HistoricalPublicationCli(LostTerminalLfCli):
    """Create loses one terminal LF; the publication link loses the last LF.

    The stored comment body is exactly what the platform kept, so publishing
    R yields O == R[:-1] (the incident), while publishing the prepared T has
    no terminal LF left to lose (O == T). Raw content-file bytes are retained,
    and the first note carries a real UUID-shaped comment id.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.raw_comment_contents: list = []
        self.comment_list_count = 0
        self.on_comment_list = None

    def _comment_add(self, core):
        raw = Path(core[core.index("--content-file") + 1]).read_bytes()
        self.raw_comment_contents.append(raw.decode("utf-8"))
        return super()._comment_add(core)

    def _store_comment(self, issue_id, content):
        if content.endswith("\n"):
            content = content[:-1]
        comment = super()._store_comment(issue_id, content)
        if comment.get("id") == "CMT-1":
            comment["id"] = _REAL_NOTE_ID
        return comment

    def _comment_list(self, core):
        self.comment_list_count += 1
        if callable(self.on_comment_list):
            self.on_comment_list(self)
        return super()._comment_list(core)


class FutureTransportCli(LiveShapeCli):
    """A clean create; the publication link loses one terminal LF if present."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.raw_comment_contents: list = []

    def _comment_add(self, core):
        raw = Path(core[core.index("--content-file") + 1]).read_bytes()
        self.raw_comment_contents.append(raw.decode("utf-8"))
        return super()._comment_add(core)

    def _store_comment(self, issue_id, content):
        if content.endswith("\n"):
            content = content[:-1]
        return super()._store_comment(issue_id, content)


# ---------------------------------------------------------------------------
# publication recovery disposition (fixture Lead authority)
# ---------------------------------------------------------------------------
_SENTINEL = object()


def migration_identities(store=None, intent_id=None):
    head = u12._git_rev_parse("HEAD")
    tree = u12._git_rev_parse("HEAD^{tree}")
    artifact = None
    if store is not None and intent_id is not None:
        artifact = (store.get(intent_id)["fields"].get(u12.R0B_FIELD)
                    or {}).get("artifact_dependency", {}).get("digest")
    return {
        "accepted_commit": head,
        "accepted_tree": tree,
        "adapter_raw_digest": u12.adapter_raw_digest(),
        "adapter_lf_digest": u12.adapter_digest(),
        "store_file_digest": u12._file_lf_digest(
            TOOLS / "chandoff_intent.py"),
        "note_file_digest": u12._file_lf_digest(TOOLS / "chandoff_note.py"),
        "artifact_dependency_digest":
            artifact or default_artifact_dependency()["digest"],
    }


def make_publication_decision(fx, **overrides):
    intent = fx.store.get(fx.intent_id)
    data = intent["fields"][u12.R0B_FIELD]
    blocker = intent["transitions"][-1]
    evidence = [
        e for e in intent["events"]
        if e.get("name") == u12.E_EVIDENCE_REFUSED
        and (e.get("data") or {}).get("code") == u12.PUB_NOTE_NOT_FOUND][0]
    attempt_event = [
        e for e in intent["events"]
        if e.get("name") == u12.E_PUBLICATION_ISSUING][0]
    attempt = attempt_event["data"]
    execution = data["execution_context"]
    rendered, _ = note.render_note_record(
        execution["result"], prepared_by=attempt["prepared_by"],
        prepared_at=attempt["prepared_at"])
    decision = {
        "schema": u12.PUBLICATION_RECOVERY_DECISION_SCHEMA,
        "decision_id": "lead-u12-r0b-publication-recovery-1",
        "disposition": u12.PUBLICATION_RECOVERY_DISPOSITION,
        "scope": u12.PUBLICATION_RECOVERY_SCOPE,
        "intent_id": fx.intent_id,
        "expected_target_id": TARGET_ID,
        "expected_intent_revision": 4,
        "expected_blocker": {
            "seq": blocker["seq"],
            "digest": u12.digest(blocker),
            "reason": u12.PUBLICATION_BLOCKER_REASON,
            "evidence_code": u12.PUBLICATION_BLOCKER_CODE,
            "evidence_event": {"seq": evidence["seq"],
                               "digest": u12.digest(evidence)},
        },
        "publication_attempt": {
            "seq": attempt_event["seq"],
            "digest": u12.digest(attempt_event),
            "operation_id": attempt["operation_id"],
        },
        "expected_note": {
            "note_comment_id": fx.note_comment["id"],
            "revision": 1,
            "created_at": fx.note_comment["created_at"],
            "updated_at": fx.note_comment["updated_at"],
            "author_id": fx.note_comment["author_id"],
            "author_type": fx.note_comment["author_type"],
            "source_task_id": fx.note_comment["source_task_id"],
            "parent_id": fx.note_comment["parent_id"],
            "rendered_raw_digest": u12._sha256_utf8(rendered),
            "observed_raw_digest": u12._sha256_utf8(fx.note_content),
        },
        "original_create_recovery": {
            "decision_digest":
                data["recovery_proof"]["decision"]["decision_digest"],
            "proof_digest": data["recovery_proof"]["proof_digest"],
            "execution_binding_digest": u12.digest(data["execution_binding"]),
            "accepted_commit": u12.PREDECESSOR_FORWARD_COMMIT,
            "accepted_adapter_digest": u12.PREDECESSOR_FORWARD_ADAPTER_DIGEST,
        },
        "source_activation": {
            "comment_id": "01a08ff4-83c5-7b0c-a075-ec60184a843a",
            "resolution_digest": "sha256:" + "e" * 64,
            "attachment_id": "01a08fec-29a9-75ec-ad1c-0f3ecbb96649",
        },
        "execution_migration":
            overrides.pop("execution_migration", None)
            or migration_identities(fx.store, fx.intent_id),
        "purpose": u12.PUBLICATION_RECOVERY_PURPOSE,
        "trigger_policy": u12.PUBLICATION_TRIGGER_POLICY,
        "design_ref": u12.PUBLICATION_TRANSPORT_DESIGN_REF,
        "design_digest": u12.PUBLICATION_TRANSPORT_DESIGN_DIGEST,
        "human_approval_ref": u12.PUBLICATION_RECOVERY_HUMAN_APPROVAL_REF,
        "lead_approval_ref": u12.PUBLICATION_RECOVERY_LEAD_APPROVAL_REF,
        "approval_ref": "multica://comment/01a09003-f830-79ac-b022-c7aaf2cf4039",
        "approved_by": "Yann (Human)",
        "approved_at": CLOCK,
    }
    decision.update(overrides)
    decision["decision_digest"] = u12.digest(
        {k: v for k, v in decision.items() if k != "decision_digest"})
    return decision


def append_comment(cli, content, *, comment_id="CMT-R-TWIN", **overrides):
    """Append one extra comment with exact bytes (no transport stripping)."""
    comment = {
        "id": comment_id,
        "content": content,
        "created_at": CLOCK,
        "updated_at": CLOCK,
        "parent_id": None,
        "author_id": PUBLISHER_AGENT,
        "author_type": "agent",
        "source_task_id": PUBLISHER_RUN,
        "issue_id": TARGET_ID,
        "resolved_at": None,
        "resolved_by_id": None,
        "resolved_by_type": None,
        "revision": 1,
        "type": "comment",
        "attachments": [],
        "reactions": [],
    }
    comment.update(overrides)
    cli.comments[TARGET_ID].append(comment)
    return comment


class PublicationRecoveryFixture:
    """A genuine da99c11 record stopped at the real publication incident."""

    def __init__(self, tmp: Path, *, cli=None, authority_reader="default",
                 blob_reader=None):
        self.tmp = Path(tmp)
        self.cli = cli if cli is not None else HistoricalPublicationCli()
        self.store = o2.DurableIntentStore(self.tmp / "ledger.jsonl")
        # stage 1: the b49630b predecessor genuinely records and creates
        self.predecessor = load_predecessor_module()
        self.forward = load_forward_module()
        base_reader = (blob_reader if blob_reader is not None
                       else u12._git_blob_reader(u12.ROOT))
        self.blob_reader = base_reader
        self.old_factory = self.predecessor.build_r0b_factory(
            self.store, runner=self.cli, artifact_blob_reader=base_reader)
        self.spec, self.intent_id = make_spec()
        context = creation_package()
        self.old_factory.record_creation_intent(
            creation_context={"result": context["result"],
                              "request": context["request"],
                              "self_check": context["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=self.spec,
            authority="Human U12 approval + YZT-83/84 decisions",
            actor=DISPATCHER, source_run=SOURCE_RUN,
            intent_id=self.intent_id)
        self.old_factory.create_target_once(self.intent_id, actor=DISPATCHER)
        # stage 2: the da99c11 forward adapter performs the accepted create
        # recovery, then ownership, E binding and the one real publication
        self.forward_factory = self.forward.build_r0b_factory(
            self.store, runner=self.cli, artifact_blob_reader=base_reader,
            authority_reader=u12.ReadinessManifestAuthorityReader())
        prefix, pair = self._audit_pins()
        create_decision = self._create_recovery_decision(prefix, pair)
        recovered = self.forward_factory.recover_created_target(
            self.intent_id, expected_target_id=TARGET_ID,
            recovery_decision=create_decision, actor=DISPATCHER,
            execution_commit=u12.PREDECESSOR_FORWARD_COMMIT)
        assert recovered["status"] == o2.S_TARGET_BOUND, recovered
        self.forward_factory.assign_ownership_once(self.intent_id,
                                                   actor=DISPATCHER)
        prepared = self.forward_factory.bind_execution_package(
            self.intent_id, execution_context=execution_context_for(self.cli),
            actor=DISPATCHER)
        assert prepared["status"] == o2.S_HANDOFF_PREPARED, prepared
        published = self.forward_factory.publish_handoff_once(
            self.intent_id, actor=DISPATCHER, publisher_run_id=PUBLISHER_RUN,
            prepared_by="01 Engineering Lead", prepared_at=CLOCK)
        assert published["status"] == o2.S_BLOCKED, published
        self.note_contents = list(self.cli.raw_comment_contents)
        comments = self.cli.comments[TARGET_ID]
        assert len(comments) == 1
        self.note_comment = comments[0]
        self.note_content = self.note_comment["content"]
        authority = (u12.ReadinessManifestAuthorityReader()
                     if authority_reader == "default" else authority_reader)
        self.factory = u12.build_r0b_factory(
            self.store, runner=self.cli, artifact_blob_reader=base_reader,
            authority_reader=authority)
        self.finding_source: list = []

    # -- creation of the historical shape -----------------------------------
    def _audit_pins(self) -> tuple:
        raw = (self.tmp / "ledger.jsonl").read_bytes()
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        prefix = {"length": len(lines),
                  "digest": u12._raw_prefix_digest(lines)}
        records = self.store.read_records()
        for index, record in enumerate(records):
            if record.get("kind") != "command" or \
                    record.get("command_class") != "issue_create":
                continue
            result = records[index + 1]
            return prefix, {
                "command_seq": record["seq"],
                "command_digest": u12.digest(record),
                "result_seq": result["seq"],
                "result_digest": u12.digest(result),
            }
        raise AssertionError("fixture ledger carries no create command")

    def _create_recovery_decision(self, prefix, pair):
        fwd = self.forward
        decision = {
            "schema": fwd.RECOVERY_DECISION_SCHEMA,
            "decision_id": "lead-accept-u12-r0-create-recovery-1",
            "disposition": fwd.RECOVERY_DISPOSITION,
            "scope": fwd.RECOVERY_SCOPE,
            "intent_id": self.intent_id,
            "expected_target_id": TARGET_ID,
            "expected_intent_revision": 1,
            "expected_target_revision": 1,
            "expected_creator_id": PUBLISHER_AGENT,
            "predecessor_commit": fwd.PREDECESSOR_ADAPTER_COMMIT,
            "predecessor_adapter_digest": fwd.PREDECESSOR_ADAPTER_DIGEST,
            "design_ref": fwd.RECOVERY_DESIGN_REF,
            "design_digest": fwd.RECOVERY_DESIGN_DIGEST,
            "evidence_decision_ref": fwd.EVIDENCE_DECISION_REF,
            "evidence_decision_digest": fwd.EVIDENCE_DECISION_DIGEST,
            "original_receipt_body_status":
                fwd.RECEIPT_STATUS_NOT_PERSISTED,
            "receipt_limit_scope": fwd.RECEIPT_LIMIT_SCOPE,
            "ledger_prefix": dict(prefix),
            "original_create_pair": dict(pair),
            "accepted_execution": {
                "commit": u12.PREDECESSOR_FORWARD_COMMIT,
                "adapter_digest": u12.PREDECESSOR_FORWARD_ADAPTER_DIGEST,
            },
            "approval_ref":
                "multica://comment/01a08f58-0168-75ca-9bd7-9528b143094c",
            "approved_by": "01 Engineering Lead",
            "approved_at": CLOCK,
        }
        decision["decision_digest"] = u12.digest(
            {k: v for k, v in decision.items() if k != "decision_digest"})
        return decision

    # -- public operation helpers -------------------------------------------
    def decision(self, **overrides):
        return make_publication_decision(self, **overrides)

    def recover(self, *, decision=None, accepted_execution=None,
                actor=DISPATCHER, findings=_SENTINEL, **overrides):
        if decision is None:
            decision = self.decision(**overrides)
        accepted = accepted_execution if accepted_execution is not None \
            else dict(decision["execution_migration"])
        return self.factory.recover_blocked_publication(
            self.intent_id, decision=decision, accepted_execution=accepted,
            actor=actor,
            current_findings=(self.finding_source if findings is _SENTINEL
                              else findings))

    def arm(self, **overrides):
        kwargs = {"actor": DISPATCHER,
                  "current_request": execution_context_for(self.cli)["request"],
                  "current_findings": []}
        kwargs.update(overrides)
        return self.factory.arm(self.intent_id, **kwargs)

    def trigger(self, **overrides):
        kwargs = {"actor": DISPATCHER,
                  "current_request": execution_context_for(self.cli)["request"],
                  "current_findings": []}
        kwargs.update(overrides)
        return self.factory.trigger(self.intent_id, **kwargs)

    def binding(self):
        return self.store.get(
            self.intent_id)["fields"][u12.R0B_FIELD]

    def commands_of(self, prefix):
        return self.cli.commands_of(prefix)

    def prepare_commit(self, decision=None):
        """White-box: build the exact commit record without committing."""
        decision = decision or self.decision()
        intent, data = self.factory._publication_recovery_inspection(
            self.intent_id)
        bundle = self.factory._publication_recovery_prerequisites(
            intent, data, decision=decision, current_findings=[],
            authority_evidence=None)
        proof = self.factory._build_publication_recovery_proof(
            intent, data, decision, bundle)
        migration = self.factory._build_publication_execution_migration(
            intent, data, decision, proof)
        record = {
            "kind": "intent", "record_type": o2.INTENT_RECORD_TYPE,
            "schema_version": o2.O2_SCHEMA, "op": u12.PUBLICATION_RECOVERY_OP,
            "intent_id": self.intent_id, "from": o2.S_BLOCKED,
            "to": o2.S_HANDOFF_PUBLISHED, "revision": 5,
            "actor": DISPATCHER, "at": CLOCK,
            "publication_recovery_proof": proof,
            "publication_execution_migration": migration,
        }
        fields, _ = u12._publication_commit_fields(
            intent, data, proof, migration, actor=DISPATCHER, now=CLOCK)
        record["fields"] = fields
        return record, bundle, decision

    def events_named(self, name):
        return [e for e in self.store.get(self.intent_id)["events"]
                if e.get("name") == name]

    def ledger_bytes(self):
        return (self.tmp / "ledger.jsonl").read_bytes()


def rendered_original(fx):
    data = fx.binding()
    attempt = [
        e for e in fx.store.get(fx.intent_id)["events"]
        if e.get("name") == u12.E_PUBLICATION_ISSUING][0]["data"]
    rendered, _ = note.render_note_record(
        data["execution_context"]["result"],
        prepared_by=attempt["prepared_by"],
        prepared_at=attempt["prepared_at"])
    return rendered


def assert_recovery_refused(case, fx, result, reason=None):
    case.assertEqual(result["status"], o2.S_BLOCKED)
    case.assertEqual(result["outcome"], "RECOVERY_REFUSED")
    if reason is not None:
        case.assertEqual(result["reason"], reason)
    case.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)
    case.assertEqual(fx.store.get(fx.intent_id)["revision"], 4)


# ---------------------------------------------------------------------------
# 1. historical shape and the one approved recovery
# ---------------------------------------------------------------------------
class HistoricalShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicationRecoveryFixture(Path(self.tmp.name))

    def test_fixture_is_the_exact_real_incident_shape(self):
        fx = self.fx
        intent = fx.store.get(fx.intent_id)
        self.assertEqual(intent["state"], o2.S_BLOCKED)
        self.assertEqual(intent["revision"], 4)
        data = fx.binding()
        self.assertEqual(data["contract_version"], u12.CONTRACT_VERSION)
        self.assertEqual(data["adapter_digest"],
                         u12.PREDECESSOR_ADAPTER_DIGEST)
        self.assertEqual(data["execution_binding"]["adapter_digest"],
                         u12.PREDECESSOR_FORWARD_ADAPTER_DIGEST)
        self.assertEqual(data["execution_binding"]["accepted_execution_commit"],
                         u12.PREDECESSOR_FORWARD_COMMIT)
        # exactly one create, one ownership, one real publication attempt
        self.assertEqual(len(fx.events_named(u12.E_CREATE_ISSUING)), 1)
        self.assertEqual(len(fx.events_named(u12.E_OWNERSHIP_ISSUING)), 1)
        self.assertEqual(len(fx.events_named(u12.E_PUBLICATION_ISSUING)), 1)
        self.assertEqual(len(fx.events_named(u12.E_PUBLICATION_RESPONSE)), 1)
        self.assertEqual(len(fx.commands_of(["issue", "create"])), 1)
        self.assertEqual(len(fx.commands_of(["issue", "assign"])), 1)
        self.assertEqual(len(fx.commands_of(["issue", "comment", "add"])), 1)
        self.assertEqual(fx.commands_of(["issue", "rerun"]), [])
        blocker = [
            e for e in fx.events_named(u12.E_EVIDENCE_REFUSED)
            if (e["data"] or {}).get("code") == u12.PUB_NOTE_NOT_FOUND]
        self.assertEqual(len(blocker), 1)
        # the exact relation: R ends with one terminal LF, O == R[:-1]
        rendered = rendered_original(fx)
        relation = u12.single_terminal_lf_relation(
            rendered, fx.note_content)
        self.assertTrue(relation["accepted"])
        self.assertTrue(relation["removed_lf"])
        self.assertEqual(fx.note_contents, [rendered])

    def test_old_predicate_refused_the_incident_before_recovery(self):
        fx = self.fx
        # the pre-commit record is not executable under the new bytes
        intent = fx.store.get(fx.intent_id)
        with self.assertRaises(u12.R0BDowngradeRefused):
            u12.validate_intent_record(intent)
        # the restricted historical inspection may read it
        inspected, data = fx.factory._publication_recovery_inspection(
            fx.intent_id)
        self.assertEqual(inspected["state"], o2.S_BLOCKED)
        self.assertEqual(data["adapter_digest"],
                         u12.PREDECESSOR_ADAPTER_DIGEST)

    def test_exact_recovery_binds_revision_five_with_zero_native_writes(self):
        fx = self.fx
        before_writes = len(fx.cli.commands)
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(result["ledger_commits"], 1)
        self.assertEqual(result["platform_writes"], 0)
        self.assertEqual(result["notes_sent"], 0)
        self.assertEqual(result["triggers_issued"], 0)
        intent = fx.store.get(fx.intent_id)
        self.assertEqual(intent["state"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(intent["revision"], 5)
        data = fx.binding()
        binding = data["publication_binding"]
        self.assertEqual(binding["body_digest_method"],
                         u12.BODY_DIGEST_METHOD_RAW)
        self.assertEqual(binding["body_digest"],
                         u12._sha256_utf8(fx.note_content))
        self.assertEqual(binding["rendered_body_digest_raw"],
                         u12._sha256_utf8(rendered_original(fx)))
        self.assertTrue(binding["recovered"])
        self.assertEqual(binding["transport_profile"], None)
        self.assertEqual(binding["relation"],
                         u12.PUBLICATION_RELATION_DIRECTIONAL)
        # no new native write at all: the only commands are the historical
        # create/assign/comment pair already exercised by the fixture
        after = fx.cli.commands[before_writes:]
        assert_only_reads(self, after)
        self.assertEqual(len(fx.commands_of(["issue", "comment", "add"])), 1)
        self.assertEqual(fx.commands_of(["issue", "rerun"]), [])

    def test_recovery_preserves_original_identity_and_history_bytes(self):
        fx = self.fx
        data_before = copy.deepcopy(fx.binding())
        records_before = len(fx.store.read_records())
        ledger_before = fx.ledger_bytes()
        fx.recover()
        data_after = fx.binding()
        self.assertEqual(
            u12.canonical_json(data_after["execution_binding"]),
            u12.canonical_json(data_before["execution_binding"]))
        self.assertEqual(
            u12.canonical_json(data_after["recovery_proof"]),
            u12.canonical_json(data_before["recovery_proof"]))
        self.assertEqual(
            u12.canonical_json(data_after["creation_spec"]),
            u12.canonical_json(data_before["creation_spec"]))
        self.assertEqual(
            u12.canonical_json(data_after["execution_context"]),
            u12.canonical_json(data_before["execution_context"]))
        self.assertEqual(
            u12.canonical_json(data_after["target_binding"]),
            u12.canonical_json(data_before["target_binding"]))
        self.assertTrue(fx.ledger_bytes().startswith(ledger_before))
        self.assertEqual(len(fx.store.read_records()), records_before + 3)

    def test_recovery_is_one_single_fsync_commit_record(self):
        fx = self.fx
        result = fx.recover()
        records = fx.store.read_records()
        commits = [r for r in records if r.get("op") ==
                   u12.PUBLICATION_RECOVERY_OP]
        self.assertEqual(len(commits), 1)
        commit = commits[0]
        self.assertEqual(commit["from"], o2.S_BLOCKED)
        self.assertEqual(commit["to"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(commit["revision"], 5)
        self.assertEqual(commit["publication_recovery_proof"]["proof_digest"],
                         result["proof_digest"])
        self.assertEqual(
            commit["publication_execution_migration"]["migration_digest"],
            result["migration_digest"])
        evidence = fx.events_named(u12.E_PUBLICATION_RECOVERY_EVIDENCE)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["data"]["proof_digest"],
                         result["proof_digest"])

    def test_ordinary_blocked_exit_stays_refused(self):
        fx = self.fx
        with self.assertRaises(o2.IllegalTransitionError):
            fx.store.transition(
                fx.intent_id, o2.S_HANDOFF_PUBLISHED,
                expected_revision=4, actor=DISPATCHER, now=CLOCK)
        self.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)
        self.assertEqual(fx.store.get(fx.intent_id)["revision"], 4)


# ---------------------------------------------------------------------------
# 2. future transport (publication-single-terminal-lf-v1)
# ---------------------------------------------------------------------------
class FutureTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cli = FutureTransportCli()
        self.store = o2.DurableIntentStore(Path(self.tmp.name) / "ledger.jsonl")
        self.factory = u12.build_r0b_factory(
            self.store, runner=self.cli,
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            authority_reader=u12.ReadinessManifestAuthorityReader())
        self.spec, self.intent_id = make_spec()
        context = creation_package()
        self.factory.record_creation_intent(
            creation_context={"result": context["result"],
                              "request": context["request"],
                              "self_check": context["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=self.spec,
            authority="Human U12 approval + YZT-83/84 decisions",
            actor=DISPATCHER, source_run=SOURCE_RUN,
            intent_id=self.intent_id)
        self.factory.create_target_once(self.intent_id, actor=DISPATCHER)
        self.factory.assign_ownership_once(self.intent_id, actor=DISPATCHER)
        self.factory.bind_execution_package(
            self.intent_id, execution_context=execution_context_for(self.cli),
            actor=DISPATCHER)

    def publish(self, **overrides):
        kwargs = {"actor": DISPATCHER, "publisher_run_id": PUBLISHER_RUN,
                  "prepared_by": "01 Engineering Lead", "prepared_at": CLOCK}
        kwargs.update(overrides)
        return self.factory.publish_handoff_once(self.intent_id, **kwargs)

    def test_future_publication_sends_exact_transport_and_binds_o(self):
        result = self.publish()
        self.assertEqual(result["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(len(self.cli.commands_of(
            ["issue", "comment", "add"])), 1)
        # the file bytes were exactly T and the link had no LF to remove
        self.assertEqual(len(self.cli.raw_comment_contents), 1)
        sent = self.cli.raw_comment_contents[0]
        self.assertTrue(sent.endswith("```"))
        self.assertFalse(sent.endswith("\n"))
        stored = self.cli.comments[TARGET_ID][0]["content"]
        self.assertEqual(stored, sent)
        attempt = [e for e in self.factory.store.get(
            self.intent_id)["events"]
            if e.get("name") == u12.E_PUBLICATION_ISSUING][0]["data"]
        self.assertEqual(attempt["transport_profile"],
                         u12.PUBLICATION_TRANSPORT_PROFILE)
        self.assertNotEqual(attempt["rendered_body_digest_raw"],
                            attempt["transport_body_digest_raw"])
        self.assertEqual(attempt["transport_body_digest_raw"],
                         u12._sha256_utf8(sent))
        binding = self.store.get(
            self.intent_id)["fields"][u12.R0B_FIELD]["publication_binding"]
        self.assertEqual(binding["body_digest"], u12._sha256_utf8(sent))
        self.assertEqual(binding["body_digest_method"],
                         u12.BODY_DIGEST_METHOD_RAW)
        self.assertEqual(binding["transport_profile"],
                         u12.PUBLICATION_TRANSPORT_PROFILE)
        self.assertFalse(binding["recovered"])
        # the renderer still ended with the exact LF-fence-LF shape
        rendered = binding["rendered_body_digest_raw"]
        self.assertTrue(rendered.startswith("sha256:"))

    def test_future_publication_note_check_and_one_strict_rerun(self):
        self.assertEqual(self.publish()["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(self.cli.commands_of(["issue", "rerun"]), [])
        armed = self.factory.arm(
            self.intent_id, actor=DISPATCHER,
            current_request=execution_context_for(self.cli)["request"],
            current_findings=[])
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        triggered = self.factory.trigger(
            self.intent_id, actor=DISPATCHER,
            current_request=execution_context_for(self.cli)["request"],
            current_findings=[])
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(len(self.cli.commands_of(["issue", "rerun"])), 1)
        self.assertEqual(len(self.cli.commands_of(
            ["issue", "comment", "add"])), 1)

    def test_mutated_note_after_transport_publication_refuses_preflight(self):
        self.publish()
        self.cli.comments[TARGET_ID][0]["content"] = (
            self.cli.comments[TARGET_ID][0]["content"] + "x")
        result = self.factory.arm(
            self.intent_id, actor=DISPATCHER,
            current_request=execution_context_for(self.cli)["request"],
            current_findings=[])
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(self.cli.commands_of(["issue", "rerun"]), [])


# ---------------------------------------------------------------------------
# 3. transport relation and renderer-shape refusal matrix
# ---------------------------------------------------------------------------
class TransportRelationTests(unittest.TestCase):
    def test_prepared_transport_is_rendered_minus_one_terminal_lf(self):
        rendered = "/note\n\nREC\nmeta\n```\n{}\n```\n"
        prepared = u12.prepare_publication_transport_body(rendered)
        self.assertEqual(prepared["transport_body"], rendered[:-1])
        self.assertEqual(prepared["rendered_body_digest_raw"],
                         u12._sha256_utf8(rendered))
        self.assertEqual(prepared["transport_body_digest_raw"],
                         u12._sha256_utf8(rendered[:-1]))
        self.assertEqual(prepared["rendered_chars"],
                         prepared["transport_chars"] + 1)

    def test_renderer_shape_refusals(self):
        bad = {
            "no_terminal_lf": "/note\n```\n{}",
            "crlf": "/note\r\n```\r\n",
            "double_lf": "/note\n```\n\n",
            "trailing_space_before_lf": "/note\n``` \n",
            "bom": "\ufeff/note\n```\n",
        }
        for name, value in bad.items():
            with self.assertRaises(u12.R0BValidationRefused, msg=name):
                u12.prepare_publication_transport_body(value)

    def test_exact_relation_only_o_equals_t(self):
        t = "/note\n```\n{}"
        for observed in (t, "/note\n```\n{}}\n"):
            expected = observed == t
            result = u12.publication_transport_relation(t, observed)
            self.assertEqual(result["accepted"], expected, observed)

    def test_relation_refuses_every_normalization_variant(self):
        t = "前缀\ninternal\n```"
        variants = {
            "leading_ws": " " + t,
            "internal_ws": t.replace("internal", "internal "),
            "crlf": t.replace("\n", "\r\n"),
            "bom": "\ufeff" + t,
            "trailing_space": t + " ",
            "double_lf": t + "\n\n",
            "unicode": t.replace("前缀", "前綴"),
        }
        for name, observed in variants.items():
            result = u12.publication_transport_relation(t, observed)
            self.assertFalse(result["accepted"], name)
            self.assertEqual(result["reason"],
                             u12.REASON_TRANSPORT_UNSUPPORTED, name)

    def test_historical_relation_requires_exactly_one_removed_lf(self):
        r = "body\n```\n"
        ok = u12.single_terminal_lf_relation(r, r[:-1])
        self.assertTrue(ok["accepted"] and ok["removed_lf"])
        for observed in (r, r + "x", r[:-2]):
            result = u12.single_terminal_lf_relation(r, observed)
            if observed == r:
                self.assertTrue(result["accepted"])
                self.assertFalse(result["removed_lf"])
            else:
                self.assertFalse(result["accepted"], observed)


# ---------------------------------------------------------------------------
# 4. recovery refusal matrix through the public operation
# ---------------------------------------------------------------------------
class RecoveryRefusalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicationRecoveryFixture(Path(self.tmp.name))

    def test_zero_note_refuses_note_not_found(self):
        self.fx.cli.comments[TARGET_ID] = []
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_PROVENANCE)
        self.assertIn("exactly one note", result["detail"])

    def test_two_identical_notes_refuse(self):
        append_comment(self.fx.cli, self.fx.note_content,
                       comment_id="CMT-DUP")
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_PROVENANCE)

    def test_r_and_r_minus_one_pair_refuses(self):
        append_comment(self.fx.cli, rendered_original(self.fx),
                       comment_id="CMT-R-TWIN")
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_PROVENANCE)

    def test_wrong_author_source_thread_timestamp_or_revision_refuses(self):
        for field, value in (("author_id", TARGET_AGENT),
                             ("source_task_id", SOURCE_RUN),
                             ("parent_id", "01a08f14-8911-7d59-aee7-e5bd0269cbf1"),
                             ("updated_at", "2026-09-11T10:09:00Z"),
                             ("revision", 2)):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            fx = PublicationRecoveryFixture(Path(tmp.name))
            decision = fx.decision()
            fx.note_comment[field] = value
            result = fx.recover(
                decision=decision,
                accepted_execution=dict(decision["execution_migration"]))
            assert_recovery_refused(
                self, fx, result, u12.REASON_PUBLICATION_PROVENANCE)

    def test_edited_note_body_refuses(self):
        self.fx.note_comment["content"] = self.fx.note_content + "x"
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_PROVENANCE)

    def test_extra_comment_around_publication_refuses(self):
        append_comment(self.fx.cli, "unrelated extra comment",
                       comment_id="CMT-EXTRA")
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_CONFLICT)

    def test_issue_projection_drift_refuses(self):
        self.fx.cli.issues[TARGET_ID]["title"] = "drifted title"
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_CONFLICT)

    def test_timeline_activity_delta_refuses(self):
        self.fx.cli.add_activity(TARGET_ID, "commented")
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_PUBLICATION_CONFLICT)

    def test_any_run_active_or_terminal_refuses(self):
        self.fx.cli.runs[TARGET_ID] = [{
            "id": "RUN-DONE", "issue_id": TARGET_ID,
            "agent_id": TARGET_AGENT, "status": "completed", "attempt": 1}]
        result = self.fx.recover()
        assert_recovery_refused(
            self, self.fx, result, u12.REASON_MATERIAL_STALE)

    def test_second_complete_read_drift_refuses(self):
        fx = self.fx

        def mutate(cli):
            if cli.comment_list_count == 2:
                append_comment(cli, "race", comment_id="CMT-RACE")

        fx.cli.comment_list_count = 0
        fx.cli.on_comment_list = mutate
        result = fx.recover()
        assert_recovery_refused(
            self, fx, result, u12.REASON_MOVING_EVIDENCE)

    def test_wrong_package_or_missing_findings_refuse(self):
        fx = self.fx
        result = fx.recover(findings=None)
        assert_recovery_refused(
            self, fx, result, u12.REASON_PREFLIGHT_INPUT_MISSING)
        # a missing artifact reader is a typed material stop
        factory = u12.build_r0b_factory(
            fx.store, runner=fx.cli, artifact_blob_reader=None,
            authority_reader=u12.ReadinessManifestAuthorityReader())
        decision = fx.decision()
        result = factory.recover_blocked_publication(
            fx.intent_id, decision=decision,
            accepted_execution=dict(decision["execution_migration"]),
            actor=DISPATCHER, current_findings=[])
        assert_recovery_refused(
            self, fx, result, u12.REASON_MATERIAL_UNAVAILABLE)

    def test_superseded_authority_refuses(self):
        fx = self.fx
        fx.factory = u12.build_r0b_factory(
            fx.store, runner=fx.cli,
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            authority_reader=FixedAuthorityReader(
                real_readiness_manifest(), disposition="SUPERSEDED"))
        result = fx.recover()
        assert_recovery_refused(
            self, fx, result, u12.REASON_MATERIAL_STALE)

    def test_wrong_decision_target_and_attempt_digest_refuse(self):
        fx = self.fx
        decision = fx.decision(expected_target_id=TARGET_AGENT)
        result = fx.recover(decision=decision,
                            accepted_execution=dict(
                                decision["execution_migration"]))
        # actual execution migration matches; but the target mismatch is
        # caught by the proof/predicate binding, never silently accepted
        assert_recovery_refused(
            self, fx, result, u12.REASON_PUBLICATION_PROVENANCE)
        decision = fx.decision()
        decision["publication_attempt"]["operation_id"] = "OP-forged"
        decision["decision_digest"] = u12.digest(
            {k: v for k, v in decision.items() if k != "decision_digest"})
        result = fx.recover(
            decision=decision,
            accepted_execution=dict(decision["execution_migration"]))
        assert_recovery_refused(
            self, fx, result, u12.REASON_PUBLICATION_PROVENANCE)

    def test_decision_schema_tamper_and_extra_fields_refuse(self):
        fx = self.fx
        for mutate in ("extra", "schema", "digest", "design"):
            decision = fx.decision()
            if mutate == "extra":
                decision["note"] = "unexpected"
            elif mutate == "schema":
                decision["schema"] = "u12-r0b-publication-recovery-decision/9"
            elif mutate == "digest":
                decision["decision_digest"] = "sha256:" + "0" * 64
            else:
                decision["design_digest"] = "sha256:" + "1" * 64
            with self.assertRaises(u12.R0BValidationRefused, msg=mutate):
                fx.recover(decision=decision,
                           accepted_execution=dict(
                               decision["execution_migration"]))
        self.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)


# ---------------------------------------------------------------------------
# 5. migration fences
# ---------------------------------------------------------------------------
class MigrationFenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicationRecoveryFixture(Path(self.tmp.name))

    def test_old_da99c11_reader_refuses_the_new_op(self):
        self.fx.recover()
        old_intent = load_forward_intent_module()
        records = self.fx.store.read_records()
        with self.assertRaises(old_intent.LedgerCorruptionError):
            old_intent.fold_records(records)

    def test_new_loader_without_migration_refuses_old_bytes(self):
        intent = self.fx.store.get(self.fx.intent_id)
        with self.assertRaises(u12.R0BDowngradeRefused):
            u12.validate_intent_record(intent)
        self.fx.recover()
        # after the commit the exact migration grants execution
        validated = u12.validate_intent_record(
            self.fx.store.get(self.fx.intent_id))
        self.assertIsInstance(validated, dict)

    def test_tampered_old_binding_or_proof_digest_in_migration_refuses(self):
        fx = self.fx
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_HANDOFF_PUBLISHED)
        intent = fx.store.get(fx.intent_id)
        data = fx.binding()
        migration = copy.deepcopy(data["publication_execution_migration"])
        migration["old_recovery_proof_digest"] = "sha256:" + "0" * 64
        migration["migration_digest"] = u12.digest(
            {k: v for k, v in migration.items() if k != "migration_digest"})
        with self.assertRaises(u12.R0BValidationRefused):
            u12.validate_publication_execution_migration(
                intent, data, migration, applying=False)
        migration = copy.deepcopy(data["publication_execution_migration"])
        migration["old_execution_binding_digest"] = "sha256:" + "1" * 64
        migration["migration_digest"] = u12.digest(
            {k: v for k, v in migration.items() if k != "migration_digest"})
        with self.assertRaises(u12.R0BValidationRefused):
            u12.validate_publication_execution_migration(
                intent, data, migration, applying=False)

    def test_cross_intent_migration_refuses(self):
        fx = self.fx
        fx.recover()
        intent = fx.store.get(fx.intent_id)
        data = fx.binding()
        migration = copy.deepcopy(data["publication_execution_migration"])
        migration["intent_id"] = "DI-" + "0" * 16
        migration["migration_digest"] = u12.digest(
            {k: v for k, v in migration.items() if k != "migration_digest"})
        with self.assertRaises(u12.R0BValidationRefused):
            u12.validate_publication_execution_migration(
                intent, data, migration, applying=False)

    def test_changed_store_or_note_bytes_refuse(self):
        fx = self.fx
        fx.recover()
        intent = fx.store.get(fx.intent_id)
        data = fx.binding()
        for key in ("store_file_digest", "note_file_digest",
                    "new_adapter_lf_digest"):
            migration = copy.deepcopy(data["publication_execution_migration"])
            migration[key] = "sha256:" + "2" * 64
            migration["migration_digest"] = u12.digest(
                {k: v for k, v in migration.items()
                 if k != "migration_digest"})
            with self.assertRaises(u12.R0BValidationRefused, msg=key):
                u12.validate_publication_execution_migration(
                    intent, data, migration, applying=False)

    def test_migration_must_bind_exactly_one_commit_revision(self):
        fx = self.fx
        fx.recover()
        intent = fx.store.get(fx.intent_id)
        data = fx.binding()
        migration = copy.deepcopy(data["publication_execution_migration"])
        migration["commit_revision"] = 6
        migration["migration_digest"] = u12.digest(
            {k: v for k, v in migration.items() if k != "migration_digest"})
        with self.assertRaises(u12.R0BValidationRefused):
            u12.validate_publication_execution_migration(
                intent, data, migration, applying=False)

    def test_tampered_committed_proof_refuses_on_load(self):
        fx = self.fx
        fx.recover()
        intent = fx.store.get(fx.intent_id)
        data = copy.deepcopy(fx.binding())
        data["publication_recovery_proof"]["note"]["observed_chars"] += 1
        with self.assertRaises(u12.R0BValidationRefused):
            u12.validate_publication_recovery_proof(
                data["publication_recovery_proof"], intent=intent, data=data,
                applying=False)

    def test_competing_second_commit_refuses_in_writer_and_reducer(self):
        fx = self.fx
        fx.recover()
        records = fx.store.read_records()
        commits = [r for r in records if r.get("op") ==
                   u12.PUBLICATION_RECOVERY_OP]
        intent = fx.store.get(fx.intent_id)
        # the reducer refuses a duplicate commit outright
        with self.assertRaises(o2.LedgerCorruptionError):
            u12.fold_publication_recovery_commit(
                commits[0], intent, {fx.intent_id: intent})
        # the writer refuses a second commit through the shared validator
        with self.assertRaises(u12.R0BValidationRefused):
            u12.validate_publication_recovery_commit_record(
                commits[0], intent)


# ---------------------------------------------------------------------------
# 6. crash / replay / lease / CAS / tail / conflicting intent
# ---------------------------------------------------------------------------
class CrashReplayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicationRecoveryFixture(Path(self.tmp.name))

    def test_replay_after_commit_is_idempotent_with_zero_new_commits(self):
        fx = self.fx
        fx.recover()
        records = len(fx.store.read_records())
        writes = len(fx.cli.commands_of(["issue", "comment", "add"]))
        replay = fx.recover()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["ledger_commits"], 0)
        self.assertEqual(replay["platform_writes"], 0)
        self.assertEqual(len(fx.store.read_records()), records)
        self.assertEqual(len(fx.cli.commands_of(["issue", "comment", "add"])),
                         writes)

    def test_competing_decision_on_replay_refuses(self):
        fx = self.fx
        first = fx.decision()
        fx.recover(decision=first,
                   accepted_execution=dict(first["execution_migration"]))
        other = fx.decision(approved_at="2026-09-11T09:00:00Z")
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(decision=other,
                       accepted_execution=dict(other["execution_migration"]))

    def test_crash_before_commit_leaves_blocked_and_recollects(self):
        fx = self.fx
        original = fx.factory._commit_publication_recovery
        calls = {"n": 0}

        def crash(*args, **kwargs):
            calls["n"] += 1
            raise u12.PreflightRefusal(
                o2.S_BLOCKED, u12.REASON_PUBLICATION_PROVENANCE,
                "simulated crash before commit", subject="commit")

        with mock.patch.object(fx.factory, "_commit_publication_recovery",
                               side_effect=crash):
            first = fx.recover()
        self.assertEqual(first["outcome"], "RECOVERY_REFUSED")
        self.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)
        self.assertEqual(fx.store.get(fx.intent_id)["revision"], 4)
        self.assertEqual(
            len(fx.events_named(u12.E_PUBLICATION_RECOVERY_EVIDENCE)), 1)
        # the same decision recollects and commits exactly once
        second = fx.recover()
        self.assertEqual(second["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(second["ledger_commits"], 1)
        commits = [r for r in fx.store.read_records()
                   if r.get("op") == u12.PUBLICATION_RECOVERY_OP]
        self.assertEqual(len(commits), 1)

    def test_lease_held_by_another_writer_refuses_before_any_commit(self):
        fx = self.fx
        fx.store.claim(fx.intent_id, "other-writer")
        before = len(fx.store.read_records())
        with self.assertRaises(o2.LeaseHeldError):
            fx.recover()
        self.assertEqual(len(fx.store.read_records()), before)
        self.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)

    def test_shared_tail_insertion_at_same_revision_refuses(self):
        fx = self.fx
        record, bundle, _decision = fx.prepare_commit()
        # a foreign shared command appears after the audited prefix at the
        # same intent revision
        fx.store.append({
            "kind": "command", "transaction_id": "tx-foreign",
            "command_class": o2.C_READ,
            "argv": ["multica", "issue", "get", TARGET_ID, "--output", "json"]})
        with self.assertRaises(u12.PreflightRefusal):
            fx.factory._commit_publication_recovery(
                fx.intent_id, record=record, expected_revision=4,
                expected_tail_digests=[], prefix_count=0,
                prefix_raw_digest=bundle["ledger_prefix_raw_digest"])
        self.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)

    def test_conflicting_open_intent_on_the_same_logical_key_refuses(self):
        fx = self.fx
        record, bundle, decision = fx.prepare_commit()
        original = fx.store.get(fx.intent_id)["fields"]
        twin_id = "DI-" + "f" * 16
        twin = {
            "intent_id": twin_id,
            "schema_version": o2.O2_SCHEMA,
            "source_task_id": SOURCE_RUN,
            "logical_task_key": original["logical_task_key"],
            "parent_issue_id": PARENT_ID,
            "target_role": u12.EXECUTION_ROLE,
            "target_agent_id": TARGET_AGENT,
            "package_id": original["package_id"],
            "artifact_dependency_digest": original[
                "artifact_dependency_digest"],
            "creation_authority": "fixture twin",
            "provenance": {"fixture": True},
            "state": o2.S_INTENT_RECORDED,
        }
        fx.store.append({
            "kind": "intent", "record_type": o2.INTENT_RECORD_TYPE,
            "schema_version": o2.O2_SCHEMA, "op": "recorded",
            "intent_id": twin_id, "at": CLOCK, "intent": twin})
        with self.assertRaises(u12.PreflightRefusal) as caught:
            fx.factory._commit_publication_recovery(
                fx.intent_id, record=record, expected_revision=4,
                expected_tail_digests=[], prefix_count=0,
                prefix_raw_digest=bundle["ledger_prefix_raw_digest"])
        self.assertIn("conflicting", str(caught.exception).lower())
        self.assertEqual(fx.store.get(fx.intent_id)["state"], o2.S_BLOCKED)


# ---------------------------------------------------------------------------
# 7. post-recovery lifecycle
# ---------------------------------------------------------------------------
class PostRecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = PublicationRecoveryFixture(Path(self.tmp.name))
        self.recovered = self.fx.recover()

    def test_recovery_issues_no_trigger_then_one_strict_rerun(self):
        fx = self.fx
        self.assertEqual(self.recovered["status"], o2.S_HANDOFF_PUBLISHED)
        self.assertEqual(fx.cli.commands_of(["issue", "rerun"]), [])
        armed = fx.arm()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        self.assertEqual(armed["plan"]["selected_trigger"], o2.TRIGGER_RERUN)
        triggered = fx.trigger()
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(len(fx.cli.commands_of(["issue", "rerun"])), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "assign"])), 1)
        self.assertEqual(len(fx.cli.commands_of(
            ["issue", "comment", "add"])), 1)
        checkpoints = [e["data"]["checkpoint"]
                       for e in fx.events_named(u12.E_PREFLIGHT)]
        self.assertEqual(checkpoints, ["ARM", "TRIGGER"])

    def test_note_relation_drift_after_recovery_blocks_arm(self):
        fx = self.fx
        fx.cli.comments[TARGET_ID][0]["content"] = (
            fx.note_content + "\n")
        armed = fx.arm()
        self.assertEqual(armed["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(fx.cli.commands_of(["issue", "rerun"]), [])

    def test_arm_snapshot_uses_the_recovered_note_identity(self):
        fx = self.fx
        armed = fx.arm()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        self.assertEqual(armed["plan"]["ready_note_id"],
                         fx.note_comment["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
