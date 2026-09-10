#!/usr/bin/env python3
"""T07 focused tests — multica-context-handoff skill + deterministic pipeline.

All Multica interaction is mocked/offline: captured CLI fixtures, canned
runners, injected seams. No network, no model call, no Canonical write, no
Memory rebuild, no issue lifecycle write, no workspace skill import, no
Agent binding, no downstream run trigger. The tests verify behavior of the
pipeline driver and the frozen T00-T06 boundaries it chains, not wording.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
SKILL = REPO / "skills" / "multica-context-handoff"

_spec = importlib.util.spec_from_file_location(
    "handoff_pipeline", SKILL / "scripts" / "handoff_pipeline.py")
pipeline = importlib.util.module_from_spec(_spec)
sys.dont_write_bytecode = True
_spec.loader.exec_module(pipeline)
sys.dont_write_bytecode = False

sys.path.insert(0, str(TOOLS))

import chandoff_compose as compose  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_note as note  # noqa: E402
import chandoff_plan as plan  # noqa: E402
from cdata import load_all_docs  # noqa: E402

FIXTURES = TOOLS / "fixtures" / "adapter"
ISSUE_FILE = FIXTURES / "issue_get_yzt58.json"
PARENT_FILE = FIXTURES / "issue_get_parent_yzt39.json"
TASK_REF = "multica://issue/YZT-58"
OTHER_TASK_REF = "multica://issue/YZT-99"
CLOCK = lambda: "2026-09-09T16:00:00Z"  # noqa: E731
PREPARED_BY = "8bc546ab-ffd8-4aa6-ad30-58583346c065"
ADD_RESPONSE = {
    "id": "01a0-new-0000000000000000000a",
    "created_at": "2026-09-09T16:00:01Z",
    "parent_id": None,
    "content": "/note",
}


def ns(**kwargs) -> argparse.Namespace:
    kwargs.setdefault("repo", str(REPO))
    kwargs.setdefault("executable", "multica")
    return argparse.Namespace(**kwargs)


def prepare_ns(out_dir=None, **over) -> argparse.Namespace:
    base = dict(
        issue="YZT-58", target_role="software-engineer",
        caller_role="context-engineer", purpose="implementation",
        project_id="web-imagegen", project_map=None,
        decision_comment=None, decision_marker=None, options_json=None,
        issue_file=str(ISSUE_FILE), parent_file=str(PARENT_FILE),
        thread_file=None, out_dir=out_dir)
    base.update(over)
    return ns(**base)


def finalize_ns(plan_file, result_file, request_file, out_dir=None,
                repairs_used=0) -> argparse.Namespace:
    return ns(plan_file=str(plan_file), result_file=str(result_file),
              request_file=str(request_file), repairs_used=repairs_used,
              out_dir=out_dir)


def selfcheck_ns(out_dir=None, **over) -> argparse.Namespace:
    base = dict(
        issue="YZT-58", task_ref=None, role=None, request_file=None,
        request_from=None, package_ref=None, envelope_file=[], store=None,
        out_dir=out_dir)
    base.update(over)
    return ns(**base)


def publish_ns(result_file, **over) -> argparse.Namespace:
    base = dict(
        issue="YZT-58", result_file=str(result_file), prepared_by=PREPARED_BY,
        prepared_at=None, parent=None, allow_partial=False, dry_run=False,
        authorize_publish=False)
    base.update(over)
    return ns(**base)


def material_finding(task_ref: str = TASK_REF) -> dict:
    return {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": "FIND-WIMG-T07-000001",
        "project_id": "web-imagegen",
        "task_id": task_ref,
        "summary": "provider routing ownership must become a canonical Rule",
        "detail": "material durable candidate that cannot be auto-processed",
        "intent": "durable_candidate",
        "source_refs": ["repo://web-imagegen@main/README.md"],
        "discovered_by": "qa",
        "status": "open",
        "verification": "unverified",
        "created_at": "2026-09-09T00:00:00Z",
    }


def blocked_plan_fn():
    def _plan(request):
        finding = material_finding(request["task_ref"])
        return plan.prepare_handoff_plan(
            request, findings=[copy.deepcopy(finding)],
            store=plan.MemoryFindingStore([copy.deepcopy(finding)]))
    return _plan


def conflicted_plan_envelope(scratch: Path) -> dict:
    docs = copy.deepcopy(load_all_docs())
    target = next(d for d in docs if d.get("id") == "RULE-WIMG-000001")
    target["status"] = "review_needed"
    request = json.loads(
        (scratch / "request.json").read_text(encoding="utf-8"))
    env = plan.prepare_handoff_plan(request, docs=docs)
    assert env["status"] == "PLAN_READY", env
    path = scratch / "plan-conflicted.json"
    path.write_text(json.dumps(env, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return env


def note_cli(comments=None, add_response=None, refuse_on_write=False):
    comments = comments if comments is not None else []

    def runner(argv):
        # runner argv includes the executable at [0]; cli.commands entries
        # recorded by NoteCli._run do not.
        if argv[1:4] == ["issue", "comment", "list"]:
            return 0, json.dumps(comments), ""
        if argv[1:] == ["version", "--output", "json"]:
            return 0, json.dumps({"version": "v0.4.41"}), ""
        if argv[1:4] == ["issue", "comment", "add"]:
            if refuse_on_write:
                raise AssertionError("unauthorized write attempted")
            return 0, json.dumps(add_response or ADD_RESPONSE), ""
        raise AssertionError(f"unexpected argv {argv}")

    return note.NoteCli(executable="multica", runner=runner)


def empty_finding_store():
    return plan.MemoryFindingStore([])


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


class ChainMixin:
    """Shared READY chain artifacts (offline snapshot -> PLAN -> READY)."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="t07-chain-"))
        payload, code = pipeline.run_prepare(
            prepare_ns(out_dir=str(cls.scratch)))
        assert payload["ok"] and payload["status"] == "PLAN_READY", payload
        assert code == pipeline.READY_EXIT
        cls.prepare = payload
        cls.plan_envelope = json.loads(
            (cls.scratch / "plan-envelope.json").read_text(encoding="utf-8"))
        cls.request = json.loads(
            (cls.scratch / "request.json").read_text(encoding="utf-8"))
        cls.compose = compose.subset_result(cls.plan_envelope["plan"])
        write_json(cls.scratch / "compose-ready.json", cls.compose)
        payload, code = pipeline.run_finalize(finalize_ns(
            cls.scratch / "plan-envelope.json",
            cls.scratch / "compose-ready.json",
            cls.scratch / "request.json", out_dir=str(cls.scratch)))
        assert payload["ok"] and payload["status"] == "READY", payload
        cls.finalize = payload
        cls.envelope = json.loads(
            (cls.scratch / "result.json").read_text(encoding="utf-8"))
        cls._conflicted = None

    @classmethod
    def conflicted_plan(cls) -> dict:
        if cls._conflicted is None:
            cls._conflicted = conflicted_plan_envelope(cls.scratch)
        return cls._conflicted


