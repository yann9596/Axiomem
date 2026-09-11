#!/usr/bin/env python3
"""Isolated acceptance reproduction for the YZT-84 recovery-evidence repair.

Runs the Validation Focus rows of the accepted
`U12_R0_RECOVERY_EVIDENCE_DECISION.md` (raw SHA256 60f265a8...) through the
public adapter operations with the committed fixture CLI and temporary JSONL
ledgers. The predecessor record is produced by the actual predecessor module
bytes loaded from commit b49630b, so the old-executor refusal row exercises the
real old code. Proposed execution identity is fixture-injected and is NOT a
live acceptance. No live Multica call, no production-ledger write, no
Canonical write, no trigger outside the isolated ledger.

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-recovery-evidence/reproduce_recovery_evidence.py \
        --output adapters/multica/u12-r0b-recovery-evidence/recovery-evidence-acceptance-matrix.json
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import chandoff_intent as o2  # noqa: E402
import u12_r0_binding as u12  # noqa: E402
from test_u12_r0_binding import TARGET_ID  # noqa: E402
from test_u12_r0_create_recovery import (  # noqa: E402
    PROPOSED_EXECUTION_COMMIT, RecoveryFixture,
    proposed_execution_resolver)
from test_u12_r0_recovery_evidence import (  # noqa: E402
    OTHER_ID, repin, rerun_record, rerun_result, rewrite_records)


def refused(result, reason=None) -> bool:
    return (result.get("status") == o2.S_CREATE_AMBIGUOUS
            and result.get("outcome") == "RECOVERY_REFUSED"
            and result.get("external_writes") == 0
            and (reason is None or result.get("reason") == reason))


def write_counts(cli) -> dict:
    return {
        "create": len(cli.commands_of(["issue", "create"])),
        "assign": len(cli.commands_of(["issue", "assign"])),
        "comment_add": len(cli.commands_of(["issue", "comment", "add"])),
        "rerun": len(cli.commands_of(["issue", "rerun"])),
        "status": len(cli.commands_of(["issue", "status"])),
        "update": len(cli.commands_of(["issue", "update"])),
        "total": len(cli.commands),
    }


def ambiguous(fx) -> bool:
    return fx.store.get(fx.intent_id)["state"] == o2.S_CREATE_AMBIGUOUS


def unbound(fx) -> bool:
    return "execution_binding" not in fx.binding()


def bound_clean(fx, result) -> bool:
    return (result.get("status") == o2.S_TARGET_BOUND
            and result.get("side_effects") == 0
            and len(fx.cli.commands_of(["issue", "create"])) == 1)


def new_fx(root: Path, label: str) -> RecoveryFixture:
    path = root / label
    path.mkdir(parents=True, exist_ok=True)
    return RecoveryFixture(path)


# ---------------------------------------------------------------------------
# group 1: shared-history classification and correlation
# ---------------------------------------------------------------------------
def case_exact_counterexample(root):
    fx = new_fx(root, "exact-counterexample")
    fx.store.append(rerun_record())
    fx.store.append(rerun_result())
    result = fx.recover()
    return {"required": "refuse before binding; persisted attempt dominates "
                        "empty live runs",
            "observed": {"result": result,
                         "state": fx.store.get(fx.intent_id)["state"],
                         "live_runs": len(fx.cli.runs.get(TARGET_ID, [])),
                         "writes": write_counts(fx.cli)},
            "pass": refused(result, u12.REASON_RECOVERY_EFFECT)
            and ambiguous(fx) and unbound(fx)}


def case_rerun_variants(root):
    rows = []
    variants = (
        ("no_result", [rerun_record()]),
        ("nonzero_result", [rerun_record(), rerun_result(exit_code=7)]),
        ("mislabelled_class", [rerun_record(cls="read"),
                               rerun_result(cls="read")]),
        ("lead_script_label", [rerun_record(cls="issue_rerun"),
                               rerun_result(cls="issue_rerun")]),
    )
    passed = True
    for label, records in variants:
        fx = new_fx(root, f"rerun-{label}")
        for record in records:
            fx.store.append(record)
        result = fx.recover()
        ok = refused(result) and ambiguous(fx) and unbound(fx)
        passed = passed and ok
        rows.append({"variant": label, "reason": result.get("reason"),
                     "pass": ok})
    return {"required": "absence/failure/mislabel of the result never erases "
                        "the persisted attempt", "observed": rows,
            "pass": passed}


def case_rerun_explicit_intent_id(root):
    fx = new_fx(root, "rerun-explicit-intent")
    fx.store.append(rerun_record(intent_id=fx.intent_id))
    fx.store.append(rerun_result(intent_id=fx.intent_id))
    result = fx.recover()
    return {"required": "an explicit intent id never makes a rerun attempt "
                        "acceptable",
            "observed": {"result": result},
            "pass": refused(result, u12.REASON_RECOVERY_EFFECT)}


def case_ownership_and_triggers(root):
    rows = []
    passed = True
    cases = (
        ("ownership_no_start", ["multica", "issue", "assign", TARGET_ID,
                                "--to-id", u12.CANARY_AGENT_ID, "--no-start",
                                "--output", "json"], "ownership_binding"),
        ("assignment_trigger", ["multica", "issue", "assign", TARGET_ID,
                                "--to-id", u12.CANARY_AGENT_ID, "--output",
                                "json"], "assignment_trigger"),
    )
    for label, argv, cls in cases:
        for with_result in (False, True):
            fx = new_fx(root, f"{label}-result-{with_result}")
            fx.store.append({"kind": "command", "command_class": cls,
                             "transaction_id": "", "argv": argv})
            if with_result:
                fx.store.append({"kind": "command_result",
                                 "command_class": cls,
                                 "transaction_id": "", "exit_code": 0})
            result = fx.recover()
            ok = refused(result, u12.REASON_RECOVERY_EFFECT) and \
                ambiguous(fx) and unbound(fx)
            passed = passed and ok
            rows.append({"variant": f"{label}{'-result' if with_result else ''}",
                         "reason": result.get("reason"), "pass": ok})
    return {"required": "ownership (including --no-start) and assignment "
                        "triggers refuse with/without a result",
            "observed": rows, "pass": passed}


def case_other_effects(root):
    rows = []
    passed = True
    cases = (
        ("publication_comment", {"kind": "command",
                                 "command_class": "mention_trigger",
                                 "transaction_id": "",
                                 "argv": ["multica", "issue", "comment", "add",
                                          "TARGET", "--content", "hi",
                                          "--output", "json"]}),
        ("issue_update", {"kind": "command", "command_class": "issue_update",
                          "transaction_id": "",
                          "argv": ["multica", "issue", "update", "TARGET",
                                   "--title", "x", "--output", "json"]}),
        ("status_trigger", {"kind": "command",
                            "command_class": "status_trigger",
                            "transaction_id": "",
                            "argv": ["multica", "issue", "status", "TARGET",
                                     "todo"]}),
        ("unknown_verb", {"kind": "command", "command_class": "other",
                          "transaction_id": "",
                          "argv": ["multica", "issue", "frobnicate",
                                   "TARGET"]}),
        ("malformed_command", {"kind": "command", "command_class": "other",
                               "transaction_id": "",
                               "argv": "issue rerun not-a-list"}),
    )
    for label, record in cases:
        fx = new_fx(root, f"effect-{label}")
        target = next(iter(fx.cli.issues))
        record = copy.deepcopy(record)
        if isinstance(record.get("argv"), list):
            record["argv"] = [target if token == "TARGET" else token
                              for token in record["argv"]]
        fx.store.append(record)
        result = fx.recover()
        ok = refused(result) and ambiguous(fx) and unbound(fx)
        passed = passed and ok
        rows.append({"variant": label, "reason": result.get("reason"),
                     "pass": ok})
    return {"required": "publication/update/status/unknown effects and "
                        "malformed commands all refuse",
            "observed": rows, "pass": passed}


def case_second_shared_create(root):
    fx = new_fx(root, "second-create")
    create = next(record for record in fx.store.read_records()
                  if record.get("kind") == "command"
                  and record.get("command_class") == "issue_create")
    fx.store.append(copy.deepcopy(create))
    fx.store.append({"kind": "command_result",
                     "command_class": "issue_create", "transaction_id": "",
                     "exit_code": 0})
    result = fx.recover()
    return {"required": "a second shared create command (no second intent "
                        "event) refuses",
            "observed": {"result": result, "native_creates":
                         len(fx.cli.commands_of(["issue", "create"]))},
            "pass": refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)}


def case_missing_original_evidence(root):
    rows = []
    passed = True
    fx = new_fx(root, "missing-command")

    def drop_command(records):
        records[:] = [r for r in records if not (
            r.get("kind") in ("command", "command_result")
            and r.get("command_class") == "issue_create")]

    rewrite_records(fx, drop_command)
    prefix, pair = repin(fx)
    result = fx.recover(recovery_decision=fx.decision(
        ledger_prefix=prefix, original_create_pair=pair))
    ok = refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)
    passed = passed and ok
    rows.append({"variant": "missing_create_command", "reason":
                 result.get("reason"), "pass": ok})

    fx = new_fx(root, "missing-result")

    def drop_result(records):
        for index, record in enumerate(records):
            if record.get("kind") == "command" and \
                    record.get("command_class") == "issue_create":
                del records[index + 1]
                return

    rewrite_records(fx, drop_result)
    prefix, pair = repin(fx)
    result = fx.recover(recovery_decision=fx.decision(
        ledger_prefix=prefix, original_create_pair=pair))
    ok = refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)
    passed = passed and ok
    rows.append({"variant": "missing_create_result", "reason":
                 result.get("reason"), "pass": ok})

    fx = new_fx(root, "nonzero-result")

    def nonzero(records):
        for record in records:
            if record.get("kind") == "command_result" and \
                    record.get("command_class") == "issue_create":
                record["exit_code"] = 1

    rewrite_records(fx, nonzero)
    prefix, pair = repin(fx)
    result = fx.recover(recovery_decision=fx.decision(
        ledger_prefix=prefix, original_create_pair=pair))
    ok = refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)
    passed = passed and ok
    rows.append({"variant": "nonzero_create_result", "reason":
                 result.get("reason"), "pass": ok})
    return {"required": "missing create command/result and nonzero results "
                        "report the exact original-evidence gap and are never "
                        "waived", "observed": rows, "pass": passed}


def case_result_pairing_refusals(root):
    rows = []
    passed = True
    variants = (
        ("orphan_result", [{"kind": "command_result",
                            "command_class": "read", "transaction_id": "",
                            "exit_code": 0}]),
        ("duplicate_create_result", None),
        ("interleaved_class_conflict", [
            {"kind": "command", "command_class": "read",
             "transaction_id": "",
             "argv": ["multica", "issue", "get", "TARGET", "--output",
                      "json"]},
            {"kind": "command_result", "command_class": "issue_create",
             "transaction_id": "", "exit_code": 0}]),
    )
    for label, records in variants:
        fx = new_fx(root, f"pairing-{label}")
        if records is None:
            for record in fx.store.read_records():
                if record.get("kind") == "command_result" and \
                        record.get("command_class") == "issue_create":
                    fx.store.append(dict(record))
                    break
        else:
            target = next(iter(fx.cli.issues))
            for record in copy.deepcopy(records):
                if isinstance(record.get("argv"), list):
                    record["argv"] = [target if token == "TARGET" else token
                                      for token in record["argv"]]
                fx.store.append(record)
        result = fx.recover()
        ok = refused(result) and ambiguous(fx) and unbound(fx)
        passed = passed and ok
        rows.append({"variant": label, "reason": result.get("reason"),
                     "pass": ok})
    return {"required": "orphan, duplicate and class-conflicting results are "
                        "never paired by guess", "observed": rows,
            "pass": passed}


def case_recognized_reads_allowed(root):
    fx = new_fx(root, "recognized-reads")
    for argv in (["multica", "issue", "get", OTHER_ID, "--output", "json"],
                 ["multica", "issue", "comment", "list", "TARGET", "--full",
                  "--output", "json"],
                 ["multica", "issue", "runs", "TARGET", "--output", "json"],
                 ["multica", "issue", "children", OTHER_ID, "--output",
                  "json"],
                 ["multica", "version"]):
        target = next(iter(fx.cli.issues))
        fx.store.append({"kind": "command", "command_class": "read",
                         "transaction_id": "",
                         "argv": [target if token == "TARGET" else token
                                  for token in argv]})
        fx.store.append({"kind": "command_result",
                         "command_class": "read", "transaction_id": "",
                         "exit_code": 0})
    fx.store.append_event(fx.intent_id, u12.E_RECOVERY, actor="actor",
                          data={"window": "own-diagnostic"})
    result = fx.recover()
    proof = fx.binding().get("recovery_proof") or {}
    classes = [entry["classification"] for entry in
               proof.get("shared_history", {}).get("records", [])]
    ok = bound_clean(fx, result) and u12.CLS_READ_COMMAND in classes and \
        u12.CLS_READ_RESULT in classes
    return {"required": "recognized reads and own lease/refusal/diagnostic "
                        "records stay allowed and are never misclassified as "
                        "native effects",
            "observed": {"status": result.get("status"),
                         "classes": sorted(set(classes)),
                         "writes": write_counts(fx.cli)},
            "pass": ok}


def case_foreign_scope_refuses(root):
    fx = new_fx(root, "foreign-scope")
    fx.store.append({"kind": "command", "command_class": "other",
                     "transaction_id": "TX-other",
                     "argv": ["multica", "workspace", "delete",
                              "other-scope"]})
    fx.store.append({"kind": "command_result", "command_class": "other",
                     "transaction_id": "TX-other", "exit_code": 0})
    result = fx.recover()
    return {"required": "an unresolved foreign command refuses",
            "observed": {"result": result},
            "pass": refused(result, u12.REASON_RECOVERY_HISTORY)}


def case_incomplete_read_blocks(root):
    fx = new_fx(root, "truncated-read")
    fx.cli.truncate_comment_list = True
    result = fx.recover()
    return {"required": "a failed/partial required read blocks completeness "
                        "and can never supply observations",
            "observed": {"reason": result.get("reason"),
                         "state": fx.store.get(fx.intent_id)["state"]},
            "pass": refused(result, u12.REASON_MATERIAL_UNAVAILABLE)}


# ---------------------------------------------------------------------------
# group 2: receipt decision and the bounded receipt-limit disposition
# ---------------------------------------------------------------------------
def case_persisted_receipt_positive(root):
    fx = new_fx(root, "receipt-persisted")
    receipt = {"id": TARGET_ID, "identifier": "YZT-85",
               "title": fx.spec["title"],
               "parent_issue_id": fx.spec["parent_issue_id"]}
    result = fx.recover(
        recovery_decision=fx.decision(
            receipt_status=u12.RECEIPT_STATUS_PERSISTED),
        original_receipt=receipt)
    proof = fx.binding().get("recovery_proof") or {}
    ok = bound_clean(fx, result) and \
        proof.get("receipt", {}).get("status") == u12.RECEIPT_STATUS_PERSISTED
    return {"required": "a consistent persisted receipt is eligible under "
                        "all unchanged gates",
            "observed": {"status": result.get("status"),
                         "receipt_status": proof.get("receipt", {}).get(
                             "status")},
            "pass": ok}


def case_conflicting_receipt_refuses(root):
    fx = new_fx(root, "receipt-conflict")
    result = fx.recover(
        recovery_decision=fx.decision(
            receipt_status=u12.RECEIPT_STATUS_PERSISTED),
        original_receipt={"id": OTHER_ID, "title": fx.spec["title"]})
    return {"required": "a receipt naming another target refuses even when "
                        "the readback looks valid",
            "observed": {"result": result},
            "pass": refused(result, u12.REASON_RECOVERY_RECEIPT)}


def case_receipt_disposition_mismatches(root):
    rows = []
    passed = True
    fx = new_fx(root, "receipt-vs-waiver")
    result = fx.recover(original_receipt={"id": TARGET_ID})
    ok = refused(result, u12.REASON_RECOVERY_RECEIPT)
    passed = passed and ok
    rows.append({"variant": "receipt_supplied_against_unpersisted_disposition",
                 "reason": result.get("reason"), "pass": ok})

    fx = new_fx(root, "persisted-without-receipt")
    result = fx.recover(recovery_decision=fx.decision(
        receipt_status=u12.RECEIPT_STATUS_PERSISTED))
    ok = refused(result, u12.REASON_RECOVERY_RECEIPT)
    passed = passed and ok
    rows.append({"variant": "persisted_disposition_without_receipt",
                 "reason": result.get("reason"), "pass": ok})

    fx = new_fx(root, "waiver-scope-missing")
    try:
        fx.recover(recovery_decision=fx.decision(receipt_limit_scope=None))
        ok = False
    except u12.R0BValidationRefused:
        ok = True
    passed = passed and ok
    rows.append({"variant": "unpersisted_without_exact_bounded_scope",
                 "pass": ok})
    return {"required": "only a truly missing raw receipt body qualifies for "
                        "the exact bounded receipt-limit disposition; the "
                        "disposition path is revalidated, never trusted",
            "observed": rows, "pass": passed}


def case_legacy_receipt_limit_positive(root):
    fx = new_fx(root, "legacy-waiver")
    result = fx.recover()
    proof = fx.binding().get("recovery_proof") or {}
    receipt = proof.get("receipt", {})
    ok = bound_clean(fx, result) and \
        receipt.get("status") == u12.RECEIPT_STATUS_NOT_PERSISTED and \
        receipt.get("body") is None and \
        receipt.get("disposition_scope") == u12.RECEIPT_LIMIT_SCOPE
    return {"required": "the exact legacy incident is recoverable only under "
                        "the explicit bound receipt-limit disposition and "
                        "operation revalidation",
            "observed": {"status": result.get("status"),
                         "receipt_status": receipt.get("status"),
                         "body": receipt.get("body"),
                         "scope": receipt.get("disposition_scope")},
            "pass": ok}


# ---------------------------------------------------------------------------
# group 3: audited prefix/pair and the shared tail
# ---------------------------------------------------------------------------
def case_prefix_and_pair_revalidated(root):
    rows = []
    passed = True
    fx = new_fx(root, "prefix-mismatch")
    bad = dict(fx.ledger_prefix)
    bad["digest"] = "sha256:" + "1" * 64
    result = fx.recover(recovery_decision=fx.decision(ledger_prefix=bad))
    ok = refused(result, u12.REASON_RECOVERY_PREFIX)
    passed = passed and ok
    rows.append({"variant": "prefix_digest_mismatch",
                 "reason": result.get("reason"), "pass": ok})

    fx = new_fx(root, "pair-mismatch")
    bad = dict(fx.original_create_pair)
    bad["result_digest"] = "sha256:" + "2" * 64
    result = fx.recover(recovery_decision=fx.decision(
        original_create_pair=bad))
    ok = refused(result, u12.REASON_RECOVERY_ORIGINAL_EVIDENCE)
    passed = passed and ok
    rows.append({"variant": "pair_digest_mismatch",
                 "reason": result.get("reason"), "pass": ok})
    return {"required": "the audited prefix and original pair are recomputed "
                        "and must match the disposition", "observed": rows,
            "pass": passed}


def case_shared_tail_race(root):
    fx = new_fx(root, "tail-race")
    original = fx.store.append_event
    state = {"injected": False}

    def racing(intent_id, name, **kwargs):
        if name == u12.E_RECOVERY_EVIDENCE and not state["injected"]:
            state["injected"] = True
            fx.store.append(rerun_record())
            fx.store.append(rerun_result())
        return original(intent_id, name, **kwargs)

    fx.store.append_event = racing
    result = fx.recover()
    fx.store.append_event = original
    intent = fx.store.get(fx.intent_id)
    ok = refused(result) and intent["state"] == o2.S_CREATE_AMBIGUOUS and \
        "execution_binding" not in intent["fields"][u12.R0B_FIELD]
    return {"required": "a shared write racing the evidence write refuses "
                        "before binding; the intent CAS alone must not pass it",
            "observed": {"reason": result.get("reason"),
                         "state": intent["state"],
                         "bound": "execution_binding" in intent["fields"][
                             u12.R0B_FIELD]},
            "pass": ok}


def case_unexpected_own_tail(root):
    fx = new_fx(root, "own-tail-race")
    original = fx.store.append_event
    state = {"injected": False}

    def racing(intent_id, name, **kwargs):
        if name == u12.E_RECOVERY_EVIDENCE and not state["injected"]:
            state["injected"] = True
            original(intent_id, "r0b_recovery", actor="external",
                     data={"window": "external"})
        return original(intent_id, name, **kwargs)

    fx.store.append_event = racing
    result = fx.recover()
    fx.store.append_event = original
    intent = fx.store.get(fx.intent_id)
    ok = refused(result, u12.REASON_RECOVERY_HISTORY) and \
        intent["state"] == o2.S_CREATE_AMBIGUOUS
    return {"required": "an unexpected own tail record refuses before "
                        "binding",
            "observed": {"reason": result.get("reason"),
                         "state": intent["state"]},
            "pass": ok}


# ---------------------------------------------------------------------------
# group 4: proof reconstructability and execution identity
# ---------------------------------------------------------------------------
def case_inline_proof(root):
    fx = new_fx(root, "inline-proof")
    result = fx.recover()
    proof = fx.binding().get("recovery_proof") or {}
    observations = proof.get("observations") or {}
    digests = observations.get("digests") or {}
    sections_ok = all(
        (key in observations) and digests.get(key) == u12.digest(
            observations[key])
        for key in ("target_issue", "target_recheck", "comments",
                    "activities", "runs", "discovery", "raw_responses"))
    shared = proof.get("shared_history") or {}
    ok = bound_clean(fx, result) and sections_ok and \
        shared.get("classification_digest") == u12.shared_history_digest(
            shared) and \
        shared.get("original_create", {}).get("result", {}).get(
            "exit_code") == 0
    return {"required": "the committed inline proof reconstructs every "
                        "observation and the classification with "
                        "recomputable hashes",
            "observed": {"status": result.get("status"),
                         "sections": sorted(observations),
                         "raw_responses": len(observations.get(
                             "raw_responses") or []),
                         "shared_records": len(shared.get("records") or []),
                         "candidates": len((observations.get("discovery")
                                            or {}).get("candidates") or [])},
            "pass": ok}


def case_candidate_body_retained(root):
    fx = new_fx(root, "candidate-body")
    parent = fx.spec["parent_issue_id"]
    fx.cli.children[parent] = [
        {key: value for key, value in row.items() if key != "description"}
        for row in fx.cli.children[parent]]
    result = fx.recover()
    discovery = (fx.binding().get("recovery_proof") or {}).get(
        "observations", {}).get("discovery", {})
    sources = {candidate["id"]: candidate["body_source"]
               for candidate in discovery.get("candidates") or []}
    ok = bound_clean(fx, result) and any(
        source == "issue-get" for source in sources.values())
    return {"required": "a candidate body fetched separately is retained "
                        "with its full response",
            "observed": {"sources": sources, "status": result.get("status")},
            "pass": ok}


def _recovered_fixture(root, label):
    fx = new_fx(root, label)
    fx.recover()
    return fx


def case_restart_load(root):
    fx = _recovered_fixture(root, "restart-load")
    factory = u12.build_r0b_factory(
        fx.store, runner=fx.cli,
        artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
        authority_reader=u12.ReadinessManifestAuthorityReader(),
        execution_blob_resolver=proposed_execution_resolver)
    audit = factory.validate(fx.intent_id)
    replay = factory.recover_created_target(
        fx.intent_id, expected_target_id=TARGET_ID,
        recovery_decision=fx.decision(), actor="restart-auditor",
        execution_commit=PROPOSED_EXECUTION_COMMIT)
    ok = audit["state"] == o2.S_TARGET_BOUND and replay.get("replayed") is True
    return {"required": "a restarted reader revalidates the full committed "
                        "proof and replays the same binding",
            "observed": {"state": audit["state"],
                         "replayed": replay.get("replayed"),
                         "revision": replay.get("revision")},
            "pass": ok}


def _tamper(fx, mutate):
    intent = copy.deepcopy(fx.store.get(fx.intent_id))
    binding = intent["fields"][u12.R0B_FIELD]
    mutate(binding)
    proof = binding["recovery_proof"]
    proof.pop("proof_digest", None)
    proof["proof_digest"] = u12.digest(proof)
    binding["execution_binding"]["recovery_proof_digest"] = \
        proof["proof_digest"]
    fx.store.get = lambda intent_id: intent


def case_proof_refusals(root):
    rows = []
    passed = True
    mutations = (
        ("old_v10_schema", "contract",
         lambda b: (b["recovery_proof"].__setitem__(
             "schema", "u12-r0b-recovery-proof/1.0"),
             b["execution_binding"].__setitem__(
                 "schema", "u12-r0b-execution-binding/1.0"))),
        ("missing_observations", "validation",
         lambda b: b["recovery_proof"].pop("observations")),
        ("edited_comment_observation", "validation",
         lambda b: b["recovery_proof"]["observations"]["comments"].append(
             {"id": "forged"})),
        ("missing_ledger_records", "validation",
         lambda b: b["recovery_proof"]["shared_history"].__setitem__(
             "records", [])),
        ("fabricated_blob_resolution", "validation",
         lambda b: b["recovery_proof"]["execution_authority"][
             "blob_resolution"].__setitem__("ok", False)),
        ("cross_intent_proof", "validation",
         lambda b: b["recovery_proof"].__setitem__(
             "intent_id", "DI-" + "9" * 16)),
        ("count_body_mismatch", "validation",
         lambda b: b["recovery_proof"]["observations"][
             "declared_completeness"].__setitem__("comments_count", 99)),
    )
    for label, kind, mutate in mutations:
        fx = _recovered_fixture(root, f"proof-{label}")
        _tamper(fx, mutate)
        expected = (u12.R0BContractError if kind == "contract"
                    else u12.R0BValidationRefused)
        try:
            fx.factory.validate(fx.intent_id)
            ok = False
        except expected:
            ok = True
        passed = passed and ok
        rows.append({"variant": label, "refused": ok})
    return {"required": "old/hash-only/edited/missing/cross-intent/count-"
                        "mismatched evidence is refused on execution load; no "
                        "fresh-data substitution",
            "observed": rows, "pass": passed}


def case_execution_identity_refusals(root):
    rows = []
    passed = True
    fx = new_fx(root, "identity-short")
    try:
        fx.recover(execution_commit="29e0bde")
        ok = False
    except u12.R0BValidationRefused:
        ok = True
    passed = passed and ok
    rows.append({"variant": "short_revision", "refused": ok})

    fx = new_fx(root, "identity-unaccepted")
    decision = fx.decision()
    decision["accepted_execution"] = {"commit": "0" * 40,
                                      "adapter_digest": u12.adapter_digest()}
    decision["decision_digest"] = u12.digest(
        {key: value for key, value in decision.items()
         if key != "decision_digest"})
    try:
        fx.recover(recovery_decision=decision,
                   execution_commit=PROPOSED_EXECUTION_COMMIT)
        ok = False
    except u12.R0BValidationRefused:
        ok = True
    passed = passed and ok
    rows.append({"variant": "unaccepted_commit", "refused": ok})

    fx = new_fx(root, "identity-unresolvable")
    try:
        factory = u12.build_r0b_factory(
            fx.store, runner=fx.cli,
            execution_blob_resolver=lambda commit, path: (_ for _ in ()).throw(
                LookupError("unresolvable")))
        factory.recover_created_target(
            fx.intent_id, expected_target_id=TARGET_ID,
            recovery_decision=fx.decision(), actor="auditor",
            execution_commit=PROPOSED_EXECUTION_COMMIT)
        ok = False
    except u12.R0BValidationRefused:
        ok = True
    passed = passed and ok
    rows.append({"variant": "unresolvable_commit", "refused": ok})

    fx = new_fx(root, "identity-mismatched")
    try:
        factory = u12.build_r0b_factory(
            fx.store, runner=fx.cli,
            execution_blob_resolver=lambda commit, path: b"changed bytes")
        factory.recover_created_target(
            fx.intent_id, expected_target_id=TARGET_ID,
            recovery_decision=fx.decision(), actor="auditor",
            execution_commit=PROPOSED_EXECUTION_COMMIT)
        ok = False
    except u12.R0BValidationRefused:
        ok = True
    passed = passed and ok
    rows.append({"variant": "mismatched_resolved_blob", "refused": ok})
    return {"required": "missing/unaccepted/unresolvable/mismatched execution "
                        "identity refuses live eligibility", "observed": rows,
            "pass": passed}


# ---------------------------------------------------------------------------
# group 5: audit entrypoint and preserved fences
# ---------------------------------------------------------------------------
def case_audit_pins(root):
    fx = new_fx(root, "audit-pins")
    before = fx.ledger_bytes()
    commands_before = len(fx.cli.commands)
    audit = fx.factory.audit_recovery_ledger(fx.intent_id)
    ok = audit["suggested_disposition"]["ledger_prefix"] == \
        fx.ledger_prefix and \
        audit["suggested_disposition"]["original_create_pair"] == \
        fx.original_create_pair and fx.ledger_bytes() == before and \
        len(fx.cli.commands) == commands_before
    return {"required": "the public audit reproduces the exact disposition "
                        "pins with zero commands and zero writes",
            "observed": {"pins": audit["suggested_disposition"],
                         "commands_before": commands_before,
                         "commands_after": len(fx.cli.commands),
                         "ledger_unchanged": fx.ledger_bytes() == before},
            "pass": ok}


def case_old_executor_fence(root):
    fx = _recovered_fixture(root, "old-executor")
    commands_before = len(fx.cli.commands)
    refusals = []
    for name, call in (("validate", lambda: fx.old_factory.validate(
            fx.intent_id)),
                       ("assign", lambda: fx.old_factory.assign_ownership_once(
                           fx.intent_id, actor="actor"))):
        try:
            call()
            refusals.append({name: False})
        except fx.old.R0BContractError:
            refusals.append({name: True})
    ok = all(all(row.values()) for row in refusals) and \
        len(fx.cli.commands) == commands_before
    return {"required": "the actual old executor refuses the corrected "
                        "record with zero external calls; predecessor pin "
                        "preserved",
            "observed": {"refusals": refusals, "old_pin": fx.binding().get(
                "adapter_digest"), "commands": len(fx.cli.commands)},
            "pass": ok}


def case_post_recovery_lifecycle(root):
    from test_u12_r0_binding import PUBLISHER_RUN
    from test_u12_r0_create_recovery import CLOCK, execution_context_for
    fx = _recovered_fixture(root, "post-recovery")
    fx.factory.assign_ownership_once(fx.intent_id, actor="dispatcher")
    fx.factory.bind_execution_package(
        fx.intent_id, execution_context=execution_context_for(fx.cli),
        actor="dispatcher")
    fx.factory.publish_handoff_once(
        fx.intent_id, actor="dispatcher", publisher_run_id=PUBLISHER_RUN,
        prepared_by="01 Engineering Lead", prepared_at=CLOCK)
    armed = fx.arm()
    triggered = fx.trigger()
    ok = armed["status"] == o2.S_TRIGGER_READY and \
        triggered["status"] == o2.S_RUN_CORRELATED and \
        len(fx.cli.commands_of(["issue", "rerun"])) == 1 and \
        len(fx.cli.commands_of(["issue", "create"])) == 1
    return {"required": "the preserved post-recovery lifecycle reaches one "
                        "strict rerun with no second create",
            "observed": {"armed": armed.get("status"),
                         "triggered": triggered.get("status"),
                         "reruns": len(fx.cli.commands_of(["issue", "rerun"])),
                         "creates": len(fx.cli.commands_of(
                             ["issue", "create"]))},
            "pass": ok}


def case_crash_recollect(root):
    fx = new_fx(root, "crash-recollect")
    original = fx.store.transition
    state = {"calls": 0}

    def crash(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise o2.IntentError("simulated crash after evidence append")
        return original(*args, **kwargs)

    fx.store.transition = crash
    try:
        fx.recover()
        first = "no-crash"
    except o2.IntentError:
        first = fx.store.get(fx.intent_id)["state"]
    fx.store.transition = original
    second = fx.recover()
    ok = first == o2.S_CREATE_AMBIGUOUS and \
        second.get("status") == o2.S_TARGET_BOUND and \
        len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 2 and \
        len(fx.events_named(u12.E_RECOVERY_BOUND)) == 1 and \
        len(fx.cli.commands_of(["issue", "create"])) == 1
    return {"required": "a crash after evidence before the CAS stays "
                        "ambiguous and fully recollects without a second "
                        "create",
            "observed": {"state_after_crash": first,
                         "second_status": second.get("status"),
                         "evidence_events": len(fx.events_named(
                             u12.E_RECOVERY_EVIDENCE)),
                         "creates": len(fx.cli.commands_of(
                             ["issue", "create"]))},
            "pass": ok}


def case_crash_before_evidence(root):
    fx = new_fx(root, "crash-before-evidence")
    original = fx.store.append_event

    def crash(intent_id, name, **kwargs):
        if name == u12.E_RECOVERY_EVIDENCE:
            raise o2.IntentError("simulated crash before evidence")
        return original(intent_id, name, **kwargs)

    fx.store.append_event = crash
    try:
        fx.recover()
        first = "no-crash"
    except o2.IntentError:
        first = fx.store.get(fx.intent_id)["state"]
    fx.store.append_event = original
    second = fx.recover()
    ok = first == o2.S_CREATE_AMBIGUOUS and \
        second.get("status") == o2.S_TARGET_BOUND and \
        len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 1 and \
        len(fx.cli.commands_of(["issue", "create"])) == 1
    return {"required": "a crash before evidence stays ambiguous and the "
                        "recollected attempt binds once",
            "observed": {"state_after_crash": first,
                         "second_status": second.get("status"),
                         "evidence_events": len(fx.events_named(
                             u12.E_RECOVERY_EVIDENCE)),
                         "creates": len(fx.cli.commands_of(
                             ["issue", "create"]))},
            "pass": ok}


def case_bare_pin_change(root):
    fx = new_fx(root, "bare-pin")
    intent = copy.deepcopy(fx.store.get(fx.intent_id))
    intent["fields"][u12.R0B_FIELD]["adapter_digest"] = "sha256:" + "1" * 64
    fx.store.get = lambda intent_id: intent
    commands_before = len(fx.cli.commands)
    try:
        fx.recover()
        ok = False
    except u12.R0BDowngradeRefused:
        ok = len(fx.cli.commands) == commands_before
    return {"required": "a bare changed pin refuses before any read; the "
                        "predecessor pin is preserved",
            "observed": {"refused": ok, "commands": len(fx.cli.commands)},
            "pass": ok}


def case_replay_one_binding(root):
    fx = new_fx(root, "replay")
    first = fx.recover()
    records = len(fx.store.read_records())
    second = fx.recover()
    ok = not first.get("replayed") and second.get("replayed") is True and \
        len(fx.store.read_records()) == records and \
        len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 1 and \
        len(fx.events_named(u12.E_RECOVERY_BOUND)) == 1
    return {"required": "repeated recovery returns the same committed "
                        "binding with no second create/bind/native effect",
            "observed": {"first_replayed": first.get("replayed"),
                         "second_replayed": second.get("replayed"),
                         "evidence_events": len(fx.events_named(
                             u12.E_RECOVERY_EVIDENCE)),
                         "bound_events": len(fx.events_named(
                             u12.E_RECOVERY_BOUND))},
            "pass": ok}


def case_drift_after_arm(root):
    from test_u12_r0_binding import PUBLISHER_RUN
    from test_u12_r0_create_recovery import CLOCK, execution_context_for
    fx = _recovered_fixture(root, "drift-after-arm")
    fx.factory.assign_ownership_once(fx.intent_id, actor="dispatcher")
    fx.factory.bind_execution_package(
        fx.intent_id, execution_context=execution_context_for(fx.cli),
        actor="dispatcher")
    fx.factory.publish_handoff_once(
        fx.intent_id, actor="dispatcher", publisher_run_id=PUBLISHER_RUN,
        prepared_by="01 Engineering Lead", prepared_at=CLOCK)
    armed = fx.arm()
    fx.cli.issues[TARGET_ID]["description"] = "drifted after arm"
    result = fx.trigger()
    ok = armed["status"] == o2.S_TRIGGER_READY and \
        result["status"] == o2.S_REFRESH_REQUIRED and \
        len(fx.cli.commands_of(["issue", "rerun"])) == 0
    return {"required": "drift after ARM prevents the trigger with zero "
                        "native reruns",
            "observed": {"armed": armed.get("status"),
                         "trigger": result.get("status"),
                         "reruns": len(fx.cli.commands_of(["issue", "rerun"]))},
            "pass": ok}


def case_discovery_refusals(root):
    rows = []
    passed = True
    fx = new_fx(root, "discovery-zero")
    marker_line = "Intent marker: " + fx.spec["marker"]
    for holder in (fx.cli.issues[TARGET_ID],
                   fx.cli.children[fx.spec["parent_issue_id"]][0]):
        holder["description"] = holder["description"].replace(
            marker_line, "Intent marker: R0BI-0000000000000000")
    result = fx.recover()
    ok = refused(result, u12.REASON_RECOVERY_IDENTITY)
    passed = passed and ok
    rows.append({"variant": "zero_identity_candidates", "pass": ok})

    fx = new_fx(root, "discovery-multiple")
    duplicate = copy.deepcopy(fx.cli.children[fx.spec["parent_issue_id"]][0])
    duplicate["id"] = "01a0dddd-0000-7000-8000-00000000ffff"
    fx.cli.children[fx.spec["parent_issue_id"]].append(duplicate)
    result = fx.recover()
    ok = refused(result, u12.REASON_RECOVERY_IDENTITY)
    passed = passed and ok
    rows.append({"variant": "multiple_identity_candidates", "pass": ok})

    fx = new_fx(root, "discovery-partial")
    fx.cli.truncate_children = True
    result = fx.recover()
    ok = refused(result, u12.REASON_MATERIAL_UNAVAILABLE)
    passed = passed and ok
    rows.append({"variant": "partial_listing", "pass": ok})

    fx = new_fx(root, "discovery-moving")
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
    result = fx.recover()
    ok = refused(result, u12.REASON_MOVING_EVIDENCE)
    passed = passed and ok
    rows.append({"variant": "moving_readback", "pass": ok})
    return {"required": "zero/multiple candidates, partial listing and moving "
                        "readback keep the existing typed refusals",
            "observed": rows, "pass": passed}


def case_transport_relation(root):
    base = ("alpha\n\nIntent marker: R0BI-0123456789abcdef\n"
            "Intent: DI-0123456789abcdef")
    cases = (
        ("exact", base, base, True),
        ("one_terminal_lf_removed", base + "\n", base, True),
        ("repeated_terminal_lf", base + "\n\n", base, False),
        ("trailing_space", base + " \n", base, False),
        ("crlf_plus_trim", base + "\r\n", base, False),
        ("leading_whitespace", base + "\n", " " + base, False),
        ("interior_change", base + "\n", base.replace("alpha", "alphb"),
         False),
        ("identity_line_change", base + "\n", base.replace("DI-", "DJ-"),
         False),
    )
    rows = []
    passed = True
    for label, source, observed, accepted in cases:
        result = u12.single_terminal_lf_relation(source, observed)
        ok = result["accepted"] == accepted
        passed = passed and ok
        rows.append({"variant": label, "accepted": result["accepted"],
                     "pass": ok})
    return {"required": "only the existing directional single-terminal-LF "
                        "rule is admitted; every other change refuses",
            "observed": rows, "pass": passed}


CASES = (
    ("1.1", "1_shared_history",
     "exact counterexample: empty-tx/no-intent rerun + exit 0, empty runs",
     case_exact_counterexample),
    ("1.2", "1_shared_history", "rerun variants: no/nonzero/mislabelled/legacy-label",
     case_rerun_variants),
    ("1.3", "1_shared_history", "rerun with explicit intent id", case_rerun_explicit_intent_id),
    ("1.4", "1_shared_history", "ownership --no-start and assignment trigger, with/without result",
     case_ownership_and_triggers),
    ("1.5", "1_shared_history", "publication/update/status/unknown/malformed effects",
     case_other_effects),
    ("1.6", "1_shared_history", "second shared create command", case_second_shared_create),
    ("1.7", "1_shared_history", "missing create command/result, nonzero create result",
     case_missing_original_evidence),
    ("1.8", "1_shared_history", "orphan/duplicate/interleaved results", case_result_pairing_refusals),
    ("1.9", "1_shared_history", "recognized reads and own diagnostics allowed",
     case_recognized_reads_allowed),
    ("1.10", "1_shared_history", "unresolved foreign command", case_foreign_scope_refuses),
    ("1.11", "1_shared_history", "failed/partial required read", case_incomplete_read_blocks),
    ("2.1", "2_receipt", "consistent persisted receipt", case_persisted_receipt_positive),
    ("2.2", "2_receipt", "conflicting target receipt", case_conflicting_receipt_refuses),
    ("2.3", "2_receipt", "disposition/receipt mismatches and exact bounded scope",
     case_receipt_disposition_mismatches),
    ("2.4", "2_receipt", "exact legacy receipt-limit path", case_legacy_receipt_limit_positive),
    ("3.1", "3_prefix_tail", "audited prefix and pair revalidated",
     case_prefix_and_pair_revalidated),
    ("3.2", "3_prefix_tail", "shared write racing the evidence write", case_shared_tail_race),
    ("3.3", "3_prefix_tail", "unexpected own tail record", case_unexpected_own_tail),
    ("4.1", "4_proof_identity", "inline reconstructable proof", case_inline_proof),
    ("4.2", "4_proof_identity", "candidate body retained", case_candidate_body_retained),
    ("4.3", "4_proof_identity", "restart/load revalidation and replay", case_restart_load),
    ("4.4", "4_proof_identity", "old/hash-only/edited/missing proof refusals",
     case_proof_refusals),
    ("4.5", "4_proof_identity", "execution identity refusals", case_execution_identity_refusals),
    ("5.1", "5_audit_fences", "public audit pins", case_audit_pins),
    ("5.2", "5_audit_fences", "actual old executor fence", case_old_executor_fence),
    ("5.3", "5_audit_fences", "post-recovery lifecycle single rerun",
     case_post_recovery_lifecycle),
    ("5.4", "5_audit_fences", "crash after evidence recollects", case_crash_recollect),
    ("5.5", "5_audit_fences", "crash before evidence recollects", case_crash_before_evidence),
    ("5.6", "5_audit_fences", "bare changed pin refuses before any read", case_bare_pin_change),
    ("5.7", "5_audit_fences", "repeated recovery is one binding", case_replay_one_binding),
    ("5.8", "5_audit_fences", "drift after ARM prevents trigger", case_drift_after_arm),
    ("6.1", "6_target_evidence", "zero/multiple candidates, partial listing, moving readback",
     case_discovery_refusals),
    ("6.2", "6_target_evidence", "directional transport relation matrix",
     case_transport_relation),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="u12-r0b-evidence-") as tmp:
        root = Path(tmp)
        for row_id, group, requirement, fn in CASES:
            try:
                outcome = fn(root)
                passed = bool(outcome.pop("pass"))
                observed = outcome.pop("observed", None)
                required = outcome.pop("required", requirement)
                row = {"row": row_id, "group": group, "case": requirement,
                       "required_outcome": required,
                       "observed": observed, "pass": passed}
            except Exception as exc:  # noqa: BLE001 - a row failure is data
                row = {"row": row_id, "group": group, "case": requirement,
                       "required_outcome": "", "observed": None,
                       "error": f"{type(exc).__name__}: {exc}",
                       "pass": False}
            rows.append(row)
    passed = sum(1 for row in rows if row["pass"])
    matrix = {
        "kind": "u12_r0b_recovery_evidence_acceptance_matrix",
        "task": "YZT-84",
        "branch": "yzt-84-u12-r0b-forward-adapter",
        "accepted_decision": {
            "ref": u12.EVIDENCE_DECISION_REF,
            "raw_sha256": u12.EVIDENCE_DECISION_DIGEST,
        },
        "adapter_digest": u12.adapter_digest(),
        "recovery_decision_schema": u12.RECOVERY_DECISION_SCHEMA,
        "recovery_proof_schema": u12.RECOVERY_PROOF_SCHEMA,
        "execution_binding_schema": u12.EXECUTION_BINDING_SCHEMA,
        "resolver": u12.EXECUTION_RESOLVER_INJECTED,
        "rows_total": len(rows),
        "rows_passed": passed,
        "verdict": ("ALL_RECOVERY_EVIDENCE_VALIDATION_ROWS_PASS"
                    if passed == len(rows) else "ROWS_FAILED"),
        "rows": rows,
    }
    text = json.dumps(matrix, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}: {passed}/{len(rows)} rows pass")
    else:
        print(text)
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
