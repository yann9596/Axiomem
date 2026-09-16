#!/usr/bin/env python3
"""YZT-84 — forward create recovery acceptance tests (public operations only).

Groups (YZT-83 accepted decision, `U12_R0_CREATE_RECOVERY_DECISION.md`):

1. exact observed single-terminal-LF recovery through the public operation,
   zero native writes, preserved original prefix/pin/spec;
2. prospective `single-terminal-lf/1` preparation and refusal matrix;
3. live target / evidence / material refusal matrix;
4. pin, proof, cross-intent and actual-old-executor fences plus frozen pins;
5. crash/replay/concurrency: one binding, one historical create, no reset;
6. full post-recovery lifecycle to fresh E/publication/preflight/strict.

The predecessor record is produced by the *actual* predecessor module bytes
loaded from the exact accepted commit, so the old-executor refusal test
exercises the real old code, not a proposed guard. Everything runs on
temporary JSONL ledgers and a fixture CLI; no live Multica write, no
production-ledger access, no Canonical write.
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
import u12_r0_binding as u12  # noqa: E402
from test_u12_r0_binding import (  # noqa: E402
    CLOCK, DISPATCHER, PARENT_ID, PUBLISHER_AGENT, PUBLISHER_RUN, SOURCE_RUN,
    TARGET_AGENT, TARGET_ID, TARGET_IDENTIFIER, FakeCli, FixedAuthorityReader,
    creation_package, execution_context_for, make_spec, real_readiness_manifest)

READ_PREFIXES = (["issue", "get"], ["issue", "comment", "list"],
                 ["issue", "timeline"], ["issue", "runs"],
                 ["issue", "children"], ["version"],
                 ["attachment", "download"])
WRITE_PREFIXES = (["issue", "create"], ["issue", "assign"],
                  ["issue", "comment", "add"], ["issue", "rerun"],
                  ["issue", "status"], ["issue", "update"])


# ---------------------------------------------------------------------------
# predecessor module: the actual accepted bytes at b49630b
# ---------------------------------------------------------------------------
_PREDECESSOR: dict = {}


def load_predecessor_module():
    """Load the exact predecessor adapter bytes from the accepted commit.

    This is a hard requirement for the old-executor fence evidence, so a
    missing git/commit/blob fails the test instead of skipping it.
    """
    if "module" in _PREDECESSOR:
        return _PREDECESSOR["module"]
    proc = subprocess.run(
        ["git", "-C", str(TOOLS.parent), "show",
         f"{u12.PREDECESSOR_ADAPTER_COMMIT}:tools/u12_r0_binding.py"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise AssertionError(
            "the predecessor adapter blob is unavailable; git and the exact "
            f"commit {u12.PREDECESSOR_ADAPTER_COMMIT} are required for the "
            "old-executor refusal evidence")
    data = proc.stdout
    digest = "sha256:" + hashlib.sha256(
        data.replace(b"\r\n", b"\n")).hexdigest()
    if digest != u12.PREDECESSOR_ADAPTER_DIGEST:
        raise AssertionError(
            f"predecessor blob digest {digest} does not match the accepted "
            f"pin {u12.PREDECESSOR_ADAPTER_DIGEST}")
    holder = Path(tempfile.mkdtemp(prefix="u12-r0b-predecessor-"))
    path = holder / "u12_r0_binding_predecessor.py"
    path.write_bytes(data)
    spec = importlib.util.spec_from_file_location(
        "u12_r0_binding_predecessor", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _PREDECESSOR["module"] = module
    _PREDECESSOR["digest"] = digest
    return module


# ---------------------------------------------------------------------------
# fixture CLI: live listing shape + lost terminal LF
# ---------------------------------------------------------------------------
class LiveShapeCli(FakeCli):
    """FakeCli with the live `issue children` shape (stages + unstaged)."""

    truncate_children = False

    def __call__(self, argv):
        argv = self._argv(argv)
        core = argv[1:]
        if core[:2] == ["issue", "children"]:
            self.commands.append(argv)
            return self._children_live(core)
        return super().__call__(argv)

    def _children_live(self, core):
        if self.truncate_children:
            return (0, "[]", "issue children response was truncated")
        parent = core[2]
        rows = copy.deepcopy(self.children.get(parent, []))
        staged = [r for r in rows if r.get("stage") is not None]
        unstaged = [r for r in rows if r.get("stage") is None]
        payload = {
            "stages": ([{"stage": 1, "total": len(staged), "done": 0,
                         "issues": staged}] if staged else []),
            "total": len(rows),
        }
        if len(unstaged) == 1:
            payload["unstaged"] = unstaged[0]
        elif unstaged:
            payload["unstaged"] = unstaged
        return self._json(payload)


class LostTerminalLfCli(LiveShapeCli):
    """One create that loses exactly the terminal LF, live-like activity."""

    def _create(self, core):
        super()._create(core)
        issue = self.issues[TARGET_ID]
        if issue["description"].endswith("\n"):
            issue["description"] = issue["description"][:-1]
        issue["creator_id"] = PUBLISHER_AGENT
        issue["creator_type"] = "agent"
        self.activities[TARGET_ID] = [{
            "id": "ACT-1", "action": "created", "actor_id": PUBLISHER_AGENT,
            "actor_type": "agent", "created_at": CLOCK, "details": {},
            "type": "activity"}]
        self.children[PARENT_ID] = [copy.deepcopy(issue)]
        return self._json(copy.deepcopy(issue))


class MangledTransportCli(LiveShapeCli):
    """A create whose stored body is not the exact persisted transport."""

    def _create(self, core):
        super()._create(core)
        issue = self.issues[TARGET_ID]
        issue["description"] = issue["description"][:-1] + "x"
        self.children[PARENT_ID] = [copy.deepcopy(issue)]
        return self._json(copy.deepcopy(issue))


# ---------------------------------------------------------------------------
# recovery fixtures
# ---------------------------------------------------------------------------
PROPOSED_EXECUTION_COMMIT = "29e0bde95e66de94527b680511289c81329ce04c"


def proposed_execution_resolver(commit, path):
    """Fixture-only proposed identity resolution: no live acceptance claimed.

    The proposed commit is explicitly a fixture proposal, never a Lead
    acceptance. The resolver returns exactly these executing adapter bytes so
    the operation exercises every digest/commit consistency predicate while
    the proof records `resolver = injected-fixture-proposal`.
    """
    if commit != PROPOSED_EXECUTION_COMMIT:
        raise LookupError("only the fixture-proposed commit is resolvable here")
    return Path(u12.__file__).resolve().read_bytes()


def make_decision(intent_id, *, ledger_prefix, original_create_pair,
                  receipt_status=u12.RECEIPT_STATUS_NOT_PERSISTED,
                  **overrides):
    decision = {
        "schema": u12.RECOVERY_DECISION_SCHEMA,
        "decision_id": "lead-accept-u12-r0-create-recovery-1",
        "disposition": u12.RECOVERY_DISPOSITION,
        "scope": u12.RECOVERY_SCOPE,
        "intent_id": intent_id,
        "expected_target_id": TARGET_ID,
        "expected_intent_revision": 1,
        "expected_target_revision": 1,
        "expected_creator_id": PUBLISHER_AGENT,
        "predecessor_commit": u12.PREDECESSOR_ADAPTER_COMMIT,
        "predecessor_adapter_digest": u12.PREDECESSOR_ADAPTER_DIGEST,
        "design_ref": u12.RECOVERY_DESIGN_REF,
        "design_digest": u12.RECOVERY_DESIGN_DIGEST,
        "evidence_decision_ref": u12.EVIDENCE_DECISION_REF,
        "evidence_decision_digest": u12.EVIDENCE_DECISION_DIGEST,
        "original_receipt_body_status": receipt_status,
        "receipt_limit_scope": (
            u12.RECEIPT_LIMIT_SCOPE
            if receipt_status == u12.RECEIPT_STATUS_NOT_PERSISTED else None),
        "ledger_prefix": dict(ledger_prefix),
        "original_create_pair": dict(original_create_pair),
        "accepted_execution": {
            "commit": PROPOSED_EXECUTION_COMMIT,
            "adapter_digest": u12.adapter_digest(),
        },
        "approval_ref": "multica://comment/01a08f58-0168-75ca-9bd7-9528b143094c",
        "approved_by": "01 Engineering Lead",
        "approved_at": CLOCK,
    }
    decision.update(overrides)
    decision["decision_digest"] = u12.digest(
        {k: v for k, v in decision.items() if k != "decision_digest"})
    return decision


class RecoveryFixture:
    """An intent genuinely recorded and created by the predecessor bytes."""

    def __init__(self, tmp: Path, *, cli=None, authority_reader="default",
                 blob_reader=None):
        self.tmp = Path(tmp)
        self.cli = cli if cli is not None else LostTerminalLfCli()
        self.store = o2.DurableIntentStore(self.tmp / "ledger.jsonl")
        self.old = load_predecessor_module()
        base_reader = (blob_reader if blob_reader is not None
                       else u12._git_blob_reader(u12.ROOT))
        self.artifact_reads: list = []

        def counting(commit, path):
            self.artifact_reads.append((commit, path))
            return base_reader(commit, path)

        self.blob_reader = counting
        self.old_factory = self.old.build_r0b_factory(
            self.store, runner=self.cli, artifact_blob_reader=counting)
        self.spec, self.intent_id = make_spec()
        context = creation_package()
        self.old_factory.record_creation_intent(
            creation_context={"result": context["result"],
                              "request": context["request"],
                              "self_check": context["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=self.spec,
            authority="Human U12 approval + YZT-83 design",
            actor=DISPATCHER, source_run=SOURCE_RUN,
            intent_id=self.intent_id)
        self.create_result = self.old_factory.create_target_once(
            self.intent_id, actor=DISPATCHER)
        self.ledger_prefix, self.original_create_pair = self._audit_pins()
        self.authority_reads: list = []
        if authority_reader == "default":
            authority = u12.ReadinessManifestAuthorityReader()
        else:
            authority = authority_reader
        if authority is not None:
            authority = _CountingAuthority(authority, self.authority_reads)
        self.factory = u12.build_r0b_factory(
            self.store, runner=u12.cfs.construct_simulation_entry(self.cli),
            artifact_blob_reader=counting,
            authority_reader=authority,
            execution_blob_resolver=proposed_execution_resolver,
            require_findings_source=False)
        self.base_decision = self.decision()

    def _audit_pins(self) -> tuple:
        """The audited prefix and original pair the Lead disposition pins."""
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

    # -- helpers -------------------------------------------------------------
    def decision(self, **overrides):
        overrides.setdefault("ledger_prefix", self.ledger_prefix)
        overrides.setdefault("original_create_pair", self.original_create_pair)
        return make_decision(self.intent_id, **overrides)

    def recover(self, **overrides):
        kwargs = {"expected_target_id": TARGET_ID,
                  "recovery_decision": self.decision(), "actor": DISPATCHER,
                  "execution_commit": PROPOSED_EXECUTION_COMMIT}
        kwargs.update(overrides)
        return self.factory.recover_created_target(self.intent_id, **kwargs)

    def arm(self, **overrides):
        kwargs = {"actor": DISPATCHER,
                  "current_request": self.current_request(),
                  "current_findings": []}
        kwargs.update(overrides)
        return self.factory.arm(self.intent_id, **kwargs)

    def trigger(self, **overrides):
        kwargs = {"actor": DISPATCHER,
                  "current_request": self.current_request(),
                  "current_findings": []}
        kwargs.update(overrides)
        return self.factory.trigger(self.intent_id, **kwargs)

    def current_request(self):
        issue = self.cli.issues[TARGET_ID]
        return execution_context_for(self.cli)["request"]

    def binding(self):
        return self.store.get(
            self.intent_id)["fields"][u12.R0B_FIELD]

    def ledger_bytes(self):
        return (self.tmp / "ledger.jsonl").read_bytes()

    def events_named(self, name):
        return [e for e in self.store.get(self.intent_id)["events"]
                if e.get("name") == name]


class _CountingAuthority:
    def __init__(self, inner, log):
        self.inner = inner
        self.log = log

    def read(self, *, path=u12.AUTHORITY_ARTIFACT_PATH):
        self.log.append(path)
        return self.inner.read(path=path)


def assert_zero_native_writes(case: unittest.TestCase, cli) -> None:
    """No native write beyond the fixture's one historical create."""
    case.assertEqual(len(cli.commands_of(["issue", "create"])), 1)
    for prefix in WRITE_PREFIXES[1:]:
        case.assertEqual(cli.commands_of(prefix), [], prefix)