class PipelineGuaranteeTests(unittest.TestCase):
    def test_guarantees_block_is_complete_and_zero(self):
        expected = {
            "pipeline_llm_calls", "canonical_writes", "memory_rebuilds",
            "issue_lifecycle_writes", "assignments", "mentions",
            "downstream_run_triggers", "frozen_schema_changes",
            "workspace_skill_imports", "agent_skill_bindings",
        }
        self.assertEqual(set(pipeline.GUARANTEES), expected)
        self.assertTrue(all(v == 0 for v in pipeline.GUARANTEES.values()))


class PrepareHandoffTests(ChainMixin, unittest.TestCase):
    def test_prepare_ready_exposes_plan_boundary_only(self):
        payload = self.prepare
        self.assertEqual(payload["stage"], "plan")
        self.assertEqual(payload["status"], "PLAN_READY")
        self.assertTrue(payload["plan_id"].startswith("PLAN-software-engineer"))
        self.assertTrue(payload["semantic_jobs"])
        self.assertLessEqual(set(payload["semantic_jobs"]),
                             set(plan.SEMANTIC_JOBS_ORDER))
        self.assertFalse(payload["case_search_allowed"])
        self.assertFalse(payload["context_engineer_woken"])
        self.assertTrue(payload["candidates"]["rules"] > 0)
        for name in ("request.json", "plan-envelope.json"):
            self.assertTrue((self.scratch / name).is_file())

    def test_prepare_blocked_stops_before_compose_finalize_publish(self):
        payload, code = pipeline.run_prepare(
            prepare_ns(), plan_fn=blocked_plan_fn())
        self.assertEqual(code, pipeline.BLOCKED_EXIT)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["blocked"])
        self.assertEqual(payload["status"], "BLOCKED")
        gate = payload["finding_gate"]
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertTrue(gate["blocked_findings"])
        self.assertTrue(payload["escalation"]["required"])
        self.assertNotIn("plan_id", payload)
        self.assertNotIn("next", payload)
        names = {p.name for p in Path(payload["artifacts"]["dir"]).iterdir()}
        self.assertNotIn("result.json", names)
        self.assertNotIn("compose-validation.json", names)

    def test_snapshot_fails_closed_without_project_mapping(self):
        payload, code = pipeline.run_prepare(prepare_ns(project_id=None))
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "snapshot")
        self.assertTrue(payload["error"]["message"])


