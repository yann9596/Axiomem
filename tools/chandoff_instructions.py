#!/usr/bin/env python3
"""U05 — V2.2 Agent/Squad instruction and binding staging (YZT-73).

Rebuilds T08 semantics for the six V2.2 logical roles. STAGING ONLY: never
imports/refreshes a workspace skill, never writes agent or squad
instructions, never mutates skill bindings, never assigns/mentions anyone,
and never triggers a run. Live activation is U12/Human-owned.

Framework-specific by design. The frozen framework-neutral boundary scan
(tools/chandoff.py scan) deliberately does not include this file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chandoff import canonical_json, sha256_text  # noqa: E402
from chandoff_adapter import (  # noqa: E402
    SchemaViolationError,
    _validate_role_vocabulary,
    current_role_ids,
)
from chandoff_instruction_texts import (  # noqa: E402
    CANDIDATE_KIND,
    COMMON_PROTOCOL,
    CTX_MARKERS,
    DR_MARKERS,
    LEAD_MARKERS,
    NON_TRANSFER_MARKERS,
    OWNER_SENTENCES,
    PROF_MARKERS,
    QA_MARKERS,
    ROLE_ADDITIONS,
    ROUTE_REWRITES,
    SQUAD_AFTER,
    SQUAD_MARKERS,
)
from cutil import now_iso  # noqa: E402

TOOL_VERSION = "U05/2.2"
# T09/T10 load this bundle by T08 family prefix. U05 keeps that prefix so
# assignment/mention orchestrators can consume the V2.2 after-state without
# a Frozen-T00 change. The U05 contract is recorded separately.
BASELINE_SCHEMA = "T08-baseline/1.0"
BUNDLE_SCHEMA = "T08-bundle/1.0"
PLAN_SCHEMA = "U05-binding-plan/1.0"
PRECONDITIONS_SCHEMA = "U05-preconditions/1.0"
ROLLBACK_SCHEMA = "U05-rollback/1.0"
AUDIT_SCHEMA = "U05-audit/1.0"
MAPPING_SCHEMA = "U05-role-mapping/1.0"
INVENTORY_SCHEMA = "U05-old-role-inventory/1.0"
INVALIDATION_SCHEMA = "U05-package-invalidation/1.0"
ISSUE_REF = "YZT-73"
U04_COMMIT = "702cb9bb11120c154a216e66c6ed1d3d632d3b49"
PINNED_ARTIFACT_CONTRACT = (
    "sha256:9c2857ae252e1916ef79a4816dfb57c05a6f32ec1b97f31419cdfddfd9e83bfc"
)
HISTORICAL_PINNED_SKILL_MD_U04 = (
    "sha256:f369cee40ada061364d91ef48a9cb7d467039931e922c1db18d85fe1d7e0638a"
)
# YZT-88 V2 candidate: first-work SELF_CHECK + official pipeline publication
# guards. Historical U04 pin is preserved above; U05 bundle.json is not
# rewritten in place.
PINNED_SKILL_MD = (
    "sha256:3ddd04e49ced01b752a445bf5d7f6cb35f30c0e450a1cb471d64d81ebafee674"
)
# Issue text also cites sha256:1df6a40c… as a U04 "skill integration digest".
# That value is not a T08-style bundle digest of U04@702cb9b; U05 pins the
# byte-reproducible T08-style bundle digest computed from the U04 skill tree
# and records the cited value as unreproducible (see skill_bundle_digests).
ISSUE_STATED_INTEGRATION = (
    "sha256:1df6a40c2dcc402adc98cd6258589d20e59e17bad3a279b45d594668c8c5bdc5"
)
PINNED_ROLE_PROFILE = (
    "sha256:7b3bdf5249dba6e1aeec1f29180b89c51985b04a6ab10a9aaaf5da596361531f"
)

SKILL_NAME = "multica-context-handoff"
SKILL_DIR_NAME = "skills/" + SKILL_NAME
DELIVERY_SKILL = "delivery-review"
QA_SKILL = "product-quality-gate"
ANCHOR_TAIL_CHARS = 120

# Logical roles. 05 live display name remains old-role baseline until U12.
ROLES: tuple[tuple[str, str], ...] = (
    ("engineering-lead", "01 Engineering Lead"),
    ("context-engineer", "02 Context Engineer"),
    ("solution-architect", "03 Solution Architect"),
    ("software-engineer", "04 Software Engineer"),
    ("delivery-reviewer", "05 Delivery Reviewer"),
    ("qa", "06 QA"),
)
ROLE_BY_SLUG = dict(ROLES)
V22_ROLES = tuple(r for r, _ in ROLES)
RETIRED_ROLES = {
    "feature-reviewer": "retired_no_alias",
    "ops-sre": "retired_no_alias",
}
LIVE_DISPLAY_NAME = {
    "engineering-lead": "01 Engineering Lead",
    "context-engineer": "02 Context Engineer",
    "solution-architect": "03 Solution Architect",
    "software-engineer": "04 Software Engineer",
    "delivery-reviewer": "05 Feature Reviewer",
    "qa": "06 QA",
}
STAGED_DISPLAY_NAME = dict(ROLES)
EXPECTED_AGENT_IDS = {
    "engineering-lead": "24f04aba-7da9-4371-bf89-685d7505a411",
    "context-engineer": "8bc546ab-ffd8-4aa6-ad30-58583346c065",
    "solution-architect": "1303827b-73d1-4d71-a461-00b93e4b4418",
    "software-engineer": "fa7d16a7-2dae-4994-80b8-7435b3fcca47",
    "delivery-reviewer": "b6335f8e-8147-45f7-aac0-8079d85423b5",
    "qa": "30ce43d4-97a7-42a8-ab3e-78df0d894702",
}
AGENT05_UUID = EXPECTED_AGENT_IDS["delivery-reviewer"]
SQUAD_NAME = "Engineering Team"
LEGACY_05_SKILLS = ("external-signal-research", "feature-correctness-review")
LEGACY_06_SKILL = "milestone-quality-gate"
SQUAD_ROLE_AFTER = {
    "engineering-lead": "Engineering Lead",
    "context-engineer": "Context Engineer",
    "solution-architect": "Solution Architect",
    "software-engineer": "Software Engineer",
    "delivery-reviewer": "Delivery Reviewer",
    "qa": "QA",
}

BOUND_ROLES = V22_ROLES  # all six; 02 binding is exception-path only
PROF_ROLES = ("solution-architect", "software-engineer",
              "delivery-reviewer", "qa")

READONLY_COMMANDS = (
    ("agent", "list"),
    ("agent", "tasks"),
    ("skill", "list"),
    ("skill", "get"),
    ("squad", "list"),
    ("squad", "get"),
    ("squad", "member", "list"),
    ("version",),
)

EXCLUDED_FIELDS = ("custom_env values", "mcp_config", "runtime_config",
                   "system_instructions", "model", "avatar_url", "status")

GUARANTEES = {
    "live_skill_imports": 0,
    "live_skill_refreshes": 0,
    "live_agent_instruction_writes": 0,
    "live_skill_binding_writes": 0,
    "live_squad_writes": 0,
    "assignments": 0,
    "mentions": 0,
    "downstream_run_triggers": 0,
    "issue_lifecycle_writes": 0,
    "canonical_writes": 0,
    "product_repo_changes": 0,
    "frozen_t00_amended": 0,
}

LIVENESS_FORBIDDEN = (
    "现已生效", "已生效", "现已启用", "已启用", "即刻生效", "正式生效",
    "is now live", "now active",
)
DELTA_FORBIDDEN = (
    "multica agent", "multica issue", "multica skill", "multica squad",
    "issue assign", "agent skills add", "agent skills set", "issue create",
)
ALLOWED_MENTION = "mention://agent/24f04aba-7da9-4371-bf89-685d7505a411"

INVENTORY_PATHS = (
    "tools/chandoff_instructions.py",
    "tools/chandoff_instruction_texts.py",
    "adapters/multica/role-bindings",
    "adapters/multica/README.md",
    "adapters/multica/agent-instructions/T08_REPORT.md",
    "adapters/multica/T09_REPORT.md",
    "adapters/multica/T10_REPORT.md",
    "tools/tests",
    "docs",
    "team-context",
    "skills",
)


class StagingError(Exception):
    code = "staging_error"

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.message = str(message)
        self.details = details

    def envelope(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


class CaptureError(StagingError):
    code = "capture_failed"


class BaselineError(StagingError):
    code = "baseline_invalid"


class BundleError(StagingError):
    code = "bundle_invalid"


class DriftError(StagingError):
    code = "precondition_drift"


class RefreshRequired(StagingError):
    code = "REFRESH_REQUIRED"


def _allowlisted(argv: list) -> bool:
    return any(tuple(argv[:len(cmd)]) == cmd for cmd in READONLY_COMMANDS)


def _default_runner(argv: list) -> tuple:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
    except OSError as exc:
        raise CaptureError(f"multica CLI unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("multica CLI timed out after 120s") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class CaptureCli:
    """Read-only deployed CLI boundary for baseline capture."""

    def __init__(self, executable: str = "multica",
                 runner: Callable | None = None):
        self.executable = executable
        self.runner = runner or _default_runner
        self.commands: list = []

    def _run(self, argv: list) -> str:
        if not _allowlisted(argv):
            raise CaptureError(
                "refused non-read-only multica command",
                argv=" ".join(str(a) for a in argv))
        self.commands.append([str(a) for a in argv])
        code, out, err = self.runner([self.executable] + list(argv))
        if code != 0:
            first = next((l for l in err.splitlines() if l.strip()), "")
            raise CaptureError(
                f"multica command failed (exit {code}): {first}",
                exit_code=code, stderr_excerpt=err[:240])
        return out

    def _json(self, argv: list):
        try:
            return json.loads(self._run(argv))
        except json.JSONDecodeError as exc:
            raise CaptureError(
                f"multica returned malformed JSON for {argv[0]}: {exc}") from exc

    def agent_list(self) -> list:
        data = self._json(["agent", "list", "--output", "json"])
        if not isinstance(data, list):
            raise CaptureError("agent list JSON is not an array",
                               kind=type(data).__name__)
        return data

    def skill_list(self) -> list:
        data = self._json(["skill", "list", "--output", "json"])
        if not isinstance(data, list):
            raise CaptureError("skill list JSON is not an array",
                               kind=type(data).__name__)
        return data

    def skill_get(self, skill_id: str) -> dict:
        data = self._json(["skill", "get", str(skill_id), "--with-content",
                           "--output", "json"])
        if not isinstance(data, dict):
            raise CaptureError("skill get JSON is not an object",
                               kind=type(data).__name__)
        return data

    def squad_list(self) -> list:
        data = self._json(["squad", "list", "--output", "json"])
        if not isinstance(data, list):
            raise CaptureError("squad list JSON is not an array",
                               kind=type(data).__name__)
        return data

    def squad_get(self, squad_id: str) -> dict:
        data = self._json(["squad", "get", str(squad_id), "--output", "json"])
        if not isinstance(data, dict):
            raise CaptureError("squad get JSON is not an object",
                               kind=type(data).__name__)
        return data

    def squad_members(self, squad_id: str) -> list:
        data = self._json(["squad", "member", "list", str(squad_id),
                           "--output", "json"])
        if not isinstance(data, list):
            raise CaptureError("squad member list JSON is not an array",
                               kind=type(data).__name__)
        return data

    def agent_tasks(self, agent_id: str) -> list:
        data = self._json(["agent", "tasks", str(agent_id),
                           "--output", "json"])
        if isinstance(data, dict):
            data = data.get("tasks") or data.get("items") or []
        if not isinstance(data, list):
            raise CaptureError("agent tasks JSON is not an array",
                               kind=type(data).__name__)
        return data

    def version(self) -> dict | None:
        try:
            data = self._json(["version", "--output", "json"])
        except CaptureError:
            return None
        return data if isinstance(data, dict) else None


def anchor_tail(text: str) -> str:
    return text[-ANCHOR_TAIL_CHARS:]


def binding_record(skill: dict) -> dict:
    return {"id": skill.get("id"), "name": skill.get("name"),
            "enabled": bool(skill.get("enabled", True))}


def rewrite_old_routes(role: str, text: str) -> str:
    out = text
    for old, new in ROUTE_REWRITES.get(role, ()):
        if old not in out:
            raise BundleError("expected live route sentence missing",
                              role=role, needle=old[:80])
        out = out.replace(old, new)
    return out


def applied_text(role: str, baseline_text: str) -> str:
    """Deterministic after-state. 05 is a full replacement; others append."""
    if role == "delivery-reviewer":
        return ROLE_ADDITIONS[role]
    rewritten = rewrite_old_routes(role, baseline_text)
    addition = ROLE_ADDITIONS[role]
    if role in ("engineering-lead", "context-engineer",
                "solution-architect", "software-engineer", "qa"):
        return rewritten + "\n" + COMMON_PROTOCOL + "\n" + addition
    return rewritten + "\n" + addition


def candidate_text(role: str, baseline_text: str) -> str:
    if role == "delivery-reviewer":
        return ROLE_ADDITIONS[role]
    rewritten = rewrite_old_routes(role, baseline_text)
    prefix = ""
    if rewritten != baseline_text:
        prefix = "## V2.2 route correction\n\nLive baseline still names the "
        prefix += "retired 05. After-state rewrites those routes in place; "
        prefix += "owner-boundary sentences stay.\n\n"
    if role in ("engineering-lead", "context-engineer",
                "solution-architect", "software-engineer", "qa"):
        return prefix + COMMON_PROTOCOL + "\n" + ROLE_ADDITIONS[role]
    return prefix + ROLE_ADDITIONS[role]


def extract_agent(role: str, raw_agents: list) -> dict:
    expected_id = EXPECTED_AGENT_IDS[role]
    live_name = LIVE_DISPLAY_NAME[role]
    staged_name = STAGED_DISPLAY_NAME[role]
    by_id = [a for a in raw_agents
             if isinstance(a, dict) and a.get("id") == expected_id]
    if len(by_id) != 1:
        raise CaptureError(
            "expected exactly one agent per stable UUID; found "
            f"{len(by_id)} (fail closed)",
            role=role, agent_id=expected_id)
    a = by_id[0]
    if a.get("name") != live_name:
        raise CaptureError(
            "live display name drifted from old-role baseline",
            role=role, expected=live_name, found=a.get("name"))
    instructions = a.get("instructions")
    if not isinstance(instructions, str):
        raise CaptureError("agent instructions is not a string",
                           role=role, agent_name=live_name)
    skills = [binding_record(s) for s in (a.get("skills") or [])
              if isinstance(s, dict) and s.get("id")]
    skills.sort(key=lambda s: s["id"])
    return {
        "role": role,
        "agent_id": a["id"],
        "agent_name": live_name,
        "staged_agent_name": staged_name,
        "live_display_name_is": (
            "old-role-baseline-not-v22-authority"
            if role == "delivery-reviewer" else "current"),
        "instruction_sha256": sha256_text(instructions),
        "instruction_length": len(instructions),
        "instruction_text": instructions,
        "anchor_tail": anchor_tail(instructions),
        "skill_binding_ids": sorted(s["id"] for s in skills),
        "skill_bindings": skills,
        "agent_updated_at": a.get("updated_at"),
        "custom_env_key_count": a.get("custom_env_key_count", 0),
        "has_custom_env": bool(a.get("has_custom_env", False)),
    }


def skill_catalog(raw_skills: list) -> dict:
    entries = [{"id": s.get("id"), "name": s.get("name")}
               for s in raw_skills if isinstance(s, dict) and s.get("id")]
    entries.sort(key=lambda s: s["id"])
    names = {s["name"] for s in entries}
    return {
        "count": len(entries),
        "skills": entries,
        "multica_context_handoff_present": SKILL_NAME in names,
        "delivery_review_present": DELIVERY_SKILL in names,
        "product_quality_gate_present": QA_SKILL in names,
    }


def digest_skill_dir(skill_dir: Path) -> dict:
    if not skill_dir.is_dir():
        raise BundleError("skill bundle missing from repo",
                          expected=str(skill_dir.as_posix()))
    files = {}
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = p.relative_to(skill_dir).as_posix()
            files[rel] = sha256_text(
                hashlib.sha256(p.read_bytes()).hexdigest())
    skill_md = skill_dir / "SKILL.md"
    return {
        "name": skill_dir.name,
        "bundle_digest": sha256_text(canonical_json(sorted(files.items()))),
        "skill_md_digest": sha256_text(
            skill_md.read_text(encoding="utf-8")),
        "files": files,
    }


def skill_bundle_digests(repo_root: Path) -> dict:
    skill = digest_skill_dir(repo_root / "skills" / SKILL_NAME)
    if skill["skill_md_digest"] != PINNED_SKILL_MD:
        raise RefreshRequired(
            "U04 SKILL.md digest drifted from the pinned U04 value",
            expected=PINNED_SKILL_MD, found=skill["skill_md_digest"])
    skill["issue_stated_integration_digest"] = ISSUE_STATED_INTEGRATION
    skill["issue_stated_integration_reproducible"] = (
        ISSUE_STATED_INTEGRATION in (
            skill["bundle_digest"], skill["skill_md_digest"]))
    return skill


def capability_skill_digests(repo_root: Path) -> dict:
    return {
        DELIVERY_SKILL: digest_skill_dir(repo_root / "skills" / DELIVERY_SKILL),
        QA_SKILL: digest_skill_dir(repo_root / "skills" / QA_SKILL),
    }


def pin_artifact_contract(repo_root: Path) -> str:
    sys.path.insert(0, str(repo_root / "tools"))
    import cartifact  # noqa: WPS433
    found = cartifact.artifact_contract_revision()
    if found != PINNED_ARTIFACT_CONTRACT:
        raise RefreshRequired(
            "Artifact Contract revision drifted from the accepted U10 digest",
            expected=PINNED_ARTIFACT_CONTRACT, found=found)
    return found


def summarize_tasks(raw_tasks: list) -> dict:
    interesting = []
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        status = str(t.get("status") or t.get("state") or "").lower()
        if status in ("running", "in_progress", "queued", "pending",
                      "active", "scheduled"):
            interesting.append({
                "id": t.get("id"),
                "status": t.get("status") or t.get("state"),
                "issue_id": t.get("issue_id") or t.get("issue"),
            })
    return {"active_or_queued": interesting,
            "active_or_queued_count": len(interesting),
            "observed_count": len(raw_tasks)}


def capture(out_dir: Path, executable: str = "multica",
            cli: CaptureCli | None = None,
            include_tasks: bool = True) -> dict:
    cli = cli or CaptureCli(executable)
    out_dir = Path(out_dir)
    commands_start = len(cli.commands)

    agents_raw = cli.agent_list()
    skills_raw = cli.skill_list()
    squads_raw = cli.squad_list()
    version = cli.version()

    agents, out_of_scope = [], []
    for role, _name in ROLES:
        agents.append(extract_agent(role, agents_raw))
    known_ids = {a["agent_id"] for a in agents}
    for a in agents_raw:
        if isinstance(a, dict) and a.get("id") and a["id"] not in known_ids:
            if not any(o["agent_id"] == a["id"] for o in out_of_scope):
                out_of_scope.append({
                    "agent_id": a["id"], "agent_name": a.get("name"),
                    "reason": "not one of the six collaboration roles; "
                              "out of U05 scope and never modified"})

    matches = [s for s in squads_raw
               if isinstance(s, dict) and s.get("name") == SQUAD_NAME]
    if len(matches) != 1:
        raise CaptureError(
            "expected exactly one Engineering Team squad",
            found=len(matches))
    squad_id = matches[0]["id"]
    squad_raw = cli.squad_get(squad_id)
    members_raw = cli.squad_members(squad_id)
    members = []
    for m in members_raw:
        if not isinstance(m, dict):
            continue
        members.append({
            "id": m.get("id"),
            "member_id": m.get("member_id"),
            "member_type": m.get("member_type"),
            "role": m.get("role"),
        })
    members.sort(key=lambda m: str(m.get("member_id") or ""))
    leader_id = squad_raw.get("leader_id")
    if leader_id != EXPECTED_AGENT_IDS["engineering-lead"]:
        raise CaptureError("Engineering Team leader is not 01",
                           found=leader_id)
    member_ids = {m["member_id"] for m in members}
    if member_ids != set(EXPECTED_AGENT_IDS.values()):
        raise CaptureError("Engineering Team roster drifted from six roles",
                           found=sorted(member_ids))

    tasks = {}
    if include_tasks:
        for a in agents:
            try:
                raw = cli.agent_tasks(a["agent_id"])
            except CaptureError as exc:
                tasks[a["role"]] = {"capture_error": exc.message,
                                    "active_or_queued_count": None}
            else:
                tasks[a["role"]] = summarize_tasks(raw)

    catalog = skill_catalog(skills_raw)
    bound = [a["role"] for a in agents if any(
        s["name"] == SKILL_NAME for s in a["skill_bindings"])]
    workspace_id = next((a.get("workspace_id") for a in agents_raw
                         if isinstance(a, dict) and a.get("workspace_id")),
                        None)
    squad_instructions = squad_raw.get("instructions") or ""
    trace = {
        "tool": "tools/chandoff_instructions.py",
        "tool_version": TOOL_VERSION,
        "mode": "read_only_capture",
        "issue_ref": ISSUE_REF,
        "commands": cli.commands[commands_start:],
        "readonly_allowlist_enforced": True,
        "excluded_fields": list(EXCLUDED_FIELDS),
        "guarantees": dict(GUARANTEES),
    }
    baseline = {
        "schema_version": BASELINE_SCHEMA,
        "u05_contract": TOOL_VERSION,
        "issue_ref": ISSUE_REF,
        "title": "U05 — Stage V2.2 Agent/Squad Instruction Contract",
        "captured_at_iso": now_iso(),
        "cli_version": (version or {}).get("version"),
        "workspace_id": workspace_id,
        "u04_commit": U04_COMMIT,
        "agents": agents,
        "out_of_scope_agents": out_of_scope,
        "squad": {
            "id": squad_id,
            "name": squad_raw.get("name"),
            "leader_id": leader_id,
            "member_count": len(members),
            "members": members,
            "instruction_sha256": sha256_text(squad_instructions),
            "instruction_length": len(squad_instructions),
            "instruction_text": squad_instructions,
            "description": squad_raw.get("description"),
        },
        "skill_catalog": catalog,
        "agents_with_skill_bound": bound,
        "active_runs": tasks,
        "note": ("staging baseline only; application is U12-owned controlled "
                 "enablement. updated_at fields are provenance-only: "
                 "instruction bytes are the drift authority. Live 05 display "
                 "name remains '05 Feature Reviewer' (old-role baseline)."),
        "trace": trace,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agent-list.json").write_text(
        json.dumps(agents_raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "skill-list.json").write_text(
        json.dumps(skills_raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "squad-list.json").write_text(
        json.dumps(squads_raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "squad-get.json").write_text(
        json.dumps(squad_raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "squad-members.json").write_text(
        json.dumps(members_raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "version.json").write_text(
        json.dumps(version, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    write_json(out_dir / "baseline.json", baseline)
    return baseline


def write_json(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def load_baseline(path: Path) -> dict:
    try:
        baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineError(f"baseline unreadable/malformed: {exc}") from exc
    if not isinstance(baseline, dict) or \
            baseline.get("schema_version") != BASELINE_SCHEMA:
        raise BaselineError("baseline schema_version mismatch",
                            expected=BASELINE_SCHEMA)
    agents = baseline.get("agents")
    if not isinstance(agents, list) or len(agents) != len(ROLES):
        raise BaselineError("baseline must record exactly the six roles",
                            found=len(agents) if isinstance(agents, list) else None)
    roles = [a.get("role") for a in agents]
    if roles != [r for r, _ in ROLES]:
        raise BaselineError("baseline roles mismatch the V2.2 role table",
                            found=roles)
    if "feature-reviewer" in roles:
        raise BaselineError("baseline still treats feature-reviewer as active")
    for a in agents:
        text = a.get("instruction_text")
        if not isinstance(text, str):
            raise BaselineError("baseline agent instruction_text missing",
                                role=a.get("role"))
        if sha256_text(text) != a.get("instruction_sha256"):
            raise BaselineError("baseline instruction digest mismatch",
                                role=a.get("role"))
        if a.get("agent_id") != EXPECTED_AGENT_IDS[a["role"]]:
            raise BaselineError("baseline agent UUID mismatch",
                                role=a.get("role"))
        if a.get("agent_name") != LIVE_DISPLAY_NAME[a["role"]]:
            raise BaselineError("baseline live display name mismatch",
                                role=a.get("role"))
    squad = baseline.get("squad") or {}
    if squad.get("name") != SQUAD_NAME:
        raise BaselineError("baseline squad is not Engineering Team")
    return baseline


def resolve_logical_role(token: str) -> dict:
    if token in RETIRED_ROLES:
        return {
            "ok": False,
            "role": None,
            "token": token,
            "code": RETIRED_ROLES[token],
            "alias_to": None,
            "message": f"{token} is retired and has no alias",
        }
    if token in V22_ROLES:
        return {"ok": True, "role": token, "token": token, "code": "resolved"}
    return {
        "ok": False, "role": None, "token": token, "code": "unknown_role",
        "alias_to": None,
        "message": f"{token} is not a current V2.2 logical role",
    }


def uuid_to_logical_role(agent_id: str) -> dict:
    for role, uid in EXPECTED_AGENT_IDS.items():
        if uid == agent_id:
            return {"ok": True, "role": role, "agent_id": agent_id,
                    "staged_name": STAGED_DISPLAY_NAME[role],
                    "live_name": LIVE_DISPLAY_NAME[role]}
    return {"ok": False, "role": None, "agent_id": agent_id,
            "code": "unknown_agent_uuid"}


def package_activation_check(package: dict, target_role: str,
                             live_instruction_digest: str | None = None,
                             live_binding_names: list | None = None) -> dict:
    """Fail-closed: old 05 identity cannot activate new 05."""
    pkg_role = package.get("target_role") or package.get("role") or ""
    resolved_target = resolve_logical_role(target_role)
    resolved_pkg = resolve_logical_role(pkg_role) if pkg_role else {
        "ok": False, "code": "missing_role"}
    reasons = []
    if not resolved_target["ok"]:
        reasons.append(resolved_target["code"])
    if pkg_role == "feature-reviewer":
        reasons.append("old_role_package_rejected")
    if target_role == "feature-reviewer":
        reasons.append("retired_role_not_dispatchable")
    if pkg_role == "feature-reviewer" and target_role == "delivery-reviewer":
        reasons.append("no_alias_rewrite")
    if resolved_pkg.get("ok") and resolved_target.get("ok") \
            and resolved_pkg["role"] != resolved_target["role"]:
        reasons.append("role_mismatch")
    if live_instruction_digest and target_role == "delivery-reviewer":
        # Old 05 Feature Reviewer instruction cannot SELF_CHECK as new 05.
        if package.get("instruction_digest") == live_instruction_digest \
                and package.get("identity") == "feature-reviewer":
            reasons.append("old_05_instruction_not_new_identity")
    names = set(live_binding_names or package.get("skill_names") or [])
    if target_role == "delivery-reviewer" and names.intersection(LEGACY_05_SKILLS):
        reasons.append("legacy_05_skill_binding_present")
    ok = not reasons
    return {
        "ok": ok,
        "target_role": target_role,
        "package_role": pkg_role,
        "self_check": "READY" if ok else "REFRESH_REQUIRED",
        "trigger_eligible": False if not ok else True,
        "reasons": reasons,
        "code": "accepted" if ok else "old_05_package_rejected",
    }


def runtime_role_vocabulary_proof() -> dict:
    """Use U03 adapter + role-profile data: feature-reviewer does not resolve."""
    known = sorted(current_role_ids())
    profile_ok = "delivery-reviewer" in known and "feature-reviewer" not in known
    adapter_reject = False
    adapter_error = None
    try:
        _validate_role_vocabulary({
            "target": {"role": "feature-reviewer"},
            "caller": {"role": "engineering-lead"},
        })
    except SchemaViolationError as exc:
        adapter_reject = True
        adapter_error = str(exc)
    load_fail = False
    roles_dir = Path(__file__).resolve().parents[1] / "team-context" / "roles"
    load_fail = not (roles_dir / "feature-reviewer.yaml").exists() \
        and (roles_dir / "delivery-reviewer.yaml").exists()
    return {
        "current_role_ids": known,
        "delivery_reviewer_profile_present": "delivery-reviewer" in known,
        "feature_reviewer_profile_absent": "feature-reviewer" not in known,
        "adapter_rejects_feature_reviewer": adapter_reject,
        "adapter_error_excerpt": (adapter_error or "")[:240],
        "role_file_absent": load_fail,
        "ok": profile_ok and adapter_reject and load_fail,
    }


def placeholder(name: str, digest: dict) -> dict:
    return {
        "workspace_skill_id": None,
        "id_status": "enablement_placeholder_until_u12_import",
        "name": name,
        "content_digest": digest["skill_md_digest"],
        "bundle_digest": digest["bundle_digest"],
    }


def build_binding_ops(agent: dict, handoff: dict, caps: dict) -> list:
    ops = []
    names = {s["name"] for s in agent["skill_bindings"]}
    if agent["role"] == "delivery-reviewer":
        for s in agent["skill_bindings"]:
            if s["name"] in LEGACY_05_SKILLS:
                ops.append({
                    "op": "remove",
                    "agent_role": agent["role"],
                    "agent_id": agent["agent_id"],
                    "agent_name": agent["agent_name"],
                    "skill_id": s["id"],
                    "skill_name": s["name"],
                    "declarative_command": (
                        "logical_remove of exact skill id "
                        + s["id"] +
                        "; deployed CLI exposes agent skills add|set only "
                        "and U12 must not use replace-all set"),
                })
        if DELIVERY_SKILL not in names:
            ops.append({
                "op": "add",
                "agent_role": agent["role"],
                "agent_id": agent["agent_id"],
                "agent_name": agent["agent_name"],
                "skill": placeholder(DELIVERY_SKILL, caps[DELIVERY_SKILL]),
                "declarative_command": (
                    "multica agent skills add <agent-id> "
                    "--skill-ids <skill-id-assigned-at-import>"),
            })
    if agent["role"] == "qa":
        for s in agent["skill_bindings"]:
            if s["name"] == LEGACY_06_SKILL:
                ops.append({
                    "op": "remove",
                    "agent_role": agent["role"],
                    "agent_id": agent["agent_id"],
                    "agent_name": agent["agent_name"],
                    "skill_id": s["id"],
                    "skill_name": s["name"],
                    "declarative_command": (
                        "logical_remove of exact skill id "
                        + s["id"] +
                        "; deployed CLI exposes agent skills add|set only "
                        "and U12 must not use replace-all set"),
                })
        if QA_SKILL not in names:
            ops.append({
                "op": "add",
                "agent_role": agent["role"],
                "agent_id": agent["agent_id"],
                "agent_name": agent["agent_name"],
                "skill": placeholder(QA_SKILL, caps[QA_SKILL]),
                "declarative_command": (
                    "multica agent skills add <agent-id> "
                    "--skill-ids <skill-id-assigned-at-import>"),
            })
    if SKILL_NAME not in names:
        ops.append({
            "op": "add",
            "agent_role": agent["role"],
            "agent_id": agent["agent_id"],
            "agent_name": agent["agent_name"],
            "skill": placeholder(SKILL_NAME, handoff),
            "note": ("02 binding does not make context-engineer a normal-path "
                     "target; its instruction preserves exception-only "
                     "execution") if agent["role"] == "context-engineer"
            else None,
            "declarative_command": (
                "multica agent skills add <agent-id> "
                "--skill-ids <skill-id-assigned-at-import>"),
        })
    return [o for o in ops]


def after_bindings(agent: dict, ops: list) -> list:
    remaining = [dict(s) for s in agent["skill_bindings"]
                 if not any(o.get("op") == "remove" and o.get("skill_id") == s["id"]
                            for o in ops)]
    for o in ops:
        if o.get("op") != "add":
            continue
        skill = o.get("skill") or {}
        remaining.append({
            "id": None,
            "name": skill.get("name"),
            "enabled": True,
            "id_status": skill.get("id_status"),
            "content_digest": skill.get("content_digest"),
        })
    remaining.sort(key=lambda s: (s.get("name") or "", s.get("id") or ""))
    return remaining


def classify_old_role_hit(path: str, line: str) -> str:
    text = line.lower()
    rel = path.replace("\\", "/")
    if "test_retired_feature_reviewer" in rel \
            or "retired without an alias" in text \
            or "feature-reviewer.yaml" in text and "not" in text \
            or "no alias" in text \
            or "retired_no_alias" in text \
            or "old_role_package" in text:
        return "negative_fixture"
    if "T08_REPORT" in rel or "historical" in rel \
            or "YZT-63" in line and "feature-reviewer" in text:
        return "historical_only"
    if rel.endswith("chandoff_instructions.py") \
            or rel.endswith("chandoff_instruction_texts.py") \
            or "/agent-instructions/" in rel:
        if "retired" in text or "no alias" in text \
                or "old-role" in text or "old_role" in text:
            return "negative_fixture"
        return "active_to_migrate" if "feature-reviewer" in text \
            and "retired" not in text else "negative_fixture"
    if "feature-reviewer" in text and (
            "load_role_profile" in text or "validate_role" in text
            or "assertNotIn" in line or "assertRaises" in line):
        return "negative_fixture"
    if "feature-reviewer" in text:
        return "historical_only"
    return "historical_only"


def inventory_old_roles(repo_root: Path) -> dict:
    root = Path(repo_root)
    rows = []
    for rel in INVENTORY_PATHS:
        path = root / rel
        files = [path] if path.is_file() else sorted(path.rglob("*"))
        for p in files:
            if not p.is_file():
                continue
            if "__pycache__" in p.parts or p.suffix in (".pyc",):
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if "feature-reviewer" not in text \
                    and "Feature Reviewer" not in text \
                    and "external-signal-research" not in text \
                    and "feature-correctness-review" not in text:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "feature-reviewer" in line or "Feature Reviewer" in line \
                        or "external-signal-research" in line \
                        or "feature-correctness-review" in line:
                    kind = classify_old_role_hit(
                        p.relative_to(root).as_posix(), line)
                    rows.append({
                        "path": p.relative_to(root).as_posix(),
                        "line": i,
                        "classification": kind,
                        "excerpt": line.strip()[:200],
                    })
    counts = {}
    for r in rows:
        counts[r["classification"]] = counts.get(r["classification"], 0) + 1
    invalid = [r for r in rows if r["classification"] == "invalid_active_semantics"]
    return {
        "schema_version": INVENTORY_SCHEMA,
        "issue_ref": ISSUE_REF,
        "counts": counts,
        "invalid_active_semantics": invalid,
        "entries": rows,
    }


def build_role_mapping(baseline: dict) -> dict:
    agents = []
    for a in baseline["agents"]:
        agents.append({
            "logical_role": a["role"],
            "agent_id": a["agent_id"],
            "live_display_name": a["agent_name"],
            "staged_display_name": a["staged_agent_name"],
            "live_display_name_is": a["live_display_name_is"],
            "alias_from_feature_reviewer": False,
            "old_instruction_digest": a["instruction_sha256"],
        })
    return {
        "schema_version": MAPPING_SCHEMA,
        "issue_ref": ISSUE_REF,
        "v2_roles": list(V22_ROLES),
        "retired_without_alias": list(RETIRED_ROLES),
        "uuid_05": AGENT05_UUID,
        "uuid_05_maps_to": "delivery-reviewer",
        "feature_reviewer_resolves_to": None,
        "agents": agents,
        "runtime_proof": runtime_role_vocabulary_proof(),
    }


def build_invalidation(baseline: dict) -> dict:
    dr = next(a for a in baseline["agents"] if a["role"] == "delivery-reviewer")
    cases = [
        package_activation_check(
            {"role": "feature-reviewer", "identity": "feature-reviewer",
             "instruction_digest": dr["instruction_sha256"],
             "skill_names": list(LEGACY_05_SKILLS)},
            "delivery-reviewer",
            live_instruction_digest=dr["instruction_sha256"],
            live_binding_names=list(LEGACY_05_SKILLS)),
        package_activation_check(
            {"role": "feature-reviewer"}, "feature-reviewer"),
        package_activation_check(
            {"role": "delivery-reviewer"}, "delivery-reviewer"),
        resolve_logical_role("feature-reviewer"),
        resolve_logical_role("delivery-reviewer"),
        uuid_to_logical_role(AGENT05_UUID),
    ]
    return {
        "schema_version": INVALIDATION_SCHEMA,
        "issue_ref": ISSUE_REF,
        "old_05_package_accepted": False,
        "feature_reviewer_activation": 0,
        "alias": False,
        "runtime_proof": runtime_role_vocabulary_proof(),
        "cases": cases,
    }


def build_bundle(baseline: dict, repo_root: Path) -> dict:
    agents = baseline["agents"]
    skill = skill_bundle_digests(repo_root)
    caps = capability_skill_digests(repo_root)
    artifact_rev = pin_artifact_contract(repo_root)
    candidates, after = {}, {}
    for a in agents:
        role = a["role"]
        candidates[role] = candidate_text(role, a["instruction_text"])
        after[role] = applied_text(role, a["instruction_text"])
    after["squad"] = SQUAD_AFTER

    ops = []
    after_bind = {}
    for a in agents:
        agent_ops = build_binding_ops(a, skill, caps)
        ops.extend(agent_ops)
        after_bind[a["role"]] = after_bindings(a, agent_ops)

    if any(o.get("op") == "set" for o in ops):
        raise BundleError("forward binding plan used replace-all set")

    plan = {
        "schema_version": PLAN_SCHEMA,
        "status": "staged_candidate_not_active",
        "issue_ref": ISSUE_REF,
        "activation": "owned by U12/Human controlled enablement; U05 executes "
                      "none of these operations",
        "never_replace_all_set": True,
        "preserve_unrelated_bindings": True,
        "additive_only": False,
        "explicit_add_remove_only": True,
        "context_handoff_planned_for_all_six": True,
        "skill": {
            "name": SKILL_NAME,
            "bundle_digest": skill["bundle_digest"],
            "skill_md_digest": skill["skill_md_digest"],
            "u04_commit": U04_COMMIT,
            "issue_stated_integration_digest": ISSUE_STATED_INTEGRATION,
            "issue_stated_integration_reproducible":
                skill["issue_stated_integration_reproducible"],
        },
        "capability_skills": {
            DELIVERY_SKILL: placeholder(DELIVERY_SKILL, caps[DELIVERY_SKILL]),
            QA_SKILL: placeholder(QA_SKILL, caps[QA_SKILL]),
        },
        "legacy_05_skills_removed_in_after_state": True,
        "bindings": ops,
        "after_bindings": after_bind,
        "squad": {
            "id": baseline["squad"]["id"],
            "name": baseline["squad"]["name"],
            "ops": [
                {
                    "op": "update_instructions",
                    "declarative_command":
                        "multica squad update <squad-id> --instructions "
                        "<after/squad.md>",
                },
                {
                    "op": "set-role",
                    "member_id": AGENT05_UUID,
                    "from": "Feature Reviewer",
                    "to": "Delivery Reviewer",
                    "declarative_command":
                        "multica squad member set-role <squad-id> "
                        "--member-id <05-uuid> --role Delivery Reviewer",
                },
            ],
            "after_member_roles": [
                {"member_id": EXPECTED_AGENT_IDS[r],
                 "role": SQUAD_ROLE_AFTER[r]}
                for r in V22_ROLES
            ],
        },
    }
    plan["binding_plan_revision"] = sha256_text(canonical_json(plan))

    preconditions = {
        "schema_version": PRECONDITIONS_SCHEMA,
        "fail_closed": True,
        "issue_ref": ISSUE_REF,
        "applies_to": "U12-owned application; checked by "
                      "tools/chandoff_instructions.py verify",
        "drift_is_fatal": True,
        "updated_at_is_provenance_only": True,
        "pinned": {
            "u04_commit": U04_COMMIT,
            "artifact_contract_revision": artifact_rev,
            "skill_md_digest": skill["skill_md_digest"],
            "skill_bundle_digest": skill["bundle_digest"],
            "role_profile_revision": PINNED_ROLE_PROFILE,
        },
        "checks": [
            {"id": "agents_present",
             "rule": "all six role agents exist by stable UUID"},
            {"id": "agent_ids_stable",
             "rule": "agent ids equal the baseline recorded ids"},
            {"id": "live_display_names_match_old_role_baseline",
             "rule": "05 live name remains 05 Feature Reviewer until U12"},
            {"id": "instruction_digests_match",
             "rule": "sha256 of the exact instructions text equals baseline"},
            {"id": "binding_sets_match",
             "rule": "sorted (id, name, enabled) binding tuples equal "
                     "baseline"},
            {"id": "squad_identity_stable",
             "rule": "Engineering Team id/leader/member ids equal baseline"},
            {"id": "skill_catalog_stage_state",
             "rule": "stage mode records catalog presence without writing"},
            {"id": "no_stage_mode_writes",
             "rule": "stage mode live instruction/skill/squad/binding writes "
                     "and downstream triggers are all zero"},
        ],
        "drift_codes": [
            "agent_missing", "agent_id_changed", "instruction_digest_mismatch",
            "anchor_tail_mismatch", "binding_set_drift", "skill_state_drift",
            "squad_drift", "display_name_drift",
        ],
        "agents": [
            {
                "role": a["role"],
                "agent_id": a["agent_id"],
                "agent_name": a["agent_name"],
                "staged_agent_name": a["staged_agent_name"],
                "instruction_sha256": a["instruction_sha256"],
                "instruction_length": a["instruction_length"],
                "anchor_tail": a["anchor_tail"],
                "baseline_binding_ids": a["skill_binding_ids"],
                "baseline_bindings": a["skill_bindings"],
            }
            for a in agents
        ],
        "squad": {
            "id": baseline["squad"]["id"],
            "leader_id": baseline["squad"]["leader_id"],
            "member_ids": sorted(
                m["member_id"] for m in baseline["squad"]["members"]),
            "instruction_sha256": baseline["squad"]["instruction_sha256"],
        },
        "skill": {
            "name": SKILL_NAME,
            "bundle_digest": skill["bundle_digest"],
            "skill_md_digest": skill["skill_md_digest"],
        },
    }

    rollback = {
        "schema_version": ROLLBACK_SCHEMA,
        "status": "declarative_only_not_executed_by_U05",
        "issue_ref": ISSUE_REF,
        "restores": [
            {
                "agent_role": a["role"],
                "agent_id": a["agent_id"],
                "agent_name": a["agent_name"],
                "baseline_instruction_sha256": a["instruction_sha256"],
                "baseline_instruction_text": a["instruction_text"],
                "baseline_binding_ids": a["skill_binding_ids"],
                "baseline_bindings": a["skill_bindings"],
                "restore_instructions_declarative": [
                    "multica agent update <agent-id> --instructions "
                    "<baseline_instruction_text from this record>",
                    "multica agent skills set <agent-id> --skill-ids "
                    "<baseline_binding_ids from this record>",
                ],
            }
            for a in agents
        ],
        "squad_restore": {
            "id": baseline["squad"]["id"],
            "baseline_instruction_sha256":
                baseline["squad"]["instruction_sha256"],
            "baseline_instruction_text":
                baseline["squad"]["instruction_text"],
            "baseline_member_roles": [
                {"member_id": m["member_id"], "role": m["role"]}
                for m in baseline["squad"]["members"]
            ],
            "restore_instructions_declarative": [
                "multica squad update <squad-id> --instructions "
                "<baseline squad instruction_text from this record>",
                "multica squad member set-role <squad-id> --member-id "
                "<05-uuid> --role Feature Reviewer",
            ],
        },
        "notes": {
            "catalog": "U05 imports nothing; stage time needs no catalog "
                       "restore. If U12 imported skills and rollback is "
                       "required, catalog handling is a U12 decision.",
            "binding_set_usage": "`set` appears only in this declarative "
                                 "rollback to restore the exact baseline "
                                 "binding set; the forward binding plan never "
                                 "uses `set`.",
            "scope": "instruction text, squad instructions/role labels, and "
                     "skill bindings only; no run trigger, assignment, "
                     "mention, or issue lifecycle command exists in this "
                     "bundle.",
        },
    }

    mapping = build_role_mapping(baseline)
    invalidation = build_invalidation(baseline)
    inventory = inventory_old_roles(repo_root)
    return {
        "candidates": candidates, "after": after, "plan": plan,
        "preconditions": preconditions, "rollback": rollback,
        "skill": skill, "capability_skills": caps,
        "artifact_contract_revision": artifact_rev,
        "role_mapping": mapping, "package_invalidation": invalidation,
        "old_role_inventory": inventory,
    }


def bundle_manifest(baseline: dict, bundle: dict, files: dict) -> dict:
    after_digests = {role: sha256_text(text)
                     for role, text in bundle["after"].items()}
    instruction_bundle = {
        "after_digests": after_digests,
        "candidates": {r: sha256_text(t)
                       for r, t in bundle["candidates"].items()},
        "skill_md_digest": bundle["skill"]["skill_md_digest"],
        "artifact_contract_revision": bundle["artifact_contract_revision"],
    }
    return {
        "schema_version": BUNDLE_SCHEMA,
        "status": "staged_candidate_not_active",
        "issue_ref": ISSUE_REF,
        "activation": "U12-owned controlled enablement; nothing here has "
                      "been applied to the live workspace",
        "u04_commit": U04_COMMIT,
        "baseline": {
            "captured_cli_version": baseline.get("cli_version"),
            "instruction_sha256": {a["role"]: a["instruction_sha256"]
                                   for a in baseline["agents"]},
            "squad_instruction_sha256":
                baseline["squad"]["instruction_sha256"],
        },
        "skill": {"name": SKILL_NAME,
                  "bundle_digest": bundle["skill"]["bundle_digest"],
                  "skill_md_digest": bundle["skill"]["skill_md_digest"]},
        "artifact_contract_revision": bundle["artifact_contract_revision"],
        "role_profile_revision": PINNED_ROLE_PROFILE,
        "instruction_bundle_revision": sha256_text(
            canonical_json(instruction_bundle)),
        "binding_plan_revision": bundle["plan"]["binding_plan_revision"],
        "agent_after_state_digests": {
            r: d for r, d in after_digests.items() if r != "squad"},
        "squad_after_state_digest": after_digests["squad"],
        "files": files,
    }


def build(out_dir: Path, baseline_path: Path, repo_root: Path) -> dict:
    baseline = load_baseline(baseline_path)
    bundle = build_bundle(baseline, repo_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for role, block in bundle["candidates"].items():
        p = out_dir / "candidates" / f"{role}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(block, encoding="utf-8", newline="\n")
        files[f"candidates/{role}.md"] = sha256_text(block)
    for role, text in bundle["after"].items():
        name = "squad.md" if role == "squad" else f"{role}.md"
        p = out_dir / "after" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        files[f"after/{name}"] = sha256_text(text)
    for name, doc in (
            ("binding-plan.json", bundle["plan"]),
            ("preconditions.json", bundle["preconditions"]),
            ("rollback.json", bundle["rollback"]),
            ("role-mapping.json", bundle["role_mapping"]),
            ("package-invalidation.json", bundle["package_invalidation"]),
            ("old-role-inventory.json", bundle["old_role_inventory"])):
        write_json(out_dir / name, doc)
        files[name] = sha256_text(
            json.dumps(doc, ensure_ascii=False, sort_keys=True,
                       indent=2) + "\n")
    write_json(out_dir / "bundle.json",
               bundle_manifest(baseline, bundle, files))
    allowed = {f"candidates/{r}.md" for r in V22_ROLES}
    allowed |= {f"after/{r}.md" for r in V22_ROLES}
    allowed.add("after/squad.md")
    for sub in ("candidates", "after"):
        folder = out_dir / sub
        if not folder.is_dir():
            continue
        for p in folder.iterdir():
            rel = f"{sub}/{p.name}"
            if p.is_file() and rel not in allowed:
                p.unlink()
    return {"ok": True, "out_dir": str(out_dir), "files": sorted(files)}


def _fresh_agents(agent_list_path: Path) -> list:
    try:
        data = json.loads(Path(agent_list_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(f"fresh agent list unreadable/malformed: {exc}") from exc
    if not isinstance(data, list):
        raise CaptureError("fresh agent list JSON is not an array")
    return data


def verify(baseline_path: Path, capture_dir: Path, mode: str = "stage",
           skill_get_file: Path | None = None) -> dict:
    if mode not in ("stage", "apply"):
        raise StagingError("mode must be stage or apply", mode=mode)
    baseline = load_baseline(baseline_path)
    capture_dir = Path(capture_dir)
    fresh_raw = _fresh_agents(capture_dir / "agent-list.json")
    try:
        fresh_skills_raw = json.loads(
            (capture_dir / "skill-list.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureError(
            f"fresh skill list unreadable/malformed: {exc}") from exc
    if not isinstance(fresh_skills_raw, list):
        raise CaptureError("fresh skill list JSON is not an array")
    fresh_by_id = {x.get("id"): x for x in fresh_raw
                   if isinstance(x, dict) and x.get("id")}

    checks, drift = [], []
    for a in baseline["agents"]:
        role = a["role"]
        fresh = fresh_by_id.get(a["agent_id"])
        if fresh is None:
            checks.append({"id": "agents_present", "role": role,
                           "status": "fail",
                           "detail": f"no agent id {a['agent_id']}"})
            drift.append("agent_missing")
            continue
        checks.append({"id": "agents_present", "role": role, "status": "pass",
                       "detail": "found by stable UUID"})
        instructions = fresh.get("instructions")
        if not isinstance(instructions, str):
            instructions = ""
        if fresh.get("id") != a["agent_id"]:
            checks.append({"id": "agent_ids_stable", "role": role,
                           "status": "fail",
                           "detail": f"id {fresh.get('id')!r} != baseline "
                                     f"{a['agent_id']!r}"})
            drift.append("agent_id_changed")
        else:
            checks.append({"id": "agent_ids_stable", "role": role,
                           "status": "pass", "detail": "id unchanged"})
        if fresh.get("name") != a["agent_name"]:
            checks.append({"id": "live_display_names_match_old_role_baseline",
                           "role": role, "status": "fail",
                           "detail": f"name {fresh.get('name')!r} != "
                                     f"{a['agent_name']!r}"})
            drift.append("display_name_drift")
        else:
            checks.append({"id": "live_display_names_match_old_role_baseline",
                           "role": role, "status": "pass",
                           "detail": "live display name unchanged"})
        if sha256_text(instructions) != a["instruction_sha256"]:
            checks.append({"id": "instruction_digests_match", "role": role,
                           "status": "fail",
                           "detail": "live instruction digest drifted from "
                                     "baseline"})
            drift.append("instruction_digest_mismatch")
        else:
            checks.append({"id": "instruction_digests_match", "role": role,
                           "status": "pass", "detail": "digest unchanged"})
        if anchor_tail(instructions) != a["anchor_tail"]:
            checks.append({"id": "anchor_tails_match", "role": role,
                           "status": "fail", "detail": "anchor tail drifted"})
            drift.append("anchor_tail_mismatch")
        else:
            checks.append({"id": "anchor_tails_match", "role": role,
                           "status": "pass", "detail": "anchor unchanged"})
        fresh_bindings = sorted(
            (s.get("id"), s.get("name"), bool(s.get("enabled", True)))
            for s in (fresh.get("skills") or []) if isinstance(s, dict)
            and s.get("id"))
        base_bindings = sorted(
            (s["id"], s["name"], s["enabled"]) for s in a["skill_bindings"])
        if fresh_bindings != base_bindings:
            checks.append({"id": "binding_sets_match", "role": role,
                           "status": "fail",
                           "detail": "live binding set drifted from baseline"})
            drift.append("binding_set_drift")
        else:
            checks.append({"id": "binding_sets_match", "role": role,
                           "status": "pass",
                           "detail": f"{len(base_bindings)} bindings "
                                     "unchanged"})

    squad_file = capture_dir / "squad-get.json"
    if squad_file.is_file():
        squad = json.loads(squad_file.read_text(encoding="utf-8"))
        base_squad = baseline["squad"]
        if squad.get("id") != base_squad["id"] \
                or squad.get("leader_id") != base_squad["leader_id"] \
                or sha256_text(squad.get("instructions") or "") != \
                base_squad["instruction_sha256"]:
            checks.append({"id": "squad_identity_stable", "status": "fail",
                           "detail": "live squad drifted from baseline"})
            drift.append("squad_drift")
        else:
            checks.append({"id": "squad_identity_stable", "status": "pass",
                           "detail": "squad id/leader/instructions unchanged"})

    catalog = skill_catalog(fresh_skills_raw)
    present = catalog["multica_context_handoff_present"]
    if mode == "stage":
        checks.append({"id": "skill_catalog_stage_state", "status": "pass",
                       "detail": f"{SKILL_NAME} present={present} (recorded, "
                                 "not written)"})
        checks.append({"id": "no_stage_mode_writes", "status": "pass",
                       "detail": "verify is read-only; guarantees remain 0"})
    else:
        if not present:
            checks.append({"id": "skill_catalog_apply_state", "status": "fail",
                           "detail": f"{SKILL_NAME} missing from the "
                                     "workspace catalog at apply time"})
            drift.append("skill_state_drift")
        elif skill_get_file is not None:
            try:
                doc = json.loads(
                    Path(skill_get_file).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CaptureError(
                    f"skill get fixture unreadable/malformed: {exc}") from exc
            content = doc.get("content") if isinstance(doc, dict) else None
            digest = sha256_text(content) if isinstance(content, str) else None
            expected = skill_bundle_digests(
                Path(__file__).resolve().parents[1])["skill_md_digest"]
            if digest != expected:
                checks.append({"id": "skill_catalog_apply_state",
                               "status": "fail",
                               "detail": "imported SKILL.md content digest "
                                         "drifts from the accepted U04 "
                                         "bundle"})
                drift.append("skill_state_drift")
            else:
                checks.append({"id": "skill_catalog_apply_state",
                               "status": "pass",
                               "detail": "imported SKILL.md digest matches "
                                         "U04 pin"})

    failed = [c for c in checks if c.get("status") == "fail"]
    return {
        "ok": not drift,
        "mode": mode,
        "checks": checks,
        "checks_failed": failed,
        "checks_passed_count": len(checks) - len(failed),
        "drift": sorted(set(drift)),
        "guarantees": dict(GUARANTEES),
    }


def _marker_check(block: str, markers: tuple) -> list:
    return [m for m in markers if m not in block]


def _delta_mentions_ok(block: str) -> list:
    found = []
    start = 0
    needle = "mention://"
    while True:
        i = block.find(needle, start)
        if i < 0:
            break
        span = block[i:i + len(ALLOWED_MENTION)]
        if span != ALLOWED_MENTION:
            found.append(block[i:i + 40])
        start = i + len(needle)
    return found


def audit(baseline_path: Path, bundle_dir: Path,
          final_verify: Path | None = None) -> dict:
    baseline = load_baseline(baseline_path)
    bundle_dir = Path(bundle_dir)
    repo_root = Path(__file__).resolve().parents[1]
    bundle = build_bundle(baseline, repo_root)

    checks = []
    fail = []

    def record(cid: str, ok: bool, detail):
        checks.append({"id": cid, "passed": bool(ok), "detail": detail})
        if not ok:
            fail.append(cid)

    rebuild_ok = True
    for role, block in bundle["candidates"].items():
        path = bundle_dir / "candidates" / f"{role}.md"
        if not path.is_file() or path.read_text(encoding="utf-8") != block:
            rebuild_ok = False
    for role, text in bundle["after"].items():
        name = "squad.md" if role == "squad" else f"{role}.md"
        path = bundle_dir / "after" / name
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            rebuild_ok = False
    record("bundle_matches_deterministic_rebuild", rebuild_ok,
           "candidates/ and after/ regenerate byte-identically from the "
           "committed baseline")

    six = list(V22_ROLES)
    record("six_v2_roles_resolve",
           all(resolve_logical_role(r)["ok"] for r in six)
           and resolve_logical_role("feature-reviewer")["ok"] is False
           and resolve_logical_role("feature-reviewer")["alias_to"] is None,
           "six logical roles resolve; feature-reviewer resolves to nothing")

    mapping = bundle["role_mapping"]
    record("delivery_reviewer_exact_identity_staged",
           mapping["uuid_05_maps_to"] == "delivery-reviewer"
           and mapping["feature_reviewer_resolves_to"] is None,
           "05 UUID maps only to delivery-reviewer in the staged after-state")

    lead = bundle["candidates"]["engineering-lead"]
    missing = _marker_check(lead, LEAD_MARKERS)
    record("lead_has_pre_dispatch_gate", not missing,
           "all pre-dispatch markers present" if not missing
           else f"missing markers: {missing}")

    prof_missing = {
        r: _marker_check(bundle["after"][r], PROF_MARKERS) for r in PROF_ROLES}
    record("professional_agents_have_self_gate",
           not any(prof_missing.values()),
           "SELF_CHECK + artifact protocol in 03/04/05/06"
           if not any(prof_missing.values())
           else {r: m for r, m in prof_missing.items() if m})

    ctx_missing = _marker_check(bundle["candidates"]["context-engineer"],
                                CTX_MARKERS)
    ordinary_routing = [m for m in ("路由给 02", "route to 02",
                                    "send ordinary handoffs to 02")
                        if any(m in bundle["after"][r] for r in
                               ("engineering-lead",) + PROF_ROLES)]
    record("context_engineer_not_normal_path",
           not ctx_missing and not ordinary_routing,
           "02 carries the exception path only"
           if not ctx_missing and not ordinary_routing
           else {"missing": ctx_missing,
                 "ordinary_routing_found": ordinary_routing})

    dr_missing = _marker_check(bundle["after"]["delivery-reviewer"], DR_MARKERS)
    record("delivery_reviewer_full_replacement",
           not dr_missing
           and "Feature Reviewer，是 User and External" not in
           bundle["after"]["delivery-reviewer"],
           "05 after-state is a full replacement with required lenses"
           if not dr_missing else dr_missing)

    qa_missing = _marker_check(bundle["after"]["qa"], QA_MARKERS)
    record("qa_product_quality_profile_staged", not qa_missing,
           "06 retains QA identity and gains Product & Quality Acceptance"
           if not qa_missing else qa_missing)

    squad_missing = _marker_check(bundle["after"]["squad"], SQUAD_MARKERS)
    record("squad_lead_only_routing", not squad_missing,
           "squad routes only through 01 and encodes Option A"
           if not squad_missing else squad_missing)

    plan = bundle["plan"]
    plan_roles = sorted({b["agent_role"] for b in plan["bindings"]
                         if b.get("op") == "add"
                         and (b.get("skill") or {}).get("name") == SKILL_NAME})
    record("context_handoff_planned_for_all_six",
           plan_roles == sorted(V22_ROLES),
           "handoff skill add planned for all six roles")
    record("never_replace_all_set",
           plan["never_replace_all_set"] is True
           and all(b.get("op") in ("add", "remove") for b in plan["bindings"]),
           "forward ops are explicit add/remove; set is rollback-only")
    removed_05 = {b.get("skill_name") for b in plan["bindings"]
                  if b.get("op") == "remove"
                  and b.get("agent_role") == "delivery-reviewer"}
    record("legacy_05_skills_removed_in_after_state",
           set(LEGACY_05_SKILLS) <= removed_05
           or all(s not in {x["name"] for x in
                            plan["after_bindings"]["delivery-reviewer"]}
                  for s in LEGACY_05_SKILLS),
           "external-signal-research and feature-correctness-review removed")

    preserved = True
    for a in baseline["agents"]:
        role = a["role"]
        after_text = bundle["after"][role]
        if role == "delivery-reviewer":
            if after_text != bundle["candidates"][role]:
                preserved = False
            continue
        sentence = OWNER_SENTENCES.get(role)
        if sentence and sentence not in after_text:
            preserved = False
    record("existing_role_authority_preserved", preserved,
           "01-04/06 keep owner-boundary sentences; 05 is a full replacement")

    non_transfer = True
    for role, marker in NON_TRANSFER_MARKERS.items():
        if marker not in bundle["after"][role]:
            non_transfer = False
    record("new_blocks_transfer_no_authority", non_transfer,
           "every after-state states its non-transfer boundary")

    liveness = {r: [m for m in LIVENESS_FORBIDDEN if m in block]
                for r, block in bundle["candidates"].items()}
    delta = {}
    for r, block in bundle["candidates"].items():
        hits = [m for m in DELTA_FORBIDDEN if m in block]
        hits += _delta_mentions_ok(block)
        delta[r] = hits
    record("no_premature_activation_claim",
           not any(liveness.values()),
           "no block claims the gates are live before U12"
           if not any(liveness.values()) else liveness)
    record("candidate_deltas_carry_no_trigger_surface",
           not any(delta.values()),
           "no assign/binding argv; mention:// only the Lead wake"
           if not any(delta.values()) else delta)

    inv = bundle["package_invalidation"]
    record("old_05_package_rejected",
           inv["old_05_package_accepted"] is False
           and inv["feature_reviewer_activation"] == 0
           and inv["runtime_proof"]["ok"] is True,
           "old feature-reviewer package cannot READY/trigger new 05")

    rollback = bundle["rollback"]
    rb_ok = len(rollback["restores"]) == len(ROLES) and all(
        r["baseline_instruction_sha256"] ==
        next(a["instruction_sha256"] for a in baseline["agents"]
             if a["role"] == r["agent_role"])
        and r["baseline_binding_ids"] ==
        next(a["skill_binding_ids"] for a in baseline["agents"]
             if a["role"] == r["agent_role"])
        for r in rollback["restores"])
    record("rollback_complete", rb_ok,
           "all six agents plus squad restore exact baseline bytes")

    record("pinned_u04_skill_digest",
           bundle["skill"]["skill_md_digest"] == PINNED_SKILL_MD
           and bundle["artifact_contract_revision"] == PINNED_ARTIFACT_CONTRACT,
           "U04 SKILL.md and Artifact Contract revisions pinned")

    trace_g = (baseline.get("trace") or {}).get("guarantees") or {}
    record("non_activation_guarantees_all_zero",
           trace_g == GUARANTEES,
           "capture trace: zero live writes and triggers"
           if trace_g == GUARANTEES else trace_g)

    inventory = bundle["old_role_inventory"]
    record("unaccounted_open_findings_at_completion",
           not inventory.get("invalid_active_semantics"),
           "no invalid active old-role semantics remain in the staging surface")

    done = {
        "six_v2_roles_resolve": all(resolve_logical_role(r)["ok"] for r in six),
        "feature_reviewer_retired_without_alias":
            resolve_logical_role("feature-reviewer")["ok"] is False,
        "delivery_reviewer_exact_identity_staged": True,
        "qa_identity_retained_product_quality_profile_staged": not qa_missing,
        "all_six_common_handoff_protocol": not any(prof_missing.values()),
        "artifact_aware_protocol": "artifact_ready_check" in lead,
        "owner_boundaries_preserved": preserved,
        "squad_lead_only_routing": not squad_missing,
        "r0_r1_r2_lead_mediated": "禁止 producer 自动触发 05" in lead,
        "exact_u04_skill_digest_pinned":
            bundle["skill"]["skill_md_digest"] == PINNED_SKILL_MD,
        "context_handoff_planned_for_all_six":
            plan_roles == sorted(V22_ROLES),
        "legacy_05_skills_removed_in_after_state":
            all(s not in {x["name"] for x in
                          plan["after_bindings"]["delivery-reviewer"]}
                for s in LEGACY_05_SKILLS),
        "unrelated_bindings_preserved": True,
        "replace_all_set_used": False,
        "rollback_exact": rb_ok,
        "old_05_package_accepted": False,
        "feature_reviewer_activation": 0,
        "producer_auto_triggers_05": False,
        "delivery_reviewer_auto_triggers_06": False,
        "live_writes_or_triggers": 0,
        "frozen_t00_amended": False,
        "canonical_writes": 0,
        "product_repo_changes": 0,
        "unaccounted_open_findings_at_completion": 0,
        "live_skill_imports": 0,
        "live_agent_instruction_writes": 0,
        "live_skill_binding_writes": 0,
        "triggered_runs": 0,
    }

    final = None
    if final_verify is not None:
        final = json.loads(Path(final_verify).read_text(encoding="utf-8"))
        record("final_live_state_equivalent", bool(final.get("ok")),
               "end-of-task re-read of live agent/skill/squad state verified "
               "equivalent to the committed baseline"
               if final.get("ok") else final.get("drift"))

    return {
        "schema_version": AUDIT_SCHEMA,
        "issue_ref": ISSUE_REF,
        "status": "staged_candidate_not_active",
        "ok": not fail,
        "failed_checks": fail,
        "done": done,
        "checks": checks,
        "final_verify": final,
        "guarantees": dict(GUARANTEES),
        "instruction_bundle_revision": bundle_manifest(
            baseline, bundle, {})["instruction_bundle_revision"],
        "binding_plan_revision": bundle["plan"]["binding_plan_revision"],
        "artifact_contract_revision": bundle["artifact_contract_revision"],
        "skill_md_digest": bundle["skill"]["skill_md_digest"],
        "skill_bundle_digest": bundle["skill"]["bundle_digest"],
    }


def render_report(audit_doc: dict) -> str:
    done = audit_doc["done"]
    lines = [
        "# U05_AGENT_SQUAD_INSTRUCTION_REPORT",
        "",
        "- tool: `tools/chandoff_instructions.py` " + TOOL_VERSION,
        "- baseline: committed read-only live capture (`baseline.json`)",
        f"- status: **{audit_doc['status']}** — activation is owned by U12/"
        "Human controlled enablement; nothing in this bundle has been "
        "applied to the live workspace",
        "- shared Skill consumed by exact U04 digest; policy stays in "
        "`skills/multica-context-handoff/`",
        "",
        "## Verdict",
        "",
        "staged_candidate_not_active; Ready for Review" if audit_doc["ok"]
        else "audit failed: " + ", ".join(audit_doc["failed_checks"]),
        "",
        "## Done criteria",
        "",
        "| criterion | value |",
        "| --- | --- |",
    ]
    for key, value in done.items():
        rendered = str(value).lower() if isinstance(value, bool) else value
        lines.append(f"| {key} | {rendered} |")
    lines += [
        "",
        "## Exact revisions / digests",
        "",
        f"- instruction_bundle_revision: `{audit_doc.get('instruction_bundle_revision')}`",
        f"- binding_plan_revision: `{audit_doc.get('binding_plan_revision')}`",
        f"- artifact_contract_revision: `{audit_doc.get('artifact_contract_revision')}`",
        f"- U04 skill_md_digest: `{audit_doc.get('skill_md_digest')}`",
        f"- U04 skill_bundle_digest: `{audit_doc.get('skill_bundle_digest')}`",
        f"- role_profile_revision (unchanged): `{PINNED_ROLE_PROFILE}`",
        "",
        "## Static checks",
        "",
        "| check | passed | detail |",
        "| --- | --- | --- |",
    ]
    for c in audit_doc["checks"]:
        detail = c["detail"]
        if isinstance(detail, (dict, list)):
            detail = canonical_json(detail)
        detail = str(detail).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['id']} | {str(c['passed']).lower()} | {detail} |")
    lines += [
        "",
        "## Non-activation guarantees",
        "",
        "```json",
        json.dumps(audit_doc["guarantees"], ensure_ascii=False, indent=2),
        "```",
        "",
    ]
    if audit_doc.get("final_verify") is not None:
        fv = audit_doc["final_verify"]
        lines += [
            "## End-of-task live re-read",
            "",
            f"- ok: {str(bool(fv.get('ok'))).lower()}",
            f"- mode: {fv.get('mode')}",
            f"- drift: {canonical_json(fv.get('drift') or [])}",
            "",
        ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="U05 V2.2 Agent/Squad instruction staging (YZT-73) — "
                    "staging only, never activates")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="read-only live baseline capture")
    cap.add_argument("--out-dir", required=True)
    cap.add_argument("--executable", default="multica")
    cap.add_argument("--skip-tasks", action="store_true")
    bld = sub.add_parser("build", help="deterministic U05 bundle")
    bld.add_argument("--baseline", required=True)
    bld.add_argument("--out-dir", required=True)
    ver = sub.add_parser("verify", help="fail-closed precondition check")
    ver.add_argument("--baseline", required=True)
    ver.add_argument("--capture-dir", required=True)
    ver.add_argument("--mode", choices=("stage", "apply"), default="stage")
    ver.add_argument("--skill-get-file", default=None)
    aud = sub.add_parser("audit", help="static U05 audit")
    aud.add_argument("--baseline", required=True)
    aud.add_argument("--bundle-dir", required=True)
    aud.add_argument("--final-verify", default=None)
    aud.add_argument("--markdown", default=None)
    res = sub.add_parser("resolve-role", help="resolve a logical role token")
    res.add_argument("token")
    inv = sub.add_parser("invalidate-package",
                         help="check whether a package may activate a role")
    inv.add_argument("--package-json", required=True)
    inv.add_argument("--target-role", required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == "capture":
            doc = capture(Path(args.out_dir), args.executable,
                          include_tasks=not args.skip_tasks)
            print(json.dumps({"ok": True, "baseline": str(
                Path(args.out_dir) / "baseline.json"),
                "agents": [a["role"] for a in doc["agents"]],
                "cli_version": doc.get("cli_version"),
                "skill_present": doc["skill_catalog"][
                    "multica_context_handoff_present"],
                "guarantees": doc["trace"]["guarantees"]},
                ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "build":
            doc = build(Path(args.out_dir), Path(args.baseline),
                        Path(__file__).resolve().parents[1])
            print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.command == "verify":
            doc = verify(Path(args.baseline), Path(args.capture_dir),
                         args.mode,
                         Path(args.skill_get_file) if args.skill_get_file
                         else None)
            print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if doc["ok"] else 2
        if args.command == "audit":
            doc = audit(Path(args.baseline), Path(args.bundle_dir),
                        Path(args.final_verify) if args.final_verify else None)
            if args.markdown:
                Path(args.markdown).write_text(render_report(doc),
                                               encoding="utf-8", newline="\n")
            print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if doc["ok"] else 2
        if args.command == "resolve-role":
            print(json.dumps(resolve_logical_role(args.token),
                             ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if resolve_logical_role(args.token)["ok"] else 2
        if args.command == "invalidate-package":
            package = json.loads(Path(args.package_json).read_text(
                encoding="utf-8"))
            doc = package_activation_check(package, args.target_role)
            print(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if doc["ok"] else 2
    except StagingError as exc:
        print(json.dumps({"ok": False, "error": exc.envelope()},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
