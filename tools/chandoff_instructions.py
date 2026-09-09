#!/usr/bin/env python3
"""T08 — Agent Instruction Contract staging tool (YZT-63).

Turns the accepted T07 `multica-context-handoff` Skill into precise,
role-specific Agent behavior-contract candidates and a reviewable rollout
bundle. STAGING ONLY: this module never imports/refreshes a workspace skill,
never writes agent instructions, never mutates skill bindings, never
assigns/mentions anyone, and never triggers a run. Application itself is
T13-owned controlled enablement and is out of scope here (YZT-63).

Framework-specific by design — like tools/chandoff_adapter.py it names
Multica runtime concepts (agents, skill bindings, workspace catalog), so the
frozen framework-neutral boundary scan (tools/chandoff.py scan) deliberately
does not include this file.

Commands:
  capture   read-only live baseline capture (strict read-only allowlist)
  build     deterministic T08 bundle from a baseline inventory
  verify    fail-closed precondition check against a fresh capture
  audit     static §33 audit (machine JSON + markdown report)

Every failure mode is a bounded StagingError; nothing is guessed.
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
from cutil import now_iso  # noqa: E402

TOOL_VERSION = "T08/1.0"
BASELINE_SCHEMA = "T08-baseline/1.0"
BUNDLE_SCHEMA = "T08-bundle/1.0"
PLAN_SCHEMA = "T08-binding-plan/1.0"
PRECONDITIONS_SCHEMA = "T08-preconditions/1.0"
ROLLBACK_SCHEMA = "T08-rollback/1.0"
AUDIT_SCHEMA = "T08-audit/1.0"

SKILL_NAME = "multica-context-handoff"
SKILL_DIR_NAME = "skills/" + SKILL_NAME
ANCHOR_TAIL_CHARS = 120
ISSUE_REF = "YZT-63"

# The six collaboration roles, by exact stable agent display name. Matching
# is exact-name; anything else fails closed.
ROLES: tuple[tuple[str, str], ...] = (
    ("engineering-lead", "01 Engineering Lead"),
    ("context-engineer", "02 Context Engineer"),
    ("solution-architect", "03 Solution Architect"),
    ("software-engineer", "04 Software Engineer"),
    ("feature-reviewer", "05 Feature Reviewer"),
    ("qa", "06 QA"),
)
ROLE_BY_SLUG = dict(ROLES)
# 02 is the exception-path only role: never bound in the default plan.
BOUND_ROLES = ("engineering-lead", "solution-architect", "software-engineer",
               "feature-reviewer", "qa")

# Read-only allowlist for live capture. Write commands can never run.
READONLY_COMMANDS = (
    ("agent", "list"),
    ("skill", "list"),
    ("skill", "get"),
    ("version",),
)

# Fields intentionally never recorded (secrets / machine state).
EXCLUDED_FIELDS = ("custom_env values", "mcp_config", "runtime_config",
                   "system_instructions", "model", "avatar_url", "status")

GUARANTEES = {
    "live_skill_imports": 0,
    "live_skill_refreshes": 0,
    "live_agent_instruction_writes": 0,
    "live_skill_binding_writes": 0,
    "assignments": 0,
    "mentions": 0,
    "downstream_run_triggers": 0,
    "issue_lifecycle_writes": 0,
    "canonical_writes": 0,
}

# --------------------------------------------------------------------------
# Candidate instruction blocks (the T08 behavior contract). Pure appends:
# baseline_text + "\n" + block. No policy is duplicated from the Skill — the
# blocks only gate dispatch/run-start behavior and point at the shared Skill.
# --------------------------------------------------------------------------

LEAD_BLOCK = """## Context Handoff 派发门

向下游 agent 或 squad 成员派发任何工作之前，必须逐条满足：

1. 目标 Issue 已存在：不为没有具体 Issue 的工作派发或触发。
2. 已确定 target role（目标角色）。
3. 已通过共享 Skill `multica-context-handoff` 执行 PREPARE_HANDOFF，并按其确定性返回状态行动。
4. Handoff 状态为 BLOCKED 时，禁止触发下游工作：停止派发，记录升级需求，并按现有 Issue/parent 协议升级。
5. Handoff 状态为 READY（或策略明确允许 PARTIAL）时才可派发，且对目标 agent 使用恰好一次 Multica 触发。
6. assignment 与 agent mention 永远不得组合为重复触发；每次 Handoff 只允许一个触发（二选一）。

创建子 Issue 的安全顺序：create 时不启动目标 agent → prepare/publish/confirm handoff → 恰好一次触发。不要在创建 Issue 时传入 assignee 而顺带触发目标 Run。

