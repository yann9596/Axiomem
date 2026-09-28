"""Isolated temporary Canonical/SQLite tests. Never touches the production index."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import index_builder as builder


class IndexLifecycleAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.team = self.root / "team-context"
        self.projects = self.root / "project-context"
        self.db = self.root / "index/v1.1/memory.db"
        self.patches = patch.multiple(builder, ROOT=self.root, TEAM=self.team,
                                      PROJECTS=self.projects, DB=self.db)
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.write("team-context/registry/projects.yaml", """projects:
  - id: sample
    name: Sample
    phase: archived
    context_repo:
      repo: sample-memory
""")
        self.write("team-context/checkpoint.yaml", """scope:
  type: team
confirmed: []
open: []
conflicts: []
""")
        self.write("project-context/sample/project.yaml", """project_id: sample
governance:
  mission: Test mission
""")
        self.write("team-context/roles/qa.yaml", "role_id: qa\nversion: 1\n")
        self.fact = "project-context/sample/facts/FACT-SAMPLE-1.yaml"
        self.write(self.fact, self.object_text("FACT-SAMPLE-1", "superseded"))
        self.write("team-context/rules/RULE-SAMPLE-1.yaml",
                   self.object_text("RULE-SAMPLE-1", "revoked"))
        self.write("project-context/sample/cases/CASE-SAMPLE-1.yaml",
                   self.object_text("CASE-SAMPLE-1", "review_needed"))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def object_text(oid, status):
        return f"""id: {oid}
title: 中文 title
statement: current bounded statement
status: {status}
verification: partially_verified
scope:
  type: project
  project_id: sample
source_refs:
  - repo://sample@abc123/file.md
relations:
  supersedes:
    - FACT-SAMPLE-0
