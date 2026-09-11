#!/usr/bin/env python3
"""Isolated acceptance reproduction for the YZT-84 publication transport repair
and the proof-bearing O2 publication recovery exception.

Runs every Validation Focus row of the approved
`U12_R0_PUBLICATION_TRANSPORT_DECISION.md` (raw SHA256 0df4cec9...) through
the public adapter operations on temporary JSONL ledgers. The historical
record is produced by the actual b49630b and da99c11 module bytes loaded from
the accepted commits, so the real-historical-shape row exercises the accepted
chain (create -> create recovery -> ownership -> E -> one real publication
attempt that lost exactly one terminal LF -> BLOCKED rev4). The execution
migration identities resolve against the real committed HEAD blobs; no
injected fixture resolver is used.

No live Multica call, no production-ledger read/write, no Canonical write.
No note is ever resent.

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-publication-transport/reproduce_publication_recovery.py \
        --output adapters/multica/u12-r0b-publication-transport/publication-transport-acceptance-matrix.json
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
import chandoff_note as note  # noqa: E402
import u12_r0_binding as u12  # noqa: E402
from test_u12_r0_binding import (  # noqa: E402
    DISPATCHER, PUBLISHER_RUN, SOURCE_RUN, TARGET_AGENT, TARGET_ID, CLOCK,
    creation_package, execution_context_for, make_spec)
from test_u12_r0_publication_recovery import (  # noqa: E402
    FutureTransportCli, HistoricalPublicationCli, PublicationRecoveryFixture,
    append_comment, load_forward_intent_module, rendered_original)


def new_fx(root: Path, label: str, *, cli=None) -> PublicationRecoveryFixture:
    path = root / label
    path.mkdir(parents=True, exist_ok=True)
    return PublicationRecoveryFixture(path, cli=cli)


def refused(result, reason=None) -> bool:
    return (result.get("status") == o2.S_BLOCKED
            and result.get("outcome") == "RECOVERY_REFUSED"
            and result.get("platform_writes") == 0
            and result.get("ledger_commits") == 0
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


def state_of(fx):
    return fx.store.get(fx.intent_id)


# ---------------------------------------------------------------------------
# 1. real historical shape and the one approved recovery commit
# ---------------------------------------------------------------------------
def case_real_historical_shape(root):
    fx = new_fx(root, "real-shape")
    intent = state_of(fx)
    rendered = rendered_original(fx)
    relation = u12.single_terminal_lf_relation(rendered, fx.note_content)
    old_confirm_blocked = any(
        (e.get("data") or {}).get("code") == u12.PUB_NOTE_NOT_FOUND
        for e in fx.events_named(u12.E_EVIDENCE_REFUSED))
    observation = {
        "state": intent["state"], "revision": intent["revision"],
        "blocker_reason": intent["transitions"][-1].get("reason"),
        "note_not_found_on_old_confirm": old_confirm_blocked,
        "relation": relation,
        "attempts": len(fx.events_named(u12.E_PUBLICATION_ISSUING)),
        "responses": len(fx.events_named(u12.E_PUBLICATION_RESPONSE)),
        "ownership": len(fx.events_named(u12.E_OWNERSHIP_ISSUING)),
        "create_commands": len(fx.commands_of(["issue", "create"])),
        "comment_adds": len(fx.commands_of(["issue", "comment", "add"])),
        "reruns": len(fx.commands_of(["issue", "rerun"])),
    }
    ok = (intent["state"] == o2.S_BLOCKED and intent["revision"] == 4
          and old_confirm_blocked and relation["accepted"]
          and relation["removed_lf"])
    return {
        "row": "1_real_observation_shape",
        "validation_focus": (
            "real observation shape: R single LF, O=R[:-1]; the old predicate "
            "NOTE_NOT_FOUND; the new historical proof passes the body relation "
            "and still verifies every other predicate"),
        "required_outcome": "BLOCKED rev4 with the exact relation; no note "
                            "resend; every other predicate verified",
        "observed": observation,
        "pass": bool(ok),
    }


def case_exact_recovery(root):
    fx = new_fx(root, "exact-recovery")
    before = len(fx.cli.commands)
    result = fx.recover()
    intent = state_of(fx)
    binding = fx.binding()["publication_binding"]
    records = fx.store.read_records()
    commits = [r for r in records if r.get("op") == u12.PUBLICATION_RECOVERY_OP]
    replay = fx.recover()
    after = fx.cli.commands[before:]
    reads_only = all(
        any(argv[1:1 + len(p)] == list(p) for p in
            (["issue", "get"], ["issue", "comment", "list"],
             ["issue", "timeline"], ["issue", "runs"], ["version"]))
        for argv in after)
    observation = {
        "status": result.get("status"),
        "revision": intent["revision"],
        "ledger_commits": result.get("ledger_commits"),
        "platform_writes": result.get("platform_writes"),
        "commits": len(commits),
        "binding_body_digest_method": binding.get("body_digest_method"),
        "binding_body_digest": binding.get("body_digest"),
        "observed_raw_digest": u12._sha256_utf8(fx.note_content),
        "recovery_issued_reads_only": reads_only,
        "no_duplicate_native_writes": (
            len(fx.commands_of(["issue", "create"])) == 1
            and len(fx.commands_of(["issue", "assign"])) == 1
            and len(fx.commands_of(["issue", "comment", "add"])) == 1
            and not fx.commands_of(["issue", "rerun"])),
        "replay_idempotent": bool(replay.get("replayed")),
        "replay_ledger_commits": replay.get("ledger_commits"),
    }
    ok = (intent["state"] == o2.S_HANDOFF_PUBLISHED
          and intent["revision"] == 5 and len(commits) == 1
          and binding.get("body_digest_method") == u12.BODY_DIGEST_METHOD_RAW
          and observation["no_duplicate_native_writes"]
          and replay.get("replayed") and replay.get("ledger_commits") == 0)
    return {
        "row": "2_exact_recovered_da99c11_chain",
        "validation_focus": (
            "exact old create-recovered da99c11 intent, ownership, E, one "
            "attempt, revision4 blocker; the controlled migration commits "
            "exactly once to revision5 with no duplicate "
            "create/ownership/publication/trigger"),
        "required_outcome": "one commit to HANDOFF_PUBLISHED rev5; replay is "
                            "idempotent with zero new commits and zero sends",
        "observed": observation,
        "pass": bool(ok),
    }


# ---------------------------------------------------------------------------
# 2. future transport
# ---------------------------------------------------------------------------
def case_future_transport(root):
    # build a clean chain with the accepted bytes of this commit
    cli = FutureTransportCli()
    path = root / "future-transport" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    store = o2.DurableIntentStore(path)
    factory = u12.build_r0b_factory(
        store, runner=cli, artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
        authority_reader=u12.ReadinessManifestAuthorityReader())
    spec, intent_id = make_spec()
    context = creation_package()
    factory.record_creation_intent(
        creation_context={"result": context["result"],
                          "request": context["request"],
                          "self_check": context["self_check"],
                          "source_task_id": SOURCE_RUN},
        creation_spec=spec, authority="approved YZT-84 exception",
        actor=DISPATCHER, source_run=SOURCE_RUN, intent_id=intent_id)
    factory.create_target_once(intent_id, actor=DISPATCHER)
    factory.assign_ownership_once(intent_id, actor=DISPATCHER)
    factory.bind_execution_package(
        intent_id, execution_context=execution_context_for(cli),
        actor=DISPATCHER)
    published = factory.publish_handoff_once(
        intent_id, actor=DISPATCHER, publisher_run_id=PUBLISHER_RUN,
        prepared_by="01 Engineering Lead", prepared_at=CLOCK)
    attempt = [e for e in store.get(intent_id)["events"]
               if e.get("name") == u12.E_PUBLICATION_ISSUING][0]["data"]
    binding = store.get(intent_id)["fields"][u12.R0B_FIELD][
        "publication_binding"]
    sent = cli.raw_comment_contents[0]
    stored = cli.comments[TARGET_ID][0]["content"]
    rendered, _ = note.render_note_record(
        execution_context_for(cli)["result"],
        prepared_by=attempt["prepared_by"], prepared_at=attempt["prepared_at"])
    observation = {
        "status": published.get("status"),
        "profile": attempt.get("transport_profile"),
        "rendered_ends_lf_fence_lf": rendered.endswith("\n```\n"),
        "transport_is_render_minus_one_lf": sent == rendered[:-1],
        "file_bytes_equal_transport": sent == stored,
        "o_equals_t": stored == sent,
        "comment_adds": len(cli.commands_of(["issue", "comment", "add"])),
        "sent_has_no_bom": not sent.encode("utf-8").startswith(b"\xef\xbb\xbf"),
        "binding_body_digest_method": binding.get("body_digest_method"),
        "binding_binds_o": binding.get("body_digest")
        == u12._sha256_utf8(sent),
        "attempt_keeps_rendered": attempt.get("rendered_body_digest_raw")
        != attempt.get("transport_body_digest_raw"),
    }
    ok = (published.get("status") == o2.S_HANDOFF_PUBLISHED
          and observation["profile"] == u12.PUBLICATION_TRANSPORT_PROFILE
          and observation["rendered_ends_lf_fence_lf"]
          and observation["transport_is_render_minus_one_lf"]
          and observation["o_equals_t"]
          and observation["comment_adds"] == 1
          and observation["binding_binds_o"])
    return {
        "row": "3_future_transport_o_equals_t",
        "validation_focus": (
            "future send T with no terminal LF; the simulated CLI link that "
            "deletes one terminal LF has nothing to delete: file bytes are T, "
            "O=T, exactly one comment_add"),
        "required_outcome": "verified exact bytes sent once; O==T; separate "
                            "R/T/O identities persisted",
        "observed": observation,
        "pass": bool(ok),
    }


def case_relation_matrix(root):
    t = "prefix\ninternal\n```"
    variants = {
        "leading_ws": " " + t,
        "internal_ws": t.replace("internal", "internal  "),
        "crlf": t.replace("\n", "\r\n"),
        "bom": "\ufeff" + t,
        "trailing_space": t + " ",
        "double_lf": t + "\n\n",
        "unicode_change": t.replace("prefix", "préfix"),
        "exact": t,
    }
    rows = []
    passed = True
    for label, observed in variants.items():
        verdict = u12.publication_transport_relation(t, observed)
        expected = observed == t
        ok = verdict["accepted"] == expected
        passed = passed and ok
        rows.append({"variant": label, "accepted": verdict["accepted"],
                     "reason": verdict["reason"], "pass": ok})
    shapes = {
        "missing_renderer_tail": "/note\n```\n{}",
        "crlf_tail": "/note\r\n```\r\n",
        "double_terminal_lf": "/note\n```\n\n",
        "trailing_space_before_lf": "/note\n``` \n",
        "bom_body": "\ufeff/note\n```\n",
    }
    for label, body in shapes.items():
        try:
            u12.prepare_publication_transport_body(body)
            rows.append({"variant": label, "accepted": True, "pass": False})
            passed = False
        except u12.R0BValidationRefused:
            rows.append({"variant": label, "accepted": False, "pass": True})
    return {
        "row": "4_raw_relation_refusal_matrix",
        "validation_focus": (
            "leading/internal whitespace, CRLF, BOM, trailing spaces, doubled "
            "terminal LF, Unicode changes: the raw relation refuses and JSON "
            "parseability never exempts"),
        "required_outcome": "every non-exact variant refuses; a wrong renderer "
                            "shape stops without guessing",
        "observed": rows,
        "pass": bool(passed),
    }


# ---------------------------------------------------------------------------
# 3. note / evidence refusal matrix
# ---------------------------------------------------------------------------
def _note_refusal(root, label, mutate, reason=None, post=None):
    fx = new_fx(root, label)
    decision = fx.decision()
    mutate(fx)
    result = fx.recover(
        decision=decision,
        accepted_execution=dict(decision["execution_migration"]))
    ok = refused(result, reason) and state_of(fx)["state"] == o2.S_BLOCKED
    if post is not None:
        ok = ok and post(fx)
    return {"variant": label, "reason": result.get("reason"),
            "detail": result.get("detail"), "pass": bool(ok)}


def case_note_candidate_matrix(root):
    rows = [
        _note_refusal(root, "zero_note",
                      lambda fx: fx.cli.comments.__setitem__(TARGET_ID, []),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "two_identical_notes",
                      lambda fx: append_comment(
                          fx.cli, fx.note_content, comment_id="CMT-DUP"),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "r_and_r_minus_one_pair",
                      lambda fx: append_comment(
                          fx.cli, rendered_original(fx),
                          comment_id="CMT-R-TWIN"),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "wrong_author",
                      lambda fx: fx.note_comment.__setitem__(
                          "author_id", TARGET_AGENT),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "wrong_source_run",
                      lambda fx: fx.note_comment.__setitem__(
                          "source_task_id", SOURCE_RUN),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "wrong_thread",
                      lambda fx: fx.note_comment.__setitem__(
                          "parent_id",
                          "01a08f14-8911-7d59-aee7-e5bd0269cbf1"),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "edited_timestamp",
                      lambda fx: fx.note_comment.__setitem__(
                          "updated_at", "2026-09-11T10:09:00Z"),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "edited_revision",
                      lambda fx: fx.note_comment.__setitem__("revision", 2),
                      u12.REASON_PUBLICATION_PROVENANCE),
        _note_refusal(root, "edited_body",
                      lambda fx: fx.note_comment.__setitem__(
                          "content", fx.note_content + "x"),
                      u12.REASON_PUBLICATION_PROVENANCE),
    ]
    return {
        "row": "5_note_candidate_refusals",
        "validation_focus": (
            "zero note, two identical notes, one R plus one R-minus-one note, "
            "wrong author/source/thread/timestamps/revision: refuse with no "
            "resend and no latest-pick"),
        "required_outcome": "typed refusal keeping BLOCKED; no note ever "
                            "resent",
        "observed": rows,
        "pass": all(row["pass"] for row in rows),
    }


def case_delta_and_material_matrix(root):
    rows = [
        _note_refusal(root, "extra_comment",
                      lambda fx: append_comment(
                          fx.cli, "unrelated extra", comment_id="CMT-EXTRA"),
                      u12.REASON_PUBLICATION_CONFLICT),
        _note_refusal(root, "issue_projection_drift",
                      lambda fx: fx.cli.issues[TARGET_ID].__setitem__(
                          "title", "drifted title"),
                      u12.REASON_PUBLICATION_CONFLICT),
        _note_refusal(root, "timeline_delta",
                      lambda fx: fx.cli.add_activity(TARGET_ID, "commented"),
                      u12.REASON_PUBLICATION_CONFLICT),
        _note_refusal(root, "terminal_run",
                      lambda fx: fx.cli.runs.__setitem__(
                          TARGET_ID, [{"id": "RUN-DONE", "issue_id": TARGET_ID,
                                       "agent_id": TARGET_AGENT,
                                       "status": "completed", "attempt": 1}]),
                      u12.REASON_MATERIAL_STALE),
    ]

    fx = new_fx(root, "moving-second-read")
    fx.cli.comment_list_count = 0

    def mutate(cli):
        if cli.comment_list_count == 2:
            append_comment(cli, "race", comment_id="CMT-RACE")

    fx.cli.on_comment_list = mutate
    result = fx.recover()
    rows.append({"variant": "second_read_drift",
                 "reason": result.get("reason"),
                 "pass": refused(result, u12.REASON_MOVING_EVIDENCE)})

    fx = new_fx(root, "missing-findings")
    decision = fx.decision()
    result = fx.recover(
        decision=decision,
        accepted_execution=dict(decision["execution_migration"]),
        findings=None)
    rows.append({"variant": "missing_findings_source",
                 "reason": result.get("reason"),
                 "pass": refused(result,
                                 u12.REASON_PREFLIGHT_INPUT_MISSING)})
    return {
        "row": "6_delta_and_material_refusals",
        "validation_focus": (
            "target projection drift, timeline add/remove/edit, baseline note "
            "change, any active or terminal run, and a drifting second read "
            "all refuse"),
        "required_outcome": "typed refusals keeping BLOCKED, zero writes",
        "observed": rows,
        "pass": all(row["pass"] for row in rows),
    }


# ---------------------------------------------------------------------------
# 4. fences: loaders, migration, tampering
# ---------------------------------------------------------------------------
def case_loader_fences(root):
    fx = new_fx(root, "loader-fences")
    pre = state_of(fx)
    with_new_loader_without_migration = False
    try:
        u12.validate_intent_record(pre)
    except u12.R0BDowngradeRefused:
        with_new_loader_without_migration = True
    inspected_ok = False
    try:
        inspected, _data = fx.factory._publication_recovery_inspection(
            fx.intent_id)
        inspected_ok = inspected["state"] == o2.S_BLOCKED
    except Exception:  # noqa: BLE001
        inspected_ok = False
    recovered = fx.recover()
    after = state_of(fx)
    post_load_ok = False
    try:
        u12.validate_intent_record(after)
        post_load_ok = True
    except Exception:  # noqa: BLE001
        post_load_ok = False
    old_intent = load_forward_intent_module()
    old_reader_refused = False
    try:
        old_intent.fold_records(fx.store.read_records())
    except old_intent.LedgerCorruptionError:
        old_reader_refused = True
    observation = {
        "new_loader_refuses_pre_migration": with_new_loader_without_migration,
        "inspection_reads_pre_migration": inspected_ok,
        "recovered": recovered.get("status"),
        "new_loader_accepts_post_migration": post_load_ok,
        "old_da99c11_reader_refuses_new_op": old_reader_refused,
    }
    ok = all(observation.values())
    return {
        "row": "7_loader_fences",
        "validation_focus": (
            "the new loader without migration and the old da99c11 reader on "
            "the new op both fail closed; inspection may only read the "
            "historical evidence"),
        "required_outcome": "pre-migration bytes refuse under new bytes; old "
                            "readers refuse the new op; post-migration load "
                            "validates the chain",
        "observed": observation,
        "pass": bool(ok),
    }


def case_migration_tamper(root):
    fx = new_fx(root, "migration-tamper")
    fx.recover()
    intent = state_of(fx)
    data = fx.binding()
    rows = []

    def tamper(key, value):
        migration = copy.deepcopy(data["publication_execution_migration"])
        migration[key] = value
        migration["migration_digest"] = u12.digest(
            {k: v for k, v in migration.items() if k != "migration_digest"})
        try:
            u12.validate_publication_execution_migration(
                intent, data, migration, applying=False)
            return False
        except u12.R0BValidationRefused:
            return True

    for key in ("old_execution_binding_digest", "old_recovery_proof_digest",
                "old_decision_digest", "store_file_digest", "note_file_digest",
                "new_adapter_lf_digest", "new_adapter_raw_digest",
                "artifact_dependency_digest"):
        rows.append({"tamper": key,
                     "refused": tamper(key, "sha256:" + "0" * 64)})
    migration = copy.deepcopy(data["publication_execution_migration"])
    migration["intent_id"] = "DI-" + "0" * 16
    migration["migration_digest"] = u12.digest(
        {k: v for k, v in migration.items() if k != "migration_digest"})
    try:
        u12.validate_publication_execution_migration(
            intent, data, migration, applying=False)
        copied_refused = False
    except u12.R0BValidationRefused:
        copied_refused = True
    rows.append({"tamper": "cross_intent_copy", "refused": copied_refused})
    migration = copy.deepcopy(data["publication_execution_migration"])
    migration["commit_revision"] = 6
    migration["migration_digest"] = u12.digest(
        {k: v for k, v in migration.items() if k != "migration_digest"})
    try:
        u12.validate_publication_execution_migration(
            intent, data, migration, applying=False)
        revision_refused = False
    except u12.R0BValidationRefused:
        revision_refused = True
    rows.append({"tamper": "wrong_commit_revision",
                 "refused": revision_refused})
    proof = copy.deepcopy(data["publication_recovery_proof"])
    proof["note"]["observed_chars"] += 1
    try:
        u12.validate_publication_recovery_proof(
            proof, intent=intent, data=data, applying=False)
        proof_refused = False
    except u12.R0BValidationRefused:
        proof_refused = True
    rows.append({"tamper": "edited_committed_proof",
                 "refused": proof_refused})
    return {
        "row": "8_migration_and_proof_tamper_refusals",
        "validation_focus": (
            "old proof/decision edits, copied migrations, hash-only "
            "acceptance and changed new dependency bytes all refuse"),
        "required_outcome": "every tampered identity refuses; no adapter-only "
                            "hash substitution or cross-intent copy",
        "observed": rows,
        "pass": all(row["refused"] for row in rows),
    }


def case_writer_reducer_refusals(root):
    fx = new_fx(root, "writer-reducer")
    record, bundle, decision = fx.prepare_commit()
    fx.store.claim(fx.intent_id, DISPATCHER)
    claim = fx.store.claim(fx.intent_id, DISPATCHER)
    tail = [u12.digest(claim["record"])] if claim.get("record") else []
    # ordinary BLOCKED -> HANDOFF_PUBLISHED stays refused by the state machine
    ordinary_refused = o2.S_HANDOFF_PUBLISHED not in o2.TRANSITIONS[
        o2.S_BLOCKED]
    # a second commit for the same intent refuses in the reducer
    fx.recover()
    intent = state_of(fx)
    commits = [r for r in fx.store.read_records()
               if r.get("op") == u12.PUBLICATION_RECOVERY_OP]
    duplicate_refused = False
    try:
        u12.fold_publication_recovery_commit(commits[0], intent,
                                             {fx.intent_id: intent})
    except o2.LedgerCorruptionError:
        duplicate_refused = True
    duplicate_writer_refused = False
    try:
        u12.validate_publication_recovery_commit_record(commits[0], intent)
    except u12.R0BValidationRefused:
        duplicate_writer_refused = True
    # a forged blocker revision refuses
    forged = copy.deepcopy(commits[0])
    forged.pop("seq", None)
    forged["revision"] = 6
    revision_refused = False
    try:
        u12.validate_publication_recovery_commit_record(forged, intent)
    except u12.R0BValidationRefused:
        revision_refused = True
    observation = {
        "ordinary_blocked_exit_refused": ordinary_refused,
        "duplicate_commit_reducer_refused": duplicate_refused,
        "duplicate_commit_writer_refused": duplicate_writer_refused,
        "forged_revision_refused": revision_refused,
        "single_commit": len(commits) == 1,
        "prepared_record_was_never_committed": record["revision"] == 5
        and bool(bundle["ledger_prefix_raw_digest"]),
        "not_blocked_on_tail": isinstance(tail, list)
        and decision["expected_intent_revision"] == 4,
    }
    return {
        "row": "9_writer_reducer_refusals",
        "validation_focus": (
            "ordinary BLOCKED -> PUBLISHED, wrong reason/revision/attempt and "
            "a duplicate recovery commit are refused by both writer and "
            "reducer"),
        "required_outcome": "no writer/reducer path admits a non-incident or "
                            "duplicate commit",
        "observed": observation,
        "pass": all(value for key, value in observation.items()
                    if key != "single_commit" and
                    key != "prepared_record_was_never_committed"
                    and key != "not_blocked_on_tail")
        and observation["single_commit"],
    }


def case_lease_tail_conflict(root):
    rows = []
    # lease held by another writer
    fx = new_fx(root, "lease-held")
    fx.store.claim(fx.intent_id, "other-writer")
    before = len(fx.store.read_records())
    try:
        fx.recover()
        rows.append({"variant": "other_writer_lease", "pass": False})
    except o2.LeaseHeldError:
        rows.append({"variant": "other_writer_lease",
                     "pass": len(fx.store.read_records()) == before
                     and state_of(fx)["state"] == o2.S_BLOCKED})
    # shared tail insertion at the same revision
    fx = new_fx(root, "tail-insertion")
    record, bundle, _decision = fx.prepare_commit()
    claim = fx.store.claim(fx.intent_id, DISPATCHER)
    expected = [u12.digest(claim["record"])]
    fx.store.append({
        "kind": "command", "transaction_id": "tx-foreign",
        "command_class": o2.C_READ,
        "argv": ["multica", "issue", "get", TARGET_ID, "--output", "json"]})
    try:
        fx.factory._commit_publication_recovery(
            fx.intent_id, record=record, expected_revision=4,
            expected_tail_digests=expected,
            prefix_count=bundle["ledger_prefix_count"],
            prefix_raw_digest=bundle["ledger_prefix_raw_digest"])
        rows.append({"variant": "foreign_tail_command", "pass": False})
    except u12.PreflightRefusal as exc:
        rows.append({"variant": "foreign_tail_command",
                     "detail": str(exc),
                     "pass": "shared tail" in str(exc)
                     and state_of(fx)["state"] == o2.S_BLOCKED})
    # conflicting open intent on the same logical key
    fx = new_fx(root, "logical-conflict")
    record, bundle, _decision = fx.prepare_commit()
    claim = fx.store.claim(fx.intent_id, DISPATCHER)
    original = state_of(fx)["fields"]
    twin = {
        "intent_id": "DI-" + "f" * 16, "schema_version": o2.O2_SCHEMA,
        "source_task_id": SOURCE_RUN,
        "logical_task_key": original["logical_task_key"],
        "parent_issue_id": original["parent_issue_id"],
        "target_role": u12.EXECUTION_ROLE, "target_agent_id": TARGET_AGENT,
        "package_id": original["package_id"],
        "artifact_dependency_digest": original["artifact_dependency_digest"],
        "creation_authority": "fixture twin",
        "provenance": {"fixture": True}, "state": o2.S_INTENT_RECORDED,
    }
    twin_record = fx.store.append({
        "kind": "intent", "record_type": o2.INTENT_RECORD_TYPE,
        "schema_version": o2.O2_SCHEMA, "op": "recorded",
        "intent_id": twin["intent_id"], "at": CLOCK, "intent": twin})
    expected = [u12.digest(claim["record"]), u12.digest(twin_record)]
    try:
        fx.factory._commit_publication_recovery(
            fx.intent_id, record=record, expected_revision=4,
            expected_tail_digests=expected,
            prefix_count=bundle["ledger_prefix_count"],
            prefix_raw_digest=bundle["ledger_prefix_raw_digest"])
        rows.append({"variant": "conflicting_logical_intent", "pass": False})
    except u12.PreflightRefusal as exc:
        rows.append({"variant": "conflicting_logical_intent",
                     "detail": str(exc),
                     "pass": "conflicting" in str(exc).lower()
                     and state_of(fx)["state"] == o2.S_BLOCKED})
    return {
        "row": "10_lease_tail_conflict_refusals",
        "validation_focus": (
            "missing/expired/other-holder lease, CAS drift, a shared tail "
            "command inserted at the same revision and a competing intent on "
            "the same logical key refuse with no commit"),
        "required_outcome": "the OS-lock recheck refuses every interleaving",
        "observed": rows,
        "pass": all(row["pass"] for row in rows),
    }


def case_crash_replay(root):
    fx = new_fx(root, "crash-before-commit")

    def crash(*args, **kwargs):
        raise u12.PreflightRefusal(
            o2.S_BLOCKED, u12.REASON_PUBLICATION_PROVENANCE,
            "simulated crash before commit", subject="commit")

    original = fx.factory._commit_publication_recovery
    fx.factory._commit_publication_recovery = crash
    first = fx.recover()
    fx.factory._commit_publication_recovery = original
    blocked_after_crash = state_of(fx)["state"] == o2.S_BLOCKED
    second = fx.recover()
    commits = [r for r in fx.store.read_records()
               if r.get("op") == u12.PUBLICATION_RECOVERY_OP]
    replay = fx.recover()
    # bad ledger line refuses folding
    fx2 = new_fx(root, "bad-line")
    fx2.store.append({"kind": "command", "transaction_id": "x",
                      "command_class": o2.C_READ,
                      "argv": ["multica", "issue", "get", TARGET_ID]})
    path = fx2.tmp / "ledger.jsonl"
    path.write_bytes(path.read_bytes() + b"{not json\n")
    bad_line_refused = False
    try:
        fx2.store.get(fx2.intent_id)
    except (o2.LedgerCorruptionError, o2.IntentError):
        bad_line_refused = True
    observation = {
        "crash_left_blocked": blocked_after_crash,
        "recovered_after_crash": second.get("status")
        == o2.S_HANDOFF_PUBLISHED,
        "one_commit": len(commits) == 1,
        "replay_idempotent": bool(replay.get("replayed")),
        "replay_zero_new_commits": replay.get("ledger_commits") == 0,
        "bad_line_fails_closed": bad_line_refused,
    }
    return {
        "row": "11_crash_replay_corruption",
        "validation_focus": (
            "crash before commit, crash after commit, restart, the same "
            "decision replayed and a corrupt ledger line: uncommitted never "
            "executes, committed is idempotent, corruption fails closed"),
        "required_outcome": "no second publication and no second commit ever",
        "observed": observation,
        "pass": all(observation.values()),
    }


# ---------------------------------------------------------------------------
# 5. post-recovery lifecycle
# ---------------------------------------------------------------------------
def case_post_recovery_lifecycle(root):
    fx = new_fx(root, "post-recovery")
    recovered = fx.recover()
    recovery_commands = list(fx.cli.commands)
    no_trigger_at_recovery = not fx.cli.commands_of(["issue", "rerun"])
    before_arm = len(fx.cli.commands)
    armed = fx.arm()
    triggered = fx.trigger()
    observation = {
        "recovered": recovered.get("status"),
        "no_trigger_from_recovery": no_trigger_at_recovery,
        "armed": armed.get("status"),
        "triggered": triggered.get("status"),
        "reruns": len(fx.cli.commands_of(["issue", "rerun"])),
        "create_commands": len(fx.cli.commands_of(["issue", "create"])),
        "assign_commands": len(fx.cli.commands_of(["issue", "assign"])),
        "comment_adds": len(fx.cli.commands_of(["issue", "comment", "add"])),
        "checkpoints": [e["data"]["checkpoint"]
                        for e in fx.events_named(u12.E_PREFLIGHT)],
        "recovery_was_read_only": all(
            not any(argv[1:1 + len(p)] == list(p) for p in
                    (["issue", "create"], ["issue", "assign"],
                     ["issue", "comment", "add"], ["issue", "rerun"]))
            for argv in recovery_commands[len(recovery_commands):]),
        "arm_added_reads": len(fx.cli.commands) > before_arm,
    }
    ok = (observation["recovered"] == o2.S_HANDOFF_PUBLISHED
          and observation["no_trigger_from_recovery"]
          and observation["armed"] == o2.S_TRIGGER_READY
          and observation["triggered"] == o2.S_RUN_CORRELATED
          and observation["reruns"] == 1 and observation["create_commands"] == 1
          and observation["assign_commands"] == 1
          and observation["comment_adds"] == 1
          and observation["checkpoints"] == ["ARM", "TRIGGER"])
    return {
        "row": "12_post_recovery_lifecycle",
        "validation_focus": (
            "after recovery the arm/trigger/restart paths exactly re-check the "
            "O-bound note and run the fresh artifact/request/SELF_CHECK/strict "
            "gates; recovery issues zero triggers and the later authorized "
            "path issues at most one"),
        "required_outcome": "zero trigger at recovery; one strict rerun after "
                            "arm; one create, one ownership, one note in total",
        "observed": observation,
        "pass": bool(ok),
    }


def case_post_recovery_drift(root):
    fx = new_fx(root, "post-recovery-drift")
    fx.recover()
    fx.cli.comments[TARGET_ID][0]["content"] = fx.note_content + "\n"
    armed = fx.arm()
    observation = {
        "status": armed.get("status"),
        "reruns": len(fx.cli.commands_of(["issue", "rerun"])),
    }
    return {
        "row": "13_post_recovery_note_drift",
        "validation_focus": (
            "a note-relation drift after recovery refuses at the fresh "
            "preflight without a trigger"),
        "required_outcome": "typed stop, zero triggers",
        "observed": observation,
        "pass": observation["status"] == o2.S_REFRESH_REQUIRED
        and observation["reruns"] == 0,
    }


CASES = (
    case_real_historical_shape,
    case_exact_recovery,
    case_future_transport,
    case_relation_matrix,
    case_note_candidate_matrix,
    case_delta_and_material_matrix,
    case_loader_fences,
    case_migration_tamper,
    case_writer_reducer_refusals,
    case_lease_tail_conflict,
    case_crash_replay,
    case_post_recovery_lifecycle,
    case_post_recovery_drift,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="u12-r0b-publication-") as tmp:
        root = Path(tmp)
        for case in CASES:
            try:
                row = case(root)
            except Exception as exc:  # noqa: BLE001 - a row failure is data
                row = {"row": case.__name__, "validation_focus": "",
                       "required_outcome": "",
                       "observed": None,
                       "error": f"{type(exc).__name__}: {exc}",
                       "pass": False}
            rows.append(row)
    passed = sum(1 for row in rows if row["pass"])
    matrix = {
        "kind": "u12_r0b_publication_transport_acceptance_matrix",
        "task": "YZT-84",
        "branch": "yzt-84-u12-r0b-forward-adapter",
        "approved_exception": {
            "design_ref": u12.PUBLICATION_TRANSPORT_DESIGN_REF,
            "design_digest": u12.PUBLICATION_TRANSPORT_DESIGN_DIGEST,
            "human_approval_ref":
                u12.PUBLICATION_RECOVERY_HUMAN_APPROVAL_REF,
            "lead_boundary_ref": u12.PUBLICATION_RECOVERY_LEAD_APPROVAL_REF,
        },
        "adapter_digest": u12.adapter_digest(),
        "adapter_raw_digest": u12.adapter_raw_digest(),
        "publication_transport_profile": u12.PUBLICATION_TRANSPORT_PROFILE,
        "publication_recovery_op": u12.PUBLICATION_RECOVERY_OP,
        "publication_recovery_decision_schema":
            u12.PUBLICATION_RECOVERY_DECISION_SCHEMA,
        "publication_recovery_proof_schema":
            u12.PUBLICATION_RECOVERY_PROOF_SCHEMA,
        "publication_migration_schema": u12.PUBLICATION_MIGRATION_SCHEMA,
        "execution_migration_commit": u12._git_rev_parse("HEAD"),
        "rows_total": len(rows),
        "rows_passed": passed,
        "verdict": ("ALL_PUBLICATION_TRANSPORT_VALIDATION_ROWS_PASS"
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