def assert_only_reads(case: unittest.TestCase, commands) -> None:
    for argv in commands:
        core = argv[1:]
        case.assertTrue(any(core[:len(p)] == p for p in READ_PREFIXES),
                        f"non-read command on recovery window: {core}")


# ---------------------------------------------------------------------------
# group 1: exact observed single-terminal-LF recovery
# ---------------------------------------------------------------------------
class ExactLfRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = RecoveryFixture(Path(self.tmp.name))

    def test_fixture_is_the_exact_live_shape(self):
        fx = self.fx
        self.assertEqual(fx.create_result["status"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        stored = fx.binding()["creation_spec"]
        self.assertEqual(stored, fx.spec)
        self.assertEqual(stored["body"], fx.spec["body"])
        self.assertTrue(stored["body"].endswith("\n"))
        self.assertEqual(fx.cli.issues[TARGET_ID]["description"],
                         stored["body"][:-1])
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_CREATE_AMBIGUOUS)

    def test_exact_lf_recovery_binds_with_zero_native_writes(self):
        fx = self.fx
        ledger_before = fx.ledger_bytes()
        commands_before = len(fx.cli.commands)
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        self.assertEqual(result["issue_id"], TARGET_ID)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["side_effects"], 0)
        self.assertEqual(result["next_action"], u12.RECOVERY_OWNERSHIP_NEXT)
        assert_only_reads(self, fx.cli.commands[commands_before:])
        assert_zero_native_writes(self, fx.cli)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        ledger_after = fx.ledger_bytes()
        self.assertTrue(ledger_after.startswith(ledger_before))
        intent = fx.store.get(fx.intent_id)
        self.assertEqual(intent["state"], o2.S_TARGET_BOUND)
        binding = intent["fields"][u12.R0B_FIELD]
        self.assertEqual(binding["contract_version"], u12.CONTRACT_VERSION)
        self.assertEqual(binding["adapter_digest"], u12.PREDECESSOR_ADAPTER_DIGEST)
        self.assertEqual(binding["creation_spec"], fx.spec)
        self.assertEqual(binding["creation_context"],
                         fx.store.get(fx.intent_id)["fields"][
                             u12.R0B_FIELD]["creation_context"])
        self.assertEqual(binding["target_binding"]["issue_id"], TARGET_ID)
        proof = binding["recovery_proof"]
        self.assertEqual(proof["proof_digest"],
                         u12.digest({k: v for k, v in proof.items()
                                     if k != "proof_digest"}))
        transport = proof["transport"]
        self.assertEqual(transport["profile"], u12.TRANSPORT_PROFILE)
        self.assertEqual(transport["transformation"],
                         "remove-single-terminal-lf")
        self.assertEqual(transport["effective_transport_body_digest"],
                         u12._sha256_utf8(fx.spec["body"][:-1]))
        self.assertEqual(proof["observed"]["relation"],
                         "single-terminal-lf-removed")
        self.assertTrue(proof["observed"]["removed_terminal_lf"])
        self.assertEqual(proof["intent_revision_before"], 1)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 1)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_BOUND)), 1)
        self.assertEqual(len(intent["transitions"]), 2)
        self.assertEqual(intent["transitions"][-1]["to"], o2.S_TARGET_BOUND)
        execution = binding["execution_binding"]
        self.assertEqual(execution["adapter_digest"], u12.adapter_digest())
        self.assertEqual(execution["original_adapter_digest"],
                         u12.PREDECESSOR_ADAPTER_DIGEST)
        self.assertEqual(execution["transition_revision"], 2)
        audit = fx.factory.validate(fx.intent_id)
        self.assertEqual(audit["contract_version"], u12.CONTRACT_VERSION)
        self.assertEqual(audit["state"], o2.S_TARGET_BOUND)

    def test_recovery_is_immutable_and_append_only(self):
        fx = self.fx
        fx.recover()
        records_before = fx.ledger_bytes()
        result = fx.recover()
        self.assertTrue(result["replayed"])
        self.assertEqual(result["issue_id"], TARGET_ID)
        self.assertEqual(fx.ledger_bytes(), records_before)
        assert_zero_native_writes(self, fx.cli)


