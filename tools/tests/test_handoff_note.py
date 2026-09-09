#!/usr/bin/env python3
"""T06 focused tests — non-trigger /note CONTEXT_HANDOFF publisher + discovery.

All CLI interaction is mocked/captured deployed-CLI JSON; the only live
Multica write allowed by YZT-59 is the controlled proof on YZT-59, which is
executed manually and never inside this suite. No network, no model, no
Canonical write, no Memory rebuild, no issue lifecycle write.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import chandoff  # noqa: E402
import chandoff_note as note  # noqa: E402
from schema_mini import Schema, load_schema_file  # noqa: E402

FIXTURES = TOOLS / "fixtures" / "note"

ISSUE_ID = "01a08610-ab5f-7e73-ba6b-145db345a36c"
ISSUE_KEY = "YZT-59"
TASK_REF = "multica://issue/YZT-59"
ROLE = "software-engineer"
PREPARED_BY = "8bc546ab-ffd8-4aa6-ad30-58583346c065"
CLOCK = lambda: "2026-09-09T15:00:00Z"  # noqa: E731

ADD_RESPONSE = {
    "attachments": [],
    "author_id": PREPARED_BY,
    "author_type": "agent",
    "content": "/note",
    "created_at": "2026-09-09T15:00:01Z",
    "id": "01a08620-0000-0000-0000-00000000000a",
    "issue_id": ISSUE_ID,
    "parent_id": None,
    "revision": 1,
    "type": "comment",
}

OTHER_TASK_REF = "multica://issue/YZT-99"


def sha(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def valid_package(**overrides) -> dict:
    package = {
        "schema_version": "1.1",
        "kind": "context_package",
        "request": {"task_id": TASK_REF, "role": ROLE,
                    "project_id": "web-imagegen"},
        "scope": {"type": "project", "project_id": "web-imagegen",
                  "projects": [], "task_id": None},
        "project_phase": "active_development",
        "anchor_digest": {"mission": "图像生成 for Web-ImageGen",
                          "target_users": [], "relevant_scope": None,
                          "relevant_constraints": [], "non_goals": [],
                          "projects": []},
        "team_state_slice": [],
        "project_state_slice": [],
        "rules": [{
            "ref": "repo://multica-memory/team-context/rules/RULE-TEAM-000001.yaml",
            "statement": "Owner boundaries hold",
            "modality": "MUST",
            "status": "active",
            "verification": "verified",
            "authority_refs": ["multica://issue/YZT-40"],
        }],
        "current_facts": [],
        "cases": [],
        "open_conflicts": [],
        "blocked_by": [],
        "task_evidence": [],
        "source_refs": [],
        "assembly_trace": {
            "scope_resolved": True,
            "case_search_performed": False,
            "case_search_trigger": None,
            "role_policy_applied": ROLE,
            "filters_applied": ["finalize_hard_invariant_recheck"],
            "excluded_counts": {},
            "generated_at": "2026-09-09T12:00:00Z",
        },
        "generated_at": "2026-09-09T12:00:00Z",
    }
    package.update(overrides)
    return package


def valid_envelope(**overrides) -> dict:
    envelope = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_result",
        "status": "READY",
        "package_id": "CTX-software-engineer-0123456789abcdef",
        "task_ref": TASK_REF,
        "role": ROLE,
        "built_from": {
            "task_fingerprint": sha("fp"),
            "memory_revision": sha("mem"),
            "registry_revision": sha("reg"),
            "role_profile_revision": sha("role"),
        },
        "package": valid_package(),
        "escalation": {"required": False},
        "generated_at": "2026-09-09T12:00:00Z",
    }
    envelope.update(overrides)
    return envelope


def partial_envelope() -> dict:
    env = valid_envelope()
    env["status"] = "PARTIAL"
    env["package"]["open_conflicts"] = [{
        "id": "CONFLICT-1", "summary": "repo-map drifts from main",
        "refs": ["multica://issue/YZT-39"],
    }]
    env["package"]["blocked_by"] = ["open_conflict:CONFLICT-1"]
    return env


def blocked_envelope() -> dict:
    env = valid_envelope()
    env["status"] = "BLOCKED"
    env["package"]["blocked_by"] = ["CONTEXT_BUDGET_OVERFLOW"]
    env["escalation"] = {"required": True, "reason": "finalize_blocked"}
    return env


def comment(cid: str, content: str, *, created_at: str,
            parent_id: str | None = None, author_id: str = PREPARED_BY,
            resolved_at: str | None = None,
            issue_id: str = ISSUE_ID) -> dict:
    return {
        "attachments": [],
        "author_id": author_id,
        "author_type": "agent",
        "content": content,
        "created_at": created_at,
        "id": cid,
        "issue_id": issue_id,
        "parent_id": parent_id,
        "reactions": [],
        "resolved_at": resolved_at,
        "resolved_by_id": None,
        "resolved_by_type": None,
        "revision": 1,
        "source_task_id": None,
        "type": "comment",
        "updated_at": created_at,
    }


def record_body(envelope=None, *, prepared_by=PREPARED_BY,
                prepared_at="2026-09-09T15:00:00Z",
                allow_partial=False, mutate=None) -> str:
    env = copy.deepcopy(envelope if envelope is not None else valid_envelope())
    if mutate is not None:
        mutate(env)
    body, _record = note.render_note_record(
        env, prepared_by=prepared_by, prepared_at=prepared_at,
        clock=CLOCK, allow_partial=allow_partial)
    return body


def fake_cli(comments=None, version="v0.4.41", add_response=None,
             exit_code=0, stderr="", raw_list=None, raw_add=None,
             written=None, refuse_on_write=False):
    comments = comments or []
    add_response = add_response if add_response is not None else ADD_RESPONSE
    calls = {"argv": [], "written": written if written is not None else {}}

    def runner(argv):
        calls["argv"].append(list(argv))
        if refuse_on_write and argv[1:3] == ["comment", "add"]:
            raise AssertionError(f"unexpected write: {argv}")
        if exit_code:
            return exit_code, "", stderr
        tail = argv[1:]
        if tail[:1] == ["version"]:
            return 0, json.dumps({"version": version}), ""
        if tail[:3] == ["issue", "comment", "list"]:
            if raw_list is not None:
                return 0, raw_list, stderr
            return 0, json.dumps(comments), stderr
        if tail[:3] == ["issue", "comment", "add"]:
            path = argv[argv.index("--content-file") + 1]
            data = Path(path).read_bytes()
            calls["written"]["body"] = data.decode("utf-8")
            calls["written"]["raw"] = data
            if raw_add is not None:
                return 0, raw_add, ""
            return 0, json.dumps(add_response), ""
        return 2, "", "unexpected command"

    cli = note.NoteCli(runner=runner)
    cli.calls = calls
    return cli


def publish(envelope=None, cli=None, **kwargs):
    kwargs.setdefault("prepared_by", PREPARED_BY)
    kwargs.setdefault("clock", CLOCK)
    return note.publish_handoff(
        copy.deepcopy(envelope if envelope is not None else valid_envelope()),
        issue_id=ISSUE_ID, cli=cli, **kwargs)


def resolve(cli, *, task_ref=TASK_REF, role=ROLE, issue_id=ISSUE_ID):
    return note.resolve_latest_handoff(
        issue_id, task_ref=task_ref, target_role=role, cli=cli)


class CompatibilityTests(unittest.TestCase):
    def test_frozen_t00_can_host_publish_without_amendment(self):
        report = note.frozen_contract_supports_publish()
        self.assertTrue(report["ok"], report)

    def test_done_criteria_match_issue_yaml(self):
        self.assertEqual(note.done_criteria(), {
            "deployed_note_contract_checked": True,
            "publish_does_not_create_run": True,
            "ready_round_trip_readable": True,
            "partial_requires_explicit_authorization": True,
            "blocked_publish_attempts": 0,
            "latest_role_package_resolvable": True,
            "newer_invalid_candidate_fails_closed": True,
            "duplicate_publish_comments": 0,
            "frozen_schema_changes": 0,
            "canonical_writes": 0,
            "llm_calls": 0,
            "rebuilds": 0,
            "downstream_run_triggers": 0,
        })

    def test_frozen_schemas_unchanged_on_disk(self):
        repo = TOOLS.parent
        expected = {
            "schemas/context-handoff/prepare-handoff-result.schema.json",
            "schemas/context-handoff/handoff-common.schema.json",
            "schemas/context-package.schema.json",
        }
        for rel in expected:
            text = (repo / rel).read_text(encoding="utf-8")
            self.assertNotIn("CONTEXT_HANDOFF", text)
            self.assertNotIn("/note", text)


class RenderTests(unittest.TestCase):
    def test_ready_render_contract(self):
        body = record_body()
        lines = body.split("\n")
        self.assertEqual(lines[0], "/note")
        self.assertEqual(lines[2], "CONTEXT_HANDOFF_RECORD v1")
        self.assertTrue(lines[3].startswith("CONTEXT_HANDOFF_META "))
        self.assertEqual(lines[4], "```json")
        self.assertEqual(lines[-2], "```")
        self.assertEqual(lines[-1], "")

    def test_render_is_deterministic(self):
        first = record_body()
        second = record_body()
        self.assertEqual(first, second)

    def test_meta_duplicates_exactly_the_envelope(self):
        body = record_body()
        meta = json.loads(body.split("\n")[3][len(note.META_PREFIX):])
        env = valid_envelope()
        self.assertEqual(meta["package_id"], env["package_id"])
        self.assertEqual(meta["task_ref"], env["task_ref"])
        self.assertEqual(meta["target_role"], env["role"])
        self.assertEqual(meta["status"], env["status"])
        self.assertEqual(meta["built_from"], env["built_from"])
        self.assertEqual(meta["prepared_by"], PREPARED_BY)
        self.assertEqual(meta["prepared_at"], "2026-09-09T15:00:00Z")

    def test_payload_round_trips_the_envelope(self):
        body = record_body()
        payload = json.loads("\n".join(body.split("\n")[5:-2]))
        self.assertEqual(payload["record_version"], 1)
        self.assertEqual(
            chandoff.canonical_json(payload["prepare_handoff_result"]),
            chandoff.canonical_json(valid_envelope()))

    def test_body_carries_no_mention_link(self):
        body = record_body()
        self.assertNotIn("mention://", body)
        self.assertNotIn("@", body.split("\n")[0])

    def test_partial_refused_by_default(self):
        with self.assertRaises(note.PublishRefusedError) as ctx:
            record_body(partial_envelope())
        self.assertEqual(ctx.exception.details["reason"],
                         "partial_requires_explicit_authorization")

    def test_partial_renders_with_explicit_authorization(self):
        body = record_body(partial_envelope(), allow_partial=True)
        payload = json.loads("\n".join(body.split("\n")[5:-2]))
        self.assertEqual(payload["prepare_handoff_result"]["status"], "PARTIAL")
        self.assertTrue(payload["prepare_handoff_result"]["package"]["open_conflicts"])
        self.assertTrue(payload["prepare_handoff_result"]["package"]["blocked_by"])

    def test_blocked_never_renders(self):
        with self.assertRaises(note.PublishRefusedError) as ctx:
            record_body(blocked_envelope())
        self.assertEqual(ctx.exception.details["reason"],
                         "blocked_not_publishable")

    def test_blank_prepared_by_refused(self):
        with self.assertRaises(note.AdapterError):
            record_body(prepared_by="  ")

    def test_body_with_unicode_is_stable(self):
        body = record_body()
        self.assertIn("图像生成", body)


class BodyScanTests(unittest.TestCase):
    def test_mention_link_anywhere_is_refused(self):
        def inject(env):
            env["package"]["open_conflicts"] = [{
                "id": "C", "summary": "x",
                "refs": ["mention://agent/24f04aba"]}]
            env["status"] = "PARTIAL"
            env["package"]["blocked_by"] = ["open_conflict:C"]
        with self.assertRaises(note.PublishRefusedError):
            record_body(mutate=inject, allow_partial=True)
        problems = note.scan_body("note\nsee [x](mention://agent/y)")
        self.assertIn("note_first_line_missing", problems)
        self.assertIn("mention_link_present", problems)

    def test_stray_slash_command_line_is_refused(self):
        problems = note.scan_body(
            "/note\n\nCONTEXT_HANDOFF_RECORD v1\n/assign someone\n")
        self.assertTrue(any(p.startswith("stray_slash_command") for p in problems))

    def test_scan_passes_on_rendered_body(self):
        self.assertEqual(note.scan_body(record_body()), [])
        self.assertEqual(note.scan_body(record_body(partial_envelope(),
                                                    allow_partial=True)), [])


class ValidationGateTests(unittest.TestCase):
    def test_schema_invalid_envelope_refused_before_cli(self):
        env = valid_envelope()
        del env["task_ref"]
        cli = fake_cli(refuse_on_write=True)
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=cli)
        self.assertEqual(cli.commands, [])

    def test_package_schema_invalid_refused(self):
        env = valid_envelope()
        env["package"]["kind"] = "not_a_context_package"
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=fake_cli(refuse_on_write=True))

    def test_ready_with_visible_gaps_is_integrity_invalid(self):
        env = valid_envelope()
        env["package"]["open_conflicts"] = [{"id": "C", "summary": "s",
                                             "refs": ["multica://issue/YZT-1"]}]
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=fake_cli(refuse_on_write=True))

    def test_partial_without_gaps_is_integrity_invalid(self):
        env = valid_envelope()
        env["status"] = "PARTIAL"
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=fake_cli(refuse_on_write=True))

    def test_package_binding_mismatch_is_integrity_invalid(self):
        env = valid_envelope()
        env["package"]["request"]["task_id"] = OTHER_TASK_REF
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=fake_cli(refuse_on_write=True))
        env2 = valid_envelope()
        env2["package"]["request"]["role"] = "qa"
        with self.assertRaises(note.SchemaViolationError):
            publish(env2, cli=fake_cli(refuse_on_write=True))

    def test_pseudo_scheme_ref_is_integrity_invalid(self):
        env = valid_envelope()
        env["package"]["rules"][0]["ref"] = "rule:RULE-TEAM-000001"
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=fake_cli(refuse_on_write=True))

    def test_built_from_shape_is_enforced(self):
        env = valid_envelope()
        del env["built_from"]["memory_revision"]
        with self.assertRaises(note.SchemaViolationError):
            publish(env, cli=fake_cli(refuse_on_write=True))


class PublishFlowTests(unittest.TestCase):
    def test_ready_publish_uses_exactly_one_content_file_write(self):
        cli = fake_cli()
        result = publish(cli=cli)
        self.assertTrue(result["published"])
        writes = [a for a in cli.commands if a[0:3] == ["issue", "comment", "add"]]
        self.assertEqual(len(writes), 1)
        argv = writes[0]
        self.assertEqual(argv[3], ISSUE_ID)
        self.assertIn("--content-file", argv)
        self.assertNotIn("--content", argv)
        self.assertNotIn("--content-stdin", argv)
        self.assertNotIn("--parent", argv)
        self.assertEqual(result["comment"]["id"], ADD_RESPONSE["id"])

    def test_publish_body_written_as_utf8_without_bom(self):
        cli = fake_cli()
        publish(cli=cli)
        raw = cli.calls["written"]["raw"]
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        body = cli.calls["written"]["body"]
        self.assertEqual(body, record_body())
        self.assertIn("图像生成", body)

    def test_temp_file_deleted_after_success(self):
        cli = fake_cli()
        result = publish(cli=cli)
        leftovers = [p.name for p in Path.cwd().glob(".t06-note-*.md")]
        self.assertEqual(leftovers, [])
        self.assertTrue(result["trace"]["temp_file_deleted"])

    def test_temp_file_deleted_after_cli_failure(self):
        cli = fake_cli(exit_code=1, stderr="error: permission denied")
        with self.assertRaises(note.CliCommandError):
            publish(cli=cli)
        leftovers = [p.name for p in Path.cwd().glob(".t06-note-*.md")]
        self.assertEqual(leftovers, [])

    def test_parent_routing_only_when_caller_supplies(self):
        cli = fake_cli()
        publish(cli=cli, parent_comment_id="01a08620-0000-0000-0000-00000000001a")
        argv = [a for a in cli.commands if a[0:3] == ["issue", "comment", "add"]][0]
        self.assertEqual(
            argv[argv.index("--parent") + 1],
            "01a08620-0000-0000-0000-00000000001a")
        cli2 = fake_cli()
        publish(cli=cli2)
        argv2 = [a for a in cli2.commands if a[0:3] == ["issue", "comment", "add"]][0]
        self.assertNotIn("--parent", argv2)

    def test_parent_id_never_invented_or_stale_reused(self):
        with self.assertRaises(note.AdapterError):
            publish(cli=fake_cli(), parent_comment_id="bad id with spaces")
        with self.assertRaises(note.AdapterError):
            publish(cli=fake_cli(), parent_comment_id="--content-file")

    def test_response_contract_drift_is_bounded(self):
        cli = fake_cli(raw_add="{not json")
        with self.assertRaises(note.CliJsonError):
            publish(cli=cli)
        cli2 = fake_cli(raw_add=json.dumps({"nope": True}))
        with self.assertRaises(note.CliContractError):
            publish(cli=cli2)

    def test_trace_guarantees_hold(self):
        cli = fake_cli(comments=[])
        result = publish(cli=cli)
        guarantees = result["trace"]["guarantees"]
        self.assertEqual(guarantees["llm_called"], False)
        self.assertEqual(guarantees["canonical_writes"], 0)
        self.assertEqual(guarantees["memory_rebuilds"], 0)
        self.assertEqual(guarantees["issue_lifecycle_writes"], 0)
        self.assertEqual(guarantees["assignments"], 0)
        self.assertEqual(guarantees["mentions"], 0)
        self.assertEqual(guarantees["downstream_run_triggers"], 0)
        self.assertEqual(guarantees["frozen_schema_changes"], 0)
        self.assertEqual(result["trace"]["write_commands"], 1)
        self.assertTrue(result["trace"]["temp_file_deleted"])


class StatusGateTests(unittest.TestCase):
    def test_blocked_refused_before_any_subprocess(self):
        cli = fake_cli(refuse_on_write=True)
        with self.assertRaises(note.PublishRefusedError):
            publish(blocked_envelope(), cli=cli)
        self.assertEqual(cli.commands, [])

    def test_partial_refused_before_any_subprocess(self):
        cli = fake_cli(refuse_on_write=True)
        with self.assertRaises(note.PublishRefusedError):
            publish(partial_envelope(), cli=cli)
        self.assertEqual(cli.commands, [])

    def test_partial_published_with_explicit_authorization(self):
        cli = fake_cli()
        result = publish(partial_envelope(), cli=cli, allow_partial=True)
        self.assertTrue(result["published"])
        body = cli.calls["written"]["body"]
        payload = json.loads("\n".join(body.split("\n")[5:-2]))
        self.assertEqual(payload["prepare_handoff_result"]["status"], "PARTIAL")
        self.assertTrue(payload["prepare_handoff_result"]["package"]["open_conflicts"])
        self.assertTrue(payload["prepare_handoff_result"]["package"]["blocked_by"])


class IdempotencyTests(unittest.TestCase):
    def test_identical_record_returns_existing_comment(self):
        body = record_body()
        existing = comment("01a08620-0000-0000-0000-0000000000aa", body,
                           created_at="2026-09-09T15:00:00Z")
        cli = fake_cli(comments=[existing])
        result = publish(cli=cli)
        self.assertFalse(result["published"])
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["existing_comment"]["id"], existing["id"])
        writes = [a for a in cli.commands if a[1:3] == ["comment", "add"]]
        self.assertEqual(writes, [])

    def test_no_duplicate_comment_created_on_retry(self):
        body = record_body()
        existing = comment("01a08620-0000-0000-0000-0000000000aa", body,
                           created_at="2026-09-09T15:00:00Z")
        first = fake_cli(comments=[])
        publish(cli=first)
        second = fake_cli(comments=[existing])
        result = publish(cli=second)
        self.assertFalse(result["published"])
        self.assertEqual(result["existing_comment"]["id"], existing["id"])

    def test_conflicting_reuse_of_package_id_fails_closed(self):
        body = record_body()
        existing = comment("01a08620-0000-0000-0000-0000000000aa", body,
                           created_at="2026-09-09T15:00:00Z")
        conflicting = valid_envelope()
        conflicting["task_ref"] = OTHER_TASK_REF
        conflicting["package"]["request"]["task_id"] = OTHER_TASK_REF
        conflicting["package_id"] = valid_envelope()["package_id"]
        cli = fake_cli(comments=[existing])
        with self.assertRaises(note.PackageIdConflictError):
            publish(conflicting, cli=cli)

    def test_same_package_id_different_content_fails_closed(self):
        body = record_body()
        existing = comment("01a08620-0000-0000-0000-0000000000aa", body,
                           created_at="2026-09-09T15:00:00Z")
        mutated = valid_envelope()
        mutated["package"]["rules"][0]["statement"] = "changed statement"
        cli = fake_cli(comments=[existing])
        with self.assertRaises(note.PackageIdConflictError):
            publish(mutated, cli=cli)

    def test_invalid_record_reusing_package_id_fails_closed(self):
        body = record_body()
        broken = body.replace("```json", "```json\nCORRUPT", 1)
        existing = comment("01a08620-0000-0000-0000-0000000000aa", broken,
                           created_at="2026-09-09T15:00:00Z")
        cli = fake_cli(comments=[existing])
        with self.assertRaises(note.PackageIdConflictError):
            publish(cli=cli)

    def test_newer_other_package_does_not_block_publish(self):
        older_body = record_body()
        older = comment("01a08620-0000-0000-0000-0000000000aa", older_body,
                        created_at="2026-09-09T15:00:00Z")
        refreshed = valid_envelope()
        refreshed["package_id"] = "CTX-software-engineer-ffffffffffffffff"
        cli = fake_cli(comments=[older])
        result = publish(refreshed, cli=cli)
        self.assertTrue(result["published"])


class ParseTests(unittest.TestCase):
    def test_valid_record_parses(self):
        parsed = note._parse_record(record_body())
        self.assertTrue(parsed["ok_record"])
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(parsed["binding"],
                         {"task_ref": TASK_REF, "target_role": ROLE})

    def test_ordinary_comment_is_not_a_record(self):
        parsed = note._parse_record("just a normal progress comment")
        self.assertTrue(parsed["not_a_record"])

    def test_marker_without_note_first_line_is_malformed(self):
        body = record_body().split("\n", 1)[1]
        parsed = note._parse_record(body)
        self.assertFalse(parsed["ok_record"])
        self.assertIn("note_first_line_missing", parsed["errors"])

    def test_ambiguous_marker_is_malformed(self):
        body = record_body() + record_body()
        parsed = note._parse_record(body)
        self.assertFalse(parsed["ok_record"])
        self.assertIn("ambiguous_record_marker", parsed["errors"])

    def test_meta_malformed_binding_falls_back_to_valid_envelope(self):
        body = record_body().replace(
            "CONTEXT_HANDOFF_META {", "CONTEXT_HANDOFF_META not-json {", 1)
        parsed = note._parse_record(body)
        self.assertFalse(parsed["ok_record"])
        self.assertIn("meta_malformed", parsed["errors"])
        self.assertEqual(parsed["binding"],
                         {"task_ref": TASK_REF, "target_role": ROLE})

    def test_unbindable_record_when_both_meta_and_envelope_unparseable(self):
        body = record_body().replace(
            "CONTEXT_HANDOFF_META {", "CONTEXT_HANDOFF_META not-json {", 1)
        broken = body.replace('"task_ref": "multica://issue/YZT-59"',
                              '"task_ref": 42')
        parsed = note._parse_record(broken)
        self.assertFalse(parsed["ok_record"])
        self.assertIsNone(parsed["binding"])

    def test_payload_missing_or_malformed(self):
        no_fence = "\n".join(record_body().split("\n")[:4])
        parsed = note._parse_record(no_fence)
        self.assertFalse(parsed["ok_record"])
        self.assertIn("payload_missing", parsed["errors"])
        truncated = record_body().split("```json")[0] + "```json\n{bad json\n```"
        parsed2 = note._parse_record(truncated)
        self.assertFalse(parsed2["ok_record"])
        self.assertTrue(any(e.startswith("payload_malformed")
                            for e in parsed2["errors"]))

    def test_trailing_content_after_record_is_malformed(self):
        parsed = note._parse_record(record_body() + "trailing chatter\n")
        self.assertFalse(parsed["ok_record"])
        self.assertIn("trailing_content_after_record", parsed["errors"])

    def test_record_version_mismatch(self):
        body = record_body().replace('"record_version": 1',
                                     '"record_version": 2')
        parsed = note._parse_record(body)
        self.assertFalse(parsed["ok_record"])
        self.assertIn("record_version_mismatch", parsed["errors"])

    def test_metadata_duplication_mismatch(self):
        body = record_body().replace('"prepared_at": "2026-09-09T15:00:00Z"',
                                     '"prepared_at": "2026-09-09T16:00:00Z"', 1)
        parsed = note._parse_record(body)
        self.assertFalse(parsed["ok_record"])
        self.assertIn("metadata_duplication_mismatch", parsed["errors"])

    def test_meta_envelope_mismatch(self):
        env = valid_envelope()
        body_lines = record_body(env).split("\n")
        meta = json.loads(body_lines[3][len(note.META_PREFIX):])
        meta["package_id"] = "CTX-other-0000000000000000"
        body_lines[3] = note.META_PREFIX + json.dumps(
            meta, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        parsed = note._parse_record("\n".join(body_lines))
        self.assertFalse(parsed["ok_record"])
        self.assertTrue(any(e.startswith("metadata_mismatch")
                            for e in parsed["errors"]))

    def test_unparseable_envelope_binding_falls_back_to_meta(self):
        body = record_body().replace('"status": "READY"', '"status": 42', 1)
        parsed = note._parse_record(body)
        self.assertFalse(parsed["ok_record"])
        self.assertEqual(parsed["binding"],
                         {"task_ref": TASK_REF, "target_role": ROLE})


class DiscoveryTests(unittest.TestCase):
    def test_top_level_and_reply_records_both_found(self):
        top = comment("c-top", record_body(), created_at="2026-09-09T15:00:00Z")
        reply = comment("c-reply", record_body(), created_at="2026-09-09T16:00:00Z",
                        parent_id="c-root")
        root = comment("c-root", "ordinary root", created_at="2026-09-09T14:00:00Z")
        cli = fake_cli(comments=[root, reply, top])
        result = resolve(cli)
        self.assertTrue(result["found"])
        self.assertEqual(result["selection"]["bound_candidates"], 2)

    def test_resolved_thread_record_still_discoverable(self):
        root = comment("c-root", "question", created_at="2026-09-09T14:00:00Z",
                       resolved_at="2026-09-09T15:30:00Z")
        reply = comment("c-reply", record_body(), created_at="2026-09-09T15:00:00Z",
                        parent_id="c-root", resolved_at="2026-09-09T15:30:00Z")
        cli = fake_cli(comments=[root, reply])
        result = resolve(cli)
        self.assertTrue(result["found"])
        self.assertEqual(result["comment"]["id"], "c-reply")
        self.assertEqual(result["comment"]["parent_id"], "c-root")
        self.assertEqual(result["comment"]["resolved_at"], "2026-09-09T15:30:00Z")

    def test_ordinary_and_other_task_role_records_ignored(self):
        other_body = record_body(mutate=lambda e: (
            e.update(task_ref=OTHER_TASK_REF),
            e["package"]["request"].update(task_id=OTHER_TASK_REF)))
        other = comment("c-other", other_body, created_at="2026-09-09T16:00:00Z")
        other_role_body = record_body(mutate=lambda e: (
            e.update(role="qa"), e["package"]["request"].update(role="qa")))
        other_role = comment("c-orole", other_role_body,
                             created_at="2026-09-09T16:30:00Z")
        ordinary = comment("c-ordinary", "progress: all good",
                           created_at="2026-09-09T17:00:00Z")
        cli = fake_cli(comments=[other, other_role, ordinary])
        result = resolve(cli)
        self.assertFalse(result["found"])
        self.assertEqual(result["reason"], "no_handoff_record_for_task_role")
        self.assertEqual(result["records_seen"], 2)

    def test_newest_created_at_wins_with_id_tiebreak(self):
        older = comment("c-older", record_body(),
                        created_at="2026-09-09T15:00:00Z")
        newer = comment("c-newer", record_body(),
                        created_at="2026-09-09T16:00:00Z")
        tie_a = comment("c-tie-a", record_body(), created_at="2026-09-09T17:00:00Z")
        tie_b = comment("c-tie-b", record_body(), created_at="2026-09-09T17:00:00Z")
        cli = fake_cli(comments=[newer, tie_a, older, tie_b])
        result = resolve(cli)
        self.assertTrue(result["found"])
        self.assertEqual(result["comment"]["id"], "c-tie-b")
        self.assertEqual(result["selection"]["order"][0], [
            "2026-09-09T17:00:00Z", "c-tie-b", True])

    def test_newer_invalid_candidate_fails_closed(self):
        valid_old = comment("c-valid", record_body(),
                            created_at="2026-09-09T15:00:00Z")
        invalid_new = comment("c-invalid", record_body() + "extra garbage",
                              created_at="2026-09-09T16:00:00Z")
        cli = fake_cli(comments=[valid_old, invalid_new])
        with self.assertRaises(note.LatestHandoffInvalidError) as ctx:
            resolve(cli)
        self.assertEqual(ctx.exception.details["comment_id"], "c-invalid")
        self.assertIn("trailing_content_after_record", ctx.exception.details["errors"])

    def test_unbindable_newer_candidate_fails_closed(self):
        valid_old = comment("c-valid", record_body(),
                            created_at="2026-09-09T15:00:00Z")
        garbage = comment("c-garbage",
                          "/note\n\nCONTEXT_HANDOFF_RECORD v1\n",
                          created_at="2026-09-09T16:00:00Z")
        cli = fake_cli(comments=[valid_old, garbage])
        with self.assertRaises(note.LatestHandoffInvalidError):
            resolve(cli)

    def test_older_invalid_candidate_does_not_block_valid_newest(self):
        invalid_old = comment("c-invalid", "CONTEXT_HANDOFF_RECORD v1 only",
                              created_at="2026-09-09T14:00:00Z")
        valid_new = comment("c-valid", record_body(),
                            created_at="2026-09-09T15:00:00Z")
        cli = fake_cli(comments=[invalid_old, valid_new])
        result = resolve(cli)
        self.assertTrue(result["found"])
        self.assertEqual(result["comment"]["id"], "c-valid")

    def test_same_package_id_conflicting_content_fails_closed(self):
        first = comment("c-first", record_body(),
                        created_at="2026-09-09T15:00:00Z")
        mutated = valid_envelope()
        mutated["package"]["rules"][0]["statement"] = "changed"
        second = comment("c-second", record_body(mutated),
                         created_at="2026-09-09T16:00:00Z")
        cli = fake_cli(comments=[first, second])
        with self.assertRaises(note.PackageIdConflictError):
            resolve(cli)

    def test_identical_duplicate_records_select_newest(self):
        body = record_body()
        first = comment("c-first", body, created_at="2026-09-09T15:00:00Z")
        second = comment("c-second", body, created_at="2026-09-09T16:00:00Z")
        cli = fake_cli(comments=[first, second])
        result = resolve(cli)
        self.assertTrue(result["found"])
        self.assertEqual(result["comment"]["id"], "c-second")

    def test_resolution_returns_t07_ready_form(self):
        body = record_body()
        rec = comment("c-rec", body, created_at="2026-09-09T15:00:00Z")
        cli = fake_cli(comments=[rec])
        result = resolve(cli)
        env = result["envelope"]
        schema = load_schema_file(note.RESULT_SCHEMA)
        self.assertEqual(Schema(schema, schema).validate(env, path="$"), [])
        self.assertEqual(env["task_ref"], TASK_REF)
        self.assertEqual(env["role"], ROLE)
        self.assertEqual(result["comment"]["id"], "c-rec")
        self.assertEqual(result["comment"]["created_at"], "2026-09-09T15:00:00Z")
        self.assertEqual(
            chandoff.canonical_json(env),
            chandoff.canonical_json(valid_envelope()))


class CliContractTests(unittest.TestCase):
    def test_malformed_list_json_is_bounded(self):
        cli = fake_cli(raw_list="{not json")
        with self.assertRaises(note.CliJsonError):
            resolve(cli)

    def test_non_array_list_is_contract_drift(self):
        cli = fake_cli(raw_list=json.dumps({"comments": []}))
        with self.assertRaises(note.CliContractError):
            resolve(cli)

    def test_missing_comment_contract_fields_is_drift(self):
        broken = {k: v for k, v in comment("c1", "x",
                                           created_at="2026-09-09T15:00:00Z").items()
                  if k != "created_at"}
        cli = fake_cli(comments=[broken])
        with self.assertRaises(note.CliContractError) as ctx:
            resolve(cli)
        self.assertEqual(ctx.exception.details["missing"], ["created_at"])

    def test_blank_comment_id_is_drift(self):
        cli = fake_cli(comments=[comment("  ", "x",
                                         created_at="2026-09-09T15:00:00Z")])
        with self.assertRaises(note.CliContractError):
            resolve(cli)

    def test_permission_failure_is_bounded(self):
        cli = fake_cli(exit_code=1, stderr="error: permission denied")
        with self.assertRaises(note.CliCommandError) as ctx:
            resolve(cli)
        self.assertEqual(ctx.exception.code, "cli_command_failed")

    def test_cursor_notice_on_complete_read_is_contract_drift(self):
        cli = fake_cli(stderr="Next thread cursor: --before 2026-09-09T12:08:26Z "
                              "--before-id 01a08608-bbd2-7a9f-b057-3e0c9d1e1b62")
        with self.assertRaises(note.CliContractError) as ctx:
            resolve(cli)
        self.assertEqual(ctx.exception.code, "incompatible_cli_contract")

    def test_no_cursor_notice_on_clean_read(self):
        cli = fake_cli(comments=[])
        result = resolve(cli)
        self.assertFalse(result["found"])

    def test_missing_cli_is_bounded(self):
        def boom(argv):
            raise FileNotFoundError("multica not found")
        cli = note.NoteCli(runner=boom)
        with self.assertRaises(note.CliUnavailableError):
            resolve(cli)


class WriteAllowlistTests(unittest.TestCase):
    def test_cli_refuses_every_non_allowlisted_command(self):
        cli = note.NoteCli(runner=lambda argv: (0, "{}", ""))
        for argv in (
            ["issue", "update", ISSUE_ID, "--status", "done"],
            ["issue", "create", "--title", "x"],
            ["issue", "assign", ISSUE_ID],
            ["issue", "status", ISSUE_ID, "done"],
            ["issue", "rerun", ISSUE_ID],
            ["issue", "cancel-task", "r"],
            ["issue", "metadata", "set", ISSUE_ID],
            ["issue", "comment", "delete", "c"],
            ["issue", "comment", "resolve", "c"],
            ["run", "trigger", "x"],
            ["agent", "list"],
        ):
            with self.assertRaises(note.AdapterError):
                cli._run(argv, write=False)
            with self.assertRaises(note.AdapterError):
                cli._run(argv, write=True)
        self.assertEqual(cli.commands, [])

    def test_publish_requires_content_file_and_forbids_inline_stdin_attachment(self):
        for bad in (
            ["issue", "comment", "add", ISSUE_ID, "--content", "hi"],
            ["issue", "comment", "add", ISSUE_ID, "--content-stdin"],
            ["issue", "comment", "add", ISSUE_ID, "--attachment", "x"],
            ["issue", "comment", "add", ISSUE_ID, "--allow-external-file",
             "--content-file", "x"],
        ):
            with self.assertRaises(note.AdapterError):
                cli_write(bad)

    def test_issued_commands_recorded_and_allowlisted(self):
        cli = fake_cli()
        publish(cli=cli)
        for argv in cli.commands:
            self.assertTrue(
                note._allowlisted_read(argv)
                or argv[0:3] == list(note.WRITE_COMMAND))


def cli_write(argv):
    cli = note.NoteCli(runner=lambda a: (0, "{}", ""))
    cli._run(argv, write=True)


class RoundTripTests(unittest.TestCase):
    def test_publish_then_discover_round_trip_byte_for_byte(self):
        env = valid_envelope()
        first = fake_cli(comments=[])
        published = publish(env, cli=first)
        stored_body = first.calls["written"]["body"]
        stored = comment(ADD_RESPONSE["id"], stored_body,
                         created_at=ADD_RESPONSE["created_at"])
        second = fake_cli(comments=[stored])
        result = resolve(second)
        self.assertTrue(result["found"])
        self.assertEqual(
            chandoff.canonical_json(result["envelope"]),
            chandoff.canonical_json(env))
        self.assertEqual(result["comment"]["id"], published["comment"]["id"])
        self.assertEqual(result["record"], published["record"])

    def test_partial_round_trip_preserves_gaps(self):
        env = partial_envelope()
        first = fake_cli(comments=[])
        publish(env, cli=first, allow_partial=True)
        stored = comment("c1", first.calls["written"]["body"],
                         created_at="2026-09-09T15:00:00Z")
        result = resolve(fake_cli(comments=[stored]))
        self.assertEqual(result["envelope"]["status"], "PARTIAL")
        self.assertEqual(result["envelope"]["package"]["open_conflicts"],
                         env["package"]["open_conflicts"])
        self.assertEqual(result["envelope"]["package"]["blocked_by"],
                         env["package"]["blocked_by"])

    def test_real_pipeline_envelope_round_trips(self):
        env = real_pipeline_envelope()
        first = fake_cli(comments=[])
        publish(env, cli=first, prepared_at="2026-09-09T15:00:00Z")
        stored = comment("c1", first.calls["written"]["body"],
                         created_at="2026-09-09T15:00:00Z")
        result = resolve(fake_cli(comments=[stored]))
        self.assertEqual(
            chandoff.canonical_json(result["envelope"]),
            chandoff.canonical_json(env))


def real_pipeline_envelope() -> dict:
    """A genuine T01→T03 pipeline output (plan→compose→finalize), offline."""
    import chandoff_compose as compose
    import chandoff_finalize as finalize
    import chandoff_plan as plan
    request = {
        "schema_version": "1.1",
        "kind": "prepare_handoff_request",
        "task_ref": TASK_REF,
        "project": {"project_id": "web-imagegen"},
        "target": {"role": ROLE},
        "purpose": "implementation",
        "task_snapshot": {
            "title": "Implement demo adapter behavior",
            "description": "Demo description for the round-trip fixture.",
            "requirements": ["stable task reference construction"],
            "acceptance_criteria": ["request validates against frozen schema"],
            "relevant_decisions": [],
        },
        "caller": {"role": "engineering-lead"},
        "options": {"limit": 8},
    }
    plan_env = plan.prepare_handoff_plan(request, findings=[])
    proposed = compose.subset_result(plan_env["plan"])
    compose_env = compose.compose_semantic(plan_env, proposed)
    if compose_env["status"] != "ACCEPTED":
        raise AssertionError(compose_env["errors"])
    return finalize.finalize_handoff(plan_env, compose_env, request,
                                     clock=CLOCK)


class MemorySideEffectTests(unittest.TestCase):
    def test_memory_trees_untouched_by_publish_and_resolve(self):
        root = TOOLS.parent
        names = ("team-context", "project-context", "index")
        before = {n: tree_manifest(root / n) for n in names}
        cli = fake_cli(comments=[comment("c1", record_body(),
                                         created_at="2026-09-09T15:00:00Z")])
        publish(cli=cli)
        resolve(cli)
        after = {n: tree_manifest(root / n) for n in names}
        self.assertEqual(before, after)

    def test_note_module_imports_are_allowlisted(self):
        allowed = {"chandoff", "chandoff_finalize", "cutil", "schema_mini",
                   "__future__", "argparse", "hashlib", "json", "re",
                   "subprocess", "sys", "uuid", "pathlib", "typing"}
        source = (TOOLS / "chandoff_note.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                self.assertIn(name.split(".")[0], allowed)

    def test_note_source_has_no_memory_llm_or_network_paths(self):
        source = (TOOLS / "chandoff_note.py").read_text(encoding="utf-8")
        for token in ("urllib", "http.client", "socket", "ssl", "openai",
                      "anthropic", "memory_revision", "resolve_scope",
                      "scope_allows", "load_registry", "load_all_docs",
                      "anchor_digest", "cbuild", "cdata", "crole",
                      "cauthority", "index_builder", "memory_cli"):
            self.assertNotIn(token, source)

    def test_no_self_check_invocation_in_t06(self):
        token = "self" + "_check"
        source = (TOOLS / "chandoff_note.py").read_text(encoding="utf-8")
        self.assertNotIn(token, source)
        self.assertNotIn("chandoff" + "_selfcheck", source)
        tests = (TOOLS / "tests" / "test_handoff_note.py").read_text(
            encoding="utf-8")
        self.assertNotIn("chandoff" + "_selfcheck", tests)


def tree_manifest(root: Path) -> list:
    out = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out.append([path.relative_to(root).as_posix(),
                        hashlib.sha256(path.read_bytes()).hexdigest()])
    return out


class OfflineFixtureTests(unittest.TestCase):
    """Captured deployed-CLI payloads from the controlled YZT-59 proof."""

    @classmethod
    def setUpClass(cls):
        cls.record_list = FIXTURES / "comment_list_yzt59_with_record.json"
        cls.add_response = FIXTURES / "comment_add_readback_yzt59.json"
        cls.proof = FIXTURES / "proof_evidence.json"
        cls.have_fixtures = (cls.record_list.exists() and
                             cls.add_response.exists() and cls.proof.exists())

    @unittest.skipUnless(
        (Path(__file__).resolve().parent.parent / "fixtures" / "note" /
         "comment_list_yzt59_with_record.json").exists(),
        "proof fixtures not yet captured")
    def test_captured_list_resolves_the_published_record(self):
        data = json.loads(self.record_list.read_text(encoding="utf-8"))
        cli = fake_cli(raw_list=json.dumps(data))
        result = note.resolve_latest_handoff(
            ISSUE_ID, task_ref=TASK_REF, target_role="context-engineer",
            cli=cli)
        self.assertTrue(result["found"], result)
        self.assertEqual(result["envelope"]["task_ref"], TASK_REF)
        self.assertEqual(result["envelope"]["role"], "context-engineer")
        stored = next(c for c in data
                      if c["id"] == result["comment"]["id"])
        self.assertEqual(result["comment"]["created_at"], stored["created_at"])
        self.assertTrue(result["comment"]["parent_id"] is None
                        or isinstance(result["comment"]["parent_id"], str))

    @unittest.skipUnless(
        (Path(__file__).resolve().parent.parent / "fixtures" / "note" /
         "comment_add_readback_yzt59.json").exists(),
        "proof fixtures not yet captured")
    def test_captured_add_response_parses(self):
        """The raw add response was consumed by the single allowed live
        write; the captured read-back comment object is the deployed stored
        shape and parses under the same contract."""
        text = self.add_response.read_text(encoding="utf-8")
        parsed = note.parse_comment_add_response(text)
        self.assertTrue(parsed["id"])
        self.assertEqual(parsed["type"], "comment")


def _proof_evidence() -> dict | None:
    path = FIXTURES / "proof_evidence.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@unittest.skipUnless(_proof_evidence(), "controlled proof evidence not captured")
class LiveDeployedCliTests(unittest.TestCase):
    """Read-only live evidence against the deployed CLI (v0.4.41)."""

    def test_live_resolve_round_trips_the_controlled_proof_record(self):
        proof = _proof_evidence()
        result = note.resolve_latest_handoff(
            "YZT-59", task_ref=proof["task_ref"],
            target_role=proof["target_role"])
        self.assertTrue(result["found"])
        self.assertEqual(result["envelope"]["task_ref"], proof["task_ref"])
        self.assertEqual(result["comment"]["id"], proof["comment_id"])
        env_text = chandoff.canonical_json(result["envelope"])
        digest = hashlib.sha256(env_text.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(digest, proof["envelope_digest16"])
        self.assertFalse(result["comment"]["parent_id"])

    def test_live_records_command_is_read_only(self):
        cli = note.NoteCli()
        note.discover_handoff_records("YZT-59", cli=cli)
        for argv in cli.commands:
            self.assertTrue(note._allowlisted_read(argv))


if __name__ == "__main__":
    unittest.main()
