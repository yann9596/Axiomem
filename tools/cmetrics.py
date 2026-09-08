#!/usr/bin/env python3
"""Gate C computed replay metrics (YZT-42).

false_canonical / false_forget / issue_noise are derived from expectation,
actual package refs, and explicit replay-evidence files. Missing evidence
does not default to 0.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from cutil import ROOT, doc_at
from schema_mini import Schema, load_schema_file


EVIDENCE_DIR = ROOT / "migration" / "replay-evidence"
LOCK_PATH = ROOT / "migration" / "replay-expectation.lock.yaml"


def load_metric_evidence(task_id: str, root: Path | None = None) -> tuple[dict | None, list[str]]:
    base = root or ROOT
    path = base / "migration" / "replay-evidence" / f"{task_id}.yaml"
    if not path.exists():
        return None, [f"migration/replay-evidence/{task_id}.yaml"]
    doc = doc_at(path)
    schema = load_schema_file("replay-metric-evidence.schema.json")
    errors = Schema(schema, schema).validate(doc, path=f"replay-evidence:{task_id}")
    if errors:
        return None, errors
    if doc.get("task_id") != task_id:
        return None, [f"replay-evidence task_id {doc.get('task_id')!r} != {task_id!r}"]
    return doc, []


def compute_false_canonical(actual_refs: list[str], evidence: dict) -> list[str]:
    banned = set(evidence.get("false_canonical_refs") or [])
    return [r for r in actual_refs if r in banned]


def compute_false_forget(actual_refs: list[str], evidence: dict) -> list[str]:
    retain = evidence.get("must_retain_refs") or []
    have = set(actual_refs)
    return [r for r in retain if r not in have]


def compute_issue_noise(blocked_by: list[str], evidence: dict) -> list[str]:
    allowed = set(evidence.get("allowed_issue_refs") or [])
    return [ref for ref in blocked_by if ref not in allowed]


def metric_bundle(task_id: str, actual_refs: list[str], blocked_by: list[str],
                  root: Path | None = None) -> dict:
    evidence, missing = load_metric_evidence(task_id, root=root)
    if evidence is None:
        return {
            "status": "missing_evidence",
            "false_canonical": None,
            "false_forget": None,
            "issue_noise": None,
            "false_canonical_refs": [],
            "false_forget_refs": [],
            "issue_noise_refs": [],
            "missing_evidence": missing,
        }
    fc = compute_false_canonical(actual_refs, evidence)
    ff = compute_false_forget(actual_refs, evidence)
    noise = compute_issue_noise(blocked_by, evidence)
    return {
        "status": "computed",
        "false_canonical": len(fc),
        "false_forget": len(ff),
        "issue_noise": len(noise),
        "false_canonical_refs": fc,
        "false_forget_refs": ff,
        "issue_noise_refs": noise,
        "missing_evidence": [],
    }


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_expectation_lock(root: Path | None = None) -> tuple[dict | None, list[str]]:
    base = root or ROOT
    path = base / "migration" / "replay-expectation.lock.yaml"
    if not path.exists():
        return None, ["migration/replay-expectation.lock.yaml"]
    doc = doc_at(path)
    schema = load_schema_file("replay-expectation-lock.schema.json")
    errors = Schema(schema, schema).validate(doc, path="replay-expectation-lock")
    if errors:
        return None, errors
    return doc, []


def verify_expectation_lock(root: Path | None = None) -> dict:
    base = root or ROOT
    doc, missing = load_expectation_lock(base)
    if doc is None:
        return {
            "ok": False,
            "status": "missing_evidence",
            "missing_evidence": missing,
            "mismatches": [],
        }
    mismatches = []
    for entry in doc.get("files") or []:
        rel = entry.get("path")
        expected = entry.get("sha256")
        path = base / rel
        if not path.exists():
            mismatches.append({"path": rel, "error": "file missing"})
            continue
        actual = file_sha256(path)
        if actual != expected:
            mismatches.append({"path": rel, "expected": expected, "actual": actual})
    return {
        "ok": not mismatches,
        "status": "computed",
        "missing_evidence": [],
        "mismatches": mismatches,
        "committed_at_revision": doc.get("committed_at_revision"),
    }
