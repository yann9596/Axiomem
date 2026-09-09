#!/usr/bin/env python3
"""T05 focused tests — read-only Multica Issue Snapshot Adapter (YZT-58).

All CLI interaction is mocked/captured JSON; no network, no model, no
Multica writes. The optional live test runs only when the deployed `multica`
CLI is present and exercises read-only commands against a real issue.
"""
from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import chandoff_adapter as adapter  # noqa: E402
from chandoff import canonical_json, fingerprint_from_request  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

FIXTURES = TOOLS / "fixtures" / "adapter"

DESCRIPTION = """## Objective

Implement the demo adapter behavior for the demo task.

## Required work

- stable task reference construction
- explicit project mapping
  - nested determinism note
- read-only boundary

## Acceptance criteria

1. request validates against frozen schema
2. output is byte-stable

## Done criteria

```yaml
done:
  request_schema_valid: true
  deterministic_snapshot: true
```

## Notes

- progress chatter that must never enter the snapshot
"""

ISSUE_ID = "11111111-1111-1111-1111-111111111111"
PARENT_ID = "22222222-2222-2222-2222-222222222222"

ISSUE_A = {
    "id": ISSUE_ID,
    "identifier": "YZT-99",
    "title": "Implement demo adapter behavior",
    "description": DESCRIPTION,
    "parent_issue_id": PARENT_ID,
    "project_id": None,
    "assignee_id": "agent-8bc546ab",
    "assignee_type": "agent",
    "labels": [],
    "stage": 5,
    "position": -3,
    "revision": 2,
    "status": "in_progress",
    "metadata": {},
    "properties": {},
    "priority": "high",
    "created_at": "2026-09-09T00:00:00Z",
    "updated_at": "2026-09-09T00:00:00Z",
}

PARENT_A = {
    "id": PARENT_ID,
    "identifier": "YZT-90",
    "title": "Parent epic",
    "description": "Parent body.",
    "parent_issue_id": None,
    "project_id": "19a54e43-57eb-4823-8bd2-4e026addcea7",
}

PROGRESS_COMMENT = {
    "id": "cccccccc-0000-0000-0000-000000000001",
    "author_id": "agent-fa7d16a7",
    "author_type": "agent",
    "content": "progress chatter: started work, will report later",
    "created_at": "2026-09-09T01:00:00Z",
    "issue_id": ISSUE_ID,
    "parent_id": None,
}

DECISION_COMMENT = {
    "id": "dddddddd-0000-0000-0000-000000000002",
    "author_id": "member-1338bca6",
    "author_type": "member",
    "content": "Human Decision\n\nGO for the demo adapter with explicit mapping.",
    "created_at": "2026-09-09T02:00:00Z",
    "issue_id": ISSUE_ID,
    "parent_id": None,
}


def fake_cli(issue=ISSUE_A, parent=PARENT_A, threads=None, version="v9.9.9-test",
             raw_issue=None, exit_code=0, stderr="", raw_threads=None):
    threads = threads or {}
    calls = {"argv": []}

    def runner(argv):
        calls["argv"].append(list(argv))
        if exit_code:
            return exit_code, "", stderr
        tail = argv[1:]
        if tail[:1] == ["version"]:
            return 0, json.dumps({"version": version}), ""
        if tail[:2] == ["issue", "get"]:
            target = tail[2]
            if raw_issue is not None and target == issue["id"]:
                return 0, raw_issue, ""
            if issue is not None and target == issue["id"]:
                return 0, json.dumps(issue), ""
            if parent is not None and target == parent["id"]:
                return 0, json.dumps(parent), ""
            return 1, "", f"error: resource not found: {target}"
        if tail[:3] == ["issue", "comment", "list"]:
            cid = tail[tail.index("--thread") + 1]
            if raw_threads is not None and cid in raw_threads:
                return 0, raw_threads[cid], ""
            if cid in threads:
                return 0, json.dumps(threads[cid]), ""
            return 1, "", f"error: comment not found: {cid}"
        return 2, "", "unexpected command"

    cli = adapter.MulticaCli(runner=runner)
    cli.calls = calls
    return cli


def build(**kwargs):
    kwargs.setdefault("issue_id", ISSUE_ID)
    kwargs.setdefault("target_role", "software-engineer")
    kwargs.setdefault("caller_role", "engineering-lead")
    kwargs.setdefault("purpose", "implementation")
    kwargs.setdefault("explicit_project_id", "web-imagegen")
    return adapter.build_snapshot_request(**kwargs)


