#!/usr/bin/env python3
"""Standalone SELF_CHECK CLI subprocess regressions (YZT-61 corrective).

The frozen T04 core (`self_check`) and the integrated `context_cli.py
self-check` route are accepted behavior; this module covers ONLY the
standalone `tools/chandoff_selfcheck.py` entry point as a subprocess:
argparse contract, status exit mapping (READY=0 / REFRESH_REQUIRED=2 /
BLOCKED=3), bounded deterministic failure diagnostics, and parity with
the integrated route and the direct function on equivalent fixtures.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for _p in (TOOLS, TESTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import chandoff_selfcheck as sc  # noqa: E402
from test_handoff_selfcheck import prepare_envelope, sample_request  # noqa: E402

STANDALONE = TOOLS / "chandoff_selfcheck.py"
INTEGRATED = TOOLS / "context_cli.py"


def run_standalone(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(STANDALONE), *args],
        capture_output=True, text=True, cwd=str(TOOLS.parent))


def run_integrated(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(INTEGRATED), "self-check", *args],
        capture_output=True, text=True, cwd=str(TOOLS.parent))


def write_json(path: Path, doc) -> str:
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(path)


def assert_bounded_failure(testcase: unittest.TestCase,
                           proc: subprocess.CompletedProcess):
    """Deterministic bounded failure: exit 2, no result, no traceback."""
    testcase.assertEqual(proc.returncode, 2, proc.stderr)
    testcase.assertEqual(proc.stdout, "")
    testcase.assertTrue(proc.stderr.startswith("ERROR: "), proc.stderr)
    testcase.assertNotIn("Traceback", proc.stderr)
    testcase.assertNotIn("Traceback", proc.stdout)


class CliContractTests(unittest.TestCase):
    """Argparse surface: help forms and standard exit-2 usage errors."""

    def test_top_level_help_exits_zero(self):
        proc = run_standalone(["--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("check", proc.stdout)
        self.assertIn("SELF_CHECK", proc.stdout)

    def test_check_help_exits_zero(self):
        proc = run_standalone(["check", "--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--request-file", "--package", "--store"):
            self.assertIn(flag, proc.stdout)

    def test_invalid_command_uses_argparse_exit_two(self):
        proc = run_standalone(["bogus-command"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("invalid choice", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_missing_command_uses_argparse_exit_two(self):
        proc = run_standalone([])
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr)

    def test_missing_required_argument_uses_argparse_exit_two(self):
        proc = run_standalone(["check"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--request-file", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class CliStatusMappingTests(unittest.TestCase):
    """Status/exit mapping and every callable form of the standalone CLI."""

    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.request = sample_request()

    def test_ready_package_form_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            pkg = write_json(Path(tmp) / "pkg.json", self.envelope)
            proc = run_standalone(["check", "--request-file", req,
                                   "--package", pkg])
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "READY")
            self.assertEqual(out["action"], "USE_EXISTING")
            self.assertEqual(out["package_id"], self.envelope["package_id"])
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_request_only_refresh_required_exit_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            proc = run_standalone(["check", "--request-file", req])
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "REFRESH_REQUIRED")
            self.assertEqual(out["action"], "REFRESH")
            self.assertEqual(out["reasons"], ["package_missing"])
            self.assertEqual(proc.returncode, 2)

    def test_blocked_package_exit_three(self):
        blocked = dict(self.envelope)
        blocked["status"] = "BLOCKED"
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            pkg = write_json(Path(tmp) / "blocked.json", blocked)
            proc = run_standalone(["check", "--request-file", req,
                                   "--package", pkg])
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "BLOCKED")
            self.assertEqual(out["action"], "ESCALATE")
            self.assertIn("package_not_ready", out["reasons"])
            self.assertEqual(proc.returncode, 3)

    def test_repeated_package_flag_resolves_latest(self):
        older = copy.deepcopy(self.envelope)
        older["generated_at"] = "2026-09-09T00:00:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            old = write_json(Path(tmp) / "older.json", older)
            new = write_json(Path(tmp) / "newer.json", self.envelope)
            proc = run_standalone(["check", "--request-file", req,
                                   "--package", old, "--package", new])
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "READY", out)
            self.assertEqual(out["package_id"], self.envelope["package_id"])
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_store_form_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            store = Path(tmp) / "store"
            store.mkdir()
            write_json(store / f"{self.envelope['package_id']}.json",
                       self.envelope)
            proc = run_standalone(["check", "--request-file", req,
                                   "--store", str(store)])
            out = json.loads(proc.stdout)
            self.assertEqual(out["status"], "READY", out)
            self.assertEqual(out["package_id"], self.envelope["package_id"])
            self.assertEqual(proc.returncode, 0, proc.stderr)


class CliBoundedErrorTests(unittest.TestCase):
    """Missing/malformed inputs fail deterministically without traceback."""

    def test_missing_request_file_bounded_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "nope.json"
            proc = run_standalone(["check", "--request-file", str(missing)])
            assert_bounded_failure(self, proc)
            self.assertIn("nope.json", proc.stderr)

    def test_malformed_request_json_bounded_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "req.json"
            req.write_text("{broken json", encoding="utf-8")
            proc = run_standalone(["check", "--request-file", str(req)])
            assert_bounded_failure(self, proc)

    def test_invalid_request_bounded_failure(self):
        bad = sample_request(project={"project_id": "app1"})
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", bad)
            proc = run_standalone(["check", "--request-file", req])
            assert_bounded_failure(self, proc)
            self.assertIn("invalid self_check_request", proc.stderr)

    def test_malformed_package_json_bounded_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", sample_request())
            pkg = Path(tmp) / "pkg.json"
            pkg.write_text("[not an object", encoding="utf-8")
            proc = run_standalone(["check", "--request-file", req,
                                   "--package", str(pkg)])
            assert_bounded_failure(self, proc)

    def test_bounded_failure_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "req.json"
            req.write_text("{broken json", encoding="utf-8")
            args = ["check", "--request-file", str(req)]
            first = run_standalone(args)
            second = run_standalone(args)
            self.assertEqual(
                (first.returncode, first.stdout, first.stderr),
                (second.returncode, second.stdout, second.stderr))
            assert_bounded_failure(self, first)


class CliParityTests(unittest.TestCase):
    """Same fixtures: standalone == integrated route == direct function."""

    @classmethod
    def setUpClass(cls):
        cls.envelope = prepare_envelope()
        cls.request = sample_request()

    def test_parity_with_integrated_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            pkg = write_json(Path(tmp) / "pkg.json", self.envelope)
            args = ["--request-file", req, "--package", pkg]
            alone = run_standalone(["check", *args])
            integrated = run_integrated(args)
            self.assertEqual(alone.returncode, integrated.returncode)
            self.assertEqual(alone.stdout, integrated.stdout)
            self.assertEqual(json.loads(alone.stdout)["status"], "READY")

    def test_parity_with_integrated_route_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            args = ["--request-file", req]
            alone = run_standalone(["check", *args])
            integrated = run_integrated(args)
            self.assertEqual(alone.returncode, integrated.returncode)
            self.assertEqual(alone.stdout, integrated.stdout)
            self.assertEqual(json.loads(alone.stdout)["status"],
                             "REFRESH_REQUIRED")
            self.assertEqual(alone.returncode, 2)

    def test_parity_with_direct_self_check(self):
        direct = sc.self_check(self.request, packages=[self.envelope])
        with tempfile.TemporaryDirectory() as tmp:
            req = write_json(Path(tmp) / "req.json", self.request)
            pkg = write_json(Path(tmp) / "pkg.json", self.envelope)
            proc = run_standalone(["check", "--request-file", req,
                                   "--package", pkg])
            self.assertEqual(json.loads(proc.stdout), direct)
            self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
