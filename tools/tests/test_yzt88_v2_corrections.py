#!/usr/bin/env python3
"""YZT-88 V2 bounded correction tests.

Unique test IDs map to expected effect/refusal. Synthetic fixtures and fake
transport only. Does not import ProductionLedgerNonWriteTests or
test_u12_p0r_evidence. Does not read D:/AI/multica-memory/runtime/v1.1/findings.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
REPO = TOOLS.parent
for _p in (TOOLS, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import chandoff_adapter as adapter  # noqa: E402
import chandoff_assignment as asm  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_findings_source as cfs  # noqa: E402
import chandoff_mention as men  # noqa: E402
import u12_r0_binding as u12  # noqa: E402
from yzt66_v2_synthetic import (  # noqa: E402
    AUTHORITY_TEXT, PROJECT, ROLE, TASK_REF, SyntheticAuthorityCli,
    build_v2_fixture, synthetic_blocking_finding, synthetic_open_finding,
)
from test_handoff_assignment import (  # noqa: E402
    AGENT_SE, COMPOSE, CLOCK, FakeMultica, PARENT_UUID, base_spec,
)
from test_handoff_findings_source import build_envelope, sample_request  # noqa: E402

SKILL = REPO / "skills" / "multica-context-handoff"
CREATED_TASK_REF = "multica://issue/YZT-9001"


class InertRecordingSpy:
    """Class-instance spy. Never subprocesses. Test-only."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return 0, "{}", ""


class MarkerMixedWrapper:
    """Ordinary configuration bypass: markers + inner live callable."""

    simulation_transport = True
    explicit_simulation_entry = True

    def __init__(self, inner):
        self.inner = inner
        self.runner = inner

    def __call__(self, argv):
        return self.inner(argv)


def _bound_source(fx, binding, *, extra_task_refs=()):
    trusted = copy.deepcopy(fx["trusted"]) if extra_task_refs else fx["trusted"]
    bound = dict(binding)
    if extra_task_refs:
        allowed = copy.deepcopy(bound["allowed"])
        for ref in extra_task_refs:
            if ref not in allowed["task_refs"]:
                allowed["task_refs"].append(ref)
        bound["allowed"] = allowed
        for entry in trusted["sources"].values():
            for ref in extra_task_refs:
                if ref not in entry["allowed"]["task_refs"]:
                    entry["allowed"]["task_refs"].append(ref)
    return cfs.BoundFindingsSource(
        bound,
        resolver=cfs.AuthenticatedCommentResolver(SyntheticAuthorityCli()),
        project_id=PROJECT, trusted=trusted, require_trusted=True), trusted, bound


class SameNameContentDriftTests(unittest.TestCase):
    TEST_ID = "YZT88-V2-C1-SAMENAME"

    def test_same_name_rewrite_during_read_is_refused(self):
        """YZT88-V2-C1-SAMENAME: in-read same-name byte change → changed."""
        fx = build_v2_fixture()
        path = fx["open_root"] / f"{fx['open_finding_id']}.json"

        class RewriteReader(cfs.FilesystemReader):
            def __init__(self):
                self.reads = 0

            def read_bytes(self, p):
                data = super().read_bytes(p)
                self.reads += 1
                if Path(p) == path and self.reads >= 2:
                    edited = synthetic_open_finding()
                    edited["summary"] = "SYNTHETIC rewritten in place"
                    return (json.dumps(edited, ensure_ascii=False, indent=2)
                            + "\n").encode("utf-8")
                return data

        source = cfs.BoundFindingsSource(
            fx["open_binding"], resolver=cfs.AuthenticatedCommentResolver(
                SyntheticAuthorityCli()),
            project_id=PROJECT, reader=RewriteReader())
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_changed")
        self.assertIn("same-name", ctx.exception.message)


class TrustedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fx = build_v2_fixture()
        self.cli = SyntheticAuthorityCli()
        self.resolver = cfs.AuthenticatedCommentResolver(self.cli)

    def test_missing_source_is_never_empty_success(self):
        """YZT88-V2-C1-MISSING: absent root → findings_source_missing."""
        missing = Path(self.fx["dir"]) / "does-not-exist"
        binding = dict(self.fx["open_binding"])
        binding["root"] = str(missing)
        trusted = json.loads(self.fx["trusted_map_file"].read_text(
            encoding="utf-8"))
        trusted["sources"][cfs.V2_SYNTHETIC_SOURCE_ID]["root"] = str(missing)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(
                binding, resolver=self.resolver, project_id=PROJECT,
                trusted=trusted, require_trusted=True).read(
                boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_missing")

    def test_wrong_project_task_role_refused(self):
        """YZT88-V2-C1-IDENTITY: wrong project/task/role → unbound."""
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=self.resolver,
            project_id=PROJECT, trusted=self.fx["trusted"],
            require_trusted=True)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(
                self.fx["open_binding"], resolver=self.resolver,
                project_id="other-project", trusted=self.fx["trusted"],
                require_trusted=True)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE",
                        task_ref="multica://issue/YZT-99", role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role="qa")
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_caller_selected_arbitrary_root_refused(self):
        """YZT88-V2-C1-ARBITRARY-ROOT: unmapped root → unbound."""
        other = Path(self.fx["dir"]) / "caller-root"
        other.mkdir()
        binding = dict(self.fx["open_binding"])
        binding["root"] = str(other)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(
                binding, resolver=self.resolver, project_id=PROJECT,
                trusted=self.fx["trusted"], require_trusted=True)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertIn("trusted mapped physical root", ctx.exception.message)

    def test_source_id_text_is_not_ownership_proof(self):
        """YZT88-V2-C1-SOURCEID: unknown source_id → unbound."""
        binding = dict(self.fx["open_binding"])
        binding["source_id"] = "arbitrary-caller-source"
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.validate_binding(binding, project_id=PROJECT,
                                 trusted=self.fx["trusted"],
                                 require_trusted=True)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_relevant_open_finding_is_visible(self):
        """YZT88-V2-C1-OPEN: synthetic open Finding remains visible."""
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=self.resolver,
            project_id=PROJECT, trusted=self.fx["trusted"],
            require_trusted=True)
        snap = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(snap["open_ids"], [self.fx["open_finding_id"]])
        self.assertEqual(snap["observation"]["snapshot_digest"],
                         self.fx["open_snapshot_digest"])
        self.assertIn("SYNTHETIC", snap["records"][0]["summary"])

    def test_empty_store_is_complete_not_missing(self):
        """YZT88-V2-C1-EMPTY: empty directory is a complete zero inventory."""
        source = cfs.BoundFindingsSource(
            self.fx["empty_binding"], resolver=self.resolver,
            project_id=PROJECT, trusted=self.fx["trusted"],
            require_trusted=True)
        snap = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(snap["observation"]["total_records"], 0)
        self.assertEqual(snap["inventory"], [])
        self.assertEqual(snap["observation"]["snapshot_digest"],
                         self.fx["empty_snapshot_digest"])

    def test_corrupt_and_denied_are_refusals(self):
        """YZT88-V2-C1-CORRUPT: malformed JSON / denied read → invalid/unreadable."""
        root = Path(tempfile.mkdtemp(prefix="yzt88-corrupt-")) / "findings"
        root.mkdir(parents=True)
        (root / "FIND-WIMG-YZT66-000001.json").write_text(
            "{not-json", encoding="utf-8")
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.read_snapshot(root, source_id="x", boundary="PREPARE")
        self.assertEqual(ctx.exception.code, "findings_source_invalid")

        class Denied(cfs.FilesystemReader):
            def scandir(self, r):
                raise PermissionError("denied by test")

        empty = Path(tempfile.mkdtemp(prefix="yzt88-denied-")) / "findings"
        empty.mkdir()
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.read_snapshot(empty, source_id="x", reader=Denied(),
                              boundary="PREPARE")
        self.assertEqual(ctx.exception.code, "findings_source_unreadable")


