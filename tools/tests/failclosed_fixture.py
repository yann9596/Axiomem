"""Isolated repo that lacks inventory, per-object map coverage, authority, and replay evidence.

Used by fail-closed negative tests so they do not depend on the current real
repo having (or missing) YZT-43 evidence.
"""
from __future__ import annotations

from pathlib import Path


def build_missing_evidence_root(root: Path) -> Path:
    """Construct a mini context repo whose Gate A/C evidence files are absent.

    Present:
      - filesystem legacy objects (unit / chain / candidate / two signals)
      - a migration-map that lists per-object dispositions for unit/chain/candidate
        but records signals only as grouped prose (not per-object `legacy` entries)

    Absent (fail-closed inputs):
      - migration/legacy-inventory.yaml
      - migration/authority-evidence.yaml
      - migration/replay-expectation.lock.yaml
      - migration/replay-evidence/<task>.yaml
    """
    (root / "memory").mkdir(parents=True, exist_ok=True)
    (root / "memory" / "unit-a.json").write_text("{}", encoding="utf-8")
    (root / "chains").mkdir(parents=True, exist_ok=True)
    (root / "chains" / "chain-a.json").write_text("{}", encoding="utf-8")
    (root / "sources" / "candidates").mkdir(parents=True, exist_ok=True)
    (root / "sources" / "signal-a.json").write_text("{}", encoding="utf-8")
    (root / "sources" / "signal-b.json").write_text("{}", encoding="utf-8")
    (root / "sources" / "candidates" / "cand-a.json").write_text("{}", encoding="utf-8")
    mig = root / "migration"
    (mig / "replay-evidence").mkdir(parents=True, exist_ok=True)
    (mig / "migration-map.yaml").write_text(
        "units:\n"
        "  - legacy: memory/unit-a.json\n"
        "    action: transform\n"
        "chains:\n"
        "  - legacy: chains/chain-a.json\n"
        "    action: transform\n"
        "candidates:\n"
        "  - legacy: sources/candidates/cand-a.json\n"
        "    action: finding_ingest\n"
        "signals:\n"
        "  action: reclassify_keep_as_evidence\n"
        "  note: all signals listed in prose, not per-object\n",
        encoding="utf-8")
    return root