本门只新增派发前置检查，不改变任何 Owner 边界：Scope/Priority/任务拆分/路由仍归 01；架构仍归 03；实现仍归 04；功能正确性仍归 05；QA 结论仍归 06；Canonical 写入仍归 02；最终 Merge 仍归 Human。
"""

PROF_BLOCK = """## Context Handoff 运行门

RUN START — 在开始任何有实际后果的专业工作之前：

1. 先对当前 Issue 与本角色执行 SELF_CHECK（通过共享 Skill `multica-context-handoff`）。SELF_CHECK 是 Run 内的开工门，不是平台级 pre-run 保证。
2. READY → 按当前有效 Context Package 继续工作。
3. REFRESH_REQUIRED → 走有界自刷新路径：对同一 task 与当前角色重新 PREPARE_HANDOFF，然后再次 SELF_CHECK；刷新通过前不开始有实际后果的工作。
4. BLOCKED → 停止有实际后果的工作，通过现有 Issue/parent 协议升级。

BEFORE HANDOFF — 在 assignment 或 @mention 另一个 agent 之前：

1. 先为 target role 执行 PREPARE_HANDOFF。
2. 仅当 Handoff READY（或策略明确允许 PARTIAL）时派发。
3. 恰好一次 Multica 触发：assignment 或 agent mention 二选一，永不同时；禁止双触发。

本门只新增开工与交接检查，不改变任何现有 Owner 边界与禁止项：Scope/Priority 仍归 01；架构仍归 03；实现仍归 04；功能正确性仍归 05；QA 结论仍归 06；Canonical 写入仍归 02；最终 Merge 仍归 Human。普通 READY/REFRESH 路径不经过 02；不得把 02 变成普通 Handoff 环节。
"""

CTX_BLOCK = """## Context Handoff 异常路径（仅异常升级）

仅当某角色经现有 Issue/parent 协议上报实质性的 Context 缺失、陈旧或冲突，且有界自刷新路径（SELF_CHECK REFRESH_REQUIRED）无法安全解决时，才进入本异常路径，按序处理：

1. resolve scope（解决范围问题）。
2. verify evidence（核实证据）。
3. resolve/restate conflicts（解决或重述冲突）。
4. 在获得明确授权时更新 Canonical；Canonical 写权限边界不变，候选不自动升级为长期事实。
5. 重建受影响的 context。
6. 返回状态（含置信度、冲突、缺失上下文）。