class ProductionSimulationBypassTests(unittest.TestCase):
    def test_fake_attribute_class_name_wrapper_zero_effects(self):
        """YZT88-V2-C2-FORGERY: markers on production ctor issue zero argv."""
        issued = []

        def live_runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        class FixtureRunner:  # noqa: N801 - forged production class name
            simulation_transport = True

            def __init__(self, inner):
                self.inner = inner
                self.runner = inner

            def __call__(self, argv):
                return self.inner(argv)

        forged = FixtureRunner(live_runner)
        result = asm.run_assignment_handoff(
            {"title": "V2 forgery drill",
             "description": "Markers must not grant a production exemption.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=forged, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-v2-forge")
        self.assertEqual(result["terminal_status"], asm.INVALID_INPUT)
        self.assertEqual(issued, [])

    def test_mention_markers_zero_effects(self):
        """YZT88-V2-C2-FORGERY-MEN: mention production ctor ignores markers."""
        issued = []

        def live_runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        live_runner.simulation_transport = True
        result = men.run_mention_handoff(
            {"issue_id": "22222222-0000-0000-0000-000000000065",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=live_runner, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-v2-forge-m",
            stage="ready")
        self.assertEqual(result["terminal_status"], men.INVALID_INPUT)
        self.assertEqual(issued, [])

    def test_r0_factory_does_not_infer_from_markers(self):
        """YZT88-V2-C2-R0: FakeCli markers no longer drop the source gate."""
        class Marked:
            simulation_transport = True

            def __call__(self, argv):
                raise AssertionError("must not run")

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        factory = u12.build_r0b_factory(
            __import__("chandoff_intent", fromlist=["DurableIntentStore"])
            .DurableIntentStore(Path(tmp.name) / "ledger.jsonl"),
            runner=Marked())
        self.assertTrue(factory.require_findings_source)
        with self.assertRaises(cfs.FindingsSourceRefusal):
            factory._gate_findings_effects("R0 trigger")

    def test_explicit_simulation_entry_still_gates_live_transport(self):
        """YZT88-V2-C2-EXPLICIT: legacy_fixture=True + live runner refuses."""
        issued = []

        def live_runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        result = asm.run_assignment_handoff(
            {"title": "Explicit fixture on live transport",
             "description": "legacy_fixture cannot ride a live runner.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=live_runner, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-v2-legacy",
            legacy_fixture=True)
        self.assertEqual(result["terminal_status"], asm.INVALID_INPUT)
        self.assertEqual(issued, [])

    def test_legacy_fixture_marker_mixed_wrapper_zero_effects(self):
        """YZT88-V2-C2-MIXED: booleans + markers + inner live → refuse."""
        issued = []

        def live_runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        mixed = MarkerMixedWrapper(live_runner)
        self.assertFalse(cfs.is_simulation_transport(mixed))
        result = asm.run_assignment_handoff(
            {"title": "Mixed wrapper drill",
             "description": "Markers plus inner live must not grant exemption.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=mixed, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-v2-mixed",
            legacy_fixture=True)
        self.assertEqual(result["terminal_status"], asm.INVALID_INPUT)
        self.assertEqual(issued, [])
        men_result = men.run_mention_handoff(
            {"issue_id": "22222222-0000-0000-0000-000000000065",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=mixed, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-v2-mixed-m",
            legacy_fixture=True, stage="ready")
        self.assertEqual(men_result["terminal_status"], men.INVALID_INPUT)
        self.assertEqual(issued, [])

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        factory = u12.build_r0b_factory(
            __import__("chandoff_intent", fromlist=["DurableIntentStore"])
            .DurableIntentStore(Path(tmp.name) / "ledger.jsonl"),
            runner=mixed, require_findings_source=False)
        with self.assertRaises(cfs.FindingsSourceRefusal):
            factory._gate_findings_effects("R0 trigger")
        self.assertEqual(issued, [])

    def test_simulation_entry_refuses_live_inner(self):
        """YZT88-V2-C2-LIVEINNER: SimulationEntry cannot forward live argv."""
        issued = []

        def live_runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        with self.assertRaises(TypeError):
            cfs.construct_simulation_entry(live_runner)
        with self.assertRaises(TypeError):
            cfs.construct_simulation_entry(MarkerMixedWrapper(live_runner))
        self.assertEqual(issued, [])

    def test_supported_simulation_entry_with_inert_spy(self):
        """YZT88-V2-C2-POSITIVE: official fake-only wrap is the test helper."""
        spy = InertRecordingSpy()
        entry = cfs.construct_simulation_entry(spy)
        self.assertTrue(cfs.is_simulation_transport(entry))
        cfs.gate_effectful_findings(
            source=None, runner=entry, require_source=False,
            allow_legacy_fixture=True, what="supported test helper")
        fake = FakeMultica()
        result = asm.run_assignment_handoff(
            base_spec(parent_issue_id=PARENT_UUID),
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=cfs.construct_simulation_entry(fake),
            ledger=dispatch.TransactionLedger(),
            compose_fn=COMPOSE, transaction_id="tx-v2-sim-ok",
            clock=CLOCK, finding_store=__import__(
                "chandoff_plan", fromlist=["MemoryFindingStore"]
            ).MemoryFindingStore([]),
            legacy_fixture=True)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["terminal_status"], "COMPLETED")
        self.assertEqual(len(fake.assign_calls), 1)
        self.assertEqual(fake.assign_calls[0][1], AGENT_SE)
        body = fake.comments["11111111-2222-3333-4444-000000000001"][0]["content"]
        self.assertEqual(body.split("\n")[0], "/note")
        self.assertNotIn("mention://", body)


class OfficialPublicationPathTests(unittest.TestCase):
    def test_skill_forbids_bare_t06_and_unbound_context_cli(self):
        """YZT88-V2-C3-SKILL: official docs route through guarded pipeline."""
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        selfcheck = (SKILL / "references" / "self-check.md").read_text(
            encoding="utf-8")
        prepare = (SKILL / "references" / "prepare-handoff.md").read_text(
            encoding="utf-8")
        for text in (skill, selfcheck, prepare):
            self.assertIn("--findings-source-binding-file", text
                          + prepare + selfcheck)
        self.assertIn("internal callable", skill.lower() + prepare.lower())
        self.assertIn("context_cli.py", skill)
        self.assertNotIn("python tools/context_cli.py self-check", selfcheck)
        self.assertIn("handoff_pipeline.py selfcheck", selfcheck)

    def test_context_cli_self_check_is_not_documented_as_official(self):
        """YZT88-V2-C3-CONTEXTCLI: context_cli self-check stays internal."""
        help_text = Path(TOOLS / "context_cli.py").read_text(encoding="utf-8")
        self.assertIn("unsupported for formal dispatch", help_text)


class DefaultResolverFakeSubprocessTests(unittest.TestCase):
    def setUp(self):
        self.fx = build_v2_fixture()
        self.comment = {
            "id": "01a0-synth-comment",
            "issue_id": "01a0-synth-issue",
            "author_id": "01a0-synth-author",
            "author_type": "agent",
            "content": AUTHORITY_TEXT,
        }

    def _cli(self, comments):
        def runner(argv):
            core = [str(a) for a in argv[1:]]
            if core[:3] == ["issue", "comment", "list"]:
                issue_id = core[3]
                thread_id = core[core.index("--thread") + 1]
                rows = [c for c in comments
                        if c.get("id") == thread_id
                        and c.get("issue_id") in (None, issue_id)]
                return 0, json.dumps(rows), ""
            return 2, "", "unexpected command"
        return adapter.MulticaCli(executable="multica", runner=runner)

    def test_default_resolver_round_trip(self):
        """YZT88-V2-C4-ROUNDTRIP: default MulticaCli + AuthenticatedCommentResolver."""
        cli = self._cli([self.comment])
        resolver = cfs.AuthenticatedCommentResolver(cli)
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=resolver, project_id=PROJECT,
            trusted=self.fx["trusted"], require_trusted=True)
        snap = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(snap["open_ids"], [self.fx["open_finding_id"]])
        self.assertEqual(cli.commands[0][:4],
                         ["issue", "comment", "list", "01a0-synth-issue"])

    def test_author_mismatch_refused(self):
        """YZT88-V2-C4-AUTHOR: live author_id mismatch → unbound."""
        rec = dict(self.comment)
        rec["author_id"] = "attacker"
        resolver = cfs.AuthenticatedCommentResolver(self._cli([rec]))
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=resolver, project_id=PROJECT,
            trusted=self.fx["trusted"], require_trusted=True)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertIn("author_id", ctx.exception.message)

    def test_scope_mismatch_refused(self):
        """YZT88-V2-C4-SCOPE: live issue_id mismatch → unbound."""
        rec = dict(self.comment)
        rec["issue_id"] = "other-issue"
        resolver = cfs.AuthenticatedCommentResolver(self._cli([rec]))
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=resolver, project_id=PROJECT,
            trusted=self.fx["trusted"], require_trusted=True)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_content_mismatch_refused(self):
        """YZT88-V2-C4-CONTENT: live content digest mismatch → unbound."""
        rec = dict(self.comment)
        rec["content"] = "SYNTHETIC edited authority body"
        resolver = cfs.AuthenticatedCommentResolver(self._cli([rec]))
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=resolver, project_id=PROJECT,
            trusted=self.fx["trusted"], require_trusted=True)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertIn("digest", ctx.exception.message.lower())

    def test_revocation_refused(self):
        """YZT88-V2-C4-REVOKE: absent live comment → unbound."""
        resolver = cfs.AuthenticatedCommentResolver(self._cli([]))
        source = cfs.BoundFindingsSource(
            self.fx["open_binding"], resolver=resolver, project_id=PROJECT,
            trusted=self.fx["trusted"], require_trusted=True)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertIn("revoked", ctx.exception.message.lower())