def tree_manifest(root: Path) -> list:
    out = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out.append([path.relative_to(root).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest()])
    return out


class TaskRefTests(unittest.TestCase):
    def test_stable_multica_uri(self):
        cli = fake_cli()
        result = build(cli=cli)
        self.assertEqual(result["request"]["task_ref"], "multica://issue/YZT-99")

    def test_task_ref_idempotent_across_repeated_builds(self):
        first = build(cli=fake_cli())
        second = build(cli=fake_cli())
        self.assertEqual(first["request"]["task_ref"], second["request"]["task_ref"])
        self.assertEqual(first["task_fingerprint"], second["task_fingerprint"])

    def test_parent_task_ref_maps_parent_identifier(self):
        result = build(cli=fake_cli())
        self.assertEqual(result["request"]["task_snapshot"]["parent_task_ref"],
                         "multica://issue/YZT-90")
        self.assertEqual(result["trace"]["parent"],
                         {"present": True, "task_ref": "multica://issue/YZT-90"})

    def test_no_parent_omits_parent_task_ref(self):
        issue = dict(ISSUE_A, parent_issue_id=None)
        result = build(cli=fake_cli(issue=issue))
        self.assertNotIn("parent_task_ref", result["request"]["task_snapshot"])
        self.assertEqual(result["trace"]["parent"], {"present": False})

    def test_parent_change_does_not_move_fingerprint(self):
        parent_b = dict(PARENT_A, identifier="YZT-88")
        a = build(cli=fake_cli(parent=PARENT_A))
        b = build(cli=fake_cli(parent=parent_b))
        self.assertNotEqual(a["request"], b["request"])
        self.assertEqual(a["task_fingerprint"], b["task_fingerprint"])


class SnapshotMappingTests(unittest.TestCase):
    def test_title_description_requirements_acceptance_mapping(self):
        result = build(cli=fake_cli())
        snap = result["request"]["task_snapshot"]
        self.assertEqual(snap["title"], ISSUE_A["title"])
        self.assertEqual(snap["description"], DESCRIPTION)
        self.assertEqual(snap["requirements"], [
            "stable task reference construction",
            "explicit project mapping",
            "nested determinism note",
            "read-only boundary",
        ])
        self.assertEqual(snap["acceptance_criteria"], [
            "request validates against frozen schema",
            "output is byte-stable",
            "request_schema_valid: true",
            "deterministic_snapshot: true",
        ])

    def test_extraction_provenance_is_exact(self):
        result = build(cli=fake_cli())
        prov = result["trace"]["extraction"]
        self.assertEqual(prov["requirements"][0], {
            "source": "issue_description", "marker": "requirements",
            "heading": "Required work", "heading_line": 5, "item_line": 7,
        })
        acc = prov["acceptance_criteria"]
        self.assertEqual(acc[0]["heading"], "Acceptance criteria")
        self.assertEqual(acc[2]["heading"], "Done criteria")
        self.assertEqual(acc[2]["item_line"], 21)

    def test_ordinary_sections_never_enter_structured_lists(self):
        result = build(cli=fake_cli())
        snap = result["request"]["task_snapshot"]
        for bucket in (snap["requirements"], snap["acceptance_criteria"],
                       snap["relevant_decisions"]):
            self.assertNotIn(
                "progress chatter that must never enter the snapshot", bucket)

    def test_marker_decisions_admitted_with_provenance(self):
        result = build(cli=fake_cli(), decision_markers=["human decision"])
        snap = result["request"]["task_snapshot"]
        self.assertNotIn("human decision", DESCRIPTION.lower())
        issue = dict(ISSUE_A, description=DESCRIPTION +
                     "\n## Human decision\n\n- approved: explicit mapping only\n")
        result = build(cli=fake_cli(issue=issue), decision_markers=["human decision"])
        decisions = result["request"]["task_snapshot"]["relevant_decisions"]
        self.assertEqual(decisions, ["approved: explicit mapping only"])
        prov = result["trace"]["extraction"]["relevant_decisions"][0]
        self.assertEqual(prov["source"], "issue_description")
        self.assertEqual(prov["decision_marker"], "human decision")
        self.assertEqual(prov["heading"], "Human decision")

    def test_no_decisions_by_default(self):
        result = build(cli=fake_cli())
        self.assertEqual(
            result["request"]["task_snapshot"]["relevant_decisions"], [])