class FinalizeHandoffTests(ChainMixin, unittest.TestCase):
    def _bad_result(self, name: str, mutate) -> Path:
        bad = copy.deepcopy(self.compose)
        mutate(bad)
        return write_json(self.scratch / name, bad)

    def test_finalize_ready_policy(self):
        payload = self.finalize
        self.assertEqual(payload["status"], "READY")
        self.assertTrue(payload["publish"]["publishable"])
        self.assertTrue(payload["publish"]["normal_ready"])
        self.assertEqual(payload["gaps"], [])
        self.assertFalse(payload["escalation"]["required"])
        self.assertEqual(payload["task_ref"], TASK_REF)
        self.assertEqual(payload["role"], "software-engineer")
        self.assertTrue(payload["package_id"].startswith("CTX-software-engineer"))

    def test_expansion_rejected_and_not_repairable_by_new_context(self):
        bad = self._bad_result(
            "compose-expand.json",
            lambda r: r["selected_rule_ids"].append("RULE-WIMG-999999"))
        payload, code = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json", bad,
            self.scratch / "request.json"))
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["status"], "REJECTED")
        codes = {e["code"] for e in payload["errors"]}
        self.assertIn("ADD_MEMORY_NOT_IN_PLAN", codes)
        self.assertIn("SELECTED_ID_NOT_IN_PLAN", codes)
        # "repair by fetching new context": re-snapshot deterministically
        # yields the same PLAN, and the same expansion is rejected again.
        again, _ = pipeline.run_prepare(prepare_ns())
        self.assertEqual(again["plan_id"],
                         self.plan_envelope["plan"]["plan_id"])
        payload2, _ = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json",
            self._bad_result("compose-expand2.json",
                             lambda r: r["selected_rule_ids"].append(
                                 "RULE-WIMG-999999")),
            self.scratch / "request.json"))
        self.assertEqual(payload2["status"], "REJECTED")
        self.assertEqual([e["code"] for e in payload["errors"]],
                         [e["code"] for e in payload2["errors"]])

    def test_scope_and_authority_expansion_rejected(self):
        # the frozen result schema itself cannot carry forbidden attempts;
        # the executor reports them in the bounded attempt wrapper and the
        # validator rejects every one of them.
        wrapper = {
            "result": copy.deepcopy(self.compose),
            "attempts": ["expand_scope", "invent_authority"],
            "scope": {"type": "team", "project_id": None, "projects": [],
                      "task_id": None},
            "invented_rules": ["RULE-WIMG-888888"],
        }
        payload, _ = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json",
            write_json(self.scratch / "compose-authority.json", wrapper),
            self.scratch / "request.json"))
        codes = {e["code"] for e in payload["errors"]}
        self.assertIn("EXPAND_SCOPE", codes)
        self.assertIn("INVENT_AUTHORITY", codes)

    def test_case_expansion_rejected_when_gate_closed(self):
        payload, _ = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json",
            self._bad_result(
                "compose-case.json",
                lambda r: r.__setitem__("selected_case_ids",
                                        ["CASE-WIMG-000001"])),
            self.scratch / "request.json"))
        codes = {e["code"] for e in payload["errors"]}
        self.assertIn("CASE_GATE_OVERRIDE", codes)
        self.assertIn("ADD_MEMORY_NOT_IN_PLAN", codes)

    def test_malformed_compose_one_repair_then_stop(self):
        bad = self._bad_result(
            "compose-bad.json",
            lambda r: r.__setitem__("selected_fact_ids", ["FACT-WIMG-999999"]))
        first, code1 = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json", bad,
            self.scratch / "request.json", repairs_used=0))
        self.assertEqual(code1, pipeline.BOUNDED_EXIT)
        self.assertFalse(first["repair_exhausted"])
        self.assertTrue(first["repair_allowed"])
        repaired, code2 = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json",
            self.scratch / "compose-ready.json",
            self.scratch / "request.json", repairs_used=1))
        self.assertEqual(code2, pipeline.READY_EXIT)
        self.assertEqual(repaired["status"], "READY")
        plan_ids = {r["id"] for r
                    in self.plan_envelope["plan"]["candidates"]["rules"]}
        self.assertLessEqual(set(self.compose["selected_rule_ids"]), plan_ids)
        payload, code3 = pipeline.run_finalize(finalize_ns(
            self.scratch / "plan-envelope.json",
            self._bad_result("compose-bad2.json",
                             lambda r: r.__setitem__(
                                 "selected_fact_ids", ["FACT-WIMG-999999"])),
            self.scratch / "request.json", repairs_used=1))
        self.assertEqual(code3, pipeline.STOP_EXIT)
        self.assertTrue(payload["repair_exhausted"])
        self.assertFalse(payload["repair_allowed"])
        self.assertTrue(payload["stop"])

    def test_partial_preserves_gaps_and_never_normal_ready(self):
        plan_env = self.conflicted_plan()
        self.assertEqual(plan_env["status"], "PLAN_READY")
        self.assertTrue(plan_env["plan"]["candidates"]["conflicts"])
        plan_path = write_json(self.scratch / "plan-conflicted.json", plan_env)
        payload, code = pipeline.run_finalize(finalize_ns(
            plan_path,
            write_json(self.scratch / "compose-for-conflicted.json",
                       compose.subset_result(plan_env["plan"])),
            self.scratch / "request.json",
            out_dir=str(self.scratch)))
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["status"], "PARTIAL")
        self.assertFalse(payload["publish"]["normal_ready"])
        self.assertEqual(payload["publish"]["publishable"],
                         "only_with_explicit_caller_authorization")
        self.assertTrue(payload["publish"]["gaps_preserved"])
        conf_ids = {c["id"] for c in plan_env["plan"]["candidates"]["conflicts"]}
        self.assertTrue(conf_ids)
        # every open conflict stays visible in the gaps, alongside any other
        # blocked_by entries (e.g. review-state objects)
        self.assertLessEqual(conf_ids, set(payload["gaps"]))
        self.assertIn("review:RULE-WIMG-000001", payload["gaps"])
        # unauthorized PARTIAL publication is refused before any CLI call
        partial_file = self.scratch / "result.json"
        ref_env = json.loads(partial_file.read_text(encoding="utf-8"))
        self.assertEqual(ref_env["status"], "PARTIAL")
        cli = note_cli(refuse_on_write=True)
        res, rc = pipeline.run_publish(
            publish_ns(partial_file, authorize_publish=True),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.BOUNDED_EXIT)
        self.assertEqual(res["error"]["code"], "partial_requires_authorization")
        self.assertEqual(len(cli.commands), 0)
        # explicitly authorized PARTIAL publication goes through T06 as-is
        res, rc = pipeline.run_publish(
            publish_ns(partial_file, authorize_publish=True,
                       allow_partial=True),
            note_cli_factory=lambda: note_cli())
        self.assertEqual(rc, pipeline.READY_EXIT)
        self.assertTrue(res["published"])
        self.assertEqual(res["trace"]["guarantees"]["mentions"], 0)


