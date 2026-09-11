#!/usr/bin/env python3
"""YZT-88 focused tests — strict explicit Findings source binding.

Synthetic in-memory/temporary stores only; no live canonical, runtime, or
platform writes. Each Validation Focus row of FINDINGS_SOURCE_BINDING_DESIGN
is exercised here or in the R0/pipeline suites named in the coverage matrix.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff_compose as compose  # noqa: E402
import chandoff_finalize as finalize  # noqa: E402
import chandoff_findings_source as cfs  # noqa: E402
import chandoff_plan as plan  # noqa: E402
import chandoff_selfcheck as selfcheck  # noqa: E402

TASK_REF = "multica://issue/YZT-88"
ROLE = "software-engineer"
PROJECT = "web-imagegen"
AUTHORITY_TEXT = "Lead disposition: accept the authorized Findings root"
AUTHORITY_DIGEST = "sha256:" + hashlib.sha256(
    AUTHORITY_TEXT.encode("utf-8")).hexdigest()
RUNTIME = {"commit": "0" * 40, "adapter_digest": "sha256:" + "0" * 64}


def finding(fid="FIND-WIMG-T88-000001", *, status="open", task="YZT-88",
            project=PROJECT, intent="observation", verification="verified",
            discovered_by=ROLE, summary="t88 finding"):
    return {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": fid,
        "project_id": project,
        "task_id": task,
        "summary": summary,
        "detail": None,
        "intent": intent,
        "source_refs": [],
        "discovered_by": discovered_by,
        "status": status,
        "verification": verification,
        "created_at": "2026-09-11T00:00:00Z",
    }


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def make_binding(root, *, allowed=None, authority=None, runtime=None,
                 layout=cfs.LAYOUT_FLAT, source_id="findings-test-root"):
    return {
        "schema": cfs.BINDING_SCHEMA,
        "source_id": source_id,
        "project_id": PROJECT,
        "root": str(root),
        "layout": layout,
        "authority": authority or {
            "comment_id": "c-1", "issue_id": "i-1", "author_id": "a-1",
            "author_type": "agent", "digest": AUTHORITY_DIGEST},
        "allowed": allowed or {"task_refs": [TASK_REF], "roles": [ROLE]},
        "runtime": runtime or dict(RUNTIME),
        "created_at": "2026-09-11T00:00:00Z",
    }


class FakeResolver:
    """Stand-in authenticated CLI resolver (live content, not a capture file)."""

    kind = cfs.RESOLVER_AUTHENTICATED_CLI
    is_production = True

    def __init__(self, content=AUTHORITY_TEXT, **overrides):
        self.content = content
        self.overrides = overrides
        self.calls = 0

    def __call__(self, authority):
        self.calls += 1
        out = {"comment_id": authority["comment_id"],
               "issue_id": authority["issue_id"],
               "author_id": authority["author_id"],
               "author_type": authority["author_type"],
               "content": self.content}
        out.update(self.overrides)
        return out


class FakeAuthenticatedCli:
    """Fake authenticated transport whose live comments are independent of
    any local capture file. Each comment_thread call is a fresh read."""

    simulation_transport = True

    def __init__(self, comments):
        self.comments = {c["id"]: dict(c) for c in comments}
        self.calls = []

    def comment_thread(self, issue_id, comment_id):
        self.calls.append((issue_id, comment_id))
        rec = self.comments.get(comment_id)
        if rec is None or rec.get("issue_id") not in (None, issue_id):
            return []
        return [dict(rec)]

    def edit(self, comment_id, **fields):
        self.comments[comment_id].update(fields)

    def revoke(self, comment_id):
        self.comments.pop(comment_id, None)


def bound_source(root, *, resolver=None, **binding_kwargs):
    return cfs.BoundFindingsSource(
        make_binding(root, **binding_kwargs), resolver=resolver or FakeResolver(),
        project_id=PROJECT, expected_commit=RUNTIME["commit"],
        expected_adapter_digest=RUNTIME["adapter_digest"])


def sample_request(task_ref=TASK_REF, role=ROLE):
    return {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": task_ref,
        "project": {"project_id": PROJECT},
        "target": {"role": role},
        "purpose": "implementation",
        "task_snapshot": {
            "title": "Binding repair for the Findings source",
            "description": "Implement strict source binding and snapshots.",
            "requirements": ["no production writes"],
            "acceptance_criteria": ["frozen schemas unchanged"],
            "relevant_decisions": ["reuse frozen T00"],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }


def build_envelope(request=None, *, findings=None):
    request = request or sample_request()
    env = plan.prepare_handoff_plan(request, findings=list(findings or []))
    assert env["status"] == "PLAN_READY", env
    accepted = compose.compose_semantic(env, compose.subset_result(env["plan"]))
    assert accepted["status"] == "ACCEPTED", accepted
    out = finalize.finalize_handoff(env, accepted, request,
                                    clock=lambda: "2026-09-11T00:00:00Z")
    assert out["status"] == "READY", out
    return out


class BindingValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "findings"
        self.root.mkdir()
        self.resolver = FakeResolver()

    def _refuse(self, binding, code=None, **kwargs):
        kwargs.setdefault("project_id", PROJECT)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(binding, resolver=self.resolver, **kwargs)
        if code:
            self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_valid_binding_is_accepted(self):
        source = bound_source(self.root)
        self.assertEqual(source.source_id, "findings-test-root")
        self.assertTrue(source.binding_digest.startswith("sha256:"))

    def test_relative_root_and_unsupported_keys_refused(self):
        binding = make_binding(self.root)
        binding["root"] = "relative/findings"
        self._refuse(binding, "findings_source_invalid")
        binding = make_binding(self.root)
        binding["extra"] = True
        self._refuse(binding, "findings_source_invalid")
        binding = make_binding(self.root)
        binding["layout"] = "recursive-v1"
        self._refuse(binding, "findings_source_invalid")
        binding = make_binding(self.root)
        binding["runtime"] = dict(RUNTIME, commit="deadbeef")
        self._refuse(binding, "findings_source_invalid")
        binding = make_binding(self.root)
        binding["authority"]["digest"] = "not-a-digest"
        self._refuse(binding, "findings_source_invalid")
        binding = make_binding(self.root)
        binding["allowed"]["task_refs"] = []
        self._refuse(binding, "findings_source_invalid")

    def test_project_and_allowed_scope_refusals(self):
        self._refuse(make_binding(self.root), "findings_source_unbound",
                     project_id="other-project")
        source = bound_source(self.root)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE",
                        task_ref="multica://issue/YZT-99", role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role="qa")
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_authority_refusals(self):
        binding = make_binding(self.root)
        source = cfs.BoundFindingsSource(binding, resolver=None)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        wrong = FakeResolver(content="different body")
        source = cfs.BoundFindingsSource(binding, resolver=wrong)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        forged = FakeResolver(author_id="attacker")
        source = cfs.BoundFindingsSource(binding, resolver=forged)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertIn("author_id", ctx.exception.message)

    def test_runtime_pin_mismatch_refused(self):
        source = bound_source(self.root)
        source.expected_commit = "f" * 40
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_authority_is_reverified_on_every_read(self):
        resolver = FakeResolver()
        source = cfs.BoundFindingsSource(make_binding(self.root),
                                         resolver=resolver, project_id=PROJECT)
        source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        source.read(boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(resolver.calls, 2)


class SnapshotReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "findings"
        self.root.mkdir()

    def read(self, **kwargs):
        return bound_source(self.root).read(
            boundary="PREPARE", task_ref=TASK_REF, role=ROLE, **kwargs)

    def test_empty_directory_is_a_positive_complete_read(self):
        snap = self.read()
        self.assertEqual(snap["observation"]["total_records"], 0)
        self.assertEqual(snap["observation"]["open_count"], 0)
        self.assertEqual(snap["observation"]["inventory"], [])
        self.assertTrue(snap["observation"]["snapshot_digest"].startswith(
            "sha256:"))
        again = self.read()
        self.assertEqual(snap["observation"]["snapshot_digest"],
                         again["observation"]["snapshot_digest"])

    def test_processed_only_is_nonempty_with_zero_open(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json",
                   finding(status="processed"))
        snap = self.read()
        self.assertEqual(snap["observation"]["total_records"], 1)
        self.assertEqual(snap["observation"]["open_count"], 0)
        self.assertEqual(snap["observation"]["open_ids"], [])

    def test_missing_and_file_root_refused(self):
        missing = Path(self.tmp.name) / "nope"
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            bound_source(missing).read(boundary="PREPARE", task_ref=TASK_REF,
                                       role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_missing")
        file_root = Path(self.tmp.name) / "file-root"
        file_root.write_text("x", encoding="utf-8")
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            bound_source(file_root).read(boundary="PREPARE", task_ref=TASK_REF,
                                         role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unreadable")

    def test_denied_enumeration_and_denied_file_refused(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json", finding())

        class DeniedScan(cfs.FilesystemReader):
            def scandir(self, root):
                raise PermissionError("denied by test")

        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(make_binding(self.root),
                                    resolver=FakeResolver(),
                                    reader=DeniedScan()).read(
                boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unreadable")

        class DeniedRead(cfs.FilesystemReader):
            def read_bytes(self, path):
                raise PermissionError("denied by test")

        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(make_binding(self.root),
                                    resolver=FakeResolver(),
                                    reader=DeniedRead()).read(
                boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unreadable")

    def test_incomplete_or_malformed_inventory_refused(self):
        good = "FIND-WIMG-T88-000001.json"
        cases = {
            "truncated.json": "{\"schema_version\": \"1.1\"",
            "hidden.json": json.dumps(finding()),
            ".hidden": json.dumps(finding()),
            "unexpected.txt": json.dumps(finding()),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "findings"
                    root.mkdir()
                    (root / name).write_text(text, encoding="utf-8")
                    with self.assertRaises(cfs.FindingsSourceRefusal):
                        bound_source(root).read(boundary="PREPARE",
                                                task_ref=TASK_REF, role=ROLE)
        # nested directory
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "findings"
            (root / "nested").mkdir(parents=True)
            with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
                bound_source(root).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)
            self.assertEqual(ctx.exception.code, "findings_source_invalid")
        # valid JSON but wrong filename/id and invalid schema
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "findings"
            root.mkdir()
            (root / "FIND-OTHER-000001.json").write_text(
                json.dumps(finding()), encoding="utf-8")
            with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
                bound_source(root).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)
            self.assertEqual(ctx.exception.code, "findings_source_invalid")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "findings"
            root.mkdir()
            bad = finding()
            bad["status"] = "maybe"
            (root / f"{good}.json").write_text(json.dumps(bad),
                                               encoding="utf-8")
            with self.assertRaises(cfs.FindingsSourceRefusal):
                bound_source(root).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)

    def test_duplicate_json_key_and_nan_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "findings"
            root.mkdir()
            text = json.dumps(finding())
            text = text.replace('"task_id": "YZT-88"',
                                '"task_id": "YZT-88", "task_id": "YZT-99"', 1)
            (root / "FIND-WIMG-T88-000001.json").write_text(
                text, encoding="utf-8")
            with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
                bound_source(root).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)
            self.assertEqual(ctx.exception.code, "findings_source_invalid")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "findings"
            root.mkdir()
            text = json.dumps(finding()).replace('"detail": null',
                                                 '"detail": NaN')
            (root / "FIND-WIMG-T88-000001.json").write_text(
                text, encoding="utf-8")
            with self.assertRaises(cfs.FindingsSourceRefusal):
                bound_source(root).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)

    def test_case_fold_collision_and_directory_entry_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "findings"
            root.mkdir()
            write_json(root / "FIND-WIMG-T88-000001.json", finding())
            write_json(root / "find-wimg-t88-000001.json", finding())
            if (root / "FIND-WIMG-T88-000001.json").samefile(
                    root / "find-wimg-t88-000001.json"):
                self.skipTest("case-insensitive filesystem cannot hold "
                              "a case-fold collision")
            with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
                bound_source(root).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)
            self.assertIn(ctx.exception.code,
                          ("findings_source_ambiguous",
                           "findings_source_invalid"))

    def test_change_while_reading_refused(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json", finding())

        class MovingReader(cfs.FilesystemReader):
            def __init__(self, root):
                self.root = root
                self.calls = 0

            def scandir(self, root):
                self.calls += 1
                if self.calls == 2:
                    write_json(root / "FIND-WIMG-T88-000002.json",
                               finding("FIND-WIMG-T88-000002"))
                return super().scandir(root)

        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(
                make_binding(self.root), resolver=FakeResolver(),
                reader=MovingReader(self.root)).read(
                boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_changed")

    def test_symlink_entry_refused_via_direntry(self):
        """Code-path proof that does not depend on OS symlink privileges."""
        class SymlinkEntry:
            def __init__(self, name, path):
                self.name = name
                self.path = str(path)

            def is_symlink(self):
                return True

            def is_file(self, follow_symlinks=False):
                return False

        class SymlinkReader(cfs.FilesystemReader):
            def scandir(self, root):
                return [SymlinkEntry("FIND-WIMG-T88-000001.json",
                                     Path(root) / "FIND-WIMG-T88-000001.json")]

        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.BoundFindingsSource(
                make_binding(self.root), resolver=FakeResolver(),
                reader=SymlinkReader()).read(
                boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_ambiguous")
        self.assertIn("symlink", ctx.exception.message.lower()
                      + ctx.exception.detail.lower())

    def test_os_reparse_or_junction_root_refused_when_available(self):
        """OS-level reparse proof. Falls back to the DirEntry path, never skip."""
        target = Path(self.tmp.name) / "real-root"
        target.mkdir()
        write_json(target / "FIND-WIMG-T88-000001.json", finding())
        link = Path(self.tmp.name) / "reparse-root"
        created = False
        if os.name == "nt":
            proc = os.system(f'cmd /c mklink /J "{link}" "{target}" >NUL 2>&1')
            created = proc == 0 and link.exists()
        else:
            try:
                os.symlink(target, link, target_is_directory=True)
                created = link.exists()
            except OSError:
                created = False
        if created:
            with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
                bound_source(link).read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)
            self.assertEqual(ctx.exception.code, "findings_source_ambiguous")
            return
        # Junction/symlink not available: the DirEntry test above is the
        # refusal proof. Assert the reader still rejects is_symlink entries.
        self.test_symlink_entry_refused_via_direntry()


class DriftAndJoinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "findings"
        self.root.mkdir()
        self.source = bound_source(self.root)
        self.prepare = self.source.read(boundary="PREPARE", task_ref=TASK_REF,
                                        role=ROLE)

    def test_whole_store_drift_refused(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json", finding())
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            self.source.read(boundary="SELF_CHECK", task_ref=TASK_REF,
                             role=ROLE,
                             prior_observation=self.prepare["observation"])
        self.assertEqual(ctx.exception.code, "findings_source_changed")
        detail = ctx.exception.detail
        self.assertIn("->", detail)

    def test_change_and_rename_between_boundaries_refused(self):
        path = write_json(self.root / "FIND-WIMG-T88-000001.json", finding())
        baseline = self.source.read(boundary="PREPARE", task_ref=TASK_REF,
                                    role=ROLE)
        # change the record content
        write_json(path, finding(summary="edited summary"))
        with self.assertRaises(cfs.FindingsSourceRefusal):
            self.source.read(boundary="SELF_CHECK", task_ref=TASK_REF,
                             role=ROLE,
                             prior_observation=baseline["observation"])
        # restore and rename
        write_json(path, finding())
        os.replace(path, self.root / "FIND-WIMG-T88-000002.json")
        with self.assertRaises(cfs.FindingsSourceRefusal):
            self.source.read(boundary="SELF_CHECK", task_ref=TASK_REF,
                             role=ROLE,
                             prior_observation=baseline["observation"])

    def test_identical_bytes_at_a_different_root_refused(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json", finding())
        baseline = self.source.read(boundary="PREPARE", task_ref=TASK_REF,
                                    role=ROLE)
        other = Path(self.tmp.name) / "other-root"
        other.mkdir()
        write_json(other / "FIND-WIMG-T88-000001.json", finding())
        relocated = cfs.BoundFindingsSource(
            make_binding(other, source_id="findings-relocated"),
            resolver=FakeResolver(), project_id=PROJECT)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            relocated.read(boundary="SELF_CHECK", task_ref=TASK_REF,
                           role=ROLE,
                           prior_observation=baseline["observation"])
        self.assertEqual(ctx.exception.code, "findings_source_changed")

    def test_authority_revocation_between_boundaries_refused(self):
        resolver = FakeResolver()
        source = cfs.BoundFindingsSource(make_binding(self.root),
                                         resolver=resolver,
                                         project_id=PROJECT)
        source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        resolver.content = "revoked by the owner"
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_expected_snapshot_digest_mismatch_refused(self):
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            self.source.read(boundary="SELF_CHECK", task_ref=TASK_REF,
                             role=ROLE,
                             expected_snapshot_digest="sha256:" + "0" * 64)
        self.assertEqual(ctx.exception.code, "findings_source_changed")

    def test_observation_join_verification(self):
        observation = self.prepare["observation"]
        self.assertEqual(cfs.verify_observation(
            observation, task_ref=TASK_REF, role=ROLE), [])
        self.assertIn("observation join role",
                      cfs.verify_observation(observation, role="qa"))
        tampered = copy.deepcopy(observation)
        tampered["join"]["task_ref"] = "multica://issue/OTHER"
        self.assertIn("observation join task_ref",
                      cfs.verify_observation(tampered, task_ref=TASK_REF))
        boundary_problems = cfs.verify_observation(observation,
                                                   boundary="ARM")
        self.assertTrue(any("observation boundary" in p
                            for p in boundary_problems), boundary_problems)

    def test_worker_entry_verification(self):
        entry = cfs.verify_worker_entry(
            self.source, task_ref=TASK_REF, role=ROLE,
            prior_observation=self.prepare["observation"])
        self.assertEqual(entry["open_ids"], [])
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.verify_worker_entry(
                self.source, task_ref=TASK_REF, role=ROLE,
                expected_snapshot_digest="sha256:" + "0" * 64)
        self.assertEqual(ctx.exception.code, "findings_source_changed")
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            cfs.verify_worker_entry(cfs.SyntheticFindingsSource([]),
                                    task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        entry = cfs.verify_worker_entry(
            cfs.SyntheticFindingsSource([]), task_ref=TASK_REF, role=ROLE,
            allow_simulation=True)
        self.assertTrue(entry["observation"]["simulation"])


class SyntheticSourceTests(unittest.TestCase):
    def test_simulation_marker_and_duplicates(self):
        src = cfs.SyntheticFindingsSource([finding(), finding()])
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            src.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_invalid")
        one = cfs.SyntheticFindingsSource([finding(), finding(
            "FIND-WIMG-T88-000002", status="processed")])
        snap = one.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.assertTrue(snap["observation"]["simulation"])
        self.assertFalse(one.is_production)
        self.assertEqual(snap["observation"]["total_records"], 2)
        self.assertEqual(snap["observation"]["open_count"], 1)


class EngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "findings"
        self.root.mkdir()
        self.source = bound_source(self.root)

    def test_prepare_reads_bound_source_and_accounts_records(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json",
                   finding("FIND-WIMG-T88-000001", task="YZT-46"))
        write_json(self.root / "FIND-WIMG-T88-000002.json",
                   finding("FIND-WIMG-T88-000002", status="processed"))
        result = plan.prepare_handoff_plan(
            sample_request(), findings_source=self.source)
        self.assertEqual(result["status"], "PLAN_READY", result)
        observation = result["findings_observation"]
        self.assertIsNotNone(observation)
        self.assertEqual(observation["total_records"], 2)
        self.assertEqual(observation["open_count"], 1)
        gate = result["finding_gate"]
        self.assertEqual(gate["status"], "CLEAR")
        self.assertTrue(gate["detached"])
        excluded = {row["finding_id"]: row["reason"]
                    for row in gate["exclusions"]}
        self.assertEqual(excluded["FIND-WIMG-T88-000001"],
                         "task_not_associated")
        self.assertEqual(gate["considered_open_count"], 1)

    def test_conflicting_inputs_refused(self):
        result = plan.prepare_handoff_plan(
            sample_request(), findings_source=self.source, findings=[])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["escalation"]["reason"],
                         "findings_source_ambiguous")
        self.assertIsNone(result["finding_gate"])
        legacy_store = plan.MemoryFindingStore([])
        result = plan.prepare_handoff_plan(
            sample_request(), findings_source=self.source, store=legacy_store)
        self.assertEqual(result["escalation"]["reason"],
                         "findings_source_ambiguous")

    def test_source_refusal_is_typed_blocked(self):
        missing = Path(self.tmp.name) / "absent"
        result = plan.prepare_handoff_plan(
            sample_request(), findings_source=bound_source(missing))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["escalation"]["reason"],
                         "findings_source_missing")
        self.assertEqual(result["findings_source_refusal"]["code"],
                         "findings_source_missing")
        self.assertIsNone(result["plan"])

    def test_material_finding_blocks_and_is_recorded(self):
        write_json(self.root / "FIND-WIMG-T88-000001.json",
                   finding(intent="durable_candidate",
                           verification="unverified",
                           summary="provider routing ownership question"))
        result = plan.prepare_handoff_plan(
            sample_request(), findings_source=self.source)
        self.assertEqual(result["status"], "BLOCKED")
        gate = result["finding_gate"]
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertTrue(gate["blocked_findings"])
        self.assertIsNotNone(result["findings_observation"])

    def test_safe_disposition_is_detached_read_only(self):
        path = write_json(
            self.root / "FIND-WIMG-T88-000001.json",
            finding(intent="task_delivery", summary="routine delivery note"))
        before_bytes = path.read_bytes()
        before_stat = path.stat()
        result = plan.prepare_handoff_plan(
            sample_request(), findings_source=self.source)
        self.assertEqual(result["status"], "PLAN_READY", result)
        gate = result["finding_gate"]
        self.assertTrue(gate["detached"])
        self.assertEqual([f["finding_id"] for f in gate["processed_findings"]],
                         ["FIND-WIMG-T88-000001"])
        self.assertEqual(path.read_bytes(), before_bytes)
        after_stat = path.stat()
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["status"], "open")

    def test_selfcheck_uses_bound_source_and_detects_drift(self):
        request = sample_request()
        envelope = build_envelope(request)
        prepare = self.source.read(boundary="PREPARE", task_ref=TASK_REF,
                                   role=ROLE)
        sc_request = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": TASK_REF,
            "role": ROLE,
            "task_snapshot": request["task_snapshot"],
        }
        current = dict(envelope["built_from"])
        trace = selfcheck.self_check_with_trace(
            sc_request, packages=[envelope], current=current,
            findings_source=self.source,
            findings_prior_observation=prepare["observation"])
        self.assertEqual(trace["result"]["status"], "READY",
                         trace["result"])
        self.assertIsNotNone(trace["findings_observation"])
        self.assertIsNone(trace["findings_source_refusal"])
        # drift after the accepted prepare baseline
        write_json(self.root / "FIND-WIMG-T88-000009.json",
                   finding("FIND-WIMG-T88-000009"))
        drift = selfcheck.self_check_with_trace(
            sc_request, packages=[envelope], current=current,
            findings_source=self.source,
            findings_prior_observation=prepare["observation"])
        self.assertEqual(drift["result"]["status"], "REFRESH_REQUIRED")
        self.assertIn("package_stale", drift["result"]["reasons"])
        self.assertEqual(drift["findings_source_refusal"]["code"],
                         "findings_source_changed")

    def test_selfcheck_conflicting_inputs_refused(self):
        request = sample_request()
        envelope = build_envelope(request)
        sc_request = {
            "schema_version": "1.1",
            "kind": "self_check_request",
            "task_ref": TASK_REF,
            "role": ROLE,
            "task_snapshot": request["task_snapshot"],
        }
        with self.assertRaises(ValueError):
            selfcheck.self_check_with_trace(
                sc_request, packages=[envelope], findings_source=self.source,
                findings=[], current=dict(envelope["built_from"]))
        with self.assertRaises(ValueError):
            selfcheck.self_check_with_trace(
                sc_request, packages=[envelope], findings_source=self.source,
                finding_store=plan.MemoryFindingStore([]),
                current=dict(envelope["built_from"]))

    def test_binding_omitted_refuses_at_pipeline_boundary(self):
        import importlib.util
        skill = (TOOLS.parent / "skills" / "multica-context-handoff" /
                 "scripts" / "handoff_pipeline.py")
        spec = importlib.util.spec_from_file_location(
            "handoff_pipeline_t88", skill)
        pipeline = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(pipeline)
        sys.dont_write_bytecode = False
        out = Path(self.tmp.name) / "out"
        out.mkdir(parents=True, exist_ok=True)
        envelope_file = write_json(out / "envelope.json",
                                   build_envelope(sample_request()))
        request_file = write_json(out / "self-check-request.json", {
            "schema_version": "1.1", "kind": "self_check_request",
            "task_ref": TASK_REF, "role": ROLE,
            "task_snapshot": sample_request()["task_snapshot"],
        })
        ns = type("NS", (), {})()
        ns.repo = str(TOOLS.parent)
        ns.issue = None
        ns.task_ref = None
        ns.role = None
        ns.request_file = str(request_file)
        ns.request_from = None
        ns.package_ref = None
        ns.envelope_file = [str(envelope_file)]
        ns.store = None
        ns.executable = "multica"
        ns.out_dir = str(out / "stage")
        ns.artifact_store_file = None
        ns.artifact_requirements_file = None
        ns.artifact_review_level = None
        ns.findings_source_binding_file = None
        ns.findings_authority_file = None
        ns.findings_authority_cli = None
        ns.findings_authority_capture_only = False
        ns.findings_evidence_file = None
        ns.findings_source_expect_commit = None
        ns.findings_source_expect_adapter_digest = None
        ns.observer_run_id = None
        payload, code = pipeline.run_selfcheck(ns)
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["error"]["code"], "findings_source_unbound")
        self.assertEqual(payload["stage"], "findings")


ADD_RESPONSE = {
    "id": "01a0-new-0000000000000000000a",
    "created_at": "2026-09-11T00:00:01Z",
    "parent_id": None,
    "content": "/note",
}

_PIPELINE = None


def pipeline_module():
    global _PIPELINE
    if _PIPELINE is None:
        import importlib.util
        skill = (TOOLS.parent / "skills" / "multica-context-handoff" /
                 "scripts" / "handoff_pipeline.py")
        spec = importlib.util.spec_from_file_location(
            "handoff_pipeline_t88", skill)
        mod = importlib.util.module_from_spec(spec)
        sys.dont_write_bytecode = True
        spec.loader.exec_module(mod)
        sys.dont_write_bytecode = False
        _PIPELINE = mod
    return _PIPELINE


def make_note_cli(*, on_add=None, refuse_on_write=False):
    import chandoff_note as note

    def runner(argv):
        if argv[1:4] == ["issue", "comment", "list"]:
            return 0, json.dumps([]), ""
        if argv[1:] == ["version", "--output", "json"]:
            return 0, json.dumps({"version": "v0.4.41"}), ""
        if argv[1:4] == ["issue", "comment", "add"]:
            if refuse_on_write:
                raise AssertionError("unauthorized write attempted")
            if on_add is not None:
                on_add()
            return 0, json.dumps(ADD_RESPONSE), ""
        raise AssertionError(f"unexpected argv {argv}")

    return note.NoteCli(executable="multica", runner=runner)


class PipelinePublicationDriftTests(unittest.TestCase):
    """Source changes around the single publication are refused before send
    and preserved (no second note) after a possible send."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name) / "case"
        self.base.mkdir()
        self.root = self.base / "findings"
        self.root.mkdir()
        binding = make_binding(self.root, source_id="pipeline-drift")
        self.binding_file = write_json(self.base / "binding.json", binding)
        self.authority_file = write_json(self.base / "authority.json", {
            "comment_id": "c-1", "issue_id": "i-1", "author_id": "a-1",
            "author_type": "agent", "content": AUTHORITY_TEXT})
        self.authority_cli = FakeAuthenticatedCli([{
            "id": "c-1", "issue_id": "i-1", "author_id": "a-1",
            "author_type": "agent", "content": AUTHORITY_TEXT}])
        source = cfs.BoundFindingsSource(
            binding,
            resolver=cfs.AuthenticatedCommentResolver(self.authority_cli),
            project_id=PROJECT,
            expected_commit=RUNTIME["commit"],
            expected_adapter_digest=RUNTIME["adapter_digest"])
        observation = source.read(boundary="PREPARE", task_ref=TASK_REF,
                                  role=ROLE)["observation"]
        self.evidence_file = write_json(self.base / "evidence.json",
                                        observation)
        self.envelope = build_envelope(sample_request())
        self.result_file = write_json(self.base / "result.json",
                                      self.envelope)

    def _publish_ns(self):
        import types
        ns = types.SimpleNamespace(
            repo=str(TOOLS.parent), issue="YZT-88",
            result_file=str(self.result_file),
            prepared_by="01 Engineering Lead", prepared_at=None, parent=None,
            allow_partial=False, dry_run=False, authorize_publish=True,
            executable="multica",
            artifact_store_file=None, artifact_requirements_file=None,
            artifact_review_level=None,
            findings_source_binding_file=str(self.binding_file),
            findings_authority_file=str(self.authority_file),
            findings_authority_cli=self.authority_cli,
            findings_authority_capture_only=False,
            findings_evidence_file=str(self.evidence_file),
            findings_source_expect_commit=RUNTIME["commit"],
            findings_source_expect_adapter_digest=RUNTIME["adapter_digest"],
            observer_run_id="t88-observer")
        return ns

    def test_drift_before_send_blocks_publication_with_zero_writes(self):
        pipeline = pipeline_module()
        write_json(self.root / "FIND-WIMG-T88-000001.json", finding())
        cli = make_note_cli(refuse_on_write=True)
        payload, code = pipeline.run_publish(self._publish_ns(),
                                             note_cli_factory=lambda: cli)
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["error"]["code"], "findings_source_changed")
        self.assertEqual(cli.commands, [])

    def test_drift_after_send_preserves_receipt_and_second_note_never_sent(self):
        pipeline = pipeline_module()
        wrote = {"n": 0}

        def on_add():
            if wrote["n"] == 0:
                write_json(self.root / "FIND-WIMG-T88-000002.json", finding(
                    "FIND-WIMG-T88-000002"))
            wrote["n"] += 1

        cli = make_note_cli(on_add=on_add)
        payload, code = pipeline.run_publish(self._publish_ns(),
                                             note_cli_factory=lambda: cli)
        self.assertEqual(code, pipeline.BOUNDED_EXIT)
        self.assertEqual(payload["error"]["code"],
                         "findings_source_changed_after_send")
        self.assertTrue(payload["publication"]["published"])
        adds = [argv for argv in cli.commands
                if argv[:3] == ["issue", "comment", "add"]]
        self.assertEqual(len(adds), 1)


