#!/usr/bin/env python3
"""Gate A legacy inventory ↔ migration-map set reconciliation (YZT-42).

Fail-closed: missing inventory, missing map, or empty inventory cannot yield
100% accounted / 0 silent drop. Group-level prose in the map does not count
as a per-object disposition.
"""
from __future__ import annotations

from pathlib import Path

from cutil import ROOT, doc_at
from schema_mini import Schema, load_schema_file


INVENTORY_PATH = ROOT / "migration" / "legacy-inventory.yaml"
MAP_PATH = ROOT / "migration" / "migration-map.yaml"


def normalize_legacy(raw: str) -> str:
    s = (raw or "").strip().replace("\\", "/")
    if " (" in s:
        s = s.split(" (", 1)[0].strip()
    return s


def derived_inventory(root: Path | None = None) -> list[dict]:
    base = root or ROOT
    items = []
    for rel_dir, kind in (
        ("memory", "unit"),
        ("chains", "chain"),
        ("sources", "signal"),
        ("sources/candidates", "candidate"),
    ):
        folder = base / rel_dir
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.json")):
            rel = path.relative_to(base).as_posix()
            items.append({"id": rel, "kind": kind, "path": rel})
    return items


def load_explicit_inventory(path: Path | None = None) -> dict | None:
    p = path or INVENTORY_PATH
    if not p.exists():
        return None
    doc = doc_at(p)
    schema = load_schema_file("legacy-inventory.schema.json")
    errors = Schema(schema, schema).validate(doc, path="legacy-inventory")
    if errors:
        raise ValueError("legacy-inventory schema: " + "; ".join(errors))
    return doc


def mapped_legacy_ids(map_doc: dict) -> dict[str, dict]:
    """Per-object map entries keyed by normalized legacy path.

    Only list items that carry a `legacy` field count. A section-level
    `action` (e.g. signals as a blob) is not a per-object disposition.
    """
    out: dict[str, dict] = {}
    if not isinstance(map_doc, dict):
        return out
    for section, value in map_doc.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict) or "legacy" not in item:
                continue
            key = normalize_legacy(str(item["legacy"]))
            if not key:
                continue
            rec = dict(item)
            rec["_section"] = section
            rec["_legacy_id"] = key
            out.setdefault(key, rec)
    return out


def reconcile(inventory_ids: list[str], mapped: dict[str, dict]) -> dict:
    inv = [i for i in inventory_ids if i]
    total = len(inv)
    mapped_keys = set(mapped)
    inv_set = set(inv)
    accounted = sorted(inv_set & mapped_keys)
    silent = sorted(inv_set - mapped_keys)
    map_only = sorted(mapped_keys - inv_set)
    if total == 0:
        pct = 0.0
    else:
        pct = round(100.0 * len(accounted) / total, 2)
    return {
        "inventory_count": total,
        "mapped_count": len(mapped_keys),
        "accounted_ids": accounted,
        "silent_drop_ids": silent,
        "map_only_ids": map_only,
        "legacy_objects_accounted_for": pct,
        "silent_drop": len(silent),
        "complete": total > 0 and len(silent) == 0 and pct == 100.0,
    }


def run_accounting(root: Path | None = None, map_path: Path | None = None,
                   inventory_path: Path | None = None) -> dict:
    base = root or ROOT
    missing: list[str] = []
    errors: list[str] = []
    mp = map_path or (base / "migration" / "migration-map.yaml")
    ip = inventory_path or (base / "migration" / "legacy-inventory.yaml")

    derived = derived_inventory(base)
    derived_ids = [d["id"] for d in derived]
    inventory_source = "derived_filesystem"
    inventory_ids = list(derived_ids)
    explicit = None
    if ip.exists():
        try:
            explicit = load_explicit_inventory(ip)
            inventory_source = "explicit_legacy_inventory"
            inventory_ids = [x["id"] for x in (explicit.get("objects") or [])]
            explicit_set, derived_set = set(inventory_ids), set(derived_ids)
            if explicit_set != derived_set:
                errors.append(
                    "legacy-inventory objects do not match filesystem inventory: "
                    f"only_in_file={sorted(explicit_set - derived_set)}; "
                    f"only_on_disk={sorted(derived_set - explicit_set)}")
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
            missing.append("migration/legacy-inventory.yaml")
            inventory_source = "derived_filesystem_after_invalid_explicit"
            inventory_ids = list(derived_ids)
    else:
        missing.append("migration/legacy-inventory.yaml")

    if not mp.exists():
        missing.append("migration/migration-map.yaml")
        mapped: dict[str, dict] = {}
        errors.append("migration-map.yaml missing; cannot reconcile inventory")
    else:
        mapped = mapped_legacy_ids(doc_at(mp))
        if not mapped:
            errors.append("migration-map.yaml has no per-object legacy entries")

    result = reconcile(inventory_ids, mapped)
    result.update({
        "inventory_source": inventory_source,
        "derived_ids": derived_ids,
        "missing_evidence": missing,
        "errors": errors,
        "fail_closed": (result["inventory_count"] == 0) or (not mp.exists()) or
                       (not result["complete"]) or bool(errors),
    })
    return result