class DuplicateUncertainAndManualCorrectionTests(unittest.TestCase):
    def test_known_duplicate_does_not_resend(self):
        """YZT88-V2-C3-DUP: completed synthetic ledger replay issues zero argv."""
        issued = []

        def runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        ledger = dispatch.TransactionLedger()
        tx = "tx-v2-dup"
        ledger.append({
            "kind": "transaction_result",
            "transaction_id": tx,
            "terminal_status": asm.COMPLETED,
            "result": {
                "ok": True, "transaction_id": tx,
                "terminal_status": asm.COMPLETED,
            },
        })
        result = asm.run_assignment_handoff(
            {"title": "duplicate drill",
             "description": "Known duplicate must not resend.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=runner, ledger=ledger,
            compose_fn=lambda *a, **k: None, transaction_id=tx)
        self.assertTrue(result.get("replayed"))
        self.assertEqual(result.get("commands"), [])
        self.assertEqual(issued, [])

    def test_uncertain_incomplete_stops_without_retry(self):
        """YZT88-V2-C3-UNCERTAIN: incomplete prior result → REPLAY_REFUSED."""
        issued = []

        def runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        ledger = dispatch.TransactionLedger()
        tx = "tx-v2-uncertain"
        ledger.append({
            "kind": "transaction_result",
            "transaction_id": tx,
            "terminal_status": asm.INVALID_INPUT,
            "result": {
                "ok": False, "transaction_id": tx,
                "terminal_status": asm.INVALID_INPUT,
            },
        })
        result = asm.run_assignment_handoff(
            {"title": "uncertain drill",
             "description": "Unknown effect must stop for reconciliation.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=runner, ledger=ledger,
            compose_fn=lambda *a, **k: None, transaction_id=tx)
        self.assertEqual(result["terminal_status"], asm.REPLAY_REFUSED)
        self.assertIn("reconcile", result["stop_reason"])
        self.assertEqual(issued, [])

    def test_manual_correction_then_new_check(self):
        """YZT88-V2-C3-MANUAL: after authority fix, a NEW check succeeds."""
        fx = build_v2_fixture()
        cli = SyntheticAuthorityCli(content="SYNTHETIC stale")
        source = cfs.BoundFindingsSource(
            fx["open_binding"],
            resolver=cfs.AuthenticatedCommentResolver(cli),
            project_id=PROJECT, trusted=fx["trusted"], require_trusted=True)
        with self.assertRaises(cfs.FindingsSourceRefusal):
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        cli.set_content(AUTHORITY_TEXT)
        snap = source.read(boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(snap["open_ids"], [fx["open_finding_id"]])


class SkillInstructionBindingTests(unittest.TestCase):
    def test_instructions_require_bound_self_check(self):
        """YZT88-V2-C5-INSTR: candidate texts require binding + evidence."""
        import chandoff_instruction_texts as texts
        self.assertIn("显式 Findings source binding", texts.COMMON_PROTOCOL)
        self.assertIn("裸 T06", texts.COMMON_PROTOCOL)
        self.assertIn("package_id", texts.COMMON_PROTOCOL)
        self.assertIn("显式 Findings source binding", texts.COMMON_MARKERS)

    def test_selfcheck_with_bound_source_records_observation(self):
        """YZT88-V2-C5-SELFCHECK: first-work source read records digest."""
        fx = build_v2_fixture()
        source = cfs.BoundFindingsSource(
            fx["open_binding"],
            resolver=cfs.AuthenticatedCommentResolver(SyntheticAuthorityCli()),
            project_id=PROJECT, trusted=fx["trusted"], require_trusted=True)
        prepare = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        check = source.read(
            boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE,
            prior_observation=prepare["observation"])
        self.assertEqual(check["observation"]["join"]["task_ref"], TASK_REF)
        self.assertEqual(check["observation"]["join"]["role"], ROLE)
        self.assertEqual(check["observation"]["project_id"], PROJECT)
        self.assertEqual(check["observation"]["source_id"],
                         cfs.V2_SYNTHETIC_SOURCE_ID)
        self.assertEqual(check["snapshot_digest"],
                         fx["open_snapshot_digest"])
        self.assertEqual(check["observation"]["boundary"], "SELF_CHECK")


class OfflineMainChainSmokeTests(unittest.TestCase):
    def _pipeline(self):
        import importlib.util
        skill = SKILL / "scripts" / "handoff_pipeline.py"
        spec = importlib.util.spec_from_file_location(
            "handoff_pipeline_v2_smoke", skill)
        pipeline = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(pipeline)
        sys.dont_write_bytecode = False
        return pipeline

    def _selfcheck_ns(self, fx, *, binding_file, trusted_file, request,
                      envelope, evidence, cli, out):
        import argparse
        request_file = Path(fx["dir"]) / "self-check-request.json"
        request_file.write_text(json.dumps(request, ensure_ascii=False,
                                           indent=2), encoding="utf-8")
        envelope_file = Path(fx["dir"]) / "envelope.json"
        envelope_file.write_text(json.dumps(envelope, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
        evidence_file = Path(fx["dir"]) / "prepare-observation.json"
        evidence_file.write_text(json.dumps(evidence, ensure_ascii=False,
                                            indent=2), encoding="utf-8")
        return argparse.Namespace(
            repo=str(REPO), issue=None, task_ref=None, role=None,
            request_file=str(request_file), request_from=None,
            package_ref=None, envelope_file=[str(envelope_file)], store=None,
            executable="multica", out_dir=str(out),
            artifact_store_file=None, artifact_requirements_file=None,
            artifact_review_level=None,
            findings_source_binding_file=str(binding_file),
            findings_authority_file=None, findings_authority_cli=cli,
            findings_authority_capture_only=False,
            findings_evidence_file=str(evidence_file),
            findings_source_expect_commit=None,
            findings_source_expect_adapter_digest=None,
            observer_run_id="yzt88-v2-smoke",
            findings_trusted_map_file=str(trusted_file))

    def test_offline_pipeline_selfcheck_invocation_only(self):
        """YZT88-V2-SMOKE: local invocation of bound selfcheck.

        Proves the pipeline is callable against the V2 fixture. Does not
        prove a successful prepared package, READY, publication, or a
        single assignment. Matching-success and refusal cases are
        separate tests.
        """
        fx = build_v2_fixture()
        pipeline = self._pipeline()
        cli = SyntheticAuthorityCli()
        source = cfs.BoundFindingsSource(
            fx["open_binding"],
            resolver=cfs.AuthenticatedCommentResolver(cli),
            project_id=PROJECT, trusted=fx["trusted"], require_trusted=True)
        prepare = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        request = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": TASK_REF,
            "role": ROLE,
            "task_snapshot": {
                "title": "SYNTHETIC V2 offline self-check",
                "description": "SYNTHETIC. Local invocation only.",
                "requirements": ["bound source"],
                "acceptance_criteria": ["trusted map"],
                "relevant_decisions": ["V2 supervised handoff"],
            },
        }
        envelope = build_envelope(sample_request())
        out = Path(fx["dir"]) / "out-invoke"
        out.mkdir()
        ns = self._selfcheck_ns(
            fx, binding_file=fx["open_binding_file"],
            trusted_file=fx["trusted_map_file"], request=request,
            envelope=envelope, evidence=prepare["observation"],
            cli=cli, out=out)
        payload, code = pipeline.run_selfcheck(ns)
        self.assertEqual(payload.get("findings_source_mode"), "bound", payload)
        self.assertEqual(payload.get("task_ref"), TASK_REF)
        self.assertEqual(payload.get("current_role"), ROLE)
        self.assertIn(payload.get("status"),
                      ("READY", "REFRESH_REQUIRED", "BLOCKED"))
        self.assertIn(code, (0, 2, 3), payload)
        self.assertEqual(payload.get("guarantees", {}).get("canonical_writes"), 0)

    def test_selected_path_ready_use_existing_one_assignment(self):
        """YZT88-V2-SMOKE-READY: empty fixture, matching package, one assign.

        Eligible success fixture is the empty store. Relevant open
        conflicts are not suppressed to obtain READY.
        """
        fx = build_v2_fixture()
        pipeline = self._pipeline()
        cli = SyntheticAuthorityCli()
        source = cfs.BoundFindingsSource(
            fx["empty_binding"],
            resolver=cfs.AuthenticatedCommentResolver(cli),
            project_id=PROJECT, trusted=fx["trusted"], require_trusted=True)
        prepare = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(prepare["observation"]["snapshot_digest"],
                         fx["empty_snapshot_digest"])
        self.assertEqual(prepare["open_ids"], [])
        prepare_req = sample_request()
        envelope = build_envelope(prepare_req, findings=[])
        self.assertEqual(envelope["status"], "READY")
        self.assertEqual(envelope["task_ref"], TASK_REF)
        self.assertEqual(envelope["role"], ROLE)
        check_req = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": prepare_req["task_ref"],
            "role": prepare_req["target"]["role"],
            "task_snapshot": prepare_req["task_snapshot"],
        }
        out = Path(fx["dir"]) / "out-ready"
        out.mkdir()
        ns = self._selfcheck_ns(
            fx, binding_file=fx["empty_binding_file"],
            trusted_file=fx["trusted_map_file"], request=check_req,
            envelope=envelope, evidence=prepare["observation"],
            cli=cli, out=out)
        payload, code = pipeline.run_selfcheck(ns)
        self.assertEqual(payload.get("status"), "READY", payload)
        self.assertEqual(payload.get("action"), "USE_EXISTING", payload)
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload.get("package_id"), envelope["package_id"])
        self.assertEqual(payload.get("task_ref"), TASK_REF)
        self.assertEqual(payload.get("current_role"), ROLE)
        report = payload.get("findings_source") or {}
        self.assertEqual(report.get("snapshot_digest"),
                         fx["empty_snapshot_digest"], payload)
        self.assertEqual(payload.get("guarantees", {}).get("canonical_writes"), 0)

        bound, _, _ = _bound_source(
            fx, fx["empty_binding"], extra_task_refs=(CREATED_TASK_REF,))
        fake = FakeMultica()
        result = asm.run_assignment_handoff(
            base_spec(parent_issue_id=PARENT_UUID),
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=fake, ledger=dispatch.TransactionLedger(),
            compose_fn=COMPOSE, transaction_id="tx-v2-ready",
            clock=CLOCK, findings_source=bound, legacy_fixture=False)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["terminal_status"], "COMPLETED", result)
        self.assertEqual(result.get("self_check", {}).get("status"), "READY",
                         result)
        self.assertEqual(len(fake.assign_calls), 1)
        self.assertEqual(fake.assign_calls[0][1], AGENT_SE)
        self.assertEqual(result["audit"]["command_counts"]
                         .get("assignment_trigger"), 1)
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish"), 1)
        body = fake.comments["11111111-2222-3333-4444-000000000001"][0]["content"]
        self.assertEqual(body.split("\n")[0], "/note")
        self.assertNotIn("mention://", body)

    def test_relevant_conflict_blocks_and_zero_assignment(self):
        """YZT88-V2-SMOKE-BLOCK: material open Finding blocks, zero assign."""
        fx = build_v2_fixture()
        blocking = synthetic_blocking_finding(task_id="YZT-9001")
        root = Path(fx["dir"]) / "blocking"
        root.mkdir()
        (root / f"{blocking['finding_id']}.json").write_text(
            json.dumps(blocking, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8", newline="\n")
        binding = dict(fx["empty_binding"])
        binding["root"] = str(root.resolve())
        trusted = copy.deepcopy(fx["trusted"])
        trusted["sources"][cfs.V2_SYNTHETIC_EMPTY_SOURCE_ID]["root"] = str(
            root.resolve())
        trusted["sources"][cfs.V2_SYNTHETIC_EMPTY_SOURCE_ID]["allowed"][
            "task_refs"].append(CREATED_TASK_REF)
        binding["allowed"] = copy.deepcopy(
            trusted["sources"][cfs.V2_SYNTHETIC_EMPTY_SOURCE_ID]["allowed"])
        source = cfs.BoundFindingsSource(
            binding,
            resolver=cfs.AuthenticatedCommentResolver(SyntheticAuthorityCli()),
            project_id=PROJECT, trusted=trusted, require_trusted=True)
        fake = FakeMultica()
        result = asm.run_assignment_handoff(
            base_spec(parent_issue_id=PARENT_UUID),
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=fake, ledger=dispatch.TransactionLedger(),
            compose_fn=COMPOSE, transaction_id="tx-v2-block",
            clock=CLOCK, findings_source=source, legacy_fixture=False)
        self.assertEqual(result["terminal_status"], "PREPARE_BLOCKED", result)
        self.assertEqual(len(fake.assign_calls), 0)
        self.assertEqual(result["audit"]["command_counts"]
                         .get("assignment_trigger", 0), 0)
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 0)

    def test_stale_package_refused_zero_assignment(self):
        """YZT88-V2-SMOKE-STALE: stale artifacts refuse, zero assign."""
        fx = build_v2_fixture()
        bound, _, _ = _bound_source(
            fx, fx["empty_binding"], extra_task_refs=(CREATED_TASK_REF,))
        fake = FakeMultica()
        stale = [{
            "artifact_type": "implementation",
            "artifact_id": "ART-WIMG-031",
            "version": "superseded-or-missing",
            "required": True,
        }]
        spec = base_spec(parent_issue_id=PARENT_UUID,
                         required_artifacts=stale,
                         artifact_store_file=str(
                             TOOLS / "fixtures" / "artifact-contract"
                             / "store-chain.json"),
                         review_level="R1")
        result = asm.run_assignment_handoff(
            spec, caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=fake, ledger=dispatch.TransactionLedger(),
            compose_fn=COMPOSE, transaction_id="tx-v2-stale",
            clock=CLOCK, findings_source=bound, legacy_fixture=False)
        self.assertEqual(result["terminal_status"], "PACKAGE_STALE", result)
        self.assertEqual(len(fake.assign_calls), 0)
        self.assertEqual(result["audit"]["command_counts"]
                         .get("assignment_trigger", 0), 0)
        self.assertEqual(result["audit"]["command_counts"]
                         .get("comment_publish", 0), 0)

    def test_missing_source_refused_zero_effects(self):
        """YZT88-V2-SMOKE-MISSING: omitted binding → zero argv."""
        issued = []

        def runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        result = asm.run_assignment_handoff(
            base_spec(parent_issue_id=PARENT_UUID),
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=runner, ledger=dispatch.TransactionLedger(),
            compose_fn=COMPOSE, transaction_id="tx-v2-missing",
            clock=CLOCK, findings_source=None, legacy_fixture=False)
        self.assertEqual(result["terminal_status"], asm.INVALID_INPUT, result)
        self.assertEqual(issued, [])
        self.assertIn("binding", result.get("stop_reason", "").lower())


V2_TEST_INVENTORY = {
    "YZT88-V2-C1-SAMENAME": "same-name rewrite during read → findings_source_changed",
    "YZT88-V2-C1-MISSING": "absent root → findings_source_missing (never empty success)",
    "YZT88-V2-C1-IDENTITY": "wrong project/task/role → findings_source_unbound",
    "YZT88-V2-C1-ARBITRARY-ROOT": "caller-selected root → findings_source_unbound",
    "YZT88-V2-C1-SOURCEID": "unknown source_id text → findings_source_unbound",
    "YZT88-V2-C1-OPEN": "synthetic relevant open Finding visible",
    "YZT88-V2-C1-EMPTY": "empty store is a complete zero inventory",
    "YZT88-V2-C1-CORRUPT": "malformed/denied → invalid/unreadable",
    "YZT88-V2-C2-FORGERY": "fake attr/class/wrapper on assignment → zero argv",
    "YZT88-V2-C2-FORGERY-MEN": "fake attr on mention → zero argv",
    "YZT88-V2-C2-R0": "R0 factory no longer infers from markers",
    "YZT88-V2-C2-EXPLICIT": "legacy_fixture + live runner → refuse, zero argv",
    "YZT88-V2-C2-MIXED": "legacy_fixture + markers + inner live → refuse, zero argv",
    "YZT88-V2-C2-LIVEINNER": "SimulationEntry refuses live/mixed inner",
    "YZT88-V2-C2-POSITIVE": "construct_simulation_entry(inert spy) is the test helper",
    "YZT88-V2-C3-SKILL": "official Skill routes through guarded pipeline",
    "YZT88-V2-C3-CONTEXTCLI": "context_cli self-check is not official",
    "YZT88-V2-C3-DUP": "known duplicate → replayed, zero argv",
    "YZT88-V2-C3-UNCERTAIN": "incomplete prior → REPLAY_REFUSED, no retry",
    "YZT88-V2-C3-MANUAL": "manual authority correction then NEW check",
    "YZT88-V2-C4-ROUNDTRIP": "default MulticaCli + AuthenticatedCommentResolver",
    "YZT88-V2-C4-AUTHOR": "author mismatch → unbound",
    "YZT88-V2-C4-SCOPE": "issue_id mismatch → unbound",
    "YZT88-V2-C4-CONTENT": "content digest mismatch → unbound",
    "YZT88-V2-C4-REVOKE": "revoked comment → unbound",
    "YZT88-V2-C5-INSTR": "instruction texts require bound self_check evidence",
    "YZT88-V2-C5-SELFCHECK": "T04 with binding records source observation",
    "YZT88-V2-SMOKE": "local bound selfcheck invocation (not a successful main chain)",
    "YZT88-V2-SMOKE-READY": "empty fixture + matching package → READY/USE_EXISTING/exit 0 + one assign",
    "YZT88-V2-SMOKE-BLOCK": "material open Finding → PREPARE_BLOCKED, zero assign/publish",
    "YZT88-V2-SMOKE-STALE": "stale artifacts → PACKAGE_STALE, zero assign/publish",
    "YZT88-V2-SMOKE-MISSING": "omitted binding → INVALID_INPUT, zero argv",
}


if __name__ == "__main__":
    unittest.main()