# ---------------------------------------------------------------------------
# group 2: prospective transport preparation and unsupported forms
# ---------------------------------------------------------------------------
class TransportProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_relation_matrix_matches_the_accepted_design(self):
        base = ("alpha\n\nIntent marker: R0BI-0123456789abcdef\n"
                "Intent: DI-0123456789abcdef")
        cases = (
            ("exact", base, base, True, "exact"),
            ("one_final_lf_removed", base + "\n", base, True,
             "single-terminal-lf-removed"),
            ("repeated_terminal_lf", base + "\n\n", base, False, None),
            ("trailing_space", base + " \n", base, False, None),
            ("trailing_tab", base + "\t\n", base, False, None),
            ("crlf_plus_trim", base + "\r\n", base, False, None),
            ("leading_whitespace", base + "\n", " " + base, False, None),
            ("interior_change", base + "\n", base.replace("alpha", "alphb"),
             False, None),
            ("marker_line_change", base + "\n", base.replace("R0BI", "R1BI"),
             False, None),
            ("space_retained_before_removed_lf", base + " \n", base + " ",
             False, None),
        )
        for label, source, observed, accepted, relation in cases:
            result = u12.single_terminal_lf_relation(source, observed)
            self.assertEqual(result["accepted"], accepted, label)
            if accepted:
                self.assertEqual(result["relation"], relation, label)

    def test_prepare_transport_refusals(self):
        for source in ("body\n\n", "body\r\n", "body \n", "body\t\n",
                       "body \r", "body\x0b"):
            with self.assertRaises(u12.R0BValidationRefused, msg=repr(source)):
                u12.prepare_transport_body(source)
        with self.assertRaises(u12.R0BValidationRefused):
            u12.prepare_transport_body("body\n", profile="auto/9")
        prepared = u12.prepare_transport_body("body\n")
        self.assertEqual(prepared["transport_body"], "body")
        self.assertEqual(prepared["transformation"],
                         "remove-single-terminal-lf")
        self.assertEqual(prepared["source_raw_digest"],
                         u12._sha256_utf8("body\n"))
        identity = u12.prepare_transport_body("body")
        self.assertEqual(identity["transport_body"], "body")
        self.assertEqual(identity["transformation"], "identity")

    def _prospective(self, leading="Prospective body.", **spec_overrides):
        cli = LiveShapeCli()
        store = o2.DurableIntentStore(self.root / "prospective-ledger.jsonl")
        factory = u12.build_r0b_factory(
            store, runner=u12.cfs.construct_simulation_entry(cli),
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            require_findings_source=False)
        spec, intent_id = make_spec()
        source = (leading + "\n\nIntent marker: " + spec["marker"] +
                  "\nIntent: " + intent_id + "\n")
        spec.pop("body")
        spec.pop("body_digest")
        spec["source_body"] = source
        spec["transport_profile"] = u12.TRANSPORT_PROFILE
        spec.update(spec_overrides)
        context = creation_package()
        factory.record_creation_intent(
            creation_context={"result": context["result"],
                              "request": context["request"],
                              "self_check": context["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=spec, authority="test authority", actor=DISPATCHER,
            source_run=SOURCE_RUN, intent_id=intent_id)
        result = factory.create_target_once(intent_id, actor=DISPATCHER)
        return cli, store, factory, intent_id, result, source

    def test_prospective_input_persists_and_sends_no_final_lf(self):
        cli, store, factory, intent_id, result, source = self._prospective()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        stored = store.get(intent_id)["fields"][u12.R0B_FIELD]["creation_spec"]
        self.assertEqual(stored["body"], source[:-1])
        self.assertFalse(stored["body"].endswith("\n"))
        self.assertEqual(stored["body_digest"], u12.digest_text_lf(source[:-1]))
        self.assertEqual(cli.issues[TARGET_ID]["description"], source[:-1])
        prep = stored["transport_preparation"]
        self.assertEqual(prep["profile"], u12.TRANSPORT_PROFILE)
        self.assertEqual(prep["transformation"], "remove-single-terminal-lf")
        self.assertEqual(prep["source_raw_digest"], u12._sha256_utf8(source))
        self.assertEqual(prep["source_lf_digest"], u12.digest_text_lf(source))
        self.assertEqual(prep["transport_body_digest"],
                         u12._sha256_utf8(source[:-1]))
        self.assertEqual(len(cli.commands_of(["issue", "create"])), 1)

    def test_exact_no_normalization_create_still_works(self):
        cli = LiveShapeCli()
        store = o2.DurableIntentStore(self.root / "legacy-ledger.jsonl")
        factory = u12.build_r0b_factory(
            store, runner=u12.cfs.construct_simulation_entry(cli),
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            require_findings_source=False)
        spec, intent_id = make_spec()
        context = creation_package()
        factory.record_creation_intent(
            creation_context={"result": context["result"],
                              "request": context["request"],
                              "self_check": context["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=spec, authority="test authority", actor=DISPATCHER,
            source_run=SOURCE_RUN, intent_id=intent_id)
        result = factory.create_target_once(intent_id, actor=DISPATCHER)
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        self.assertEqual(cli.issues[TARGET_ID]["description"], spec["body"])
        stored = store.get(intent_id)["fields"][u12.R0B_FIELD]["creation_spec"]
        self.assertNotIn("transport_preparation", stored)

    def test_unsupported_source_is_refused_before_persistence(self):
        cli = LiveShapeCli()
        store = o2.DurableIntentStore(self.root / "refused-ledger.jsonl")
        factory = u12.build_r0b_factory(
            store, runner=u12.cfs.construct_simulation_entry(cli), require_findings_source=False)
        spec, intent_id = make_spec()
        spec.pop("body")
        spec.pop("body_digest")
        spec["source_body"] = "body with trailing space \n"
        spec["transport_profile"] = u12.TRANSPORT_PROFILE
        context = creation_package()
        with self.assertRaises(u12.R0BValidationRefused):
            factory.record_creation_intent(
                creation_context={"result": context["result"],
                                  "request": context["request"],
                                  "self_check": context["self_check"],
                                  "source_task_id": SOURCE_RUN},
                creation_spec=spec, authority="test authority",
                actor=DISPATCHER, source_run=SOURCE_RUN,
                intent_id=intent_id)
        self.assertEqual(cli.commands_of(["issue", "create"]), [])

    def test_transport_mutation_by_platform_is_create_ambiguous(self):
        cli = MangledTransportCli()
        store = o2.DurableIntentStore(self.root / "mangled-ledger.jsonl")
        factory = u12.build_r0b_factory(
            store, runner=u12.cfs.construct_simulation_entry(cli),
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            require_findings_source=False)
        spec, intent_id = make_spec()
        source = spec["body"]
        spec.pop("body")
        spec.pop("body_digest")
        spec["source_body"] = source
        spec["transport_profile"] = u12.TRANSPORT_PROFILE
        context = creation_package()
        factory.record_creation_intent(
            creation_context={"result": context["result"],
                              "request": context["request"],
                              "self_check": context["self_check"],
                              "source_task_id": SOURCE_RUN},
            creation_spec=spec, authority="test authority", actor=DISPATCHER,
            source_run=SOURCE_RUN, intent_id=intent_id)
        result = factory.create_target_once(intent_id, actor=DISPATCHER)
        self.assertEqual(result["status"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(len(cli.commands_of(["issue", "create"])), 1)
        self.assertEqual(cli.commands_of(["issue", "rerun"]), [])


# ---------------------------------------------------------------------------
# group 3: live target / evidence / material refusals
# ---------------------------------------------------------------------------
class RecoveryRefusalMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, **kwargs):
        return RecoveryFixture(Path(self.tmp.name), **kwargs)

    def assert_refused(self, fx, result=None, reason=None):
        if result is None:
            result = fx.recover()
        self.assertEqual(result["status"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(result["outcome"], "RECOVERY_REFUSED")
        self.assertEqual(result["external_writes"], 0)
        if reason is not None:
            self.assertEqual(result["reason"], reason)
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        assert_zero_native_writes(self, fx.cli)

    def test_wrong_target_refused(self):
        fx = self.make()
        other = "01a0bbbb-0000-7000-8000-00000000ffff"
        result = fx.recover(expected_target_id=other,
                            recovery_decision=fx.decision(
                                expected_target_id=other))
        self.assert_refused(fx, result, u12.REASON_RECOVERY_IDENTITY)

    def test_title_mismatch_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["title"] = "different title"
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_parent_mismatch_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["parent_issue_id"] = \
            "01a0cccc-0000-7000-8000-00000000ffff"
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_project_mismatch_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["project_id"] = "other-project"
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_priority_mismatch_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["priority"] = "low"
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_creator_mismatch_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["creator_id"] = TARGET_AGENT
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_moving_target_revision_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["revision"] = 2
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_assignee_present_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["assignee_id"] = TARGET_AGENT
        fx.cli.issues[TARGET_ID]["assignee_type"] = "agent"
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_non_backlog_status_refused(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["status"] = "todo"
        fx.cli.issues[TARGET_ID]["status_category"] = "todo"
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_TARGET)

    def test_duplicate_identity_candidates_refused(self):
        fx = self.make()
        duplicate = copy.deepcopy(fx.cli.children[PARENT_ID][0])
        duplicate["id"] = "01a0dddd-0000-7000-8000-00000000ffff"
        fx.cli.children[PARENT_ID].append(duplicate)
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_IDENTITY)

    def test_missing_identity_candidate_refused(self):
        fx = self.make()
        marker_line = "Intent marker: " + fx.spec["marker"]
        for holder in (fx.cli.issues[TARGET_ID],
                       fx.cli.children[PARENT_ID][0]):
            holder["description"] = holder["description"].replace(
                marker_line, "Intent marker: R0BI-0000000000000000")
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_IDENTITY)

    def test_marker_substring_alone_is_not_identity(self):
        fx = self.make()
        marker = fx.spec["marker"]
        fx.cli.issues[TARGET_ID]["description"] = (
            "the marker " + marker + " appears inline only\n")
        fx.cli.children[PARENT_ID][0]["description"] = \
            fx.cli.issues[TARGET_ID]["description"]
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_IDENTITY)

    def test_nonempty_runs_refused(self):
        fx = self.make()
        fx.cli.runs[TARGET_ID].append({
            "id": "RUN-X", "issue_id": TARGET_ID, "agent_id": TARGET_AGENT,
            "status": "queued", "attempt": 1})
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_EFFECT)

    def test_extra_comment_refused(self):
        fx = self.make()
        fx.cli.comments[TARGET_ID].append({
            "id": "CMT-X", "content": "unexpected", "created_at": CLOCK,
            "updated_at": CLOCK, "parent_id": None, "author_id": PUBLISHER_AGENT,
            "author_type": "agent", "source_task_id": PUBLISHER_RUN,
            "resolved_at": None, "revision": 1, "issue_id": TARGET_ID,
            "type": "comment", "attachments": [], "reactions": []})
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_EFFECT)

    def test_prior_ownership_attempt_refused(self):
        fx = self.make()
        fx.store.append_event(fx.intent_id, u12.E_OWNERSHIP_ISSUING,
                              actor=DISPATCHER, data={})
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_EFFECT)

    def test_prior_publication_attempt_refused(self):
        fx = self.make()
        fx.store.append_event(fx.intent_id, u12.E_PUBLICATION_ISSUING,
                              actor=DISPATCHER, data={})
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_EFFECT)

    def test_unknown_effect_refused(self):
        fx = self.make()
        fx.store.append_event(fx.intent_id, "some_unknown_effect",
                              actor=DISPATCHER, data={})
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_EFFECT)

    def test_second_create_attempt_refused(self):
        fx = self.make()
        fx.store.append_event(fx.intent_id, u12.E_CREATE_ISSUING,
                              actor=DISPATCHER, data={})
        self.assert_refused(fx, reason=u12.REASON_RECOVERY_UNSUPPORTED)

    def test_truncated_comment_read_refused(self):
        fx = self.make()
        fx.cli.truncate_comment_list = True
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_UNAVAILABLE)

    def test_truncated_timeline_read_refused(self):
        fx = self.make()
        fx.cli.truncate_timeline = True
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_UNAVAILABLE)

    def test_truncated_run_read_refused(self):
        fx = self.make()
        fx.cli.truncate_runs = True
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_UNAVAILABLE)

    def test_truncated_children_read_refused(self):
        fx = self.make()
        fx.cli.truncate_children = True
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_UNAVAILABLE)

    def test_stale_artifact_refused(self):
        fx = self.make()
        fx.factory.artifact_blob_reader = lambda commit, path: b"changed bytes"
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_STALE)

    def test_missing_artifact_reader_refused(self):
        fx = self.make()
        fx.factory.artifact_blob_reader = None
        fx.factory.artifact_root = None
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_UNAVAILABLE)

    def test_superseded_authority_refused(self):
        altered = real_readiness_manifest()
        altered["generated_at"] = "2026-09-12T00:00:00Z"
        fx = self.make(authority_reader=FixedAuthorityReader(altered))
        self.assert_refused(fx, reason=u12.REASON_MATERIAL_STALE)

    def test_missing_authority_source_refused(self):
        fx = self.make(authority_reader=None)
        self.assert_refused(fx, reason=u12.REASON_PREFLIGHT_INPUT_MISSING)

    def test_moving_target_during_collection_refused(self):
        fx = self.make()
        original = fx.factory.reader.issue_get
        calls = {"n": 0}

        def drifting(issue_id):
            calls["n"] += 1
            data = original(issue_id)
            if calls["n"] >= 2:
                data = dict(data)
                data["revision"] = data["revision"] + 1
            return data

        fx.factory.reader.issue_get = drifting
        self.assert_refused(fx, reason=u12.REASON_MOVING_EVIDENCE)

    def test_refusal_never_changes_state_or_pins(self):
        fx = self.make()
        fx.cli.issues[TARGET_ID]["title"] = "different title"
        before = fx.ledger_bytes()
        result = fx.recover()
        self.assertEqual(result["outcome"], "RECOVERY_REFUSED")
        after = fx.ledger_bytes()
        self.assertTrue(after.startswith(before))
        binding = fx.binding()
        self.assertEqual(binding["adapter_digest"],
                         u12.PREDECESSOR_ADAPTER_DIGEST)
        self.assertNotIn("execution_binding", binding)
        self.assertEqual(binding["creation_spec"], fx.spec)
        proof = fx.recover()
        self.assertEqual(proof["outcome"], "RECOVERY_REFUSED")


