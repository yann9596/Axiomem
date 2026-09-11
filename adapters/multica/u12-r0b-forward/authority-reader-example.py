#!/usr/bin/env python3
"""Concrete current authority-reader binding/invocation example (YZT-84).

Executable example of the required current-authority input for the forward
adapter preflight. The binding is the existing accepted readiness manifest
already pinned in the intent artifact dependency:

    adapters/multica/u12-p0r/readiness-manifest.json
    ref:           repo://adapters/multica/u12-p0r/readiness-manifest.json
    digest_method: canonical_json

The reader is read-only and is invoked fresh at every consequential
entrypoint; it never caches and never adopts a newer artifact. The same
evidence can be produced from the CLI and passed to `arm`/`trigger` as
`authority_evidence=` when no reader is injected:

    python -B tools/u12_r0_binding.py authority-evidence --root <repo-root>

Usage (from the repository root):
    python -B adapters/multica/u12-r0b-forward/authority-reader-example.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

import u12_r0_binding as u12  # noqa: E402

ACCEPTED_MANIFEST_DIGEST = (
    "sha256:64a5c449a9639979cb6a87627c583683cb99fb0f237ceff6634fc4b5c7e3bc93")


def main() -> int:
    bound = u12.build_artifact_dependency_digest()["entries"][
        u12.AUTHORITY_ARTIFACT_PATH]
    reader = u12.ReadinessManifestAuthorityReader(ROOT)
    evidence = reader.read()
    record = evidence["record"]
    recomputed_manifest = u12.digest({k: v for k, v in record.items()
                                      if k != "manifest_digest"})
    checks = {
        "path_is_the_bound_authority_path":
            evidence["path"] == u12.AUTHORITY_ARTIFACT_PATH,
        "ref_is_the_canonical_bound_ref":
            evidence["ref"] == u12.AUTHORITY_REF,
        "digest_method_matches_binding":
            evidence["digest_method"] == bound["digest_method"],
        "current_record_equals_bound_version":
            evidence["sha256"] == bound["sha256"],
        "accepted_manifest_digest_reproduces":
            record["manifest_digest"] == ACCEPTED_MANIFEST_DIGEST
            and recomputed_manifest == ACCEPTED_MANIFEST_DIGEST,
        "strict_gate_cross_link_matches_bound_subject":
            record["strict_receipt_gate"]["sha256_lf"]
            == u12.build_artifact_dependency_digest()["entries"][
                "tools/u12_strict_receipt.py"]["sha256"],
        "disposition_is_ready": evidence["disposition"] == "READY",
    }
    print(json.dumps({
        "kind": "u12_r0b_authority_reader_example",
        "bound_entry": {"path": u12.AUTHORITY_ARTIFACT_PATH,
                        "commit": bound["commit"],
                        "digest_method": bound["digest_method"],
                        "sha256": bound["sha256"]},
        "evidence_summary": {
            "ref": evidence["ref"],
            "sha256": evidence["sha256"],
            "disposition": evidence["disposition"],
            "manifest_digest": record["manifest_digest"],
        },
        "checks": checks,
        "ok": all(checks.values()),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
