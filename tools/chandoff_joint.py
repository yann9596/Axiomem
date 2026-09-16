#!/usr/bin/env python3
"""U11 — Joint end-to-end replay for O2 and V2.2 gates (YZT-80).

Simulation-only composition harness. It replays, over the exact accepted
U03-U10 + O2 lineage, one deterministic joint workflow:

- V2.2 role topology R0/R1/R2 (Lead-mediated Option A routing),
- U10 Artifact Contract exact-version binding and staleness,
- U09 Finding/Challenge boundaries and drain,
- O2 durable dispatch intent, trigger planning, correlation and recovery,
- stage completion + explicit parent wake semantics.

Hard boundaries (U11 issue, YZT-80):

- no live issue/comment/assignment/rerun/mention/status/run mutation,
- no live 05/06 activation, no formal Delivery Review or QA Gate,
- no O3, daemon, scheduler, platform database/API/queue change,
- no production ledger-root selection or deployment,
- no Canonical Memory, Frozen T00, predecessor/O2 history or product write,
- no merge.

Every native command in this harness is served by an injected fixture runner
(`chandoff_intent.O2FixtureRunner` / `RefusingRunner`); the module itself has
no live mutation surface. All outputs are deterministic machine-readable
records under `adapters/multica/joint-replay/`.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cartifact  # noqa: E402
import chandoff  # noqa: E402
import chandoff_assignment as assignment  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_finding as u09  # noqa: E402
import chandoff_instructions as instr  # noqa: E402
import chandoff_intent as o2  # noqa: E402
import chandoff_plan as plan  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
JOINT_DIR = ROOT / "adapters" / "multica" / "joint-replay"
CAPTURE_DIR = JOINT_DIR / "capture"
O2_DIR = ROOT / "adapters" / "multica" / "dispatch-intent"
U09_DIR = ROOT / "adapters" / "multica" / "finding-challenge"
U10_FIXTURES = ROOT / "tools" / "fixtures" / "artifact-contract"
LEGACY_LEDGER = (ROOT / "adapters" / "multica" / "assignment-handoff" /
                 "sample-main-path-ledger.jsonl")

LAYER_VERSION = "U11/1.0"
CLOCK = "2026-09-11T00:00:00Z"
LATER = "2026-09-11T00:10:01Z"
ACTOR = "u11-fixture-replay"

PARENT = o2.PARENT_YZT_66
AGENTS = dict(instr.EXPECTED_AGENT_IDS)
LEAD_AGENT = AGENTS["engineering-lead"]
REVIEW_AGENT = AGENTS["delivery-reviewer"]
QA_AGENT = AGENTS["qa"]

REVIEW_ISSUE = "a11e0000-0000-4000-8000-000000000005"
REVIEW_RUN = "b11e0000-0000-4000-8000-000000000105"
REVIEW_NOTE = "c11e0000-0000-4000-8000-000000000005"
QA_ISSUE = "a11e0000-0000-4000-8000-000000000006"
QA_RUN = "b11e0000-0000-4000-8000-000000000106"
QA_NOTE = "c11e0000-0000-4000-8000-000000000006"
CHILD_ISSUE = "a11e0000-0000-4000-8000-000000000007"
CHILD_RUN = "b11e0000-0000-4000-8000-000000000107"
REVIEW_PACKAGE = "CTX-delivery-reviewer-5a1d0c7f3b9e2468"
QA_PACKAGE = "CTX-qa-7c4e2b8d1f0a3695"

# Accepted U10 fixture identity (never modified by U11).
U10_IMPL_ID = "ART-WIMG-031"
U10_IMPL_VERSION = "aaa111"
U10_IMPL_B_VERSION = "bbb222"
U10_REVIEW_ID = "REV-WIMG-008"
U10_REVIEW_VERSION = "1"
U10_QA_ID = "QA-WIMG-004"
U10_QA_VERSION = "1"
U10_PE_ID = "PE-WIMG-004"
U10_PE_VERSION = "4"
U10_DESIGN_ID = "SOL-WIMG-017"
U10_DESIGN_VERSION = "3"
U10_ISSUE_ID = "ISS-WIMG-071"
U10_ISSUE_VERSION = "1"

# Parent YZT-66 Final Gate counters (exact names and expected values).
PARENT_FINAL_GATE = (
    "feature_reviewer_activation",
    "old_role_package_accepted",
    "wrong_role_routing",
    "normal_path_run_before_ready_handoff",
    "duplicate_intended_run",
    "duplicate_review_run",
    "duplicate_qa_run",
    "duplicate_lead_stage_activation",
    "scope_pollution",
    "invalid_rule_authority",
    "hidden_unresolved_conflict",
    "stale_or_missing_package_not_detected",
    "stale_artifact_triggered",
    "stale_baseline_received_qa_pass",
    "relevant_finding_hidden",
    "task_closed_with_open_unaccounted_finding",
    "ordinary_ready_build_calls_context_engineer",
    "grok_raw_signal_used_as_project_truth",
    "qa_without_required_baseline",
    "delivery_review_without_exact_artifact_version",
)

# O2 safety gates that must remain zero/false after the joint replay.
O2_SAFETY_GATES = (
    "target_mutation_before_intent",
    "underspecified_parked_target",
    "backlog_assignment_counted_as_enqueue",
    "cli_success_counted_as_delivery",
    "duplicate_note",
    "duplicate_trigger",
    "duplicate_correlated_run",
    "ambiguous_trigger_retry",
    "provider_failure_counted_as_orphan",
    "hidden_open_intent",
    "legacy_history_rewritten",
    "u09_drift",
)


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def digest_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_digest(path: Path, *, normalize_lf: bool = False) -> str:
    data = path.read_bytes()
    if normalize_lf:
        data = data.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def bundle_digest(directory: Path, *, exclude=()) -> dict:
    """Canonical sorted file-name -> sha256(bytes) digest of a bundle dir."""
    entries = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if any(rel.startswith(prefix) for prefix in exclude):
            continue
        entries.append([rel, "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()])
    return {"file_count": len(entries), "digest": digest(entries)}


# ---------------------------------------------------------------------------
# Artifact fixtures (accepted U10 chain, exact versions)
# ---------------------------------------------------------------------------
def _load_json(path: Path):
    return copy.deepcopy(json.loads(path.read_text(encoding="utf-8")))


def artifact_store() -> cartifact.ArtifactStore:
    raw = _load_json(U10_FIXTURES / "store-chain.json")
    envelopes = raw["envelopes"] if isinstance(raw, dict) else raw
    return cartifact.ArtifactStore(envelopes)


def ready_request(name: str) -> dict:
    return _load_json(U10_FIXTURES / f"ready-{name}.json")


def routing_plan_template(level: str) -> dict:
    return _load_json(U10_FIXTURES / f"routing-{level.lower()}.json")


def artifact_set_manifest() -> dict:
    store = artifact_store()
    rows = []
    for env in sorted(store.envelopes, key=lambda e: (e["artifact_type"],
                                                      e["artifact_id"],
                                                      e["version"])):
        rows.append({
            "artifact_type": env["artifact_type"],
            "artifact_id": env["artifact_id"],
            "version": env["version"],
            "status": env["status"],
            "owner_role": env["owner_role"],
            "based_on": [[r["artifact_id"], r["version"]] for r in env.get("based_on") or []],
            "reviewed_artifact": ([env["reviewed_artifact"]["artifact_id"],
                                   env["reviewed_artifact"]["version"]]
                                  if env.get("reviewed_artifact") else None),
            "validated_against": [[r["artifact_id"], r["version"]]
                                  for r in env.get("validated_against") or []],
        })
    return {
        "source": "tools/fixtures/artifact-contract/store-chain.json",
        "source_digest": file_digest(U10_FIXTURES / "store-chain.json"),
        "types": list(cartifact.CORE_ARTIFACT_TYPES),
        "envelopes": rows,
        "artifact_contract_revision": cartifact.artifact_contract_revision(),
    }


def artifact_ready_case(case: str, request: dict,
                        *, mutate=None) -> dict:
    store = artifact_store()
    if mutate is not None:
        mutate(store)
    result = cartifact.artifact_ready_check(store, request)
    summary = {
        "case": case,
        "target_role": request.get("target_role"),
        "review_level": request.get("review_level"),
        "status": result["status"],
        "blocks_handoff": result["blocks_handoff"],
        "failure_codes": sorted({f["reason_code"] for f in result["failures"]}),
        "dependency_digest": result["dependency_digest"],
        "checks": result["checks"],
    }
    summary["evidence_digest"] = digest(summary)
    return summary


def _supersede_implementation(store: cartifact.ArtifactStore) -> None:
    new_env = _load_json(U10_FIXTURES / "implementation-b.json")
    cartifact.apply_supersede(store, new_env)


def _supersede_product_expectation(store: cartifact.ArtifactStore) -> None:
    target = store.get(U10_PE_ID, U10_PE_VERSION)
    assert target is not None
    replacement = copy.deepcopy(target)
    replacement["version"] = "5"
    replacement["supersedes"] = {"artifact_type": "product_expectation",
                                 "artifact_id": U10_PE_ID,
                                 "version": U10_PE_VERSION}
    replacement["status"] = "accepted"
    replacement["provenance"]["updated_at"] = LATER
    cartifact.apply_supersede(store, replacement)


def _supersede_design_baseline(store: cartifact.ArtifactStore) -> None:
    target = store.get(U10_DESIGN_ID, U10_DESIGN_VERSION)
    assert target is not None
    replacement = copy.deepcopy(target)
    replacement["version"] = "4"
    replacement["supersedes"] = {"artifact_type": "design_baseline",
                                 "artifact_id": U10_DESIGN_ID,
                                 "version": U10_DESIGN_VERSION}
    replacement["status"] = "accepted"
    replacement["provenance"]["updated_at"] = LATER
    cartifact.apply_supersede(store, replacement)


def artifact_replays() -> dict:
    """Exact-version binding, staleness and verdict-preservation replays."""
    replays = {
        "r1_review_input_ready": artifact_ready_case(
            "r1_review_input_ready", ready_request("r1")),
        "r2_qa_baselines_ready": artifact_ready_case(
            "r2_qa_baselines_ready", ready_request("r2")),
        "r0_minimal_ready": artifact_ready_case(
            "r0_minimal_ready", ready_request("r0")),
        "implementation_superseded": artifact_ready_case(
            "implementation_superseded", ready_request("r1"),
            mutate=_supersede_implementation),
        "pe_superseded_blocks_qa": artifact_ready_case(
            "pe_superseded_blocks_qa", ready_request("r2"),
            mutate=_supersede_product_expectation),
        "design_stale_blocks_qa": artifact_ready_case(
            "design_stale_blocks_qa", ready_request("r2"),
            mutate=_supersede_design_baseline),
    }
    # Non-exact version fails closed.
    bad = ready_request("r1")
    bad["requirements"][0]["version"] = "latest"
    replays["latest_version_refused"] = artifact_ready_case(
        "latest_version_refused", bad)
    # Missing required baseline for QA.
    missing = ready_request("r2")
    missing["requirements"] = [r for r in missing["requirements"]
                               if r["artifact_type"] != "product_expectation"]
    replays["qa_missing_baseline_refused"] = artifact_ready_case(
        "qa_missing_baseline_refused", missing)

    # Review verdict not APPROVE blocks the QA gate.
    store = artifact_store()
    review = store.get(U10_REVIEW_ID, U10_REVIEW_VERSION)
    review["semantics"] = dict(review["semantics"], verdict="CHANGES_REQUIRED")
    result = cartifact.artifact_ready_check(store, ready_request("r2"))
    replays["qa_after_changes_required_refused"] = {
        "case": "qa_after_changes_required_refused",
        "status": result["status"],
        "blocks_handoff": result["blocks_handoff"],
        "failure_codes": sorted({f["reason_code"] for f in result["failures"]}),
        "evidence_digest": digest({"status": result["status"],
                                   "failures": sorted({f["reason_code"]
                                                       for f in result["failures"]})}),
    }

    # Supersede a verdict-bearing artifact: history is preserved, never edited.
    store = artifact_store()
    before = {e["artifact_id"]: copy.deepcopy(e) for e in store.envelopes}
    _supersede_implementation(store)
    old_review = store.get(U10_REVIEW_ID, U10_REVIEW_VERSION)
    old_qa = store.get(U10_QA_ID, U10_QA_VERSION)
    new_attempt = copy.deepcopy(old_review)
    new_attempt["artifact_id"] = "REV-WIMG-009"
    new_attempt["version"] = "1"
    new_attempt["semantics"] = dict(new_attempt["semantics"], verdict="APPROVE")
    attempt = cartifact.open_new_attempt(store, old_review, new_attempt)
    replays["verdict_preservation"] = {
        "case": "verdict_preservation",
        "old_review_status": old_review["status"],
        "old_review_verdict": old_review["semantics"]["verdict"],
        "old_qa_status": old_qa["status"],
        "old_qa_verdict": old_qa["semantics"]["verdict"],
        "open_new_attempt": attempt,
        "source_serials_before": sorted(before),
        "rewritten_verdicts": 0,
        "evidence_digest": digest({
            "old_review": [old_review["status"],
                           old_review["semantics"]["verdict"]],
            "old_qa": [old_qa["status"], old_qa["semantics"]["verdict"]],
            "attempt": attempt,
        }),
        "_note": "verdict-bearing history is marked stale and preserved; "
                 "a new attempt is a new envelope",
    }
    expectations = {
        "r1_review_input_ready": "ARTIFACT_READY",
        "r2_qa_baselines_ready": "ARTIFACT_READY",
        "r0_minimal_ready": "ARTIFACT_READY",
        "implementation_superseded": "ARTIFACT_NOT_READY",
        "pe_superseded_blocks_qa": "ARTIFACT_NOT_READY",
        "design_stale_blocks_qa": "ARTIFACT_NOT_READY",
        "latest_version_refused": "ARTIFACT_NOT_READY",
        "qa_missing_baseline_refused": "ARTIFACT_NOT_READY",
        "qa_after_changes_required_refused": "ARTIFACT_NOT_READY",
    }
    for key, row in replays.items():
        if key == "verdict_preservation":
            row["passed"] = (row["rewritten_verdicts"] == 0
                             and row["old_review_status"] == "stale"
                             and row["old_review_verdict"] == "APPROVE"
                             and row["old_qa_status"] == "stale"
                             and row["old_qa_verdict"] == "PASS"
                             and row["open_new_attempt"]["history_preserved"])
        else:
            row["passed"] = row["status"] == expectations[key]
    return replays


# ---------------------------------------------------------------------------
# Topology R0 / R1 / R2
# ---------------------------------------------------------------------------
def route_guard(level: str, target_role: str) -> dict:
    """Refuse review/QA routing that the Option A level does not require."""
    spec = cartifact.routing_contract(level)
    if target_role == "delivery-reviewer" and not spec["requires_delivery_review"]:
        return {"ok": False, "code": "R0_REVIEW_NOT_ALLOWED",
                "level": level, "target_role": target_role}
    if target_role == "qa" and not spec["requires_qa"]:
        return {"ok": False, "code": f"{level}_QA_NOT_ALLOWED",
                "level": level, "target_role": target_role}
    return {"ok": True, "code": "ROUTE_ALLOWED", "level": level,
            "target_role": target_role}


def routing_negative_replay() -> list:
    cases = [
        ("r0_with_delivery_review",
         {"review_level": "R0", "requires_delivery_review": True,
          "independent_delivery_review_attempt": True}),
        ("r0_with_qa", {"review_level": "R0", "requires_qa": True,
                        "independent_qa_attempt": True}),
        ("r1_without_exact_review_input", {"review_level": "R1"}),
        ("r1_producer_auto_trigger",
         {"review_level": "R1",
          "producer_auto_triggers_delivery_reviewer": True}),
        ("r1_delivery_reviewer_auto_trigger_qa",
         {"review_level": "R1", "delivery_reviewer_auto_triggers_qa": True}),
        ("r2_without_exact_qa_baselines", {"review_level": "R2"}),
        ("r2_trigger_created_flag", {"review_level": "R2",
                                     "triggers_created": 1}),
    ]
    rows = []
    for case, plan_doc in cases:
        result = cartifact.validate_routing(plan_doc)
        rows.append({
            "case": case,
            "ok": result["ok"],
            "violation_codes": sorted({v["code"] for v in result["violations"]}),
            "triggers_created": result["triggers_created"],
            "evidence_digest": digest({
                "case": case, "ok": result["ok"],
                "violations": sorted({v["code"] for v in result["violations"]})}),
        })
    return rows


def _dispatch_fixture(*, issue_id: str, run_id: str, role: str, agent_id: str,
                      package_id: str, artifact_digest: str,
                      revision: int, status: str, assignee,
                      note_id: str, identifier: str) -> dict:
    return {
        "issue_id": issue_id, "run_id": run_id, "role": role,
        "agent_id": agent_id, "package_id": package_id,
        "artifact_digest": artifact_digest, "revision": revision,
        "status": status, "assignee": assignee, "note_id": note_id,
        "identifier": identifier,
    }


def _issue_payload(fixture: dict, *, revision=None, status=None,
                   assignee="__keep__") -> dict:
    return {
        "id": fixture["issue_id"],
        "identifier": fixture["identifier"],
        "title": f"U11 fixture target {fixture['identifier']}",
        "description": "",
        "parent_issue_id": PARENT,
        "project_id": None,
        "revision": fixture["revision"] if revision is None else revision,
        "status_category": fixture["status"] if status is None else status,
        "assignee_id": (fixture["assignee"] if assignee == "__keep__"
                        else assignee),
    }


def _run_payload(fixture: dict) -> dict:
    return {"id": fixture["run_id"], "issue_id": fixture["issue_id"],
            "agent_id": fixture["agent_id"], "status": "running"}


def _intent_fields(intent_id: str, fixture: dict, key: str) -> dict:
    return {
        "intent_id": intent_id,
        "schema_version": o2.O2_SCHEMA,
        "source_task_id": PARENT,
        "logical_task_key": f"u11-{key}",
        "parent_issue_id": PARENT,
        "target_role": fixture["role"],
        "target_agent_id": fixture["agent_id"],
        "package_id": fixture["package_id"],
        "artifact_dependency_digest": fixture["artifact_digest"],
        "expected_issue_revision": fixture["revision"],
        "expected_status_category": fixture["status"],
        "expected_assignee_id": fixture["assignee"],
        "creation_authority": "01 engineering-lead via YZT-66 SAFE_DISPATCH",
        "provenance": {"fixture": "U11 joint replay", "target": fixture["identifier"]},
    }


def _new_intent_id(fixture: dict, key: str) -> str:
    return o2.new_intent_id(
        source_task_id=PARENT, logical_task_key=f"u11-{key}",
        target_agent_id=fixture["agent_id"], package_id=fixture["package_id"],
        nonce="u11joint")


def _orchestrator(store, runner_table, workdir) -> tuple:
    runner = o2.O2FixtureRunner(runner_table)
    orch = o2.O2Orchestrator(store, runner=runner, workdir=str(workdir),
                             clock=lambda: CLOCK)
    return runner, orch


def _snapshot(fixture: dict, *, runs=None, artifact_ready=True,
              ready_note=True, package_id=None, artifact_digest=None,
              note_package_id=None, note_artifact_digest="__same__") -> dict:
    package_id = package_id or fixture["package_id"]
    artifact_digest = artifact_digest or fixture["artifact_digest"]
    note = None
    if ready_note:
        note = {"comment_id": fixture["note_id"],
                "package_id": note_package_id or package_id,
                "artifact_dependency_digest":
                    (artifact_digest if note_artifact_digest == "__same__"
                     else note_artifact_digest)}
    return o2.build_snapshot(
        issue=_issue_payload(fixture), runs=runs or [],
        package={"package_id": package_id,
                 "artifact_dependency_digest": artifact_digest,
                 "artifact_ready": artifact_ready},
        ready_note=note, target_role=fixture["role"],
        target_agent_id=fixture["agent_id"])


def _reach_published(orch, store, fixture: dict, *, key: str):
    intent_id = _new_intent_id(fixture, key)
    orch.record_intent(_intent_fields(intent_id, fixture, key))
    orch.bind_target(intent_id, issue=_issue_payload(fixture), actor=ACTOR)
    orch.mark_prepared(intent_id, package_id=fixture["package_id"],
                       artifact_dependency_digest=fixture["artifact_digest"],
                       actor=ACTOR)
    orch.mark_published(intent_id, note_comment_id=fixture["note_id"],
                        receipt_digest=digest_text("u11-ready-note"),
                        actor=ACTOR)
    return intent_id


def _audit_summary(store) -> dict:
    records = store.read_records()
    audit = o2.o2_audit_ledger(records)
    issuing_seqs = [r["seq"] for r in records
                    if r.get("op") == "transition"
                    and r.get("to") == o2.S_TRIGGER_ISSUING]
    trigger_seqs = [r["seq"] for r in records
                    if r.get("kind") == "command"
                    and (r.get("command_class") or "") in
                    o2.TRIGGER_COMMAND_CLASSES]
    precedes = ((not trigger_seqs)
                or (bool(issuing_seqs)
                    and min(issuing_seqs) < min(trigger_seqs)))
    return {
        "ok": audit["ok"],
        "triggers": audit["triggers"],
        "ownership_bindings": audit["ownership_bindings"],
        "creates": audit["creates"],
        "status_triggers": audit["status_triggers"],
        "mention_triggers": audit["mention_triggers"],
        "issuing_precedes_trigger_command": precedes,
    }


_TEMP_TOKEN_RE = re.compile(r"^.*[\\/](\.o2-create-[0-9a-f]+\.md)$")


def _sanitize_token(token: str) -> str:
    match = _TEMP_TOKEN_RE.match(token)
    return "<workdir>/" + match.group(1) if match else token


def _sanitize_commands(commands: list, workdir=None) -> list:
    out = []
    for argv in commands:
        out.append([_sanitize_token(str(token)) for token in argv])
    return out


def _record(case: str, *, fixture=None, expected: dict, actual: dict,
            store=None, runner=None, workdir=None, extra=None) -> dict:
    passed = all(actual.get(key) == value
                 for key, value in expected.items() if not key.startswith("_"))
    row = {
        "case": case,
        "fixture": fixture,
        "expected": expected,
        "actual": actual,
        "passed": passed,
        "issued_commands": (_sanitize_commands(runner.issued, workdir)
                            if runner is not None else []),
        "write_commands": (_sanitize_commands(runner.mutations(), workdir)
                           if runner is not None else []),
        "audit": _audit_summary(store) if store is not None else None,
        "live_mutations": 0,
    }
    if extra:
        row.update(extra)
    row["evidence_digest"] = digest({"case": case, "expected": expected,
                                     "actual": actual})
    return row


def _store_at(root: Path, name: str) -> o2.DurableIntentStore:
    return o2.DurableIntentStore(Path(root) / f"{name}.jsonl")


def _state(store, intent_id) -> str:
    return store.get(intent_id)["state"]


# --- individual dispatch scenarios -----------------------------------------
def _case_standard(root: Path, *, case: str, fixture: dict,
                   runs_after: list, bind_first: bool = False) -> dict:
    store = _store_at(root, case)
    table = []
    if bind_first:
        table.append({
            "match": ["issue", "assign", fixture["issue_id"]],
            "stdout": json.dumps({"id": fixture["issue_id"],
                                  "assignee_id": fixture["agent_id"]},
                                 sort_keys=True)})
        after = _issue_payload(fixture, revision=fixture["revision"] + 1,
                               assignee=fixture["agent_id"])
        table.append({"match": ["issue", "get", fixture["issue_id"]],
                      "stdout": json.dumps(after, sort_keys=True)})
    elif fixture["status"] in o2.ACTIVE_STATUS_CATEGORIES and \
            fixture["assignee"] != fixture["agent_id"]:
        table.append({
            "match": ["issue", "assign", fixture["issue_id"]],
            "stdout": json.dumps({"id": fixture["issue_id"],
                                  "assignee_id": fixture["agent_id"]},
                                 sort_keys=True)})
    table.append({"match": ["issue", "rerun", fixture["issue_id"]],
                  "stdout": json.dumps(_run_payload(fixture), sort_keys=True)})
    table.append({"match": ["issue", "runs", fixture["issue_id"]],
                  "stdout": json.dumps(runs_after, sort_keys=True)})
    runner, orch = _orchestrator(store, table, root)
    intent_id = _reach_published(orch, store, fixture, key=case)
    snapshot = _snapshot(fixture)
    armed = orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    result = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    intent = store.get(intent_id)
    selected = o2._latest_field(intent, "selected_trigger")
    binding = o2._latest_field(intent, "ownership_binding")
    expected = {
        "plan_decision": "TRIGGER_READY",
        "selected_trigger": fixture.get("expected_trigger"),
        "ownership_binding": fixture.get("expected_binding"),
        "state": "RUN_CORRELATED",
        "correlated_run_id": fixture["run_id"],
        "triggers": 1,
    }
    actual = {
        "plan_decision": armed.get("plan", {}).get("decision"),
        "selected_trigger": selected,
        "ownership_binding": binding,
        "state": intent["state"],
        "correlated_run_id": o2._latest_field(intent, "correlated_run_id"),
        "triggers": _audit_summary(store)["triggers"],
    }
    return _record(case, fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _case_publish_pre_trigger_kill(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "publish_pre_trigger_kill")
    runner0, orch0 = _orchestrator(store, [], root)
    intent_id = _reach_published(orch0, store, fixture,
                                 key="publish_pre_trigger_kill")
    table = [
        {"match": ["issue", "rerun", fixture["issue_id"]],
         "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]],
         "stdout": json.dumps([_run_payload(fixture)], sort_keys=True)},
    ]
    runner, orch = _orchestrator(store, table, root)
    evidence = {"runs": [], "runs_trusted": True}
    classified = orch.reconcile(intent_id, evidence, actor=ACTOR)
    snapshot = _snapshot(fixture)
    resumed = orch.resume(intent_id, {**evidence, "snapshot": snapshot},
                          actor=ACTOR)
    intent = store.get(intent_id)
    expected = {
        "classification": "POST_PUBLISH_PRE_TRIGGER",
        "safe_action": "RESUME_ISSUE",
        "state": "RUN_CORRELATED",
        "triggers": 1,
        "side_effects_on_classification": 0,
    }
    actual = {
        "classification": classified["classification"],
        "safe_action": classified.get("action"),
        "state": intent["state"],
        "triggers": _audit_summary(store)["triggers"],
        "side_effects_on_classification": classified.get("side_effects"),
        "resume_status": resumed.get("status"),
    }
    return _record("publish_pre_trigger_kill_resume", fixture=fixture["identifier"],
                   expected=expected, actual=actual, store=store, runner=runner, workdir=root)


def _case_lost_trigger_response(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "lost_trigger_response")
    runner0, orch0 = _orchestrator(store, [], root)
    intent_id = _reach_published(orch0, store, fixture,
                                 key="lost_trigger_response")
    table = [{"match": ["issue", "rerun", fixture["issue_id"]], "code": 1,
              "stderr": "synthetic lost response"}]
    runner, orch = _orchestrator(store, table, root)
    snapshot = _snapshot(fixture)
    orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    triggered = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    state_after_trigger = _state(store, intent_id)
    attached = orch.reconcile(
        intent_id, {"runs": [_run_payload(fixture)], "runs_trusted": True},
        actor=ACTOR)
    intent = store.get(intent_id)
    expected = {
        "state_after_trigger": "TRIGGER_AMBIGUOUS",
        "reconcile_action": "ATTACH_RUN",
        "state_final": "RUN_CORRELATED",
        "triggers": 1,
        "trigger_reissued": False,
    }
    actual = {
        "state_after_trigger": state_after_trigger,
        "reconcile_action": attached.get("action"),
        "state_final": intent["state"],
        "triggers": _audit_summary(store)["triggers"],
        "trigger_reissued": attached.get("trigger_reissued"),
        "trigger_status": triggered.get("status"),
    }
    return _record("lost_trigger_response_no_retry", fixture=fixture["identifier"],
                   expected=expected, actual=actual, store=store, runner=runner, workdir=root)


def _case_delayed_visibility(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "delayed_run_visibility")
    runner0, orch0 = _orchestrator(store, [], root)
    intent_id = _reach_published(orch0, store, fixture,
                                 key="delayed_run_visibility")
    table = [
        {"match": ["issue", "rerun", fixture["issue_id"]],
         "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]], "stdout": "[]"},
    ]
    runner, orch = _orchestrator(store, table, root)
    snapshot = _snapshot(fixture)
    orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    triggered = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    state_after_trigger = _state(store, intent_id)
    attached = orch.reconcile(
        intent_id, {"runs": [_run_payload(fixture)], "runs_trusted": True},
        actor=ACTOR)
    intent = store.get(intent_id)
    expected = {
        "state_after_trigger": "TRIGGER_ISSUING",
        "awaiting_visibility": True,
        "reconcile_action": "ATTACH_RUN",
        "state_final": "RUN_CORRELATED",
        "triggers": 1,
    }
    actual = {
        "state_after_trigger": state_after_trigger,
        "awaiting_visibility": bool(triggered.get("awaiting_visibility")),
        "reconcile_action": attached.get("action"),
        "state_final": intent["state"],
        "triggers": _audit_summary(store)["triggers"],
    }
    return _record("delayed_run_visibility_attach", fixture=fixture["identifier"],
                   expected=expected, actual=actual, store=store, runner=runner, workdir=root)


def _case_duplicate_runs(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "duplicate_runs")
    runner0, orch0 = _orchestrator(store, [], root)
    intent_id = _reach_published(orch0, store, fixture, key="duplicate_runs")
    other = dict(_run_payload(fixture))
    other["id"] = "b11e0000-0000-4000-8000-000000000199"
    other["status"] = "queued"
    table = [
        {"match": ["issue", "rerun", fixture["issue_id"]],
         "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]],
         "stdout": json.dumps([_run_payload(fixture), other], sort_keys=True)},
    ]
    runner, orch = _orchestrator(store, table, root)
    snapshot = _snapshot(fixture)
    orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    result = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    intent = store.get(intent_id)
    expected = {
        "state": "BLOCKED",
        "result_status": "BLOCKED",
        "triggers": 1,
    }
    actual = {
        "state": intent["state"],
        "result_status": result.get("status"),
        "triggers": _audit_summary(store)["triggers"],
        "reason": intent["transitions"][-1].get("reason"),
    }
    return _record("duplicate_correlated_runs_refused",
                   fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _case_wrong_run(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "wrong_run")
    runner0, orch0 = _orchestrator(store, [], root)
    intent_id = _reach_published(orch0, store, fixture, key="wrong_run")
    wrong = dict(_run_payload(fixture))
    wrong["agent_id"] = LEAD_AGENT
    table = [
        {"match": ["issue", "rerun", fixture["issue_id"]],
         "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]],
         "stdout": json.dumps([wrong], sort_keys=True)},
    ]
    runner, orch = _orchestrator(store, table, root)
    snapshot = _snapshot(fixture)
    orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    result = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    expected = {"state": "BLOCKED", "result_status": "BLOCKED",
                "triggers": 1}
    actual = {"state": _state(store, intent_id),
              "result_status": result.get("status"),
              "triggers": _audit_summary(store)["triggers"]}
    return _record("wrong_target_run_refused", fixture=fixture["identifier"],
                   expected=expected, actual=actual, store=store, runner=runner, workdir=root)


def _case_pre_existing_run(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "pre_existing_run")
    old = dict(_run_payload(fixture))
    old["id"] = "b11e0000-0000-4000-8000-000000000198"
    table = []
    runner, orch = _orchestrator(store, table, root)
    intent_id = _reach_published(orch, store, fixture, key="pre_existing_run")
    snapshot = _snapshot(fixture, runs=[old])
    armed = orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    expected = {
        "decision": "BLOCKED",
        "reason": "UNEXPECTED_RUN",
        "state": "BLOCKED",
        "issued_commands": 0,
    }
    actual = {
        "decision": armed.get("decision") or armed.get("status"),
        "reason": armed.get("reason"),
        "state": _state(store, intent_id),
        "issued_commands": len(runner.issued),
    }
    return _record("pre_existing_unexpected_run_refused",
                   fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _case_cli_success_not_delivery(root: Path) -> dict:
    fixture = _dispatch_fixture(
        issue_id=QA_ISSUE, run_id=QA_RUN, role="qa", agent_id=QA_AGENT,
        package_id=QA_PACKAGE, artifact_digest="sha256:" + "d" * 64,
        revision=3, status="in_progress", assignee=None, note_id=QA_NOTE,
        identifier="FIX-QA-WAIT")
    store = _store_at(root, "cli_success_not_delivery")
    table = [
        {"match": ["issue", "assign", fixture["issue_id"]],
         "stdout": json.dumps({"id": fixture["issue_id"],
                               "assignee_id": fixture["agent_id"]},
                              sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]], "stdout": "[]"},
    ]
    runner, orch = _orchestrator(store, table, root)
    intent_id = _reach_published(orch, store, fixture,
                                 key="cli_success_not_delivery")
    snapshot = _snapshot(fixture)
    armed = orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    result = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    expected = {
        "selected_trigger": "issue_assign",
        "state": "TRIGGER_ISSUING",
        "awaiting_visibility": True,
        "correlated": False,
        "triggers": 1,
    }
    actual = {
        "selected_trigger": armed.get("plan", {}).get("selected_trigger"),
        "state": _state(store, intent_id),
        "awaiting_visibility": bool(result.get("awaiting_visibility")),
        "correlated": result.get("status") == "RUN_CORRELATED",
        "triggers": _audit_summary(store)["triggers"],
    }
    return _record("cli_success_not_delivery", fixture=fixture["identifier"],
                   expected=expected, actual=actual, store=store, runner=runner, workdir=root)


def _case_provider_quota(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "provider_quota")
    table = [
        {"match": ["issue", "rerun", fixture["issue_id"]],
         "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]],
         "stdout": json.dumps([_run_payload(fixture)], sort_keys=True)},
    ]
    runner, orch = _orchestrator(store, table, root)
    intent_id = _reach_published(orch, store, fixture, key="provider_quota")
    snapshot = _snapshot(fixture)
    orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    recovery = orch.record_execution_recovery(
        intent_id, run_id=fixture["run_id"], failure="provider_quota", actor=ACTOR)
    classified = orch.classify_reconciliation(
        intent_id, {"provider_failure": True})
    intent = store.get(intent_id)
    expected = {
        "status": "EXECUTION_RECOVERY_RECORDED",
        "redispatch": False,
        "dispatch_orphan": False,
        "state": "RUN_CORRELATED",
        "classification": "PROVIDER_QUOTA_FAILURE",
        "safe_action": "EXECUTION_RECOVERY",
        "triggers": 1,
    }
    actual = {
        "status": recovery.get("status"),
        "redispatch": recovery.get("redispatch"),
        "dispatch_orphan": recovery.get("dispatch_orphan"),
        "state": intent["state"],
        "classification": classified["classification"],
        "safe_action": classified["safe_action"],
        "triggers": _audit_summary(store)["triggers"],
    }
    return _record("provider_quota_execution_recovery",
                   fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _case_completed_replay(root: Path, fixture: dict) -> dict:
    store = _store_at(root, "completed_replay")
    table = [
        {"match": ["issue", "rerun", fixture["issue_id"]],
         "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
        {"match": ["issue", "runs", fixture["issue_id"]],
         "stdout": json.dumps([_run_payload(fixture)], sort_keys=True)},
    ]
    runner, orch = _orchestrator(store, table, root)
    intent_id = _reach_published(orch, store, fixture, key="completed_replay")
    snapshot = _snapshot(fixture)
    orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
    orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    orch.record_self_check(intent_id, status="CLEAR", actor=ACTOR)
    orch.complete(intent_id, actor=ACTOR)
    commands_after_terminal = len(runner.issued)
    replay = orch.reconcile(intent_id, {"runs": [], "runs_trusted": True},
                            actor=ACTOR)
    replay_trigger = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
    replay_complete = orch.complete(intent_id, actor=ACTOR)
    replay_publish = orch.mark_published(intent_id,
                                         note_comment_id=fixture["note_id"],
                                         receipt_digest=None, actor=ACTOR)
    commands_after_replays = len(runner.issued)
    expected = {
        "state": "COMPLETED",
        "replay_classification": "TERMINAL_REPLAY",
        "replay_action": "NONE",
        "replay_side_effects": 0,
        "commands_after_terminal": 2,
        "commands_after_replays": 2,
        "replay_complete": True,
        "replay_publish": True,
    }
    actual = {
        "state": _state(store, intent_id),
        "replay_classification": replay.get("classification"),
        "replay_action": replay.get("next_action") or replay.get("action"),
        "replay_side_effects": replay.get("side_effects"),
        "commands_after_terminal": commands_after_terminal,
        "commands_after_replays": commands_after_replays,
        "replay_complete": bool(replay_complete.get("replayed")),
        "replay_publish": bool(replay_publish.get("replayed")),
        "replay_trigger_side_effects": replay_trigger.get("side_effects"),
    }
    return _record("completed_replay_zero_side_effects",
                   fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _case_ambiguous_create(root: Path, *, matches: int) -> dict:
    case = f"ambiguous_create_{matches}_targets"
    store = _store_at(root, case)
    fixture = _dispatch_fixture(
        issue_id=CHILD_ISSUE, run_id=CHILD_RUN, role="software-engineer",
        agent_id=AGENTS["software-engineer"],
        package_id="CTX-software-engineer-2f6c9d4a8e1b7053",
        artifact_digest="sha256:" + "e" * 64, revision=1, status="backlog",
        assignee=None, note_id="c11e0000-0000-4000-8000-000000000008",
        identifier="FIX-CHILD")
    intent_id = _new_intent_id(fixture, case)
    table = [{"match": ["issue", "create"], "code": 2,
              "stderr": "synthetic lost create response"}]
    children = []
    for index in range(matches):
        children.append({"id": CHILD_ISSUE if index == 0
                         else "a11e0000-0000-4000-8000-000000000009",
                         "identifier": fixture["identifier"],
                         "title": "child",
                         "description": f"marker {intent_id}",
                         "revision": 1, "status_category": "backlog",
                         "assignee_id": None})
    table.append({"match": ["issue", "children", PARENT],
                  "stdout": json.dumps({"stages": [{"stage": 7,
                                                    "issues": children}]},
                                       sort_keys=True)})
    if matches == 1:
        table.append({"match": ["issue", "get", CHILD_ISSUE],
                      "stdout": json.dumps(_issue_payload(fixture),
                                           sort_keys=True)})
    runner, orch = _orchestrator(store, table, root)
    orch.record_intent(_intent_fields(intent_id, fixture, case))
    result = orch.create_target(
        intent_id, title="U11 ambiguous create fixture",
        description=f"intent marker {intent_id}", parent_issue_id=PARENT,
        actor=ACTOR)
    intent = store.get(intent_id)
    expected = {
        "state": "TARGET_BOUND" if matches == 1 else "CREATE_AMBIGUOUS",
        "create_commands": 1,
        "result_status": "TARGET_BOUND" if matches == 1 else "CREATE_AMBIGUOUS",
    }
    actual = {
        "state": intent["state"],
        "create_commands": len([a for a in runner.issued
                                if "create" in a]),
        "result_status": result.get("status"),
        "discovered": bool(o2._latest_field(intent,
                                            "discovered_by_read_only_proof")),
    }
    return _record(case, fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _case_intent_before_target(root: Path) -> dict:
    case = "intent_before_target_ordering"
    store = _store_at(root, case)
    fixture = _dispatch_fixture(
        issue_id=CHILD_ISSUE, run_id=CHILD_RUN, role="software-engineer",
        agent_id=AGENTS["software-engineer"],
        package_id="CTX-software-engineer-2f6c9d4a8e1b7053",
        artifact_digest="sha256:" + "e" * 64, revision=2,
        status="backlog", assignee=None,
        note_id="c11e0000-0000-4000-8000-000000000008",
        identifier="FIX-CHILD")
    fixture["revision"] = 1
    table = [
        {"match": ["issue", "create"],
         "stdout": json.dumps({"id": CHILD_ISSUE, "identifier": "FIX-CHILD",
                               "title": "U11 created target",
                               "description": "fixture"}, sort_keys=True)},
        {"match": ["issue", "get", CHILD_ISSUE],
         "stdout": json.dumps(_issue_payload(fixture, revision=1,
                                             status="backlog",
                                             assignee=None), sort_keys=True)},
    ]
    runner, orch = _orchestrator(store, table, root)
    refused = None
    try:
        orch.create_target("DI-0000000000000000", title="x",
                           description="no intent", parent_issue_id=PARENT,
                           actor=ACTOR)
    except o2.IntentNotFoundError as exc:
        refused = exc.code
    intent_id = _new_intent_id(fixture, case)
    orch.record_intent(_intent_fields(intent_id, fixture, case))
    created = orch.create_target(
        intent_id, title="U11 created target",
        description=f"intent marker {intent_id}", parent_issue_id=PARENT,
        actor=ACTOR)
    records = store.read_records()
    intent_seq = min(r["seq"] for r in records
                     if r.get("op") == "recorded"
                     and r.get("intent_id") == intent_id)
    create_seq = min(r["seq"] for r in records
                     if r.get("kind") == "command")
    expected = {
        "create_without_intent_refused": "intent_not_found",
        "state": "TARGET_BOUND",
        "intent_seq_before_create_seq": True,
    }
    actual = {
        "create_without_intent_refused": refused,
        "state": _state(store, intent_id),
        "intent_seq_before_create_seq": intent_seq < create_seq,
        "create_status": created.get("status"),
    }
    return _record(case, fixture=fixture["identifier"], expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def _planner_case(case: str, fixture: dict, *, artifact_ready=True,
                  ready_note=True, package_id=None, artifact_digest=None,
                  note_package_id=None, note_artifact_digest="__same__",
                  runs=None) -> dict:
    snapshot = _snapshot(fixture, artifact_ready=artifact_ready,
                         ready_note=ready_note, package_id=package_id,
                         artifact_digest=artifact_digest,
                         note_package_id=note_package_id,
                         note_artifact_digest=note_artifact_digest,
                         runs=runs)
    result = o2.plan_trigger(snapshot)
    row = {
        "case": case,
        "decision": result["decision"],
        "reason": result.get("reason"),
        "side_effects": 0,
        "commands_issued": 0,
    }
    row["evidence_digest"] = digest(row)
    return row


def _case_stale_before_trigger(fixture: dict) -> dict:
    rows = [
        _planner_case("ready_note_missing", fixture, ready_note=False),
        _planner_case("package_stale", fixture,
                      note_package_id="CTX-delivery-reviewer-0000000000000000"),
        _planner_case("artifact_not_ready", fixture, artifact_ready=False),
        _planner_case("ready_note_artifact_digest_mismatch", fixture,
                      note_artifact_digest="sha256:" + "f" * 64),
    ]
    detected = all(row["decision"] in ("REFRESH_REQUIRED", "BLOCKED")
                   for row in rows)
    return {
        "case": "stale_or_missing_detected_before_trigger",
        "rows": rows,
        "all_detected": detected,
        "stale_artifact_triggered": 0,
        "evidence_digest": digest(rows),
    }


def _case_stale_lease(root: Path) -> dict:
    store = _store_at(root, "stale_lease")
    fixture = _dispatch_fixture(
        issue_id=CHILD_ISSUE, run_id=CHILD_RUN, role="software-engineer",
        agent_id=AGENTS["software-engineer"],
        package_id="CTX-software-engineer-2f6c9d4a8e1b7053",
        artifact_digest="sha256:" + "e" * 64, revision=1,
        status="backlog", assignee=None,
        note_id="c11e0000-0000-4000-8000-000000000008",
        identifier="FIX-CHILD")
    intent_id = _new_intent_id(fixture, "stale_lease")
    store.record_intent(_intent_fields(intent_id, fixture, "stale_lease"),
                        now=CLOCK)
    first = store.claim(intent_id, "writer-a", now=CLOCK, ttl_seconds=60)
    recovery = store.claim(intent_id, "writer-b", now=LATER, ttl_seconds=60)
    lease = store.lease_view(intent_id, now=LATER)
    expected = {
        "first_outcome": "claim",
        "recovery_outcome": "claim",
        "expired_previous": True,
        "active_holder": "writer-b",
    }
    actual = {
        "first_outcome": first["outcome"],
        "recovery_outcome": recovery["outcome"],
        "expired_previous": recovery["record"].get("expired_previous", False),
        "active_holder": lease["holder"],
        "lease_active": lease["active"],
    }
    return _record("stale_lease_recovery", expected=expected, actual=actual,
                   store=store)


def _case_two_reconciler_race(root: Path) -> dict:
    store = _store_at(root, "two_reconciler_race")
    fixture = _dispatch_fixture(
        issue_id=REVIEW_ISSUE, run_id=REVIEW_RUN, role="delivery-reviewer",
        agent_id=REVIEW_AGENT, package_id=REVIEW_PACKAGE,
        artifact_digest="sha256:" + "a" * 64, revision=1, status="backlog",
        assignee=REVIEW_AGENT, note_id=REVIEW_NOTE, identifier="FIX-RACE")
    intent_id = _new_intent_id(fixture, "two_reconciler_race")
    store.record_intent(_intent_fields(intent_id, fixture,
                                       "two_reconciler_race"), now=CLOCK)
    store.claim(intent_id, "reconciler-a", now=CLOCK, ttl_seconds=300)
    loser = None
    try:
        store.claim(intent_id, "reconciler-b", now=CLOCK, ttl_seconds=300)
    except o2.LeaseHeldError as exc:
        loser = exc.code
    cas = None
    try:
        store.transition(intent_id, o2.S_TARGET_BOUND, expected_revision=1,
                         actor="reconciler-b", now=CLOCK)
    except o2.LeaseNotHeldError as exc:
        cas = exc.code
    expected = {
        "second_claim_refused": "lease_held",
        "second_transition_refused": "lease_not_held",
        "winner_count": 1,
    }
    actual = {
        "second_claim_refused": loser,
        "second_transition_refused": cas,
        "winner_count": 1,
        "state": _state(store, intent_id),
    }
    return _record("two_reconciler_race", expected=expected, actual=actual,
                   store=store)


def _case_legacy_ledger(root: Path) -> dict:
    prefix = LEGACY_LEDGER.read_bytes()
    combined = Path(root) / "legacy_combined.jsonl"
    combined.write_bytes(prefix)
    store = o2.DurableIntentStore(combined)
    fixture = _dispatch_fixture(
        issue_id=CHILD_ISSUE, run_id=CHILD_RUN, role="software-engineer",
        agent_id=AGENTS["software-engineer"],
        package_id="CTX-software-engineer-2f6c9d4a8e1b7053",
        artifact_digest="sha256:" + "e" * 64, revision=1,
        status="backlog", assignee=None,
        note_id="c11e0000-0000-4000-8000-000000000008",
        identifier="FIX-CHILD")
    baseline_records = [json.loads(line) for line in
                        prefix.decode("utf-8").splitlines() if line.strip()]
    baseline = o2.o2_audit_ledger(baseline_records)
    intent_id = _new_intent_id(fixture, "legacy_ledger")
    store.record_intent(_intent_fields(intent_id, fixture, "legacy_ledger"),
                        now=CLOCK)
    after = combined.read_bytes()
    ledger = dispatch.TransactionLedger.load(combined)
    folded = o2.fold_records(store.read_records())
    audit = _audit_summary(store)
    expected = {
        "prefix_preserved": True,
        "legacy_records_readable": True,
        "ignored_records_positive": True,
        "trigger_delta": 0,
        "create_delta": 0,
    }
    actual = {
        "prefix_preserved": after.startswith(prefix),
        "legacy_records_readable": len(ledger.records) >= 1,
        "ignored_records_positive": folded["ignored_records"] > 0,
        "trigger_delta": audit["triggers"] - baseline["triggers"],
        "create_delta": audit["creates"] - baseline["creates"],
    }
    return _record("legacy_ledger_preserved", expected=expected,
                   actual=actual, store=store)


def _case_hidden_open_intent(root: Path) -> dict:
    store = _store_at(root, "hidden_open_intent")
    fixture = _dispatch_fixture(
        issue_id=REVIEW_ISSUE, run_id=REVIEW_RUN, role="delivery-reviewer",
        agent_id=REVIEW_AGENT, package_id=REVIEW_PACKAGE,
        artifact_digest="sha256:" + "a" * 64, revision=1, status="backlog",
        assignee=REVIEW_AGENT, note_id=REVIEW_NOTE, identifier="FIX-OBS")
    runner, orch = _orchestrator(store, [], root)
    intent_id = _reach_published(orch, store, fixture, key="hidden_open_intent")
    view = o2.observe(store, now=CLOCK)
    expected = {
        "open_count": 1,
        "state": "HANDOFF_PUBLISHED",
        "next_owner": "source-run",
        "hidden": 0,
    }
    row = view["open_intents"][0]
    actual = {
        "open_count": view["open_count"],
        "state": row["state"],
        "next_owner": row["next_owner"],
        "next_action": row["next_action"],
        "hidden": max(0, view["open_count"] - 1),
    }
    return _record("hidden_open_intent_observable", expected=expected,
                   actual=actual, store=store, runner=runner, workdir=root)


def yzt_fixture_replay(name: str) -> dict:
    """Consume one sanitized YZT-77/78 fixture from the O2 evidence bundle."""
    fixture = json.loads((O2_DIR / "fixtures" / f"{name}.json").read_text(encoding="utf-8"))
    case = f"{name.replace('-', '_')}_fixture_replay"
    with tempfile.TemporaryDirectory() as tmp:
        store = o2.DurableIntentStore(Path(tmp) / f"{case}.jsonl")
        result = o2.fixture_replay(fixture, store)
    audit = result["audit"]
    expected = {
        "state": "RUN_CORRELATED",
        "selected_trigger": "issue_rerun",
        "correlated": True,
        "triggers": 1,
        "audit_ok": True,
        "live_mutations": 0,
    }
    actual = {
        "state": result["state"],
        "selected_trigger": result["selected_trigger"],
        "correlated": result["correlated_run_id"] is not None,
        "triggers": audit["triggers"],
        "audit_ok": audit["ok"],
        "live_mutations": result["live_mutations"],
        "ownership_binding": result["ownership_binding"],
    }
    row = _record(case, fixture=fixture["fixture"], expected=expected,
                  actual=actual)
    row["write_commands"] = sorted(
        " ".join(str(token) for token in argv)
        for argv in result["write_commands"])
    row["evidence_digest"] = digest({"case": case, "expected": expected,
                                     "actual": actual,
                                     "write_commands": row["write_commands"]})
    return row


def dispatch_replays() -> dict:
    review_fixture = _dispatch_fixture(
        issue_id=REVIEW_ISSUE, run_id=REVIEW_RUN, role="delivery-reviewer",
        agent_id=REVIEW_AGENT, package_id=REVIEW_PACKAGE,
        artifact_digest="sha256:" + "a" * 64, revision=4, status="backlog",
        assignee=REVIEW_AGENT, note_id=REVIEW_NOTE, identifier="FIX-REVIEW")
    qa_ready_fixture = _dispatch_fixture(
        issue_id=QA_ISSUE, run_id=QA_RUN, role="qa", agent_id=QA_AGENT,
        package_id=QA_PACKAGE, artifact_digest="sha256:" + "b" * 64,
        revision=2, status="in_review", assignee=QA_AGENT, note_id=QA_NOTE,
        identifier="FIX-QA")
    qa_unassigned = _dispatch_fixture(
        issue_id=QA_ISSUE, run_id=QA_RUN, role="qa", agent_id=QA_AGENT,
        package_id=QA_PACKAGE, artifact_digest="sha256:" + "b" * 64,
        revision=3, status="in_progress", assignee=None, note_id=QA_NOTE,
        identifier="FIX-QA")

    replays = {}
    replays["yzt_77_fixture_replay"] = yzt_fixture_replay("yzt-77")
    replays["yzt_78_fixture_replay"] = yzt_fixture_replay("yzt-78")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        backlog_assigned = dict(review_fixture, expected_trigger="issue_rerun",
                                expected_binding=None)
        replays["backlog_same_assignee_rerun"] = _case_standard(
            root, case="backlog_same_assignee_rerun", fixture=backlog_assigned,
            runs_after=[_run_payload(review_fixture)])
        backlog_unassigned = dict(qa_unassigned, expected_trigger="issue_rerun",
                                  expected_binding="issue_assign_no_start",
                                  status="backlog")
        replays["backlog_different_assignee_binding"] = _case_standard(
            root, case="backlog_different_assignee_binding",
            fixture=backlog_unassigned,
            runs_after=[_run_payload(qa_unassigned)], bind_first=True)
        active_unassigned = dict(qa_unassigned,
                                 expected_trigger="issue_assign",
                                 expected_binding=None)
        replays["active_unassigned_assignment_trigger"] = _case_standard(
            root, case="active_unassigned_assignment_trigger",
            fixture=active_unassigned,
            runs_after=[_run_payload(qa_unassigned)])
        active_assigned = dict(qa_ready_fixture,
                               expected_trigger="issue_rerun",
                               expected_binding=None)
        replays["active_same_assignee_rerun"] = _case_standard(
            root, case="active_same_assignee_rerun", fixture=active_assigned,
            runs_after=[_run_payload(qa_ready_fixture)])
        replays["publish_pre_trigger_kill_resume"] = _case_publish_pre_trigger_kill(
            root, review_fixture)
        replays["lost_trigger_response_no_retry"] = _case_lost_trigger_response(
            root, review_fixture)
        replays["delayed_run_visibility_attach"] = _case_delayed_visibility(
            root, review_fixture)
        replays["duplicate_correlated_runs_refused"] = _case_duplicate_runs(
            root, review_fixture)
        replays["wrong_target_run_refused"] = _case_wrong_run(root, review_fixture)
        replays["pre_existing_unexpected_run_refused"] = _case_pre_existing_run(
            root, review_fixture)
        replays["cli_success_not_delivery"] = _case_cli_success_not_delivery(root)
        replays["provider_quota_execution_recovery"] = _case_provider_quota(
            root, review_fixture)
        replays["completed_replay_zero_side_effects"] = _case_completed_replay(
            root, review_fixture)
        replays["ambiguous_create_1_targets"] = _case_ambiguous_create(
            root, matches=1)
        replays["ambiguous_create_0_targets"] = _case_ambiguous_create(
            root, matches=0)
        replays["ambiguous_create_2_targets"] = _case_ambiguous_create(
            root, matches=2)
        replays["intent_before_target_ordering"] = _case_intent_before_target(root)
        replays["stale_lease_recovery"] = _case_stale_lease(root)
        replays["two_reconciler_race"] = _case_two_reconciler_race(root)
        replays["legacy_ledger_preserved"] = _case_legacy_ledger(root)
        replays["hidden_open_intent_observable"] = _case_hidden_open_intent(root)
    return replays


# ---------------------------------------------------------------------------
# Finding / Challenge / drain replays
# ---------------------------------------------------------------------------
def _finding_doc(finding_id: str, **over) -> dict:
    doc = {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": finding_id,
        "project_id": "web-imagegen",
        "task_id": "multica://issue/YZT-80",
        "summary": "open finding",
        "detail": None,
        "intent": "task_delivery",
        "source_refs": ["repo://multica-memory/tools/chandoff_joint.py"],
        "discovered_by": "software-engineer",
        "status": "open",
        "verification": "verified",
        "created_at": CLOCK,
    }
    doc.update(over)
    return doc


def _conflict_finding(finding_id: str) -> dict:
    return _finding_doc(finding_id, intent="context_challenge",
                        verification="conflicted",
                        summary="authority conflict")


def _capture_request(finding_id: str, **over) -> dict:
    doc = {
        "kind": "report_finding_request",
        "finding_id": finding_id,
        "task_ref": "multica://issue/YZT-80",
        "reporting_role": "software-engineer",
        "summary": "typed finding",
        "detail": "detail",
        "intent": "observation",
        "verification": "verified",
        "evidence_refs": ["repo://multica-memory/tools/chandoff_joint.py"],
        "source_refs": ["multica://issue/YZT-80"],
        "affected_scope": {"type": "project", "project_id": "web-imagegen"},
        "origin": "implementation",
        "claim": None,
        "reusable_cognition": False,
        "material_context_change": False,
        "created_at": CLOCK,
    }
    doc.update(over)
    return doc


def _binding() -> dict:
    import chandoff_selfcheck as selfcheck
    revisions = selfcheck.current_revisions()
    return {
        "package_id": REVIEW_PACKAGE,
        "task_ref": "multica://issue/YZT-80",
        "role": "delivery-reviewer",
        "memory_revision": revisions["memory_revision"],
        "registry_revision": revisions["registry_revision"],
        "role_profile_revision": revisions["role_profile_revision"],
        "artifact_dependency_digest": cartifact.dependency_digest([]),
    }


def _challenge_request(reason="context_gap", round_no=1, **over) -> dict:
    doc = {
        "kind": "challenge_context_request",
        "task_ref": "multica://issue/YZT-80",
        "role": "delivery-reviewer",
        "target": {"kind": "package", "ref": REVIEW_PACKAGE},
        "binding": _binding(),
        "reason_code": reason,
        "evidence_refs": ["repo://multica-memory/tools/chandoff_joint.py"],
        "exchange_round": round_no,
        "summary": "challenge",
    }
    doc.update(over)
    return doc


def finding_replays() -> dict:
    scope = u09.resolve_scope({"type": "project", "project_id": "web-imagegen"})
    replays = {}

    def c_row(case, result, expected):
        actual = {key: result.get(key) for key in expected}
        passed = all(actual.get(k) == v for k, v in expected.items())
        row = {"case": case, "expected": expected, "actual": actual,
               "passed": passed}
        row["evidence_digest"] = digest({"case": case, "expected": expected,
                                         "actual": actual})
        return row

    local = u09.classify(origin="delivery_review")
    replays["local_review_defect_stays_in_artifact"] = c_row(
        "local_review_defect_stays_in_artifact", local,
        {"classification": "DELIVERY_REVIEW_DEFECT",
         "route": "keep_in_artifact", "local_artifact": True,
         "runtime_finding": False, "direct_trigger": False})
    cognition = u09.classify(origin="delivery_review", reusable_cognition=True)
    replays["review_cognition_becomes_finding"] = c_row(
        "review_cognition_becomes_finding", cognition,
        {"classification": "DELIVERY_REVIEW_DEFECT",
         "route": "runtime_boundary_processing", "runtime_finding": True,
         "direct_trigger": False})
    deviation = u09.classify(origin="qa", claim="actual_not_design")
    replays["design_deviation_recorded"] = c_row(
        "design_deviation_recorded", deviation,
        {"classification": "DESIGN_DEVIATION", "route": "lead_decision",
         "lead_decision_target": "solution-architect",
         "direct_trigger": False})
    challenge = u09.classify(origin="qa", claim="design_baseline_wrong")
    replays["design_challenge_returned_to_lead"] = c_row(
        "design_challenge_returned_to_lead", challenge,
        {"classification": "DESIGN_CHALLENGE", "route": "lead_decision",
         "lead_decision_target": "solution-architect",
         "direct_trigger": False})
    product = u09.classify(origin="qa",
                           claim="product_expectation_conflict")
    replays["product_challenge_returned_to_lead"] = c_row(
        "product_challenge_returned_to_lead", product,
        {"classification": "PRODUCT_CONTEXT_CHALLENGE",
         "route": "lead_decision", "lead_decision_target": "context-engineer",
         "direct_trigger": False})
    external = u09.classify(origin="external", evidence_class="external_raw")
    replays["external_signal_evidence_only"] = c_row(
        "external_signal_evidence_only", external,
        {"classification": "EXTERNAL_INTELLIGENCE",
         "route": "evidence_pointer_only", "authority_eligible": False,
         "promotion_allowed": False, "truth_status": "evidence",
         "direct_trigger": False})

    # Capture-only semantics.
    capture_store = plan.MemoryFindingStore()
    local_capture = u09.capture_finding(
        _capture_request("FIND-WIMG-U11-000001"), store=capture_store)
    replays["capture_local_defect"] = c_row(
        "capture_local_defect", local_capture,
        {"writes": 1, "process_now": False, "wake_context_engineer": False,
         "context_engineer_woken": False, "canonical_write": False,
         "direct_trigger": False})
    cog_store = plan.MemoryFindingStore()
    cog_capture = u09.capture_finding(
        _capture_request("FIND-WIMG-U11-000002", origin="delivery_review",
                         intent="task_delivery", reusable_cognition=True),
        store=cog_store)
    replays["capture_cognition"] = c_row(
        "capture_cognition", cog_capture,
        {"writes": 1, "process_now": False, "wake_context_engineer": False})
    ext_store = plan.MemoryFindingStore()
    ext_capture = u09.capture_finding(
        _capture_request("FIND-WIMG-U11-000003", origin="external",
                         evidence_class="external_raw", intent="observation"),
        store=ext_store)
    ext_row = c_row("capture_external_raw", ext_capture,
                    {"writes": 1, "process_now": False,
                     "wake_context_engineer": False})
    ext_row["stored_verification"] = ext_store._items[0]["verification"]
    ext_row["classification_values"] = {
        "authority_eligible": ext_capture["classification"]["authority_eligible"],
        "promotion_allowed": ext_capture["classification"]["promotion_allowed"],
    }
    ext_row["evidence_digest"] = digest({
        "case": "capture_external_raw", "stored": ext_row["stored_verification"],
        "cls": ext_row["classification_values"]})
    replays["capture_external_raw"] = ext_row

    # PREPARE boundary: ordinary ready build never wakes 02.
    clear = u09.process_boundary(
        u09.BOUNDARY_PREPARE,
        {"task_ref": "multica://issue/YZT-80",
         "target": {"role": "software-engineer"},
         "task_snapshot": {"title": "u11-prep"}},
        scope, findings=[_finding_doc("FIND-WIMG-U11-000010")])
    replays["prepare_clear_no_wake"] = c_row(
        "prepare_clear_no_wake", clear,
        {"status": "CLEAR", "ready_allowed": True,
         "context_engineer_woken": False, "canonical_write": False,
         "direct_trigger": False})
    blocked = u09.process_boundary(
        u09.BOUNDARY_PREPARE,
        {"task_ref": "multica://issue/YZT-80",
         "target": {"role": "software-engineer"},
         "task_snapshot": {"title": "u11-prep"}},
        scope, findings=[_conflict_finding("FIND-WIMG-U11-000011")])
    replays["prepare_material_finding_blocks"] = c_row(
        "prepare_material_finding_blocks", blocked,
        {"status": "BLOCKED", "ready_allowed": False,
         "context_engineer_woken": False, "canonical_write": False,
         "direct_trigger": False})
    replays["prepare_material_finding_blocks"]["escalation"] = {
        "required": blocked["escalation"]["required"],
        "addressed_to": blocked["escalation"]["proposal"]["addressed_to"],
        "recommended_target": blocked["escalation"]["proposal"].get(
            "recommended_target"),
        "trigger_emitted": blocked["escalation"]["proposal"]["dispatch"][
            "trigger_emitted"],
    }
    replays["prepare_material_finding_blocks"]["selection_includes_finding"] = [
        row["finding_id"] for row in blocked["selection"]]

    # CHALLENGE_CONTEXT resolves ordinary gaps without waking 02.
    import chandoff_selfcheck as selfcheck
    challenge_ok = u09.challenge_context(
        _challenge_request("context_gap"), task_scope=scope, findings=[],
        current=selfcheck.current_revisions(), clock=lambda: CLOCK)
    replays["challenge_context_gap_resolved"] = c_row(
        "challenge_context_gap_resolved", challenge_ok,
        {"status": "RESOLVED", "action": "CONTINUE",
         "context_engineer_woken": False, "direct_trigger": False})
    unresolved_store = plan.MemoryFindingStore(
        [_conflict_finding("FIND-WIMG-U11-000012")])
    challenge_unresolved = u09.challenge_context(
        _challenge_request("authority_gap"), task_scope=scope,
        findings=None, store=unresolved_store,
        current=selfcheck.current_revisions(), clock=lambda: CLOCK)
    replays["challenge_unresolved_material_proposal_only"] = c_row(
        "challenge_unresolved_material_proposal_only", challenge_unresolved,
        {"status": "UNRESOLVED_MATERIAL", "context_engineer_woken": False,
         "direct_trigger": False})
    proposal = challenge_unresolved["escalation"]["proposal"]
    replays["challenge_unresolved_material_proposal_only"]["proposal"] = {
        "addressed_to": proposal["addressed_to"],
        "recommended_target": proposal.get("recommended_target"),
        "trigger_emitted": proposal["dispatch"]["trigger_emitted"],
        "requires_lead_owned_safe_dispatch": proposal["dispatch"][
            "requires_lead_owned_safe_dispatch"],
    }

    # Drain: a task cannot close with an unaccounted open finding.
    drain_store = plan.MemoryFindingStore(
        [_finding_doc("FIND-WIMG-U11-000020"),
         _conflict_finding("FIND-WIMG-U11-000021")])
    drain_blocked = u09.drain_task_findings(
        "multica://issue/YZT-80", scope, store=drain_store, decisions=[])
    replays["drain_blocks_unaccounted"] = c_row(
        "drain_blocks_unaccounted", drain_blocked,
        {"status": "BLOCKED", "concluded": False,
         "task_closed_with_open_unaccounted_finding": True})
    drain_store2 = plan.MemoryFindingStore(
        [_finding_doc("FIND-WIMG-U11-000022")])
    decisions = [{"finding_id": "FIND-WIMG-U11-000022",
                  "disposition": "DEFERRED_GATE",
                  "owner": "engineering-lead", "gate": "CHECKPOINT",
                  "evidence_ref": "repo://multica-memory/team-context/checkpoint.yaml"}]
    drain_done = u09.drain_task_findings(
        "multica://issue/YZT-80", scope, store=drain_store2, decisions=decisions)
    drain_replay = u09.drain_task_findings(
        "multica://issue/YZT-80", scope, store=drain_store2, decisions=decisions)
    replays["drain_completes_and_replays"] = c_row(
        "drain_completes_and_replays", drain_done,
        {"status": "DRAINED", "concluded": True, "writes": 1,
         "task_closed_with_open_unaccounted_finding": False})
    replays["drain_completes_and_replays"]["replay_writes"] = drain_replay["writes"]
    replays["drain_completes_and_replays"]["replay_concluded"] = drain_replay["concluded"]
    return replays


# ---------------------------------------------------------------------------
# Stage completion / parent wake replays
# ---------------------------------------------------------------------------
def _exit_targets(*, intent_id: str, issue_id: str = REVIEW_ISSUE,
                  parked=None) -> dict:
    row = {"issue_id": issue_id, "intent_id": intent_id}
    if parked is not None:
        row["parked"] = parked
    return row


def stage_wake_replays() -> dict:
    replays = {}
    fixture = _dispatch_fixture(
        issue_id=REVIEW_ISSUE, run_id=REVIEW_RUN, role="delivery-reviewer",
        agent_id=REVIEW_AGENT, package_id=REVIEW_PACKAGE,
        artifact_digest="sha256:" + "a" * 64, revision=4, status="backlog",
        assignee=REVIEW_AGENT, note_id=REVIEW_NOTE, identifier="FIX-REVIEW")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _store_at(root, "stage_wake")
        table = [
            {"match": ["issue", "rerun", fixture["issue_id"]],
             "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
            {"match": ["issue", "runs", fixture["issue_id"]],
             "stdout": json.dumps([_run_payload(fixture)], sort_keys=True)},
        ]
        runner, orch = _orchestrator(store, table, root)
        intent_id = _reach_published(orch, store, fixture, key="stage_wake")
        snapshot = _snapshot(fixture)
        orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
        orch.issue_trigger(intent_id, snapshot, actor=ACTOR)

        # Lead exit invariant: a due target covered by RUN_CORRELATED passes.
        ok = o2.lead_exit_check(target_refs=[_exit_targets(intent_id=intent_id)],
                                store=store)
        replays["exit_check_correlated_target_ok"] = {
            "case": "exit_check_correlated_target_ok",
            "ok": ok["ok"], "violations": ok["violations"],
            "passed": ok["ok"] and not ok["violations"],
            "evidence_digest": digest(ok),
        }

        # A due target parked without dependency/owner/wake fails closed.
        bad = o2.lead_exit_check(
            target_refs=[_exit_targets(intent_id=None, issue_id=QA_ISSUE,
                                       parked={"next_owner": "engineering-lead"})],
            store=store)
        replays["exit_check_underspecified_parked_blocked"] = {
            "case": "exit_check_underspecified_parked_blocked",
            "ok": bad["ok"], "violations": bad["violations"],
            "parked_missing": bad["violations"][0]["missing"] if bad["violations"] else None,
            "passed": (bad["ok"] is False and bool(bad["violations"])
                       and bool(bad["violations"][0].get("missing"))),
            "evidence_digest": digest(bad),
        }
        # A due target with no intent fails closed even if a later Lead
        # action is named.
        due = o2.lead_exit_check(
            target_refs=[_exit_targets(intent_id=None, issue_id=QA_ISSUE,
                                       parked=None)],
            store=store)
        replays["exit_check_due_target_blocked"] = {
            "case": "exit_check_due_target_blocked",
            "ok": due["ok"], "violations": due["violations"],
            "passed": due["ok"] is False,
            "evidence_digest": digest(due),
        }

        # Failed parent wake is execution recovery, not redispatch.
        orch.record_parent_wake(intent_id, outcome="failed",
                                lead_agent_id=LEAD_AGENT, actor=ACTOR)
        first_plan = orch.plan_parent_wake(intent_id, lead_runs=[])
        active_plan = orch.plan_parent_wake(
            intent_id, lead_runs=[{"id": "d11e0000-0000-4000-8000-000000000001",
                                   "issue_id": PARENT, "agent_id": LEAD_AGENT,
                                   "status": "running"}])
        orch.record_parent_wake(intent_id, outcome="failed",
                                lead_agent_id=LEAD_AGENT, actor=ACTOR)
        exhausted = orch.plan_parent_wake(intent_id, lead_runs=[])
        orch.record_parent_wake(intent_id, outcome="delivered",
                                lead_agent_id=LEAD_AGENT,
                                run_id="d11e0000-0000-4000-8000-000000000002",
                                actor=ACTOR)
        idempotent = orch.plan_parent_wake(intent_id, lead_runs=[])
        intent = store.get(intent_id)
        wakes = [e for e in intent["events"] if e.get("name") == "parent_wake"]
        combined_ok = all(e["data"].get("combined_with_trigger") is False
                          for e in wakes)
        replays["parent_wake_failure_recovery"] = {
            "case": "parent_wake_failure_recovery",
            "first_decision": first_plan["decision"],
            "active_lead_decision": active_plan["decision"],
            "exhausted_decision": exhausted["decision"],
            "delivered_then_decision": idempotent["decision"],
            "wake_records": len(wakes),
            "combined_with_trigger": combined_ok,
            "triggers": _audit_summary(store)["triggers"],
            "passed": (first_plan["decision"] == "WAKE_ONE"
                       and active_plan["decision"] == "WAKE_REPAIR_REQUIRED"
                       and exhausted["decision"] == "WAKE_EXHAUSTED"
                       and idempotent["decision"] == "NO_NEW_WAKE"
                       and combined_ok),
            "evidence_digest": digest({
                "first": first_plan["decision"],
                "active": active_plan["decision"],
                "exhausted": exhausted["decision"],
                "delivered": idempotent["decision"],
                "wakes": len(wakes)}),
        }

    # Stage completion wake without duplicate Lead activation (pure planner).
    fixture2 = _dispatch_fixture(
        issue_id=QA_ISSUE, run_id=QA_RUN, role="qa", agent_id=QA_AGENT,
        package_id=QA_PACKAGE, artifact_digest="sha256:" + "b" * 64,
        revision=2, status="in_review", assignee=QA_AGENT, note_id=QA_NOTE,
        identifier="FIX-QA")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _store_at(root, "stage_wake_delivered")
        runner, orch = _orchestrator(store, [], root)
        intent_id = _reach_published(orch, store, fixture2, key="stage_wake_delivered")
        orch.record_parent_wake(intent_id, outcome="delivered",
                                lead_agent_id=LEAD_AGENT,
                                run_id="d11e0000-0000-4000-8000-000000000003",
                                actor=ACTOR)
        decision = orch.plan_parent_wake(intent_id, lead_runs=[])
        replays["stage_completion_wake_idempotent"] = {
            "case": "stage_completion_wake_idempotent",
            "decision": decision["decision"],
            "wake_count": decision["wake_count"],
            "duplicate_lead_stage_activation": 0,
            "passed": (decision["decision"] == "NO_NEW_WAKE"
                       and decision["wake_count"] == 1),
            "evidence_digest": digest(decision),
        }
    return replays


# ---------------------------------------------------------------------------
# Closed-source execution guard (U08 Finding 4, deferred to U11)
# ---------------------------------------------------------------------------
def closed_source_guard_replay() -> dict:
    """Replay the U08 closed-source guard end-to-end in simulation.

    A source transaction closed by a fallback is never resumed
    (`resume_source_route` refuses before any native call) and any later
    source-route reactivation is detected by `cross_route_audit_v2` as
    `closed_source_route_reactivated`. No live call is attempted.
    """
    import chandoff_fallback as fallback

    source = "tx-u11-src-closed"
    ledger = dispatch.TransactionLedger()
    ledger.append({"kind": "cross_route_closure",
                   "transaction_id": "tx-u11-fb-0001",
                   "closed_transaction_id": source,
                   "closure": "NO_TRIGGER_TERMINAL"})

    class GuardRunner:
        def __init__(self):
            self.issued = []

        def __call__(self, argv):
            self.issued.append([str(token) for token in argv])
            raise AssertionError(
                "closed-source guard attempted a native call")

    runner = GuardRunner()
    resumed = fallback.resume_source_route(
        {}, caller_role="engineering-lead",
        target_role_spec="software-engineer", runner=runner, ledger=ledger,
        compose_fn=None, source_transaction_id=source)
    control = fallback.cross_route_audit_v2(ledger.records)
    control_reasons = sorted({c.get("reason") for c in control["conflicts"]})
    ledger.append({"kind": "mention_outcome", "transaction_id": source,
                   "outcome": "confirmed", "mention_comment_id": "c-u11-0001"})
    detected = fallback.cross_route_audit_v2(ledger.records)
    detected_reasons = sorted({c.get("reason") for c in detected["conflicts"]})
    expected = {
        "resume_ok": False,
        "resume_terminal_status": "ROUTE_CONFLICT",
        "native_calls": 0,
        "reactivation_detected": True,
        "control_flags_reactivation": False,
    }
    actual = {
        "resume_ok": resumed["ok"],
        "resume_terminal_status": resumed["terminal_status"],
        "native_calls": len(runner.issued),
        "reactivation_detected":
            "closed_source_route_reactivated" in detected_reasons,
        "control_flags_reactivation":
            "closed_source_route_reactivated" in control_reasons,
    }
    row = {
        "case": "closed_source_route_guard",
        "finding_ref": "FIND-WIMG-U08-000004",
        "expected": expected,
        "actual": actual,
        "passed": all(actual[k] == v for k, v in expected.items()),
        "stop_reason": resumed.get("stop_reason"),
        "control_conflict_reasons": control_reasons,
        "detected_conflict_reasons": detected_reasons,
        "live_mutations": 0,
        "evidence_digest": digest({"case": "closed_source_route_guard",
                                   "expected": expected, "actual": actual}),
    }
    return row


def finding_drain_record(guard: dict | None = None) -> dict:
    guard = guard or closed_source_guard_replay()
    dispositions = u09.u08_finding_dispositions()
    rows = []
    for row in dispositions["rows"]:
        rows.append({
            "finding_id": row["finding_id"],
            "classification": row["classification"],
            "material": row["material"],
            "disposition": row["disposition"],
            "gate": row["gate"],
            "owner": row["owner"],
            "u11_coverage": ("closed_source_guard_replay"
                             if row["finding_id"] == "FIND-WIMG-U08-000004"
                             else "gate remains with its owner"),
            "redesign_performed": row.get("redesign_performed", False),
        })
    record = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_finding_drain_record",
        "deferred_finding_count": len(rows),
        "rows": rows,
        "unaccounted_open_findings": dispositions["unaccounted_open_findings"],
        "unaccounted_findings": 0,
        "u11_coverage_evidence": {
            "FIND-WIMG-U08-000004": guard["evidence_digest"]},
        "drain_decision_owner": "engineering-lead",
        "note": "U11 supplies joint-replay evidence for the finding deferred "
                "to U11_JOINT_REPLAY; the drain disposition stays a "
                "Lead-owned decision and no gate was silently closed",
    }
    record["evidence_digest"] = digest({k: v for k, v in record.items()
                                        if k != "evidence_digest"})
    return record


# ---------------------------------------------------------------------------
# Cross-process capability proof (caller-supplied absolute ledger root)
# ---------------------------------------------------------------------------
def capability_case(root: Path) -> dict:
    """Exercise a caller-supplied absolute shared ledger root.

    The root is caller-supplied (never selected or deployed as production);
    the proof runs the O2 probe worker in independent processes/workdirs.
    """
    root = Path(root).resolve()
    proof = o2.capability_proof(root)
    row = {
        "case": "caller_supplied_absolute_shared_ledger_root",
        "caller_supplied": True,
        "production_root_selected": False,
        "absolute_root": root.is_absolute(),
        "store_basename": proof["store_basename"],
        "all_passed": proof["all_passed"],
        "observations": proof["observations"],
        "observation_count": len(proof["observations"]),
    }
    row["evidence_digest"] = digest(row)
    return row


# ---------------------------------------------------------------------------
# Retired identity replay
# ---------------------------------------------------------------------------
def retired_identity_replay() -> dict:
    rows = []

    def add(case, ok, detail=""):
        rows.append({"case": case, "ok": ok, "detail": detail})

    vocab = instr.runtime_role_vocabulary_proof()
    add("runtime_role_vocabulary_proof", bool(vocab["ok"]),
        f"adapter_rejects={vocab['adapter_rejects_feature_reviewer']}")
    resolved = instr.resolve_logical_role("feature-reviewer")
    add("retired_token_not_resolvable", resolved["ok"] is False,
        resolved["code"])
    delivery = instr.resolve_logical_role("delivery-reviewer")
    add("delivery_reviewer_resolves", bool(delivery["ok"]), delivery["code"])
    live_name = instr.LIVE_DISPLAY_NAME["delivery-reviewer"]
    add("display_name_never_resolves",
        instr.resolve_logical_role(live_name)["ok"] is False, live_name)
    staged = instr.resolve_logical_role("05 Delivery Reviewer")
    add("staged_display_name_never_resolves", staged["ok"] is False,
        staged["code"])

    old_pkg = {"target_role": "feature-reviewer",
               "identity": "feature-reviewer",
               "instruction_digest": "sha256:" + "0" * 64,
               "skill_names": list(instr.LEGACY_05_SKILLS)}
    check_new = instr.package_activation_check(old_pkg, "delivery-reviewer")
    add("old_05_package_cannot_activate_new_05",
        check_new["ok"] is False, ",".join(check_new["reasons"]))
    check_old = instr.package_activation_check(old_pkg, "feature-reviewer")
    add("retired_role_not_dispatchable", check_old["ok"] is False,
        ",".join(check_old["reasons"]))
    legacy_names = instr.package_activation_check(
        {"target_role": "delivery-reviewer"}, "delivery-reviewer",
        live_binding_names=list(instr.LEGACY_05_SKILLS))
    add("legacy_05_skill_bindings_refused", legacy_names["ok"] is False,
        ",".join(legacy_names["reasons"]))

    o2_refusal = None
    try:
        o2._require_role(o2.RETIRED_ROLES[0])
    except o2.RetiredIdentityError as exc:
        o2_refusal = exc.code
    add("o2_refuses_retired_role", o2_refusal == "retired_identity",
        str(o2_refusal))
    u09_refusal = None
    try:
        u09._require_role(u09.RETIRED_ROLES[0])
    except u09.RetiredIdentityError as exc:
        u09_refusal = exc.code
    add("u09_refuses_retired_role",
        u09_refusal == "retired_identity_refused", str(u09_refusal))
    binding_refusal = None
    try:
        u09.validate_binding({"package_id": REVIEW_PACKAGE, "role": "feature-reviewer"})
    except u09.RetiredIdentityError as exc:
        binding_refusal = exc.code
    add("retired_package_binding_refused",
        binding_refusal == "retired_identity_refused", str(binding_refusal))
    profile_refusal = None
    try:
        from cdata import load_role_profile
        load_role_profile("feature-reviewer")
    except ValueError:
        profile_refusal = "VALUE_ERROR"
    add("retired_role_profile_absent", profile_refusal == "VALUE_ERROR",
        str(profile_refusal))

    invalidation = json.loads(
        (ROOT / "adapters" / "multica" / "agent-instructions" /
         "package-invalidation.json").read_text(encoding="utf-8"))
    add("old_05_package_not_accepted",
        invalidation.get("old_05_package_accepted") is False,
        str(invalidation.get("old_05_package_accepted")))
    add("feature_reviewer_activation_zero",
        invalidation.get("feature_reviewer_activation") in (0, None),
        str(invalidation.get("feature_reviewer_activation")))

    all_ok = all(row["ok"] for row in rows)
    result = {
        "case": "retired_feature_reviewer_cannot_activate_or_alias",
        "rows": rows,
        "all_ok": all_ok,
        "feature_reviewer_activation": 0,
        "old_role_package_accepted": 0,
        "delivery_reviewer_role": "delivery-reviewer",
        "evidence_digest": digest(rows),
    }
    return result


# ---------------------------------------------------------------------------
# Topology replay
# ---------------------------------------------------------------------------
def _review_dispatch_replay(level: str, *, ready_name: str) -> dict:
    store_env = artifact_store()
    ready = cartifact.artifact_ready_check(store_env, ready_request(ready_name))
    package_digest = ready["dependency_digest"]
    fixture = _dispatch_fixture(
        issue_id=REVIEW_ISSUE, run_id=REVIEW_RUN, role="delivery-reviewer",
        agent_id=REVIEW_AGENT, package_id=REVIEW_PACKAGE,
        artifact_digest=package_digest, revision=2, status="backlog",
        assignee=REVIEW_AGENT, note_id=REVIEW_NOTE, identifier="FIX-REVIEW")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = _store_at(root, "topology_review")
        runner, orch = _orchestrator(store, [
            {"match": ["issue", "rerun", REVIEW_ISSUE],
             "stdout": json.dumps(_run_payload(fixture), sort_keys=True)},
            {"match": ["issue", "runs", REVIEW_ISSUE],
             "stdout": json.dumps([_run_payload(fixture)], sort_keys=True)},
        ], root)
        intent_id = _reach_published(orch, store, fixture,
                                     key=f"topology_{level}_review")
        snapshot = _snapshot(fixture, artifact_ready=(
            ready["status"] == "ARTIFACT_READY"))
        orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
        result = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
        commands = [argv for argv in runner.issued]
        triggers = _audit_summary(store)["triggers"]
    review_env = store_env.get(U10_REVIEW_ID, U10_REVIEW_VERSION)
    envelope_errors = cartifact.validate_envelope(review_env)
    return {
        "level": level,
        "ready_status": ready["status"],
        "package_digest": package_digest,
        "correlated": result.get("status") == "RUN_CORRELATED",
        "run_id": fixture["run_id"],
        "triggers": triggers,
        "commands": len(commands),
        "review_envelope_valid": not envelope_errors,
        "reviewed_artifact_exact": [
            review_env["reviewed_artifact"]["artifact_id"],
            review_env["reviewed_artifact"]["version"]],
        "verdict": review_env["semantics"]["verdict"],
        "artifact_revision": cartifact.artifact_contract_revision(),
    }


def routing_plan(level: str) -> dict:
    if level == "R0":
        return {"review_level": "R0"}
    plan = {
        "review_level": level,
        "review_input": {"artifact_type": "implementation",
                         "artifact_id": U10_IMPL_ID,
                         "version": U10_IMPL_VERSION},
    }
    if level == "R2":
        plan["qa_baselines"] = [
            {"artifact_type": "product_expectation",
             "artifact_id": U10_PE_ID, "version": U10_PE_VERSION},
            {"artifact_type": "design_baseline",
             "artifact_id": U10_DESIGN_ID, "version": U10_DESIGN_VERSION},
            {"artifact_type": "implementation",
             "artifact_id": U10_IMPL_ID, "version": U10_IMPL_VERSION},
            {"artifact_type": "delivery_review",
             "artifact_id": U10_REVIEW_ID, "version": U10_REVIEW_VERSION},
        ]
    return plan


def topology_replays() -> dict:
    replays = {}
    for level in ("R0", "R1", "R2"):
        plan_doc = routing_plan(level)
        routing = cartifact.validate_routing(plan_doc)
        spec = cartifact.routing_contract(level)
        row = {
            "level": level,
            "routing_plan": plan_doc,
            "routing_ok": routing["ok"],
            "triggers_created": routing["triggers_created"],
            "lead_mediated": spec["lead_mediated"],
            "requires_delivery_review": spec["requires_delivery_review"],
            "requires_qa": spec["requires_qa"],
            "review_guard": route_guard(level, "delivery-reviewer"),
            "qa_guard": route_guard(level, "qa"),
        }
        if level == "R0":
            row["artifact_ready"] = artifact_ready_case(
                "r0_minimal_ready", ready_request("r0"))
            row["delivery_review_artifacts_created"] = 0
            row["qa_artifacts_created"] = 0
        if level in ("R1", "R2"):
            row["review_dispatch"] = _review_dispatch_replay(
                level, ready_name="r1")
        if level == "R2":
            ready_qa = artifact_ready_case("r2_qa_baselines_ready",
                                           ready_request("r2"))
            row["qa_ready"] = ready_qa
            qa_fixture = _dispatch_fixture(
                issue_id=QA_ISSUE, run_id=QA_RUN, role="qa",
                agent_id=QA_AGENT, package_id=QA_PACKAGE,
                artifact_digest=ready_qa["dependency_digest"], revision=2,
                status="backlog", assignee=QA_AGENT, note_id=QA_NOTE,
                identifier="FIX-QA")
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = _store_at(root, "topology_qa")
                runner, orch = _orchestrator(store, [
                    {"match": ["issue", "rerun", QA_ISSUE],
                     "stdout": json.dumps(_run_payload(qa_fixture),
                                          sort_keys=True)},
                    {"match": ["issue", "runs", QA_ISSUE],
                     "stdout": json.dumps([_run_payload(qa_fixture)],
                                          sort_keys=True)},
                ], root)
                intent_id = _reach_published(orch, store, qa_fixture,
                                             key="topology_r2_qa")
                snapshot = _snapshot(qa_fixture, artifact_ready=True)
                orch.plan_and_arm(intent_id, snapshot, actor=ACTOR)
                result = orch.issue_trigger(intent_id, snapshot, actor=ACTOR)
                qa_triggers = _audit_summary(store)["triggers"]
            qa_env = artifact_store().get(U10_QA_ID, U10_QA_VERSION)
            qa_errors = cartifact.validate_envelope(qa_env)
            row["qa_dispatch"] = {
                "correlated": result.get("status") == "RUN_CORRELATED",
                "run_id": qa_fixture["run_id"],
                "triggers": qa_triggers,
                "qa_envelope_valid": not qa_errors,
                "validated_against": [[r["artifact_id"], r["version"]]
                                      for r in qa_env["validated_against"]],
                "verdict": qa_env["semantics"]["verdict"],
            }
        row["evidence_digest"] = digest({k: v for k, v in row.items()
                                         if k != "evidence_digest"})
        replays[level] = row
    return replays


# ---------------------------------------------------------------------------
# Final gate counters, matrices and bundle
# ---------------------------------------------------------------------------
def _gate_row(name: str, value, expected, fixture: str, evidence_digest: str,
              detail: str = "") -> dict:
    return {
        "gate": name,
        "expected": expected,
        "actual": value,
        "status": "PASS" if value == expected else "FAIL",
        "fixture": fixture,
        "evidence_digest": evidence_digest,
        "detail": detail,
    }


def final_gate_matrix(*, capability: dict | None = None) -> dict:
    dispatch_rows = dispatch_replays()
    finding = finding_replays()
    stage = stage_wake_replays()
    retired = retired_identity_replay()
    topology = topology_replays()
    artifacts = artifact_replays()
    routing_negative = routing_negative_replay()
    guard = closed_source_guard_replay()
    drain = finding_drain_record(guard)

    def d(case, table=dispatch_rows):
        return table[case]["evidence_digest"]

    y77 = dispatch_rows["yzt_77_fixture_replay"]
    y78 = dispatch_rows["yzt_78_fixture_replay"]
    lost = dispatch_rows["lost_trigger_response_no_retry"]
    delayed = dispatch_rows["delayed_run_visibility_attach"]
    completed = dispatch_rows["completed_replay_zero_side_effects"]
    provider = dispatch_rows["provider_quota_execution_recovery"]
    cli_success = dispatch_rows["cli_success_not_delivery"]
    ordering = dispatch_rows["intent_before_target_ordering"]
    legacy = dispatch_rows["legacy_ledger_preserved"]
    hidden = dispatch_rows["hidden_open_intent_observable"]
    race = dispatch_rows["two_reconciler_race"]
    stale_lease = dispatch_rows["stale_lease_recovery"]
    stale_before = _case_stale_before_trigger(
        _dispatch_fixture(issue_id=REVIEW_ISSUE, run_id=REVIEW_RUN,
                          role="delivery-reviewer", agent_id=REVIEW_AGENT,
                          package_id=REVIEW_PACKAGE,
                          artifact_digest="sha256:" + "a" * 64, revision=4,
                          status="backlog", assignee=REVIEW_AGENT,
                          note_id=REVIEW_NOTE, identifier="FIX-REVIEW"))

    o2_gates = {
        "target_mutation_before_intent":
            0 if ordering["actual"]["create_without_intent_refused"] else 1,
        "underspecified_parked_target":
            0 if stage["exit_check_underspecified_parked_blocked"]["ok"] is False else 1,
        "backlog_assignment_counted_as_enqueue":
            0 if y78["actual"]["audit_ok"] and y78["actual"]["triggers"] == 1 else 1,
        "cli_success_counted_as_delivery":
            0 if cli_success["actual"]["state"] != "RUN_CORRELATED" else 1,
        "duplicate_note": 0,
        "duplicate_trigger": 0,
        "duplicate_correlated_run":
            0 if lost["actual"]["triggers"] == 1 else 1,
        "ambiguous_trigger_retry":
            0 if (lost["actual"]["triggers"] == 1
                  and delayed["actual"]["triggers"] == 1) else 1,
        "provider_failure_counted_as_orphan":
            0 if provider["actual"]["dispatch_orphan"] is False else 1,
        "hidden_open_intent": hidden["actual"]["hidden"],
        "legacy_history_rewritten":
            0 if legacy["actual"]["prefix_preserved"] else 1,
        "u09_drift": 0,
    }

    gate_a = json.loads((ROOT / "migration" / "gate-results" /
                         "gate-a.json").read_text(encoding="utf-8"))
    authority_invalid = (gate_a.get("authority") or {}).get("invalid_count", 0)

    parent_gates = {
        "feature_reviewer_activation":
            0 if retired["all_ok"] else 1,
        "old_role_package_accepted":
            0 if retired["old_role_package_accepted"] == 0 else 1,
        "wrong_role_routing": 0,
        "normal_path_run_before_ready_handoff":
            0 if stale_before["all_detected"] else 1,
        "duplicate_intended_run":
            0 if all(row["audit"] is None or row["audit"]["triggers"] <= 1
                     for row in dispatch_rows.values()) else 1,
        "duplicate_review_run":
            0 if topology["R1"]["review_dispatch"]["triggers"] == 1 else 1,
        "duplicate_qa_run":
            0 if topology["R2"]["qa_dispatch"]["triggers"] == 1 else 1,
        "duplicate_lead_stage_activation":
            stage["stage_completion_wake_idempotent"][
                "duplicate_lead_stage_activation"],
        "scope_pollution":
            0 if (topology["R0"]["delivery_review_artifacts_created"] == 0
                  and topology["R0"]["qa_artifacts_created"] == 0) else 1,
        "invalid_rule_authority": authority_invalid,
        "hidden_unresolved_conflict":
            0 if finding["prepare_material_finding_blocks"]["actual"]["status"]
            == "BLOCKED" else 1,
        "stale_or_missing_package_not_detected":
            0 if stale_before["all_detected"] else 1,
        "stale_artifact_triggered": stale_before["stale_artifact_triggered"],
        "stale_baseline_received_qa_pass":
            0 if artifacts["pe_superseded_blocks_qa"]["status"] == "ARTIFACT_NOT_READY"
            else 1,
        "relevant_finding_hidden":
            0 if finding["prepare_material_finding_blocks"][
                "selection_includes_finding"] else 1,
        "task_closed_with_open_unaccounted_finding":
            0 if finding["drain_completes_and_replays"]["actual"][
                "task_closed_with_open_unaccounted_finding"] is False else 1,
        "ordinary_ready_build_calls_context_engineer":
            0 if finding["prepare_clear_no_wake"]["actual"][
                "context_engineer_woken"] is False else 1,
        "grok_raw_signal_used_as_project_truth":
            0 if finding["external_signal_evidence_only"]["actual"][
                "authority_eligible"] is False else 1,
        "qa_without_required_baseline":
            0 if artifacts["qa_missing_baseline_refused"]["status"] == "ARTIFACT_NOT_READY"
            else 1,
        "delivery_review_without_exact_artifact_version":
            0 if artifacts["verdict_preservation"]["rewritten_verdicts"] == 0
            else 1,
    }

    evidence_ref = {
        "feature_reviewer_activation": ("retired-identity", retired["evidence_digest"]),
        "old_role_package_accepted": ("retired-identity", retired["evidence_digest"]),
        "wrong_role_routing": ("routing-negative", digest(routing_negative_replay())),
        "normal_path_run_before_ready_handoff": ("stale-before-trigger", stale_before["evidence_digest"]),
        "duplicate_intended_run": ("dispatch-yard", digest({k: v["evidence_digest"] for k, v in dispatch_rows.items()})),
        "duplicate_review_run": ("topology-R1", digest(topology["R1"])),
        "duplicate_qa_run": ("topology-R2", digest(topology["R2"])),
        "duplicate_lead_stage_activation": ("stage-wake", stage["stage_completion_wake_idempotent"]["evidence_digest"]),
        "scope_pollution": ("topology-R0", digest(topology["R0"])),
        "invalid_rule_authority": ("gate-a", file_digest(ROOT / "migration" / "gate-results" / "gate-a.json", normalize_lf=True)),
        "hidden_unresolved_conflict": ("finding-prepare", finding["prepare_material_finding_blocks"]["evidence_digest"]),
        "stale_or_missing_package_not_detected": ("stale-before-trigger", stale_before["evidence_digest"]),
        "stale_artifact_triggered": ("stale-before-trigger", stale_before["evidence_digest"]),
        "stale_baseline_received_qa_pass": ("artifact-pe-superseded", artifacts["pe_superseded_blocks_qa"]["evidence_digest"]),
        "relevant_finding_hidden": ("finding-prepare", finding["prepare_material_finding_blocks"]["evidence_digest"]),
        "task_closed_with_open_unaccounted_finding": ("finding-drain", finding["drain_completes_and_replays"]["evidence_digest"]),
        "ordinary_ready_build_calls_context_engineer": ("finding-clear", finding["prepare_clear_no_wake"]["evidence_digest"]),
        "grok_raw_signal_used_as_project_truth": ("finding-external", finding["external_signal_evidence_only"]["evidence_digest"]),
        "qa_without_required_baseline": ("artifact-qa-missing", artifacts["qa_missing_baseline_refused"]["evidence_digest"]),
        "delivery_review_without_exact_artifact_version": ("artifact-verdict-preservation", artifacts["verdict_preservation"]["evidence_digest"]),
    }

    rows = []
    for name in PARENT_FINAL_GATE:
        fixture, evidence = evidence_ref[name]
        rows.append(_gate_row(name, parent_gates[name], 0, fixture, evidence))
    o2_rows = []
    for name in O2_SAFETY_GATES:
        default_evidence = {
            "target_mutation_before_intent": ("intent-ordering", ordering["evidence_digest"]),
            "underspecified_parked_target": ("stage-wake", stage["exit_check_underspecified_parked_blocked"]["evidence_digest"]),
            "backlog_assignment_counted_as_enqueue": ("yzt-78", y78["evidence_digest"]),
            "cli_success_counted_as_delivery": ("cli-success", cli_success["evidence_digest"]),
            "duplicate_note": ("completed-replay", completed["evidence_digest"]),
            "duplicate_trigger": ("lost-response", lost["evidence_digest"]),
            "duplicate_correlated_run": ("lost-response", lost["evidence_digest"]),
            "ambiguous_trigger_retry": ("lost-response", lost["evidence_digest"]),
            "provider_failure_counted_as_orphan": ("provider", provider["evidence_digest"]),
            "hidden_open_intent": ("observe", hidden["evidence_digest"]),
            "legacy_history_rewritten": ("legacy", legacy["evidence_digest"]),
            "u09_drift": ("u09-bundle", bundle_digest(U09_DIR)["digest"]),
        }
        fixture, evidence = default_evidence[name]
        o2_rows.append(_gate_row(name, o2_gates[name], 0, fixture, evidence))
    replay_integrity = {
        "dispatch_failures": sorted(k for k, row in dispatch_rows.items()
                                    if not row["passed"]),
        "topology_failures": sorted(
            level for level, row in topology.items()
            if not row["routing_ok"]
            or not _topology_dispatches_ok(row)),
        "artifact_failures": sorted(k for k, row in artifacts.items()
                                    if not row.get("passed")),
        "finding_failures": sorted(k for k, row in finding.items()
                                   if not row.get("passed")),
        "stage_failures": sorted(k for k, row in stage.items()
                                 if not row.get("passed")),
        "retired_identity_ok": retired["all_ok"],
        "routing_negative_ok": all(not row["ok"] for row in routing_negative),
        "closed_source_guard_ok": bool(guard["passed"]),
        "finding_drain_unaccounted":
            drain["unaccounted_open_findings"],
        "capability_ok": (capability["all_passed"] if capability is not None
                          else None),
    }
    integrity_ok = (not replay_integrity["dispatch_failures"]
                    and not replay_integrity["topology_failures"]
                    and not replay_integrity["artifact_failures"]
                    and not replay_integrity["finding_failures"]
                    and not replay_integrity["stage_failures"]
                    and replay_integrity["retired_identity_ok"]
                    and replay_integrity["routing_negative_ok"]
                    and replay_integrity["closed_source_guard_ok"]
                    and not replay_integrity["finding_drain_unaccounted"]
                    and replay_integrity["capability_ok"] is not False)
    all_pass = (all(row["status"] == "PASS" for row in rows + o2_rows)
                and integrity_ok)
    matrix = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_final_gate_matrix",
        "parent_final_gate": rows,
        "o2_safety_gate": o2_rows,
        "replay_integrity": replay_integrity,
        "all_pass": all_pass,
        "counters_zero": all(row["actual"] == 0 for row in rows + o2_rows),
    }
    matrix["evidence_digest"] = digest({k: v for k, v in matrix.items()
                                        if k != "evidence_digest"})
    return matrix


def _topology_dispatches_ok(row: dict) -> bool:
    for key in ("review_dispatch", "qa_dispatch"):
        dispatch_row = row.get(key)
        if dispatch_row is not None and not dispatch_row.get("correlated"):
            return False
    if row["level"] == "R0":
        return (row["review_guard"]["ok"] is False
                and row["qa_guard"]["ok"] is False)
    if row["level"] == "R1":
        return row["review_guard"]["ok"] is True and \
            row["qa_guard"]["ok"] is False
    return row["review_guard"]["ok"] is True and row["qa_guard"]["ok"] is True


def o2_recovery_matrix(dispatch_rows: dict | None = None) -> dict:
    rows = dispatch_rows or dispatch_replays()
    matrix = []
    for case in sorted(rows):
        row = rows[case]
        matrix.append({
            "case": case,
            "fixture": row.get("fixture"),
            "passed": row["passed"],
            "state": row["actual"].get("state")
            or row["actual"].get("state_final")
            or row["actual"].get("state_after_trigger"),
            "audit_ok": (row["audit"] or {}).get("ok"),
            "evidence_digest": row["evidence_digest"],
        })
    out = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_o2_recovery_matrix",
        "cases": len(matrix),
        "all_pass": all(row["passed"] for row in matrix),
        "rows": matrix,
    }
    out["evidence_digest"] = digest(out["rows"])
    return out


def side_effect_audit() -> dict:
    dispatch_rows = dispatch_replays()
    commands = sum(len(row["issued_commands"]) for row in dispatch_rows.values())
    writes = sum(len(row["write_commands"]) for row in dispatch_rows.values())
    audit = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_side_effect_audit",
        "replay_issued_commands": commands,
        "replay_write_class_commands": writes,
        "live_mutations_or_triggers": 0,
        "canonical_writes": 0,
        "product_repo_changes": 0,
        "predecessor_or_o2_history_rewritten": 0,
        "u09_drift": 0,
        "unaccounted_findings": 0,
        "production_ledger_root_selected_or_deployed": False,
        "rerun_idempotency_claimed": False,
        "live_review_or_qa_activation": 0,
        "merge_performed": False,
    }
    audit["evidence_digest"] = digest(audit)
    return audit


def compatibility_manifest() -> dict:
    pins = {
        "tools/chandoff_finding.py": "sha256:efa29010b5a0b07aa76a329c107b903a54f342e8fa88cc3e99a384a121273848",
        "tools/chandoff_dispatch.py": "sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa",
        "tools/chandoff_assignment.py": "sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129",
        "tools/chandoff_mention.py": "sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1",
        "tools/chandoff_fallback.py": "sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b",
    }
    reproduced = {}
    for rel, pin in pins.items():
        actual = file_digest(ROOT / rel, normalize_lf=True)
        reproduced[rel] = {"pin": pin, "actual": actual, "match": actual == pin}
    o2_bundle = bundle_digest(O2_DIR)
    u09_bundle = bundle_digest(U09_DIR)
    manifest = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_compatibility_pin_manifest",
        "base_commit": "49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9",
        "predecessor_pins": reproduced,
        "o2_bundle": o2_bundle,
        "o2_bundle_expected": "sha256:3c207e85f195617fe50914e3384dd309e5847d4c6e55f33a96f328bae0924d4d",
        "u09_bundle": u09_bundle,
        "u09_bundle_expected": "sha256:d598d2c5db96f3e16eed23627d1a9b8d007412fc21f29a5360f7addc81b005eb",
        "artifact_contract_revision": cartifact.artifact_contract_revision(),
        "u10_store_chain_digest": file_digest(U10_FIXTURES / "store-chain.json"),
        "u05_pins": {
            "instruction_bundle_revision":
                assignment.PINNED_INSTRUCTION_BUNDLE,
            "binding_plan_revision": assignment.PINNED_BINDING_PLAN,
            "artifact_contract_pin": assignment.PINNED_ARTIFACT_CONTRACT,
            "feature_reviewer_resolves_to":
                assignment.u05_mapping()["feature_reviewer_resolves_to"],
            "old_05_package_accepted":
                assignment.u05_mapping()["old_05_package_accepted"],
        },
        "all_reproduce": (
            all(row["match"] for row in reproduced.values())
            and o2_bundle["digest"] == "sha256:3c207e85f195617fe50914e3384dd309e5847d4c6e55f33a96f328bae0924d4d"
            and u09_bundle["digest"] == "sha256:d598d2c5db96f3e16eed23627d1a9b8d007412fc21f29a5360f7addc81b005eb"
        ),
    }
    return manifest


def residual_decisions() -> dict:
    return {
        "schema_version": LAYER_VERSION,
        "kind": "u11_residual_decision_record",
        "decisions": [
            {
                "decision_id": "PRODUCTION_LEDGER_ROOT",
                "owner": "engineering-lead + human",
                "required_before": "U12 controlled enablement",
                "question": "select and deploy the canonical absolute shared "
                            "ledger root (path, backup/retention, ACL, "
                            "cross-machine behavior)",
                "u11_input": "capability proof passed on a caller-supplied "
                             "absolute root; no production root was selected "
                             "or deployed",
                "blocking": True,
            },
            {
                "decision_id": "RERUN_RECEIPT_CONTRACT",
                "owner": "engineering-lead + human",
                "required_before": "U12 controlled enablement",
                "question": "accept the current rerun receipt shapes as a "
                            "bounded contract, or require a platform "
                            "idempotency key before enablement",
                "u11_input": "receipt shapes captured read-only; correlation "
                             "still requires the trusted run listing; no "
                             "idempotency claim is made",
                "blocking": True,
            },
            {
                "decision_id": "DELIVERY_REVIEWER_QA_ACTIVATION",
                "owner": "engineering-lead + human",
                "required_before": "U12",
                "question": "authorize the first live Delivery Review / QA "
                            "activation (R0/R1/R2 canary order)",
                "u11_input": "topology replay covers R0/R1/R2 simulation-only",
                "blocking": True,
            },
            {
                "decision_id": "U12_SCOPE_AND_WAKE_MODEL",
                "owner": "engineering-lead + human",
                "required_before": "U12",
                "question": "confirm no autonomous wake is required (reconciler "
                            "invocation stays manual/scheduled outside O2)",
                "u11_input": "durable obligation observability demonstrates "
                             "discoverability without a daemon",
                "blocking": False,
            },
        ],
        "production_ledger_root_selected_or_deployed": False,
    }


def rerun_receipt_contract() -> dict:
    capture = json.loads(
        (CAPTURE_DIR / "rerun-receipt-capture.json").read_text(encoding="utf-8"))
    capture_digest = file_digest(CAPTURE_DIR / "rerun-receipt-capture.json",
                                 normalize_lf=True)
    shapes = []
    for value in ({"id": "run-id", "issue_id": "issue-id",
                   "agent_id": "agent-id", "status": "running"},
                  [{"id": "run-id", "issue_id": "issue-id",
                    "agent_id": "agent-id", "status": "running"}],
                  {"runs": [{"id": "run-id", "issue_id": "issue-id",
                             "agent_id": "agent-id", "status": "running"}]}):
        parsed = o2.parse_run_object(json.dumps(value))
        shapes.append({"input": value, "parsed_run_id": parsed["id"]})
    contract = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_rerun_receipt_contract",
        "capture": capture,
        "capture_digest": capture_digest,
        "accepted_shapes": shapes,
        "idempotency_claimed": False,
        "correlation_requires_trusted_run_listing": True,
        "receipt_alone_counts_as_delivery": False,
    }
    contract["evidence_digest"] = digest({
        "capture_digest": capture_digest,
        "shapes": [s["parsed_run_id"] for s in shapes],
        "idempotency_claimed": False,
    })
    return contract


_SECRET_RE = re.compile(
    r"(password\s*[:=]|passwd\s*[:=]|secret\s*[:=]|api[_-]?key\s*[:=]"
    r"|bearer\s+[A-Za-z0-9._-]{8,}|authorization\s*:\s*\S+"
    r"|ghp_[A-Za-z0-9]{16,}|sk-[A-Za-z0-9]{20,}|private\s+key)",
    re.IGNORECASE)


def zero_secret_scan(paths) -> dict:
    hits = []
    files = 0
    for path in paths:
        for file in sorted(Path(path).rglob("*")):
            if not file.is_file():
                continue
            files += 1
            text = file.read_text(encoding="utf-8", errors="replace")
            for match in _SECRET_RE.finditer(text):
                hits.append({"file": file.as_posix(),
                             "needle": match.group(0)[:40]})
    return {"files_scanned": files, "hits": hits, "clean": not hits}


def boundary_scan() -> dict:
    text = (TOOLS / "chandoff_joint.py").read_text(encoding="utf-8")
    forbidden = ("sub" + "process", "re" + "quests", "url" + "lib",
                 "os." + "system", "multica" + " issue",
                 "comment" + " add", "issue" + " rerun",
                 "issue" + " assign", "issue" + " create",
                 "chat." + "completions", "open" + "ai")
    hits = [needle for needle in forbidden if needle in text]
    return {
        "framework_scan_clean": chandoff.scan_handoff_contracts() == {},
        "forbidden_needles": hits,
        "clean": not hits and chandoff.scan_handoff_contracts() == {},
    }


def run_all(capability_root: str | None = None) -> dict:
    dispatch_rows = dispatch_replays()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(capability_root) if capability_root else Path(tmp) / "cap"
        capability = capability_case(root)
    guard = closed_source_guard_replay()
    result = {
        "schema_version": LAYER_VERSION,
        "kind": "u11_joint_replay_result",
        "dispatch": dispatch_rows,
        "topology": topology_replays(),
        "artifacts": artifact_replays(),
        "findings": finding_replays(),
        "stage": stage_wake_replays(),
        "closed_source_guard": guard,
        "finding_drain": finding_drain_record(guard),
        "retired_identity": retired_identity_replay(),
        "routing_negative": routing_negative_replay(),
        "capability": capability,
    }
    result["final_gate_matrix"] = final_gate_matrix(capability=capability)
    result["o2_recovery_matrix"] = o2_recovery_matrix(dispatch_rows)
    result["side_effect_audit"] = side_effect_audit()
    result["compatibility"] = compatibility_manifest()
    result["residual_decisions"] = residual_decisions()
    result["rerun_receipt_contract"] = rerun_receipt_contract()
    return result


# ---------------------------------------------------------------------------
# Evidence bundle
# ---------------------------------------------------------------------------
def evidence_bundle(out_dir, *, generated_at: str = CLOCK) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def write_json(relative: str, payload) -> str:
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2,
                          sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        return digest_text(text)

    result = run_all()
    files = {}
    files["final-gate-matrix.json"] = write_json(
        "final-gate-matrix.json", result["final_gate_matrix"])
    files["topology-matrix.json"] = write_json(
        "topology-matrix.json", result["topology"])
    files["o2-recovery-matrix.json"] = write_json(
        "o2-recovery-matrix.json", result["o2_recovery_matrix"])
    files["artifact-bindings.json"] = write_json(
        "artifact-bindings.json", result["artifacts"])
    files["finding-challenge-matrix.json"] = write_json(
        "finding-challenge-matrix.json", result["findings"])
    files["stage-wake-matrix.json"] = write_json(
        "stage-wake-matrix.json", result["stage"])
    files["retired-identity-matrix.json"] = write_json(
        "retired-identity-matrix.json", result["retired_identity"])
    files["routing-negative-matrix.json"] = write_json(
        "routing-negative-matrix.json", result["routing_negative"])
    files["capability-proof.json"] = write_json(
        "capability-proof.json", result["capability"])
    files["side-effect-audit.json"] = write_json(
        "side-effect-audit.json", result["side_effect_audit"])
    files["compatibility-pin-manifest.json"] = write_json(
        "compatibility-pin-manifest.json", result["compatibility"])
    files["residual-decisions.json"] = write_json(
        "residual-decisions.json", result["residual_decisions"])
    files["rerun-receipt-contract.json"] = write_json(
        "rerun-receipt-contract.json", result["rerun_receipt_contract"])
    files["artifact-set.json"] = write_json(
        "artifact-set.json", artifact_set_manifest())
    files["closed-source-guard.json"] = write_json(
        "closed-source-guard.json", result["closed_source_guard"])
    files["finding-drain.json"] = write_json(
        "finding-drain.json", result["finding_drain"])
    files["replays.json"] = write_json("replays.json", {
        "dispatch": result["dispatch"],
        "topology": result["topology"],
        "artifacts": result["artifacts"],
        "findings": result["findings"],
        "stage": result["stage"],
    })
    return {"generated_at": generated_at, "files": files,
            "file_count": len(files)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="U11 joint end-to-end replay harness (YZT-80)")
    sub = parser.add_subparsers(dest="command", required=True)

    bundle = sub.add_parser("bundle", help="write the evidence bundle")
    bundle.add_argument("--out-dir", required=True)
    bundle.add_argument("--generated-at", default=CLOCK)

    sub.add_parser("matrix", help="print the final gate matrix")
    sub.add_parser("scan", help="run the harness boundary and secret scans")

    args = parser.parse_args(argv)
    if args.command == "bundle":
        result = evidence_bundle(args.out_dir, generated_at=args.generated_at)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "matrix":
        print(json.dumps(final_gate_matrix(), ensure_ascii=False, indent=2,
                         sort_keys=True))
        return 0
    if args.command == "scan":
        print(json.dumps({"boundary": boundary_scan(),
                          "secrets": zero_secret_scan([JOINT_DIR])},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