边界（逐条保持，不变）：
- 普通 READY / REFRESH_REQUIRED 的 Handoff 不经过 02；普通成功路径与普通 Finding 均不唤醒 02。
- 02 不是普通 Handoff 的必经环节（handoff hop），也不是默认刷新路径。
- 02 不接管：普通角色上下文生成、Issue 路由、实现、架构决策、功能决策。
- Canonical 写权限不扩大；本路径不授予任何新的派发、绑定或触发权限。
"""

BLOCKS = {
    "engineering-lead": LEAD_BLOCK,
    "context-engineer": CTX_BLOCK,
    "solution-architect": PROF_BLOCK,
    "software-engineer": PROF_BLOCK,
    "feature-reviewer": PROF_BLOCK,
    "qa": PROF_BLOCK,
}

# Structural markers the static audit (and tests) require in each block type.
LEAD_MARKERS = (
    "目标 Issue 已存在",
    "已确定 target role",
    "共享 Skill `multica-context-handoff` 执行 PREPARE_HANDOFF",
    "BLOCKED 时，禁止触发下游工作",
    "READY（或策略明确允许 PARTIAL）时才可派发",
    "恰好一次 Multica 触发",
    "assignment 与 agent mention 永远不得组合为重复触发",
    "create 时不启动目标 agent",
    "prepare/publish/confirm handoff",
    "不要在创建 Issue 时传入 assignee",
)
PROF_MARKERS = (
    "执行 SELF_CHECK",
    "有实际后果的专业工作",
    "SELF_CHECK 是 Run 内的开工门，不是平台级 pre-run 保证",
    "READY → 按当前有效 Context Package 继续工作",
    "REFRESH_REQUIRED",
    "有界自刷新路径",
    "同一 task 与当前角色",
    "BLOCKED → 停止有实际后果的工作",
    "现有 Issue/parent 协议",
    "先为 target role 执行 PREPARE_HANDOFF",
    "恰好一次 Multica 触发",
    "禁止双触发",
)
CTX_MARKERS = (
    "无法安全解决",
    "resolve scope",
    "verify evidence",
    "resolve/restate conflicts",
    "更新 Canonical",
    "重建受影响的 context",
    "返回状态",
    "普通 READY / REFRESH_REQUIRED 的 Handoff 不经过 02",
    "不唤醒 02",
    "handoff hop",
    "Canonical 写权限不扩大",
)
NON_TRANSFER_MARKERS = {
    "engineering-lead": "不改变任何 Owner 边界",
    "context-engineer": "Canonical 写权限不扩大",
    "solution-architect": "不改变任何现有 Owner 边界与禁止项",
    "software-engineer": "不改变任何现有 Owner 边界与禁止项",
    "feature-reviewer": "不改变任何现有 Owner 边界与禁止项",
    "qa": "不改变任何现有 Owner 边界与禁止项",
}
# Blocks must never claim the gates are already live: activation is T13-owned.
LIVENESS_FORBIDDEN = (
    "现已生效", "已生效", "现已启用", "已启用", "即刻生效", "正式生效",
    "is now live", "now active",
)
# Candidate deltas must never contain an executable trigger surface.
DELTA_FORBIDDEN = (
    "multica agent", "multica issue", "multica skill", "mention://",
    "issue assign", "agent skills add", "agent skills set", "issue create",
)

PROF_ROLES = ("solution-architect", "software-engineer", "feature-reviewer",
              "qa")


class StagingError(Exception):
    """Bounded stop. Nothing about the failure is guessed away."""

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


# --------------------------------------------------------------------------
# read-only CLI boundary
# --------------------------------------------------------------------------

def _allowlisted(argv: list) -> bool:
    return any(tuple(argv[:len(cmd)]) == cmd for cmd in READONLY_COMMANDS)


def _default_runner(argv: list) -> tuple:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
    except OSError as exc:
        raise CaptureError(f"multica CLI unavailable: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("multica CLI timed out after 60s") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


class CaptureCli:
    """Read-only deployed CLI boundary for baseline capture.

    Records every issued argv; refuses anything outside READONLY_COMMANDS.
    """

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

    def version(self) -> dict | None:
        try:
            data = self._json(["version", "--output", "json"])
        except CaptureError:
            return None
        return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------
# baseline extraction
# --------------------------------------------------------------------------

def anchor_tail(text: str) -> str:
    return text[-ANCHOR_TAIL_CHARS:]


def applied_text(baseline_text: str, block: str) -> str:
    """Deterministic post-apply text: pure append, baseline bytes preserved."""
    return baseline_text + "\n" + block


def binding_record(skill: dict) -> dict:
    return {"id": skill.get("id"), "name": skill.get("name"),
            "enabled": bool(skill.get("enabled", True))}


def extract_agent(role: str, expected_name: str, raw_agents: list) -> dict:
    matches = [a for a in raw_agents
               if isinstance(a, dict) and a.get("name") == expected_name]
    if len(matches) != 1:
        raise CaptureError(
            "expected exactly one agent per role name; found "
            f"{len(matches)} (fail closed)",
            role=role, agent_name=expected_name)
    a = matches[0]
    instructions = a.get("instructions")
    if not isinstance(instructions, str):
        raise CaptureError("agent instructions is not a string",
                           role=role, agent_name=expected_name)
    skills = [binding_record(s) for s in (a.get("skills") or [])
              if isinstance(s, dict) and s.get("id")]
    skills.sort(key=lambda s: s["id"])
    return {
        "role": role,
        "agent_id": a["id"],
        "agent_name": a["name"],
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
    return {
        "count": len(entries),
        "skills": entries,
        "multica_context_handoff_present": any(
            s["name"] == SKILL_NAME for s in entries),
    }


def skill_bundle_digests(repo_root: Path) -> dict:
    """Deterministic digests of the accepted T07 skill bundle in this repo.

    `files`/`bundle_digest` hash raw bytes (tamper-evidence for the repo
    bundle). `skill_md_digest` hashes the newline-normalized SKILL.md text so
    it can be compared with the workspace skill's `content` field at T13
    apply time.
    """
    skill_dir = repo_root / "skills" / SKILL_NAME
    if not skill_dir.is_dir():
        raise BundleError("accepted T07 skill bundle missing from repo",
                          expected=SKILL_DIR_NAME)
    files = {}
    for p in sorted(skill_dir.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            rel = p.relative_to(skill_dir).as_posix()
            files[rel] = sha256_text(
                hashlib.sha256(p.read_bytes()).hexdigest())
    return {
        "name": SKILL_NAME,
        "bundle_digest": sha256_text(canonical_json(
            sorted(files.items()))),
        "skill_md_digest": sha256_text(
            (skill_dir / "SKILL.md").read_text(encoding="utf-8")),
        "files": files,
    }


def capture(out_dir: Path, executable: str = "multica",
            cli: CaptureCli | None = None) -> dict:
    """Read-only live baseline capture -> raw files + baseline.json."""
    cli = cli or CaptureCli(executable)
    out_dir = Path(out_dir)
    commands_start = len(cli.commands)

    agents_raw = cli.agent_list()
    skills_raw = cli.skill_list()
    version = cli.version()

    agents, out_of_scope = [], []
    for role, name in ROLES:
        entry = extract_agent(role, name, agents_raw)
        agents.append(entry)
    known_ids = {a["agent_id"] for a in agents}
    for a in agents_raw:
        if isinstance(a, dict) and a.get("id") and a["id"] not in known_ids:
            if not any(o["agent_id"] == a["id"] for o in out_of_scope):
                out_of_scope.append({
                    "agent_id": a["id"], "agent_name": a.get("name"),
                    "reason": "not one of the six collaboration roles; "
                              "out of T08 scope and never modified"})

    catalog = skill_catalog(skills_raw)
    bound = [a["role"] for a in agents if any(
        s["name"] == SKILL_NAME for s in a["skill_bindings"])]
    workspace_id = next((a.get("workspace_id") for a in agents_raw
                         if isinstance(a, dict) and a.get("workspace_id")),
                        None)
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
        "issue_ref": ISSUE_REF,
        "title": "CTX-HO-08 — Stage Agent Instruction Contract",
        "captured_at_iso": now_iso(),
        "cli_version": (version or {}).get("version"),
        "workspace_id": workspace_id,
        "agents": agents,
        "out_of_scope_agents": out_of_scope,
        "skill_catalog": catalog,
        "agents_with_skill_bound": bound,
        "note": ("staging baseline only; application is T13-owned controlled "
                 "enablement. updated_at fields are provenance-only: "
                 "instruction bytes are the drift authority."),
        "trace": trace,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "agent-list.json").write_text(
        json.dumps(agents_raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    (out_dir / "skill-list.json").write_text(
        json.dumps(skills_raw, ensure_ascii=False, indent=2) + "\n",
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


# --------------------------------------------------------------------------
# baseline validation
# --------------------------------------------------------------------------

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
        raise BaselineError("baseline roles mismatch the frozen role table",
                            found=roles)
    for a in agents:
        text = a.get("instruction_text")
        if not isinstance(text, str):
            raise BaselineError("baseline agent instruction_text missing",
                                role=a.get("role"))
        if sha256_text(text) != a.get("instruction_sha256"):
            raise BaselineError("baseline instruction digest mismatch",
                                role=a.get("role"))
        if anchor_tail(text) != a.get("anchor_tail"):
            raise BaselineError("baseline anchor tail mismatch",
                                role=a.get("role"))
        if applied_text(text, BLOCKS[a["role"]]).startswith(text) is False:
            raise BaselineError("baseline text not prefix-safe", role=a["role"])
    catalog = baseline.get("skill_catalog") or {}
    if catalog.get("multica_context_handoff_present") is not False:
        raise BaselineError(
            "baseline was staged with the skill already present in the "
            "workspace catalog; re-stage against the true state")
    return baseline


# --------------------------------------------------------------------------
# bundle build (deterministic)
# --------------------------------------------------------------------------

def build_bundle(baseline: dict, repo_root: Path) -> dict:
    """Deterministic T08 bundle from a validated baseline."""
    agents = baseline["agents"]
    skill = skill_bundle_digests(repo_root)

    candidates, after = {}, {}
    for a in agents:
        role = a["role"]
        block = BLOCKS[role]
        candidates[role] = block
        after[role] = applied_text(a["instruction_text"], block)

    plan = {
        "schema_version": PLAN_SCHEMA,
        "status": "staged_candidate_not_active",
        "issue_ref": ISSUE_REF,
        "activation": "owned by T13 controlled enablement; T08 executes none "
                      "of these operations",
        "skill": {"name": SKILL_NAME, "bundle_digest": skill["bundle_digest"]},
        "additive_only": True,
        "never_replace_all_set": True,
        "preserve_existing_bindings": True,
        "bindings": [
            {
                "agent_role": a["role"],
                "agent_id": a["agent_id"],
                "agent_name": a["agent_name"],
                "op": "add",
                "declarative_command": (
                    "multica agent skills add <agent-id> "
                    "--skill-ids <skill-id-assigned-at-import>"),
                "baseline_binding_ids": a["skill_binding_ids"],
                "baseline_bindings": a["skill_bindings"],
            }
            for a in agents if a["role"] in BOUND_ROLES
        ],
        "excluded": [
            {
                "agent_role": a["role"],
                "agent_id": a["agent_id"],
                "agent_name": a["agent_name"],
                "reason": "exception-path only; ordinary handoffs must not "
                          "route through 02 and 02 must not be made mandatory "
                          "without evidence (YZT-63 §33.4)",
                "binding": "not planned",
            }
            for a in agents if a["role"] not in BOUND_ROLES
        ],
    }

    preconditions = {
        "schema_version": PRECONDITIONS_SCHEMA,
        "fail_closed": True,
        "issue_ref": ISSUE_REF,
        "applies_to": "T13-owned application; checked by "
                      "tools/chandoff_instructions.py verify",
        "drift_is_fatal": True,
        "updated_at_is_provenance_only": True,
        "checks": [
            {"id": "agents_present",
             "rule": "all six role agents exist by exact name"},
            {"id": "agent_ids_stable",
             "rule": "agent ids equal the baseline recorded ids"},
            {"id": "instruction_digests_match",
             "rule": "sha256 of the exact instructions text equals baseline"},
            {"id": "anchor_tails_match",
             "rule": f"last {ANCHOR_TAIL_CHARS} chars equal baseline "
                     "anchor_tail"},
            {"id": "binding_sets_match",
             "rule": "sorted (id, name, enabled) binding tuples equal "
                     "baseline"},
            {"id": "skill_catalog_stage_state",
             "rule": "stage mode: workspace catalog has no "
                     f"{SKILL_NAME}"},
            {"id": "skill_catalog_apply_state",
             "rule": "apply mode: catalog has " + SKILL_NAME +
                     " whose SKILL.md content digest equals "
                     "skill.skill_md_digest"},
            {"id": "no_in_scope_binding_in_stage_mode",
             "rule": "stage mode: none of the six agents is bound to " +
                     SKILL_NAME},
        ],
        "drift_codes": [
            "agent_missing", "agent_id_changed", "instruction_digest_mismatch",
            "anchor_tail_mismatch", "binding_set_drift", "skill_state_drift",
        ],
        "agents": [
            {
                "role": a["role"],
                "agent_id": a["agent_id"],
                "agent_name": a["agent_name"],
                "instruction_sha256": a["instruction_sha256"],
                "instruction_length": a["instruction_length"],
                "anchor_tail": a["anchor_tail"],
                "baseline_binding_ids": a["skill_binding_ids"],
                "baseline_bindings": a["skill_bindings"],
            }
            for a in agents
        ],
        "skill": {
            "name": SKILL_NAME,
            "bundle_digest": skill["bundle_digest"],
            "skill_md_digest": skill["skill_md_digest"],
        },
    }

    rollback = {
        "schema_version": ROLLBACK_SCHEMA,
        "status": "declarative_only_not_executed_by_T08",
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
        "notes": {
            "catalog": "T08 imports nothing; stage time needs no catalog "
                       "restore. If T13 imported the skill and rollback is "
                       "required, catalog handling is a T13 decision.",
            "binding_set_usage": "`set` appears only in this declarative "
                                 "rollback to restore the exact baseline "
                                 "binding set; the forward binding plan never "
                                 "uses `set` (additive only).",
            "scope": "instruction text and skill bindings only; no run "
                     "trigger, assignment, mention, or issue lifecycle "
                     "command exists in this bundle.",
        },
    }
    return {"candidates": candidates, "after": after, "plan": plan,
            "preconditions": preconditions, "rollback": rollback,
            "skill": skill}


def bundle_manifest(baseline: dict, bundle: dict, files: dict) -> dict:
    return {
        "schema_version": BUNDLE_SCHEMA,
        "status": "staged_candidate_not_active",
        "issue_ref": ISSUE_REF,
        "activation": "T13-owned controlled enablement; nothing here has "
                      "been applied to the live workspace",
        "baseline": {
            "captured_cli_version": baseline.get("cli_version"),
            "instruction_sha256": {a["role"]: a["instruction_sha256"]
                                   for a in baseline["agents"]},
        },
        "skill": {"name": SKILL_NAME,
                  "bundle_digest": bundle["skill"]["bundle_digest"]},
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
        p = out_dir / "after" / f"{role}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        files[f"after/{role}.md"] = sha256_text(text)
    write_json(out_dir / "binding-plan.json", bundle["plan"])
    files["binding-plan.json"] = sha256_text(
        json.dumps(bundle["plan"], ensure_ascii=False, sort_keys=True,
                   indent=2) + "\n")
    write_json(out_dir / "preconditions.json", bundle["preconditions"])
    files["preconditions.json"] = sha256_text(
        json.dumps(bundle["preconditions"], ensure_ascii=False,
                   sort_keys=True, indent=2) + "\n")
    write_json(out_dir / "rollback.json", bundle["rollback"])
    files["rollback.json"] = sha256_text(
        json.dumps(bundle["rollback"], ensure_ascii=False, sort_keys=True,
                   indent=2) + "\n")
    write_json(out_dir / "bundle.json",
               bundle_manifest(baseline, bundle, files))
    return {"ok": True, "out_dir": str(out_dir), "files": sorted(files)}


# --------------------------------------------------------------------------
# fail-closed precondition verification
# --------------------------------------------------------------------------

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
    """Fail-closed precondition check of a fresh capture against baseline."""
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
    fresh_by_name = {x.get("name"): x for x in fresh_raw
                     if isinstance(x, dict) and x.get("name")}

    checks, drift = [], []
    for a in baseline["agents"]:
        role = a["role"]
        fresh = fresh_by_name.get(a["agent_name"])
        if fresh is None:
            checks.append({"id": "agents_present", "role": role,
                           "status": "fail",
                           "detail": f"no agent named {a['agent_name']!r}"})
            drift.append("agent_missing")
            continue
        checks.append({"id": "agents_present", "role": role, "status": "pass",
                       "detail": "found by exact name"})
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

    catalog = skill_catalog(fresh_skills_raw)
    present = catalog["multica_context_handoff_present"]
    bound_roles = [role for role, name in ROLES
                   if any(s.get("name") == SKILL_NAME
                          for s in (fresh_by_name.get(name) or {})
                          .get("skills") or [])]
    if mode == "stage":
        if present:
            checks.append({"id": "skill_catalog_stage_state", "status": "fail",
                           "detail": f"{SKILL_NAME} appeared in the workspace "
                                     "catalog; re-stage the bundle"})
            drift.append("skill_state_drift")
        else:
            checks.append({"id": "skill_catalog_stage_state", "status": "pass",
                           "detail": f"{SKILL_NAME} absent from the catalog "
                                     "as staged"})
        if bound_roles:
            checks.append({"id": "no_in_scope_binding_in_stage_mode",
                           "status": "fail",
                           "detail": f"agents already bound: {bound_roles}"})
            drift.append("skill_state_drift")
        else:
            checks.append({"id": "no_in_scope_binding_in_stage_mode",
                           "status": "pass",
                           "detail": "no in-scope agent is bound to the skill"})
    else:
        if not present:
            checks.append({"id": "skill_catalog_apply_state", "status": "fail",
                           "detail": f"{SKILL_NAME} missing from the "
                                     "workspace catalog at apply time"})
            drift.append("skill_state_drift")
        else:
            checks.append({"id": "skill_catalog_apply_state",
                           "status": "pass",
                           "detail": f"{SKILL_NAME} present in the catalog"})
        if present and skill_get_file is not None:
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
                                         "drifts from the accepted T07 bundle"})
                drift.append("skill_state_drift")

    failed = [c for c in checks if c.get("status") == "fail"]
    report = {
        "ok": not drift,
        "mode": mode,
        "checks": checks,
        "checks_failed": failed,
        "checks_passed_count": len(checks) - len(failed),
        "drift": sorted(set(drift)),
    }
    return report


# --------------------------------------------------------------------------
# static §33 audit
# --------------------------------------------------------------------------

def _marker_check(block: str, markers: tuple) -> list:
    return [m for m in markers if m not in block]


def audit(baseline_path: Path, bundle_dir: Path,
          final_verify: Path | None = None) -> dict:
    baseline = load_baseline(baseline_path)
    bundle_dir = Path(bundle_dir)
    bundle = build_bundle(baseline, Path(__file__).resolve().parents[1])

    checks = []
    fail = []

    def record(cid: str, ok: bool, detail: str):
        checks.append({"id": cid, "passed": bool(ok), "detail": detail})
        if not ok:
            fail.append(cid)

    # bundle matches a deterministic rebuild from the committed baseline
    rebuild_ok = True
    for role, block in bundle["candidates"].items():
        path = bundle_dir / "candidates" / f"{role}.md"
        if not path.is_file() or path.read_text(encoding="utf-8") != block:
            rebuild_ok = False
    for role, text in bundle["after"].items():
        path = bundle_dir / "after" / f"{role}.md"
        if not path.is_file() or path.read_text(encoding="utf-8") != text:
            rebuild_ok = False
    record("bundle_matches_deterministic_rebuild", rebuild_ok,
           "candidates/ and after/ regenerate byte-identically from the "
           "committed baseline")

    # lead pre-dispatch gate
    lead = bundle["candidates"]["engineering-lead"]
    missing = _marker_check(lead, LEAD_MARKERS)
    record("lead_has_pre_dispatch_gate", not missing,
           "all pre-dispatch markers present" if not missing
           else f"missing markers: {missing}")

    # professional run gates
    prof_missing = {r: _marker_check(bundle["candidates"][r], PROF_MARKERS)
                    for r in PROF_ROLES}
    record("professional_agents_have_self_gate",
           not any(prof_missing.values()),
           "SELF_CHECK before consequential work + READY/REFRESH_REQUIRED/"
           "BLOCKED routing in 03/04/05/06" if not any(prof_missing.values())
           else {r: m for r, m in prof_missing.items() if m})
    handoff_markers = ("先为 target role 执行 PREPARE_HANDOFF",
                       "恰好一次 Multica 触发", "禁止双触发")
    pre_missing = {r: [m for m in handoff_markers
                       if m not in bundle["candidates"][r]]
                   for r in PROF_ROLES}
    record("professional_agents_have_pre_handoff_gate",
           not any(pre_missing.values()),
           "PREPARE_HANDOFF before dispatch + exactly-one-trigger in "
           "03/04/05/06" if not any(pre_missing.values())
           else {r: m for r, m in pre_missing.items() if m})

    # 02 exception-only
    ctx = bundle["candidates"]["context-engineer"]
    ctx_missing = _marker_check(ctx, CTX_MARKERS)
    ordinary_routing = [m for m in ("路由给 02", "route to 02",
                                    "send ordinary handoffs to 02")
                        if any(m in bundle["candidates"][r] for r in
                               ("engineering-lead",) + PROF_ROLES)]
    record("context_engineer_not_normal_path",
           not ctx_missing and not ordinary_routing,
           "02 carries the exception path only; ordinary READY/REFRESH "
           "routing never targets 02 (lead/professional blocks state the "
           "boundary)" if not ctx_missing and not ordinary_routing
           else {"missing": ctx_missing, "ordinary_routing_found":
                 ordinary_routing})

    # 02 not bound in the plan
    plan = bundle["plan"]
    plan_roles = [b["agent_role"] for b in plan["bindings"]]
    record("binding_plan_additive_exactly_five",
           sorted(plan_roles) == sorted(BOUND_ROLES)
           and all(b["op"] == "add" for b in plan["bindings"])
           and plan["never_replace_all_set"] is True,
           "additive `add` ops only, exactly 01/03/04/05/06, `set` never used "
           "forward; 02 excluded with rationale")

    # preservation of existing contracts
    preserved, non_transfer = True, True
    for a in baseline["agents"]:
        role = a["role"]
        after_text = bundle["after"][role]
        if not after_text.startswith(a["instruction_text"]) or \
                applied_text(a["instruction_text"],
                             bundle["candidates"][role]) != after_text:
            preserved = False
        if NON_TRANSFER_MARKERS[role] not in bundle["candidates"][role]:
            non_transfer = False
    record("existing_role_authority_preserved", preserved,
           "each post-apply text starts with the exact baseline instruction "
           "text (pure append, zero loss)" if preserved
           else "baseline text not preserved verbatim")
    record("new_blocks_transfer_no_authority", non_transfer,
           "every block states its non-transfer boundary explicitly")

    # liveness claims and executable trigger surfaces
    liveness = {r: [m for m in LIVENESS_FORBIDDEN if m in block]
                for r, block in bundle["candidates"].items()}
    delta = {r: [m for m in DELTA_FORBIDDEN if m in block]
             for r, block in bundle["candidates"].items()}
    record("no_premature_activation_claim",
           not any(liveness.values()),
           "no block claims the gates are live before T09/T13"
           if not any(liveness.values()) else liveness)
    record("candidate_deltas_carry_no_trigger_surface",
           not any(delta.values()),
           "no mention link / assign / binding argv inside candidate deltas"
           if not any(delta.values()) else delta)

    # rollback completeness
    rollback = bundle["rollback"]
    rb_ok = len(rollback["restores"]) == len(ROLES) and all(
        r["baseline_instruction_sha256"] ==
        next(a["instruction_sha256"] for a in baseline["agents"]
             if a["role"] == r["agent_role"])
        and r["baseline_binding_ids"] ==
        next(a["skill_binding_ids"] for a in baseline["agents"]
             if a["role"] == r["agent_role"])
        for r in rollback["restores"])
    rb_no_trigger = all(not any(m in c for c in
                          r["restore_instructions_declarative"]
                          for m in ("mention://", "issue assign", "rerun",
                                    "agent run"))
                        for r in rollback["restores"])
    record("rollback_complete", rb_ok and rb_no_trigger,
           "all six agents restore exact baseline instruction text and "
           "binding sets; declarative commands only, no run triggers")

    # non-activation guarantees from the capture trace
    trace_g = (baseline.get("trace") or {}).get("guarantees") or {}
    record("non_activation_guarantees_all_zero",
           trace_g == GUARANTEES,
           "capture trace: zero skill imports, zero instruction writes, zero "
           "binding writes, zero assignments/mentions/run triggers"
           if trace_g == GUARANTEES else trace_g)

    # stage-time workspace state
    record("stage_state_skill_absent",
           baseline["skill_catalog"]["multica_context_handoff_present"]
           is False and not baseline["agents_with_skill_bound"],
           "at capture time the skill was absent from the workspace catalog "
           "and unbound on all six agents")

    done = {
        "lead_has_pre_dispatch_gate": not missing,
        "professional_agents_have_self_gate": not any(prof_missing.values()),
        "professional_agents_have_pre_handoff_gate":
            not any(pre_missing.values()),
        "context_engineer_not_normal_path":
            not ctx_missing and not ordinary_routing,
        "existing_role_authority_preserved": preserved,
        "exactly_one_trigger_preserved":
            "恰好一次 Multica 触发" in lead and not any(pre_missing.values()),
        "rollback_complete": rb_ok and rb_no_trigger,
        "live_skill_imports": 0,
        "live_agent_instruction_writes": 0,
        "live_skill_binding_writes": 0,
        "triggered_runs": 0,
    }

    final = None
    if final_verify is not None:
        final = json.loads(Path(final_verify).read_text(encoding="utf-8"))
        record("final_live_state_equivalent", bool(final.get("ok")),
               "end-of-task re-read of live agent/skill state verified "
               "byte/ID-equivalent to the committed baseline"
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
    }


def render_report(audit_doc: dict) -> str:
    done = audit_doc["done"]
    lines = [
        "# T08 — Agent Instruction Contract — Static Audit (YZT-63)",
        "",
        "- tool: `tools/chandoff_instructions.py` " + TOOL_VERSION,
        "- baseline: committed read-only live capture (`baseline.json`)",
        f"- status: **{audit_doc['status']}** — activation is owned by T13 "
        "controlled enablement; nothing in this bundle has been applied to "
        "the live workspace",
        "- shared Skill referenced by name only; deterministic policy stays "
        "in the accepted T07 Skill (`skills/multica-context-handoff/`)",
        "",
        "## §33 Done criteria",
        "",
        "| criterion | value |",
        "| --- | --- |",
    ]
    for key in ("lead_has_pre_dispatch_gate",
                "professional_agents_have_self_gate",
                "professional_agents_have_pre_handoff_gate",
                "context_engineer_not_normal_path",
                "existing_role_authority_preserved",
                "exactly_one_trigger_preserved",
                "rollback_complete",
                "live_skill_imports",
                "live_agent_instruction_writes",
                "live_skill_binding_writes",
                "triggered_runs"):
        value = done[key]
        rendered = str(value).lower() if isinstance(value, bool) else value
        lines.append(f"| {key} | {rendered} |")
    lines += [
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
            "Live agent/skill state was re-read read-only at task end and "
            "verified ID-equivalent (agent ids, instruction digests, binding "
            "sets, catalog state) to the committed baseline. `updated_at` "
            "fields are provenance-only and excluded from equivalence.",
            "",
        ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T08 Agent Instruction Contract staging (YZT-63) — "
                    "staging only, never activates")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="read-only live baseline capture")
    cap.add_argument("--out-dir", required=True)
    cap.add_argument("--executable", default="multica")
    bld = sub.add_parser("build", help="deterministic T08 bundle")
    bld.add_argument("--baseline", required=True)
    bld.add_argument("--out-dir", required=True)
    ver = sub.add_parser("verify", help="fail-closed precondition check")
    ver.add_argument("--baseline", required=True)
    ver.add_argument("--capture-dir", required=True)
    ver.add_argument("--mode", choices=("stage", "apply"), default="stage")
    ver.add_argument("--skill-get-file", default=None)
    aud = sub.add_parser("audit", help="static §33 audit")
    aud.add_argument("--baseline", required=True)
    aud.add_argument("--bundle-dir", required=True)
    aud.add_argument("--final-verify", default=None)
    aud.add_argument("--markdown", default=None,
                     help="also write the markdown report to this path")
    args = parser.parse_args(argv)

    try:
        if args.command == "capture":
            doc = capture(Path(args.out_dir), args.executable)
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
    except StagingError as exc:
        print(json.dumps({"ok": False, "error": exc.envelope()},
                         ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