class BlockedPublishTests(ChainMixin, unittest.TestCase):
    def test_blocked_result_never_reaches_the_publisher(self):
        blocked_envelope = copy.deepcopy(self.envelope)
        blocked_envelope["status"] = "BLOCKED"
        blocked_envelope["escalation"] = {
            "required": True, "reason": "unresolved_material_finding"}
        path = write_json(self.scratch / "blocked-result.json",
                          blocked_envelope)
        cli = note_cli(refuse_on_write=True)
        res, rc = pipeline.run_publish(
            publish_ns(path, authorize_publish=True),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.BLOCKED_EXIT)
        self.assertEqual(res["error"]["code"], "blocked_never_publishable")
        self.assertEqual(len(cli.commands), 0)
        # and T06 itself refuses a BLOCKED result before any subprocess
        with self.assertRaises(note.AdapterError):
            note.render_note_record(blocked_envelope, prepared_by=PREPARED_BY,
                                    clock=CLOCK)


class SelfCheckTests(ChainMixin, unittest.TestCase):
    def _request_file(self, name: str, task_ref: str, role: str,
                      **extra) -> Path:
        doc = {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": task_ref, "role": role,
            "task_snapshot": self.request["task_snapshot"],
        }
        doc.update(extra)
        return write_json(self.scratch / name, doc)

    def test_ready_uses_discovered_package_and_wakes_nobody(self):
        req_file = self._request_file("self-check-request.json", TASK_REF,
                                      "software-engineer")
        payload, code = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(req_file),
            envelope_file=[str(self.scratch / "result.json")],
            out_dir=str(self.scratch)), finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.READY_EXIT)
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["action"], "USE_EXISTING")
        self.assertEqual(payload["reasons"], [])
        self.assertEqual(payload["package_id"], self.finalize["package_id"])
        self.assertFalse(payload["context_engineer_woken"])
        self.assertEqual(payload["consequential_work"], "allowed")

    def test_missing_package_requires_refresh_bound_to_task_and_role(self):
        req_file = self._request_file("self-check-request-missing.json",
                                      OTHER_TASK_REF, "qa")
        payload, code = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(req_file)), finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["status"], "REFRESH_REQUIRED")
        self.assertIn("package_missing", payload["reasons"])
        self.assertEqual(payload["consequential_work"],
                         "stopped_until_refreshed_ready")
        self.assertEqual(payload["refresh"]["task_ref"], OTHER_TASK_REF)
        self.assertEqual(payload["refresh"]["target_role"], "qa")
        self.assertFalse(payload["context_engineer_woken"])

    def test_task_change_and_role_mismatch_are_detected(self):
        req_file = self._request_file(
            "self-check-request-mismatch.json", OTHER_TASK_REF, "qa",
            package_ref=self.finalize["package_id"])
        payload, _ = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(req_file),
            envelope_file=[str(self.scratch / "result.json")]),
            finding_store=empty_finding_store())
        self.assertEqual(payload["status"], "REFRESH_REQUIRED")
        self.assertIn("task_changed", payload["reasons"])
        self.assertIn("role_mismatch", payload["reasons"])

    def test_stale_package_detected_via_revision_change(self):
        stale = copy.deepcopy(self.envelope)
        stale["built_from"]["memory_revision"] = "sha256:" + "0" * 64
        path = write_json(self.scratch / "stale-result.json", stale)
        req_file = self._request_file("self-check-request-stale.json",
                                      TASK_REF, "software-engineer")
        payload, _ = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(req_file), envelope_file=[str(path)]),
            finding_store=empty_finding_store())
        self.assertEqual(payload["status"], "REFRESH_REQUIRED")
        self.assertIn("memory_revision_changed", payload["reasons"])
        self.assertEqual(payload["consequential_work"],
                         "stopped_until_refreshed_ready")

    def _comments(self, newest_invalid: bool) -> list:
        body, _record = note.render_note_record(
            self.envelope, prepared_by=PREPARED_BY, prepared_at=CLOCK(),
            clock=CLOCK)
        old = {"id": "01a0-old-0000000000000000000c", "content": body,
               "created_at": "2026-09-09T15:00:01Z", "parent_id": None}
        if not newest_invalid:
            return [old]
        corrupted = body.replace('"escalation"', '"escalation_broken"', 1)
        new = {"id": "01a0-new-0000000000000000000c", "content": corrupted,
               "created_at": "2026-09-09T16:30:01Z", "parent_id": None}
        return [old, new]

    def test_discovery_fail_closed_stops_without_fallback(self):
        req_file = self._request_file("self-check-request-discovery.json",
                                      TASK_REF, "software-engineer")
        payload, code = pipeline.run_selfcheck(
            selfcheck_ns(request_file=str(req_file)),
            note_cli_factory=lambda: note_cli(
                comments=self._comments(newest_invalid=True)),
            finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["stage"], "discovery")
        out_dir = Path(payload["artifacts"]["dir"])
        self.assertFalse((out_dir / "self-check-result.json").exists())
        self.assertNotIn("status", payload)
        self.assertNotIn("package_id", payload)

    def test_discovery_resolves_latest_valid_package(self):
        req_file = self._request_file("self-check-request-online.json",
                                      TASK_REF, "software-engineer")
        payload, code = pipeline.run_selfcheck(
            selfcheck_ns(request_file=str(req_file)),
            note_cli_factory=lambda: note_cli(
                comments=self._comments(newest_invalid=False)),
            finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.READY_EXIT)
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["provenance"]["mode"], "t06_discovery")
        self.assertEqual(payload["provenance"]["comment"]["id"],
                         "01a0-old-0000000000000000000c")
        self.assertEqual(payload["package_id"], self.finalize["package_id"])

    def test_request_from_prepare_envelope_derives_caller_role(self):
        payload, code = pipeline.run_selfcheck(selfcheck_ns(
            request_from=str(self.scratch / "request-envelope.json"),
            envelope_file=[str(self.scratch / "result.json")]),
            finding_store=empty_finding_store())
        # the prepare caller was context-engineer; the derived request uses
        # that current role and the same task, so the software-engineer
        # package cannot satisfy it
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["status"], "REFRESH_REQUIRED")
        self.assertIn("package_missing", payload["reasons"])
        self.assertEqual(payload["current_role"], "context-engineer")
        self.assertEqual(payload["refresh"]["task_ref"], TASK_REF)
        self.assertEqual(payload["refresh"]["target_role"], "context-engineer")
        self.assertEqual(payload["consequential_work"],
                         "stopped_until_refreshed_ready")