class OrchestratorWorldOverrideTests(unittest.TestCase):
    """Production world.findings cannot shadow a verified source binding."""

    def _handoff(self, module, source):
        import chandoff_assignment as asm
        import types
        recorder = types.SimpleNamespace(transaction_id="tx-t88")
        if module == "assignment":
            return asm.AssignmentHandoff(
                validated={"caller_role": "engineering-lead"},
                target_role_spec="software-engineer", recorder=recorder,
                ledger=None, compose_fn=lambda *a, **k: None,
                clock=lambda: "2026-09-11T00:00:00Z", bundle_dir=None,
                finding_store=None, world={"findings": []}, policy=None,
                workdir=Path("."), executable="multica",
                findings_source=source)
        import chandoff_mention as men
        return men.MentionHandoff(
            validated={"caller_role": "engineering-lead"},
            target_role_spec="software-engineer", recorder=recorder,
            ledger=None, compose_fn=lambda *a, **k: None,
            clock=lambda: "2026-09-11T00:00:00Z", bundle_dir=None,
            finding_store=None, world={"findings": []}, policy=None,
            workdir=Path("."), executable="multica",
            findings_source=source)

    def test_assignment_world_findings_override_refused(self):
        import chandoff_assignment as asm
        source = cfs.SyntheticFindingsSource([])
        handoff = self._handoff("assignment", source)
        with self.assertRaises(asm._Stop) as ctx:
            handoff._plan(sample_request())
        self.assertEqual(ctx.exception.status, asm.INVALID_INPUT)

    def test_mention_world_findings_override_refused(self):
        import chandoff_mention as men
        source = cfs.SyntheticFindingsSource([])
        handoff = self._handoff("mention", source)
        with self.assertRaises(men._Stop) as ctx:
            handoff._plan(sample_request())
        self.assertEqual(ctx.exception.status, men.INVALID_INPUT)


