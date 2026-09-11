#!/usr/bin/env python3
"""Shared synthetic Findings-source fixture for the T07 pipeline tests.

Builds one isolated temp root, a fully valid `findings-source-binding/1`
document, its captured authority record, and one PREPARE-bound observation.
Test namespaces get these paths injected by default so the production
pipeline stages exercise the strict bound path with a real read-only
transport; tests that inject an in-memory store take the recorded
simulation seam instead.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff_findings_source as findings_source  # noqa: E402

TASK_REF = "multica://issue/YZT-58"
ROLE = "software-engineer"
PROJECT = "web-imagegen"

_FIXTURE: dict | None = None


def _write(path: Path, doc) -> Path:
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def fixture() -> dict:
    global _FIXTURE
    if _FIXTURE is not None:
        return _FIXTURE
    base = Path(tempfile.mkdtemp(prefix="t07-findings-fixture-"))
    root = base / "findings"
    root.mkdir()
    content = "T07 shared pipeline simulation authority record"
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    binding = findings_source.make_binding(
        source_id="findings-t07-simulation",
        project_id=PROJECT,
        root=str(root),
        authority={
            "comment_id": "01a0-sim-comment", "issue_id": "01a0-sim-issue",
            "author_id": "01a0-sim-author", "author_type": "agent",
            "digest": digest,
        },
        allowed={
            "task_refs": [TASK_REF, "multica://issue/YZT-99"],
            "roles": ["software-engineer", "context-engineer",
                      "engineering-lead", "solution-architect",
                      "delivery-reviewer", "qa"],
        },
        runtime={"commit": "0" * 40, "adapter_digest": "sha256:" + "0" * 64},
        created_at="2026-09-11T00:00:00Z",
    )
    binding_path = _write(base / "binding.json", binding)
    authority_path = _write(base / "authority.json", {
        "comment_id": binding["authority"]["comment_id"],
        "issue_id": binding["authority"]["issue_id"],
        "author_id": binding["authority"]["author_id"],
        "author_type": binding["authority"]["author_type"],
        "content": content,
    })
    source = findings_source.BoundFindingsSource(
        binding, resolver=lambda authority: json.loads(
            authority_path.read_text(encoding="utf-8")),
        project_id=PROJECT, expected_commit="0" * 40,
        expected_adapter_digest="sha256:" + "0" * 64)
    snapshot = source.read(boundary="PREPARE", task_ref=TASK_REF, role=ROLE)
    evidence_path = _write(base / "evidence.json", snapshot["observation"])
    _FIXTURE = {
        "dir": base,
        "root": root,
        "binding_file": binding_path,
        "authority_file": authority_path,
        "evidence_file": evidence_path,
        "binding": binding,
    }
    return _FIXTURE


def findings_args() -> dict:
    f = fixture()
    return {
        "findings_source_binding_file": str(f["binding_file"]),
        "findings_authority_file": str(f["authority_file"]),
        "findings_evidence_file": str(f["evidence_file"]),
        "findings_source_expect_commit": "0" * 40,
        "findings_source_expect_adapter_digest": "sha256:" + "0" * 64,
        "observer_run_id": "sim-observer-1",
    }
