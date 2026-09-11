#!/usr/bin/env python3
"""U12-P0 focused tests — production ledger deployment preflight (YZT-81).

Everything runs against temporary directories and a fake host; no production
path is touched, no ACL is applied, no platform call is made. The real
deployment is executed once by the task owner against the approved exact path.
"""
from __future__ import annotations

import json
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff_dispatch as dispatch  # noqa: E402
import chandoff_intent as o2  # noqa: E402
import u12_preflight as u12  # noqa: E402


class FakeHost:
    def __init__(self, acl_entries=None, readonly=True):
        self.acl_entries = acl_entries
        self.readonly = readonly
        self.applied = []
        self.calls = []
        self.cli_outputs = {}

    def whoami(self):
        return {"name": "test\\administrator", "sid": "S-1-5-21-1-2-3-500",
                "groups": ["S-1-5-32-544"]}

    def read_acl(self, path):
        return {"sddl": "FAKE", "owner_sid": "S-1-5-21-1-2-3-500",
                "entries": self.acl_entries or []}

    def apply_acl(self, path, *, inherit_remove, grants, run=None):
        self.applied.append({"path": str(path), "inherit_remove": inherit_remove,
                             "grants": list(grants)})
        return {"argv": ["icacls", str(path)], "exit": 0, "stdout": "",
                "stderr": ""}

    def set_readonly(self, path):
        self.calls.append("readonly")
        return {"exit": 0, "readonly": self.readonly}

    def capture_cli(self, argv):
        text = self.cli_outputs.get(tuple(argv), "")
        return {"argv": ["multica"] + list(argv), "exit": 0,
                "stdout": text, "stderr": "",
                "stdout_digest_lf": u12.digest_bytes(
                    text.encode("utf-8"), normalize_lf=True)}


def allowed_entries(sids=(u12.SYSTEM_SID, u12.ADMINS_SID,
                          "S-1-5-21-1-2-3-500")):
    return [{"sid": sid, "rights": 0x1F01FF if sid != "S-1-5-21-1-2-3-500"
             else 0x1301BF, "type": "Allow", "inherited": False,
             "inheritance": "ContainerInherit, ObjectInherit",
             "propagation": "None"} for sid in sids]


def readonly_entries(sids=(u12.SYSTEM_SID, u12.ADMINS_SID,
                           "S-1-5-21-1-2-3-500")):
    return [{"sid": sid, "rights": 0x1, "type": "Allow", "inherited": False,
             "inheritance": "None", "propagation": "None"} for sid in sids]


class PathValidationTests(unittest.TestCase):
    def test_overlap_reason_covers_all_directions(self):
        self.assertEqual(u12.overlap_reason(r"C:\a\b", r"C:\a\b"), "same_path")
        self.assertEqual(u12.overlap_reason(r"C:\a\b\c", r"C:\a\b"),
                         "candidate_inside_tree")
        self.assertEqual(u12.overlap_reason(r"C:\a", r"C:\a\b"),
                         "tree_inside_candidate")
        self.assertIsNone(u12.overlap_reason(r"C:\a\b", r"C:\a\x"))

    def test_validate_refuses_nonproduction_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state" / "web-imagegen"
            ledger = root / "dispatch" / "ledger.jsonl"
            result = u12.validate_paths(root=root, ledger=ledger,
                                        worktrees=[Path(tmp) / "wt"])
            self.assertFalse(result["ok"])
            self.assertTrue(any("not the exact approved" in r
                                for r in result["refusals"]))

    def test_validate_refuses_overlap_with_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "wt"
            tree.mkdir()
            fake_root = tree / "state" / "web-imagegen"
            fake_root.mkdir(parents=True)
            fake_ledger = fake_root / "dispatch" / "ledger.jsonl"
            with mock.patch.object(u12, "APPROVED_ROOT", fake_root), \
                    mock.patch.object(u12, "APPROVED_LEDGER", fake_ledger):
                result = u12.validate_paths(
                    root=fake_root, ledger=fake_ledger, worktrees=[tree])
            self.assertFalse(result["ok"])
            self.assertTrue(result["checks"]["forbidden_tree_overlap"])

    def test_validate_detects_reparse_point(self):
        fake_attrs = types.SimpleNamespace(
            st_mode=stat.S_IFDIR, st_file_attributes=0x400)  # reparse

        def fake_lstat(path):
            if str(path).lower().endswith("state"):
                return fake_attrs
            return types.SimpleNamespace(st_mode=stat.S_IFDIR,
                                         st_file_attributes=0)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "state" / "web-imagegen"
            ledger = root / "dispatch" / "ledger.jsonl"
            with mock.patch.object(u12, "APPROVED_ROOT", root), \
                    mock.patch.object(u12, "APPROVED_LEDGER", ledger):
                result = u12.validate_paths(
                    root=root, ledger=ledger, worktrees=[Path(tmp) / "wt"],
                    lstat=fake_lstat)
            self.assertFalse(result["ok"])
            self.assertTrue(result["checks"]["reparse_points"])

    def test_preexisting_unexpected_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "multica-state"
            root = base / "web-imagegen"
            (root / "dispatch").mkdir(parents=True)
            (root / "stray.txt").write_text("unexpected", encoding="utf-8")
            pre = u12.check_preexisting(root, root / "dispatch" / "ledger.jsonl")
            self.assertTrue(pre["unexpected"])
            self.assertIn("web-imagegen/stray.txt", pre["unexpected_entries"])

    def test_preexisting_expected_layout_is_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "multica-state"
            root = base / "web-imagegen"
            (root / "dispatch" / "backups").mkdir(parents=True)
            (root / "preflight").mkdir()
            (root / "dispatch" / "ledger.jsonl").write_text("", encoding="utf-8")
            pre = u12.check_preexisting(root, root / "dispatch" / "ledger.jsonl")
            self.assertFalse(pre["unexpected"])


