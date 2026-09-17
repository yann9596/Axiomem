#!/usr/bin/env python3
"""One scoped child completion, followed by authoritative state readback."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


class CompletionRefusal(RuntimeError):
    pass


class MulticaCLI:
    def __init__(self, executable: str, profile: str, workspace: str):
        self.prefix = [executable, "--profile", profile, "--workspace-id", workspace]

    def call(self, args: list[str]) -> dict | list:
        proc = subprocess.run(self.prefix + args + ["--output", "json"],
                              capture_output=True, timeout=90)
        if proc.returncode:
            raise CompletionRefusal("CLI operation failed: " +
                                    proc.stderr.decode("utf-8", "replace")[:240])
        try:
            return json.loads(proc.stdout)
        except (ValueError, UnicodeError) as exc:
            raise CompletionRefusal("CLI did not return authoritative JSON") from exc


def complete(args, cli) -> dict:
    """Caller attests goal fulfilment; deterministic checks bind its state change."""
    if not args.goal_met or not args.authorize_complete:
        raise CompletionRefusal("met-goal attestation and completion authorization required")
    if not args.revision.strip():
        raise CompletionRefusal("exact deliverable revision required")
    verification = json.loads(Path(args.verification_file).read_text(encoding="utf-8-sig"))
    if not isinstance(verification, list) or not verification:
        raise CompletionRefusal("nonempty verification records required")
    for item in verification:
        if not isinstance(item, dict) or not item.get("command") or item.get("status") not in {
            "PASS", "FAIL", "NOT_RUN", "BLOCKED"
        }:
            raise CompletionRefusal("verification requires command and honest status")

    issue = cli.call(["issue", "get", args.issue])
    parent = cli.call(["issue", "get", args.parent])
    if issue.get("project_id") != args.project_id or parent.get("project_id") != args.project_id:
        raise CompletionRefusal("task/parent project mismatch")
    if not issue.get("id") or issue.get("parent_issue_id") != parent.get("id"):
        raise CompletionRefusal("task is not the declared parent's child")
    if issue.get("assignee_id") != args.assignee_id:
        raise CompletionRefusal("assigned owner mismatch")
    if not isinstance(issue.get("stage"), int) or isinstance(issue.get("stage"), bool):
        raise CompletionRefusal("explicit child stage required for native handoff")
    comments = cli.call(["issue", "comment", "list", issue["id"]])
    matches = [c for c in comments if c.get("id") == args.final_comment_id]
    if len(matches) != 1:
        raise CompletionRefusal("one actual final child comment required")
    comment = matches[0]
    if comment.get("issue_id") != issue["id"] or comment.get("author_id") != args.assignee_id:
        raise CompletionRefusal("final result does not belong to the assigned child owner")
    if comment.get("author_type") != "agent" or comment.get("type") != "comment":
        raise CompletionRefusal("final result must be an assigned agent's result comment")
    if not (comment.get("content") or "").strip():
        raise CompletionRefusal("empty result is not a deliverable")
    if issue.get("status") in {"canceled", "cancelled"}:
        raise CompletionRefusal("cancelled work cannot be completed")

    receipt = {
        "schema": "task-completion-receipt/1", "task_ref": "multica://issue/" + issue["identifier"],
        "issue_id": issue["id"], "parent_id": parent["id"], "project_id": args.project_id,
        "assignee_id": args.assignee_id, "final_comment_id": args.final_comment_id,
        "artifact_revision": args.revision, "verification": verification,
        "goal_met": True, "before": issue["status"], "mutation_attempted": False,
        "native_wake_expected": True,
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    def save():
        (out / "completion-receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    if issue["status"] == "done":
        receipt.update(status="DONE_CONFIRMED", after="done", idempotent=True)
        save()
        return receipt

    receipt["mutation_attempted"] = True
    save()  # Preserve the intended one mutation before sending it.
    try:
        cli.call(["issue", "status", issue["id"], "done", "--no-start"])
    except (CompletionRefusal, subprocess.TimeoutExpired) as exc:
        receipt["mutation_response"] = "unknown"
        receipt["mutation_error"] = str(exc)[:240]
    try:
        current = cli.call(["issue", "get", issue["id"]])
    except (CompletionRefusal, subprocess.TimeoutExpired) as exc:
        receipt.update(status="COMPLETION_UNCONFIRMED", readback_error=str(exc)[:240])
        save()
        raise CompletionRefusal("completion readback unavailable; inspect receipt, do not retry") from exc
    if current.get("id") != issue["id"] or current.get("project_id") != args.project_id:
        receipt.update(status="COMPLETION_UNCONFIRMED", after=current.get("status"))
        save()
        raise CompletionRefusal("completion readback identity changed")
    receipt["after"] = current.get("status")
    if current.get("status") != "done":
        receipt["status"] = "COMPLETION_UNCONFIRMED"
        save()
        raise CompletionRefusal("live child is not done; inspect receipt, do not retry")
    receipt.update(status="DONE_CONFIRMED", idempotent=False)
    save()
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ["issue", "parent", "project-id", "assignee-id", "final-comment-id",
                 "revision", "verification-file", "out-dir", "executable", "profile", "workspace-id"]:
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--goal-met", action="store_true")
    parser.add_argument("--authorize-complete", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = complete(args, MulticaCLI(args.executable, args.profile, args.workspace_id))
    except (CompletionRefusal, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "status": "STOPPED", "reason": str(exc)[:300]},
                         ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
