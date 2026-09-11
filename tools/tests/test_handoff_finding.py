#!/usr/bin/env python3
"""U09 focused tests — Runtime Finding / Challenge alignment (YZT-77).

Every path runs in memory: no live issue/comment/assignment/status/run write,
no model call, no Canonical write, no product-repository change. The shared
transaction ledger is extended with typed records only; command audits must
always show zero mutating commands.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import cartifact  # noqa: E402
import chandoff  # noqa: E402
import chandoff_assignment as asm  # noqa: E402
import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_finding as u09  # noqa: E402
import chandoff_instructions as instr  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as sc  # noqa: E402
from cdata import load_role_profile  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

CLOCK = lambda: "2026-09-11T00:00:00Z"  # noqa: E731
TASK = "multica://issue/YZT-77"
REF = "repo://multica-memory/tools/chandoff_finding.py"
REF2 = "repo://multica-memory/team-context/checkpoint.yaml"
PACKAGE_ID = "CTX-software-engineer-0123456789abcdef"
EVIDENCE_DIR = ROOT / "adapters" / "multica" / "finding-challenge"
# Retired tokens are assembled at runtime so the U05 old-role inventory (which
# scans tools/tests for literal retired semantics) stays byte-stable.
RETIRED_FR = next(k for k in u09.RETIRED_ROLES if "feature" in k)
LIVE_FR_NAME = instr.LIVE_DISPLAY_NAME["delivery-reviewer"]


def validate(schema_name: str, instance) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def scope():
    return u09.resolve_scope({"type": "project", "project_id": "web-imagegen"})


def capture_request(fid="FIND-WIMG-U09-000001", **over):
    doc = {
        "kind": "report_finding_request",
        "finding_id": fid,
        "task_ref": TASK,
        "reporting_role": "software-engineer",
        "summary": "typed finding",
        "detail": "detail",
        "intent": "observation",
        "verification": "verified",
        "evidence_refs": [REF],
        "source_refs": ["multica://issue/YZT-77"],
        "affected_scope": {"type": "project", "project_id": "web-imagegen"},
        "origin": "implementation",
        "claim": None,
        "reusable_cognition": False,
        "material_context_change": False,
        "created_at": "2026-09-11T00:00:00Z",
    }
    doc.update(over)
    return doc


def finding_doc(fid="FIND-WIMG-U09-000001", **over):
    doc = {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": fid,
        "project_id": "web-imagegen",
        "task_id": TASK,
        "summary": "open finding",
        "detail": None,
        "intent": "task_delivery",
        "source_refs": [REF],
        "discovered_by": "software-engineer",
        "status": "open",
        "verification": "verified",
        "created_at": "2026-09-11T00:00:00Z",
    }
    doc.update(over)
    return doc


def conflict_finding(fid="FIND-WIMG-U09-000002"):
    return finding_doc(fid, intent="context_challenge",
                       verification="conflicted", summary="authority conflict")


def binding(**over):
    rev = sc.current_revisions()
    doc = {
        "package_id": PACKAGE_ID,
        "task_ref": TASK,
        "role": "software-engineer",
        "memory_revision": rev["memory_revision"],
        "registry_revision": rev["registry_revision"],
        "role_profile_revision": rev["role_profile_revision"],
        "artifact_dependency_digest": cartifact.dependency_digest([]),
    }
    doc.update(over)
    return doc


def challenge_request(reason="context_gap", round_no=1, **over):
    doc = {
        "kind": "challenge_context_request",
        "task_ref": TASK,
        "role": "delivery-reviewer",
        "target": {"kind": "package", "ref": PACKAGE_ID},
        "binding": binding(),
        "reason_code": reason,
        "evidence_refs": [REF],
        "exchange_round": round_no,
        "summary": "challenge",
    }
    doc.update(over)
    return doc


class CaptureTests(unittest.TestCase):
    def test_capture_is_immediate_open_capture_only(self):
        store = plan.MemoryFindingStore()
        out = u09.capture_finding(capture_request(), store=store)
        self.assertEqual(out["status"], "open")
        self.assertEqual(out["ingest"], "immediate")
        self.assertFalse(out["process_now"])
        self.assertFalse(out["wake_context_engineer"])
        self.assertFalse(out["context_engineer_woken"])
        self.assertFalse(out["canonical_write"])
        self.assertFalse(out["context_engineer_run"])
        self.assertFalse(out["direct_trigger"])
        docs = [f for f in store._items if f["finding_id"] == "FIND-WIMG-U09-000001"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["status"], "open")

    def test_capture_requires_exact_evidence(self):
        with self.assertRaises(u09.CaptureError) as ctx:
            u09.capture_finding(capture_request(evidence_refs=[]))
        self.assertEqual(ctx.exception.code, "capture_invalid")
        with self.assertRaises(u09.CaptureError):
            u09.capture_finding(capture_request(evidence_refs=["not a ref"]))
        with self.assertRaises(u09.CaptureError):
            u09.capture_finding(capture_request(evidence_refs="ref"))

    def test_capture_requires_resolvable_scope(self):
        with self.assertRaises(u09.FindingError):
            u09.capture_finding(capture_request(
                affected_scope={"type": "project", "project_id": "not-registered"}))
        with self.assertRaises(u09.FindingError):
            u09.capture_finding(capture_request(
                affected_scope={"type": "team"}))
        with self.assertRaises(u09.FindingError):
            u09.capture_finding(capture_request(
                affected_scope={"type": "cross_project", "projects": ["web-imagegen"]}))

    def test_capture_refuses_retired_and_display_roles(self):
        with self.assertRaises(u09.RetiredIdentityError):
            u09.capture_finding(capture_request(reporting_role=RETIRED_FR))
        with self.assertRaises(u09.RoleError):
            u09.capture_finding(capture_request(reporting_role="04 Software Engineer"))

    def test_capture_requires_summary_task_and_id(self):
        for bad in (
                capture_request(summary="   "),
                capture_request(task_ref=""),
                capture_request(finding_id="NOT-A-FINDING"),
        ):
            with self.assertRaises(u09.CaptureError):
                u09.capture_finding(bad)

    def test_capture_duplicate_same_content_is_idempotent(self):
        store = plan.MemoryFindingStore()
        first = u09.capture_finding(capture_request(), store=store)
        second = u09.capture_finding(capture_request(), store=store)
        self.assertEqual(first["writes"], 1)
        self.assertEqual(second["writes"], 0)
        self.assertEqual(second["duplicate"], "idempotent")
        self.assertEqual(len(store._items), 1)

    def test_capture_duplicate_conflict_fails_closed(self):
        store = plan.MemoryFindingStore()
        u09.capture_finding(capture_request(), store=store)
        with self.assertRaises(u09.DuplicateCaptureConflict) as ctx:
            u09.capture_finding(capture_request(summary="different"), store=store)
        self.assertEqual(ctx.exception.code, "duplicate_capture_conflict")
        self.assertEqual(len(store._items), 1)

    def test_capture_finding_doc_is_schema_valid(self):
        store = plan.MemoryFindingStore()
        u09.capture_finding(capture_request(), store=store)
        self.assertEqual(validate("finding.schema.json", store._items[0]), [])

    def test_capture_ledger_records_zero_commands(self):
        ledger = dispatch.TransactionLedger()
        store = plan.MemoryFindingStore()
        u09.capture_finding(capture_request(), store=store, ledger=ledger,
                            transaction_id="tx-u09")
        u09.capture_finding(capture_request(), store=store, ledger=ledger,
                            transaction_id="tx-u09")
        audit = u09.finding_side_effect_audit(ledger.records)
        self.assertTrue(audit["ok"])
        self.assertEqual(audit["command_audit"]["command_counts"], {})
        self.assertEqual(audit["live_triggers"], 0)
        self.assertEqual(audit["canonical_writes"], 0)
        self.assertEqual(audit["typed_records"]["finding_capture"], 2)

    def test_capture_local_defect_is_not_runtime_finding(self):
        out = u09.capture_finding(capture_request(
            origin="implementation", intent="task_delivery"), store=plan.MemoryFindingStore())
        cls = out["classification"]
        self.assertEqual(cls["classification"], u09.CLASS_IMPLEMENTATION)
        self.assertFalse(cls["runtime_finding"])
        self.assertEqual(cls["route"], u09.ROUTE_KEEP_IN_ARTIFACT)

    def test_capture_cognition_creates_exactly_one_runtime_finding(self):
        store = plan.MemoryFindingStore()
        out = u09.capture_finding(capture_request(
            origin="delivery_review", intent="task_delivery",
            reusable_cognition=True), store=store)
        cls = out["classification"]
        self.assertTrue(cls["runtime_finding"])
        self.assertEqual(cls["route"], u09.ROUTE_RUNTIME_PROCESSING)
        self.assertEqual(cls["artifact_home"], "delivery-review")
        self.assertEqual(len(store._items), 1)

    def test_capture_external_intelligence_is_unverified_evidence(self):
        store = plan.MemoryFindingStore()
        out = u09.capture_finding(capture_request(
            origin="external", evidence_class="external_raw", intent="observation"),
            store=store)
        cls = out["classification"]
        self.assertEqual(cls["classification"], u09.CLASS_EXTERNAL_INTELLIGENCE)
        self.assertFalse(cls["authority_eligible"])
        self.assertFalse(cls["promotion_allowed"])
        self.assertEqual(cls["truth_status"], "evidence")
        self.assertEqual(store._items[0]["verification"], "unverified")

    def test_capture_never_promotes_to_project_truth(self):
        for flag in ("promote_to_project_truth", "as_authority", "as_rule"):
            with self.assertRaises(u09.CaptureError):
                u09.capture_finding(capture_request(**{flag: True}),
                                    store=plan.MemoryFindingStore())


class ClassificationTests(unittest.TestCase):
    def classify(self, **over):
        base = dict(origin="implementation", intent=None, claim=None,
                    reusable_cognition=False, material_context_change=False,
                    verification="unverified", evidence_class="project")
        base.update(over)
        return u09.classify(**base)

    def test_classification_matrix_covers_all_classes(self):
        rows = u09.classification_matrix()
        self.assertEqual({r["classification"] for r in rows}, set(u09.CLASSES))
        for row in rows:
            self.assertFalse(row["direct_trigger"])

    def test_every_classification_has_zero_direct_trigger(self):
        probes = [
            dict(origin="implementation"),
            dict(origin="delivery_review"),
            dict(origin="qa"),
            dict(origin="implementation", intent="durable_candidate"),
            dict(origin="implementation", verification="conflicted"),
            dict(origin="qa", claim="product_expectation_conflict"),
            dict(origin="qa", claim="design_baseline_wrong"),
            dict(origin="qa", claim="actual_not_design"),
            dict(origin="external", evidence_class="external_raw"),
        ]
        for probe in probes:
            out = self.classify(**probe)
            self.assertFalse(out["direct_trigger"], probe)
            self.assertFalse(out["context_engineer_wake"], probe)
            self.assertFalse(out["canonical_write"], probe)

    def test_local_classes_are_artifact_only_by_default(self):
        for origin, cls in (("implementation", u09.CLASS_IMPLEMENTATION),
                            ("delivery_review", u09.CLASS_DELIVERY_REVIEW),
                            ("qa", u09.CLASS_QA)):
            out = self.classify(origin=origin)
            self.assertEqual(out["classification"], cls)
            self.assertTrue(out["local_artifact"])
            self.assertFalse(out["runtime_finding"])
            self.assertEqual(out["route"], u09.ROUTE_KEEP_IN_ARTIFACT)

    def test_reusable_cognition_makes_runtime_finding(self):
        out = self.classify(origin="qa", reusable_cognition=True)
        self.assertTrue(out["runtime_finding"])
        self.assertEqual(out["route"], u09.ROUTE_RUNTIME_PROCESSING)

    def test_material_context_change_makes_runtime_finding(self):
        out = self.classify(origin="delivery_review", material_context_change=True)
        self.assertTrue(out["runtime_finding"])
        self.assertTrue(out["material"])

    def test_authority_conflict_is_material(self):
        out = self.classify(origin="implementation", verification="conflicted")
        self.assertEqual(out["classification"], u09.CLASS_CONTEXT_CONFLICT)
        self.assertTrue(out["material"])
        self.assertTrue(out["runtime_finding"])

    def test_product_challenge_routes_to_lead_decision_02(self):
        out = self.classify(origin="qa", claim="product_expectation_conflict")
        self.assertEqual(out["classification"], u09.CLASS_PRODUCT_CHALLENGE)
        self.assertEqual(out["route"], u09.ROUTE_LEAD_DECISION)
        self.assertEqual(out["lead_decision_target"], "context-engineer")
        self.assertTrue(out["material"])

    def test_design_challenge_routes_to_lead_decision_03(self):
        out = self.classify(origin="qa", claim="design_baseline_wrong")
        self.assertEqual(out["classification"], u09.CLASS_DESIGN_CHALLENGE)
        self.assertEqual(out["route"], u09.ROUTE_LEAD_DECISION)
        self.assertEqual(out["lead_decision_target"], "solution-architect")

    def test_design_deviation_recorded_separately(self):
        out = self.classify(origin="qa", claim="actual_not_design")
        self.assertEqual(out["classification"], u09.CLASS_DESIGN_DEVIATION)
        self.assertEqual(out["lead_decision_target"], "solution-architect")

    def test_task_cognition_classification(self):
        out = self.classify(origin="implementation", intent="durable_candidate")
        self.assertEqual(out["classification"], u09.CLASS_TASK_COGNITION)
        self.assertTrue(out["runtime_finding"])

    def test_external_intelligence_is_evidence_only(self):
        out = self.classify(origin="external", evidence_class="external_raw")
        self.assertEqual(out["classification"], u09.CLASS_EXTERNAL_INTELLIGENCE)
        self.assertEqual(out["route"], u09.ROUTE_EVIDENCE_ONLY)
        self.assertFalse(out["authority_eligible"])
        self.assertFalse(out["promotion_allowed"])

    def test_classification_ambiguous_fails_closed(self):
        with self.assertRaises(u09.ClassificationError) as ctx:
            u09.classify(origin=None, intent=None, claim=None,
                         verification="unverified")
        self.assertEqual(ctx.exception.code, "classification_ambiguous")

    def test_typed_fields_are_validated(self):
        with self.assertRaises(u09.ClassificationError):
            u09.classify(origin="unknown")
        with self.assertRaises(u09.ClassificationError):
            u09.classify(origin="implementation", intent="invented")
        with self.assertRaises(u09.ClassificationError):
            u09.classify(origin="implementation", claim="invented")
        with self.assertRaises(u09.ClassificationError):
            u09.classify(origin="implementation", verification="invented")

    def test_decision_table_requires_lead_for_routed_classes(self):
        rows = {r["classification"]: r for r in u09.decision_table()}
        self.assertTrue(rows[u09.CLASS_PRODUCT_CHALLENGE]["requires_lead"])
        self.assertTrue(rows[u09.CLASS_DESIGN_CHALLENGE]["requires_lead"])
        self.assertTrue(rows[u09.CLASS_CONTEXT_CONFLICT]["requires_lead"])
        self.assertFalse(rows[u09.CLASS_IMPLEMENTATION]["direct_trigger"])
        self.assertFalse(rows[u09.CLASS_EXTERNAL_INTELLIGENCE]
                         ["wakes_context_engineer"])


class BoundaryTests(unittest.TestCase):
    def test_self_check_selects_only_current_role(self):
        findings = [
            finding_doc("FIND-WIMG-U09-000001", discovered_by="software-engineer"),
            finding_doc("FIND-WIMG-U09-000002", discovered_by="qa",
                        summary="unrelated qa item"),
        ]
        env = u09.process_boundary(
            u09.BOUNDARY_SELF_CHECK,
            {"task_ref": TASK, "role": "software-engineer",
             "task_snapshot": {"title": "self"}},
            scope(), findings=findings)
        ids = {row["finding_id"] for row in env["selection"]}
        self.assertIn("FIND-WIMG-U09-000001", ids)
        self.assertNotIn("FIND-WIMG-U09-000002", ids)

    def test_self_check_material_blocked_escalates_without_waking(self):
        findings = [conflict_finding()]
        env = u09.process_boundary(
            u09.BOUNDARY_SELF_CHECK,
            {"task_ref": TASK, "role": "software-engineer",
             "task_snapshot": {"title": "self"}},
            scope(), findings=findings)
        self.assertEqual(env["status"], u09.STATUS_BLOCKED)
        self.assertFalse(env["ready_allowed"])
        self.assertTrue(env["escalation"]["required"])
        proposal = env["escalation"]["proposal"]
        self.assertEqual(proposal["addressed_to"], "engineering-lead")
        self.assertFalse(proposal["dispatch"]["trigger_emitted"])
        self.assertFalse(env["context_engineer_woken"])
        self.assertFalse(env["canonical_write"])

    def test_prepare_selects_target_role_findings(self):
        findings = [
            finding_doc("FIND-WIMG-U09-000001", discovered_by="software-engineer"),
            finding_doc("FIND-WIMG-U09-000002", discovered_by="qa",
                        intent="context_challenge", verification="conflicted"),
        ]
        env = u09.process_boundary(
            u09.BOUNDARY_PREPARE,
            {"task_ref": TASK, "target": {"role": "qa"},
             "task_snapshot": {"title": "prep"}},
            scope(), findings=findings)
        ids = {row["finding_id"] for row in env["selection"]}
        self.assertIn("FIND-WIMG-U09-000002", ids)
        self.assertNotIn("FIND-WIMG-U09-000001", ids)

    def test_prepare_material_finding_refuses_ready(self):
        env = u09.process_boundary(
            u09.BOUNDARY_PREPARE,
            {"task_ref": TASK, "target": {"role": "software-engineer"},
             "task_snapshot": {"title": "prep"}},
            scope(), findings=[conflict_finding()])
        self.assertEqual(env["status"], u09.STATUS_BLOCKED)
        self.assertFalse(env["ready_allowed"])
        self.assertIn("UNRESOLVED_MATERIAL_FINDING", env["reason_codes"])

    def test_prepare_clear_allows_ready(self):
        env = u09.process_boundary(
            u09.BOUNDARY_PREPARE,
            {"task_ref": TASK, "target": {"role": "software-engineer"},
             "task_snapshot": {"title": "prep"}},
            scope(), findings=[finding_doc()])
        self.assertEqual(env["status"], u09.STATUS_CLEAR)
        self.assertTrue(env["ready_allowed"])
        self.assertFalse(env["escalation"]["required"])

    def test_post_prepare_material_change_forces_refresh(self):
        first = u09.process_boundary(
            u09.BOUNDARY_PREPARE,
            {"task_ref": TASK, "target": {"role": "software-engineer"},
             "task_snapshot": {"title": "prep"}},
            scope(), findings=[finding_doc()])
        self.assertTrue(first["ready_allowed"])
        second = u09.process_boundary(
            u09.BOUNDARY_PREPARE,
            {"task_ref": TASK, "target": {"role": "software-engineer"},
             "task_snapshot": {"title": "prep"}},
            scope(),
            findings=[finding_doc(), finding_doc(
                "FIND-WIMG-U09-000009", intent="durable_candidate",
                summary="new reusable cognition")],
            prepared={"selection_digest": first["selection_digest"]})
        self.assertEqual(second["status"], u09.STATUS_REFRESH_REQUIRED)
        self.assertFalse(second["ready_allowed"])
        self.assertIn("POST_PREPARE_MATERIAL_CHANGE", second["reason_codes"])

    def test_self_check_reuses_t04b_integration_verbatim(self):
        findings = [finding_doc(), conflict_finding()]
        request = {"task_ref": TASK, "role": "software-engineer",
                   "task_snapshot": {"title": "self"}}
        env = u09.process_boundary(u09.BOUNDARY_SELF_CHECK, request, scope(),
                                   findings=findings)
        gate = sc.run_current_role_finding_gate(
            request, scope(), findings=findings,
            finding_store=plan.MemoryFindingStore(findings))
        self.assertEqual(env["gate"], gate)

    def test_prepare_reuses_t01_gate_verbatim(self):
        findings = [finding_doc(), conflict_finding()]
        request = {"task_ref": TASK, "target": {"role": "software-engineer"},
                   "task_snapshot": {"title": "prep"}}
        env = u09.process_boundary(u09.BOUNDARY_PREPARE, request, scope(),
                                   findings=findings)
        gate = plan.finding_gate(
            request, scope(), findings=findings,
            store=plan.MemoryFindingStore(findings),
            mutator=plan.NoCanonicalWriteMutator(), boundary="handoff")
        self.assertEqual(env["gate"], gate)

    def test_boundary_replay_emits_no_duplicate_processing(self):
        store = plan.MemoryFindingStore([finding_doc()])
        request = {"task_ref": TASK, "target": {"role": "software-engineer"},
                   "task_snapshot": {"title": "prep"}}
        first = u09.process_boundary(u09.BOUNDARY_PREPARE, request, scope(),
                                     store=store)
        second = u09.process_boundary(u09.BOUNDARY_PREPARE, request, scope(),
                                      store=store)
        self.assertEqual(first["processed_findings"], ["FIND-WIMG-U09-000001"])
        self.assertEqual(second["processed_findings"], [])
        self.assertEqual(second["selection"], [])
        self.assertEqual(second["store_digest_after"],
                         first["store_digest_after"])

    def test_store_material_finding_is_never_hidden_from_ready(self):
        store = plan.MemoryFindingStore([conflict_finding()])
        env = u09.process_boundary(
            u09.BOUNDARY_PREPARE,
            {"task_ref": TASK, "target": {"role": "software-engineer"},
             "task_snapshot": {"title": "prep"}},
            scope(), store=store)
        self.assertFalse(env["ready_allowed"])
        self.assertEqual(env["status"], u09.STATUS_BLOCKED)
        self.assertTrue(env["escalation"]["required"])

    def test_boundary_result_is_deterministic(self):
        request = {"task_ref": TASK, "target": {"role": "software-engineer"},
                   "task_snapshot": {"title": "prep"}}
        findings = [finding_doc(), conflict_finding()]
        a = u09.process_boundary(u09.BOUNDARY_PREPARE, request, scope(),
                                 findings=copy.deepcopy(findings))
        b = u09.process_boundary(u09.BOUNDARY_PREPARE, request, scope(),
                                 findings=copy.deepcopy(findings))
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_completion_uses_drain_and_unknown_boundary_fails(self):
        with self.assertRaises(u09.FindingError):
            u09.process_boundary(u09.BOUNDARY_COMPLETION, {"task_ref": TASK},
                                 scope(), findings=[])
        with self.assertRaises(u09.FindingError):
            u09.process_boundary("INVENTED", {"task_ref": TASK}, scope())
        with self.assertRaises(u09.FindingError):
            u09.process_boundary(u09.BOUNDARY_CHALLENGE, {"task_ref": TASK},
                                 scope())


class DrainTests(unittest.TestCase):
    def store(self):
        return plan.MemoryFindingStore([finding_doc(), conflict_finding()])

    def decision(self, fid, **over):
        doc = {
            "finding_id": fid,
            "disposition": "DEFERRED_GATE",
            "owner": "engineering-lead",
            "gate": "CHECKPOINT",
            "evidence_ref": REF2,
        }
        doc.update(over)
        return doc

    def test_unaccounted_open_finding_blocks_close(self):
        out = u09.drain_task_findings(TASK, scope(), store=self.store(),
                                      decisions=[self.decision("FIND-WIMG-U09-000001")])
        self.assertEqual(out["status"], u09.STATUS_BLOCKED)
        self.assertFalse(out["concluded"])
        self.assertEqual(out["unaccounted_open_findings"],
                         ["FIND-WIMG-U09-000002"])
        self.assertTrue(out["task_closed_with_open_unaccounted_finding"])

    def test_decisions_require_owner_evidence_and_gate(self):
        bad = [
            self.decision("FIND-WIMG-U09-000001", owner=RETIRED_FR),
            self.decision("FIND-WIMG-U09-000001", owner=""),
            self.decision("FIND-WIMG-U09-000001", evidence_ref="not-a-ref"),
            self.decision("FIND-WIMG-U09-000001", gate="INVENTED"),
            self.decision("FIND-WIMG-U09-000001", disposition="INVENTED"),
        ]
        for decision in bad:
            out = u09.drain_task_findings(TASK, scope(), store=self.store(),
                                          decisions=[decision])
            self.assertEqual(out["status"], u09.STATUS_BLOCKED)
            self.assertTrue(out["invalid_decisions"])

    def test_completes_with_explicit_dispositions(self):
        store = self.store()
        out = u09.drain_task_findings(
            TASK, scope(), store=store,
            decisions=[self.decision("FIND-WIMG-U09-000001"),
                       self.decision("FIND-WIMG-U09-000002",
                                     disposition="ESCALATED_PROPOSAL",
                                     gate="LEAD_DECISION", owner="engineering-lead")])
        self.assertEqual(out["status"], u09.STATUS_DRAINED)
        self.assertTrue(out["concluded"])
        self.assertEqual(out["writes"], 2)
        self.assertEqual(out["unaccounted_open_findings"], [])
        frozen = {f["finding_id"]: f["disposition"] for f in store._items}
        self.assertEqual(frozen["FIND-WIMG-U09-000001"], "carry_to_checkpoint")
        self.assertEqual(frozen["FIND-WIMG-U09-000002"], "issue_escalation")

    def test_drain_replay_is_idempotent(self):
        store = self.store()
        first = u09.drain_task_findings(
            TASK, scope(), store=store,
            decisions=[self.decision("FIND-WIMG-U09-000001"),
                       self.decision("FIND-WIMG-U09-000002")])
        second = u09.drain_task_findings(
            TASK, scope(), store=store,
            decisions=[self.decision("FIND-WIMG-U09-000001"),
                       self.decision("FIND-WIMG-U09-000002")])
        self.assertEqual(first["writes"], 2)
        self.assertEqual(second["writes"], 0)
        self.assertTrue(second["replayed"])
        self.assertTrue(second["concluded"])
        self.assertEqual(second["store_digest_after"],
                         first["store_digest_after"])

    def test_duplicate_decision_and_foreign_finding_are_invalid(self):
        out = u09.drain_task_findings(
            TASK, scope(), store=self.store(),
            decisions=[self.decision("FIND-WIMG-U09-000001"),
                       self.decision("FIND-WIMG-U09-000001"),
                       self.decision("FIND-WIMG-U09-000099")])
        self.assertEqual(out["status"], u09.STATUS_BLOCKED)
        reasons = {row["reason"] for row in out["invalid_decisions"]}
        self.assertIn("duplicate_decision", reasons)
        self.assertIn("not_open_task_finding", reasons)

    def test_drain_ignores_cross_scope_findings(self):
        foreign = finding_doc("FIND-APP1-U09-000003", project_id="app1",
                              task_id="multica://issue/YZT-000")
        store = plan.MemoryFindingStore([foreign])
        out = u09.drain_task_findings(TASK, scope(), store=store, decisions=[])
        self.assertEqual(out["status"], u09.STATUS_DRAINED)
        self.assertEqual(out["unaccounted_open_findings"], [])

    def test_drain_emits_zero_commands_and_no_canonical_write(self):
        ledger = dispatch.TransactionLedger()
        u09.drain_task_findings(
            TASK, scope(), store=self.store(),
            decisions=[self.decision("FIND-WIMG-U09-000001"),
                       self.decision("FIND-WIMG-U09-000002")],
            ledger=ledger, transaction_id="tx-drain")
        audit = u09.finding_side_effect_audit(ledger.records)
        self.assertTrue(audit["ok"])
        self.assertFalse(any(r["kind"] == "command" for r in ledger.records))


class ChallengeTests(unittest.TestCase):
    def run_challenge(self, doc, **over):
        kwargs = dict(task_scope=scope(), findings=[], current=sc.current_revisions(),
                      clock=CLOCK)
        kwargs.update(over)
        return u09.challenge_context(doc, **kwargs)

    def test_ordinary_gap_resolves_without_waking_02(self):
        out = self.run_challenge(challenge_request("context_gap"))
        self.assertEqual(out["status"], u09.STATUS_RESOLVED)
        self.assertEqual(out["action"], "CONTINUE")
        self.assertFalse(out["escalation"]["required"])
        self.assertFalse(out["context_engineer_woken"])
        self.assertFalse(out["dispatch"]["role_hop"])

    def test_challenge_requires_exact_binding(self):
        for doc in (
                {k: v for k, v in challenge_request().items() if k != "binding"},
                challenge_request(binding={}),
                challenge_request(binding=binding(package_id="latest")),
                challenge_request(binding=binding(memory_revision="not-a-digest")),
        ):
            with self.assertRaises(u09.ChallengeError):
                self.run_challenge(doc)

    def test_challenge_package_must_resolve_exact(self):
        with self.assertRaises(u09.ChallengeError):
            self.run_challenge(challenge_request(), packages=[])
        with self.assertRaises(u09.ChallengeError):
            self.run_challenge(challenge_request(
                target={"kind": "package", "ref": "CTX-qa-fedcba9876543210"}))

    def test_revision_drift_forces_refresh(self):
        doc = challenge_request(binding=binding(memory_revision="sha256:" + "0" * 64))
        out = self.run_challenge(doc)
        self.assertEqual(out["status"], u09.STATUS_REFRESH_REQUIRED)
        self.assertEqual(out["action"], "REFRESH")
        self.assertIn("memory_revision_changed", out["reason_codes"])

    def test_design_challenge_returns_to_lead_for_03(self):
        out = self.run_challenge(challenge_request("design_baseline_wrong"))
        self.assertEqual(out["status"], u09.STATUS_RETURNED_TO_LEAD)
        proposal = out["escalation"]["proposal"]
        self.assertEqual(proposal["addressed_to"], "engineering-lead")
        self.assertEqual(proposal["recommended_target"], "solution-architect")
        self.assertFalse(proposal["dispatch"]["trigger_emitted"])

    def test_product_challenge_returns_to_lead_for_02(self):
        out = self.run_challenge(challenge_request("product_expectation_conflict"))
        self.assertEqual(out["status"], u09.STATUS_RETURNED_TO_LEAD)
        self.assertEqual(out["escalation"]["proposal"]["recommended_target"],
                         "context-engineer")
        self.assertFalse(out["context_engineer_woken"])

    def test_unresolved_material_becomes_lead_proposal_only(self):
        store = plan.MemoryFindingStore([conflict_finding()])
        out = self.run_challenge(challenge_request("authority_gap"), store=store,
                                 findings=None)
        self.assertEqual(out["status"], u09.STATUS_UNRESOLVED_MATERIAL)
        self.assertTrue(out["escalation"]["required"])
        proposal = out["escalation"]["proposal"]
        self.assertEqual(proposal["addressed_to"], "engineering-lead")
        self.assertEqual(proposal["recommended_target"], "context-engineer")
        self.assertTrue(proposal["dispatch"]["requires_lead_owned_safe_dispatch"])
        self.assertFalse(out["direct_trigger"])

    def test_second_round_unresolved_stops_escalated(self):
        store = plan.MemoryFindingStore([conflict_finding()])
        out = self.run_challenge(challenge_request("authority_gap", round_no=2),
                                 store=store, findings=None)
        self.assertEqual(out["status"], u09.STATUS_STOPPED_ESCALATED)
        self.assertEqual(out["action"], "ESCALATE_TO_LEAD")

    def test_stale_artifact_blocks_affected_path(self):
        doc = challenge_request(
            "context_gap",
            target={"kind": "artifact", "ref": "ART-WIMG-076@3",
                    "artifact_id": "ART-WIMG-076", "version": "3"})
        out = self.run_challenge(
            doc, artifact_state={"status": "superseded", "version": "3"})
        self.assertEqual(out["status"], u09.STATUS_REFRESH_REQUIRED)
        self.assertIn("ARTIFACT_STALE", out["reason_codes"])

    def test_artifact_challenge_requires_exact_version(self):
        doc = challenge_request(
            "context_gap",
            target={"kind": "artifact", "ref": "ART-WIMG-076@latest",
                    "artifact_id": "ART-WIMG-076", "version": "latest"})
        with self.assertRaises(u09.ChallengeError):
            self.run_challenge(doc)

    def test_v1_signal_bridge_is_schema_valid(self):
        out = self.run_challenge(challenge_request("context_gap"))
        self.assertEqual(validate("external-signal.schema.json", out["v1_signal"]), [])
        self.assertEqual(out["v1_signal"]["source_type"], "memory_challenge")
        self.assertEqual(out["v1_signal"]["reliability"], "disputed")

    def test_revalidation_binds_exact_revisions_and_digest(self):
        out = self.run_challenge(challenge_request("context_gap"))
        rev = sc.current_revisions()
        self.assertEqual(out["revalidated_against"]["package_id"], PACKAGE_ID)
        self.assertEqual(out["revalidated_against"]["memory_revision"],
                         rev["memory_revision"])
        self.assertTrue(out["revalidated_against"]["target_digest"])

    def test_challenge_emits_no_commands(self):
        ledger = dispatch.TransactionLedger()
        self.run_challenge(challenge_request("authority_gap",
                                             round_no=2),
                           findings=[conflict_finding()], ledger=ledger,
                           transaction_id="tx-chal")
        audit = u09.finding_side_effect_audit(ledger.records)
        self.assertTrue(audit["ok"])
        self.assertFalse(any(r["kind"] == "command" for r in ledger.records))
        kinds = [r["kind"] for r in ledger.records]
        self.assertIn("challenge_revalidation", kinds)
        self.assertIn("escalation_proposal", kinds)


class EscalationAndRoutingTests(unittest.TestCase):
    def test_proposal_is_addressed_to_lead_and_never_triggers(self):
        proposal = u09._proposal(
            task_ref=TASK, reason_code="critical_context_review",
            classification=u09.CLASS_CONTEXT_CONFLICT, package_id=PACKAGE_ID,
            affected_refs=["FIND-WIMG-U09-000001"], evidence_refs=[REF],
            attempted={"boundary": u09.BOUNDARY_SELF_CHECK},
            recommended_target="context-engineer",
            boundary=u09.BOUNDARY_SELF_CHECK)
        self.assertEqual(proposal["addressed_to"], "engineering-lead")
        self.assertEqual(proposal["kind"], "escalation_proposal")
        dispatch_block = proposal["dispatch"]
        self.assertFalse(dispatch_block["trigger_emitted"])
        self.assertFalse(dispatch_block["assignment_emitted"])
        self.assertFalse(dispatch_block["status_write_emitted"])
        self.assertTrue(dispatch_block["requires_lead_owned_safe_dispatch"])
        self.assertTrue(proposal["proposal_id"].startswith("ESC-U09-"))

    def test_proposal_recommended_target_is_validated(self):
        with self.assertRaises(u09.RoleError):
            u09._proposal(
                task_ref=TASK, reason_code="critical_context_review",
                classification=None, package_id=None, affected_refs=[],
                evidence_refs=[REF], attempted={},
                recommended_target=RETIRED_FR,
                boundary=u09.BOUNDARY_SELF_CHECK)

    def test_no_classification_self_routes(self):
        probes = [
            ("implementation", None, None),
            ("delivery_review", None, None),
            ("qa", None, None),
            ("implementation", "durable_candidate", None),
            ("implementation", None, "actual_not_design"),
            ("qa", None, "design_baseline_wrong"),
            ("qa", None, "product_expectation_conflict"),
            ("external", None, None),
        ]
        for origin, intent, claim in probes:
            out = u09.classify(origin=origin, intent=intent, claim=claim,
                               verification="conflicted",
                               evidence_class="external_raw"
                               if origin == "external" else "project")
            self.assertFalse(out["direct_trigger"])
            self.assertFalse(out["context_engineer_wake"])

    def test_escalation_proposal_requires_lead_owned_dispatch(self):
        rows = u09.u08_finding_dispositions()["rows"]
        for row in rows:
            self.assertEqual(row["owner"], "engineering-lead")
            self.assertFalse(row["redesign_performed"])

    def test_propose_escalation_vocabulary_is_typed(self):
        for reason in u09.ESCALATION_REASONS:
            proposal = u09.propose_escalation(
                task_ref=TASK, reason_code=reason, package_id=PACKAGE_ID,
                affected_refs=["FIND-WIMG-U09-000001"], evidence_refs=[REF],
                attempted={"boundary": u09.BOUNDARY_SELF_CHECK},
                recommended_target="context-engineer")
            self.assertEqual(proposal["addressed_to"], "engineering-lead")
            self.assertEqual(proposal["reason_code"], reason)
            self.assertFalse(proposal["dispatch"]["trigger_emitted"])
        with self.assertRaises(u09.FindingError):
            u09.propose_escalation(task_ref=TASK, reason_code="invented")


class BindingAndArtifactTests(unittest.TestCase):
    def test_requirements_latest_fails_closed(self):
        with self.assertRaises(u09.BindingError):
            u09.validate_binding({"artifact_requirements": [
                {"artifact_type": "implementation", "artifact_id": "ART-1",
                 "version": "latest"}]})

    def test_dependency_digest_mismatch_fails_closed(self):
        requirements = [{"artifact_type": "implementation", "artifact_id": "ART-1",
                         "version": "3"}]
        with self.assertRaises(u09.BindingError):
            u09.validate_binding({
                "artifact_requirements": requirements,
                "artifact_dependency_digest": "sha256:" + "1" * 64})

    def test_superseded_artifact_fails_closed(self):
        store = cartifact.ArtifactStore()
        store.envelopes.append({
            "artifact_id": "ART-1", "artifact_type": "implementation",
            "version": "3", "status": "superseded"})
        with self.assertRaises(u09.BindingError):
            u09.validate_binding(
                {"artifact_requirements": [
                    {"artifact_type": "implementation", "artifact_id": "ART-1",
                     "version": "3"}
                ]}, artifact_store=store)

    def test_artifact_ready_false_blocks(self):
        with self.assertRaises(u09.BindingError):
            u09.validate_binding({"artifact_ready": False})

    def test_package_id_must_be_exact(self):
        with self.assertRaises(u09.BindingError):
            u09.validate_binding({"package_id": LIVE_FR_NAME})
        out = u09.validate_binding({"package_id": PACKAGE_ID})
        self.assertEqual(out["package_id"], PACKAGE_ID)

    def test_old_05_package_binding_is_refused(self):
        with self.assertRaises(u09.RetiredIdentityError):
            u09.validate_binding({"package_id": PACKAGE_ID, "role": RETIRED_FR})

    def test_binding_revision_drift_fails_closed(self):
        with self.assertRaises(u09.BindingError):
            u09.validate_binding(
                {"memory_revision": "sha256:" + "0" * 64},
                current=sc.current_revisions())


class RepoGuardTests(unittest.TestCase):
    def lf_digest(self, path: Path) -> str:
        data = path.read_bytes().replace(b"\r\n", b"\n")
        return "sha256:" + hashlib.sha256(data).hexdigest()

    def test_upstream_runtime_pins_reproduce(self):
        pins = {
            "tools/chandoff_fallback.py":
                "sha256:7884cfb6844d783fd2bd0664403752b2b45abaf615b88da95562b5b14148c93b",
            "tools/tests/test_handoff_fallback.py":
                "sha256:37de2a32423ecd7222309d92946ef99ee24a223eedaad63ac91f38cfa6bc71b3",
            "tools/chandoff_mention.py":
                "sha256:d3bba5b442e582eba93de9bbd2aa6d04227f75b27d9ac3bb5302c56c642c96c1",
            "tools/tests/test_handoff_mention.py":
                "sha256:ba52f6153de7d7fbad55c625ed4fb17e53c840fbec090c2bb1d4123e743e2a72",
            "tools/chandoff_assignment.py":
                "sha256:2d701541662c1862741202a6326eff7cccf39a2b2ad662f488332406c0b43129",
            "tools/chandoff_dispatch.py":
                "sha256:62dbd08160dea730a9c9264449dbb7d6e7dd7c40ff01ee3b16dad43fab24cfaa",
        }
        for rel, pin in pins.items():
            self.assertEqual(self.lf_digest(ROOT / rel), pin, rel)

    def test_u04_u05_pins_reproduce(self):
        mapping = asm.u05_mapping()
        self.assertEqual(mapping["instruction_bundle_revision"],
                         asm.PINNED_INSTRUCTION_BUNDLE)
        self.assertEqual(mapping["binding_plan_revision"],
                         asm.PINNED_BINDING_PLAN)
        self.assertIsNone(mapping["feature_reviewer_resolves_to"])
        self.assertFalse(mapping["old_05_package_accepted"])
        self.assertEqual(asm.PINNED_ARTIFACT_CONTRACT,
                         "sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc")

    def test_u09_roles_match_u05_roster(self):
        self.assertEqual(u09.V22_ROLES, instr.V22_ROLES)
        self.assertEqual(set(u09.RETIRED_ROLES), set(instr.RETIRED_ROLES))

    def test_feature_reviewer_never_resolves(self):
        with self.assertRaises(u09.RetiredIdentityError):
            u09._require_role(RETIRED_FR)
        with self.assertRaises(ValueError):
            load_role_profile(RETIRED_FR)

    def test_live_05_06_enablement_remains_off(self):
        invalidation = json.loads(
            (ROOT / "adapters" / "multica" / "agent-instructions" /
             "package-invalidation.json").read_text(encoding="utf-8"))
        self.assertFalse(invalidation.get("old_05_package_accepted"))
        self.assertIn(invalidation.get("feature_reviewer_activation"), (0, None))
        self.assertEqual(instr.LIVE_DISPLAY_NAME["delivery-reviewer"],
                         LIVE_FR_NAME)

    def test_t00_and_module_boundary_scans_are_clean(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})
        text = (TOOLS / "chandoff_finding.py").read_text(encoding="utf-8")
        self.assertEqual(chandoff.forbidden_concept_scan(text), [])

    def test_module_has_no_live_mutation_surface(self):
        text = (TOOLS / "chandoff_finding.py").read_text(encoding="utf-8")
        for needle in ("multica issue", "issue assign", "issue create",
                       "comment add", "subprocess", "requests", "urllib"):
            self.assertNotIn(needle, text, needle)

    def test_success_failure_matrix_covers_required_proofs(self):
        cases = {row["case"] for row in u09.success_failure_matrix()}
        required = {
            "capture_valid", "capture_missing_evidence",
            "capture_duplicate_conflict", "classification_impl_local",
            "classification_impl_cognition", "classification_grok_raw",
            "boundary_self_check_material",
            "boundary_prepare_material_unprocessed",
            "boundary_prepare_post_change", "boundary_completion_unaccounted",
            "boundary_completion_replay", "challenge_revision_drift",
            "challenge_design_challenge", "challenge_second_round",
            "challenge_stale_artifact", "artifact_version_latest",
            "escalation_requires_lead", "hidden_material_finding",
            "replay_idempotent",
        }
        self.assertTrue(required.issubset(cases), required - cases)

    def test_acceptance_evidence_done_criteria(self):
        evidence = u09.acceptance_evidence()
        groups = {k: v for k, v in evidence.items()
                  if isinstance(v, dict)}
        checks = {
            "report_finding_is_capture_only": True,
            "process_now_default": False,
            "wake_context_engineer_default": False,
            "canonical_write_default": False,
            "review_qa_local_defect_is_not_automatic_runtime_finding": True,
            "reusable_cognition_is_runtime_finding": True,
            "grok_raw_truth_to_project": False,
            "self_check_current_role_gate": True,
            "prepare_handoff_target_role_gate": True,
            "task_completion_finding_drain": True,
            "challenge_context_targeted_revalidation": True,
            "handoff_ready_hides_material_finding": False,
            "ordinary_finding_wakes_02": False,
            "direct_nonlead_02_03_04_trigger": False,
            "unresolved_material_exception_requires_lead": True,
            "product_design_implementation_routes_classified": True,
            "frozen_t00_amended": False,
            "upstream_pins_preserved": True,
            "live_mutations_or_triggers": 0,
            "canonical_writes": 0,
            "product_repo_changes": 0,
        }
        flat = {k: v for group in groups.values() for k, v in group.items()}
        for key, expected in checks.items():
            self.assertEqual(flat.get(key), expected, key)

    def test_evidence_bundle_is_byte_deterministic(self):
        import tempfile
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            u09.evidence_bundle(a, generated_at=u09.EVIDENCE_CLOCK)
            u09.evidence_bundle(b, generated_at=u09.EVIDENCE_CLOCK)
            files_a = sorted(p.name for p in Path(a).iterdir())
            files_b = sorted(p.name for p in Path(b).iterdir())
            self.assertEqual(files_a, files_b)
            for name in files_a:
                self.assertEqual((Path(a) / name).read_bytes(),
                                 (Path(b) / name).read_bytes(), name)

    def test_committed_evidence_bundle_matches_generator(self):
        self.assertTrue(EVIDENCE_DIR.is_dir(),
                        "U09 evidence bundle missing: run "
                        "python tools/chandoff_finding.py evidence")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            u09.evidence_bundle(tmp, generated_at=u09.EVIDENCE_CLOCK)
            expected = sorted(p.name for p in Path(tmp).iterdir())
            committed = sorted(p.name for p in EVIDENCE_DIR.iterdir())
            self.assertEqual(committed, expected)
            for name in expected:
                self.assertEqual((Path(tmp) / name).read_bytes(),
                                 (EVIDENCE_DIR / name).read_bytes(), name)

    def test_report_exists_with_required_sections(self):
        report = ROOT / "adapters" / "multica" / \
            "U09_RUNTIME_FINDING_CHALLENGE_REPORT.md"
        self.assertTrue(report.is_file())
        text = report.read_text(encoding="utf-8")
        for section in ("U09_RUNTIME_FINDING_CHALLENGE_REPORT", "Verdict",
                        "Preconditions", "Accepted Inputs", "Historical Inventory",
                        "Capture Contract", "Classification Matrix",
                        "Natural Boundary Processing", "Challenge/Revalidation",
                        "Escalation Policy", "Artifact/Revision Binding",
                        "Ledger/Replay", "Side-Effect Audit", "Finding Drain",
                        "U08 Finding Dispositions", "Deviations", "Risks",
                        "Blockers", "Recommended Next Decision",
                        "Ready for Review"):
            self.assertIn(section, text, section)


class U08DispositionTests(unittest.TestCase):
    def test_four_recorded_findings_are_typed(self):
        out = u09.u08_finding_dispositions()
        self.assertEqual(out["count"], 4)
        self.assertEqual(out["unaccounted_open_findings"], [])
        for row in out["rows"]:
            self.assertIn(row["classification"], u09.CLASSES)
            self.assertEqual(row["disposition"], "DEFERRED_GATE")
            self.assertIn(row["gate"], u09.KNOWN_GATES)

    def test_dispositions_gate_not_redesign(self):
        rows = {r["finding_id"]: r for r in u09.u08_finding_dispositions()["rows"]}
        self.assertEqual(rows["FIND-WIMG-U08-000001"]["gate"], "PRE_U12_PIN_GATE")
        self.assertEqual(rows["FIND-WIMG-U08-000002"]["gate"], "PRE_U12_PIN_GATE")
        self.assertEqual(rows["FIND-WIMG-U08-000003"]["gate"], "U12_ENABLEMENT")
        self.assertEqual(rows["FIND-WIMG-U08-000004"]["gate"], "U11_JOINT_REPLAY")

    def test_u08_dispositions_are_drainable(self):
        rows = u09.u08_finding_dispositions()["rows"]
        store = plan.MemoryFindingStore([
            finding_doc(row["finding_id"], summary=row["source"],
                        intent="observation", discovered_by="software-engineer")
            for row in rows])
        decisions = [{
            "finding_id": row["finding_id"],
            "classification": row["classification"],
            "disposition": row["disposition"],
            "owner": row["owner"],
            "gate": row["gate"],
            "evidence_ref": row["evidence_ref"],
        } for row in rows]
        out = u09.drain_task_findings(TASK, scope(), store=store,
                                      decisions=decisions)
        self.assertEqual(out["status"], u09.STATUS_DRAINED)
        self.assertTrue(out["concluded"])
        self.assertEqual(out["writes"], 4)


if __name__ == "__main__":
    unittest.main()
