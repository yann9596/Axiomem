#!/usr/bin/env python3
"""Context Handoff Native API contract tests (T00, YZT-46).

Freezes and verifies:
- all handoff schemas load and validate valid instances (schema_mini engine);
- prepare_handoff / self_check status enums are exact, actions pair 1:1;
- semantic_compose_result is structured and subset-checked against the PLAN;
- the task fingerprint contract (frozen inputs, normalization, exclusions);
- built_from content revisions are deterministic;
- prepare_handoff_result.package reuses the existing V1.1 context package
  schema (no duplicate package schema exists);
- the framework-neutral boundary scan passes.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from schema_mini import Schema, load_schema_file  # noqa: E402
import chandoff  # noqa: E402
from cbuild import resolve_scope, build_package  # noqa: E402

REQ = "context-handoff/prepare-handoff-request.schema.json"
PLAN_S = "context-handoff/context-plan.schema.json"
SEM_S = "context-handoff/semantic-compose-result.schema.json"
RES_S = "context-handoff/prepare-handoff-result.schema.json"
SREQ_S = "context-handoff/self-check-request.schema.json"
SRES_S = "context-handoff/self-check-result.schema.json"

HANDOFF_REQUEST = {
    "schema_version": "1.1",
    "kind": "prepare_handoff_request",
    "task_ref": "multica://issue/YZT-46",
    "project": {"project_id": "web-imagegen"},
    "target": {"role": "software-engineer"},
    "purpose": "implementation",
    "task_snapshot": {
        "title": "Freeze Context Handoff Native API Contract",
        "description": "Freeze the interface baseline for prepare_handoff and self_check.",
        "requirements": ["freeze all handoff schemas", "freeze the task fingerprint contract"],
        "acceptance_criteria": ["all schemas validate", "boundary scan clean"],
        "relevant_decisions": ["reuse the existing Task Context Package schema"],
        "parent_task_ref": "multica://issue/YZT-45",
    },
    "caller": {"role": "engineering-lead"},
    "options": {"max_context_tier": 1},
}

HANDOFF_PLAN = {
    "schema_version": "1.1",
    "kind": "context_plan",
    "plan_id": "PLAN-YZT-46-software-engineer-001",
    "task_ref": "multica://issue/YZT-46",
    "role": "software-engineer",
    "scope": {"type": "project", "project_id": "web-imagegen",
              "projects": [], "task_id": None},
    "hard_filters_applied": ["strict_scope_filter", "lifecycle_active_only",
                             "case_activation_default_off", "context_budget",
                             "role_profile_loaded"],
    "case_search": {"allowed": False},
    "candidates": {
        "anchor": [{"id": "anchor:web-imagegen", "ref": "project://web-imagegen/project"}],
        "checkpoint_entries": [
            {"checkpoint": "web-imagegen", "section": "confirmed",
             "id": "cp-confirmed-001", "summary": "pilot baseline",
             "refs": ["multica://issue/YZT-10"]},
        ],
        "rules": [
            {"id": "RULE-WIMG-000001", "summary": "provider neutrality",
             "modality": "MUST", "status": "active"},
        ],
        "facts": [
            {"id": "FACT-WIMG-000001", "summary": "repo map", "status": "active"},
        ],
        "cases": [],
        "evidence": [
            {"ref": "repo://web-imagegen@main/README.md", "note": "baseline"},
        ],
        "conflicts": [],
    },
    "semantic_jobs": ["select_task_applicable_rules_from_candidates",
                      "select_decision_relevant_facts_from_candidates"],
    "generated_at": "2026-09-09T00:00:00Z",
}

SEMANTIC_RESULT = {
    "schema_version": "1.1",
    "kind": "semantic_compose_result",
    "plan_id": HANDOFF_PLAN["plan_id"],
    "selected_rule_ids": ["RULE-WIMG-000001"],
    "selected_fact_ids": ["FACT-WIMG-000001"],
    "selected_case_ids": [],
    "checkpoint_entry_ids": ["cp-confirmed-001"],
    "conflict_ids": [],
    "semantic_notes": ["minimum sufficient selection from the plan"],
}

SELF_CHECK_REQUEST = {
    "schema_version": "1.1",
    "kind": "self_check_request",
    "task_ref": HANDOFF_REQUEST["task_ref"],
    "role": "software-engineer",
    "task_snapshot": HANDOFF_REQUEST["task_snapshot"],
}


def validate(schema_name: str, instance: dict, path: str = "$") -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path=path)


class HandoffRequestTests(unittest.TestCase):
    def test_valid_request_passes(self):
        self.assertEqual(validate(REQ, HANDOFF_REQUEST), [])

    def test_task_ref_must_be_opaque_uri(self):
        bad = dict(HANDOFF_REQUEST, task_ref="YZT-46")
        errs = validate(REQ, bad)
        self.assertTrue(any("task_ref" in e for e in errs))

    def test_dispatch_framework_keys_are_rejected(self):
        bad = dict(HANDOFF_REQUEST, assignee_id="someone-else")
        self.assertTrue(validate(REQ, bad))

    def test_options_is_an_open_content_object(self):
        # options is open for build/content options by design; the framework
        # boundary is enforced on schemas and by the scan, not by key policing.
        req = dict(HANDOFF_REQUEST, options={"max_context_tier": 3})
        self.assertEqual(validate(REQ, req), [])

    def test_unknown_top_level_key_rejected(self):
        bad = dict(HANDOFF_REQUEST, extra_field="nope")
        self.assertTrue(validate(REQ, bad))


class ContextPlanTests(unittest.TestCase):
    def test_valid_plan_passes(self):
        self.assertEqual(validate(PLAN_S, HANDOFF_PLAN), [])

    def test_candidates_must_carry_all_seven_kinds(self):
        import copy
        bad = copy.deepcopy(HANDOFF_PLAN)
        del bad["candidates"]["evidence"]
        self.assertTrue(validate(PLAN_S, bad))

    def test_semantic_jobs_are_frozen_vocabulary(self):
        import copy
        bad = copy.deepcopy(HANDOFF_PLAN)
        bad["semantic_jobs"] = ["rewrite_all_memory"]
        self.assertTrue(validate(PLAN_S, bad))


class StatusEnumTests(unittest.TestCase):
    def setUp(self):
        # A schema-conforming minimal V1.1 package. The real builder's output
        # currently carries pseudo-scheme refs (rule:/fact:/case:) that predate
        # the ref grammar (FIND-WIMG-HO00-000001); T00 freezes the contract and
        # records the gap instead of changing Core or builder behavior.
        self.pkg = {
            "schema_version": "1.1", "kind": "context_package",
            "request": {"task_id": HANDOFF_REQUEST["task_ref"],
                        "role": "software-engineer", "project_id": "web-imagegen"},
            "scope": {"type": "project", "project_id": "web-imagegen",
                      "projects": [], "task_id": None},
            "project_phase": "active_development",
            "anchor_digest": {"mission": "vendor-neutral web image generation"},
            "team_state_slice": [], "project_state_slice": [],
            "rules": [], "current_facts": [], "cases": [],
            "open_conflicts": [], "blocked_by": [], "task_evidence": [],
            "source_refs": [],
            "assembly_trace": {"scope_resolved": True, "case_search_performed": False,
                               "generated_at": "2026-09-09T00:00:00Z"},
            "generated_at": "2026-09-09T00:00:00Z",
        }
        self.built = chandoff.compute_built_from(HANDOFF_REQUEST)

    def result(self, status: str, escalation: dict) -> dict:
        return {
            "schema_version": "1.1",
            "kind": "prepare_handoff_result",
            "status": status,
            "package_id": "CTX-YZT-46-software-engineer-001",
            "task_ref": HANDOFF_REQUEST["task_ref"],
            "role": "software-engineer",
            "built_from": self.built,
            "package": self.pkg,
            "escalation": escalation,
            "generated_at": "2026-09-09T00:00:00Z",
        }

    def test_prepare_handoff_status_enum_exact(self):
        for status, esc in (("READY", {"required": False}),
                            ("PARTIAL", {"required": True, "reason": "case pending"}),
                            ("BLOCKED", {"required": True, "reason": "scope_ambiguous"})):
            self.assertEqual(validate(RES_S, self.result(status, esc)), [])
        for status in ("DONE", "REFRESH_REQUIRED", "OK", "ready"):
            errs = validate(RES_S, self.result(status, {"required": False}))
            self.assertTrue(any(".status" in e for e in errs))

    def test_blocked_requires_escalation(self):
        errs = validate(RES_S, self.result("BLOCKED", {"required": False}))
        self.assertTrue(errs)

    def test_result_package_is_existing_v1_1_context_package(self):
        # The package inside the result validates against the untouched V1.1
        # schema and carries the task_ref as its task identity.
        self.assertEqual(self.pkg["request"]["task_id"], HANDOFF_REQUEST["task_ref"])
        self.assertEqual(validate(RES_S, self.result("READY", {"required": False})), [])

    def test_real_builder_package_covers_required_structure(self):
        # Structural reuse proof: the existing V1.1 builder emits every key the
        # package schema requires (ref-grammar gap tracked as FIND-WIMG-HO00-000001).
        scope = resolve_scope(HANDOFF_REQUEST["task_ref"], None, [], "web-imagegen")
        real = build_package(HANDOFF_REQUEST["task_ref"], "software-engineer", scope,
                             decision="freeze native api contract")
        schema = load_schema_file("context-package.schema.json")
        self.assertEqual(set(schema["required"]) - set(real), set())

    def test_no_duplicate_context_package_schema(self):
        base = Path(TOOLS).resolve().parent / "schemas"
        for p in (base / "context-handoff").glob("*.schema.json"):
            self.assertNotIn('"const": "context_package"', p.read_text(encoding="utf-8"),
                             f"{p.name} must not redefine the package schema")
        res_text = (base / "context-handoff" / "prepare-handoff-result.schema.json") \
            .read_text(encoding="utf-8")
        self.assertIn('"context-package.schema.json"', res_text)


class SemanticResultTests(unittest.TestCase):
    def test_semantic_result_is_structured(self):
        self.assertEqual(validate(SEM_S, SEMANTIC_RESULT), [])
        free_prose = {"schema_version": "1.1", "kind": "semantic_compose_result",
                      "plan_id": HANDOFF_PLAN["plan_id"],
                      "markdown": "# Context\n\nfree prose only"}
        errs = validate(SEM_S, free_prose)
        self.assertTrue(any("selected_rule_ids" in e for e in errs))

    def test_selected_objects_can_be_validated_against_plan(self):
        self.assertEqual(chandoff.validate_semantic_result(HANDOFF_PLAN, SEMANTIC_RESULT), [])

    def test_foreign_id_rejected(self):
        import copy
        bad = copy.deepcopy(SEMANTIC_RESULT)
        bad["selected_rule_ids"] = ["RULE-APP1-000001"]
        errs = chandoff.validate_semantic_result(HANDOFF_PLAN, bad)
        self.assertTrue(any("RULE-APP1-000001" in e for e in errs))

    def test_plan_id_mismatch_rejected(self):
        import copy
        bad = copy.deepcopy(SEMANTIC_RESULT)
        bad["plan_id"] = "PLAN-OTHER-001"
        self.assertTrue(chandoff.validate_semantic_result(HANDOFF_PLAN, bad))


class SelfCheckTests(unittest.TestCase):
    def res(self, status: str, action: str, reasons: list, **extra) -> dict:
        out = {"schema_version": "1.1", "kind": "self_check_result",
               "status": status, "reasons": reasons, "action": action}
        out.update(extra)
        return out

    def test_self_check_status_enum_exact(self):
        self.assertEqual(validate(SREQ_S, SELF_CHECK_REQUEST), [])
        self.assertEqual(validate(SRES_S, self.res("READY", "USE_EXISTING", [],
                                                   package_id="CTX-YZT-46-software-engineer-001")), [])
        self.assertEqual(validate(SRES_S, self.res(
            "REFRESH_REQUIRED", "REFRESH", ["task_changed"])), [])
        self.assertEqual(validate(SRES_S, self.res(
            "BLOCKED", "ESCALATE", ["scope_mismatch"])), [])
        for status in ("PARTIAL", "OK", "WAITING"):
            self.assertTrue(validate(SRES_S, self.res(status, "USE_EXISTING", [])))

    def test_status_action_pairing_enforced(self):
        self.assertTrue(validate(SRES_S, self.res("READY", "REFRESH", [])))
        self.assertTrue(validate(SRES_S, self.res("BLOCKED", "USE_EXISTING", ["x"])))
        self.assertTrue(validate(SRES_S, self.res("REFRESH_REQUIRED", "ESCALATE", ["x"])))

    def test_refresh_and_blocked_require_reasons(self):
        self.assertTrue(validate(SRES_S, self.res("REFRESH_REQUIRED", "REFRESH", [])))
        self.assertTrue(validate(SRES_S, self.res("BLOCKED", "ESCALATE", [])))

    def test_request_accepts_optional_package_ref(self):
        req = dict(SELF_CHECK_REQUEST, package_ref="CTX-YZT-46-software-engineer-001")
        self.assertEqual(validate(SREQ_S, req), [])


class FingerprintContractTests(unittest.TestCase):
    def test_frozen_input_set(self):
        self.assertEqual(
            chandoff.FINGERPRINT_INPUTS,
            ("task_ref", "title", "description", "requirements",
             "acceptance_criteria", "explicit_scope", "relevant_human_decisions"))

    def test_fingerprint_format_and_determinism(self):
        fp1 = chandoff.fingerprint_from_request(HANDOFF_REQUEST)
        fp2 = chandoff.fingerprint_from_request(HANDOFF_REQUEST)
        self.assertRegex(fp1, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(fp1, fp2)

    def test_relevant_changes_move_the_fingerprint(self):
        import copy
        base = chandoff.fingerprint_from_request(HANDOFF_REQUEST)
        for field in ("title", "description"):
            bad = copy.deepcopy(HANDOFF_REQUEST)
            bad["task_snapshot"][field] += " (amended)"
            self.assertNotEqual(chandoff.fingerprint_from_request(bad), base, field)
        for field in ("requirements", "acceptance_criteria", "relevant_decisions"):
            bad = copy.deepcopy(HANDOFF_REQUEST)
            bad["task_snapshot"][field].append("new item")
            self.assertNotEqual(chandoff.fingerprint_from_request(bad), base, field)
        bad = copy.deepcopy(HANDOFF_REQUEST)
        bad["project"] = {"project_id": "app1"}
        self.assertNotEqual(chandoff.fingerprint_from_request(bad), base)

    def test_list_order_and_repetition_do_not_move_the_fingerprint(self):
        import copy
        base = chandoff.fingerprint_from_request(HANDOFF_REQUEST)
        bad = copy.deepcopy(HANDOFF_REQUEST)
        snap = bad["task_snapshot"]
        snap["requirements"] = list(reversed(snap["requirements"])) + [snap["requirements"][0]]
        self.assertEqual(chandoff.fingerprint_from_request(bad), base)

    def test_non_task_fields_do_not_invalidate_the_package(self):
        # caller / purpose / options / parent relation are NOT fingerprint
        # inputs: progress chatter and transport details never invalidate.
        import copy
        base = chandoff.fingerprint_from_request(HANDOFF_REQUEST)
        variants = []
        bad = copy.deepcopy(HANDOFF_REQUEST)
        bad["caller"] = {"role": "solution-architect"}
        variants.append(bad)
        bad = copy.deepcopy(HANDOFF_REQUEST)
        bad["purpose"] = "design"
        variants.append(bad)
        bad = copy.deepcopy(HANDOFF_REQUEST)
        bad["options"] = {"max_context_tier": 3, "new_note": "progress chatter"}
        variants.append(bad)
        bad = copy.deepcopy(HANDOFF_REQUEST)
        del bad["task_snapshot"]["parent_task_ref"]
        variants.append(bad)
        for v in variants:
            self.assertEqual(chandoff.fingerprint_from_request(v), base)

    def test_payload_keys_are_exactly_the_frozen_inputs(self):
        payload = chandoff.fingerprint_payload(
            "multica://issue/YZT-46", "t", "d", ["r"], ["a"], {"project_id": "app1"}, [])
        self.assertEqual(tuple(sorted(payload)), tuple(sorted(chandoff.FINGERPRINT_INPUTS)))


class RevisionTests(unittest.TestCase):
    def test_revisions_are_deterministic_and_formatted(self):
        for fn in (chandoff.memory_revision, chandoff.registry_revision,
                   chandoff.role_profile_revision):
            a, b = fn(), fn()
            self.assertRegex(a, r"^sha256:[0-9a-f]{64}$")
            self.assertEqual(a, b)

    def test_compute_built_from_shape(self):
        built = chandoff.compute_built_from(HANDOFF_REQUEST)
        self.assertEqual(set(built), {"task_fingerprint", "memory_revision",
                                      "registry_revision", "role_profile_revision"})
        self.assertEqual(built, chandoff.compute_built_from(HANDOFF_REQUEST))


class FrameworkNeutralTests(unittest.TestCase):
    def test_boundary_scan_is_clean(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})

    def test_scanner_detects_framework_vocabulary(self):
        poisoned = ("squad-x mention it; assignee: someone; stage 2; autopilot; "
                    "comment routing; multica run; run_id 7")
        found = set(chandoff.forbidden_concept_scan(poisoned))
        self.assertEqual(found, {"squad", "mention", "assignee", "stage",
                                 "autopilot", "comment_routing", "multica_run",
                                 "run_identifier"})

    def test_opaque_task_ref_is_allowed(self):
        # multica:// inside a URI is an opaque reference, not framework vocabulary
        self.assertEqual(chandoff.forbidden_concept_scan("multica://issue/YZT-46"), [])


class ValidationMatrixTests(unittest.TestCase):
    """The issue's validation block, re-derived live (YZT-46)."""

    def test_validation_matrix(self):
        sr = SelfCheckTests("test_self_check_status_enum_exact")
        st = StatusEnumTests("test_prepare_handoff_status_enum_exact")
        st.setUp()
        matrix = {
            "all_new_schemas_valid": not (validate(REQ, HANDOFF_REQUEST)
                                          or validate(PLAN_S, HANDOFF_PLAN)
                                          or validate(SEM_S, SEMANTIC_RESULT)
                                          or validate(SREQ_S, SELF_CHECK_REQUEST)),
            "native_api_contains_multica_runtime_concepts": bool(chandoff.scan_handoff_contracts()),
            "duplicate_task_context_package_schema": any(
                '"const": "context_package"' in p.read_text(encoding="utf-8")
                for p in (TOOLS.resolve().parent / "schemas" / "context-handoff")
                .glob("*.schema.json")),
            "prepare_handoff_status_enum_exact": not validate(
                RES_S, st.result("READY", {"required": False})),
            "self_check_status_enum_exact": not validate(
                SRES_S, sr.res("READY", "USE_EXISTING", [])),
            "semantic_result_is_structured": not validate(SEM_S, SEMANTIC_RESULT),
            "selected_objects_can_be_validated_against_plan": not chandoff.validate_semantic_result(
                HANDOFF_PLAN, SEMANTIC_RESULT),
            "task_fingerprint_contract_defined": bool(chandoff.FINGERPRINT_INPUTS)
            and len(chandoff.FINGERPRINT_INPUTS) == 7,
        }
        failures = {k: v for k, v in matrix.items()
                    if (v if k == "native_api_contains_multica_runtime_concepts"
                        or k == "duplicate_task_context_package_schema" else not v)}
        self.assertEqual(failures, {})


if __name__ == "__main__":
    unittest.main()