class ProjectMappingTests(unittest.TestCase):
    def test_explicit_project_id_succeeds(self):
        result = build(cli=fake_cli(), explicit_project_id="web-imagegen")
        self.assertEqual(result["request"]["project"], {"project_id": "web-imagegen"})
        self.assertEqual(result["trace"]["project_mapping"]["mode"], "explicit_caller")

    def test_map_file_lookup_succeeds(self):
        result = build(cli=fake_cli(issue=dict(ISSUE_A, project_id=PARENT_A["project_id"])),
                       explicit_project_id=None,
                       project_map={PARENT_A["project_id"]: "web-imagegen"})
        self.assertEqual(result["request"]["project"], {"project_id": "web-imagegen"})
        self.assertEqual(result["trace"]["project_mapping"]["mode"], "explicit_map")

    def test_null_project_fails_closed(self):
        with self.assertRaises(adapter.ProjectMappingError) as ctx:
            build(cli=fake_cli(), explicit_project_id=None)
        self.assertEqual(ctx.exception.code, "project_mapping_unresolved")

    def test_unmapped_project_fails_closed(self):
        with self.assertRaises(adapter.ProjectMappingError):
            build(cli=fake_cli(issue=dict(ISSUE_A, project_id="99999999-9999-9999-9999-999999999999")),
                  explicit_project_id=None)

    def test_multica_project_uuid_is_never_a_registry_id(self):
        issue = dict(ISSUE_A, project_id="19a54e43-57eb-4823-8bd2-4e026addcea7")
        with self.assertRaises(adapter.ProjectMappingError):
            build(cli=fake_cli(issue=issue), explicit_project_id=None)
        ok = build(cli=fake_cli(issue=issue),
                   project_map={"19a54e43-57eb-4823-8bd2-4e026addcea7": "web-imagegen"})
        self.assertEqual(ok["request"]["project"], {"project_id": "web-imagegen"})


class CliFailureTests(unittest.TestCase):
    def test_malformed_json_is_bounded(self):
        with self.assertRaises(adapter.CliJsonError) as ctx:
            build(cli=fake_cli(raw_issue="{not json"))
        self.assertEqual(ctx.exception.code, "cli_json_malformed")

    def test_missing_contract_fields_stops_bounded(self):
        raw = json.dumps({k: v for k, v in ISSUE_A.items() if k != "identifier"})
        with self.assertRaises(adapter.CliContractError) as ctx:
            build(cli=fake_cli(raw_issue=raw))
        self.assertEqual(ctx.exception.code, "incompatible_cli_contract")
        self.assertEqual(ctx.exception.details["missing"], ["identifier"])

    def test_command_failure_is_bounded(self):
        with self.assertRaises(adapter.CliCommandError) as ctx:
            build(cli=fake_cli(exit_code=1, stderr="error: permission denied"))
        self.assertEqual(ctx.exception.code, "cli_command_failed")
        self.assertIn("permission denied", ctx.exception.message)

    def test_missing_cli_is_bounded(self):
        def boom(argv):
            raise FileNotFoundError("multica not found")
        cli = adapter.MulticaCli(runner=boom)
        with self.assertRaises(adapter.CliUnavailableError):
            build(cli=cli)

    def test_parent_fetch_failure_is_bounded(self):
        issue = dict(ISSUE_A, parent_issue_id="33333333-3333-3333-3333-333333333333")
        with self.assertRaises(adapter.AdapterError) as ctx:
            build(cli=fake_cli(issue=issue, parent=None))
        self.assertTrue(ctx.exception.message.startswith("parent issue fetch failed"))


class DeterminismTests(unittest.TestCase):
    THREADS = {DECISION_COMMENT["id"]: [DECISION_COMMENT],
               PROGRESS_COMMENT["id"]: [PROGRESS_COMMENT]}

    def test_repeated_output_is_byte_stable(self):
        kwargs = dict(cli=fake_cli(threads=self.THREADS),
                      decision_comment_ids=[DECISION_COMMENT["id"]],
                      decision_markers=["required work"])
        first = build(**kwargs)
        second = build(**kwargs)
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_document_order_is_preserved(self):
        result = build(cli=fake_cli())
        req = result["request"]["task_snapshot"]["requirements"]
        self.assertEqual(req.index("stable task reference construction"),
                         0)
        self.assertLess(req.index("explicit project mapping"),
                        req.index("read-only boundary"))

    def test_selection_order_follows_caller_order(self):
        result = build(cli=fake_cli(threads=self.THREADS),
                       decision_comment_ids=[DECISION_COMMENT["id"],
                                             PROGRESS_COMMENT["id"]])
        self.assertEqual(result["request"]["task_snapshot"]["relevant_decisions"],
                         [DECISION_COMMENT["content"], PROGRESS_COMMENT["content"]])