# ---------------------------------------------------------------------------
# group 4: pins, proof fences and the actual old executor
# ---------------------------------------------------------------------------
class PinAndProofFenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make(self, **kwargs):
        return RecoveryFixture(Path(self.tmp.name), **kwargs)

    def tamper(self, fx, mutate):
        intent = fx.store.get(fx.intent_id)
        tampered = copy.deepcopy(intent)
        mutate(tampered["fields"][u12.R0B_FIELD])
        patcher = mock.patch.object(fx.store, "get", return_value=tampered)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_bare_changed_pin_refused_before_any_read(self):
        fx = self.make()
        self.tamper(fx, lambda binding: binding.__setitem__(
            "adapter_digest", "sha256:" + "1" * 64))
        commands_before = len(fx.cli.commands)
        with self.assertRaises(u12.R0BDowngradeRefused):
            fx.recover()
        self.assertEqual(len(fx.cli.commands), commands_before)
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_CREATE_AMBIGUOUS)

    def test_unknown_contract_version_refused(self):
        fx = self.make()
        self.tamper(fx, lambda binding: binding.__setitem__(
            "contract_version", "U12-R0B/9.9"))
        with self.assertRaises(u12.R0BContractError):
            fx.recover()

    def test_expected_intent_revision_mismatch_refused(self):
        fx = self.make()
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=fx.decision(
                expected_intent_revision=7))

    def test_decision_tamper_refused(self):
        fx = self.make()
        decision = fx.decision()
        decision["approved_by"] = "someone else"
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=decision)

    def test_decision_extra_field_refused(self):
        fx = self.make()
        decision = fx.decision()
        decision["wildcard"] = True
        decision["decision_digest"] = u12.digest(
            {k: v for k, v in decision.items() if k != "decision_digest"})
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=decision)

    def test_decision_design_digest_refused(self):
        fx = self.make()
        decision = fx.decision(design_digest="sha256:" + "0" * 64)
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=decision)

    def test_decision_wrong_predecessor_commit_refused(self):
        fx = self.make()
        decision = fx.decision(predecessor_commit="0" * 40)
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=decision)

    def test_decision_target_mismatch_refused(self):
        fx = self.make()
        other = "01a0bbbb-0000-7000-8000-00000000ffff"
        decision = fx.decision(expected_target_id=other)
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=decision)

    def test_new_reader_audits_old_history_but_refuses_execution(self):
        fx = self.make()
        audit = fx.factory.validate(fx.intent_id)
        self.assertEqual(audit["contract_version"],
                         u12.PREDECESSOR_CONTRACT_VERSION)
        self.assertEqual(audit["state"], o2.S_CREATE_AMBIGUOUS)
        with self.assertRaises(u12.R0BCompatibilityRefused):
            fx.factory.assign_ownership_once(fx.intent_id, actor=DISPATCHER)
        with self.assertRaises(u12.R0BCompatibilityRefused):
            fx.factory.create_target_once(fx.intent_id, actor=DISPATCHER)
        instruction = fx.factory.recover(fx.intent_id, actor=DISPATCHER)
        self.assertEqual(instruction["classification"],
                         "PREDECESSOR_CREATE_RECOVERY_REQUIRED")
        self.assertEqual(instruction["action"], "RECOVER_CREATED_TARGET")
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        assert_zero_native_writes(self, fx.cli)

    def test_recovered_record_executes_under_new_bytes_only(self):
        fx = self.make()
        fx.recover()
        audit = fx.factory.validate(fx.intent_id)
        self.assertEqual(audit["contract_version"], u12.CONTRACT_VERSION)
        self.assertEqual(audit["phase"], "TARGET_BOUND")
        ownership = fx.factory.assign_ownership_once(fx.intent_id,
                                                     actor=DISPATCHER)
        self.assertEqual(ownership["status"], o2.S_TARGET_BOUND)
        self.assertEqual(
            len(fx.cli.commands_of(["issue", "assign"])), 1)

    def test_tampered_proof_refused(self):
        fx = self.make()
        fx.recover()
        self.tamper(fx, lambda binding: binding["recovery_proof"].__setitem__(
            "target", {"issue_id": "01a0eeee-0000-7000-8000-00000000ffff"}))
        with self.assertRaises(u12.R0BValidationRefused):
            fx.factory.validate(fx.intent_id)

    def test_cross_intent_proof_refused(self):
        fx = self.make()
        fx.recover()

        def cross(binding):
            proof = binding["recovery_proof"]
            proof["intent_id"] = "DI-" + "9" * 16
            proof.pop("proof_digest")
            proof["proof_digest"] = u12.digest(proof)
            binding["execution_binding"]["recovery_proof_digest"] = \
                proof["proof_digest"]

        self.tamper(fx, cross)
        with self.assertRaises(u12.R0BValidationRefused):
            fx.factory.validate(fx.intent_id)

    def test_original_pin_cannot_be_relabeled(self):
        fx = self.make()
        fx.recover()
        self.tamper(fx, lambda binding: binding.__setitem__(
            "adapter_digest", u12.adapter_digest()))
        with self.assertRaises(u12.R0BValidationRefused):
            fx.factory.validate(fx.intent_id)

    def test_actual_old_executor_refuses_recovered_record(self):
        fx = self.make()
        fx.recover()
        self.assertEqual(_PREDECESSOR["digest"],
                         u12.PREDECESSOR_ADAPTER_DIGEST)
        commands_before = len(fx.cli.commands)
        with self.assertRaises(fx.old.R0BContractError):
            fx.old_factory.validate(fx.intent_id)
        with self.assertRaises(fx.old.R0BContractError):
            fx.old_factory.assign_ownership_once(fx.intent_id,
                                                 actor=DISPATCHER)
        with self.assertRaises(fx.old.R0BContractError):
            fx.old_factory.recover(fx.intent_id, actor=DISPATCHER)
        self.assertEqual(len(fx.cli.commands), commands_before)
        assert_zero_native_writes(self, fx.cli)
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_TARGET_BOUND)

    def test_frozen_strict_and_core_pins_reproduce(self):
        self.assertEqual(
            u12.adapter_digest(TOOLS / "u12_strict_receipt.py"),
            "sha256:f37ed0912ecbdc3944529cffc6d7a28bc9a41d1faba8cd1e2"
            "84eab6b0ccf4bed")
        # The accepted pin for tools/chandoff_intent.py is the pinned commit
        # blob; the approved YZT-84 publication-recovery exception binds the
        # executing bytes through the committed execution migration instead.
        blob = u12._git_blob_reader(TOOLS.parent)(
            "49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9",
            "tools/chandoff_intent.py")
        self.assertEqual(
            "sha256:" + hashlib.sha256(
                bytes(blob).replace(b"\r\n", b"\n")).hexdigest(),
            "sha256:0544046fa2ca97c12e6c48de574074df9de5715a950a"
            "d75a783cf31853037032")
        rebuilt = u12.build_artifact_dependency_digest()
        entries = rebuilt["entries"]
        self.assertEqual(entries["adapters/multica/u12-p0r/"
                                 "readiness-manifest.json"]["sha256"],
                         "sha256:ee6cb9a24580e651edc4c7af2d3b4df723622fa6a8"
                         "bc3f368c07916b0cea5da0")
        manifest = json.loads(
            (TOOLS.parent / "adapters/multica/u12-p0r/readiness-manifest.json")
            .read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_digest"],
                         "sha256:64a5c449a9639979cb6a87627c583683cb99fb0f"
                         "237ceff6634fc4b5c7e3bc93")
        self.assertEqual(
            u12.digest({k: v for k, v in manifest.items()
                        if k != "manifest_digest"}),
            manifest["manifest_digest"])


