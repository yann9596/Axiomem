#!/usr/bin/env python3
"""Gate A inventory/map reconciliation fail-closed tests (YZT-42)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (TOOLS, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from caccount import (  # noqa: E402
    derived_inventory, mapped_legacy_ids, normalize_legacy, reconcile, run_accounting,
)
from yaml_mini import parse_yaml  # noqa: E402
from failclosed_fixture import build_missing_evidence_root  # noqa: E402


class AccountingUnitTests(unittest.TestCase):
    def test_normalize_strips_parenthetical_suffix(self):
        self.assertEqual(
            normalize_legacy("memory/app1-project-context.json (governance part)"),
            "memory/app1-project-context.json")

    def test_empty_inventory_is_not_100_percent(self):
        result = reconcile([], {"memory/x.json": {"legacy": "memory/x.json"}})
        self.assertEqual(result["legacy_objects_accounted_for"], 0.0)
        self.assertFalse(result["complete"])
        self.assertEqual(result["inventory_count"], 0)

    def test_silent_drop_is_inventory_minus_map(self):
        result = reconcile(
            ["memory/a.json", "memory/b.json", "sources/c.json"],
            {"memory/a.json": {"legacy": "memory/a.json"}},
        )
        self.assertEqual(result["silent_drop"], 2)
        self.assertEqual(result["silent_drop_ids"], ["memory/b.json", "sources/c.json"])
        self.assertLess(result["legacy_objects_accounted_for"], 100)
        self.assertFalse(result["complete"])

    def test_full_set_reconcile_is_100(self):
        ids = ["memory/a.json", "chains/b.json"]
        mapped = {i: {"legacy": i} for i in ids}
        result = reconcile(ids, mapped)
        self.assertEqual(result["legacy_objects_accounted_for"], 100.0)
        self.assertEqual(result["silent_drop"], 0)
        self.assertTrue(result["complete"])

    def test_group_level_signals_section_is_not_per_object(self):
        doc = parse_yaml(
            "signals:\n"
            "  action: reclassify_keep_as_evidence\n"
            "  note: all 11 signals listed in prose\n"
            "candidates:\n"
            "  - legacy: sources/candidates/one.json\n"
            "    action: finding_ingest\n"
        )
        mapped = mapped_legacy_ids(doc)
        self.assertEqual(list(mapped), ["sources/candidates/one.json"])
        self.assertNotIn("signals", mapped)

    def test_missing_map_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory").mkdir()
            (root / "memory" / "unit.json").write_text("{}", encoding="utf-8")
            (root / "migration").mkdir()
            result = run_accounting(root=root)
            self.assertTrue(result["fail_closed"])
            self.assertIn("migration/migration-map.yaml", result["missing_evidence"])
            self.assertNotEqual(result["legacy_objects_accounted_for"], 100)

    def test_corrupt_explicit_inventory_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "memory").mkdir()
            (root / "memory" / "unit.json").write_text("{}", encoding="utf-8")
            mig = root / "migration"
            mig.mkdir()
            (mig / "migration-map.yaml").write_text(
                "candidates:\n  - legacy: memory/unit.json\n    action: transform\n",
                encoding="utf-8")
            (mig / "legacy-inventory.yaml").write_text(
                "schema_version: \"1.1\"\nkind: wrong_kind\nobjects: []\n",
                encoding="utf-8")
            result = run_accounting(root=root, map_path=mig / "migration-map.yaml",
                                    inventory_path=mig / "legacy-inventory.yaml")
            self.assertTrue(result["fail_closed"])
            self.assertTrue(result["errors"])

    def test_derived_inventory_from_real_repo_is_nonempty(self):
        items = derived_inventory()
        ids = {i["id"] for i in items}
        self.assertIn("memory/governance-owner-boundaries.json", ids)
        self.assertGreaterEqual(len(items), 20)

    def test_real_map_does_not_silently_claim_100(self):
        """Isolated fixture: missing inventory + grouped-signal map must not claim 100%."""
        with tempfile.TemporaryDirectory() as tmp:
            root = build_missing_evidence_root(Path(tmp))
            result = run_accounting(root=root)
            # Signals are grouped in prose, not per-object legacy entries.
            self.assertGreater(result["silent_drop"], 0)
            self.assertEqual(
                result["silent_drop_ids"],
                ["sources/signal-a.json", "sources/signal-b.json"])
            self.assertNotEqual(result["legacy_objects_accounted_for"], 100)
            self.assertFalse(result["complete"])
            self.assertIn("migration/legacy-inventory.yaml", result["missing_evidence"])
            self.assertTrue(result["fail_closed"])

            (root / "migration" / "migration-map.yaml").unlink()
            missing_map = run_accounting(root=root)
            self.assertTrue(missing_map["fail_closed"])
            self.assertIn("migration/migration-map.yaml", missing_map["missing_evidence"])
            self.assertNotEqual(missing_map["legacy_objects_accounted_for"], 100)
            self.assertGreater(missing_map["silent_drop"], 0)


if __name__ == "__main__":
    unittest.main()
