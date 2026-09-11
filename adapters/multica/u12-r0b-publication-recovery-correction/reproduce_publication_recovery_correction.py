#!/usr/bin/env python3
"""Isolated acceptance reproduction for the YZT-84 source-activation and
semantic publication-recovery proof correction.

Runs every corrected acceptance row of the approved
`U12_R0_PUBLICATION_TRANSPORT_DECISION.md` (raw SHA256 0df4cec9...) plus the
two independently reproduced Lead counterexamples through the public adapter
operations on temporary JSONL ledgers. The historical record is produced by
the actual b49630b and da99c11 module bytes loaded from the accepted commits;
the corrected decision/proof/migration are v1.1 and the previous ac1e5b4
reader must refuse them.

No live Multica call, no production-ledger read/write, no Canonical write.
No note is ever resent.

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-publication-recovery-correction/reproduce_publication_recovery_correction.py \
        --output adapters/multica/u12-r0b-publication-recovery-correction/publication-recovery-correction-acceptance-matrix.json
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
from test_u12_r0_binding import (  # noqa: E402
    CLOCK, DISPATCHER, PARENT_ID, PUBLISHER_AGENT, PUBLISHER_RUN, TARGET_AGENT,
    TARGET_ID, TARGET_IDENTIFIER)
from test_u12_r0_publication_recovery import (  # noqa: E402
    LEAD_BOUNDARY_TEXT, HUMAN_APPROVAL_TEXT, PublicationRecoveryFixture)


def new_fx(root: Path, label: str) -> PublicationRecoveryFixture:
    path = root / label
    path.mkdir(parents=True, exist_ok=True)
    return PublicationRecoveryFixture(path)


def refused(result, reason=None) -> bool:
    return (result.get("status") == o2.S_BLOCKED
            and result.get("outcome") == "RECOVERY_REFUSED"
            and result.get("platform_writes") == 0
            and result.get("ledger_commits") == 0
            and (reason is None or result.get("reason") == reason))


def stuck(fx) -> bool:
    intent = fx.store.get(fx.intent_id)
    commits = [r for r in fx.store.read_records()
               if r.get("op") == u12.PUBLICATION_RECOVERY_OP]
    return (intent["state"] == o2.S_BLOCKED and intent["revision"] == 4
            and not commits)


def row(name, focus, required, observed, ok):
    return {"row": name, "validation_focus": focus, "required_outcome": required,
            "observed": observed, "pass": bool(ok)}


def decision_for(fx, **overrides):
    decision = fx.decision(**overrides)
    decision["decision_digest"] = u12.digest(
        {k: v for k, v in decision.items() if k != "decision_digest"})
    return decision


def tamper_proof(fx, mutate):
    record, _bundle, decision = fx.prepare_commit()
    proof = record["publication_recovery_proof"]
    mutate(proof)
    proof["proof_digest"] = u12.digest(
        {k: v for k, v in proof.items() if k != "proof_digest"})
    return record, decision


def load_previous_adapter():
    import hashlib
    import importlib.util
    import subprocess
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "show",
         f"{u12.PREVIOUS_PUBLICATION_ADAPTER_COMMIT}:tools/u12_r0_binding.py"],
        capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise AssertionError("the ac1e5b4 adapter blob is unavailable")
    digest = "sha256:" + hashlib.sha256(
        proc.stdout.replace(b"\r\n", b"\n")).hexdigest()
    if digest != u12.PREVIOUS_PUBLICATION_ADAPTER_DIGEST:
        raise AssertionError(
            f"ac1e5b4 blob digest {digest} does not match the preserved pin")
    holder = Path(tempfile.mkdtemp(prefix="u12-r0b-previous-"))
    path = holder / "u12_r0_binding_previous.py"
    path.write_bytes(proc.stdout)
    spec = importlib.util.spec_from_file_location(
        "u12_r0_binding_previous", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# rows
# ---------------------------------------------------------------------------
def case_valid_chain(root):
    fx = new_fx(root, "valid-chain")
    result = fx.recover()
    replay = fx.recover()
    binding = fx.binding()
    observed = {
        "result": result,
        "replay": replay,
        "revision": fx.store.get(fx.intent_id)["revision"],
        "state": fx.store.get(fx.intent_id)["state"],
        "comment_adds": len(fx.cli.commands_of(["issue", "comment", "add"])),
        "reruns": len(fx.cli.commands_of(["issue", "rerun"])),
        "creates": len(fx.cli.commands_of(["issue", "create"])),
        "assigns": len(fx.cli.commands_of(["issue", "assign"])),
        "migration_present": isinstance(
            binding.get("publication_execution_migration"), dict),
        "proof_schema": binding["publication_recovery_proof"]["schema"],
    }
    ok = (result["status"] == o2.S_HANDOFF_PUBLISHED
          and result["ledger_commits"] == 1 and result["platform_writes"] == 0
          and replay["replayed"] is True and replay["ledger_commits"] == 0
          and fx.store.get(fx.intent_id)["revision"] == 5
          and observed["comment_adds"] == 1 and observed["reruns"] == 0
          and observed["creates"] == 1 and observed["assigns"] == 1
          and observed["migration_present"]
          and observed["proof_schema"] == u12.PUBLICATION_RECOVERY_PROOF_SCHEMA)
    return row(
        "valid_exact_historical_chain_recovers_once",
        "the one approved recovery still commits once to HANDOFF_PUBLISHED "
        "rev5 with the v1.1 proof/migration, replays idempotently and issues "
        "no second publication/trigger",
        "HANDOFF_PUBLISHED rev5; ledger_commits 1 then 0; zero native writes",
        observed, ok)


def case_source_counterexample_old_shape(root):
    fx = new_fx(root, "source-ce-old-shape")
    decision = fx.decision()
    decision["source_activation"] = {
        "comment_id": "00000000-0000-7000-8000-000000000001",
        "resolution_digest": "sha256:" + "0" * 64,
        "attachment_id": "00000000-0000-7000-8000-000000000002",
    }
    decision["decision_digest"] = u12.digest(
        {k: v for k, v in decision.items() if k != "decision_digest"})
    try:
        result = fx.recover(decision=decision)
        outcome = f"returned {result.get('outcome')}"
    except u12.R0BValidationRefused as exc:
        outcome = "R0BValidationRefused: " + exc.message
    observed = {"outcome": outcome, "state_still_blocked": stuck(fx),
                "comment_adds": len(
                    fx.cli.commands_of(["issue", "comment", "add"]))}
    return row(
        "lead_counterexample_source_activation_old_shape",
        "the independently reproduced counterexample payload (nonexistent "
        "comment/attachment and zero resolution digest, decision digest "
        "recomputed) must fail closed",
        "R0BValidationRefused and BLOCKED rev4 with no commit and no note",
        observed, "R0BValidationRefused" in outcome
        and observed["state_still_blocked"] and observed["comment_adds"] == 1)


def case_source_counterexample_new_shape(root):
    fx = new_fx(root, "source-ce-new-shape")
    decision = fx.decision()
    decision["source_activation"].update({
        "comment_id": "00000000-0000-7000-8000-000000000001",
        "resolution_attachment_id": "00000000-0000-7000-8000-000000000002",
        "resolution_raw_digest": "sha256:" + "0" * 64,
    })
    decision["decision_digest"] = u12.digest(
        {k: v for k, v in decision.items() if k != "decision_digest"})
    result = fx.recover(decision=decision)
    parent_reads = [r for r in fx.cli.commands
                    if r[1:5] == ["issue", "comment", "list", PARENT_ID]]
    observed = {"result": result, "parent_reads": len(parent_reads),
                "downloads": len(
                    fx.cli.commands_of(["attachment", "download"])),
                "state_still_blocked": stuck(fx)}
    return row(
        "lead_counterexample_source_activation_nonexistent_ids",
        "the same attack re-expressed on the v1.1 schema: the resolver must "
        "actually read the parent activation record, find it missing and "
        "refuse instead of shape-checking",
        "RECOVERY_REFUSED PUBLICATION_PROVENANCE_INCOMPLETE; parent read "
        "performed; zero downloads and zero commit",
        observed,
        refused(result, u12.REASON_PUBLICATION_PROVENANCE)
        and observed["parent_reads"] == 1 and observed["downloads"] == 0
        and observed["state_still_blocked"])


def case_source_activation_content(root):
    results = {}
    downloads = {}

    def run(label, mutate=None, decision_factory=None):
        fx = new_fx(root, "source-" + label)
        if mutate is not None:
            mutate(fx)
        kwargs = {}
        if decision_factory is not None:
            decision = decision_factory(fx)
            kwargs = {
                "decision": decision,
                "accepted_execution": dict(decision["execution_migration"]),
            }
        result = fx.recover(**kwargs)
        results[label] = result
        downloads[label] = len(
            fx.cli.commands_of(["attachment", "download"]))
        results[label + "__stuck"] = stuck(fx)

    def tamper_attachment(fx):
        stored = fx.cli.attachment_files[fx.resolution_attachment_id]
        stored["data"] = stored["data"] + b"tampered\n"

    def duplicate_record(fx):
        fx.cli.comments[PARENT_ID].insert(
            1, copy.deepcopy(fx.cli.comments[PARENT_ID][0]))

    def edited_record(fx):
        fx.cli.comments[PARENT_ID][0]["content"] += "edited"

    def wrong_author(fx):
        fx.cli.comments[PARENT_ID][0]["author_id"] = TARGET_AGENT

    def forged_digest_decision(fx):
        decision = decision_for(fx)
        decision["source_activation"]["resolution_raw_digest"] = \
            "sha256:" + "f" * 64
        decision["decision_digest"] = u12.digest(
            {k: v for k, v in decision.items() if k != "decision_digest"})
        return decision

    for label, mutate in (("tampered_attachment_bytes", tamper_attachment),
                          ("duplicate_activation", duplicate_record),
                          ("edited_activation", edited_record),
                          ("wrong_author", wrong_author)):
        run(label, mutate)
    run("forged_digest", decision_factory=forged_digest_decision)
    observed = {}
    for label in ("tampered_attachment_bytes", "duplicate_activation",
                  "edited_activation", "wrong_author", "forged_digest"):
        observed[label] = {
            "outcome": results[label].get("outcome"),
            "reason": results[label].get("reason"),
            "stuck": results[label + "__stuck"],
            "downloads": downloads[label],
        }
    ok = all(item["outcome"] == "RECOVERY_REFUSED" and item["stuck"]
             for item in observed.values())
    ok = ok and observed["tampered_attachment_bytes"]["downloads"] == 2 \
        and observed["duplicate_activation"]["downloads"] == 0 \
        and observed["edited_activation"]["downloads"] == 0 \
        and observed["wrong_author"]["downloads"] == 0 \
        and observed["forged_digest"]["downloads"] == 0
    return row(
        "resolved_source_activation_content_enforced",
        "recomputed raw attachment hashes, unique Lead-authored activation "
        "record and exact resolution/request fingerprints; missing, "
        "duplicate, edited or wrong-author records and false hashes refuse",
        "every variant RECOVERY_REFUSED and still BLOCKED rev4; the "
        "attachment bytes are actually downloaded and re-hashed",
        observed, ok)


def case_authority_content(root):
    fx = new_fx(root, "authority-content")
    rows = fx.cli.comments[PARENT_ID]
    human = next(r for r in rows if r["id"] ==
                 u12.PUBLICATION_RECOVERY_HUMAN_APPROVAL_COMMENT_ID)
    human["content"] += "同意"
    result = fx.recover()
    observed = {"human_mismatch": result, "stuck": stuck(fx)}
    fx2 = new_fx(root, "authority-author")
    lead = next(r for r in fx2.cli.comments[PARENT_ID]
                if r["id"] == u12.PUBLICATION_RECOVERY_LEAD_APPROVAL_COMMENT_ID)
    lead["author_id"] = TARGET_AGENT
    result2 = fx2.recover()
    observed["lead_author_mismatch"] = result2
    observed["stuck2"] = stuck(fx2)
    ok = (refused(result) and observed["stuck"]
          and refused(result2) and observed["stuck2"])
    return row(
        "actual_approval_authority_content_enforced",
        "the actual Human approval and Lead boundary comment author and "
        "content digests are read and pinned; a fixed reference string or a "
        "caller digest alone is never authority",
        "both variants RECOVERY_REFUSED and still BLOCKED rev4",
        observed, ok)


def case_request_reconstruction(root):
    fx = new_fx(root, "substituted-request")
    request = json.loads(fx.request_text)
    request["task_snapshot"]["title"] = "substituted request"
    new_text = json.dumps(request, ensure_ascii=False, sort_keys=True,
                          indent=2) + "\n"
    fx.cli.add_attachment(fx.request_attachment_id, "request.json", new_text)
    decision = decision_for(fx)
    decision["source_activation"]["request_raw_digest"] = \
        u12._sha256_utf8(new_text)
    decision["decision_digest"] = u12.digest(
        {k: v for k, v in decision.items() if k != "decision_digest"})
    result = fx.recover(decision=decision)
    fx2 = new_fx(root, "stale-target")
    fx2.cli.issues[TARGET_ID]["title"] = "stale title"
    result2 = fx2.recover()
    observed = {"substituted": result, "stale": result2,
                "stuck": stuck(fx), "stuck2": stuck(fx2)}
    return row(
        "fresh_request_reconstruction_enforced",
        "reconstruct from the freshly read target body plus the accepted "
        "explicit decisions, bind the unique actual E envelope/fingerprint "
        "and refuse a substituted or stale request",
        "both variants RECOVERY_REFUSED and still BLOCKED rev4",
        observed, refused(result) and refused(result2)
        and observed["stuck"] and observed["stuck2"])


def case_proof_counterexample_paths(root):
    def mutate_runs(proof):
        proof["observations"]["runs"] = [
            {"id": "unexpected-terminal-run", "status": "completed"}]
        proof["observations"]["digests"]["runs"] = u12.digest(
            proof["observations"]["runs"])

    fx = new_fx(root, "proof-ce-paths")
    record, decision = tamper_proof(fx, mutate_runs)
    intent = fx.store.get(fx.intent_id)
    data = fx.binding()
    helper = writer = reducer = replay = None
    try:
        u12.validate_publication_recovery_proof(
            record["publication_recovery_proof"], intent=intent, data=data,
            applying=True)
        helper = "ACCEPTED"
    except u12.R0BValidationRefused as exc:
        helper = "R0BValidationRefused: " + exc.message
    try:
        u12.validate_publication_recovery_commit_record(record, intent)
        writer = "ACCEPTED"
    except u12.R0BValidationRefused as exc:
        writer = "R0BValidationRefused: " + exc.message
    try:
        u12.fold_publication_recovery_commit(record, intent,
                                             {fx.intent_id: intent})
        reducer = "ACCEPTED"
    except o2.LedgerCorruptionError as exc:
        reducer = "LedgerCorruptionError: " + exc.message
    committed = fx.recover()
    replay_intent = fx.store.get(fx.intent_id)
    replay_data = copy.deepcopy(fx.binding())
    mutate_runs(replay_data["publication_recovery_proof"])
    replay_data["publication_recovery_proof"]["proof_digest"] = u12.digest(
        {k: v for k, v in replay_data["publication_recovery_proof"].items()
         if k != "proof_digest"})
    try:
        fx.factory._replay_publication_recovery(
            replay_intent, replay_data, decision, DISPATCHER)
        replay = "ACCEPTED"
    except u12.R0BValidationRefused as exc:
        replay = "R0BValidationRefused: " + exc.message
    observed = {"helper": helper, "writer": writer, "reducer": reducer,
                "replay": replay, "valid_commit": committed["status"]}
    ok = (helper.startswith("R0BValidationRefused")
          and writer.startswith("R0BValidationRefused")
          and reducer.startswith("LedgerCorruptionError")
          and replay.startswith("R0BValidationRefused")
          and committed["status"] == o2.S_HANDOFF_PUBLISHED)
    return row(
        "lead_counterexample_rehashed_runs_all_paths",
        "the independently reproduced proof counterexample (nonempty runs "
        "with every local digest recomputed) must be refused by the helper, "
        "the actual writer, the reducer and the committed replay - while the "
        "untampered record still commits once",
        "helper/writer/replay R0BValidationRefused; reducer "
        "LedgerCorruptionError; valid record HANDOFF_PUBLISHED",
        observed, ok)


def case_semantic_rehash_matrix(root):
    observed = {}

    def projection_mismatch(proof):
        fabricated = u12.comment_record({
            "id": "CMT-FABRICATED", "revision": 1, "created_at": CLOCK,
            "updated_at": CLOCK, "parent_id": None,
            "author_id": PUBLISHER_AGENT, "author_type": "agent",
            "source_task_id": PUBLISHER_RUN, "resolved_at": None,
            "content": "fabricated comment"})
        proof["observations"]["comments"].append(fabricated)
        proof["observations"]["digests"]["comments"] = u12.digest(
            proof["observations"]["comments"])

    def relabel_ownership(proof):
        target = next(item for item in proof["shared_history"]["records"]
                      if item["classification"] == u12.CLS_OWNERSHIP_COMMAND)
        target["classification"] = u12.CLS_READ_COMMAND
        target["reason"] = "recognized read command"
        proof["shared_history"]["classification_digest"] = \
            u12.publication_history_digest(proof["shared_history"])

    def relabel_create(proof):
        target = next(
            item for item in proof["shared_history"]["records"]
            if item["classification"] == u12.CLS_ORIGINAL_CREATE_COMMAND)
        target["classification"] = u12.CLS_READ_COMMAND
        target["reason"] = "recognized read command"
        proof["shared_history"]["classification_digest"] = \
            u12.publication_history_digest(proof["shared_history"])

    def record_outcome(label, mutate):
        fx = new_fx(root, "rehash-" + label)
        record, decision = tamper_proof(fx, mutate)
        intent = fx.store.get(fx.intent_id)
        try:
            u12.validate_publication_recovery_proof(
                record["publication_recovery_proof"], intent=intent,
                data=fx.binding(), applying=True)
            outcome = "ACCEPTED"
        except u12.R0BValidationRefused as exc:
            outcome = "R0BValidationRefused: " + exc.message
        try:
            u12.fold_publication_recovery_commit(
                record, intent, {fx.intent_id: intent})
            reducer = "ACCEPTED"
        except o2.LedgerCorruptionError as exc:
            reducer = "LedgerCorruptionError: " + exc.message
        observed[label] = {"helper": outcome, "reducer": reducer,
                           "stuck": stuck(fx)}

    note_id = None

    def remove_note(proof):
        nonlocal note_id
        for index in (4, 8):
            entry = proof["observations"]["raw_responses"][index]
            rows = [item for item in json.loads(entry["stdout"])
                    if note_id is None or item.get("id") != note_id]
            entry["stdout"] = json.dumps(rows, ensure_ascii=False)
            entry["stdout_digest"] = u12._sha256_utf8(entry["stdout"])
        proof["observations"]["comments"] = [
            u12.comment_record(item) for item in json.loads(
                proof["observations"]["raw_responses"][4]["stdout"])]
        proof["observations"]["digests"]["comments"] = u12.digest(
            proof["observations"]["comments"])
        proof["observations"]["digests"]["raw_responses"] = u12.digest(
            proof["observations"]["raw_responses"])

    fx_note = new_fx(root, "rehash-note-probe")
    note_id = fx_note.note_comment["id"]
    record_outcome("projection_mismatch", projection_mismatch)
    record_outcome("relabeled_ownership", relabel_ownership)
    record_outcome("relabeled_create", relabel_create)
    record_outcome("deep_note_removed", remove_note)

    ok = all(value["helper"].startswith("R0BValidationRefused")
             and value["reducer"].startswith("LedgerCorruptionError")
             and value["stuck"] for value in observed.values())
    return row(
        "semantic_rehash_tampering_refused",
        "raw-response/projection mismatch, a removed note rehashed in both "
        "the raw responses and the projection, and shared commands relabelled "
        "as reads with a recomputed classification digest are all refused by "
        "semantics, not by digests",
        "every variant refused in helper and reducer; still BLOCKED rev4",
        observed, ok)


def case_proof_content_fences(root):
    fx = new_fx(root, "proof-content-fences")
    record, _decision = tamper_proof(fx, lambda proof: None)
    intent = fx.store.get(fx.intent_id)
    data = fx.binding()
    observed = {}
    for name, mutate in (
            ("absence", lambda item: item.pop("source_activation")),
            ("authority_edit", lambda item: item["authority_approvals"]
             ["human"].__setitem__("content_raw_digest", "sha256:" + "0" * 64)),
            ("cross_intent", lambda item: item.__setitem__(
                "intent_id", "DI-" + "0" * 16)),
            ("old_schema", lambda item: item.__setitem__(
                "schema", u12.PUBLICATION_RECOVERY_PROOF_SCHEMA_V1_0))):
        candidate = copy.deepcopy(record["publication_recovery_proof"])
        mutate(candidate)
        candidate["proof_digest"] = u12.digest(
            {k: v for k, v in candidate.items() if k != "proof_digest"})
        try:
            u12.validate_publication_recovery_proof(
                candidate, intent=intent, data=data, applying=True)
            observed[name] = "ACCEPTED"
        except u12.R0BValidationRefused as exc:
            observed[name] = "R0BValidationRefused: " + exc.message
    ok = all(value.startswith("R0BValidationRefused")
             for value in observed.values())
    return row(
        "corrected_proof_content_fences",
        "absence, edits, cross-intent copying and the frozen v1.0 schema are "
        "never accepted as recovery authority",
        "every variant R0BValidationRefused",
        observed, ok)


def case_old_reader_refusal(root):
    fx = new_fx(root, "old-reader")
    record, decision = tamper_proof(fx, lambda proof: None)
    intent = fx.store.get(fx.intent_id)
    old = load_previous_adapter()
    observed = {}
    try:
        for name, call in (
                ("decision", lambda: old.validate_publication_recovery_decision(
                    copy.deepcopy(decision))),
                ("proof", lambda: old.validate_publication_recovery_proof(
                    copy.deepcopy(record["publication_recovery_proof"]),
                    intent=intent, data=fx.binding(), applying=True)),
                ("reducer", lambda: old.fold_publication_recovery_commit(
                    copy.deepcopy(record), intent, {fx.intent_id: intent}))):
            try:
                call()
                observed[name] = "ACCEPTED"
            except Exception as exc:  # noqa: BLE001 - refusal is the row
                observed[name] = type(exc).__name__ + ": " + str(exc)[:160]
    finally:
        o2.register_extension_op(u12.PUBLICATION_RECOVERY_OP,
                                 u12.fold_publication_recovery_commit)
    legacy = copy.deepcopy(decision)
    legacy["schema"] = u12.PUBLICATION_RECOVERY_DECISION_SCHEMA_V1_0
    legacy["decision_digest"] = u12.digest(
        {k: v for k, v in legacy.items() if k != "decision_digest"})
    try:
        u12.validate_publication_recovery_decision(legacy)
        observed["new_loader_v1_0_decision"] = "ACCEPTED"
    except u12.R0BValidationRefused as exc:
        observed["new_loader_v1_0_decision"] = \
            "R0BValidationRefused: " + exc.message
    legacy_proof = copy.deepcopy(record["publication_recovery_proof"])
    legacy_proof["schema"] = u12.PUBLICATION_RECOVERY_PROOF_SCHEMA_V1_0
    legacy_proof["proof_digest"] = u12.digest(
        {k: v for k, v in legacy_proof.items() if k != "proof_digest"})
    try:
        u12.validate_publication_recovery_proof(
            legacy_proof, intent=intent, data=fx.binding(), applying=True)
        observed["new_loader_v1_0_proof"] = "ACCEPTED"
    except u12.R0BValidationRefused as exc:
        observed["new_loader_v1_0_proof"] = \
            "R0BValidationRefused: " + exc.message
    ok = all(not value.startswith("ACCEPTED")
             for value in observed.values())
    return row(
        "old_reader_and_v1_0_payload_refusal",
        "the immediately previous ac1e5b4 reader refuses every corrected "
        "payload and the new loader refuses the frozen v1.0 decision/proof "
        "(no silent upgrade, no rollback readability)",
        "every old/legacy variant refused",
        observed, ok)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
CASES = (
    case_valid_chain,
    case_source_counterexample_old_shape,
    case_source_counterexample_new_shape,
    case_source_activation_content,
    case_authority_content,
    case_request_reconstruction,
    case_proof_counterexample_paths,
    case_semantic_rehash_matrix,
    case_proof_content_fences,
    case_old_reader_refusal,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="u12-r0b-correction-") as tmp:
        root = Path(tmp)
        rows = [case(root) for case in CASES]
    matrix = {
        "schema": "u12-r0b-publication-recovery-correction-matrix/1.0",
        "adapter_module": u12.ADAPTER_MODULE,
        "adapter_digest_lf": u12.adapter_digest(),
        "proof_schema": u12.PUBLICATION_RECOVERY_PROOF_SCHEMA,
        "decision_schema": u12.PUBLICATION_RECOVERY_DECISION_SCHEMA,
        "migration_schema": u12.PUBLICATION_MIGRATION_SCHEMA,
        "previous_adapter_commit": u12.PREVIOUS_PUBLICATION_ADAPTER_COMMIT,
        "previous_adapter_digest": u12.PREVIOUS_PUBLICATION_ADAPTER_DIGEST,
        "live_access": False,
        "production_ledger_access": False,
        "rows": rows,
    }
    matrix["passed"] = sum(1 for item in rows if item["pass"])
    matrix["total"] = len(rows)
    matrix["ok"] = matrix["passed"] == matrix["total"]
    output.write_text(json.dumps(matrix, ensure_ascii=False, indent=2,
                                 sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"ok": matrix["ok"], "passed": matrix["passed"],
                      "total": matrix["total"], "output": str(output)},
                     ensure_ascii=False))
    return 0 if matrix["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