class PublishBoundaryTests(ChainMixin, unittest.TestCase):
    def test_unauthorized_publish_never_touches_multica(self):
        cli = note_cli(refuse_on_write=True)
        res, rc = pipeline.run_publish(
            publish_ns(self.scratch / "result.json"),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.BOUNDED_EXIT)
        self.assertEqual(res["error"]["code"], "publish_not_authorized")
        self.assertEqual(len(cli.commands), 0)

    def test_authorized_publish_performs_exactly_one_allowlisted_write(self):
        cli = note_cli()
        res, rc = pipeline.run_publish(
            publish_ns(self.scratch / "result.json", authorize_publish=True),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.READY_EXIT)
        self.assertTrue(res["published"])
        self.assertFalse(res["idempotent"])
        self.assertEqual(res["trace"]["write_commands"], 1)
        writes = [argv for argv in cli.commands
                  if argv[:3] == ["issue", "comment", "add"]]
        self.assertEqual(len(writes), 1)
        self.assertIn("--content-file", writes[0])
        self.assertNotIn("--content", writes[0])
        self.assertNotIn("--content-stdin", writes[0])
        g = res["trace"]["guarantees"]
        self.assertEqual(g["assignments"], 0)
        self.assertEqual(g["mentions"], 0)
        self.assertEqual(g["downstream_run_triggers"], 0)
        self.assertEqual(g["canonical_writes"], 0)
        self.assertEqual(g["memory_rebuilds"], 0)
        self.assertTrue(res["trace"]["temp_file_deleted"])

    def test_publish_is_idempotent_for_identical_record(self):
        body, _record = note.render_note_record(
            self.envelope, prepared_by=PREPARED_BY, prepared_at=CLOCK(),
            clock=CLOCK)
        existing = {"id": ADD_RESPONSE["id"], "content": body,
                    "created_at": ADD_RESPONSE["created_at"],
                    "parent_id": None}
        cli = note_cli(comments=[existing])
        res, rc = pipeline.run_publish(
            publish_ns(self.scratch / "result.json", authorize_publish=True),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.READY_EXIT)
        self.assertTrue(res["idempotent"])
        self.assertFalse(res["published"])
        writes = [argv for argv in cli.commands
                  if argv[:3] == ["issue", "comment", "add"]]
        self.assertEqual(writes, [])

    def test_publish_guarantees_block(self):
        res, _ = pipeline.run_publish(
            publish_ns(self.scratch / "result.json", authorize_publish=True),
            note_cli_factory=lambda: note_cli())
        self.assertEqual(res["trace"]["guarantees"], {
            "llm_called": False,
            "canonical_writes": 0,
            "memory_rebuilds": 0,
            "issue_lifecycle_writes": 0,
            "assignments": 0,
            "mentions": 0,
            "downstream_run_triggers": 0,
            "frozen_schema_changes": 0,
        })


