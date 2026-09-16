#!/usr/bin/env python3
"""Fixture helper: resolve the legacy `app1` scope against a pre-YZT-98 Registry.

YZT-98 (2026-09-16, Human decision on YZT-97) archived the legacy `app1`
project in the canonical Project Registry and registered `teachers-app1` as the
new, independent project. The canonical `project-context/app1/**` objects are
deliberately kept in place for traceability, and several T01/T02/T03 tests
exercise *mechanics* — case hard gate, cross-project allow list, ref grammar —
through those canonical app1 objects.

Those tests therefore pin scope resolution to a fixture Registry in which
`app1` is still a live phase. They assert nothing about the canonical phase:
the canonical archived phase is covered by
`test_handoff_plan.ScopeIsolationTests.test_canonical_registry_archives_app1`
and by the YZT-98 read-only isolation probes
(`adapters/multica/project-bindings/teachers-app1/verify_isolation.py`).

This helper changes no canonical file and no production code path; it only uses
the `registry=` injection seam that `chandoff_plan` already exposes (the same
seam `test_archived_project_is_excluded` uses).
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chandoff_plan as plan  # noqa: E402

ARCHIVED_LEGACY_PROJECT = "app1"
LIVE_APP1_PHASE = "incubation"


def registry_with_live_app1() -> dict:
    """Canonical Registry with the archived `app1` phase restored to live."""
    registry = copy.deepcopy(plan.load_registry())
    for project in registry["projects"]:
        if project["id"] == ARCHIVED_LEGACY_PROJECT:
            project["phase"] = LIVE_APP1_PHASE
    return registry
