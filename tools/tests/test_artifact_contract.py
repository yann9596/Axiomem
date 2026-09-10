#!/usr/bin/env python3
"""U10 / YZT-71 — Artifact Contract Runtime.

Covers the seven core types, exact version fail-closed, ARTIFACT_READY_CHECK,
deterministic dependency export into Frozen T00 surfaces, stale/supersede
invalidation with preserved Review/QA verdicts, and Option A R0/R1/R2
routing contracts (zero triggers).
"""
from __future__ import annotations

import copy
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
FIXTURES = TOOLS / "fixtures" / "artifact-contract"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from schema_mini import Schema, load_schema_file  # noqa: E402
import cartifact as ac  # noqa: E402
import chandoff  # noqa: E402
import chandoff_compose as compose  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as sc  # noqa: E402

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
CLOCK = lambda: "2026-09-10T12:00:00Z"  # noqa: E731


def validate_schema(schema_name: str, instance) -> list:
    schema = load_schema_file(schema_name)
    return Schema(schema, schema).validate(instance, path="$")


def load_chain() -> ac.ArtifactStore:
    raw = json.loads((FIXTURES / "store-chain.json").read_text(encoding="utf-8"))
    return ac.ArtifactStore(raw["envelopes"])


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sample_handoff_request(role: str = "software-engineer") -> dict:
    return {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": "multica://issue/YZT-71",
        "project": {"project_id": "web-imagegen"},
        "target": {"role": role},
        "purpose": "implementation",
        "task_snapshot": {
            "title": "Build artifact contract runtime",
            "description": "U10 foundation",
            "requirements": ["exact version", "no T00 amendment"],
            "acceptance_criteria": ["ARTIFACT_NOT_READY blocks READY"],
            "relevant_decisions": ["Option A routing"],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }


def pipeline(role: str = "software-engineer"):
    req = sample_handoff_request(role)
    envelope = plan.prepare_handoff_plan(req, findings=[])
    accepted = compose.compose_semantic(
        envelope, compose.subset_result(envelope["plan"]))
    result = finalize.finalize_handoff(envelope, accepted, req, clock=CLOCK)
    return req, result


class CatalogAndEnvelopeTests(unittest.TestCase):
    def test_catalog_has_exactly_seven_core_types(self):
        cat = ac.load_catalog()
        self.assertEqual(validate_schema("artifact-contract/catalog.schema.json", cat), [])
        types = [t["artifact_type"] for t in cat["types"]]
        self.assertEqual(tuple(types), ac.CORE_ARTIFACT_TYPES)

    def test_routing_contracts_forbid_auto_triggers(self):
        doc = ac.load_routing_contracts()
        self.assertEqual(validate_schema("artifact-contract/routing.schema.json", doc), [])
        for level, spec in doc["levels"].items():
            self.assertFalse(spec["producer_auto_triggers_delivery_reviewer"], level)
            self.assertFalse(spec["delivery_reviewer_auto_triggers_qa"], level)
        self.assertFalse(doc["levels"]["R0"]["requires_delivery_review"])
        self.assertFalse(doc["levels"]["R0"]["requires_qa"])
        self.assertTrue(doc["levels"]["R1"]["requires_delivery_review"])
        self.assertTrue(doc["levels"]["R2"]["requires_qa"])

    def test_chain_envelopes_validate(self):
        store = load_chain()
        present = {e["artifact_type"] for e in store.envelopes}
        self.assertEqual(present, set(ac.CORE_ARTIFACT_TYPES))
        for env in store.envelopes:
            level = "R0" if env["artifact_id"] == "ISS-WIMG-R0" else None
            errs = ac.validate_envelope(env, review_level=level)
            self.assertEqual(errs, [], (env["artifact_id"], env["version"], errs))

    def test_relations_traceable(self):
        store = load_chain()
        impl = store.get("ART-WIMG-031", "aaa111")
        self.assertTrue(any(
            r["artifact_id"] == "SOL-WIMG-017" for r in impl["based_on"]))
        review = store.get("REV-WIMG-008", "1")
        self.assertEqual(review["reviewed_artifact"]["version"], "aaa111")
        qa = store.get("QA-WIMG-004", "1")
        types = {r["artifact_type"] for r in qa["validated_against"]}
        self.assertEqual(types, {
            "product_expectation", "design_baseline",
            "implementation", "delivery_review",
        })
        pe4 = store.get("PE-WIMG-004", "4")
        self.assertEqual(pe4["supersedes"]["version"], "3")


class VersionResolutionTests(unittest.TestCase):
    def test_exact_version_resolves_deterministically(self):
        store = load_chain()
        a = ac.resolve_exact(store, "SOL-WIMG-017", "3", "design_baseline")
        b = ac.resolve_exact(store, "SOL-WIMG-017", "3", "design_baseline")
        self.assertTrue(a["ok"])
        self.assertEqual(a["envelope"]["version"], b["envelope"]["version"])
        auth = ac.authoritative_version(store, "PE-WIMG-004", "product_expectation")
        self.assertTrue(auth["ok"])
        self.assertEqual(auth["version"], "4")

    def test_latest_and_prose_fail_closed(self):
        store = load_chain()
        for token in ("latest", "current", "当前代码", "大概那个", "aaa 111"):
            out = ac.resolve_exact(store, "ART-WIMG-031", token)
            self.assertFalse(out["ok"], token)
            self.assertEqual(out["code"], "VERSION_NOT_EXACT")

    def test_missing_and_wrong_type_fail_closed(self):
        store = load_chain()
        missing = ac.resolve_exact(store, "ART-WIMG-031", "no-such")
        self.assertEqual(missing["code"], "MISSING")
        wrong = ac.resolve_exact(store, "ART-WIMG-031", "aaa111", "design_baseline")
        self.assertEqual(wrong["code"], "WRONG_TYPE")

    def test_ambiguous_authoritative_fails_closed(self):
        store = load_chain()
        clone = copy.deepcopy(store.get("ART-WIMG-031", "aaa111"))
        clone["version"] = "ccc333"
        store.add(clone, validate=False)
        auth = ac.authoritative_version(store, "ART-WIMG-031")
        self.assertFalse(auth["ok"])
        self.assertEqual(auth["code"], "AMBIGUOUS")


class ReadyCheckTests(unittest.TestCase):
    def test_r1_ready_on_exact_implementation(self):
        store = load_chain()
        result = ac.artifact_ready_check(store, load_json("ready-r1.json"))
        self.assertEqual(result["status"], "ARTIFACT_READY", result["failures"])
        self.assertFalse(result["blocks_handoff"])
        self.assertEqual(
            ac._validate("artifact-contract/readiness.schema.json#/$defs/result", result),
            [],
        )

    def test_r2_qa_ready_with_all_baselines(self):
        store = load_chain()
        result = ac.artifact_ready_check(store, load_json("ready-r2.json"))
        self.assertEqual(result["status"], "ARTIFACT_READY", result["failures"])

    def test_r0_ready_with_minimal_envelope(self):
        store = load_chain()
        result = ac.artifact_ready_check(store, load_json("ready-r0.json"))
        self.assertEqual(result["status"], "ARTIFACT_READY", result["failures"])

    def test_missing_required_returns_not_ready_and_blocks(self):
        store = load_chain()
        req = {
            "schema_version": "1.0",
            "kind": "artifact_ready_check_request",
            "target_role": "delivery-reviewer",
            "review_level": "R1",
            "requirements": [{
                "artifact_type": "implementation",
                "artifact_id": "ART-MISSING",
                "version": "1",
                "required": True,
            }],
        }
        result = ac.artifact_ready_check(store, req)
        self.assertEqual(result["status"], "ARTIFACT_NOT_READY")
        self.assertTrue(result["blocks_handoff"])
        self.assertEqual(result["failures"][0]["reason_code"], "MISSING")
        self.assertEqual(result["failures"][0]["route"], "producer_correction")

    def test_superseded_and_stale_cannot_be_ready(self):
        store = load_chain()
        req = {
            "schema_version": "1.0",
            "kind": "artifact_ready_check_request",
            "target_role": "qa",
            "review_level": "R2",
            "requirements": [{
                "artifact_type": "product_expectation",
                "artifact_id": "PE-WIMG-004",
                "version": "3",
                "required": True,
            }],
        }
        result = ac.artifact_ready_check(store, req)
        self.assertEqual(result["status"], "ARTIFACT_NOT_READY")
        self.assertTrue(result["checks"]["superseded"])
        codes = {f["reason_code"] for f in result["failures"]}
        self.assertIn("SUPERSEDED", codes)

    def test_disallowed_status_and_missing_semantics(self):
        store = load_chain()
        draft = copy.deepcopy(store.get("ART-WIMG-031", "aaa111"))
        draft["version"] = "draft1"
        draft["status"] = "draft"
        store.add(draft, validate=False)
        req = {
            "schema_version": "1.0",
            "kind": "artifact_ready_check_request",
            "target_role": "delivery-reviewer",
            "review_level": "R1",
            "requirements": [{
                "artifact_type": "implementation",
                "artifact_id": "ART-WIMG-031",
                "version": "draft1",
                "required": True,
            }],
        }
        result = ac.artifact_ready_check(store, req)
        self.assertEqual(result["status"], "ARTIFACT_NOT_READY")
        self.assertFalse(result["checks"]["status_allowed_for_target_role"])

        thin = copy.deepcopy(store.get("SOL-WIMG-017", "3"))
        thin["version"] = "thin"
        thin["semantics"] = {"problem": "only problem"}
        store.add(thin, validate=False)
        req2 = {
            "schema_version": "1.0",
            "kind": "artifact_ready_check_request",
            "target_role": "software-engineer",
            "review_level": "R1",
            "requirements": [{
                "artifact_type": "design_baseline",
                "artifact_id": "SOL-WIMG-017",
                "version": "thin",
                "required": True,
            }],
        }
        result2 = ac.artifact_ready_check(store, req2)
        self.assertEqual(result2["status"], "ARTIFACT_NOT_READY")
        self.assertFalse(result2["checks"]["required_semantics_present"])

    def test_unresolved_upstream_ref_fails(self):
        store = load_chain()
        broken = copy.deepcopy(store.get("ART-WIMG-031", "aaa111"))
        broken["version"] = "orphan"
        broken["based_on"] = [{
            "artifact_type": "design_baseline",
            "artifact_id": "SOL-MISSING",
            "version": "9",
        }]
        store.add(broken, validate=False)
        req = {
            "schema_version": "1.0",
            "kind": "artifact_ready_check_request",
            "target_role": "delivery-reviewer",
            "review_level": "R1",
            "requirements": [{
                "artifact_type": "implementation",
                "artifact_id": "ART-WIMG-031",
                "version": "orphan",
                "required": True,
            }],
        }
        result = ac.artifact_ready_check(store, req)
        self.assertEqual(result["status"], "ARTIFACT_NOT_READY")
        self.assertFalse(result["checks"]["upstream_refs_resolvable"])

    def test_qa_without_baselines_blocked(self):
        store = load_chain()
        req = {
            "schema_version": "1.0",
            "kind": "artifact_ready_check_request",
            "target_role": "qa",
            "review_level": "R2",
            "requirements": [{
                "artifact_type": "implementation",
                "artifact_id": "ART-WIMG-031",
                "version": "aaa111",
                "required": True,
            }],
        }
        result = ac.artifact_ready_check(store, req)
        self.assertEqual(result["status"], "ARTIFACT_NOT_READY")
        codes = {f["reason_code"] for f in result["failures"]}
        self.assertIn("QA_GATE_BLOCKED", codes)
        self.assertFalse(result["checks"]["expected_artifact_type_present"])


class DependencyExportTests(unittest.TestCase):
    def test_digest_byte_stable_and_order_independent(self):
        a = [
            {"artifact_type": "implementation", "artifact_id": "ART-WIMG-031",
             "version": "aaa111", "required": True},
            {"artifact_type": "design_baseline", "artifact_id": "SOL-WIMG-017",
             "version": "3", "required": True},
        ]
        b = list(reversed(a))
        self.assertEqual(ac.dependency_digest(a), ac.dependency_digest(b))
        c = copy.deepcopy(a)
        c[0]["version"] = "bbb222"
        self.assertNotEqual(ac.dependency_digest(a), ac.dependency_digest(c))
        self.assertTrue(ac.dependency_changed(ac.dependency_digest(a), c))

    def test_export_fits_frozen_task_evidence_and_source_refs(self):
        store = load_chain()
        req, result = pipeline("delivery-reviewer")
        exported = ac.export_t00_surfaces(store, load_json("ready-r1.json")["requirements"])
        self.assertEqual(validate_schema(
            "artifact-contract/dependency-set.schema.json", {
                "schema_version": "1.0",
                "kind": "artifact_dependency_set",
                "dependencies": exported["dependencies"],
                "dependency_digest": exported["dependency_digest"],
            }), [])
        pkg = result["package"]
        pkg["task_evidence"] = list(pkg.get("task_evidence") or []) + exported["task_evidence"]
        pkg["source_refs"] = list(pkg.get("source_refs") or []) + exported["source_refs"]
        self.assertEqual(validate_schema(PKG_S, pkg), [])
        for ref in exported["source_refs"]:
            self.assertRegex(ref, r"^(multica|adr|doc|repo|registry|project|git)://\S+$")
        kinds = {row["kind"] for row in exported["task_evidence"]}
        self.assertEqual(kinds, {"artifact_dependency", "artifact_dependency_set"})

    def test_implementation_b_makes_handoff_refresh_required(self):
        store = load_chain()
        reqs_a = load_json("ready-r1.json")["requirements"]
        digest_a = ac.dependency_digest(reqs_a)
        exported_a = ac.export_t00_surfaces(store, reqs_a)
        report = ac.apply_supersede(store, load_json("implementation-b.json"))
        self.assertEqual(store.get("ART-WIMG-031", "aaa111")["status"], "superseded")
        reqs_b = copy.deepcopy(reqs_a)
        for row in reqs_b:
            if row["artifact_id"] == "ART-WIMG-031":
                row["version"] = "bbb222"
        self.assertTrue(ac.dependency_changed(digest_a, reqs_b))
        self.assertNotEqual(exported_a["dependency_digest"], ac.dependency_digest(reqs_b))
        ready_old = ac.artifact_ready_check(store, load_json("ready-r1.json"))
        self.assertEqual(ready_old["status"], "ARTIFACT_NOT_READY")
        self.assertTrue(ready_old["blocks_handoff"])
        self.assertIn("REV-WIMG-008@1", report["stale"])


class InvalidationTests(unittest.TestCase):
    def test_review_binds_exact_implementation_and_stales_on_change(self):
        store = load_chain()
        review = store.get("REV-WIMG-008", "1")
        self.assertEqual(review["reviewed_artifact"]["version"], "aaa111")
        report = ac.apply_supersede(store, load_json("implementation-b.json"))
        self.assertEqual(store.get("REV-WIMG-008", "1")["status"], "stale")
        self.assertEqual(store.get("QA-WIMG-004", "1")["status"], "stale")
        preserved = {p["artifact_id"]: p["verdict"] for p in report["preserved_verdicts"]}
        self.assertEqual(preserved["REV-WIMG-008"], "APPROVE")
        self.assertEqual(preserved["QA-WIMG-004"], "PASS")
        self.assertEqual(report["rewritten_verdicts"], 0)

    def test_new_attempt_required_and_old_verdict_not_rewritten(self):
        store = load_chain()
        ac.apply_supersede(store, load_json("implementation-b.json"))
        old = store.get("REV-WIMG-008", "1")
        self.assertEqual(old["semantics"]["verdict"], "APPROVE")
        new_review = copy.deepcopy(old)
        new_review["version"] = "2"
        new_review["status"] = "ready"
        new_review["reviewed_artifact"] = {
            "artifact_type": "implementation",
            "artifact_id": "ART-WIMG-031",
            "version": "bbb222",
        }
        new_review["based_on"] = [new_review["reviewed_artifact"]]
        new_review["semantics"] = dict(old["semantics"], verdict="CHANGES_REQUIRED")
        opened = ac.open_new_attempt(store, old, new_review)
        self.assertTrue(opened["history_preserved"])
        self.assertEqual(store.get("REV-WIMG-008", "1")["semantics"]["verdict"], "APPROVE")
        self.assertEqual(store.get("REV-WIMG-008", "2")["semantics"]["verdict"],
                         "CHANGES_REQUIRED")
        with self.assertRaisesRegex(ValueError, "cannot rewrite"):
            ac.open_new_attempt(store, new_review, {
                **new_review,
                "semantics": dict(new_review["semantics"], verdict="APPROVE"),
            })


class RoutingContractTests(unittest.TestCase):
    def test_r0_r1_r2_fixtures(self):
        for name in ("routing-r0.json", "routing-r1.json", "routing-r2.json"):
            result = ac.validate_routing(load_json(name))
            self.assertTrue(result["ok"], (name, result["violations"]))
            self.assertEqual(result["triggers_created"], 0)

    def test_r0_rejects_05_06_requirement(self):
        plan = load_json("routing-r0.json")
        plan["independent_delivery_review_attempt"] = True
        result = ac.validate_routing(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(result["triggers_created"], 0)

    def test_r1_requires_exact_review_input(self):
        plan = load_json("routing-r1.json")
        plan["review_input"] = {"artifact_type": "implementation",
                                "artifact_id": "ART-WIMG-031",
                                "version": "latest"}
        result = ac.validate_routing(plan)
        self.assertFalse(result["ok"])
        codes = {v["code"] for v in result["violations"]}
        self.assertIn("R1_EXACT_REVIEW_INPUT_REQUIRED", codes)

    def test_r2_requires_exact_qa_baselines(self):
        plan = load_json("routing-r2.json")
        plan["qa_baselines"] = [
            {"artifact_type": "implementation", "artifact_id": "ART-WIMG-031",
             "version": "aaa111"},
        ]
        result = ac.validate_routing(plan)
        self.assertFalse(result["ok"])
        codes = {v["code"] for v in result["violations"]}
        self.assertIn("R2_EXACT_QA_BASELINES_REQUIRED", codes)

    def test_auto_trigger_forbidden(self):
        plan = load_json("routing-r1.json")
        plan["producer_auto_triggers_delivery_reviewer"] = True
        result = ac.validate_routing(plan)
        self.assertFalse(result["ok"])
        self.assertEqual(result["triggers_created"], 0)


class T00CompatibilityTests(unittest.TestCase):
    def test_frozen_t00_files_unmodified_vs_u03_base(self):
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--name-only", U03_BASE, "--",
             *FROZEN_PATHS],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(proc.stdout.strip(), "")

    def test_u03_expressibility_still_holds_without_schema_amendment(self):
        report = chandoff.artifact_handoff_compatibility()
        self.assertTrue(report["ok"], report)
        self.assertFalse(report["t00_public_schema_amended"])

    def test_u03_namespaced_checkpoint_ids_intact(self):
        req, result = pipeline("context-engineer")
        ids = [e["id"] for e in result["package"]["team_state_slice"]]
        proj = [e["id"] for e in result["package"]["project_state_slice"]]
        self.assertIn("team:cp-confirmed-001", ids)
        self.assertIn("web-imagegen:cp-confirmed-001", proj)
        source, local = chandoff.split_checkpoint_candidate_id("team:cp-confirmed-001")
        self.assertEqual((source, local), ("team", "cp-confirmed-001"))

    def test_boundary_scan_clean_and_cartifact_not_in_native_api_scan(self):
        self.assertEqual(chandoff.scan_handoff_contracts(), {})
        scanned = (ROOT / "tools" / "chandoff.py").read_text(encoding="utf-8")
        self.assertNotIn("cartifact", scanned)

    def test_context_package_artifact_can_wrap_real_t00_package(self):
        req, result = pipeline("software-engineer")
        pkg = result["package"]
        self.assertEqual(validate_schema(PKG_S, pkg), [])
        env = {
            "schema_version": "1.0",
            "kind": "artifact_envelope",
            "artifact_type": "context_package",
            "artifact_id": "CTX-LIVE-001",
            "project_id": "web-imagegen",
            "task_id": req["task_ref"],
            "owner_role": "context-engineer",
            "version": "1",
            "status": "ready",
            "purpose": "wrap live T00 package",
            "intended_consumers": ["software-engineer"],
            "provenance": {
                "created_at": "2026-09-10T12:00:00Z",
                "updated_at": "2026-09-10T12:00:00Z",
                "produced_by_role": "context-engineer",
            },
            "based_on": [],
            "semantics": {"package": pkg},
        }
        self.assertEqual(ac.validate_envelope(env), [])
        check = sc.self_check({
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": req["task_ref"],
            "role": "software-engineer",
            "task_snapshot": req["task_snapshot"],
        }, packages=[result], current=dict(result["built_from"]))
        self.assertIn(check["status"], {"READY", "REFRESH_REQUIRED"})

    def test_no_new_public_context_api_symbols_in_context_cli(self):
        src = (TOOLS / "context_cli.py").read_text(encoding="utf-8")
        for token in ("prepare_artifact_handoff", "review_handoff",
                      "qa_handoff", "artifact-ready", "cartifact"):
            self.assertNotIn(token, src)

    def test_revision_is_deterministic(self):
        a = ac.artifact_contract_revision()
        b = ac.artifact_contract_revision()
        self.assertTrue(a.startswith("sha256:"))
        self.assertEqual(a, b)
        self.assertEqual(len(a), len("sha256:") + 64)


class CliReplayTests(unittest.TestCase):
    def test_cli_ready_check_and_routing(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ac.main([
                "ready-check",
                "--store", str(FIXTURES / "store-chain.json"),
                "--request-file", str(FIXTURES / "ready-r2.json"),
            ])
            rt = ac.main(["routing-check", "--request-file",
                          str(FIXTURES / "routing-r0.json")])
            bad = ac.main([
                "resolve",
                "--store", str(FIXTURES / "store-chain.json"),
                "--artifact-id", "ART-WIMG-031",
                "--version", "latest",
            ])
        self.assertEqual(rc, 0)
        self.assertEqual(rt, 0)
        self.assertEqual(bad, 0)
        out = ac.resolve_exact(load_chain(), "ART-WIMG-031", "latest")
        self.assertFalse(out["ok"])
        self.assertIn("ARTIFACT_READY", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
