#!/usr/bin/env python3
"""YZT-98 operator helper — build the production Findings source binding for
`teachers-app1` from a live, Lead-authorized Multica comment.

Why a helper: the frozen binding schema (`findings-source-binding/1`,
`tools/chandoff_findings_source.py`) requires the `authority.digest` to be the
exact sha256 of the live authority comment's content, and the production
resolver (`AuthenticatedCommentResolver`) re-reads that comment through the
authenticated `multica` CLI on every boundary. Hand-editing that digest is
error-prone, so this script reads the comment once, computes the digest, and
writes a fully validated binding.

Authority rules kept intact:

- it never posts, edits or mentions anything on the platform (read-only CLI);
- it never invents authority: the comment must already exist, be authored by
  the id the caller passes, and be readable through the authenticated CLI;
- the trusted source map (which pins the physical root) is required and is
  itself validated before a binding is written;
- an unreadable comment, an author mismatch, or an invalid trusted map is a
  bounded failure, never a partially written binding.

Usage:
  python build_binding.py --repo <context-repo-root> \\
      --issue <issue-id> --comment-id <comment-id> \\
      --trusted-map <trusted-map.json> --out <binding.json> \\
      [--source-id teachers-app1-runtime-findings] [--project-id teachers-app1] \\
      [--task-ref multica://issue/YTZ-97 --task-ref ...] [--role ...] \\
      [--commit <40-hex>] [--adapter-digest sha256:...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BINDING_SCHEMA = "findings-source-binding/1"
LAYOUT_FLAT = "flat-finding-json-v1"
DEFAULT_SOURCE_ID = "teachers-app1-runtime-findings"
DEFAULT_PROJECT_ID = "teachers-app1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fail(code: str, message: str, **details) -> "SystemExit":
    payload = {"ok": False, "code": code, "message": message}
    if details:
        payload["details"] = details
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    return SystemExit(2)


def cli_prefix(executable, profile=None, workspace_id=None):
    result = [executable]
    if profile:
        result += ["--profile", profile]
    if workspace_id:
        result += ["--workspace-id", workspace_id]
    return result


def validate_task_scope(task_refs, root_issue, project_id, read_issue):
    """Every exact task must belong to this project and descend from the approved root."""
    root = read_issue(root_issue)
    if root.get("project_id") != project_id:
        raise ValueError("authority root belongs to another project")
    evidence = []
    for ref in task_refs:
        if not isinstance(ref, str) or not ref.startswith("multica://issue/"):
            raise ValueError("exact Multica task reference required")
        current = read_issue(ref.removeprefix("multica://issue/"))
        seen = set()
        chain = []
        while True:
            ident = current.get("id")
            if not ident or ident in seen or len(seen) >= 64:
                raise ValueError("invalid or cyclic parent chain")
            if current.get("project_id") != project_id:
                raise ValueError("task/parent belongs to another project")
            seen.add(ident); chain.append(ident)
            if ident == root.get("id"):
                break
            parent = current.get("parent_issue_id")
            if not parent:
                raise ValueError("task is outside the authorized root")
            current = read_issue(parent)
        evidence.append({"task_ref": ref, "project_id": project_id, "parent_chain": chain})
    if not evidence:
        raise ValueError("at least one exact task is required")
    return evidence


def read_live_comment(executable: str, issue_id: str, comment_id: str,
                      profile=None, workspace_id=None) -> dict:
    """Read one comment through the authenticated CLI. Read-only, no effects."""
    cmd = cli_prefix(executable, profile, workspace_id) + ["issue", "comment", "list", issue_id,
           "--thread", comment_id, "--tail", "50", "--compact", "--output", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise _fail("authority_cli_failed",
                    "authenticated comment read failed",
                    exit_code=proc.returncode, stderr=(proc.stderr or "")[:400])
    try:
        records = json.loads(proc.stdout)
    except ValueError as exc:
        raise _fail("authority_cli_unparseable",
                    f"comment list is not JSON: {exc}")
    if not isinstance(records, list):
        raise _fail("authority_cli_unparseable", "comment list is not an array")
    for record in records:
        if isinstance(record, dict) and record.get("id") == comment_id:
            return record
    raise _fail("authority_comment_missing",
                "the authority comment is absent on the platform",
                comment_id=comment_id)


def build_binding(*, comment: dict, issue_id: str, comment_id: str,
                  source_id: str, project_id: str, root: str,
                  task_refs: list, roles: list, commit: str,
                  adapter_digest: str) -> dict:
    content = comment.get("content")
    if not isinstance(content, str) or not content:
        raise _fail("authority_content_missing",
                    "the authority comment carries no verifiable content")
    author_id = comment.get("author_id")
    author_type = comment.get("author_type")
    if not isinstance(author_id, str) or not author_id.strip():
        raise _fail("authority_author_missing",
                    "the authority comment has no author id")
    if author_type not in ("agent", "member"):
        raise _fail("authority_author_type_invalid",
                    "author_type must be agent or member", found=author_type)
    return {
        "schema": BINDING_SCHEMA,
        "source_id": source_id,
        "project_id": project_id,
        "root": root,
        "layout": LAYOUT_FLAT,
        "authority": {
            "comment_id": comment_id,
            "issue_id": comment.get("issue_id") or issue_id,
            "author_id": author_id,
            "author_type": author_type,
            "digest": "sha256:" + hashlib.sha256(
                content.encode("utf-8")).hexdigest(),
        },
        "allowed": {"task_refs": list(task_refs), "roles": list(roles)},
        "runtime": {"commit": commit, "adapter_digest": adapter_digest},
        "created_at": _now_iso(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True,
                        help="Context repository root (verified, never guessed)")
    parser.add_argument("--issue", required=True,
                        help="issue id holding the authority comment")
    parser.add_argument("--comment-id", required=True,
                        help="Lead-authorized authority comment id")
    parser.add_argument("--trusted-map", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--source-id", default=DEFAULT_SOURCE_ID)
    parser.add_argument("--project-id", default=DEFAULT_PROJECT_ID)
    parser.add_argument("--task-ref", action="append", default=[])
    parser.add_argument("--role", action="append", default=[])
    parser.add_argument("--commit", default=None,
                        help="40-hex Context repo commit pin (default: HEAD)")
    parser.add_argument("--adapter-digest", default=None,
                        help="sha256 adapter/runtime pin "
                             "(default: sha256 of tools/chandoff_findings_source.py)")
    parser.add_argument("--executable", default="multica")
    parser.add_argument("--profile")
    parser.add_argument("--workspace-id")
    parser.add_argument("--authority-root", help="approved task-tree root; defaults to the authority issue")
    parser.add_argument("--multica-project-id", default="7a2195b5-6628-4b02-9fb2-bc3ce161de85")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if not (repo / "tools" / "chandoff_findings_source.py").is_file():
        raise _fail("repo_not_verified",
                    "--repo is not a verified Context repository root", repo=str(repo))
    sys.path.insert(0, str(repo / "tools"))
    import chandoff_findings_source as cfs  # noqa: E402

    trusted = cfs.load_trusted_source_map(args.trusted_map)
    if trusted.get("project_id") != args.project_id:
        raise _fail("trusted_map_project_mismatch",
                    "trusted map project_id differs from the binding project",
                    map=trusted.get("project_id"), binding=args.project_id)
    entry = (trusted.get("sources") or {}).get(args.source_id)
    if not isinstance(entry, dict):
        raise _fail("trusted_map_source_missing",
                    "source_id is not present in the trusted source map",
                    source_id=args.source_id)

    root = entry["root"]
    task_refs = args.task_ref or list(entry["allowed"]["task_refs"])
    roles = args.role or list(entry["allowed"]["roles"])

    commit = args.commit
    if not commit:
        proc = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True)
        commit = (proc.stdout or "").strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit.lower()):
        raise _fail("commit_pin_invalid", "commit must be a full 40-hex sha",
                    value=commit)

    adapter_digest = args.adapter_digest
    if not adapter_digest:
        adapter_digest = "sha256:" + hashlib.sha256(
            (repo / "tools" / "chandoff_findings_source.py").read_bytes()
        ).hexdigest()

    def read_issue(issue):
        command = cli_prefix(args.executable, args.profile, args.workspace_id) + ["issue", "get", issue, "--output", "json"]
        result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", timeout=45)
        return json.loads(result.stdout)
    try:
        scope_evidence = validate_task_scope(task_refs, args.authority_root or args.issue,
                                             args.multica_project_id, read_issue)
    except (ValueError, subprocess.SubprocessError) as exc:
        raise _fail("task_scope_unverified", str(exc))
    comment = read_live_comment(args.executable, args.issue, args.comment_id,
                                args.profile, args.workspace_id)
    binding = build_binding(
        comment=comment, issue_id=args.issue, comment_id=args.comment_id,
        source_id=args.source_id, project_id=args.project_id, root=root,
        task_refs=task_refs, roles=roles, commit=commit,
        adapter_digest=adapter_digest)

    try:
        validated = cfs.validate_binding(binding, project_id=args.project_id,
                                        trusted=trusted, require_trusted=True)
    except cfs.FindingsSourceRefusal as exc:
        raise _fail(exc.code, exc.message, **(exc.details or {}))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    out.with_suffix(".scope.json").write_text(json.dumps(scope_evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "binding": str(out),
        "source_id": validated["source_id"],
        "project_id": validated["project_id"],
        "root": validated["root"],
        "authority_comment": validated["authority"]["comment_id"],
        "authority_digest": validated["authority"]["digest"],
        "task_refs": validated["allowed"]["task_refs"],
        "roles": validated["allowed"]["roles"],
        "runtime_commit": validated["runtime"]["commit"],
        "runtime_adapter_digest": validated["runtime"]["adapter_digest"],
        "note": "production activation is still per-boundary: the resolver "
                "re-reads this comment and re-verifies the digest on every "
                "PREPARE/FINALIZE/SELF_CHECK/PUBLISH boundary",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
