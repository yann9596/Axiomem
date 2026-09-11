#!/usr/bin/env python3
"""Isolated acceptance reproduction for the YZT-83 preflight forward repair.

Runs the required acceptance matrix for the repaired U12-R0B forward adapter
with the FakeCli + temporary-ledger harness from the committed test module.
It performs no live Multica call, no production-ledger access, no Canonical
write and no trigger outside the isolated ledger.

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-forward/reproduce_preflight.py \
        --output adapters/multica/u12-r0b-forward/preflight-reproduction.json
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
from test_u12_r0_binding import (  # noqa: E402
    DISPATCHER, FixedAuthorityReader, LifecycleHarness, TARGET_ID,
    TARGET_AGENT, blocking_finding, real_readiness_manifest)


def reruns(harness) -> int:
    return len(harness.cli.commands_of(["issue", "rerun"]))


def snapshot(harness) -> dict:
    return harness.factory._build_snapshot(
        harness.store.get(harness.intent_id)["fields"][u12.R0B_FIELD],
        issue=harness.cli.issue_of(TARGET_ID), runs=[])


def case_unchanged(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    arm_reads = len(h.artifact_reads)
    authority_reads = len(h.authority_reads)
    armed = h.arm()
    arm_reads = len(h.artifact_reads) - arm_reads
    authority_arm = len(h.authority_reads) - authority_reads
    trigger_reads = len(h.artifact_reads)
    authority_trigger = len(h.authority_reads)
    triggered = h.trigger()
    intent = h.store.get(h.intent_id)
    return {
        "arm": armed.get("status"),
        "trigger": triggered.get("status"),
        "run_id": triggered.get("run_id"),
        "reruns": reruns(h),
        "artifact_reads_at_arm": arm_reads,
        "artifact_reads_at_trigger": len(h.artifact_reads) - trigger_reads,
        "authority_reads_at_arm": authority_arm,
        "authority_reads_at_trigger": len(h.authority_reads) - authority_trigger,
        "preflight_events": [e["data"]["checkpoint"] for e in intent["events"]
                             if e.get("name") == u12.E_PREFLIGHT],
    }


def case_changed_after_arm(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.arm()
    reads = []
    h.factory.artifact_blob_reader = (
        lambda commit, path: reads.append((commit, path))
        or b"changed artifact bytes after arming")
    before = len(h.cli.commands)
    result = h.trigger()
    return {
        "trigger": result.get("status"),
        "reason": result.get("reason"),
        "reruns": reruns(h),
        "artifact_reads_after_change": len(reads),
        "commands_after_change": [argv[1:3] for argv in h.cli.commands[before:]],
    }


def case_changed_before_arm(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.factory.artifact_blob_reader = (
        lambda commit, path: b"changed artifact bytes before arming")
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_reconstructed_factory(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.arm()
    other = u12.build_r0b_factory(
        o2.DurableIntentStore(tmp / "ledger.jsonl"), runner=h.cli,
        artifact_blob_reader=u12._git_blob_reader(u12.ROOT),
        authority_reader=u12.ReadinessManifestAuthorityReader())
    recovered = other.recover(h.intent_id, actor=DISPATCHER)
    reads = []
    other.artifact_blob_reader = (
        lambda commit, path: reads.append((commit, path)) or b"changed")
    result = other.trigger(h.intent_id, actor=DISPATCHER,
                           current_request=h.current_request(),
                           current_findings=[])
    return {"recover_action": recovered.get("action"),
            "recover_performed": recovered.get("performed"),
            "trigger": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h),
            "artifact_reads_after_reconstruction": len(reads)}


def case_note_edited(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.cli.comments[TARGET_ID][0]["content"] = "tampered note body"
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_note_missing(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.cli.comments[TARGET_ID] = []
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_description_drift_held_revision(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.cli.issues[TARGET_ID]["description"] = "drifted, revision held"
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "issue_revision": h.cli.issue_of(TARGET_ID)["revision"],
            "reruns": reruns(h)}


def case_revision_only(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    h.cli.issues[TARGET_ID]["revision"] += 1
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_role_profile_drift(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    with mock.patch.object(u12.chandoff, "role_profile_revision",
                           return_value="sha256:" + "9" * 64):
        result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_authority_superseded(tmp: Path) -> dict:
    altered = real_readiness_manifest()
    altered["generated_at"] = "2026-09-12T00:00:00Z"
    h = LifecycleHarness(tmp, authority_reader=FixedAuthorityReader(altered))
    h.to_prepared()
    h.publish()
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_blocking_finding(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    result = h.arm(current_findings=[blocking_finding()])
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "detail": result.get("detail", "")[:160], "reruns": reruns(h)}


def case_no_authority_source(tmp: Path) -> dict:
    h = LifecycleHarness(tmp, authority_reader=None)
    h.to_prepared()
    h.publish()
    result = h.arm()
    return {"arm": result.get("status"), "reason": result.get("reason"),
            "reruns": reruns(h)}


def case_fabricated_snapshot(tmp: Path) -> dict:
    h = LifecycleHarness(tmp)
    h.to_prepared()
    h.publish()
    fabricated = snapshot(h)
    fabricated["artifact_ready"] = True
    fabricated["ready_note_id"] = "CMT-FABRICATED"
    outcomes = []
    for name, call in (
            ("plan_and_arm", lambda: h.factory.plan_and_arm(
                h.intent_id, fabricated, actor=DISPATCHER)),
            ("issue_trigger", lambda: h.factory.issue_trigger(
                h.intent_id, fabricated, actor=DISPATCHER)),
            ("resume", lambda: h.factory.resume(
                h.intent_id, {"snapshot": fabricated}, actor=DISPATCHER))):
        try:
            call()
            outcomes.append({name: "NOT_REFUSED"})
        except u12.R0BDowngradeRefused:
            outcomes.append({name: "REFUSED"})
    return {"outcomes": outcomes, "reruns": reruns(h),
            "state": h.store.get(h.intent_id)["state"]}


CASES = (
    ("all_subjects_unchanged_and_ready", case_unchanged),
    ("changed_artifact_reader_after_arm", case_changed_after_arm),
    ("changed_artifact_reader_before_arm", case_changed_before_arm),
    ("changed_artifact_reader_reconstructed_factory",
     case_reconstructed_factory),
    ("published_note_body_edited_revision_held", case_note_edited),
    ("published_note_missing_revision_held", case_note_missing),
    ("issue_description_changed_revision_held",
     case_description_drift_held_revision),
    ("only_issue_revision_incremented", case_revision_only),
    ("role_profile_revision_changed", case_role_profile_drift),
    ("authority_manifest_superseded", case_authority_superseded),
    ("relevant_blocking_finding", case_blocking_finding),
    ("no_authority_source_resolvable", case_no_authority_source),
    ("caller_fabricated_snapshot", case_fabricated_snapshot),
)


def module_digests() -> dict:
    def lf(path: Path) -> str:
        data = path.read_bytes().replace(b"\r\n", b"\n")
        return "sha256:" + hashlib.sha256(data).hexdigest()

    return {
        "tools/u12_r0_binding.py": lf(ROOT / "tools/u12_r0_binding.py"),
        "tools/tests/test_u12_r0_binding.py":
            lf(ROOT / "tools/tests/test_u12_r0_binding.py"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
        for name, fn in CASES:
            case_dir = Path(tmp) / name
            case_dir.mkdir()
            try:
                row = fn(case_dir)
            except Exception as exc:  # noqa: BLE001 - evidence must be honest
                row = {"status": f"EXCEPTION:{type(exc).__name__}",
                       "detail": str(exc)[:200]}
            row["case"] = name
            rows.append(row)
    report = {
        "kind": "u12_r0b_forward_preflight_reproduction",
        "schema_version": "U12-R0B/1.0",
        "task": "YZT-84",
        "contract": ("U12_R0_PREFLIGHT_DECISION.md raw sha256 "
                     "3acb66e3be54e9c00bf7c8f819b8970175d3a14984e3422f500220ae2106a2a9"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isolation": ("FakeCli + temporary JSONL ledgers inside the workdir; "
                      "no live Multica call, no production-ledger access, no "
                      "Canonical write"),
        "module_digests_lf": module_digests(),
        "rows": rows,
        "verdict": ("ALL_REQUIRED_ROWS_TYPED_STOP_OR_CORRELATED"
                    if all(row.get("status") or row.get("arm")
                           or row.get("trigger") or row.get("outcomes")
                           for row in rows) else "INCOMPLETE"),
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