class AclModelTests(unittest.TestCase):
    def test_least_privilege_ok(self):
        result = u12.evaluate_acl(
            allowed_entries(),
            allowed_sids=(u12.SYSTEM_SID, u12.ADMINS_SID,
                          "S-1-5-21-1-2-3-500"))
        self.assertTrue(result["ok"], result["violations"])

    def test_broad_principal_refused(self):
        entries = allowed_entries() + [{
            "sid": "S-1-5-11", "rights": 0x1301BF, "type": "Allow",
            "inherited": False}]
        result = u12.evaluate_acl(
            entries, allowed_sids=(u12.SYSTEM_SID, u12.ADMINS_SID,
                                   "S-1-5-21-1-2-3-500"))
        self.assertFalse(result["ok"])
        self.assertIn("principal_not_least_privilege",
                      [v["code"] for v in result["violations"]])

    def test_deny_and_missing_principal_refused(self):
        entries = [{"sid": u12.SYSTEM_SID, "rights": 0x1F01FF,
                    "type": "Deny", "inherited": False}]
        result = u12.evaluate_acl(
            entries, allowed_sids=(u12.SYSTEM_SID, u12.ADMINS_SID))
        codes = [v["code"] for v in result["violations"]]
        self.assertIn("deny_ace_present", codes)
        self.assertIn("required_principal_missing", codes)

    def test_immutable_rejects_write_rights(self):
        entries = [{"sid": u12.SYSTEM_SID, "rights": 0x1F01FF,
                    "type": "Allow", "inherited": False},
                   {"sid": u12.ADMINS_SID, "rights": 0x1, "type": "Allow",
                    "inherited": False}]
        result = u12.evaluate_acl(
            entries, allowed_sids=(u12.SYSTEM_SID, u12.ADMINS_SID),
            require_protected=True, immutable=True)
        codes = [v["code"] for v in result["violations"]]
        self.assertIn("immutable_object_has_write_right", codes)

    def test_protected_acl_rejects_inherited(self):
        entries = allowed_entries()
        entries[0] = dict(entries[0], inherited=True)
        result = u12.evaluate_acl(
            entries, allowed_sids=(u12.SYSTEM_SID, u12.ADMINS_SID,
                                   "S-1-5-21-1-2-3-500"),
            require_protected=True)
        self.assertIn("protected_acl_inherits",
                      [v["code"] for v in result["violations"]])