# ---------------------------------------------------------------------------
# group 5: crash, replay, concurrency, at-most-once
# ---------------------------------------------------------------------------
class CrashReplayConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = RecoveryFixture(Path(self.tmp.name))

    def test_crash_before_evidence_leaves_create_ambiguous(self):
        fx = self.fx
        original = fx.store.append_event

        def crash(intent_id, name, **kwargs):
            if name == u12.E_RECOVERY_EVIDENCE:
                raise o2.IntentError("simulated crash before evidence")
            return original(intent_id, name, **kwargs)

        with mock.patch.object(fx.store, "append_event", side_effect=crash):
            with self.assertRaises(o2.IntentError):
                fx.recover()
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(fx.events_named(u12.E_RECOVERY_EVIDENCE), [])
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 1)

    def test_crash_after_evidence_before_transition_recollects(self):
        fx = self.fx
        original = fx.store.transition
        calls = {"n": 0}

        def crash(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise o2.IntentError("simulated crash after evidence append")
            return original(*args, **kwargs)

        with mock.patch.object(fx.store, "transition", side_effect=crash):
            with self.assertRaises(o2.IntentError):
                fx.recover()
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 1)
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 2)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_BOUND)), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)

    def test_crash_after_transition_before_ack_replays(self):
        fx = self.fx
        original = fx.store.append_event

        def crash(intent_id, name, **kwargs):
            if name == u12.E_RECOVERY_BOUND:
                raise o2.IntentError("simulated crash before acknowledgement")
            return original(intent_id, name, **kwargs)

        with mock.patch.object(fx.store, "append_event", side_effect=crash):
            with self.assertRaises(o2.IntentError):
                fx.recover()
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_TARGET_BOUND)
        self.assertEqual(fx.events_named(u12.E_RECOVERY_BOUND), [])
        commands_before = len(fx.cli.commands)
        replay = fx.recover()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["issue_id"], TARGET_ID)
        self.assertEqual(len(fx.cli.commands), commands_before)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)

    def test_repeated_recovery_is_one_binding(self):
        fx = self.fx
        first = fx.recover()
        self.assertFalse(first["replayed"])
        records = len(fx.store.read_records())
        second = fx.recover()
        self.assertTrue(second["replayed"])
        self.assertEqual(len(fx.store.read_records()), records)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 1)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_BOUND)), 1)

    def test_different_target_and_decision_refuse_on_replay(self):
        fx = self.fx
        fx.recover()
        other = "01a0bbbb-0000-7000-8000-00000000ffff"
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(expected_target_id=other,
                       recovery_decision=fx.decision(
                           expected_target_id=other))
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=fx.decision(
                approved_at="2026-09-11T09:00:00Z"))

    def test_competing_lease_refuses_before_any_read(self):
        fx = self.fx
        fx.store.claim(fx.intent_id, "other-writer")
        commands_before = len(fx.cli.commands)
        with self.assertRaises(o2.LeaseHeldError):
            fx.recover()
        self.assertEqual(len(fx.cli.commands), commands_before)
        self.assertEqual(fx.events_named(u12.E_RECOVERY_EVIDENCE), [])
        self.assertEqual(fx.store.get(fx.intent_id)["state"],
                         o2.S_CREATE_AMBIGUOUS)

    def test_concurrent_recoverers_keep_one_binding(self):
        fx = self.fx
        path = fx.tmp / "ledger.jsonl"
        barrier = threading.Barrier(2)
        results: dict = {}

        def worker(name):
            barrier.wait()
            store = o2.DurableIntentStore(path)
            factory = u12.build_r0b_factory(
                store, runner=u12.cfs.construct_simulation_entry(fx.cli),
                artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
                authority_reader=u12.ReadinessManifestAuthorityReader(),
                execution_blob_resolver=proposed_execution_resolver,
                require_findings_source=False)
            try:
                results[name] = factory.recover_created_target(
                    fx.intent_id, expected_target_id=TARGET_ID,
                    recovery_decision=fx.decision(), actor=name,
                    execution_commit=PROPOSED_EXECUTION_COMMIT)
            except o2.IntentError as exc:
                results[name] = {"error": type(exc).__name__}

        threads = [threading.Thread(target=worker, args=(f"w{i}",))
                   for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        intent = fx.store.get(fx.intent_id)
        self.assertEqual(intent["state"], o2.S_TARGET_BOUND)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_EVIDENCE)), 1)
        self.assertEqual(len(fx.events_named(u12.E_RECOVERY_BOUND)), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        self.assertEqual(len(results), 2)
        replayed = [r for r in results.values() if r.get("replayed")]
        refused = [r for r in results.values() if r.get("error")]
        succeeded = [r for r in results.values()
                     if r.get("status") == o2.S_TARGET_BOUND
                     and not r.get("replayed")]
        self.assertLessEqual(len(replayed), 1)
        self.assertEqual(len(succeeded) + len(replayed) + len(refused), 2)

    def test_attempt_flags_are_never_reset(self):
        fx = self.fx
        fx.recover()
        creates = len(fx.cli.commands_of(["issue", "create"]))
        replay = fx.factory.create_target_once(fx.intent_id, actor=DISPATCHER)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["issue_id"], TARGET_ID)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), creates)
        instruction = fx.factory.recover(fx.intent_id, actor=DISPATCHER)
        self.assertEqual(instruction["classification"],
                         "POST_BIND_PRE_OWNERSHIP")
        self.assertEqual(instruction["action"], "RESUME_OWNERSHIP")
        self.assertFalse(instruction["performed"])


