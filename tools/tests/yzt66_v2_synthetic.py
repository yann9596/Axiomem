#!/usr/bin/env python3
"""YZT-88 V2 dedicated synthetic Findings fixture constructor.

Builds a run-local fixture root for source_id `yzt66-v2-synthetic-findings`
(and an empty-store sibling). Every object is marked SYNTHETIC. No live
Finding bytes are copied. Fake authority is restricted to this constructor.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff_findings_source as cfs  # noqa: E402

PROJECT = "web-imagegen"
TASK_REF = "multica://issue/YZT-88"
TASK_ID = "YZT-88"
ROLE = "software-engineer"
OPEN_FINDING_ID = "FIND-WIMG-YZT66-000001"
AUTHORITY_TEXT = (
    "SYNTHETIC Lead disposition: accept yzt66-v2-synthetic-findings "
    "(not production Owner attestation)"
)
AUTHORITY_DIGEST = "sha256:" + hashlib.sha256(
    AUTHORITY_TEXT.encode("utf-8")).hexdigest()
RUNTIME = {"commit": "0" * 40, "adapter_digest": "sha256:" + "0" * 64}


def synthetic_open_finding():
    return {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": OPEN_FINDING_ID,
        "project_id": PROJECT,
        "task_id": TASK_ID,
        "summary": "SYNTHETIC relevant open Finding for YZT-88 V2 tests",
        "detail": "SYNTHETIC. Do not treat as production Owner attestation.",
        "intent": "observation",
        "source_refs": [],
        "discovered_by": ROLE,
        "status": "open",
        "verification": "verified",
        "created_at": "2026-09-12T00:00:00Z",
    }


def synthetic_blocking_finding(*, task_id=TASK_ID):
    """Material unresolved Finding. Distinct from the visible observation."""
    return {
        "schema_version": "1.1",
        "kind": "finding",
        "finding_id": "FIND-WIMG-YZT66-000002",
        "project_id": PROJECT,
        "task_id": task_id,
        "summary": "SYNTHETIC unresolved durable candidate on the handoff task",
        "detail": "SYNTHETIC blocking conflict. Do not auto-process.",
        "intent": "durable_candidate",
        "source_refs": ["repo://web-imagegen@main/README.md"],
        "discovered_by": ROLE,
        "status": "open",
        "verification": "unverified",
        "created_at": "2026-09-12T00:00:00Z",
    }


def _write_json(path: Path, doc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def _inventory_for(root: Path, source_id: str) -> tuple[list, str]:
    reader = cfs.FilesystemReader()
    snap = cfs.read_snapshot(Path(root), source_id=source_id, reader=reader,
                             boundary="FIXTURE_BUILD")
    return copy.deepcopy(snap["inventory"]), snap["snapshot_digest"]


def build_v2_fixture(base: Path | None = None) -> dict:
    """Create the dedicated fixture root and trusted map. SYNTHETIC only."""
    if base is None:
        base = Path(tempfile.mkdtemp(prefix="yzt66-v2-synthetic-"))
    else:
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
    open_root = (base / "open").resolve()
    empty_root = (base / "empty").resolve()
    open_root.mkdir(parents=True, exist_ok=True)
    empty_root.mkdir(parents=True, exist_ok=True)
    _write_json(open_root / f"{OPEN_FINDING_ID}.json", synthetic_open_finding())
    open_inventory, open_digest = _inventory_for(
        open_root, cfs.V2_SYNTHETIC_SOURCE_ID)
    empty_inventory, empty_digest = _inventory_for(
        empty_root, cfs.V2_SYNTHETIC_EMPTY_SOURCE_ID)
    allowed = {"task_refs": [TASK_REF], "roles": [ROLE]}
    trusted = {
        "schema": cfs.TRUSTED_SOURCE_SCHEMA,
        "project_id": PROJECT,
        "synthetic": True,
        "marks": ["SYNTHETIC"],
        "scope": {
            "task_ref": TASK_REF,
            "role": ROLE,
            "project_id": PROJECT,
            "issue": "YZT-88",
        },
        "sources": {
            cfs.V2_SYNTHETIC_SOURCE_ID: {
                "root": str(open_root.resolve()),
                "allowed": copy.deepcopy(allowed),
                "inventory": open_inventory,
                "snapshot_digest": open_digest,
                "marks": ["SYNTHETIC"],
            },
            cfs.V2_SYNTHETIC_EMPTY_SOURCE_ID: {
                "root": str(empty_root.resolve()),
                "allowed": copy.deepcopy(allowed),
                "inventory": empty_inventory,
                "snapshot_digest": empty_digest,
                "marks": ["SYNTHETIC"],
            },
        },
    }
    trusted_path = _write_json(base / "trusted-map.json", trusted)

    def binding_for(source_id: str, root: Path) -> dict:
        return cfs.make_binding(
            source_id=source_id, project_id=PROJECT, root=str(root.resolve()),
            authority={
                "comment_id": "01a0-synth-comment",
                "issue_id": "01a0-synth-issue",
                "author_id": "01a0-synth-author",
                "author_type": "agent",
                "digest": AUTHORITY_DIGEST,
            },
            allowed=copy.deepcopy(allowed),
            runtime=dict(RUNTIME),
            created_at="2026-09-12T00:00:00Z",
        )

    open_binding = binding_for(cfs.V2_SYNTHETIC_SOURCE_ID, open_root)
    empty_binding = binding_for(cfs.V2_SYNTHETIC_EMPTY_SOURCE_ID, empty_root)
    return {
        "dir": base,
        "open_root": open_root.resolve(),
        "empty_root": empty_root.resolve(),
        "trusted": trusted,
        "trusted_map_file": trusted_path,
        "open_binding": open_binding,
        "empty_binding": empty_binding,
        "open_binding_file": _write_json(base / "open-binding.json",
                                         open_binding),
        "empty_binding_file": _write_json(base / "empty-binding.json",
                                          empty_binding),
        "authority_text": AUTHORITY_TEXT,
        "authority_digest": AUTHORITY_DIGEST,
        "open_inventory": open_inventory,
        "open_snapshot_digest": open_digest,
        "empty_inventory": empty_inventory,
        "empty_snapshot_digest": empty_digest,
        "task_ref": TASK_REF,
        "role": ROLE,
        "project_id": PROJECT,
        "open_finding_id": OPEN_FINDING_ID,
        "marks": ["SYNTHETIC"],
    }


class SyntheticAuthorityCli:
    """Fake transport for this constructor only. Not production authority."""

    explicit_simulation_entry = True
    simulation_transport = True

    def __init__(self, content=AUTHORITY_TEXT, **fields):
        self.record = {
            "id": "01a0-synth-comment",
            "issue_id": "01a0-synth-issue",
            "author_id": "01a0-synth-author",
            "author_type": "agent",
            "content": content,
        }
        self.record.update(fields)
        self.calls: list = []

    def comment_thread(self, issue_id, comment_id):
        self.calls.append((issue_id, comment_id))
        rec = self.record
        if rec.get("id") != comment_id:
            return []
        if rec.get("issue_id") not in (None, issue_id):
            return []
        return [dict(rec)]

    def revoke(self):
        self.record = {}

    def set_content(self, content: str):
        self.record["content"] = content