class LedgerTests(unittest.TestCase):
    def test_genesis_record_readable_by_all_readers(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            store = o2.DurableIntentStore(ledger)
            record = u12.genesis_record(at="2026-09-11T00:00:00Z",
                                        ledger=ledger, root=Path(tmp))
            store.append(record)
            integrity = u12.ledger_integrity(ledger)
            self.assertEqual(integrity["partial_or_corrupt_records"], 0)
            self.assertEqual(integrity["total_records"], 1)
            self.assertEqual(integrity["intent_records"], 0)
            self.assertEqual(integrity["ignored_records"], 1)
            self.assertTrue(integrity["audit_ok"])
            self.assertTrue(integrity["legacy_reader_ok"])
            legacy = dispatch.TransactionLedger.load(ledger)
            self.assertEqual(len(legacy.records), 1)

    def test_partial_record_fails_closed_in_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "ledger.jsonl"
            store = o2.DurableIntentStore(ledger)
            store.append(u12.genesis_record(at="2026-09-11T00:00:00Z",
                                            ledger=ledger, root=Path(tmp)))
            with open(ledger, "a", encoding="utf-8") as handle:
                handle.write('{"kind": "command", "argv": ["issue"]\n')
            integrity = u12.ledger_integrity(ledger)
            self.assertEqual(integrity["partial_or_corrupt_records"], 1)
            self.assertTrue(integrity["legacy_reader_ok"] is False)

    def test_backup_and_isolated_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ledger = tmp / "dispatch" / "ledger.jsonl"
            ledger.parent.mkdir(parents=True)
            store = o2.DurableIntentStore(ledger)
            store.append(u12.genesis_record(at="2026-09-11T00:00:00Z",
                                            ledger=ledger, root=tmp))
            host = FakeHost(acl_entries=readonly_entries())
            result = u12.deploy_backup(
                ledger=ledger, backup_dir=tmp / "dispatch" / "backups",
                stamp="20260911T000000Z", host=host,
                runtime_sid="S-1-5-21-1-2-3-500",
                restore_dir=tmp / "restore")
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["source_tip_digest"],
                             result["backup_digest"])
            self.assertEqual(result["restore_digest"],
                             result["source_tip_digest"])
            self.assertFalse(result["production_ledger_overwritten"])
            self.assertEqual(result["acl_violations"], [])
            self.assertEqual(result["restore_integrity"][
                "partial_or_corrupt_records"], 0)
            self.assertTrue((tmp / "dispatch" / "backups" /
                             "ledger.jsonl.20260911T000000Z.bak").exists())

    def test_backup_refuses_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ledger = tmp / "ledger.jsonl"
            store = o2.DurableIntentStore(ledger)
            store.append(u12.genesis_record(at="2026-09-11T00:00:00Z",
                                            ledger=ledger, root=tmp))
            backup_dir = tmp / "backups"
            backup_dir.mkdir()
            (backup_dir / "ledger.jsonl.20260911T000000Z.bak").write_text(
                "existing", encoding="utf-8")
            result = u12.deploy_backup(
                ledger=ledger, backup_dir=backup_dir,
                stamp="20260911T000000Z", host=FakeHost(),
                runtime_sid="S-1-5-21-1-2-3-500", restore_dir=tmp / "r")
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "backup_target_exists")


class ReceiptContractTests(unittest.TestCase):
    def test_documented_shapes_accepted_and_negatives_refused(self):
        host = FakeHost()
        version_out = "{}"
        with mock.patch.dict(u12.RECEIPT_PINS, {
                "version": u12.digest_bytes(version_out.encode("utf-8")),
                "rerun_help": u12.digest_bytes(b"r"),
                "runs_help": u12.digest_bytes(b"l")}), \
                mock.patch.object(host, "capture_cli", side_effect=[
                    {"argv": ["multica", "version"], "exit": 0,
                     "stdout": version_out, "stderr": "",
                     "stdout_digest_lf": u12.digest_bytes(
                         version_out.encode("utf-8"))},
                    {"argv": ["multica", "issue", "rerun", "--help"], "exit": 0,
                     "stdout": "r", "stderr": "",
                     "stdout_digest_lf": u12.digest_bytes(b"r")},
                    {"argv": ["multica", "issue", "runs", "--help"], "exit": 0,
                     "stdout": "l", "stderr": "",
                     "stdout_digest_lf": u12.digest_bytes(b"l")}]):
            evidence = u12.receipt_contract_evidence(host)
        self.assertTrue(evidence["documented_shapes_accepted"])
        self.assertTrue(evidence["refused_all"], evidence["refused_shapes"])
        self.assertFalse(evidence["drift"])
        self.assertEqual(evidence["live_triggers_issued"], 0)

    def test_extra_wrapper_shape_is_recorded(self):
        host = FakeHost()
        version_out = "{}"
        with mock.patch.dict(u12.RECEIPT_PINS, {
                "version": u12.digest_bytes(version_out.encode("utf-8")),
                "rerun_help": u12.digest_bytes(b"r"),
                "runs_help": u12.digest_bytes(b"l")}), \
                mock.patch.object(host, "capture_cli", side_effect=[
                    {"argv": ["multica", "version"], "exit": 0,
                     "stdout": version_out, "stderr": "",
                     "stdout_digest_lf": u12.digest_bytes(
                         version_out.encode("utf-8"))},
                    {"argv": ["multica", "issue", "rerun", "--help"], "exit": 0,
                     "stdout": "r", "stderr": "",
                     "stdout_digest_lf": u12.digest_bytes(b"r")},
                    {"argv": ["multica", "issue", "runs", "--help"], "exit": 0,
                     "stdout": "l", "stderr": "",
                     "stdout_digest_lf": u12.digest_bytes(b"l")}]):
            evidence = u12.receipt_contract_evidence(host)
        self.assertTrue(evidence["extra_accepted_wrapper_shape"]["accepted"])

    def test_parser_matrix(self):
        run = {"id": "r", "issue_id": "i", "agent_id": "a", "status": "s"}
        self.assertEqual(o2.parse_run_object(json.dumps(run))["id"], "r")
        self.assertEqual(o2.parse_run_object(json.dumps([run]))["id"], "r")
        self.assertEqual(o2.parse_run_object(json.dumps({"runs": [run]}))["id"],
                         "r")
        for bad in ([], [run, run], {"runs": []}, {"id": "x"}):
            with self.assertRaises(o2.IntentError):
                o2.parse_run_object(json.dumps(bad))
        with self.assertRaises(o2.IntentError):
            o2.parse_run_object("not json")


