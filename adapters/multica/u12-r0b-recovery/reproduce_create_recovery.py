#!/usr/bin/env python3
"""Isolated acceptance reproduction for the YZT-83 create-recovery repair.

Runs all six validation groups of `U12_R0_CREATE_RECOVERY_DECISION.md` through
the public adapter operations with the committed fixture CLI and temporary
JSONL ledgers. The predecessor record is produced by the actual predecessor
module bytes loaded from commit b49630b, so the old-executor refusal row
exercises the real old code. No live Multica call, no production-ledger write,
no Canonical write, no trigger outside the isolated ledger.

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-recovery/reproduce_create_recovery.py \
        --output adapters/multica/u12-r0b-recovery/recovery-acceptance-matrix.json
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import chandoff_intent as o2  # noqa: E402
import u12_r0_binding as u12  # noqa: E402
import test_u12_r0_create_recovery as recovery  # noqa: E402
from test_u12_r0_binding import (  # noqa: E402
    CLOCK, DISPATCHER, PARENT_ID, PUBLISHER_AGENT, PUBLISHER_RUN, SOURCE_RUN,
    TARGET_AGENT, TARGET_ID, FakeCli, creation_package, execution_context_for,
    make_spec)

PRODUCTION = Path(r"D:\AI\multica-state\web-imagegen\dispatch\ledger.jsonl")


def command_counts(cli) -> dict:
    prefixes = {
        "create": ["issue", "create"],
        "assign": ["issue", "assign"],
        "comment_add": ["issue", "comment", "add"],
        "rerun": ["issue", "rerun"],
        "status": ["issue", "status"],
        "update": ["issue", "update"],
        "reads": [],
    }
    counts = {name: len(cli.commands_of(prefix))
              for name, prefix in prefixes.items() if prefix}
    reads = 0
    for argv in cli.commands:
        core = argv[1:]
        if any(core[:2] == p[:2] and len(p) == 2 for p in (
                ["issue", "get"], ["issue", "comment"], ["issue", "timeline"],
                ["issue", "runs"], ["issue", "children"], ["version"])):
            reads += 1
    counts["reads"] = reads
    counts["total"] = len(cli.commands)
    return counts


def recovery_summary(fx) -> dict:
    intent = fx.store.get(fx.intent_id)
    binding = intent["fields"][u12.R0B_FIELD]
    return {
        "state": intent["state"],
        "revision": intent["revision"],
        "transitions": len(intent["transitions"]),
        "recovery_evidence_events": len(fx.events_named(
            u12.E_RECOVERY_EVIDENCE)),
        "recovery_bound_events": len(fx.events_named(u12.E_RECOVERY_BOUND)),
        "execution_binding": isinstance(binding.get("execution_binding"), dict),
        "original_pin": binding.get("adapter_digest"),
        "contract_version": binding.get("contract_version"),
    }


# ---------------------------------------------------------------------------
# group 1
# ---------------------------------------------------------------------------
def case_exact_lf_recovery(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    ledger_before = fx.ledger_bytes()
    commands_before = len(fx.cli.commands)
    result = fx.recover()
    window = fx.cli.commands[commands_before:]
    ledger_after = fx.ledger_bytes()
    proof = fx.binding()["recovery_proof"]
    row = {
        "status": result["status"],
        "outcome": result.get("outcome"),
        "issue_id": result.get("issue_id"),
        "next_action": result.get("next_action"),
        "commands": command_counts(fx.cli),
        "recovery_window_commands": [argv[1:3] for argv in window],
        "ledger_bytes_before": len(ledger_before),
        "ledger_bytes_after": len(ledger_after),
        "prefix_preserved": ledger_after.startswith(ledger_before),
        "preserved_spec_digest": u12.digest(fx.binding()["creation_spec"]),
        "original_spec_unchanged": fx.binding()["creation_spec"] == fx.spec,
        "observed_relation": proof["observed"]["relation"],
        "transport_transformation": proof["transport"]["transformation"],
        "effective_transport_digest":
            proof["transport"]["effective_transport_body_digest"],
        "proof_digest": proof["proof_digest"],
        **recovery_summary(fx),
    }
    row["pass"] = (
        row["status"] == o2.S_TARGET_BOUND
        and row["commands"]["create"] == 1
        and row["commands"]["assign"] == 0
        and row["commands"]["comment_add"] == 0
        and row["commands"]["rerun"] == 0
        and row["prefix_preserved"]
        and row["original_spec_unchanged"]
        and row["observed_relation"] == "single-terminal-lf-removed")
    return row


def case_replay_read_only(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    fx.recover()
    ledger_before = fx.ledger_bytes()
    commands_before = len(fx.cli.commands)
    replay = fx.recover()
    row = {
        "status": replay["status"],
        "replayed": replay.get("replayed"),
        "ledger_unchanged": fx.ledger_bytes() == ledger_before,
        "commands_after_replay": len(fx.cli.commands) - commands_before,
        **recovery_summary(fx),
    }
    row["pass"] = (
        row["status"] == o2.S_TARGET_BOUND and row["replayed"] is True
        and row["ledger_unchanged"] and row["commands_after_replay"] == 0
        and row["recovery_evidence_events"] == 1
        and row["recovery_bound_events"] == 1)
    return row


# ---------------------------------------------------------------------------
# group 2
# ---------------------------------------------------------------------------
def case_transport_relation_matrix(tmp: Path) -> dict:
    base = ("alpha\n\nIntent marker: R0BI-0123456789abcdef\n"
            "Intent: DI-0123456789abcdef")
    cases = (
        ("exact", base, base, True),
        ("one_final_lf_removed", base + "\n", base, True),
        ("repeated_terminal_lf", base + "\n\n", base, False),
        ("trailing_space", base + " \n", base, False),
        ("trailing_tab", base + "\t\n", base, False),
        ("crlf_plus_trim", base + "\r\n", base, False),
        ("leading_whitespace", base + "\n", " " + base, False),
        ("interior_change", base + "\n", base.replace("alpha", "alphb"),
         False),
        ("marker_line_change", base + "\n", base.replace("R0BI", "R1BI"),
         False),
        ("space_retained_before_removed_lf", base + " \n", base + " ", False),
    )
    rows = []
    for label, source, observed, expected in cases:
        result = u12.single_terminal_lf_relation(source, observed)
        rows.append({"case": label, "accepted": result["accepted"],
                     "expected": expected,
                     "relation": result["relation"],
                     "pass": result["accepted"] == expected})
    return {"rows": rows, "all_pass": all(r["pass"] for r in rows),
            "pass": all(r["pass"] for r in rows)}


def case_transport_preparation_refusals(tmp: Path) -> dict:
    refused = []
    for source in ("body\n\n", "body\r\n", "body \n", "body\t\n", "body \r",
                   "body\x0b"):
        try:
            u12.prepare_transport_body(source)
            refused.append({"source": repr(source), "refused": False})
        except u12.R0BValidationRefused:
            refused.append({"source": repr(source), "refused": True})
    unknown = False
    try:
        u12.prepare_transport_body("body\n", profile="auto/9")
    except u12.R0BValidationRefused:
        unknown = True
    prepared = u12.prepare_transport_body("body\n")
    return {
        "refusals": refused,
        "unknown_profile_refused": unknown,
        "all_refused": all(r["refused"] for r in refused) and unknown,
        "prepared_transport": prepared["transport_body"] == "body",
        "prepared_transformation": prepared["transformation"],
        "raw_source_digest": prepared["source_raw_digest"],
        "historical_lf_digest": prepared["source_lf_digest"],
        "pass": (all(r["refused"] for r in refused) and unknown
                 and prepared["transport_body"] == "body"),
    }


def case_prospective_create(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    harness = object.__new__(recovery.TransportProfileTests)
    harness.root = tmp
    cli, store, factory, intent_id, result, source = harness._prospective()
    stored = store.get(
        intent_id)["fields"][u12.R0B_FIELD]["creation_spec"]
    prep = stored["transport_preparation"]
    return {
        "status": result["status"],
        "persisted_body_is_transport": stored["body"] == source[:-1],
        "persisted_has_final_lf": stored["body"].endswith("\n"),
        "sent_body_is_transport":
            cli.issues[TARGET_ID]["description"] == source[:-1],
        "body_digest_method": prep["body_digest_method"],
        "body_digest": stored["body_digest"],
        "source_raw_digest": prep["source_raw_digest"],
        "source_lf_digest": prep["source_lf_digest"],
        "transport_profile": prep["profile"],
        "transformation": prep["transformation"],
        "create_calls": len(cli.commands_of(["issue", "create"])),
        "pass": (result["status"] == o2.S_TARGET_BOUND
                 and stored["body"] == source[:-1]
                 and cli.issues[TARGET_ID]["description"] == source[:-1]
                 and len(cli.commands_of(["issue", "create"])) == 1),
    }


def case_legacy_exact_create(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    harness = object.__new__(recovery.TransportProfileTests)
    harness.root = tmp
    cli = recovery.LiveShapeCli()
    store = o2.DurableIntentStore(tmp / "legacy-ledger.jsonl")
    factory = u12.build_r0b_factory(
        store, runner=cli,
        artifact_blob_reader=u12._git_blob_reader(u12.ROOT))
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
    stored = store.get(
        intent_id)["fields"][u12.R0B_FIELD]["creation_spec"]
    return {
        "status": result["status"],
        "exact_body_sent": cli.issues[TARGET_ID]["description"] == spec["body"],
        "transport_preparation_absent": "transport_preparation" not in stored,
        "create_calls": len(cli.commands_of(["issue", "create"])),
        "pass": (result["status"] == o2.S_TARGET_BOUND
                 and cli.issues[TARGET_ID]["description"] == spec["body"]
                 and "transport_preparation" not in stored),
    }


def case_platform_mutation(tmp: Path) -> dict:
    tmp.mkdir(parents=True, exist_ok=True)
    harness = object.__new__(recovery.TransportProfileTests)
    harness.root = tmp
    cli = recovery.MangledTransportCli()
    store = o2.DurableIntentStore(tmp / "mangled-ledger.jsonl")
    factory = u12.build_r0b_factory(
        store, runner=cli,
        artifact_blob_reader=u12._git_blob_reader(u12.ROOT))
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
    return {
        "status": result["status"],
        "detail": result.get("detail"),
        "create_calls": len(cli.commands_of(["issue", "create"])),
        "rerun_calls": len(cli.commands_of(["issue", "rerun"])),
        "pass": (result["status"] == o2.S_CREATE_AMBIGUOUS
                 and len(cli.commands_of(["issue", "create"])) == 1
                 and not cli.commands_of(["issue", "rerun"])),
    }


# ---------------------------------------------------------------------------
# group 3
# ---------------------------------------------------------------------------
def refusal_case(name, mutate, expected_reason, constructor=None):
    def run(tmp: Path) -> dict:
        kwargs = {}
        if constructor:
            kwargs = constructor()
        fx = recovery.RecoveryFixture(tmp, **kwargs)
        mutate(fx)
        result = fx.recover()
        return {
            "case": name,
            "expected_reason": expected_reason,
            "observed_reason": result.get("reason"),
            "outcome": result.get("outcome"),
            "status": result.get("status"),
            "external_writes": result.get("external_writes"),
            "state_after": fx.store.get(fx.intent_id)["state"],
            "create_calls": len(fx.cli.commands_of(["issue", "create"])),
            "assign_calls": len(fx.cli.commands_of(["issue", "assign"])),
            "rerun_calls": len(fx.cli.commands_of(["issue", "rerun"])),
            "pass": (result.get("outcome") == "RECOVERY_REFUSED"
                     and result.get("reason") == expected_reason
                     and fx.store.get(fx.intent_id)["state"]
                     == o2.S_CREATE_AMBIGUOUS),
        }
    return run


def _superseded_authority_constructor():
    altered = recovery.real_readiness_manifest()
    altered["generated_at"] = "2026-09-12T00:00:00Z"
    return {"authority_reader": recovery.FixedAuthorityReader(altered)}


def _missing_authority_constructor():
    return {"authority_reader": None}


def case_wrong_target(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    other = "01a0bbbb-0000-7000-8000-00000000ffff"
    result = fx.recover(expected_target_id=other,
                        recovery_decision=fx.decision(
                            expected_target_id=other))
    return {"case": "wrong_expected_target",
            "observed_reason": result.get("reason"),
            "outcome": result.get("outcome"),
            "state_after": fx.store.get(fx.intent_id)["state"],
            "create_calls": len(fx.cli.commands_of(["issue", "create"])),
            "pass": result.get("reason") == u12.REASON_RECOVERY_IDENTITY}


REJECTION_CASES = (
    refusal_case("title_mismatch",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "title", "different title"),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("parent_mismatch",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "parent_issue_id",
                     "01a0cccc-0000-7000-8000-00000000ffff"),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("project_mismatch",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "project_id", "other-project"),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("priority_mismatch",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "priority", "low"),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("creator_mismatch",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "creator_id", TARGET_AGENT),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("moving_target_revision",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "revision", 2),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("assignee_present",
                 lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                     "assignee_id", TARGET_AGENT),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("non_backlog_status",
                 lambda fx: fx.cli.issues[TARGET_ID].update(
                     {"status": "todo", "status_category": "todo"}),
                 u12.REASON_RECOVERY_TARGET),
    refusal_case("nonempty_runs",
                 lambda fx: fx.cli.runs[TARGET_ID].append(
                     {"id": "RUN-X", "issue_id": TARGET_ID,
                      "agent_id": TARGET_AGENT, "status": "queued",
                      "attempt": 1}),
                 u12.REASON_RECOVERY_EFFECT),
    refusal_case("prior_ownership_attempt",
                 lambda fx: fx.store.append_event(
                     fx.intent_id, u12.E_OWNERSHIP_ISSUING,
                     actor=DISPATCHER, data={}),
                 u12.REASON_RECOVERY_EFFECT),
    refusal_case("prior_publication_attempt",
                 lambda fx: fx.store.append_event(
                     fx.intent_id, u12.E_PUBLICATION_ISSUING,
                     actor=DISPATCHER, data={}),
                 u12.REASON_RECOVERY_EFFECT),
    refusal_case("unknown_effect",
                 lambda fx: fx.store.append_event(
                     fx.intent_id, "some_unknown_effect",
                     actor=DISPATCHER, data={}),
                 u12.REASON_RECOVERY_EFFECT),
    refusal_case("second_create_attempt",
                 lambda fx: fx.store.append_event(
                     fx.intent_id, u12.E_CREATE_ISSUING,
                     actor=DISPATCHER, data={}),
                 u12.REASON_RECOVERY_UNSUPPORTED),
    refusal_case("truncated_comment_read",
                 lambda fx: setattr(fx.cli, "truncate_comment_list", True),
                 u12.REASON_MATERIAL_UNAVAILABLE),
    refusal_case("truncated_timeline_read",
                 lambda fx: setattr(fx.cli, "truncate_timeline", True),
                 u12.REASON_MATERIAL_UNAVAILABLE),
    refusal_case("truncated_run_read",
                 lambda fx: setattr(fx.cli, "truncate_runs", True),
                 u12.REASON_MATERIAL_UNAVAILABLE),
    refusal_case("truncated_children_read",
                 lambda fx: setattr(fx.cli, "truncate_children", True),
                 u12.REASON_MATERIAL_UNAVAILABLE),
    refusal_case("stale_artifact",
                 lambda fx: setattr(fx.factory, "artifact_blob_reader",
                                    lambda c, p: b"changed bytes"),
                 u12.REASON_MATERIAL_STALE),
    refusal_case("missing_artifact_reader",
                 lambda fx: (setattr(fx.factory, "artifact_blob_reader", None),
                             setattr(fx.factory, "artifact_root", None)),
                 u12.REASON_MATERIAL_UNAVAILABLE),
    refusal_case("superseded_authority", lambda fx: None,
                 u12.REASON_MATERIAL_STALE,
                 constructor=_superseded_authority_constructor),
    refusal_case("missing_authority_source", lambda fx: None,
                 u12.REASON_PREFLIGHT_INPUT_MISSING,
                 constructor=_missing_authority_constructor),
)


def case_duplicate_candidates(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    duplicate = copy.deepcopy(fx.cli.children[PARENT_ID][0])
    duplicate["id"] = "01a0dddd-0000-7000-8000-00000000ffff"
    fx.cli.children[PARENT_ID].append(duplicate)
    result = fx.recover()
    return {"case": "duplicate_identity_candidates",
            "observed_reason": result.get("reason"),
            "outcome": result.get("outcome"),
            "create_calls": len(fx.cli.commands_of(["issue", "create"])),
            "pass": result.get("reason") == u12.REASON_RECOVERY_IDENTITY}


def case_missing_candidate(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    marker_line = "Intent marker: " + fx.spec["marker"]
    for holder in (fx.cli.issues[TARGET_ID], fx.cli.children[PARENT_ID][0]):
        holder["description"] = holder["description"].replace(
            marker_line, "Intent marker: R0BI-0000000000000000")
    result = fx.recover()
    return {"case": "missing_identity_candidate",
            "observed_reason": result.get("reason"),
            "outcome": result.get("outcome"),
            "create_calls": len(fx.cli.commands_of(["issue", "create"])),
            "pass": result.get("reason") == u12.REASON_RECOVERY_IDENTITY}


def case_marker_substring(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    text = "the marker " + fx.spec["marker"] + " appears inline only\n"
    fx.cli.issues[TARGET_ID]["description"] = text
    fx.cli.children[PARENT_ID][0]["description"] = text
    result = fx.recover()
    return {"case": "marker_substring_only",
            "observed_reason": result.get("reason"),
            "outcome": result.get("outcome"),
            "pass": result.get("reason") == u12.REASON_RECOVERY_IDENTITY}


def case_moving_collection(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
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
    return {"case": "moves_during_collection",
            "observed_reason": result.get("reason"),
            "outcome": result.get("outcome"),
            "create_calls": len(fx.cli.commands_of(["issue", "create"])),
            "pass": result.get("reason") == u12.REASON_MOVING_EVIDENCE}


# ---------------------------------------------------------------------------
# group 4
# ---------------------------------------------------------------------------
def case_bare_changed_pin(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    intent = fx.store.get(fx.intent_id)
    tampered = copy.deepcopy(intent)
    tampered["fields"][u12.R0B_FIELD]["adapter_digest"] = "sha256:" + "1" * 64
    commands_before = len(fx.cli.commands)
    try:
        with mock.patch.object(fx.store, "get", return_value=tampered):
            fx.recover()
        outcome = "NOT_REFUSED"
    except u12.R0BDowngradeRefused as exc:
        outcome = exc.code
    return {"case": "bare_changed_pin", "outcome": outcome,
            "external_calls": len(fx.cli.commands) - commands_before,
            "pass": outcome == u12.R0BDowngradeRefused.code}


def case_old_executor_refusal(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    fx.recover()
    commands_before = len(fx.cli.commands)
    outcomes = []
    for label, call in (
            ("validate", lambda: fx.old_factory.validate(fx.intent_id)),
            ("assign_ownership_once",
             lambda: fx.old_factory.assign_ownership_once(
                 fx.intent_id, actor=DISPATCHER)),
            ("recover", lambda: fx.old_factory.recover(
                fx.intent_id, actor=DISPATCHER))):
        try:
            call()
            outcomes.append({"entrypoint": label, "refused": False})
        except fx.old.R0BContractError:
            outcomes.append({"entrypoint": label, "refused": True})
    return {
        "case": "actual_old_executor_fence",
        "predecessor_adapter_digest": recovery._PREDECESSOR["digest"],
        "outcomes": outcomes,
        "external_calls_after_recovery": len(fx.cli.commands)
        - commands_before,
        "all_refused": all(o["refused"] for o in outcomes),
        "state": fx.store.get(fx.intent_id)["state"],
        "pass": all(o["refused"] for o in outcomes)
        and len(fx.cli.commands) == commands_before,
    }


def case_execution_fences(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    audit_before = None
    try:
        audit_before = fx.factory.validate(fx.intent_id)
    except u12.R0BError as exc:  # pragma: no cover - audit must be readable
        audit_before = {"error": getattr(exc, "code", None)}
    execution_outcomes = []
    for label, call in (
            ("assign_ownership_once",
             lambda: fx.factory.assign_ownership_once(
                 fx.intent_id, actor=DISPATCHER)),
            ("create_target_once",
             lambda: fx.factory.create_target_once(
                 fx.intent_id, actor=DISPATCHER))):
        try:
            call()
            execution_outcomes.append({"entrypoint": label, "refused": False})
        except u12.R0BCompatibilityRefused:
            execution_outcomes.append({"entrypoint": label, "refused": True})
    fx.recover()
    recovered_audit = fx.factory.validate(fx.intent_id)
    cross = copy.deepcopy(fx.store.get(fx.intent_id))
    proof = cross["fields"][u12.R0B_FIELD]["recovery_proof"]
    proof["intent_id"] = "DI-" + "9" * 16
    proof.pop("proof_digest")
    proof["proof_digest"] = u12.digest(proof)
    cross["fields"][u12.R0B_FIELD]["execution_binding"][
        "recovery_proof_digest"] = proof["proof_digest"]
    try:
        with mock.patch.object(fx.store, "get", return_value=cross):
            fx.factory.validate(fx.intent_id)
        cross_outcome = "NOT_REFUSED"
    except u12.R0BValidationRefused:
        cross_outcome = "R0BValidationRefused"
    return {
        "case": "execution_fences",
        "old_history_audit_contract_version": audit_before.get(
            "contract_version"),
        "pre_recovery_execution_refusals": execution_outcomes,
        "recovered_audit_contract_version": recovered_audit.get(
            "contract_version"),
        "cross_intent_proof_outcome": cross_outcome,
        "pass": (audit_before.get("contract_version")
                 == u12.PREDECESSOR_CONTRACT_VERSION
                 and all(o["refused"] for o in execution_outcomes)
                 and recovered_audit.get("contract_version")
                 == u12.CONTRACT_VERSION
                 and cross_outcome == "R0BValidationRefused"),
    }


def case_frozen_pins(tmp: Path) -> dict:
    rebuilt = u12.build_artifact_dependency_digest()
    entries = rebuilt["entries"]
    manifest = json.loads(
        (ROOT / "adapters/multica/u12-p0r/readiness-manifest.json")
        .read_text(encoding="utf-8"))
    return {
        "case": "frozen_pins",
        "strict_gate_lf": u12.adapter_digest(
            ROOT / "tools/u12_strict_receipt.py"),
        "chandoff_intent_lf": u12.adapter_digest(
            ROOT / "tools/chandoff_intent.py"),
        "readiness_manifest_entry": entries[
            "adapters/multica/u12-p0r/readiness-manifest.json"]["sha256"],
        "readiness_manifest_self_digest": manifest["manifest_digest"],
        "artifact_dependency_digest": rebuilt["digest"],
        "pass": (u12.adapter_digest(ROOT / "tools/u12_strict_receipt.py")
                 == "sha256:f37ed0912ecbdc3944529cffc6d7a28bc9a41d1faba8cd1e2"
                    "84eab6b0ccf4bed"
                 and u12.adapter_digest(ROOT / "tools/chandoff_intent.py")
                 == "sha256:0544046fa2ca97c12e6c48de574074df9de5715a950a"
                    "d75a783cf31853037032"),
    }


# ---------------------------------------------------------------------------
# group 5
# ---------------------------------------------------------------------------
def case_crash_before_evidence(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    original = fx.store.append_event

    def crash(intent_id, name, **kwargs):
        if name == u12.E_RECOVERY_EVIDENCE:
            raise o2.IntentError("simulated crash before evidence")
        return original(intent_id, name, **kwargs)

    crashed = False
    try:
        with mock.patch.object(fx.store, "append_event", side_effect=crash):
            fx.recover()
    except o2.IntentError:
        crashed = True
    state_after_crash = fx.store.get(fx.intent_id)["state"]
    result = fx.recover()
    return {
        "case": "crash_before_evidence",
        "crashed": crashed,
        "state_after_crash": state_after_crash,
        "state_after_retry": result["status"],
        "evidence_events": len(fx.events_named(u12.E_RECOVERY_EVIDENCE)),
        "create_calls": len(fx.cli.commands_of(["issue", "create"])),
        "pass": crashed and state_after_crash == o2.S_CREATE_AMBIGUOUS
        and result["status"] == o2.S_TARGET_BOUND
        and len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 1,
    }


def case_crash_after_evidence(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    original = fx.store.transition
    calls = {"n": 0}

    def crash(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise o2.IntentError("simulated crash after evidence append")
        return original(*args, **kwargs)

    crashed = False
    try:
        with mock.patch.object(fx.store, "transition", side_effect=crash):
            fx.recover()
    except o2.IntentError:
        crashed = True
    state_after_crash = fx.store.get(fx.intent_id)["state"]
    evidence_after_crash = len(fx.events_named(u12.E_RECOVERY_EVIDENCE))
    result = fx.recover()
    return {
        "case": "crash_after_evidence_before_transition",
        "crashed": crashed,
        "state_after_crash": state_after_crash,
        "evidence_events_after_crash": evidence_after_crash,
        "state_after_retry": result["status"],
        "evidence_events_final": len(fx.events_named(u12.E_RECOVERY_EVIDENCE)),
        "bound_events_final": len(fx.events_named(u12.E_RECOVERY_BOUND)),
        "create_calls": len(fx.cli.commands_of(["issue", "create"])),
        "pass": crashed and state_after_crash == o2.S_CREATE_AMBIGUOUS
        and evidence_after_crash == 1 and result["status"] == o2.S_TARGET_BOUND
        and len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 2
        and len(fx.events_named(u12.E_RECOVERY_BOUND)) == 1,
    }


def case_crash_after_transition(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    original = fx.store.append_event

    def crash(intent_id, name, **kwargs):
        if name == u12.E_RECOVERY_BOUND:
            raise o2.IntentError("simulated crash before acknowledgement")
        return original(intent_id, name, **kwargs)

    crashed = False
    try:
        with mock.patch.object(fx.store, "append_event", side_effect=crash):
            fx.recover()
    except o2.IntentError:
        crashed = True
    state_after_crash = fx.store.get(fx.intent_id)["state"]
    commands_before = len(fx.cli.commands)
    replay = fx.recover()
    return {
        "case": "crash_after_transition_before_ack",
        "crashed": crashed,
        "state_after_crash": state_after_crash,
        "replayed": replay.get("replayed"),
        "commands_on_replay": len(fx.cli.commands) - commands_before,
        "evidence_events": len(fx.events_named(u12.E_RECOVERY_EVIDENCE)),
        "bound_events": len(fx.events_named(u12.E_RECOVERY_BOUND)),
        "create_calls": len(fx.cli.commands_of(["issue", "create"])),
        "pass": crashed and state_after_crash == o2.S_TARGET_BOUND
        and replay.get("replayed") is True
        and len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 1,
    }


def case_competing_lease(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    fx.store.claim(fx.intent_id, "other-writer")
    commands_before = len(fx.cli.commands)
    refused = False
    try:
        fx.recover()
    except o2.LeaseHeldError:
        refused = True
    return {
        "case": "competing_lease",
        "refused": refused,
        "commands_before_refusal": len(fx.cli.commands) - commands_before,
        "evidence_events": len(fx.events_named(u12.E_RECOVERY_EVIDENCE)),
        "state": fx.store.get(fx.intent_id)["state"],
        "pass": refused and len(fx.cli.commands) == commands_before,
    }


def case_concurrent_callers(tmp: Path) -> dict:
    import threading
    fx = recovery.RecoveryFixture(tmp)
    path = tmp / "ledger.jsonl"
    barrier = threading.Barrier(2)
    results: dict = {}

    def worker(name):
        barrier.wait()
        store = o2.DurableIntentStore(path)
        factory = u12.build_r0b_factory(
            store, runner=fx.cli,
            artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
            authority_reader=u12.ReadinessManifestAuthorityReader())
        try:
            results[name] = factory.recover_created_target(
                fx.intent_id, expected_target_id=TARGET_ID,
                recovery_decision=fx.decision(), actor=name)
        except o2.IntentError as exc:
            results[name] = {"error": type(exc).__name__}

    threads = [threading.Thread(target=worker, args=(f"w{i}",))
               for i in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    intent = fx.store.get(fx.intent_id)
    return {
        "case": "concurrent_callers",
        "results": results,
        "state": intent["state"],
        "evidence_events": len(fx.events_named(u12.E_RECOVERY_EVIDENCE)),
        "bound_events": len(fx.events_named(u12.E_RECOVERY_BOUND)),
        "create_calls": len(fx.cli.commands_of(["issue", "create"])),
        "pass": intent["state"] == o2.S_TARGET_BOUND
        and len(fx.events_named(u12.E_RECOVERY_EVIDENCE)) == 1
        and len(fx.events_named(u12.E_RECOVERY_BOUND)) == 1
        and len(fx.cli.commands_of(["issue", "create"])) == 1,
    }


def case_attempt_flags(tmp: Path) -> dict:
    fx = recovery.RecoveryFixture(tmp)
    fx.recover()
    creates = len(fx.cli.commands_of(["issue", "create"]))
    replay = fx.factory.create_target_once(fx.intent_id, actor=DISPATCHER)
    instruction = fx.factory.recover(fx.intent_id, actor=DISPATCHER)
    return {
        "case": "attempt_flags_not_reset",
        "create_replay": replay.get("replayed"),
        "create_calls_after": len(fx.cli.commands_of(["issue", "create"])),
        "classification": instruction.get("classification"),
        "performed": instruction.get("performed"),
        "pass": replay.get("replayed") is True
        and len(fx.cli.commands_of(["issue", "create"])) == creates,
    }


# ---------------------------------------------------------------------------
# group 6
# ---------------------------------------------------------------------------
def _post_recovery_fixture(tmp: Path):
    fx = recovery.RecoveryFixture(tmp)
    fx.recover()
    fx.factory.assign_ownership_once(fx.intent_id, actor=DISPATCHER)
    fx.factory.bind_execution_package(
        fx.intent_id, execution_context=execution_context_for(fx.cli),
        actor=DISPATCHER)
    fx.factory.publish_handoff_once(
        fx.intent_id, actor=DISPATCHER, publisher_run_id=PUBLISHER_RUN,
        prepared_by="01 Engineering Lead", prepared_at=CLOCK)
    return fx


def case_post_recovery_lifecycle(tmp: Path) -> dict:
    fx = _post_recovery_fixture(tmp)
    armed = fx.arm()
    triggered = fx.trigger()
    return {
        "case": "post_recovery_lifecycle",
        "armed": armed.get("status"),
        "triggered": triggered.get("status"),
        "selected_trigger": (armed.get("plan") or {}).get("selected_trigger"),
        "rerun_calls": len(fx.cli.commands_of(["issue", "rerun"])),
        "create_calls": len(fx.cli.commands_of(["issue", "create"])),
        "assign_calls": len(fx.cli.commands_of(["issue", "assign"])),
        "comment_add_calls": len(
            fx.cli.commands_of(["issue", "comment", "add"])),
        "preflight_checkpoints": [e["data"]["checkpoint"]
                                  for e in fx.events_named(u12.E_PREFLIGHT)],
        "pass": armed.get("status") == o2.S_TRIGGER_READY
        and triggered.get("status") == o2.S_RUN_CORRELATED
        and len(fx.cli.commands_of(["issue", "rerun"])) == 1
        and len(fx.cli.commands_of(["issue", "create"])) == 1,
    }


def case_drift_after_arm(tmp: Path) -> dict:
    fx = _post_recovery_fixture(tmp)
    armed = fx.arm()
    fx.cli.issues[TARGET_ID]["description"] = "drifted after arm"
    result = fx.trigger()
    return {
        "case": "drift_after_arm",
        "armed": armed.get("status"),
        "triggered": result.get("status"),
        "reason": result.get("reason"),
        "rerun_calls": len(fx.cli.commands_of(["issue", "rerun"])),
        "pass": armed.get("status") == o2.S_TRIGGER_READY
        and result.get("status") == o2.S_REFRESH_REQUIRED
        and len(fx.cli.commands_of(["issue", "rerun"])) == 0,
    }


def case_ambiguous_trigger(tmp: Path) -> dict:
    fx = _post_recovery_fixture(tmp)
    fx.arm()
    fx.cli._rerun = lambda core: (
        0, json.dumps({"run": {"id": "RUN-1", "issue_id": core[2],
                               "agent_id": TARGET_AGENT,
                               "status": "queued"}}), "")
    first = fx.trigger()
    second = fx.trigger()
    return {
        "case": "ambiguous_trigger_reconcile",
        "first": first.get("status"),
        "second": second.get("status"),
        "replayed": second.get("replayed"),
        "rerun_calls": len(fx.cli.commands_of(["issue", "rerun"])),
        "pass": first.get("status") == o2.S_TRIGGER_AMBIGUOUS
        and second.get("status") == o2.S_TRIGGER_AMBIGUOUS
        and second.get("replayed") is True
        and len(fx.cli.commands_of(["issue", "rerun"])) == 1,
    }


GROUPS = {
    "1_exact_single_terminal_lf_recovery": (
        case_exact_lf_recovery, case_replay_read_only),
    "2_transport_preparation": (
        case_transport_relation_matrix, case_transport_preparation_refusals,
        case_prospective_create, case_legacy_exact_create,
        case_platform_mutation),
    "3_target_evidence_material_refusals": (
        case_wrong_target, case_duplicate_candidates, case_missing_candidate,
        case_marker_substring, case_moving_collection) + REJECTION_CASES,
    "4_pin_proof_and_old_executor_fences": (
        case_bare_changed_pin, case_execution_fences, case_old_executor_refusal,
        case_frozen_pins),
    "5_crash_replay_concurrency": (
        case_crash_before_evidence, case_crash_after_evidence,
        case_crash_after_transition, case_competing_lease,
        case_concurrent_callers, case_attempt_flags),
    "6_post_recovery_lifecycle": (
        case_post_recovery_lifecycle, case_drift_after_arm,
        case_ambiguous_trigger),
}


def file_digest_lf(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def production_observation() -> dict:
    try:
        raw = PRODUCTION.read_bytes()
    except OSError as exc:
        return {"readable": False, "error": type(exc).__name__}
    observation = {
        "readable": True,
        "bytes": len(raw),
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    try:
        records = [json.loads(line)
                   for line in raw.decode("utf-8").splitlines()
                   if line.strip()]
        folded = o2.fold_records(records)
        intent = folded["intents"].get("DI-e5e2b856f6b7cd36")
        if intent is not None:
            observation["live_intent_state"] = intent["state"]
            observation["live_intent_revision"] = intent["revision"]
            observation["live_create_attempts"] = len(
                [e for e in intent["events"]
                 if e.get("name") == u12.E_CREATE_ISSUING])
            observation["live_ownership_events"] = len(
                [e for e in intent["events"]
                 if e.get("name") == u12.E_OWNERSHIP_ISSUING])
            observation["live_publication_events"] = len(
                [e for e in intent["events"]
                 if e.get("name") == u12.E_PUBLICATION_ISSUING])
    except (ValueError, o2.IntentError) as exc:
        observation["fold_error"] = type(exc).__name__
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    groups = []
    failures = []
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
        tmp_root = Path(tmp)
        for group, case_functions in GROUPS.items():
            rows = []
            for index, function in enumerate(case_functions):
                case_dir = tmp_root / f"{group}-{index:02d}-{function.__name__}"
                case_dir.mkdir()
                try:
                    row = function(case_dir)
                except Exception as exc:  # noqa: BLE001 - evidence must be honest
                    row = {"status": f"EXCEPTION:{type(exc).__name__}",
                           "detail": str(exc)[:200], "pass": False}
                row.setdefault("pass", False)
                row["case_function"] = function.__name__
                rows.append(row)
                if not row.get("pass"):
                    failures.append(f"{group}/{function.__name__}")
            groups.append({"group": group, "rows": rows,
                           "passed": sum(1 for r in rows if r["pass"]),
                           "total": len(rows)})
    report = {
        "kind": "u12_r0b_create_recovery_acceptance_matrix",
        "schema_version": "U12-R0B/1.1",
        "task": "YZT-84",
        "contract": ("U12_R0_CREATE_RECOVERY_DECISION.md raw sha256 "
                     "1fedade6694d6d15f076a1ecb84aedfa680c93767cd3ec27ac94b"
                     "594220b101e"),
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "isolation": ("fixture CLI + temporary JSONL ledgers inside the "
                      "workdir; predecessor record produced by the actual "
                      "b49630b module bytes; no live Multica call, no "
                      "production-ledger write, no Canonical write"),
        "adapter_digest_lf": u12.adapter_digest(
            ROOT / "tools/u12_r0_binding.py"),
        "module_digests_lf": {
            "tools/u12_r0_binding.py": file_digest_lf(
                ROOT / "tools/u12_r0_binding.py"),
            "tools/tests/test_u12_r0_create_recovery.py": file_digest_lf(
                ROOT / "tools/tests/test_u12_r0_create_recovery.py"),
            "tools/tests/test_u12_r0_binding.py": file_digest_lf(
                ROOT / "tools/tests/test_u12_r0_binding.py"),
            "adapters/multica/u12-r0b-recovery/"
            "reproduce_create_recovery.py": file_digest_lf(
                ROOT / "adapters/multica/u12-r0b-recovery/"
                       "reproduce_create_recovery.py"),
        },
        "frozen_pins_lf": {
            "tools/u12_strict_receipt.py": u12.adapter_digest(
                ROOT / "tools/u12_strict_receipt.py"),
            "tools/chandoff_intent.py": u12.adapter_digest(
                ROOT / "tools/chandoff_intent.py"),
        },
        "production_ledger_observation": production_observation(),
        "groups": groups,
        "group_totals": {g["group"]: f"{g['passed']}/{g['total']}"
                         for g in groups},
        "row_totals": {
            "passed": sum(g["passed"] for g in groups),
            "total": sum(g["total"] for g in groups),
        },
        "failures": failures,
        "verdict": ("ALL_SIX_VALIDATION_GROUPS_PASS" if not failures
                    else "FAILURES_PRESENT"),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    print(json.dumps({"verdict": report["verdict"],
                      "group_totals": report["group_totals"],
                      "failures": failures},
                     ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
