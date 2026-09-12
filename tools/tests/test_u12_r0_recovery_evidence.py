#!/usr/bin/env python3
"""YZT-84 — recovery-evidence correction acceptance tests (public operations).

Exercises the Validation Focus rows of the accepted
`U12_R0_RECOVERY_EVIDENCE_DECISION.md` (raw SHA256 60f265a8...):

A. conservative shared-history classification and correlation: the Lead
   counterexample (empty transaction/no intent-id rerun command + successful
   result with empty live runs), its no-result/nonzero/mislabelled/explicit-id
   variants, ownership/publication/update/status/unknown effects, a second
   create, a missing create command/result, orphan/duplicate/interleaved
   results and nonzero create results all refuse before binding;
B. recognized reads and own lease/refusal/prior-evidence records stay allowed
   and are never misclassified as native effects; unresolved foreign records
   refuse;
C. the receipt decision: a consistent persisted receipt is eligible; a truly
   missing raw receipt body is eligible only under the exact bounded
   receipt-limit disposition; conflicting receipts and missing/nonzero/
   ambiguous results are never waived;
D. the disposition's audited prefix and original pair are revalidated, and a
   shared write racing the evidence write refuses before binding;
E. the committed proof is reconstructable: full inline observations, complete
   discovery inputs, raw response provenance, recomputable digests, restart/
   load revalidation, old/hash-only/edited/missing evidence refusal and the
   exact accepted execution identity;
F. the public audit entrypoint, the old-executor fence and the preserved
   post-recovery lifecycle.

Everything runs on temporary JSONL ledgers and the isolated fixture CLI; no
live Multica call, no production-ledger access, no Canonical write.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "tests"))

import chandoff_intent as o2  # noqa: E402
import u12_r0_binding as u12  # noqa: E402
from test_u12_r0_create_recovery import (  # noqa: E402
    DISPATCHER, PROPOSED_EXECUTION_COMMIT, RecoveryFixture,
    assert_zero_native_writes, proposed_execution_resolver)
from test_u12_r0_binding import TARGET_ID  # noqa: E402

OTHER_ID = "01a0bbbb-0000-7000-8000-00000000ffff"


def rerun_record(intent_id=None, cls="rerun_trigger", tx=""):
    record = {"kind": "command", "command_class": cls, "transaction_id": tx,
              "argv": ["multica", "issue", "rerun", TARGET_ID,
                       "--output", "json"]}
    if intent_id is not None:
        record["intent_id"] = intent_id
    return record


def rerun_result(cls="rerun_trigger", tx="", exit_code=0, intent_id=None):
    record = {"kind": "command_result", "command_class": cls,
              "transaction_id": tx, "exit_code": exit_code}
    if intent_id is not None:
        record["intent_id"] = intent_id
    return record


def rewrite_records(fx, mutate) -> None:
    """Rewrite the fixture ledger records exactly as the store serializes."""
    path = fx.tmp / "ledger.jsonl"
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines()]
    mutate(records)
    path.write_text("".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in records), encoding="utf-8")


def repin(fx) -> tuple:
    """Recompute the audited prefix/pair after a ledger rewrite."""
    raw = (fx.tmp / "ledger.jsonl").read_bytes()
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    prefix = {"length": len(lines), "digest": u12._raw_prefix_digest(lines)}
    records = fx.store.read_records()
    for index, record in enumerate(records):
        if record.get("kind") == "command" and \
                record.get("command_class") == "issue_create":
            result = records[index + 1]
            return prefix, {
                "command_seq": record["seq"],
                "command_digest": u12.digest(record),
                "result_seq": result["seq"],
                "result_digest": u12.digest(result),
            }
    return prefix, {
        "command_seq": 1,
        "command_digest": "sha256:" + "0" * 64,
        "result_seq": 2,
        "result_digest": "sha256:" + "0" * 64,
    }


class EvidenceFixtureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = RecoveryFixture(Path(self.tmp.name))

    def make_fixture(self, label) -> RecoveryFixture:
        path = Path(self.tmp.name) / label
        path.mkdir(parents=True, exist_ok=True)
        return RecoveryFixture(path)

    def assert_refused(self, result, reason=None, allow_evidence=False):
        self.assertEqual(result["status"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(result["outcome"], "RECOVERY_REFUSED")
        self.assertEqual(result["external_writes"], 0)
        if reason is not None:
            self.assertEqual(result["reason"], reason)
        intent = self.fx.store.get(self.fx.intent_id)
        self.assertEqual(intent["state"], o2.S_CREATE_AMBIGUOUS)
        self.assertNotIn("execution_binding",
                         intent["fields"][u12.R0B_FIELD])
        if not allow_evidence:
            self.assertEqual(
                len(self.fx.events_named(u12.E_RECOVERY_EVIDENCE)), 0)
        assert_zero_native_writes(self, self.fx.cli)

    def recover_with(self, **overrides):
        return self.fx.recover(**overrides)


# ---------------------------------------------------------------------------
# A. counterexample + shared-history refusals
# ---------------------------------------------------------------------------
class SharedHistoryRefusalTests(EvidenceFixtureBase):
    def test_counterexample_rerun_command_and_success_result_refuse(self):
        fx = self.fx
        fx.store.append(rerun_record())
        fx.store.append(rerun_result())
        self.assertEqual(fx.cli.commands_of(["issue", "rerun"]), [])
        self.assertEqual(fx.cli.runs[TARGET_ID], [])
        self.assert_refused(self.recover_with(),
                            u12.REASON_RECOVERY_EFFECT)

    def test_rerun_variants_all_refuse(self):
        cases = (
            ("no_result", [rerun_record()]),
            ("nonzero_result", [rerun_record(), rerun_result(exit_code=7)]),
            ("mislabelled_read", [rerun_record(cls="read"),
                                  rerun_result(cls="read")]),
            ("lead_script_class_label", [
                rerun_record(cls="issue_rerun"),
                rerun_result(cls="issue_rerun")]),
            ("explicit_intent_id", [
                rerun_record(intent_id=self.fx.intent_id),
                rerun_result(intent_id=self.fx.intent_id)]),
        )
        for label, records in cases:
            with self.subTest(label):
                fx = self.make_fixture(label)
                for record in records:
                    fx.store.append(record)
                self.assert_refused(fx.recover())

    def test_ownership_and_trigger_variants_refuse(self):
        cases = (
            ("ownership_no_start", ["multica", "issue", "assign", TARGET_ID,
                                    "--to-id", u12.CANARY_AGENT_ID,
                                    "--no-start", "--output", "json"],
             "ownership_binding"),
            ("assignment_trigger", ["multica", "issue", "assign", TARGET_ID,
                                    "--to-id", u12.CANARY_AGENT_ID,
                                    "--output", "json"],
             "assignment_trigger"),
        )
        for label, argv, cls in cases:
            with self.subTest(label):
                fx = self.make_fixture(label)
                for exit_code in (None, 0, 1):
                    if exit_code is None:
                        fx.store.append({"kind": "command",
                                         "command_class": cls,
                                         "transaction_id": "",
                                         "argv": argv})
                    else:
                        fx.store.append({"kind": "command",
                                         "command_class": cls,
                                         "transaction_id": "",
                                         "argv": argv})
                        fx.store.append({"kind": "command_result",
                                         "command_class": cls,
                                         "transaction_id": "",
                                         "exit_code": exit_code})
                self.assert_refused(fx.recover())

    def test_publication_update_status_and_unknown_refuse(self):
        cases = (
            ("publication_comment", {"kind": "command",
                                     "command_class": "mention_trigger",
                                     "transaction_id": "",
                                     "argv": ["multica", "issue", "comment",
                                              "add", TARGET_ID, "--content",
                                              "hi", "--output", "json"]}),
            ("issue_update", {"kind": "command",
                              "command_class": "issue_update",
                              "transaction_id": "",
                              "argv": ["multica", "issue", "update",
                                       TARGET_ID, "--title", "x",
                                       "--output", "json"]}),
            ("status_trigger", {"kind": "command",
                                "command_class": "status_trigger",
                                "transaction_id": "",
                                "argv": ["multica", "issue", "status",
                                         TARGET_ID, "todo"]}),
            ("unknown_verb", {"kind": "command",
                              "command_class": "other",
                              "transaction_id": "",
                              "argv": ["multica", "issue", "frobnicate",
                                       TARGET_ID]}),
            ("malformed_class", {"kind": "command", "command_class": "other",
                                 "transaction_id": "",
                                 "argv": "issue rerun not-a-list"}),
        )
        for label, record in cases:
            with self.subTest(label):
                fx = self.make_fixture(label)
                fx.store.append(record)
                self.assert_refused(fx.recover())

    def test_second_shared_create_refuses(self):
        fx = self.fx
        create = next(r for r in fx.store.read_records()
                      if r.get("kind") == "command"
                      and r.get("command_class") == "issue_create")
        fx.store.append(copy.deepcopy(create))
        fx.store.append({"kind": "command_result",
                         "command_class": "issue_create",
                         "transaction_id": "", "exit_code": 0})
        self.assert_refused(
            self.recover_with(),
            u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)
        self.assertEqual(len(fx.cli.commands_of(["issue", "create"])), 1)

    def test_missing_create_command_refuses_with_exact_gap(self):
        fx = self.fx

        def drop(records):
            records[:] = [r for r in records if not (
                r.get("kind") in ("command", "command_result")
                and r.get("command_class") == "issue_create")]

        rewrite_records(fx, drop)
        prefix, pair = repin(fx)
        result = self.recover_with(recovery_decision=fx.decision(
            ledger_prefix=prefix, original_create_pair=pair))
        self.assert_refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)
        self.assertIn("create command", result["detail"])

    def test_missing_create_result_refuses_with_exact_gap(self):
        fx = self.fx

        def drop(records):
            for index, record in enumerate(records):
                if record.get("kind") == "command" and \
                        record.get("command_class") == "issue_create":
                    del records[index + 1]
                    return

        rewrite_records(fx, drop)
        prefix, pair = repin(fx)
        result = self.recover_with(recovery_decision=fx.decision(
            ledger_prefix=prefix, original_create_pair=pair))
        self.assert_refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)

    def test_nonzero_create_result_never_waived(self):
        fx = self.fx

        def nonzero(records):
            for record in records:
                if record.get("kind") == "command_result" and \
                        record.get("command_class") == "issue_create":
                    record["exit_code"] = 1

        rewrite_records(fx, nonzero)
        prefix, pair = repin(fx)
        result = self.recover_with(recovery_decision=fx.decision(
            ledger_prefix=prefix, original_create_pair=pair))
        self.assert_refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)

    def test_orphan_duplicate_and_interleaved_results_refuse(self):
        with self.subTest("orphan_result"):
            fx = self.make_fixture("orphan")
            fx.store.append({"kind": "command_result",
                             "command_class": "read",
                             "transaction_id": "", "exit_code": 0})
            self.assert_refused(fx.recover())
        with self.subTest("duplicate_result"):
            fx = self.make_fixture("duplicate")
            for record in fx.store.read_records():
                if record.get("kind") == "command_result" and \
                        record.get("command_class") == "issue_create":
                    fx.store.append(dict(record))
                    break
            self.assert_refused(fx.recover())
        with self.subTest("class_conflicting_result"):
            fx = self.make_fixture("interleaved")
            fx.store.append({"kind": "command", "command_class": "read",
                             "transaction_id": "",
                             "argv": ["multica", "issue", "get", TARGET_ID,
                                      "--output", "json"]})
            fx.store.append({"kind": "command_result",
                             "command_class": "issue_create",
                             "transaction_id": "", "exit_code": 0})
            self.assert_refused(fx.recover())

    def test_foreign_unknown_command_targeting_other_scope_refuses(self):
        fx = self.fx
        fx.store.append({"kind": "command", "command_class": "other",
                         "transaction_id": "TX-other",
                         "argv": ["multica", "workspace", "delete",
                                  "other-scope"]})
        fx.store.append({"kind": "command_result", "command_class": "other",
                         "transaction_id": "TX-other", "exit_code": 0})
        self.assert_refused(fx.recover(), u12.REASON_RECOVERY_HISTORY)


# ---------------------------------------------------------------------------
# B. allowed reads / own diagnostics
# ---------------------------------------------------------------------------
class RecognizedHistoryTests(EvidenceFixtureBase):
    def test_recognized_reads_and_own_diagnostics_are_allowed(self):
        fx = self.fx
        for argv in (["multica", "issue", "get", OTHER_ID, "--output", "json"],
                     ["multica", "issue", "comment", "list", TARGET_ID,
                      "--full", "--output", "json"],
                     ["multica", "issue", "runs", TARGET_ID, "--output",
                      "json"],
                     ["multica", "issue", "children", OTHER_ID, "--output",
                      "json"],
                     ["multica", "version"]):
            fx.store.append({"kind": "command", "command_class": "read",
                             "transaction_id": "", "argv": argv})
            fx.store.append({"kind": "command_result",
                             "command_class": "read",
                             "transaction_id": "", "exit_code": 0})
        fx.store.append_event(fx.intent_id, u12.E_RECOVERY, actor=DISPATCHER,
                              data={"window": "diag"})
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        proof = fx.binding()["recovery_proof"]
        classes = [entry["classification"]
                   for entry in proof["shared_history"]["records"]]
        self.assertIn(u12.CLS_READ_COMMAND, classes)
        self.assertIn(u12.CLS_READ_RESULT, classes)
        assert_zero_native_writes(self, fx.cli)

    def test_failed_read_result_never_supplies_observations(self):
        fx = self.fx
        fx.store.append({"kind": "command", "command_class": "read",
                         "transaction_id": "",
                         "argv": ["multica", "issue", "get", TARGET_ID,
                                  "--output", "json"]})
        fx.store.append({"kind": "command_result", "command_class": "read",
                         "transaction_id": "", "exit_code": 3})
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        proof = fx.binding()["recovery_proof"]
        failed_read_results = [
            entry for entry in proof["shared_history"]["records"]
            if entry["classification"] == u12.CLS_READ_RESULT
            and entry["record"].get("exit_code") == 3]
        self.assertEqual(len(failed_read_results), 1)


# ---------------------------------------------------------------------------
# C. receipt decision and bounded receipt-limit disposition
# ---------------------------------------------------------------------------
class ReceiptDispositionTests(EvidenceFixtureBase):
    def test_persisted_receipt_available_and_consistent_succeeds(self):
        fx = self.fx
        receipt = {"id": TARGET_ID, "identifier": "YZT-85",
                   "title": fx.spec["title"],
                   "parent_issue_id": fx.spec["parent_issue_id"]}
        result = self.recover_with(
            recovery_decision=fx.decision(
                receipt_status=u12.RECEIPT_STATUS_PERSISTED),
            original_receipt=receipt)
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        proof = fx.binding()["recovery_proof"]
        self.assertEqual(proof["receipt"]["status"],
                         u12.RECEIPT_STATUS_PERSISTED)
        self.assertEqual(proof["receipt"]["body"], receipt)
        self.assertEqual(proof["receipt"]["body_digest"], u12.digest(receipt))
        audit = fx.factory.validate(fx.intent_id)
        self.assertEqual(audit["state"], o2.S_TARGET_BOUND)

    def test_conflicting_receipt_refuses_even_with_valid_readback(self):
        fx = self.fx
        receipt = {"id": OTHER_ID, "title": fx.spec["title"]}
        self.assert_refused(
            self.recover_with(
                recovery_decision=fx.decision(
                    receipt_status=u12.RECEIPT_STATUS_PERSISTED),
                original_receipt=receipt),
            u12.REASON_RECOVERY_RECEIPT)

    def test_receipt_supplied_against_unpersisted_disposition_refuses(self):
        fx = self.fx
        self.assert_refused(
            self.recover_with(original_receipt={"id": TARGET_ID}),
            u12.REASON_RECOVERY_RECEIPT)

    def test_missing_receipt_without_bounded_disposition_refuses(self):
        fx = self.fx
        self.assert_refused(
            self.recover_with(
                recovery_decision=fx.decision(
                    receipt_status=u12.RECEIPT_STATUS_PERSISTED)),
            u12.REASON_RECOVERY_RECEIPT)

    def test_legacy_incident_requires_exact_bounded_scope(self):
        fx = self.fx
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=fx.decision(
                receipt_limit_scope=None))

    def test_exact_legacy_receipt_limit_path_succeeds(self):
        fx = self.fx
        result = fx.recover()
        self.assertEqual(result["status"], o2.S_TARGET_BOUND)
        proof = fx.binding()["recovery_proof"]
        self.assertEqual(proof["receipt"]["status"],
                         u12.RECEIPT_STATUS_NOT_PERSISTED)
        self.assertIsNone(proof["receipt"]["body"])
        self.assertEqual(proof["receipt"]["disposition_scope"],
                         u12.RECEIPT_LIMIT_SCOPE)


# ---------------------------------------------------------------------------
# D. audited prefix/pair revalidation and the shared-tail race
# ---------------------------------------------------------------------------
class PrefixAndTailTests(EvidenceFixtureBase):
    def test_disposition_prefix_and_pair_are_recomputed(self):
        fx = self.fx
        bad_prefix = dict(fx.ledger_prefix)
        bad_prefix["digest"] = "sha256:" + "1" * 64
        self.assert_refused(
            self.recover_with(recovery_decision=fx.decision(
                ledger_prefix=bad_prefix)),
            u12.REASON_RECOVERY_PREFIX)
        bad_pair = dict(fx.original_create_pair)
        bad_pair["result_digest"] = "sha256:" + "2" * 64
        self.assert_refused(
            self.recover_with(recovery_decision=fx.decision(
                original_create_pair=bad_pair)),
            u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)

    def test_shared_write_racing_evidence_write_refuses(self):
        fx = self.fx
        original = fx.store.append_event
        state = {"injected": False}

        def racing(intent_id, name, **kwargs):
            if name == u12.E_RECOVERY_EVIDENCE and not state["injected"]:
                state["injected"] = True
                fx.store.append(rerun_record())
                fx.store.append(rerun_result())
            return original(intent_id, name, **kwargs)

        fx.store.append_event = racing
        self.addCleanup(lambda: setattr(fx.store, "append_event", original))
        result = fx.recover()
        self.assert_refused(result, allow_evidence=True)
        intent = self.fx.store.get(self.fx.intent_id)
        self.assertEqual(intent["state"], o2.S_CREATE_AMBIGUOUS)
        self.assertEqual(len(self.fx.events_named(u12.E_RECOVERY_BOUND)), 0)
        self.assertEqual(len(self.fx.cli.commands_of(["issue", "rerun"])), 0)

    def test_unexpected_own_tail_record_refuses_before_binding(self):
        fx = self.fx
        original = fx.store.append_event
        state = {"injected": False}

        def racing(intent_id, name, **kwargs):
            if name == u12.E_RECOVERY_EVIDENCE and not state["injected"]:
                state["injected"] = True
                original(intent_id, "r0b_recovery", actor="external",
                         data={"window": "external"})
            return original(intent_id, name, **kwargs)

        fx.store.append_event = racing
        self.addCleanup(lambda: setattr(fx.store, "append_event", original))
        result = fx.recover()
        self.assert_refused(result, u12.REASON_RECOVERY_HISTORY,
                            allow_evidence=True)
        intent = self.fx.store.get(self.fx.intent_id)
        self.assertEqual(intent["state"], o2.S_CREATE_AMBIGUOUS)
        self.assertNotIn("execution_binding", intent["fields"][u12.R0B_FIELD])
        self.assertEqual(len(self.fx.events_named(u12.E_RECOVERY_BOUND)), 0)


# ---------------------------------------------------------------------------
# E. reconstructable proof, restart/load and execution identity
# ---------------------------------------------------------------------------
class ProofReconstructabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = RecoveryFixture(Path(self.tmp.name))
        self.fx.recover()

    def tamper(self, mutate):
        intent = self.fx.store.get(self.fx.intent_id)
        tampered = copy.deepcopy(intent)
        binding = tampered["fields"][u12.R0B_FIELD]
        mutate(binding)
        proof = binding["recovery_proof"]
        proof.pop("proof_digest", None)
        proof["proof_digest"] = u12.digest(proof)
        binding["execution_binding"]["recovery_proof_digest"] = \
            proof["proof_digest"]
        patcher = mock.patch.object(self.fx.store, "get",
                                    return_value=tampered)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_proof_reconstructs_full_inline_observations(self):
        proof = self.fx.binding()["recovery_proof"]
        observations = proof["observations"]
        for key in ("target_issue", "target_recheck", "comments",
                    "activities", "runs", "discovery", "raw_responses"):
            self.assertIn(key, observations)
        self.assertEqual(observations["target_issue"]["id"], TARGET_ID)
        self.assertEqual(observations["digests"]["target_issue"],
                         u12.digest(observations["target_issue"]))
        self.assertEqual(observations["digests"]["comments"],
                         u12.digest(observations["comments"]))
        self.assertEqual(observations["digests"]["discovery"],
                         u12.digest(observations["discovery"]))
        self.assertEqual(observations["digests"]["raw_responses"],
                         u12.digest(observations["raw_responses"]))
        self.assertTrue(observations["raw_responses"])
        ws = {row["what"] for row in observations["raw_responses"]}
        self.assertIn("issue get", ws)
        self.assertIn("issue comment list", ws)
        self.assertIn("issue timeline", ws)
        self.assertIn("issue runs", ws)
        self.assertIn("issue children", ws)
        discovery = observations["discovery"]
        self.assertEqual(discovery["total"], discovery["declared_total"])
        self.assertTrue(discovery["candidates"])
        self.assertTrue(discovery["completeness"]["candidate_bodies_retained"])
        shared = proof["shared_history"]
        self.assertEqual(shared["classification_digest"],
                         u12.shared_history_digest(shared))
        self.assertEqual(shared["original_create"]["result"]["exit_code"], 0)
        self.assertEqual(
            shared["original_create"]["result_seq"],
            shared["original_create"]["command_seq"] + 1)

    def test_candidate_body_fetched_when_listing_omits_it(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fx = RecoveryFixture(Path(tmp.name))
        from test_u12_r0_create_recovery import PARENT_ID
        fx.cli.children[PARENT_ID] = [
            {k: v for k, v in row.items() if k != "description"}
            for row in fx.cli.children[PARENT_ID]]
        fx.recover()
        discovery = fx.binding()["recovery_proof"]["observations"]["discovery"]
        sources = {candidate["id"]: candidate["body_source"]
                   for candidate in discovery["candidates"]}
        self.assertEqual(sources.get(TARGET_ID), "issue-get")
        self.assertTrue(discovery["candidates"][0]["full_response"])

    def test_restart_and_load_recompute_proof(self):
        factory = u12.build_r0b_factory(
            self.fx.store, runner=self.fx.cli,
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            authority_reader=u12.ReadinessManifestAuthorityReader(),
            execution_blob_resolver=proposed_execution_resolver,
            require_findings_source=False)
        audit = factory.validate(self.fx.intent_id)
        self.assertEqual(audit["state"], o2.S_TARGET_BOUND)
        replay = factory.recover_created_target(
            self.fx.intent_id, expected_target_id=TARGET_ID,
            recovery_decision=self.fx.decision(), actor=DISPATCHER,
            execution_commit=PROPOSED_EXECUTION_COMMIT)
        self.assertTrue(replay["replayed"])

    def test_old_hash_only_proof_refused(self):
        def old_schema(binding):
            binding["recovery_proof"]["schema"] = \
                "u12-r0b-recovery-proof/1.0"
            binding["execution_binding"]["schema"] = \
                "u12-r0b-execution-binding/1.0"

        self.tamper(old_schema)
        with self.assertRaises(u12.R0BContractError):
            self.fx.factory.validate(self.fx.intent_id)

    def test_missing_observations_refused(self):
        def strip(binding):
            binding["recovery_proof"].pop("observations")

        self.tamper(strip)
        with self.assertRaises(u12.R0BValidationRefused):
            self.fx.factory.validate(self.fx.intent_id)

    def test_edited_observation_refused(self):
        def edit(binding):
            binding["recovery_proof"]["observations"]["comments"].append(
                {"id": "forged"})

        self.tamper(edit)
        with self.assertRaises(u12.R0BValidationRefused):
            self.fx.factory.validate(self.fx.intent_id)

    def test_missing_ledger_records_refused(self):
        def strip(binding):
            binding["recovery_proof"]["shared_history"]["records"] = []

        self.tamper(strip)
        with self.assertRaises(u12.R0BValidationRefused):
            self.fx.factory.validate(self.fx.intent_id)

    def test_fabricated_blob_resolution_refused(self):
        def fabricate(binding):
            binding["recovery_proof"]["execution_authority"][
                "blob_resolution"]["ok"] = False

        self.tamper(fabricate)
        with self.assertRaises(u12.R0BValidationRefused):
            self.fx.factory.validate(self.fx.intent_id)

    def test_execution_identity_refusals(self):
        fx = self.fx
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(execution_commit="29e0bde")
        decision = fx.decision()
        decision["accepted_execution"] = {
            "commit": "0" * 40, "adapter_digest": u12.adapter_digest()}
        decision["decision_digest"] = u12.digest(
            {k: v for k, v in decision.items() if k != "decision_digest"})
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(recovery_decision=decision,
                       execution_commit=PROPOSED_EXECUTION_COMMIT)
        with self.assertRaises(u12.R0BValidationRefused):
            u12.build_r0b_factory(
                fx.store, runner=fx.cli,
                execution_blob_resolver=lambda commit, path: b"changed",
                require_findings_source=False). \
                recover_created_target(
                    fx.intent_id, expected_target_id=TARGET_ID,
                    recovery_decision=fx.decision(), actor=DISPATCHER,
                    execution_commit=PROPOSED_EXECUTION_COMMIT)

    def test_old_execution_commit_short_refused(self):
        fx = self.fx
        with self.assertRaises(u12.R0BValidationRefused):
            fx.recover(execution_commit=None)


# ---------------------------------------------------------------------------
# F. audit entrypoint and preserved fences
# ---------------------------------------------------------------------------
class AuditAndFenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fx = RecoveryFixture(Path(self.tmp.name))

    def test_audit_pins_match_the_operation_classification(self):
        before_bytes = self.fx.ledger_bytes()
        before_commands = len(self.fx.cli.commands)
        audit = self.fx.factory.audit_recovery_ledger(self.fx.intent_id)
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["suggested_disposition"]["ledger_prefix"],
                         self.fx.ledger_prefix)
        self.assertEqual(
            audit["suggested_disposition"]["original_create_pair"],
            self.fx.original_create_pair)
        self.assertEqual(self.fx.ledger_bytes(), before_bytes)
        self.assertEqual(len(self.fx.cli.commands), before_commands)
        classifications = [entry["classification"] for entry in
                           audit["shared_history"]["records"]]
        self.assertIn(u12.CLS_ORIGINAL_CREATE_COMMAND, classifications)
        self.assertIn(u12.CLS_ORIGINAL_CREATE_RESULT, classifications)
        self.assertIn(u12.CLS_INTENT_HISTORY, classifications)

    def test_audit_refuses_unresolved_shared_write(self):
        self.fx.store.append(rerun_record())
        self.fx.store.append(rerun_result())
        with self.assertRaises(u12.PreflightRefusal) as caught:
            self.fx.factory.audit_recovery_ledger(self.fx.intent_id)
        self.assertEqual(caught.exception.reason, u12.REASON_RECOVERY_EFFECT)

    def test_old_executor_still_refuses_the_corrected_record(self):
        self.fx.recover()
        commands_before = len(self.fx.cli.commands)
        with self.assertRaises(self.fx.old.R0BContractError):
            self.fx.old_factory.validate(self.fx.intent_id)
        with self.assertRaises(self.fx.old.R0BContractError):
            self.fx.old_factory.assign_ownership_once(self.fx.intent_id,
                                                      actor=DISPATCHER)
        self.assertEqual(len(self.fx.cli.commands), commands_before)
        assert_zero_native_writes(self, self.fx.cli)

    def test_post_recovery_lifecycle_reaches_one_strict_rerun(self):
        from test_u12_r0_binding import PUBLISHER_RUN
        from test_u12_r0_create_recovery import CLOCK, execution_context_for
        self.fx.recover()
        owned = self.fx.factory.assign_ownership_once(self.fx.intent_id,
                                                      actor=DISPATCHER)
        self.assertEqual(owned["status"], o2.S_TARGET_BOUND)
        prepared = self.fx.factory.bind_execution_package(
            self.fx.intent_id,
            execution_context=execution_context_for(self.fx.cli),
            actor=DISPATCHER)
        self.assertEqual(prepared["status"], o2.S_HANDOFF_PREPARED)
        self.fx.factory.publish_handoff_once(
            self.fx.intent_id, actor=DISPATCHER,
            publisher_run_id=PUBLISHER_RUN,
            prepared_by="01 Engineering Lead", prepared_at=CLOCK)
        self.fx.arm()
        triggered = self.fx.trigger()
        self.assertEqual(triggered["status"], o2.S_RUN_CORRELATED)
        self.assertEqual(len(self.fx.cli.commands_of(["issue", "rerun"])), 1)
        self.assertEqual(len(self.fx.cli.commands_of(["issue", "create"])), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
