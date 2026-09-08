#!/usr/bin/env python3
"""R8 — Compatibility adapter: translate old V1 calls to V1.1 semantics.

Adapter contract (YZT-40 R8 / Spec §40):
- translates old calls only; produces no V1.0 canonical; writes nothing;
- cannot write old fields into V1.1 (it only reads);
- chain → scope mapping (role baseline / checkpoint / builder policy);
- lifecycle: migration -> cutover compatibility window -> cleanup -> delete.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cutil import ROOT, CHAIN_SCOPE, toks, now_iso  # noqa: E402
from cbuild import resolve_scope, build_package  # noqa: E402
from cdata import load_all_docs  # noqa: E402


def compat_get(task_id: str, role: str, decision: str, chain: str | None,
               query: str = "", limit: int = 10) -> dict:
    stype, pid = CHAIN_SCOPE.get(chain, ("team", None))
    scope = resolve_scope(task_id, stype, [], pid)
    pkg = build_package(task_id, "context-engineer", scope,
                        decision=decision, query=query, limit=limit)
    refs = [r["ref"] for r in pkg["rules"]] + \
        [f["ref"] for f in pkg["current_facts"]]
    return {
        "schema_version": "1.0", "kind": "context_package",
        "task_id": task_id, "role": "context-engineer",
        "decision": decision,
        "known_facts": [f"{r['ref']} — {r['statement']}" for r in pkg["rules"]] +
                       [f"{f['ref']} — {f['statement']}" for f in pkg["current_facts"]],
        "relevant_memory": [{"memory_id": r, "reason": "v1.1 scope-first assembly (compat)"}
                            for r in refs],
        "confidence": None,
        "conflicts": [c["id"] for c in pkg["open_conflicts"]],
        "missing_context": [f"checkpoint: {b}" for b in pkg["blocked_by"]]
                           if not refs else [],
        "memory_candidates": [],
        "generated_at": pkg["generated_at"],
        "compat_note": "translated from V1.1 canonical by the compat adapter; "
                       "old --chain/--tag mapped to V1.1 scope; no canonical writes",
    }


def compat_retrieve(query: str, limit: int = 10) -> dict:
    qt = toks(query)
    results = []
    for doc in load_all_docs():
        if doc.get("status", "active") != "active":
            continue
        if toks(doc.get("statement", "") + " " + doc.get("title", "")) & qt:
            results.append({"id": doc["id"], "kind": doc["_kind"],
                            "title": doc.get("title", ""),
                            "status": doc.get("status", "active"),
                            "verification": doc.get("verification", "unverified"),
                            "source_refs": (doc.get("source_refs") or [])[:3]})
    return {"query": query, "count": min(len(results), limit),
            "retrieval": "v1_1_compat_metadata", "embedding_provider": "disabled",
            "results": results[:limit]}