class AuthenticatedAuthorityTests(unittest.TestCase):
    """Platform edit/revocation with an unchanged local capture must refuse."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "findings"
        self.root.mkdir()
        write_json(self.root / "FIND-WIMG-T88-000001.json", finding())
        self.capture = write_json(Path(self.tmp.name) / "capture.json", {
            "comment_id": "c-1", "issue_id": "i-1", "author_id": "a-1",
            "author_type": "agent", "content": AUTHORITY_TEXT})
        self.live = FakeAuthenticatedCli([{
            "id": "c-1", "issue_id": "i-1", "author_id": "a-1",
            "author_type": "agent", "content": AUTHORITY_TEXT}])
        self.binding = make_binding(self.root)

    def test_live_cli_read_succeeds_and_is_fresh_each_boundary(self):
        resolver = cfs.AuthenticatedCommentResolver(self.live)
        source = cfs.BoundFindingsSource(
            self.binding, resolver=resolver, project_id=PROJECT)
        source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        source.read(boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(len(self.live.calls), 2)
        self.assertTrue(source.is_production)

    def test_platform_edit_with_unchanged_capture_refused(self):
        resolver = cfs.AuthenticatedCommentResolver(self.live)
        source = cfs.BoundFindingsSource(
            self.binding, resolver=resolver, project_id=PROJECT)
        source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.live.edit("c-1", content="revoked and replaced on the platform")
        capture = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(capture["content"], AUTHORITY_TEXT)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")

    def test_platform_revocation_with_unchanged_capture_refused(self):
        resolver = cfs.AuthenticatedCommentResolver(self.live)
        source = cfs.BoundFindingsSource(
            self.binding, resolver=resolver, project_id=PROJECT)
        source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
        self.live.revoke("c-1")
        capture = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(capture["content"], AUTHORITY_TEXT)
        with self.assertRaises(cfs.FindingsSourceRefusal) as ctx:
            source.read(boundary="SELF_CHECK", task_ref=TASK_REF, role=ROLE)
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertIn("revoked", ctx.exception.message.lower())

    def test_capture_only_source_is_not_production(self):
        source = cfs.BoundFindingsSource(
            self.binding, resolver=cfs.CaptureFileResolver(self.capture),
            project_id=PROJECT)
        self.assertFalse(source.is_production)
        snap = source.read(boundary="OPERATOR_READ")
        self.assertEqual(snap["observation"]["open_count"], 1)

    def test_capture_only_cannot_publish_through_pipeline(self):
        pipeline = pipeline_module()
        ns = type("NS", (), {})()
        ns.repo = str(TOOLS.parent)
        ns.findings_source_binding_file = str(
            write_json(Path(self.tmp.name) / "binding.json", self.binding))
        ns.findings_authority_file = str(self.capture)
        ns.findings_authority_cli = None
        ns.findings_authority_capture_only = True
        ns.findings_evidence_file = None
        ns.findings_source_expect_commit = None
        ns.findings_source_expect_adapter_digest = None
        with self.assertRaises(pipeline.PipelineError) as ctx:
            pipeline._load_findings_source(
                ns, pipeline._tools(Path(ns.repo)),
                project_id=PROJECT, stage="publish")
        self.assertEqual(ctx.exception.code, "findings_source_unbound")
        self.assertIn("capture-only", ctx.exception.message.lower())


class OmittedBindingEntrypointTests(unittest.TestCase):
    """Absent binding => zero publication/trigger on production entrypoints."""

    def test_assignment_omitted_binding_zero_effects(self):
        import chandoff_assignment as asm
        import chandoff_dispatch as dispatch
        issued = []

        def runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        result = asm.run_assignment_handoff(
            {"title": "Omitted binding assignment drill",
             "description": "Prove omitted Findings binding issues zero "
                            "native publication or trigger effects.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=runner, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-omit-asg",
            legacy_fixture=False)
        self.assertEqual(result["terminal_status"], asm.INVALID_INPUT)
        self.assertEqual(issued, [])
        self.assertIn("binding", result.get("stop_reason", "").lower())

    def test_mention_omitted_binding_zero_effects(self):
        import chandoff_mention as men
        import chandoff_dispatch as dispatch
        issued = []

        def runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        result = men.run_mention_handoff(
            {"issue_id": "22222222-0000-0000-0000-000000000065",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=runner, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-omit-men",
            legacy_fixture=False, stage="ready")
        self.assertEqual(result["terminal_status"], men.INVALID_INPUT)
        self.assertEqual(issued, [])

    def test_assignment_legacy_fixture_refuses_real_transport(self):
        import chandoff_assignment as asm
        import chandoff_dispatch as dispatch
        issued = []

        def runner(argv):
            issued.append(list(argv))
            return 0, "{}", ""

        result = asm.run_assignment_handoff(
            {"title": "Legacy fixture real transport drill",
             "description": "Prove legacy unbound Findings cannot issue "
                            "real publication or trigger effects.",
             "project_id": PROJECT, "purpose": "implementation"},
            caller_role="engineering-lead",
            target_role_spec="software-engineer",
            runner=runner, ledger=dispatch.TransactionLedger(),
            compose_fn=lambda *a, **k: None, transaction_id="tx-legacy-real",
            legacy_fixture=True)
        self.assertEqual(result["terminal_status"], asm.INVALID_INPUT)
        self.assertEqual(issued, [])
        self.assertIn("simulation", result.get("stop_reason", "").lower())

    def test_r0_non_simulation_runner_omitted_binding_zero_effects(self):
        import chandoff_intent as o2
        import u12_r0_binding as u12

        class LiveRunner:
            def __init__(self):
                self.commands = []

            def __call__(self, argv):
                self.commands.append(list(argv))
                return 1, "", "live runner must not be used"

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        runner = LiveRunner()
        store = o2.DurableIntentStore(Path(tmp.name) / "ledger.jsonl")
        factory = u12.build_r0b_factory(store, runner=runner)
        self.assertTrue(factory.require_findings_source)
        self.assertIsNone(factory.findings_source)
        with self.assertRaises(cfs.FindingsSourceRefusal):
            factory._gate_findings_effects("R0 trigger")
        self.assertEqual(runner.commands, [])


if __name__ == "__main__":
    unittest.main()