class OrdinaryCommentExclusionTests(unittest.TestCase):
    def test_no_comment_fetch_by_default(self):
        cli = fake_cli(threads={PROGRESS_COMMENT["id"]: [PROGRESS_COMMENT]})
        result = build(cli=cli)
        for argv in result["trace"]["cli"]["commands"]:
            self.assertNotIn("comment", argv)
        self.assertEqual(result["trace"]["excluded_by_default"]["comment_fetches"], 0)

    def test_progress_comments_never_reach_request_or_fingerprint(self):
        threads = {PROGRESS_COMMENT["id"]: [PROGRESS_COMMENT]}
        result = build(cli=fake_cli(threads=threads))
        text = canonical_json(result)
        self.assertNotIn("started work, will report later", text)
        self.assertEqual(result["task_fingerprint"],
                         fingerprint_from_request(result["request"]))


class DecisionSelectionTests(unittest.TestCase):
    def test_selected_comment_admitted_with_exact_provenance(self):
        result = build(cli=fake_cli(threads={DECISION_COMMENT["id"]: [DECISION_COMMENT]}),
                       decision_comment_ids=[DECISION_COMMENT["id"]])
        decisions = result["request"]["task_snapshot"]["relevant_decisions"]
        self.assertEqual(decisions, [DECISION_COMMENT["content"]])
        prov = result["trace"]["extraction"]["relevant_decisions"][0]
        self.assertEqual(prov, {
            "source": "selected_comment",
            "comment_id": DECISION_COMMENT["id"],
            "author_id": DECISION_COMMENT["author_id"],
            "author_type": DECISION_COMMENT["author_type"],
            "created_at": DECISION_COMMENT["created_at"],
            "issue_id": ISSUE_ID,
        })

    def test_unselected_comments_stay_out(self):
        result = build(cli=fake_cli(threads={DECISION_COMMENT["id"]: [DECISION_COMMENT]}),
                       decision_comment_ids=[DECISION_COMMENT["id"]])
        self.assertNotIn(PROGRESS_COMMENT["content"], canonical_json(result))

    def test_unknown_selected_comment_is_bounded(self):
        with self.assertRaises(adapter.CliCommandError):
            build(cli=fake_cli(),
                  decision_comment_ids=["ffffffff-0000-0000-0000-00000000000f"])
        raw = json.dumps([dict(DECISION_COMMENT, id="eeeeeeee-0000-0000-0000-000000000003")])
        with self.assertRaises(adapter.SelectionError):
            build(cli=fake_cli(raw_threads={DECISION_COMMENT["id"]: raw}),
                  decision_comment_ids=[DECISION_COMMENT["id"]])


class FrozenSchemaTests(unittest.TestCase):
    def test_request_validates_against_frozen_schema(self):
        result = build(cli=fake_cli(threads={DECISION_COMMENT["id"]: [DECISION_COMMENT]}),
                       decision_comment_ids=[DECISION_COMMENT["id"]])
        schema = load_schema_file(adapter.REQUEST_SCHEMA)
        self.assertEqual(Schema(schema, schema).validate(result["request"], path="$"), [])

    def test_invalid_caller_role_fails_closed(self):
        with self.assertRaises(adapter.SchemaViolationError):
            build(cli=fake_cli(), explicit_project_id="web-imagegen", caller_role="wizard")

    def test_invalid_project_id_pattern_fails_closed(self):
        with self.assertRaises(adapter.SchemaViolationError):
            build(cli=fake_cli(), explicit_project_id="Web_ImageGen")

    def test_blank_purpose_fails_closed(self):
        with self.assertRaises(adapter.SchemaViolationError):
            build(cli=fake_cli(), explicit_project_id="web-imagegen", purpose="  ")