class SkillShapeTests(unittest.TestCase):
    def test_skill_frontmatter_and_tree(self):
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: multica-context-handoff", text)
        self.assertLess(len("multica-context-handoff"), 64)
        self.assertIn("description:", text)
        for ref in ("references/prepare-handoff.md",
                    "references/self-check.md"):
            self.assertIn(ref, text)
            self.assertTrue((SKILL / ref).is_file())
        names = {p.relative_to(SKILL).as_posix()
                 for p in SKILL.rglob("*") if p.is_file()
                 and "__pycache__" not in p.parts}
        self.assertEqual(names, {
            "SKILL.md", "agents/openai.yaml",
            "scripts/handoff_pipeline.py", "scripts/artifact_gate.py",
            "references/prepare-handoff.md", "references/self-check.md"})

    def test_skill_files_are_location_free(self):
        pattern = re.compile(
            r"[A-Za-z]:[\\/]|/Users/|/home/|multica-memory|worktree|yzt-\d",
            re.I)
        for path in SKILL.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                text = path.read_text(encoding="utf-8")
                match = pattern.search(text)
                self.assertIsNone(
                    match, f"machine-specific reference in "
                           f"{path.relative_to(SKILL)}: {match!r}")

    def test_pipeline_script_has_no_multica_write_implementation(self):
        src = (SKILL / "scripts" / "handoff_pipeline.py").read_text(
            encoding="utf-8")
        self.assertNotIn("import subprocess", src)
        for marker in ('"issue", "comment"', "--content-file",
                       "--content-stdin", "--allow-external-file",
                       "mention://", "rebuild-index", "memory.db",
                       "skills install"):
            self.assertNotIn(marker, src)

    def test_openai_yaml_carries_no_deployment_state(self):
        text = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        for marker in ("workspace", "agent_id", "binding", "import",
                       "install", "branch", "worktree", "policy"):
            self.assertNotIn(marker, text)


class FullChainZeroSideEffectTests(ChainMixin, unittest.TestCase):
    def test_every_stage_reports_zero_side_effects(self):
        expected = dict(pipeline.GUARANTEES)
        for payload in (self.prepare, self.finalize):
            self.assertEqual(payload["guarantees"], expected)
        req_file = write_json(self.scratch / "self-check-request-zero.json", {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": TASK_REF, "role": "software-engineer",
            "task_snapshot": self.request["task_snapshot"],
        })
        payload, _ = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(req_file),
            envelope_file=[str(self.scratch / "result.json")]),
            finding_store=empty_finding_store())
        self.assertEqual(payload["guarantees"], expected)
        self.assertFalse(payload["context_engineer_woken"])
        self.assertFalse(self.prepare["context_engineer_woken"])


if __name__ == "__main__":
    unittest.main()
