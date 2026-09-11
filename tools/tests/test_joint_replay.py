#!/usr/bin/env python3
"""U11 focused tests — joint end-to-end replay for O2 and V2.2 gates (YZT-80).

Every replay runs in memory or in a temporary caller-supplied root: no live
issue/comment/assignment/rerun/mention/status/run write, no live Review/QA
activation, no Canonical/product write, no production ledger root. Privileged
tokens are assembled at runtime so the U05 old-role inventory stays stable.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import cartifact  # noqa: E402
import chandoff  # noqa: E402
import chandoff_finding as u09  # noqa: E402
import chandoff_instructions as instr  # noqa: E402
import chandoff_intent as o2  # noqa: E402
import chandoff_joint as u11  # noqa: E402

RETIRED_FR = next(k for k in u09.RETIRED_ROLES if "feature" in k)
LIVE_05_NAME = instr.LIVE_DISPLAY_NAME["delivery-reviewer"]
JOINT_DIR = ROOT / "adapters" / "multica" / "joint-replay"
O2_DIR = ROOT / "adapters" / "multica" / "dispatch-intent"
REPORT = (ROOT / "adapters" / "multica" /
          "U11_JOINT_END_TO_END_REPLAY_REPORT.md")


@functools.lru_cache(maxsize=None)
def run_all():
    return u11.run_all()


@functools.lru_cache(maxsize=None)
def dispatch_rows():
    return u11.dispatch_replays()


@functools.lru_cache(maxsize=None)
def topology_rows():
    return u11.topology_replays()


@functools.lru_cache(maxsize=None)
def artifact_rows():
    return u11.artifact_replays()


@functools.lru_cache(maxsize=None)
def finding_rows():
    return u11.finding_replays()


@functools.lru_cache(maxsize=None)
def stage_rows():
    return u11.stage_wake_replays()


@functools.lru_cache(maxsize=None)
def matrix():
    return u11.final_gate_matrix(capability=run_all()["capability"])


class TopologyTests(unittest.TestCase):
    def test_r0_no_meaningless_review_or_qa(self):
        row = topology_rows()["R0"]
        self.assertTrue(row["routing_ok"])
        self.assertFalse(row["requires_delivery_review"])
        self.assertFalse(row["requires_qa"])
        self.assertFalse(row["review_guard"]["ok"])
        self.assertFalse(row["qa_guard"]["ok"])
        self.assertEqual(row["delivery_review_artifacts_created"], 0)
        self.assertEqual(row["qa_artifacts_created"], 0)

    def test_r1_lead_mediated_single_exact_review(self):
        row = topology_rows()["R1"]
        self.assertTrue(row["routing_ok"])
        self.assertTrue(row["lead_mediated"])
        dispatch = row["review_dispatch"]
        self.assertTrue(dispatch["correlated"])
        self.assertEqual(dispatch["triggers"], 1)
        self.assertTrue(dispatch["review_envelope_valid"])
        self.assertEqual(dispatch["reviewed_artifact_exact"],
                         [u11.U10_IMPL_ID, u11.U10_IMPL_VERSION])
        self.assertEqual(dispatch["verdict"], "APPROVE")

    def test_r2_extends_with_exact_baseline_qa(self):
        row = topology_rows()["R2"]
        self.assertTrue(row["routing_ok"])
        self.assertTrue(row["review_dispatch"]["correlated"])
        qa = row["qa_dispatch"]
        self.assertTrue(qa["correlated"])
        self.assertEqual(qa["triggers"], 1)
        self.assertTrue(qa["qa_envelope_valid"])
        baseline_ids = {row[0] for row in qa["validated_against"]}
        self.assertEqual(
            baseline_ids,
            {u11.U10_PE_ID, u11.U10_DESIGN_ID, u11.U10_IMPL_ID,
             u11.U10_REVIEW_ID})

    def test_r1_has_no_qa_route(self):
        row = topology_rows()["R1"]
        self.assertNotIn("qa_dispatch", row)
        self.assertFalse(row["qa_guard"]["ok"])

    def test_routing_negatives_all_refused(self):
        rows = u11.routing_negative_replay()
        self.assertTrue(rows)
        for row in rows:
            self.assertFalse(row["ok"], row["case"])
            self.assertEqual(row["triggers_created"], 0)
        codes = {code for row in rows for code in row["violation_codes"]}
        self.assertIn("R1_EXACT_REVIEW_INPUT_REQUIRED", codes)
        self.assertIn("R2_EXACT_QA_BASELINES_REQUIRED", codes)
        self.assertIn("AUTO_TRIGGER_FORBIDDEN", codes)
        self.assertIn("TRIGGER_CREATED", codes)


class ArtifactTests(unittest.TestCase):
    def test_artifact_set_is_seven_typed_and_valid(self):
        store = u11.artifact_store()
        types = {env["artifact_type"] for env in store.envelopes}
        self.assertEqual(types, set(cartifact.CORE_ARTIFACT_TYPES))
        for env in store.envelopes:
            level = (env.get("semantics") or {}).get("review_level")
            self.assertEqual(
                cartifact.validate_envelope(env, review_level=level), [],
                env["artifact_id"])

    def test_r1_and_r2_ready_on_exact_versions(self):
        rows = artifact_rows()
        self.assertEqual(rows["r1_review_input_ready"]["status"],
                         "ARTIFACT_READY")
        self.assertEqual(rows["r2_qa_baselines_ready"]["status"],
                         "ARTIFACT_READY")
        self.assertFalse(rows["r1_review_input_ready"]["blocks_handoff"])

    def test_superseded_implementation_blocks_review(self):
        row = artifact_rows()["implementation_superseded"]
        self.assertEqual(row["status"], "ARTIFACT_NOT_READY")
        self.assertIn("SUPERSEDED", row["failure_codes"])

    def test_stale_pe_or_design_blocks_qa(self):
        for case in ("pe_superseded_blocks_qa", "design_stale_blocks_qa"):
            row = artifact_rows()[case]
            self.assertEqual(row["status"], "ARTIFACT_NOT_READY", case)
            self.assertTrue(row["blocks_handoff"], case)

    def test_latest_version_and_missing_baseline_fail_closed(self):
        self.assertIn("VERSION_NOT_EXACT",
                      artifact_rows()["latest_version_refused"]["failure_codes"])
        self.assertEqual(
            artifact_rows()["qa_missing_baseline_refused"]["status"],
            "ARTIFACT_NOT_READY")

    def test_changes_required_blocks_qa_gate(self):
        row = artifact_rows()["qa_after_changes_required_refused"]
        self.assertEqual(row["status"], "ARTIFACT_NOT_READY")
        self.assertIn("QA_GATE_BLOCKED", row["failure_codes"])

    def test_old_verdict_is_never_rewritten(self):
        row = artifact_rows()["verdict_preservation"]
        self.assertEqual(row["old_review_verdict"], "APPROVE")
        self.assertEqual(row["old_review_status"], "stale")
        self.assertEqual(row["old_qa_verdict"], "PASS")
        self.assertEqual(row["old_qa_status"], "stale")
        self.assertTrue(row["open_new_attempt"]["history_preserved"])
        self.assertEqual(row["rewritten_verdicts"], 0)
        self.assertTrue(row["passed"])


class RetiredIdentityTests(unittest.TestCase):
    def test_retired_identity_replay_all_ok(self):
        result = run_all()["retired_identity"]
        self.assertTrue(result["all_ok"],
                        [r for r in result["rows"] if not r["ok"]])
        self.assertEqual(result["feature_reviewer_activation"], 0)
        self.assertEqual(result["old_role_package_accepted"], 0)
        self.assertEqual(result["delivery_reviewer_role"], "delivery-reviewer")

    def test_retired_token_never_resolves(self):
        self.assertFalse(instr.resolve_logical_role(RETIRED_FR)["ok"])
        self.assertTrue(instr.resolve_logical_role("delivery-reviewer")["ok"])
        self.assertFalse(instr.resolve_logical_role(LIVE_05_NAME)["ok"])

    def test_old_package_cannot_alias_new_route(self):
        old_package = {"target_role": RETIRED_FR, "identity": RETIRED_FR,
                       "skill_names": list(instr.LEGACY_05_SKILLS)}
        result = instr.package_activation_check(old_package,
                                                "delivery-reviewer")
        self.assertFalse(result["ok"])
        self.assertIn("no_alias_rewrite", result["reasons"])
        self.assertIn("old_role_package_rejected", result["reasons"])

    def test_runtime_layers_refuse_retired_role(self):
        with self.assertRaises(o2.RetiredIdentityError):
            o2._require_role(RETIRED_FR)
        with self.assertRaises(u09.RetiredIdentityError):
            u09._require_role(RETIRED_FR)
        with self.assertRaises(u09.RetiredIdentityError):
            u09.validate_binding({"package_id": u11.REVIEW_PACKAGE,
                                  "role": RETIRED_FR})

    def test_live_05_package_invalidation_remains(self):
        invalidation = json.loads(
            (ROOT / "adapters" / "multica" / "agent-instructions" /
             "package-invalidation.json").read_text(encoding="utf-8"))
        self.assertFalse(invalidation["old_05_package_accepted"])
        self.assertIn(invalidation.get("feature_reviewer_activation"), (0, None))


class DispatchTests(unittest.TestCase):
    def test_all_dispatch_replays_pass(self):
        rows = dispatch_rows()
        failures = {k: (r["expected"], r["actual"])
                    for k, r in rows.items() if not r["passed"]}
        self.assertEqual(failures, {})

    def test_yzt_77_and_78_sanitized_fixtures_replay(self):
        for case, binding in (("yzt_77_fixture_replay", None),
                              ("yzt_78_fixture_replay",
                               "issue_assign_no_start")):
            row = dispatch_rows()[case]
            self.assertEqual(row["actual"]["state"], "RUN_CORRELATED")
            self.assertEqual(row["actual"]["selected_trigger"], "issue_rerun")
            self.assertEqual(row["actual"]["triggers"], 1)
            self.assertTrue(row["actual"]["audit_ok"])
            self.assertEqual(row["actual"]["live_mutations"], 0)
            self.assertEqual(row["actual"]["ownership_binding"], binding)

    def test_yzt_78_binding_is_not_counted_as_trigger(self):
        row = dispatch_rows()["yzt_78_fixture_replay"]
        commands = " ".join(row["write_commands"])
        self.assertIn("--no-start", commands)
        self.assertEqual(row["actual"]["triggers"], 1)

    def test_intent_precedes_target_mutation(self):
        row = dispatch_rows()["intent_before_target_ordering"]
        self.assertEqual(row["actual"]["create_without_intent_refused"],
                         "intent_not_found")
        self.assertTrue(row["actual"]["intent_seq_before_create_seq"])

    def test_ambiguous_create_attaches_or_stops_without_duplication(self):
        rows = dispatch_rows()
        one = rows["ambiguous_create_1_targets"]
        self.assertEqual(one["actual"]["state"], "TARGET_BOUND")
        self.assertTrue(one["actual"]["discovered"])
        for case in ("ambiguous_create_0_targets", "ambiguous_create_2_targets"):
            self.assertEqual(rows[case]["actual"]["state"], "CREATE_AMBIGUOUS")
            self.assertEqual(rows[case]["actual"]["create_commands"], 1)

    def test_lost_trigger_response_is_ambiguous_and_never_retried(self):
        row = dispatch_rows()["lost_trigger_response_no_retry"]
        self.assertEqual(row["actual"]["state_after_trigger"],
                         "TRIGGER_AMBIGUOUS")
        self.assertEqual(row["actual"]["reconcile_action"], "ATTACH_RUN")
        self.assertEqual(row["actual"]["state_final"], "RUN_CORRELATED")
        self.assertEqual(row["actual"]["triggers"], 1)
        self.assertFalse(row["actual"]["trigger_reissued"])

    def test_delayed_visibility_waits_then_attaches(self):
        row = dispatch_rows()["delayed_run_visibility_attach"]
        self.assertEqual(row["actual"]["state_after_trigger"],
                         "TRIGGER_ISSUING")
        self.assertTrue(row["actual"]["awaiting_visibility"])
        self.assertEqual(row["actual"]["reconcile_action"], "ATTACH_RUN")
        self.assertEqual(row["actual"]["triggers"], 1)

    def test_duplicate_wrong_and_preexisting_runs_fail_closed(self):
        rows = dispatch_rows()
        self.assertEqual(rows["duplicate_correlated_runs_refused"]["actual"]["state"],
                         "BLOCKED")
        self.assertEqual(rows["wrong_target_run_refused"]["actual"]["state"],
                         "BLOCKED")
        self.assertEqual(
            rows["pre_existing_unexpected_run_refused"]["actual"]["reason"],
            "UNEXPECTED_RUN")
        self.assertEqual(
            rows["pre_existing_unexpected_run_refused"]["actual"]["issued_commands"],
            0)

    def test_publish_pre_trigger_kill_resumes_with_one_trigger(self):
        row = dispatch_rows()["publish_pre_trigger_kill_resume"]
        self.assertEqual(row["actual"]["classification"],
                         "POST_PUBLISH_PRE_TRIGGER")
        self.assertEqual(row["actual"]["safe_action"], "RESUME_ISSUE")
        self.assertEqual(row["actual"]["state"], "RUN_CORRELATED")
        self.assertEqual(row["actual"]["triggers"], 1)

    def test_cli_success_is_not_delivery(self):
        row = dispatch_rows()["cli_success_not_delivery"]
        self.assertEqual(row["actual"]["selected_trigger"], "issue_assign")
        self.assertEqual(row["actual"]["state"], "TRIGGER_ISSUING")
        self.assertFalse(row["actual"]["correlated"])
        self.assertTrue(row["actual"]["awaiting_visibility"])

    def test_provider_quota_is_execution_recovery(self):
        row = dispatch_rows()["provider_quota_execution_recovery"]
        self.assertEqual(row["actual"]["status"], "EXECUTION_RECOVERY_RECORDED")
        self.assertFalse(row["actual"]["redispatch"])
        self.assertFalse(row["actual"]["dispatch_orphan"])
        self.assertEqual(row["actual"]["state"], "RUN_CORRELATED")
        self.assertEqual(row["actual"]["classification"], "PROVIDER_QUOTA_FAILURE")

    def test_completed_replay_has_zero_side_effects(self):
        row = dispatch_rows()["completed_replay_zero_side_effects"]
        self.assertEqual(row["actual"]["state"], "COMPLETED")
        self.assertEqual(row["actual"]["replay_classification"],
                         "TERMINAL_REPLAY")
        self.assertEqual(row["actual"]["replay_side_effects"], 0)
        self.assertEqual(row["actual"]["commands_after_replays"],
                         row["actual"]["commands_after_terminal"])
        self.assertTrue(row["actual"]["replay_complete"])
        self.assertTrue(row["actual"]["replay_publish"])

    def test_stale_package_and_artifact_detected_before_trigger(self):
        fixture = u11._dispatch_fixture(
            issue_id=u11.REVIEW_ISSUE, run_id=u11.REVIEW_RUN,
            role="delivery-reviewer", agent_id=u11.REVIEW_AGENT,
            package_id=u11.REVIEW_PACKAGE,
            artifact_digest="sha256:" + "a" * 64, revision=4, status="backlog",
            assignee=u11.REVIEW_AGENT, note_id=u11.REVIEW_NOTE,
            identifier="FIX-REVIEW")
        result = u11._case_stale_before_trigger(fixture)
        self.assertTrue(result["all_detected"])
        decisions = {row["case"]: (row["decision"], row["reason"])
                     for row in result["rows"]}
        self.assertEqual(decisions["ready_note_missing"],
                         ("REFRESH_REQUIRED", "READY_NOTE_MISSING"))
        self.assertEqual(decisions["package_stale"],
                         ("REFRESH_REQUIRED", "PACKAGE_STALE"))
        self.assertEqual(decisions["artifact_not_ready"],
                         ("REFRESH_REQUIRED", "ARTIFACT_STALE"))
        self.assertEqual(result["stale_artifact_triggered"], 0)

    def test_stale_lease_and_two_reconciler_race(self):
        rows = dispatch_rows()
        lease = rows["stale_lease_recovery"]
        self.assertTrue(lease["actual"]["expired_previous"])
        self.assertEqual(lease["actual"]["active_holder"], "writer-b")
        race = rows["two_reconciler_race"]
        self.assertEqual(race["actual"]["second_claim_refused"], "lease_held")
        self.assertEqual(race["actual"]["second_transition_refused"],
                         "lease_not_held")

    def test_legacy_history_preserved(self):
        row = dispatch_rows()["legacy_ledger_preserved"]
        self.assertTrue(row["actual"]["prefix_preserved"])
        self.assertTrue(row["actual"]["legacy_records_readable"])
        self.assertTrue(row["actual"]["ignored_records_positive"])
        self.assertEqual(row["actual"]["trigger_delta"], 0)
        self.assertEqual(row["actual"]["create_delta"], 0)

    def test_open_intent_is_visible(self):
        row = dispatch_rows()["hidden_open_intent_observable"]
        self.assertEqual(row["actual"]["open_count"], 1)
        self.assertEqual(row["actual"]["hidden"], 0)
        self.assertEqual(row["actual"]["next_owner"], "source-run")

    def test_trigger_issuing_precedes_native_call(self):
        rows = dispatch_rows()
        for case in ("backlog_same_assignee_rerun",
                     "active_unassigned_assignment_trigger",
                     "backlog_different_assignee_binding"):
            audit = rows[case]["audit"]
            self.assertTrue(audit["ok"], case)
            self.assertTrue(audit["issuing_precedes_trigger_command"], case)
            self.assertEqual(audit["status_triggers"], 0)
            self.assertEqual(audit["mention_triggers"], 0)

    def test_every_dispatch_ledger_shows_no_duplicate_delivery(self):
        for case, row in dispatch_rows().items():
            audit = row["audit"]
            if audit is None:
                continue
            self.assertLessEqual(audit["triggers"], 1, case)
            self.assertLessEqual(audit["ownership_bindings"], 1, case)
            self.assertLessEqual(audit["creates"], 1, case)


class FindingChallengeTests(unittest.TestCase):
    def test_local_review_defect_stays_in_artifact(self):
        row = finding_rows()["local_review_defect_stays_in_artifact"]
        self.assertEqual(row["actual"]["route"], "keep_in_artifact")
        self.assertTrue(row["actual"]["local_artifact"])
        self.assertFalse(row["actual"]["runtime_finding"])
        self.assertFalse(row["actual"]["direct_trigger"])

    def test_cognition_becomes_runtime_finding(self):
        row = finding_rows()["review_cognition_becomes_finding"]
        self.assertEqual(row["actual"]["route"], "runtime_boundary_processing")
        self.assertTrue(row["actual"]["runtime_finding"])
        self.assertFalse(row["actual"]["direct_trigger"])

    def test_design_deviation_and_challenge_are_distinct(self):
        rows = finding_rows()
        self.assertEqual(rows["design_deviation_recorded"]["actual"]["classification"],
                         "DESIGN_DEVIATION")
        self.assertEqual(rows["design_challenge_returned_to_lead"]["actual"]["classification"],
                         "DESIGN_CHALLENGE")
        for case in ("design_deviation_recorded",
                     "design_challenge_returned_to_lead",
                     "product_challenge_returned_to_lead"):
            self.assertEqual(rows[case]["actual"]["route"], "lead_decision")
            self.assertFalse(rows[case]["actual"]["direct_trigger"])

    def test_external_signal_is_evidence_only(self):
        row = finding_rows()["external_signal_evidence_only"]
        self.assertEqual(row["actual"]["truth_status"], "evidence")
        self.assertFalse(row["actual"]["authority_eligible"])
        self.assertFalse(row["actual"]["promotion_allowed"])
        self.assertFalse(row["actual"]["direct_trigger"])

    def test_capture_is_capture_only(self):
        for case in ("capture_local_defect", "capture_cognition",
                     "capture_external_raw"):
            row = finding_rows()[case]
            self.assertFalse(row["actual"]["process_now"], case)
            self.assertFalse(row["actual"]["wake_context_engineer"], case)
        self.assertEqual(
            finding_rows()["capture_external_raw"]["stored_verification"],
            "unverified")

    def test_prepare_clear_never_calls_context_engineer(self):
        row = finding_rows()["prepare_clear_no_wake"]
        self.assertEqual(row["actual"]["status"], "CLEAR")
        self.assertTrue(row["actual"]["ready_allowed"])
        self.assertFalse(row["actual"]["context_engineer_woken"])

    def test_material_finding_blocks_ready_into_proposal_only(self):
        row = finding_rows()["prepare_material_finding_blocks"]
        self.assertEqual(row["actual"]["status"], "BLOCKED")
        self.assertFalse(row["actual"]["ready_allowed"])
        self.assertTrue(row["escalation"]["required"])
        self.assertEqual(row["escalation"]["addressed_to"], "engineering-lead")
        self.assertFalse(row["escalation"]["trigger_emitted"])
        self.assertIn("FIND-WIMG-U11-000011", row["selection_includes_finding"])

    def test_challenge_gap_resolves_before_any_02_handoff(self):
        row = finding_rows()["challenge_context_gap_resolved"]
        self.assertEqual(row["actual"]["status"], "RESOLVED")
        self.assertEqual(row["actual"]["action"], "CONTINUE")
        self.assertFalse(row["actual"]["context_engineer_woken"])

    def test_challenge_unresolved_is_lead_owned_proposal_only(self):
        row = finding_rows()["challenge_unresolved_material_proposal_only"]
        self.assertEqual(row["actual"]["status"], "UNRESOLVED_MATERIAL")
        self.assertEqual(row["proposal"]["addressed_to"], "engineering-lead")
        self.assertEqual(row["proposal"]["recommended_target"],
                         "context-engineer")
        self.assertFalse(row["proposal"]["trigger_emitted"])
        self.assertTrue(row["proposal"]["requires_lead_owned_safe_dispatch"])
        self.assertFalse(row["actual"]["direct_trigger"])

    def test_finding_drain_before_completion(self):
        blocked = finding_rows()["drain_blocks_unaccounted"]
        self.assertEqual(blocked["actual"]["status"], "BLOCKED")
        self.assertTrue(blocked["actual"]
                        ["task_closed_with_open_unaccounted_finding"])
        done = finding_rows()["drain_completes_and_replays"]
        self.assertEqual(done["actual"]["status"], "DRAINED")
        self.assertEqual(done["replay_writes"], 0)
        self.assertTrue(done["replay_concluded"])


class StageWakeTests(unittest.TestCase):
    def test_exit_invariant_accepts_correlated_target(self):
        row = stage_rows()["exit_check_correlated_target_ok"]
        self.assertTrue(row["ok"])
        self.assertEqual(row["violations"], [])

    def test_exit_invariant_blocks_underspecified_parked_target(self):
        row = stage_rows()["exit_check_underspecified_parked_blocked"]
        self.assertFalse(row["ok"])
        self.assertEqual(row["violations"][0]["violation"],
                         "PARKED_TARGET_UNDERSPECIFIED")
        self.assertIn("dependency", row["parked_missing"])
        self.assertIn("wake_boundary", row["parked_missing"])

    def test_exit_invariant_blocks_due_target_without_intent(self):
        row = stage_rows()["exit_check_due_target_blocked"]
        self.assertFalse(row["ok"])
        self.assertEqual(row["violations"][0]["violation"],
                         "DUE_TARGET_UNTRACKED")

    def test_parent_wake_failure_is_recovery_not_redispatch(self):
        row = stage_rows()["parent_wake_failure_recovery"]
        self.assertEqual(row["first_decision"], "WAKE_ONE")
        self.assertEqual(row["active_lead_decision"], "WAKE_REPAIR_REQUIRED")
        self.assertEqual(row["exhausted_decision"], "WAKE_EXHAUSTED")
        self.assertEqual(row["delivered_then_decision"], "NO_NEW_WAKE")
        self.assertTrue(row["combined_with_trigger"])
        self.assertEqual(row["triggers"], 1)

    def test_stage_completion_wake_is_idempotent(self):
        row = stage_rows()["stage_completion_wake_idempotent"]
        self.assertEqual(row["decision"], "NO_NEW_WAKE")
        self.assertEqual(row["duplicate_lead_stage_activation"], 0)


class FinalGateTests(unittest.TestCase):
    def test_matrix_reports_every_parent_gate(self):
        final = matrix()
        self.assertEqual([row["gate"] for row in final["parent_final_gate"]],
                         list(u11.PARENT_FINAL_GATE))
        self.assertEqual(len(final["parent_final_gate"]), 20)

    def test_matrix_reports_every_o2_gate(self):
        final = matrix()
        self.assertEqual([row["gate"] for row in final["o2_safety_gate"]],
                         list(u11.O2_SAFETY_GATES))
        self.assertEqual(len(final["o2_safety_gate"]), 12)

    def test_all_gates_and_counters_pass(self):
        final = matrix()
        failing = [row for row in final["parent_final_gate"]
                   + final["o2_safety_gate"] if row["status"] != "PASS"]
        self.assertEqual(failing, [])
        self.assertTrue(final["all_pass"])
        self.assertTrue(final["counters_zero"])
        self.assertEqual(final["replay_integrity"]["dispatch_failures"], [])
        self.assertEqual(final["replay_integrity"]["artifact_failures"], [])
        self.assertEqual(final["replay_integrity"]["finding_failures"], [])
        self.assertEqual(final["replay_integrity"]["stage_failures"], [])
        self.assertEqual(final["replay_integrity"]["topology_failures"], [])

    def test_o2_recovery_matrix_all_pass(self):
        result = run_all()
        self.assertTrue(result["o2_recovery_matrix"]["all_pass"])
        self.assertGreaterEqual(result["o2_recovery_matrix"]["cases"], 20)

    def test_capability_proof_caller_supplied_root(self):
        capability = run_all()["capability"]
        self.assertTrue(capability["caller_supplied"])
        self.assertTrue(capability["absolute_root"])
        self.assertTrue(capability["all_passed"])
        self.assertEqual(capability["observation_count"], 6)
        self.assertFalse(capability["production_root_selected"])

    def test_side_effect_audit_all_zero(self):
        audit = run_all()["side_effect_audit"]
        for key in ("live_mutations_or_triggers", "canonical_writes",
                    "product_repo_changes",
                    "predecessor_or_o2_history_rewritten", "u09_drift",
                    "unaccounted_findings", "live_review_or_qa_activation"):
            self.assertEqual(audit[key], 0, key)
        self.assertFalse(audit["production_ledger_root_selected_or_deployed"])
        self.assertFalse(audit["rerun_idempotency_claimed"])

    def test_compatibility_pins_reproduce(self):
        compat = run_all()["compatibility"]
        self.assertTrue(compat["all_reproduce"])
        self.assertEqual(
            compat["o2_bundle"]["digest"], compat["o2_bundle_expected"])
        self.assertEqual(
            compat["u09_bundle"]["digest"], compat["u09_bundle_expected"])
        self.assertIsNone(compat["u05_pins"]["feature_reviewer_resolves_to"])
        self.assertFalse(compat["u05_pins"]["old_05_package_accepted"])

    def test_residual_decisions_name_u12_inputs(self):
        decisions = run_all()["residual_decisions"]
        ids = {row["decision_id"] for row in decisions["decisions"]}
        self.assertIn("PRODUCTION_LEDGER_ROOT", ids)
        self.assertIn("RERUN_RECEIPT_CONTRACT", ids)
        self.assertFalse(decisions["production_ledger_root_selected_or_deployed"])

    def test_rerun_receipt_contract_makes_no_idempotency_claim(self):
        contract = run_all()["rerun_receipt_contract"]
        self.assertFalse(contract["idempotency_claimed"])
        self.assertTrue(contract["correlation_requires_trusted_run_listing"])
        self.assertEqual(len(contract["accepted_shapes"]), 3)
        for shape in contract["accepted_shapes"]:
            self.assertEqual(shape["parsed_run_id"], "run-id")


class BundleTests(unittest.TestCase):
    def test_committed_bundle_regenerates_byte_for_byte(self):
        committed = JOINT_DIR
        self.assertTrue((committed / "final-gate-matrix.json").is_file())
        with tempfile.TemporaryDirectory() as tmp:
            u11.evidence_bundle(tmp, generated_at=u11.CLOCK)
            generated = Path(tmp)
            for file in sorted(committed.rglob("*")):
                if not file.is_file() or "capture" in file.parts:
                    continue
                rel = file.relative_to(committed).as_posix()
                self.assertTrue((generated / rel).is_file(), rel)
                self.assertEqual(file.read_bytes(),
                                 (generated / rel).read_bytes(), rel)

    def test_bundle_regeneration_is_deterministic(self):
        with tempfile.TemporaryDirectory() as a, \
                tempfile.TemporaryDirectory() as b:
            u11.evidence_bundle(a, generated_at=u11.CLOCK)
            u11.evidence_bundle(b, generated_at=u11.CLOCK)
            pa, pb = Path(a), Path(b)
            files = sorted(str(p.relative_to(pa)) for p in pa.rglob("*")
                           if p.is_file())
            for rel in files:
                self.assertEqual((pa / rel).read_bytes(),
                                 (pb / rel).read_bytes(), rel)

    def test_zero_secret_scan_on_bundle(self):
        scan = u11.zero_secret_scan([JOINT_DIR])
        self.assertTrue(scan["clean"], scan["hits"])
        self.assertGreater(scan["files_scanned"], 10)

    def test_boundary_scan_clean(self):
        scan = u11.boundary_scan()
        self.assertTrue(scan["framework_scan_clean"])
        self.assertEqual(scan["forbidden_needles"], [])
        self.assertTrue(scan["clean"])

    def test_module_has_no_live_runner_or_network_surface(self):
        text = (TOOLS / "chandoff_joint.py").read_text(encoding="utf-8")
        for needle in ("sub" + "process", "re" + "quests", "url" + "lib",
                       "os." + "system", ("multica" + " issue"),
                       ("comment" + " add")):
            self.assertNotIn(needle, text, needle)

    def test_o2_sanitized_fixtures_consumed(self):
        for name in ("yzt-77", "yzt-78"):
            fixture = json.loads((O2_DIR / "fixtures" / f"{name}.json")
                                 .read_text(encoding="utf-8"))
            self.assertEqual(fixture["fixture"], name.upper())
            self.assertNotIn("mention://", json.dumps(fixture))


class ReportTests(unittest.TestCase):
    def test_report_exists_with_required_sections(self):
        self.assertTrue(REPORT.is_file())
        text = REPORT.read_text(encoding="utf-8")
        required = (
            "U11_JOINT_END_TO_END_REPLAY_REPORT",
            "Verdict", "Authorization", "Preconditions", "Accepted Inputs",
            "Exact Revisions", "Joint Topology", "Artifact",
            "O2 Dispatch", "Finding", "Stage", "Parent Final Gate",
            "Tests", "Side-Effect Audit", "Compatibility", "Deviations",
            "Finding Drain", "Risks", "Blockers",
            "Production Ledger Root Input", "Rerun Receipt Contract",
            "Recommended Next Decision", "Ready for Review",
        )
        for section in required:
            self.assertIn(section, text, section)

    def test_final_gate_matrix_digest_recorded_in_report(self):
        final = matrix()
        text = REPORT.read_text(encoding="utf-8")
        self.assertIn(final["evidence_digest"], text)


class PredecessorGuardTests(unittest.TestCase):
    def lf_digest(self, rel: str) -> str:
        data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def test_predecessor_pins_reproduce(self):
        pins = {
            "tools/chandoff_finding.py":
                "sha256:efa29010b5a0b07aa76a329c107b903a54f342e8fa88cc3e99a384a121273848",
            "tools/chandoff_dispatch.py":
                "sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa",
            "tools/chandoff_assignment.py":
                "sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129",
            "tools/chandoff_mention.py":
                "sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1",
            "tools/chandoff_fallback.py":
                "sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b",
        }
        for rel, pin in pins.items():
            self.assertEqual(self.lf_digest(rel), pin, rel)

    def test_t00_scan_clean(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})

    def test_u11_files_are_forward_only(self):
        final = matrix()
        self.assertTrue(final["all_pass"])
        added = [
            "tools/chandoff_joint.py",
            "tools/tests/test_joint_replay.py",
            "adapters/multica/U11_JOINT_END_TO_END_REPLAY_REPORT.md",
        ]
        for rel in added:
            self.assertTrue((ROOT / rel).is_file(), rel)


if __name__ == "__main__":
    unittest.main()