updated_at: 2026-09-28
"""

    def rows(self, query, params=()):
        conn = sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    def test_preserves_fact_rule_case_lifecycle(self):
        builder.rebuild()
        actual = dict(self.rows("SELECT id,status FROM memory_object"))
        self.assertEqual(actual["FACT-SAMPLE-1"], "superseded")
        self.assertEqual(actual["RULE-SAMPLE-1"], "revoked")
        self.assertEqual(actual["CASE-SAMPLE-1"], "review_needed")

    def test_preserves_active_lifecycle(self):
        self.write(self.fact, self.object_text("FACT-SAMPLE-1", "active"))
        builder.rebuild()
        self.assertEqual(self.rows("SELECT status FROM memory_object WHERE id='FACT-SAMPLE-1'"),
                         [("active",)])

    def test_anchor_phase_not_rewritten_as_fact_status(self):
        builder.rebuild()
        self.assertEqual(self.rows("SELECT phase FROM project_registry_cache"), [("archived",)])
        self.assertEqual(self.rows("SELECT status FROM memory_object WHERE kind='anchor'"), [("active",)])

    def test_canonical_provenance_path_and_verification(self):
        builder.rebuild()
        row = self.rows("SELECT path,verification FROM memory_object WHERE id='FACT-SAMPLE-1'")[0]
        self.assertEqual(row, (self.fact, "partially_verified"))

    def test_sources_and_relations_retained(self):
        builder.rebuild()
        self.assertEqual(self.rows("SELECT source_ref FROM memory_source_ref WHERE object_id='FACT-SAMPLE-1'"),
                         [("repo://sample@abc123/file.md",)])
        self.assertEqual(self.rows("SELECT relation_type,target FROM memory_relation WHERE object_id='FACT-SAMPLE-1'"),
                         [("supersedes", "FACT-SAMPLE-0")])

    def test_metadata_and_read_only_freshness(self):
        report = builder.rebuild()
        before = self.db.read_bytes()
        settings = dict(self.rows("SELECT key,value FROM settings"))
        self.assertEqual(settings["canonical_revision"], report["canonical_revision"])
        self.assertEqual(settings["embedding_provider"], "disabled")
        self.assertTrue(builder.check()["ok"])
        self.assertEqual(before, self.db.read_bytes())

    def test_missing_index_not_created_by_check(self):
        self.assertEqual(builder.check(), {"ok": False, "reason": "index_missing"})
        self.assertFalse(self.db.exists())

    def test_changed_source_is_stale(self):
        builder.rebuild()
        self.write(self.fact, self.object_text("FACT-SAMPLE-1", "active"))
        self.assertEqual(builder.check()["reason"], "index_stale")

    def test_source_addition_is_stale(self):
        builder.rebuild()
        self.write("project-context/sample/facts/FACT-SAMPLE-2.yaml",
                   self.object_text("FACT-SAMPLE-2", "active"))
        self.assertEqual(builder.check()["reason"], "index_stale")

    def test_source_deletion_is_stale(self):
        builder.rebuild()
        (self.root / self.fact).unlink()
        self.assertEqual(builder.check()["reason"], "index_stale")

    def test_role_change_is_stale(self):
        builder.rebuild()
        self.write("team-context/roles/qa.yaml", "role_id: qa\nversion: 2\n")
        self.assertEqual(builder.check()["reason"], "index_stale")

    def test_legacy_index_requires_rebuild(self):
        builder.rebuild()
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("DELETE FROM settings WHERE key='index_build_version'")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(builder.check()["reason"], "index_builder_version_mismatch")

    def test_rebuild_preserves_unrelated_files(self):
        sentinel = self.write("index/v1.1/operator-notes.txt", "do not remove")
        builder.rebuild()
        builder.rebuild()
        self.assertEqual(sentinel.read_text(), "do not remove")

    def test_rebuild_failure_keeps_old_index_and_cleans_lock(self):
        builder.rebuild()
        before = self.db.read_bytes()
        with patch.object(builder, "_insert", side_effect=ValueError("injected failure")):
            with self.assertRaisesRegex(ValueError, "injected"):
                builder.rebuild()
        self.assertEqual(before, self.db.read_bytes())
        self.assertFalse(self.db.with_name("memory.db.rebuild.lock").exists())
        self.assertEqual(list(self.db.parent.glob(".memory-*.db")), [])

    def test_missing_status_refused_without_replacing_old(self):
        builder.rebuild()
        before = self.db.read_bytes()
        self.write(self.fact, self.object_text("FACT-SAMPLE-1", "active").replace("status: active\n", ""))
        with self.assertRaisesRegex(ValueError, "missing canonical lifecycle status"):
            builder.rebuild()
        self.assertEqual(before, self.db.read_bytes())

    def test_source_drift_during_rebuild_refused(self):
        builder.rebuild()
        before = self.db.read_bytes()
        original = builder._write_database
        def write_then_mutate(path, snapshot):
            report = original(path, snapshot)
            self.write(self.fact, self.object_text("FACT-SAMPLE-1", "active"))
            return report
        with patch.object(builder, "_write_database", side_effect=write_then_mutate):
            with self.assertRaisesRegex(RuntimeError, "canonical changed"):
                builder.rebuild()
        self.assertEqual(before, self.db.read_bytes())

    def test_concurrent_builder_lock_is_not_deleted(self):
        builder.rebuild()
        lock = self.db.with_name("memory.db.rebuild.lock")
        lock.write_text("other-builder")
        with self.assertRaises(FileExistsError):
            builder.rebuild()
        self.assertEqual(lock.read_text(), "other-builder")

    def test_sqlite_sidecars_refuse_replacement(self):
        builder.rebuild()
        before = self.db.read_bytes()
        self.write("index/v1.1/memory.db-wal", "other writer")
        with self.assertRaisesRegex(RuntimeError, "sidecars"):
            builder.rebuild()
        self.assertEqual(before, self.db.read_bytes())

    def test_os_replace_failure_keeps_old(self):
        builder.rebuild()
        before = self.db.read_bytes()
        with patch.object(builder.os, "replace", side_effect=PermissionError("open handle")):
            with self.assertRaises(PermissionError):
                builder.rebuild()
        self.assertEqual(before, self.db.read_bytes())
        self.assertFalse(self.db.with_name("memory.db.rebuild.lock").exists())

    def test_rebuild_does_not_modify_canonical(self):
        before = builder._snapshot()
        builder.rebuild()
        self.assertEqual(before, builder._snapshot())

    def test_stable_rebuild_revision(self):
        first = builder.rebuild()
        second = builder.rebuild()
        self.assertEqual(first["canonical_revision"], second["canonical_revision"])

    def test_corrupt_database_fails_closed(self):
        self.write("index/v1.1/memory.db", "not a database")
        self.assertEqual(builder.check()["reason"], "index_check_failed")


if __name__ == "__main__":
    unittest.main()