class NoLeakTests(unittest.TestCase):
    def test_multica_fields_never_leak_into_request(self):
        result = build(cli=fake_cli(), explicit_project_id="web-imagegen")
        request = result["request"]
        self.assertEqual(set(request), {
            "schema_version", "kind", "task_ref", "project", "target",
            "purpose", "task_snapshot", "caller", "options"})
        self.assertEqual(set(request["task_snapshot"]),
                         {"title", "description", "requirements",
                          "acceptance_criteria", "relevant_decisions",
                          "parent_task_ref"})
        text = canonical_json(request)
        for token in ("assignee", "labels", "stage", "position", "revision",
                      "subscriber", "metadata", "properties", "squad"):
            self.assertNotIn(token, text)

    def test_assignee_never_mapped_even_when_present(self):
        result = build(cli=fake_cli(), explicit_project_id="web-imagegen")
        self.assertFalse(result["trace"]["assignee"]["mapped"])
        self.assertEqual(result["request"].get("assignee"), None)

    def test_options_stay_framework_neutral(self):
        with self.assertRaises(adapter.ForbiddenOptionsError):
            build(cli=fake_cli(), explicit_project_id="web-imagegen",
                  options={"assignee": "someone"})
        ok = build(cli=fake_cli(), explicit_project_id="web-imagegen",
                   options={"limit": 8})
        self.assertEqual(ok["request"]["options"], {"limit": 8})


