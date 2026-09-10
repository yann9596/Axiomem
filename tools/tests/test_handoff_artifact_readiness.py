#!/usr/bin/env python3
"""U04 / YZT-72 — Artifact readiness integrated into the shared handoff skill.

Adversarial replay of PREPARE_HANDOFF / SELF_CHECK / publish against the
real U10 cartifact runtime. No network, no model, no Canonical write, no
downstream 05/06 trigger. Frozen T00 is not amended.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
SKILL = REPO / "skills" / "multica-context-handoff"
FIXTURES = TOOLS / "fixtures" / "adapter"
ART = TOOLS / "fixtures" / "artifact-contract"

_spec = importlib.util.spec_from_file_location(
    "handoff_pipeline", SKILL / "scripts" / "handoff_pipeline.py")
pipeline = importlib.util.module_from_spec(_spec)
sys.dont_write_bytecode = True
_spec.loader.exec_module(pipeline)
sys.dont_write_bytecode = False

sys.path.insert(0, str(TOOLS))

import cartifact as ac  # noqa: E402
import chandoff  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_plan as plan  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

ISSUE_FILE = FIXTURES / "issue_get_yzt58.json"
PARENT_FILE = FIXTURES / "issue_get_parent_yzt39.json"
STORE_FILE = ART / "store-chain.json"
READY_SE = ART / "ready-se.json"
READY_R0 = ART / "ready-r0.json"
READY_R1 = ART / "ready-r1.json"
TASK_REF = "multica://issue/YZT-58"
PREPARED_BY = "8bc546ab-ffd8-4aa6-ad30-58583346c065"
ADD_RESPONSE = {
    "id": "01a0-new-0000000000000000000a",
    "created_at": "2026-09-09T16:00:01Z",
    "parent_id": None,
    "content": "/note",
}
U10_BASE = "73f922ea33c2e2867ba51b6843588c5aa4980ff6"
U03_BASE = "2e1959b"
FROZEN_PATHS = (
    "schemas/context-handoff/handoff-common.schema.json",
    "schemas/context-handoff/prepare-handoff-request.schema.json",
    "schemas/context-handoff/context-plan.schema.json",
    "schemas/context-handoff/semantic-compose-result.schema.json",
    "schemas/context-handoff/prepare-handoff-result.schema.json",
    "schemas/context-handoff/self-check-request.schema.json",
    "schemas/context-handoff/self-check-result.schema.json",
    "schemas/context-package.schema.json",
    "tools/chandoff.py",
)
PKG_S = "context-package.schema.json"
RES_S = "context-handoff/prepare-handoff-result.schema.json"
CONTRACT_REV = "sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc"


def validate(schema_name: str, instance) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def ns(**kwargs) -> argparse.Namespace:
    kwargs.setdefault("repo", str(REPO))
    kwargs.setdefault("executable", "multica")
    kwargs.setdefault("artifact_store_file", None)
    kwargs.setdefault("artifact_requirements_file", None)
    kwargs.setdefault("artifact_review_level", None)
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
                repairs_used=0, **over) -> argparse.Namespace:
    return ns(plan_file=str(plan_file), result_file=str(result_file),
              request_file=str(request_file), repairs_used=repairs_used,
              out_dir=out_dir, **over)


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


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def empty_finding_store():
    return plan.MemoryFindingStore([])


def note_cli(comments=None, add_response=None, refuse_on_write=False):
    import chandoff_note as note
    comments = comments if comments is not None else []

    def runner(argv):
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


def prepare_compose(scratch: Path, **prepare_over):
    payload, code = pipeline.run_prepare(
        prepare_ns(out_dir=str(scratch), **prepare_over))
    assert payload["ok"] and payload["status"] == "PLAN_READY", payload
    assert code == pipeline.READY_EXIT
    plan_env = json.loads((scratch / "plan-envelope.json").read_text(encoding="utf-8"))
    compose_doc = compose.subset_result(plan_env["plan"])
    write_json(scratch / "compose-ready.json", compose_doc)
    return payload, plan_env


def reqs_file(scratch: Path, name: str, requirements, role="software-engineer",
              level="R1") -> Path:
    return write_json(scratch / name, {
        "schema_version": "1.0",
        "kind": "artifact_ready_check_request",
        "target_role": role,
        "review_level": level,
        "requirements": requirements,
    })


class PreconditionsTests(unittest.TestCase):
    def test_artifact_contract_revision_is_the_accepted_digest(self):
        self.assertEqual(ac.artifact_contract_revision(), CONTRACT_REV)

    def test_memory_registry_role_revisions_unchanged(self):
        self.assertEqual(
            chandoff.memory_revision(),
            "sha256:30b51dea6d6f2a09b3ec25d283d207f8705d4198206664e33049ef6285138561")
        self.assertEqual(
            chandoff.registry_revision(),
            "sha256:a08e20ebea57bf830c605e8c9cc87950bc3781350d1d0c22c01e83e994684edb")
        self.assertEqual(
            chandoff.role_profile_revision(),
            "sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f")

    def test_frozen_t00_unmodified_vs_u10_and_u03(self):
        for base in (U10_BASE, U03_BASE):
            proc = subprocess.run(
                ["git", "-C", str(REPO), "diff", "--name-only", base, "--",
                 *FROZEN_PATHS],
                check=True, capture_output=True, text=True)
            self.assertEqual(proc.stdout.strip(), "", base)

    def test_no_new_public_context_api_in_context_cli(self):
        src = (TOOLS / "context_cli.py").read_text(encoding="utf-8")
        for token in ("prepare_artifact_handoff", "review_handoff",
                      "qa_handoff", "artifact-ready", "cartifact"):
            self.assertNotIn(token, src)

    def test_skill_imports_cartifact_and_does_not_duplicate_runtime(self):
        gate = (SKILL / "scripts" / "artifact_gate.py").read_text(encoding="utf-8")
        pipe = (SKILL / "scripts" / "handoff_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("artifact_ready_check", gate)
        self.assertIn("export_t00_surfaces", gate)
        self.assertIn("dependency_changed", gate)
        self.assertIn("import cartifact", gate)
        for src in (gate, pipe):
            self.assertNotIn("FORBIDDEN_VERSION_TOKENS", src)
            self.assertNotIn("CORE_ARTIFACT_TYPES", src)
            self.assertNotIn("prepare_artifact_handoff", src)


class ArtifactReadyChain(unittest.TestCase):
    """Valid exact requirements → ARTIFACT_READY + T00-compatible export."""

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="u04-ready-"))
        prepare_compose(cls.scratch)
        payload, code = pipeline.run_finalize(finalize_ns(
            cls.scratch / "plan-envelope.json",
            cls.scratch / "compose-ready.json",
            cls.scratch / "request.json",
            out_dir=str(cls.scratch),
            artifact_store_file=str(STORE_FILE),
            artifact_requirements_file=str(READY_SE),
            artifact_review_level="R1"))
        cls.payload = payload
        cls.code = code
        cls.envelope = json.loads(
            (cls.scratch / "result.json").read_text(encoding="utf-8"))
        cls.request = json.loads(
            (cls.scratch / "request.json").read_text(encoding="utf-8"))

    def test_valid_exact_requirements_are_ready_and_schema_valid(self):
        self.assertEqual(self.code, pipeline.READY_EXIT, self.payload)
        self.assertTrue(self.payload["ok"])
        self.assertEqual(self.payload["status"], "READY")
        self.assertEqual(self.payload["artifact_status"], "ARTIFACT_READY")
        self.assertTrue(self.payload["publish"]["normal_ready"])
        self.assertTrue(self.payload["publish"]["publishable"])
        self.assertFalse(self.payload["artifact_ready"]["blocks_handoff"])
        self.assertEqual(self.payload["artifact_ready"]["failures"], [])
        self.assertEqual(validate(RES_S, self.envelope), [])
        self.assertEqual(validate(PKG_S, self.envelope["package"]), [])
        kinds = {row.get("kind") for row in self.envelope["package"]["task_evidence"]}
        self.assertEqual(kinds, {"artifact_dependency", "artifact_dependency_set"})
        digest_rows = [r for r in self.envelope["package"]["task_evidence"]
                       if r.get("kind") == "artifact_dependency_set"]
        self.assertEqual(len(digest_rows), 1)
        digest = digest_rows[0]["dependency_digest"]
        self.assertTrue(digest.startswith("sha256:"))
        self.assertEqual(digest, ac.dependency_digest(
            json.loads(READY_SE.read_text(encoding="utf-8"))["requirements"]))
        self.assertEqual(self.payload["artifact_ready"]["dependency_digest"], digest)
        for ref in self.envelope["package"]["source_refs"]:
            if ref.startswith(("multica://", "repo://", "adr://", "doc://",
                               "registry://", "project://", "git://")):
                self.assertRegex(
                    ref, r"^(multica|adr|doc|repo|registry|project|git)://\S+$")
        ids = [e["id"] for e in self.envelope["package"]["project_state_slice"]]
        self.assertTrue(any(i.startswith("web-imagegen:") for i in ids))

    def test_reordered_requirements_are_byte_stable(self):
        reqs = json.loads(READY_SE.read_text(encoding="utf-8"))["requirements"]
        other = reqs_file(self.scratch, "ready-se-reordered.json", list(reversed(reqs)))
        scratch = Path(tempfile.mkdtemp(prefix="u04-reorder-"))
        prepare_compose(scratch)
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch),
            artifact_store_file=str(STORE_FILE),
            artifact_requirements_file=str(other),
            artifact_review_level="R1"))
        self.assertEqual(code, pipeline.READY_EXIT, payload)
        a = [r for r in self.envelope["package"]["task_evidence"]
             if r.get("kind") == "artifact_dependency_set"][0]["dependency_digest"]
        env = json.loads((scratch / "result.json").read_text(encoding="utf-8"))
        b = [r for r in env["package"]["task_evidence"]
             if r.get("kind") == "artifact_dependency_set"][0]["dependency_digest"]
        self.assertEqual(a, b)

    def test_selfcheck_fresh_package_stays_ready(self):
        req_file = write_json(self.scratch / "self-check-request.json", {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": TASK_REF, "role": "software-engineer",
            "task_snapshot": self.request["task_snapshot"],
        })
        payload, code = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(req_file),
            envelope_file=[str(self.scratch / "result.json")],
            artifact_store_file=str(STORE_FILE),
            out_dir=str(self.scratch)), finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.READY_EXIT, payload)
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["consequential_work"], "allowed")
        self.assertFalse(payload["artifact_freshness"]["stale"])
        self.assertFalse(payload["context_engineer_woken"])
        self.assertEqual(payload["guarantees"]["downstream_run_triggers"], 0)
        self.assertEqual(payload["guarantees"]["mentions"], 0)
        self.assertEqual(payload["guarantees"]["assignments"], 0)

    def test_version_set_change_is_package_stale(self):
        reqs = json.loads(READY_SE.read_text(encoding="utf-8"))["requirements"]
        changed = copy.deepcopy(reqs)
        for row in changed:
            if row["artifact_id"] == "ART-WIMG-031":
                row["version"] = "bbb222"
        req_path = reqs_file(self.scratch, "ready-se-b.json", changed)
        sc_req = write_json(self.scratch / "self-check-changed.json", {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": TASK_REF, "role": "software-engineer",
            "task_snapshot": self.request["task_snapshot"],
        })
        payload, code = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(sc_req),
            envelope_file=[str(self.scratch / "result.json")],
            artifact_store_file=str(STORE_FILE),
            artifact_requirements_file=str(req_path),
            out_dir=str(self.scratch)), finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["status"], "REFRESH_REQUIRED")
        self.assertIn("package_stale", payload["reasons"])
        self.assertEqual(payload["consequential_work"],
                         "stopped_until_refreshed_ready")
        self.assertTrue(payload["artifact_freshness"]["dependency_changed"])
        self.assertEqual(payload["guarantees"]["downstream_run_triggers"], 0)

    def test_supersede_invalidates_old_package_and_preserves_verdicts(self):
        store = ac.ArtifactStore(
            json.loads(STORE_FILE.read_text(encoding="utf-8"))["envelopes"])
        report = ac.apply_supersede(
            store, json.loads((ART / "implementation-b.json").read_text(
                encoding="utf-8")))
        self.assertEqual(report["rewritten_verdicts"], 0)
        preserved = {p["artifact_id"]: p["verdict"] for p in report["preserved_verdicts"]}
        self.assertEqual(preserved["REV-WIMG-008"], "APPROVE")
        self.assertEqual(preserved["QA-WIMG-004"], "PASS")
        mutated = write_json(self.scratch / "store-superseded.json",
                             {"envelopes": store.envelopes})
        sc_req = write_json(self.scratch / "self-check-supersede.json", {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": TASK_REF, "role": "software-engineer",
            "task_snapshot": self.request["task_snapshot"],
        })
        payload, code = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(sc_req),
            envelope_file=[str(self.scratch / "result.json")],
            artifact_store_file=str(mutated),
            out_dir=str(self.scratch)), finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["status"], "REFRESH_REQUIRED")
        self.assertIn("package_stale", payload["reasons"])
        self.assertEqual(store.get("REV-WIMG-008", "1")["semantics"]["verdict"],
                         "APPROVE")
        self.assertEqual(store.get("QA-WIMG-004", "1")["semantics"]["verdict"],
                         "PASS")

    def test_bounded_refresh_builds_a_fresh_package(self):
        store = ac.ArtifactStore(
            json.loads(STORE_FILE.read_text(encoding="utf-8"))["envelopes"])
        ac.apply_supersede(
            store, json.loads((ART / "implementation-b.json").read_text(
                encoding="utf-8")))
        mutated = write_json(self.scratch / "store-refresh.json",
                             {"envelopes": store.envelopes})
        reqs = json.loads(READY_SE.read_text(encoding="utf-8"))["requirements"]
        refreshed = copy.deepcopy(reqs)
        for row in refreshed:
            if row["artifact_id"] == "ART-WIMG-031":
                row["version"] = "bbb222"
        req_path = reqs_file(self.scratch, "ready-se-refresh.json", refreshed)
        scratch = Path(tempfile.mkdtemp(prefix="u04-refresh-"))
        prepare_compose(scratch)
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch),
            artifact_store_file=str(mutated),
            artifact_requirements_file=str(req_path),
            artifact_review_level="R1"))
        self.assertEqual(code, pipeline.READY_EXIT, payload)
        self.assertEqual(payload["artifact_status"], "ARTIFACT_READY")
        old_digest = [r for r in self.envelope["package"]["task_evidence"]
                      if r.get("kind") == "artifact_dependency_set"][0]["dependency_digest"]
        new_env = json.loads((scratch / "result.json").read_text(encoding="utf-8"))
        new_digest = [r for r in new_env["package"]["task_evidence"]
                      if r.get("kind") == "artifact_dependency_set"][0]["dependency_digest"]
        self.assertNotEqual(old_digest, new_digest)
        sc_req = write_json(scratch / "self-check-refresh.json", {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": TASK_REF, "role": "software-engineer",
            "task_snapshot": json.loads(
                (scratch / "request.json").read_text(encoding="utf-8"))["task_snapshot"],
        })
        check, code = pipeline.run_selfcheck(selfcheck_ns(
            request_file=str(sc_req),
            envelope_file=[str(scratch / "result.json")],
            artifact_store_file=str(mutated),
            artifact_requirements_file=str(req_path),
            out_dir=str(scratch)), finding_store=empty_finding_store())
        self.assertEqual(code, pipeline.READY_EXIT, check)
        self.assertEqual(check["status"], "READY")

    def test_publish_without_store_fails_closed_when_deps_exported(self):
        cli = note_cli(refuse_on_write=True)
        res, rc = pipeline.run_publish(
            publish_ns(self.scratch / "result.json", authorize_publish=True),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.BOUNDED_EXIT)
        self.assertEqual(res["error"]["code"], "artifact_store_required")
        self.assertEqual(len(cli.commands), 0)

    def test_authorized_publish_with_fresh_store_is_one_write(self):
        cli = note_cli()
        res, rc = pipeline.run_publish(
            publish_ns(self.scratch / "result.json", authorize_publish=True,
                       artifact_store_file=str(STORE_FILE)),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.READY_EXIT, res)
        self.assertTrue(res["published"])
        self.assertEqual(res["trace"]["guarantees"]["downstream_run_triggers"], 0)
        self.assertEqual(res["trace"]["guarantees"]["mentions"], 0)
        self.assertEqual(res["trace"]["guarantees"]["assignments"], 0)


class ArtifactNotReadyTests(unittest.TestCase):
    def _finalize_with(self, requirements, store_path=None, **prepare_over):
        scratch = Path(tempfile.mkdtemp(prefix="u04-notready-"))
        prepare_compose(scratch, **prepare_over)
        req_path = reqs_file(
            scratch, "reqs.json", requirements,
            role=prepare_over.get("target_role", "software-engineer"))
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch),
            artifact_store_file=str(store_path or STORE_FILE),
            artifact_requirements_file=str(req_path),
            artifact_review_level="R1"))
        return scratch, payload, code

    def _assert_blocked(self, payload, code, reason_code=None):
        self.assertEqual(code, pipeline.BOUNDED_EXIT, payload)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload.get("artifact_status"), "ARTIFACT_NOT_READY")
        self.assertFalse(payload["publish"]["normal_ready"])
        self.assertFalse(payload["publish"]["publishable"])
        failures = payload["artifact_ready"]["failures"]
        self.assertTrue(failures)
        if reason_code:
            self.assertIn(reason_code, {f["reason_code"] for f in failures})
        for row in failures:
            for key in ("artifact_id", "artifact_type", "version", "ref",
                        "reason_code", "reason", "correction_owner", "route"):
                self.assertIn(key, row)
            self.assertIn(row["route"], ("producer_correction", "lead_replan"))
        self.assertEqual(payload["guarantees"]["mentions"], 0)
        self.assertEqual(payload["guarantees"]["downstream_run_triggers"], 0)
        self.assertEqual(payload["guarantees"]["assignments"], 0)
        return failures

    def test_missing_required_blocks_ready(self):
        _, payload, code = self._finalize_with([{
            "artifact_type": "implementation",
            "artifact_id": "ART-MISSING",
            "version": "1",
            "required": True,
        }])
        self._assert_blocked(payload, code, "MISSING")

    def test_wrong_type_blocks_ready(self):
        _, payload, code = self._finalize_with([{
            "artifact_type": "design_baseline",
            "artifact_id": "ART-WIMG-031",
            "version": "aaa111",
            "required": True,
        }])
        self._assert_blocked(payload, code, "WRONG_TYPE")

    def test_latest_and_prose_block_ready(self):
        for token in ("latest", "current", "当前代码"):
            _, payload, code = self._finalize_with([{
                "artifact_type": "implementation",
                "artifact_id": "ART-WIMG-031",
                "version": token,
                "required": True,
            }])
            self._assert_blocked(payload, code, "VERSION_NOT_EXACT")

    def test_superseded_and_stale_block_ready(self):
        _, payload, code = self._finalize_with([{
            "artifact_type": "product_expectation",
            "artifact_id": "PE-WIMG-004",
            "version": "3",
            "required": True,
        }])
        failures = self._assert_blocked(payload, code, "SUPERSEDED")
        self.assertTrue(payload["artifact_ready"]["checks"]["superseded"])
        self.assertEqual(failures[0]["route"], "producer_correction")

    def test_ineligible_status_blocks_ready(self):
        store = ac.ArtifactStore(
            json.loads(STORE_FILE.read_text(encoding="utf-8"))["envelopes"])
        draft = copy.deepcopy(store.get("ART-WIMG-031", "aaa111"))
        draft["version"] = "draft1"
        draft["status"] = "draft"
        store.add(draft, validate=False)
        scratch = Path(tempfile.mkdtemp(prefix="u04-draft-"))
        store_path = write_json(scratch / "store.json", {"envelopes": store.envelopes})
        _, payload, code = self._finalize_with([{
            "artifact_type": "implementation",
            "artifact_id": "ART-WIMG-031",
            "version": "draft1",
            "required": True,
        }], store_path=store_path)
        self._assert_blocked(payload, code, "STATUS_NOT_ALLOWED")

    def test_missing_semantics_blocks_ready(self):
        store = ac.ArtifactStore(
            json.loads(STORE_FILE.read_text(encoding="utf-8"))["envelopes"])
        thin = copy.deepcopy(store.get("SOL-WIMG-017", "3"))
        thin["version"] = "thin"
        thin["semantics"] = {"problem": "only problem"}
        store.add(thin, validate=False)
        scratch = Path(tempfile.mkdtemp(prefix="u04-thin-"))
        store_path = write_json(scratch / "store.json", {"envelopes": store.envelopes})
        _, payload, code = self._finalize_with([{
            "artifact_type": "design_baseline",
            "artifact_id": "SOL-WIMG-017",
            "version": "thin",
            "required": True,
        }], store_path=store_path)
        self._assert_blocked(payload, code, "MISSING_SEMANTICS")

    def test_unresolved_upstream_blocks_ready(self):
        store = ac.ArtifactStore(
            json.loads(STORE_FILE.read_text(encoding="utf-8"))["envelopes"])
        broken = copy.deepcopy(store.get("ART-WIMG-031", "aaa111"))
        broken["version"] = "orphan"
        broken["based_on"] = [{
            "artifact_type": "design_baseline",
            "artifact_id": "SOL-MISSING",
            "version": "9",
        }]
        store.add(broken, validate=False)
        scratch = Path(tempfile.mkdtemp(prefix="u04-orphan-"))
        store_path = write_json(scratch / "store.json", {"envelopes": store.envelopes})
        _, payload, code = self._finalize_with([{
            "artifact_type": "implementation",
            "artifact_id": "ART-WIMG-031",
            "version": "orphan",
            "required": True,
        }], store_path=store_path)
        self._assert_blocked(payload, code, "UNRESOLVED_UPSTREAM")

    def test_not_ready_package_cannot_be_published(self):
        scratch, payload, code = self._finalize_with([{
            "artifact_type": "implementation",
            "artifact_id": "ART-MISSING",
            "version": "1",
            "required": True,
        }])
        self._assert_blocked(payload, code, "MISSING")
        result = scratch / "result.json"
        self.assertTrue(result.is_file())
        req_path = scratch / "reqs.json"
        cli = note_cli(refuse_on_write=True)
        res, rc = pipeline.run_publish(
            publish_ns(result, authorize_publish=True,
                       artifact_store_file=str(STORE_FILE),
                       artifact_requirements_file=str(req_path)),
            note_cli_factory=lambda: cli)
        self.assertEqual(rc, pipeline.BOUNDED_EXIT)
        self.assertEqual(res["error"]["code"], "artifact_not_ready")
        self.assertEqual(len(cli.commands), 0)
        failures = res["error"]["artifact_ready"]["failures"]
        self.assertIn("MISSING", {f["reason_code"] for f in failures})

    def test_declared_set_without_store_fails_closed(self):
        scratch = Path(tempfile.mkdtemp(prefix="u04-nostore-"))
        prepare_compose(scratch)
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch),
            artifact_requirements_file=str(READY_SE)))
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "artifact_store_required")
        self.assertFalse(payload["publish"]["normal_ready"])


class RoutingAndR0Tests(unittest.TestCase):
    def test_r0_ready_does_not_require_or_trigger_05_06(self):
        scratch = Path(tempfile.mkdtemp(prefix="u04-r0-"))
        prepare_compose(scratch, target_role="engineering-lead",
                        purpose="decision")
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch),
            artifact_store_file=str(STORE_FILE),
            artifact_requirements_file=str(READY_R0),
            artifact_review_level="R0"))
        self.assertEqual(code, pipeline.READY_EXIT, payload)
        self.assertEqual(payload["artifact_status"], "ARTIFACT_READY")
        self.assertEqual(payload["guarantees"]["downstream_run_triggers"], 0)
        self.assertEqual(payload["guarantees"]["mentions"], 0)
        self.assertEqual(payload["guarantees"]["assignments"], 0)
        routing = ac.validate_routing(json.loads(
            (ART / "routing-r0.json").read_text(encoding="utf-8")))
        self.assertTrue(routing["ok"])
        self.assertEqual(routing["triggers_created"], 0)

    def test_r1_ready_for_delivery_reviewer_creates_zero_triggers(self):
        scratch = Path(tempfile.mkdtemp(prefix="u04-r1-"))
        prepare_compose(scratch, target_role="delivery-reviewer",
                        purpose="review")
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch),
            artifact_store_file=str(STORE_FILE),
            artifact_requirements_file=str(READY_R1),
            artifact_review_level="R1"))
        self.assertEqual(code, pipeline.READY_EXIT, payload)
        self.assertEqual(payload["artifact_status"], "ARTIFACT_READY")
        self.assertEqual(payload["guarantees"]["downstream_run_triggers"], 0)
        self.assertEqual(payload["role"], "delivery-reviewer")


class CompatibilityAndZeroSideEffectTests(unittest.TestCase):
    def test_t00_compatibility_report(self):
        report = chandoff.artifact_handoff_compatibility()
        self.assertTrue(report["ok"], report)
        self.assertFalse(report["t00_public_schema_amended"])
        self.assertEqual(chandoff.scan_handoff_contracts(), {})

    def test_legacy_finalize_without_artifact_flags_unchanged(self):
        scratch = Path(tempfile.mkdtemp(prefix="u04-legacy-"))
        prepare_compose(scratch)
        payload, code = pipeline.run_finalize(finalize_ns(
            scratch / "plan-envelope.json", scratch / "compose-ready.json",
            scratch / "request.json", out_dir=str(scratch)))
        self.assertEqual(code, pipeline.READY_EXIT, payload)
        self.assertNotIn("artifact_status", payload)
        self.assertTrue(payload["publish"]["normal_ready"])
        env = json.loads((scratch / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(env["package"]["task_evidence"], [])


if __name__ == "__main__":
    unittest.main()
