#!/usr/bin/env python3
"""Generate the v1.1 schema examples for the publication recovery operator inputs.

These files are produced from the isolated local historical fixture; they are
schema/usage examples, never live evidence. The Lead must regenerate every
value from the authenticated production ledger audit before any live call.

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-publication-recovery-correction/generate_examples.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import u12_r0_binding as u12  # noqa: E402
from test_u12_r0_publication_recovery import (  # noqa: E402
    PublicationRecoveryFixture)

OUT = Path(__file__).resolve().parent


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="u12-r0b-correction-example-") as tmp:
        fx = PublicationRecoveryFixture(Path(tmp))
        decision = fx.decision()
    accepted = dict(decision["execution_migration"])
    (OUT / "publication-recovery-decision-example.json").write_text(
        json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    (OUT / "accepted-execution-example.json").write_text(
        json.dumps(accepted, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    print("wrote v1.1 examples with adapter_digest", u12.adapter_digest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