class SideEffectTests(unittest.TestCase):
    def test_memory_trees_untouched_and_guarantees_hold(self):
        root = TOOLS.parent
        before = {name: tree_manifest(root / name)
                  for name in ("team-context", "project-context", "index")}
        result = build(cli=fake_cli(), explicit_project_id="web-imagegen")
        after = {name: tree_manifest(root / name)
                 for name in ("team-context", "project-context", "index")}
        self.assertEqual(before, after)
        guarantees = result["trace"]["guarantees"]
        self.assertFalse(guarantees["llm_called"])
        self.assertEqual(guarantees["canonical_writes"], 0)
        self.assertEqual(guarantees["memory_reads"], 0)
        self.assertEqual(guarantees["memory_rebuilds"], 0)
        self.assertEqual(guarantees["multica_write_commands"], 0)

    def test_issued_commands_are_read_only_allowlisted(self):
        result = build(cli=fake_cli(threads={DECISION_COMMENT["id"]: [DECISION_COMMENT]}),
                       decision_comment_ids=[DECISION_COMMENT["id"]])
        expected = [
            ["version", "--output", "json"],
            ["issue", "get", ISSUE_ID, "--output", "json"],
            ["issue", "get", PARENT_ID, "--output", "json"],
            ["issue", "comment", "list", ISSUE_ID, "--thread",
             DECISION_COMMENT["id"], "--full", "--output", "json"],
        ]
        self.assertEqual(result["trace"]["cli"]["commands"], expected)

    def test_write_commands_are_refused(self):
        cli = adapter.MulticaCli(runner=lambda argv: (0, "{}", ""))
        for argv in (["issue", "update", "x", "--status", "done"],
                     ["issue", "comment", "add", "x"],
                     ["issue", "assign", "x"],
                     ["run", "trigger", "x"]):
            with self.assertRaises(adapter.AdapterError):
                cli._run(argv)
        self.assertEqual(cli.commands, [])

    def test_adapter_source_imports_are_allowlisted(self):
        allowed = {"chandoff", "schema_mini", "__future__", "argparse", "json",
                   "re", "subprocess", "sys", "pathlib", "typing"}
        source = (TOOLS / "chandoff_adapter.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                self.assertIn(name.split(".")[0], allowed)

    def test_adapter_source_has_no_memory_llm_or_network_paths(self):
        source = (TOOLS / "chandoff_adapter.py").read_text(encoding="utf-8")
        for token in ("urllib", "http.client", "socket", "ssl", "openai",
                      "anthropic", "memory_revision", "resolve_scope",
                      "scope_allows", "load_registry", "cbuild", "cdata",
                      "cutil", "crole", "cauthority", "index_builder"):
            self.assertNotIn(token, source)


class OfflineFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.issue_file = FIXTURES / "issue_get_yzt58.json"
        cls.parent_file = FIXTURES / "issue_get_parent_yzt39.json"
        cls.thread_file = FIXTURES / "comment_thread_yzt56_report.json"

    def build_offline(self, **kwargs):
        kwargs.setdefault("issue_id", "01a085f5-ac12-7738-a64d-a7d18394708e")
        kwargs.setdefault("target_role", "software-engineer")
        kwargs.setdefault("caller_role", "engineering-lead")
        kwargs.setdefault("purpose", "implementation")
        kwargs.setdefault("explicit_project_id", "web-imagegen")
        kwargs.setdefault("issue_file", str(self.issue_file))
        kwargs.setdefault("parent_file", str(self.parent_file))
        return build(**kwargs)

    def test_real_captured_issue_builds_valid_request(self):
        result = self.build_offline()
        snap = result["request"]["task_snapshot"]
        self.assertEqual(result["request"]["task_ref"], "multica://issue/YZT-58")
        self.assertEqual(snap["title"], "CTX-HO-05 — Implement Multica Issue Snapshot Adapter")
        self.assertIn("read-only Multica Issue Snapshot Adapter", snap["description"])
        self.assertIn("multica://issue/YZT-N", " ".join(snap["requirements"]))
        self.assertIn("arbitrary_comments_in_fingerprint: 0",
                      " ".join(snap["acceptance_criteria"]))
        self.assertEqual(snap["parent_task_ref"], "multica://issue/YZT-39")
        schema = load_schema_file(adapter.REQUEST_SCHEMA)
        self.assertEqual(Schema(schema, schema).validate(result["request"], path="$"), [])

    def test_offline_output_is_byte_stable(self):
        first = self.build_offline(
            decision_comment_ids=["01a085f1-c4bf-7b6c-af91-3c7160e38c0d"],
            thread_files={"01a085f1-c4bf-7b6c-af91-3c7160e38c0d": str(self.thread_file)})
        second = self.build_offline(
            decision_comment_ids=["01a085f1-c4bf-7b6c-af91-3c7160e38c0d"],
            thread_files={"01a085f1-c4bf-7b6c-af91-3c7160e38c0d": str(self.thread_file)})
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_offline_selected_comment_provenance(self):
        result = self.build_offline(
            decision_comment_ids=["01a085f1-c4bf-7b6c-af91-3c7160e38c0d"],
            thread_files={"01a085f1-c4bf-7b6c-af91-3c7160e38c0d": str(self.thread_file)})
        prov = result["trace"]["extraction"]["relevant_decisions"][0]
        self.assertEqual(prov["comment_id"], "01a085f1-c4bf-7b6c-af91-3c7160e38c0d")
        self.assertEqual(prov["author_id"], "8bc546ab-ffd8-4aa6-ad30-58583346c065")
        self.assertEqual(prov["source"], "selected_comment")

    def test_offline_missing_parent_fixture_is_bounded(self):
        kwargs = dict(issue_id="01a085f5-ac12-7738-a64d-a7d18394708e",
                      target_role="software-engineer", caller_role="engineering-lead",
                      purpose="implementation", explicit_project_id="web-imagegen",
                      issue_file=str(self.issue_file))
        with self.assertRaises(adapter.AdapterError) as ctx:
            build(**kwargs)
        self.assertIn("offline parent fixture required", ctx.exception.message)


@unittest.skipUnless(shutil.which("multica"), "deployed multica CLI not available")
class LiveDeployedCliTests(unittest.TestCase):
    """Read-only evidence against the deployed CLI (v0.4.41)."""

    def test_live_snapshot_is_byte_stable_and_side_effect_free(self):
        root = TOOLS.parent
        before = {name: tree_manifest(root / name)
                  for name in ("team-context", "project-context", "index")}
        kwargs = dict(issue_id="YZT-58", target_role="software-engineer",
                      caller_role="engineering-lead", purpose="implementation",
                      explicit_project_id="web-imagegen")
        first = build(**kwargs)
        second = build(**dict(kwargs, cli=adapter.MulticaCli()))
        self.assertEqual(canonical_json(first), canonical_json(second))
        after = {name: tree_manifest(root / name)
                 for name in ("team-context", "project-context", "index")}
        self.assertEqual(before, after)
        self.assertEqual(first["request"]["task_ref"], "multica://issue/YZT-58")
        self.assertFalse(first["trace"]["guarantees"]["llm_called"])
        self.assertEqual(first["trace"]["cli"]["version"], "v0.4.41")
        for argv in first["trace"]["cli"]["commands"]:
            self.assertTrue(adapter._allowlisted(argv))


if __name__ == "__main__":
    unittest.main()