class ManifestTests(unittest.TestCase):
    def _inputs(self):
        return dict(
            validation={
                "production_path_exact": True,
                "normalized_root": "D:\\root",
                "normalized_ledger": "D:\\root\\ledger.jsonl",
                "volume": "D:",
                "checks": {"forbidden_tree_overlap": {},
                           "reparse_points": [],
                           "forbidden_trees_checked": ["D:\\wt"],
                           "preexisting_state": {"unexpected": False}},
            },
            acl={"least_privilege_verified": True,
                 "acl_evidence_digest": "sha256:acl"},
            integrity={"tip_digest": "sha256:t"},
            backup={"ok": True, "backup_path": "D:\\b.bak",
                    "backup_digest": "sha256:b", "acl_immutable_ok": True,
                    "restore_digest_matches_source": True,
                    "production_ledger_overwritten": False},
            capability={"passed_observations": 6, "observation_count": 6,
                        "all_passed": True, "workdir": "D:\\cap",
                        "production_ledger_untouched": True},
            receipts={"drift": False, "cli_version": {"version": "v0"},
                      "digests_match": {"a": True},
                      "documented_shapes_accepted": True,
                      "refused_all": True,
                      "trigger_issued_during_revalidation": False},
            pins={"accepted_inputs_all_match": True,
                  "documentation_discrepancies": []},
            identity={"name": "administrator",
                      "sid": "S-1-5-21-1-2-3-500"},
            ledger_after={"tip_digest": "sha256:t", "total_records": 1,
                          "intent_records": 0,
                          "partial_or_corrupt_records": 0,
                          "audit_ok": True, "legacy_reader_ok": True},
            now="2026-09-11T00:00:00Z",
        )

    def test_completion_evidence_and_authorization_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            evidence_dir = Path(tmp)
            (evidence_dir / "path-validation.json").write_text("{}",
                                                              encoding="utf-8")
            manifest = u12.assemble_manifest(evidence_dir=evidence_dir,
                                             **self._inputs())
            completion = manifest["completion_evidence"]
            self.assertTrue(completion["production_ledger_path_exact"])
            self.assertTrue(completion["outside_all_forbidden_trees"])
            self.assertFalse(completion["unexpected_preexisting_state"])
            self.assertEqual(completion["absolute_root_capability"], "6/6")
            self.assertEqual(completion["partial_or_corrupt_records"], 0)
            self.assertFalse(manifest["r0_canary_authorized"])
            self.assertFalse(completion["r0_canary_authorized"])
            self.assertIn("path-validation.json",
                          manifest["evidence_files"])
            self.assertTrue(manifest["manifest_digest"].startswith("sha256:"))


class PinVerificationTests(unittest.TestCase):
    def test_real_pins_reproduce_and_discrepancy_recorded(self):
        result = u12.verify_pins(ROOT)
        self.assertTrue(result["accepted_inputs_all_match"], result["checks"])
        row = result["checks"]["o2_report_test_row"]
        self.assertFalse(row["match"])
        self.assertEqual(row["finding"], "U12-P0-F1")
        self.assertFalse(row["blocking"])
        self.assertEqual(len(result["documentation_discrepancies"]), 1)

    def test_bundle_digest_ignores_missing_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "a.txt").write_text("a", encoding="utf-8")
            (tmp / "capture").mkdir()
            (tmp / "capture" / "x.json").write_text("{}", encoding="utf-8")
            all_files = u12.bundle_digest(tmp)
            excluded = u12.bundle_digest(tmp, exclude=("capture/",))
            self.assertEqual(all_files["file_count"], 2)
            self.assertEqual(excluded["file_count"], 1)
            self.assertNotEqual(all_files["digest"], excluded["digest"])


if __name__ == "__main__":
    unittest.main()
