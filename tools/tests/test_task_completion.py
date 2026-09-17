"""Completion must bind the delivered child and reconcile an uncertain mutation."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SOURCE = Path(__file__).resolve().parents[2] / "skills/parent-handoff-wake/scripts/complete_task.py"
spec = importlib.util.spec_from_file_location("task_completion", SOURCE)
completion = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completion)


class FakeCLI:
    def __init__(self, *, status="in_review", mutation="success"):
        self.child = dict(id="child", identifier="YZT-130", project_id="project",
                          parent_issue_id="parent", assignee_id="reviewer", stage=14, status=status)
        self.parent = dict(id="parent", project_id="project")
        self.comments = [dict(id="result", issue_id="child", author_id="reviewer",
                              author_type="agent", type="comment", content="Exact review verdict: REJECT")]
        self.calls = []
        self.mutation = mutation

    def call(self, args):
        self.calls.append(args)
        if args[:2] == ["issue", "get"]:
            return dict(self.parent if args[2] == "parent" else self.child)
        if args[:3] == ["issue", "comment", "list"]:
            return self.comments
        if args[:2] == ["issue", "status"]:
            if self.mutation != "ignored":
                self.child["status"] = "done"
            if self.mutation == "timeout":
                raise subprocess.TimeoutExpired("status", 90)
            return dict(self.child)
        raise AssertionError(args)


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        evidence = root / "verification.json"
        evidence.write_text(json.dumps([dict(command="review exact artifact", status="FAIL")]))
        self.args = SimpleNamespace(issue="child", parent="parent", project_id="project",
                                    assignee_id="reviewer", final_comment_id="result", revision="review-v1",
                                    verification_file=str(evidence), out_dir=str(root / "receipt"),
                                    goal_met=True, authorize_complete=True)

    def mutations(self, cli):
        return [a for a in cli.calls if a[:2] == ["issue", "status"]]

    def test_completed_reject_report_finishes_task_without_approving_implementation(self):
        cli = FakeCLI()
        result = completion.complete(self.args, cli)
        self.assertEqual(result["status"], "DONE_CONFIRMED")
        self.assertEqual(result["after"], "done")
        self.assertEqual(len(self.mutations(cli)), 1)
        self.assertEqual(cli.comments[0]["content"], "Exact review verdict: REJECT")
        self.assertEqual(cli.calls[-1], ["issue", "get", "child"])

    def test_already_done_never_retriggers_native_handoff(self):
        cli = FakeCLI(status="done")
        self.assertTrue(completion.complete(self.args, cli)["idempotent"])
        self.assertEqual(self.mutations(cli), [])

    def test_unmet_goal_never_sends_state_change(self):
        self.args.goal_met = False
        cli = FakeCLI()
        with self.assertRaises(completion.CompletionRefusal):
            completion.complete(self.args, cli)
        self.assertEqual(cli.calls, [])

    def test_wrong_scope_owner_or_result_never_mutates(self):
        for field, value in [("project_id", "other"), ("parent_issue_id", "other"),
                             ("assignee_id", "other"), ("stage", None)]:
            with self.subTest(field=field):
                cli = FakeCLI()
                cli.child[field] = value
                with self.assertRaises(completion.CompletionRefusal):
                    completion.complete(self.args, cli)
                self.assertEqual(self.mutations(cli), [])
        cli = FakeCLI()
        cli.comments[0]["author_id"] = "producer"
        with self.assertRaises(completion.CompletionRefusal):
            completion.complete(self.args, cli)
        self.assertEqual(self.mutations(cli), [])

    def test_lost_mutation_response_reconciles_without_retry(self):
        cli = FakeCLI(mutation="timeout")
        result = completion.complete(self.args, cli)
        self.assertEqual(result["after"], "done")
        self.assertEqual(result["mutation_response"], "unknown")
        self.assertEqual(len(self.mutations(cli)), 1)

    def test_success_response_without_done_readback_cannot_claim_completion(self):
        cli = FakeCLI(mutation="ignored")
        with self.assertRaises(completion.CompletionRefusal):
            completion.complete(self.args, cli)
        receipt = json.loads((Path(self.args.out_dir) / "completion-receipt.json").read_text())
        self.assertEqual(receipt["status"], "COMPLETION_UNCONFIRMED")
        self.assertEqual(receipt["after"], "in_review")
        self.assertEqual(len(self.mutations(cli)), 1)


if __name__ == "__main__":
    unittest.main()