# ---------------------------------------------------------------------------
# group 6: post-recovery lifecycle through the existing path
# ---------------------------------------------------------------------------
class PostRecoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = RecoveryFixture(Path(self.tmp.name))
        self.recovered = self.fx.recover()

    def test_full_lifecycle_after_recovery_single_rerun(self):
        fx = self.fx
        self.assertEqual(self.recovered["status"], o2.S_TARGET_BOUND)
        owned = fx.factory.assign_ownership_once(fx.intent_id,
                                                 actor=DISPATCHER)
        self.assertEqual(owned["status"], o2.S_TARGET_BOUND)
        prepared = fx.factory.bind_execution_package(
            fx.intent_id, execution_context=execution_context_for(fx.cli),
            actor=DISPATCHER)
        self.assertEqual(prepared["status"], o2.S_HANDOFF_PREPARED)
        published = fx.factory.publish_handoff_once(
            fx.intent_id, actor=DISPATCHER, publisher_run_id=PUBLISHER_RUN,
            prepared_by="01 Engineering Lead", prepared_at=CLOCK)
        self.assertEqual(published["status"], o2.S_HANDOFF_PUBLISHED)
        armed = fx.arm()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        triggered = fx.trigger()
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(len(fx.cli.commands_of(["issue", "rerun"])), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)
        self.assertEqual(len(fx.cli.commands_of(["issue", "assign"])), 1)
        self.assertIn("--no-start",
                      fx.cli.commands_of(["issue", "assign"])[0])
        self.assertEqual(
            len(fx.cli.commands_of(["issue", "comment", "add"])), 1)
        checkpoints = [e["data"]["checkpoint"]
                       for e in fx.events_named(u12.E_PREFLIGHT)]
        self.assertEqual(checkpoints, ["ARM", "TRIGGER"])

    def test_drift_after_arm_prevents_trigger(self):
        fx = self.fx
        fx.factory.assign_ownership_once(fx.intent_id, actor=DISPATCHER)
        fx.factory.bind_execution_package(
            fx.intent_id, execution_context=execution_context_for(fx.cli),
            actor=DISPATCHER)
        fx.factory.publish_handoff_once(
            fx.intent_id, actor=DISPATCHER, publisher_run_id=PUBLISHER_RUN,
            prepared_by="01 Engineering Lead", prepared_at=CLOCK)
        armed = fx.arm()
        self.assertEqual(armed["status"], o2.S_TRIGGER_READY)
        fx.cli.issues[TARGET_ID]["description"] = "drifted after arm"
        result = fx.trigger()
        self.assertEqual(result["status"], o2.S_REFRESH_REQUIRED)
        self.assertEqual(fx.cli.commands_of(["issue", "rerun"]), [])

    def test_ambiguous_trigger_reconciles_without_reissue(self):
        fx = self.fx
        fx.factory.assign_ownership_once(fx.intent_id, actor=DISPATCHER)
        fx.factory.bind_execution_package(
            fx.intent_id, execution_context=execution_context_for(fx.cli),
            actor=DISPATCHER)
        fx.factory.publish_handoff_once(
            fx.intent_id, actor=DISPATCHER, publisher_run_id=PUBLISHER_RUN,
            prepared_by="01 Engineering Lead", prepared_at=CLOCK)
        self.assertEqual(fx.arm()["status"], o2.S_TRIGGER_READY)
        fx.cli._rerun = lambda core: (
            0, json.dumps({"run": {"id": "RUN-1", "issue_id": core[2],
                                   "agent_id": TARGET_AGENT,
                                   "status": "queued"}}), "")
        first = fx.trigger()
        self.assertEqual(first["status"], o2.S_TRIGGER_AMBIGUOUS)
        second = fx.trigger()
        self.assertEqual(second["status"], o2.S_TRIGGER_AMBIGUOUS)
        self.assertTrue(second["replayed"])
        self.assertEqual(len(fx.cli.commands_of(["issue", "rerun"])), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
