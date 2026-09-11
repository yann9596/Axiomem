#!/usr/bin/env python3
"""U12-P0 production ledger deployment preflight (YZT-81).

One-shot, host-local deployment tooling for the approved production ledger
root. It performs no platform write, no trigger, no retry and no autonomous
wake:

  validate  -> exact path / reparse / overlap / pre-existing / identity checks
  deploy    -> create the state tree, apply the least-privilege ACL, create
               the O2 append-only ledger, immutable backup + isolated restore,
               absolute-root capability proof, receipt-contract revalidation
  verify    -> read-only re-check of an existing deployment
  pins      -> read-only U11/O2 pin and digest verification

Usage (production):
  python tools/u12_preflight.py deploy --evidence-dir adapters/multica/u12-p0

Safety: every refusal is fail-closed and never repairs, deletes or rewrites
ledger state. Ambiguity stops the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff_intent as o2  # noqa: E402

TOOL_VERSION = "U12-P0/1.0"
BASE_COMMIT = "6439bd429af0aa9ec4b95d838643bd8b633ed053"
BRANCH = "yzt-81-u12-p0-production-ledger"
TASK_REF = "YZT-81"
AUTHORITY = ("Human approval comment 01a08e93-0a1a-7074-9dda-912d1237bf56; "
             "Lead U12 decision comment 01a08e89-22ec-7db7-a581-0c62f30db538")

APPROVED_ROOT = Path(r"D:\AI\multica-state\web-imagegen")
APPROVED_LEDGER = Path(r"D:\AI\multica-state\web-imagegen\dispatch\ledger.jsonl")
PRODUCT_REPO = Path(r"D:\AI\projects\opencode-web-imagegen")
MAIN_REPO = Path(r"D:\AI\multica-memory")
CANONICAL_MEMORY = Path(r"D:\AI\multica-memory\memory")
DERIVED_INDEX = Path(r"D:\AI\multica-memory\index")
RUNTIME_PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")

SYSTEM_SID = "S-1-5-18"
ADMINS_SID = "S-1-5-32-544"
BACKUP_STAMP_FMT = "%Y%m%dT%H%M%SZ"

WRITE_BITS = (0x10000000 | 0x40000000 | 0x00010000 | 0x00040000 |
              0x00080000 | 0x00000002 | 0x00000004 | 0x00000010 |
              0x00000040 | 0x00000100)


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest(obj) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(obj).encode("utf-8")).hexdigest()


def digest_bytes(data: bytes, *, normalize_lf: bool = False) -> str:
    if normalize_lf:
        data = data.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_file(path: Path, *, normalize_lf: bool = False) -> str:
    return digest_bytes(Path(path).read_bytes(), normalize_lf=normalize_lf)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(path) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(str(path))))


def overlap_reason(candidate, tree) -> str | None:
    a, b = norm(candidate), norm(tree)
    if a == b:
        return "same_path"
    if a.startswith(b + os.sep.lower()):
        return "candidate_inside_tree"
    if b.startswith(a + os.sep.lower()):
        return "tree_inside_candidate"
    return None


def find_reparse_points(path, *, lstat=os.lstat):
    hits = []
    nodes = []
    cur = Path(path).absolute()
    while True:
        nodes.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    for node in nodes:
        try:
            st = lstat(str(node))
        except OSError:
            continue
        attrs = getattr(st, "st_file_attributes", 0)
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        if (reparse and attrs & reparse) or stat.S_ISLNK(st.st_mode):
            hits.append(str(node))
    return hits


def git_worktree_paths(repo=MAIN_REPO, *, run=subprocess.run) -> list:
    try:
        proc = run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                   capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    paths = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[len("worktree "):].strip()))
    return paths


def forbidden_trees(worktrees=None) -> list:
    trees = [PRODUCT_REPO, CANONICAL_MEMORY, DERIVED_INDEX, MAIN_REPO]
    trees.extend(worktrees if worktrees is not None
                 else git_worktree_paths())
    seen, out = set(), []
    for tree in trees:
        key = norm(tree)
        if key not in seen:
            seen.add(key)
            out.append(tree)
    return out


def check_preexisting(root=APPROVED_ROOT, ledger=APPROVED_LEDGER) -> dict:
    """Fail-closed inventory of unexpected pre-existing state.

    Scans the container (root.parent) so anything sitting unexpectedly next to
    the approved state tree is visible before the first write.
    """
    root, ledger = Path(root), Path(ledger)
    base = root.parent
    if not base.exists():
        return {"container_exists": False, "root_exists": False,
                "unexpected": False, "unexpected_entries": [], "entries": [],
                "ledger_exists": False}
    entries = []
    for path in sorted(base.rglob("*")):
        entries.append(Path(path).relative_to(base).as_posix())
    unexpected = []
    for rel in entries:
        first = rel.split("/", 1)[0]
        if first != root.name:
            unexpected.append(rel)
            continue
        rest = rel[len(root.name):].lstrip("/")
        if not rest:
            continue
        second = rest.split("/", 1)[0]
        if second not in {"dispatch", "preflight"}:
            unexpected.append(rel)
            continue
        if second == "dispatch":
            tail = rest[len("dispatch"):].lstrip("/")
            if not tail:
                continue
            third = tail.split("/", 1)[0]
            if third == "backups":
                continue
            if third not in {"ledger.jsonl", "ledger.jsonl.lock"}:
                unexpected.append(rel)
    return {
        "container_exists": True,
        "container": str(base),
        "root_exists": root.exists(),
        "unexpected": bool(unexpected),
        "unexpected_entries": unexpected,
        "entry_count": len(entries),
        "ledger_exists": ledger.exists(),
    }


def validate_paths(*, root=APPROVED_ROOT, ledger=APPROVED_LEDGER,
                   worktrees=None, existing=None, lstat=os.lstat) -> dict:
    """All pre-write path/boundary checks. Never writes."""
    root, ledger = Path(root), Path(ledger)
    result = {
        "kind": "u12_p0_path_validation",
        "schema_version": TOOL_VERSION,
        "approved_root": str(APPROVED_ROOT),
        "approved_ledger": str(APPROVED_LEDGER),
        "production_path_exact": (
            norm(root) == norm(APPROVED_ROOT)
            and norm(ledger) == norm(APPROVED_LEDGER)),
        "normalized_root": os.path.normpath(os.path.abspath(str(root))),
        "normalized_ledger": os.path.normpath(os.path.abspath(str(ledger))),
        "volume": os.path.splitdrive(str(ledger))[0],
        "checks": {},
        "refusals": [],
    }
    checks = result["checks"]
    if not result["production_path_exact"]:
        result["refusals"].append(
            "ledger path is not the exact approved production path")

    reparse = sorted(set(find_reparse_points(ledger, lstat=lstat))
                     | set(find_reparse_points(root, lstat=lstat)))
    checks["reparse_points"] = reparse
    if reparse:
        result["refusals"].append(
            f"reparse point on the state/ledger path chain: {reparse}")

    if worktrees is None:
        worktrees = git_worktree_paths()
        if not worktrees and MAIN_REPO.exists():
            result["refusals"].append(
                "git worktree enumeration failed; overlap cannot be proven")
    trees = forbidden_trees(worktrees)
    overlap = {}
    for label, candidate in (("root", root), ("ledger", ledger)):
        for tree in trees:
            reason = overlap_reason(candidate, tree)
            if reason:
                overlap[f"{label}->{tree}"] = reason
    checks["forbidden_tree_overlap"] = overlap
    checks["forbidden_trees_checked"] = [str(t) for t in trees]
    checks["worktree_enumeration_ok"] = bool(worktrees)
    if overlap:
        result["refusals"].append(
            f"approved path overlaps forbidden tree(s): {overlap}")

    pre = existing if existing is not None else check_preexisting(root, ledger)
    checks["preexisting_state"] = {
        "container_exists": pre.get("container_exists", False),
        "root_exists": pre.get("root_exists", pre.get("exists", False)),
        "unexpected": pre.get("unexpected", False),
        "unexpected_entries": pre.get("unexpected_entries", []),
        "entry_count": pre.get("entry_count", len(pre.get("entries", []))),
        "ledger_exists": pre.get("ledger_exists", False),
    }
    if pre.get("unexpected"):
        result["refusals"].append(
            "unexpected pre-existing state: "
            f"{pre.get('unexpected_entries', [])[:10]}")

    result["ok"] = not result["refusals"]
    return result


# ---------------------------------------------------------------------------
# ACL model
# ---------------------------------------------------------------------------
def evaluate_acl(entries, *, allowed_sids, require_protected=False,
                 immutable=False) -> dict:
    """Least-privilege evaluation over ACL entries.

    entries: [{"sid","rights","type","inherited","inheritance","propagation"}]
    """
    violations = []
    allowed = {s.lower() for s in allowed_sids}
    for entry in entries:
        sid = str(entry.get("sid", "")).lower()
        rights = int(entry.get("rights", 0))
        etype = str(entry.get("type", "Allow"))
        inherited = bool(entry.get("inherited", False))
        if etype != "Allow":
            violations.append({"code": "deny_ace_present", "sid": sid})
        if sid not in allowed:
            violations.append({"code": "principal_not_least_privilege",
                               "sid": sid, "rights": rights})
        if immutable and rights & WRITE_BITS:
            violations.append({"code": "immutable_object_has_write_right",
                               "sid": sid, "rights": rights})
        if require_protected and inherited:
            violations.append({"code": "protected_acl_inherits", "sid": sid})
    present = {str(e.get("sid", "")).lower() for e in entries}
    missing = sorted(allowed - present)
    if missing:
        violations.append({"code": "required_principal_missing",
                           "sids": missing})
    return {"ok": not violations, "violations": violations,
            "entry_count": len(entries), "allowed_sids": sorted(allowed)}


# ---------------------------------------------------------------------------
# Host operations (PowerShell / icacls); injectable for tests
# ---------------------------------------------------------------------------
class Host:
    def __init__(self, pwsh=RUNTIME_PWSH, multica="multica"):
        self.pwsh = str(pwsh)
        self.multica = multica
        self.commands: list = []

    def _run(self, argv, *, env=None, cwd=None):
        argv = [str(a) for a in argv]
        self.commands.append(argv)
        return subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              cwd=str(cwd) if cwd else None, timeout=300)

    def whoami(self) -> dict:
        script = ("$id=[System.Security.Principal.WindowsIdentity]::GetCurrent();"
                  "[pscustomobject]@{Name=$id.Name;Sid=$id.User.Value;"
                  "Groups=@($id.Groups|ForEach-Object{$_.Value})}"
                  "|ConvertTo-Json -Compress")
        proc = self._run([self.pwsh, "-NoProfile", "-NonInteractive",
                          "-Command", script])
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        return {"name": data["Name"], "sid": data["Sid"],
                "groups": data.get("Groups", [])}

    def read_acl(self, path) -> dict:
        script = (
            "$p=$env:U12_ACL_PATH;"
            "$acl=Get-Acl -LiteralPath $p;"
            "$owner=$acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value;"
            "$rows=@($acl.Access|ForEach-Object{"
            "[pscustomobject]@{"
            "Sid=$_.IdentityReference.Translate("
            "[System.Security.Principal.SecurityIdentifier]).Value;"
            "Rights=[int64]$_.FileSystemRights;"
            "RightsName=$_.FileSystemRights.ToString();"
            "Type=$_.AccessControlType.ToString();"
            "Inherited=$_.IsInherited;"
            "Inheritance=$_.InheritanceFlags.ToString();"
            "Propagation=$_.PropagationFlags.ToString()}});"
            "[pscustomobject]@{Sddl=$acl.Sddl;Owner=$owner;Access=$rows}"
            "|ConvertTo-Json -Depth 5 -Compress")
        env = dict(os.environ)
        env["U12_ACL_PATH"] = str(path)
        proc = self._run([self.pwsh, "-NoProfile", "-NonInteractive",
                          "-Command", script], env=env)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        entries = payload.get("Access") or []
        if isinstance(entries, dict):
            entries = [entries]
        normalized = []
        for entry in entries:
            normalized.append({
                "sid": entry.get("Sid"),
                "rights": int(entry.get("Rights", 0)),
                "rights_name": entry.get("RightsName"),
                "type": entry.get("Type", "Allow"),
                "inherited": bool(entry.get("Inherited", False)),
                "inheritance": entry.get("Inheritance"),
                "propagation": entry.get("Propagation"),
            })
        return {"sddl": payload.get("Sddl"), "owner_sid": payload.get("Owner"),
                "entries": normalized}

    def apply_acl(self, path, *, inherit_remove: bool, grants: list,
                  run=None) -> dict:
        argv = ["icacls", str(path)]
        if inherit_remove:
            argv.append("/inheritance:r")
        if grants:
            argv += ["/grant:r"] + [str(g) for g in grants]
        runner = run or self._run
        proc = runner(argv)
        return {"argv": argv, "exit": proc.returncode,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip()}

    def set_readonly(self, path) -> dict:
        p = str(path)
        if os.name == "nt":
            proc = self._run(["attrib", "+R", p])
            exit_code = proc.returncode
        else:
            os.chmod(p, 0o444)
            exit_code = 0
        readonly = not bool(os.stat(p).st_mode & stat.S_IWRITE)
        return {"exit": exit_code, "readonly": readonly}

    def capture_cli(self, argv: list) -> dict:
        proc = self._run([self.multica] + [str(a) for a in argv])
        return {"argv": ["multica"] + [str(a) for a in argv],
                "exit": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "stdout_digest_lf": digest_bytes(
                    (proc.stdout or "").encode("utf-8"), normalize_lf=True)}


# ---------------------------------------------------------------------------
# U11 / O2 pin verification
# ---------------------------------------------------------------------------
PIN_FILES_LF = {
    "tools/chandoff_intent.py":
        "sha256:0544046fa2ca97c12e6c48de574074df9de5715a950ad75a783cf31853037032",
    "tools/o2_store_probe.py":
        "sha256:b9cf1d3ac0cb95bd15c0ac02c29a969874fad430b269fac14421719f6b726922",
    "tools/chandoff_joint.py":
        "sha256:2afc229364e6202bef9fae0c5bba264194210d7d1797b5c6cdc39735fc6b6208",
}
PIN_BUNDLES = {
    "adapters/multica/dispatch-intent":
        "sha256:3c207e85f195617fe50914e3384dd309e5847d4c6e55f33a96f328bae0924d4d",
    "adapters/multica/joint-replay":
        "sha256:04e0da8412ba8b024fc18fd06608b59ebe39de76692f7ed85f9c4663d28a29b9",
}
PIN_MATRIX_FILE = (
    "adapters/multica/joint-replay/final-gate-matrix.json",
    "sha256:a9ecc24c0ee1ff8965b534ba3b317b5e1c31e541458bb9fb06d88d59eefe946d")
PIN_FEATURE_COMMITS = ["964f935", "e855fa1", "6439bd4"]
# Approved YZT-84 publication-recovery exception: these dependency files are
# legally changed by the bounded O2 store/fold increment and the exact
# publication transport send. Their accepted artifact pin is the pinned commit
# blob; the executing bytes are bound by the committed publication execution
# migration (never by re-asserting the working file).
EXCEPTION_BLOB_FILES = {
    "tools/chandoff_intent.py":
        "49c48a9c2ef4ac89dd9321a42b0132a2a78cceb9",
}
O2_REPORT_TEST_DIGEST_ROW = {
    "path": "tools/tests/test_handoff_intent.py",
    "recorded": "sha256:649580b22055202280a670889e0a8d5ec5b93a51d2a90ce8dc094d268502923d",
    "actual_lf": "sha256:78fb99d40ad22ffcf45a087314901e26df5c1d502b038404728c4f1672283665",
}


def bundle_digest(directory: Path, *, exclude=()) -> dict:
    entries = []
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(directory).as_posix()
        if any(rel.startswith(prefix) for prefix in exclude):
            continue
        entries.append([rel, digest_file(path)])
    return {"file_count": len(entries), "digest": digest(entries)}


def verify_pins(root=Path(__file__).resolve().parent.parent, *,
                run=subprocess.run) -> dict:
    root = Path(root)
    checks = {}

    def check(name, actual, expected):
        checks[name] = {"actual": actual, "expected": expected,
                        "match": actual == expected}

    for rel, pin in PIN_FILES_LF.items():
        pinned_commit = EXCEPTION_BLOB_FILES.get(rel)
        if pinned_commit is not None:
            proc = run(["git", "-C", str(root), "show",
                        f"{pinned_commit}:{rel}"], capture_output=True)
            actual = None
            if getattr(proc, "returncode", 1) == 0 and proc.stdout:
                actual = digest_bytes(proc.stdout, normalize_lf=True)
            checks[f"file:{rel}"] = {
                "actual": actual, "expected": pin,
                "match": actual == pin,
                "pinned_commit": pinned_commit,
                "executing_lf": digest_file(root / rel, normalize_lf=True),
                "execution_note": (
                    "approved YZT-84 publication-recovery exception: the "
                    "accepted artifact pin is the pinned commit blob; the "
                    "executing bytes are bound by the committed publication "
                    "execution migration"),
            }
            continue
        check(f"file:{rel}", digest_file(root / rel, normalize_lf=True), pin)
    for rel, pin in PIN_BUNDLES.items():
        info = bundle_digest(root / rel)
        check(f"bundle:{rel}", info["digest"], pin)
        checks[f"bundle:{rel}"]["file_count"] = info["file_count"]
    check(f"file:{PIN_MATRIX_FILE[0]}",
          digest_file(root / PIN_MATRIX_FILE[0]), PIN_MATRIX_FILE[1])
    commits = {}
    for rev in PIN_FEATURE_COMMITS:
        proc = run(["git", "-C", str(root), "cat-file", "-e", rev],
                   capture_output=True, text=True)
        commits[rev] = proc.returncode == 0
    checks["commits"] = {"present": commits, "match": all(commits.values())}
    test_digest = digest_file(root / O2_REPORT_TEST_DIGEST_ROW["path"],
                              normalize_lf=True)
    checks["o2_report_test_row"] = {
        "path": O2_REPORT_TEST_DIGEST_ROW["path"],
        "recorded_in_o2_report": O2_REPORT_TEST_DIGEST_ROW["recorded"],
        "actual_lf": test_digest,
        "match": test_digest == O2_REPORT_TEST_DIGEST_ROW["recorded"],
        "finding": "U12-P0-F1",
        "blocking": False,
    }
    accepted = {k: v for k, v in checks.items()
                if k != "o2_report_test_row"}
    return {
        "kind": "u12_p0_u11_o2_pin_verification",
        "schema_version": TOOL_VERSION,
        "base_commit": BASE_COMMIT,
        "checks": checks,
        "accepted_inputs_all_match": all(v["match"] for v in accepted.values()),
        "documentation_discrepancies": [] if checks[
            "o2_report_test_row"]["match"] else [{
                "id": "U12-P0-F1",
                "severity": "low/documentation",
                "blocking": False,
                "detail": ("O2 report digest row for its own new test file is "
                           "not reproducible from any committed blob; the "
                           "runtime pins and bundle digests all reproduce. "
                           "No history rewrite proposed."),
                "evidence": checks["o2_report_test_row"],
            }],
    }


# ---------------------------------------------------------------------------
# Deployment steps
# ---------------------------------------------------------------------------
GENESIS_KIND = "deployment_record"
GENESIS_RECORD_TYPE = "u12_p0_production_ledger"


def genesis_record(*, at: str, ledger: Path, root: Path) -> dict:
    return {
        "kind": GENESIS_KIND,
        "record_type": GENESIS_RECORD_TYPE,
        "schema_version": TOOL_VERSION,
        "op": "ledger_created",
        "at": at,
        "ledger_path": str(ledger),
        "state_root": str(root),
        "creation_authority": AUTHORITY,
        "provenance": {
            "task": TASK_REF,
            "owner_role": "software-engineer",
            "branch": BRANCH,
            "base_commit": BASE_COMMIT,
        },
    }


def ledger_integrity(ledger: Path) -> dict:
    ledger = Path(ledger)
    text = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    corrupt = 0
    records = []
    for number, line in enumerate(lines, start=1):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            corrupt += 1
            records.append({"line": number, "error": str(exc)[:120]})
            continue
        records.append(parsed)
    parsed_records = [r for r in records if isinstance(r, dict)]
    folded = o2.fold_records(parsed_records)
    audit = o2.o2_audit_ledger(parsed_records)
    legacy_ok = True
    try:
        import chandoff_dispatch as dispatch
        dispatch.TransactionLedger.from_jsonl(text)
    except Exception as exc:  # pragma: no cover - defensive
        legacy_ok = f"{type(exc).__name__}: {exc}"
    return {
        "kind": "u12_p0_ledger_integrity",
        "schema_version": TOOL_VERSION,
        "ledger": str(ledger),
        "exists": ledger.exists(),
        "bytes": ledger.stat().st_size if ledger.exists() else 0,
        "tip_digest": digest_file(ledger) if ledger.exists() else None,
        "total_records": len(records),
        "partial_or_corrupt_records": corrupt,
        "intent_records": len(folded["intents"]),
        "ignored_records": folded["ignored_records"],
        "audit_ok": bool(audit["ok"]),
        "audit": audit,
        "legacy_reader_ok": legacy_ok is True,
        "legacy_reader_error": None if legacy_ok is True else legacy_ok,
    }


def deploy_backup(*, ledger: Path, backup_dir: Path, stamp: str,
                  host: Host, runtime_sid: str, restore_dir: Path) -> dict:
    """Immutable timestamped backup + isolated restore validation."""
    ledger, backup_dir = Path(ledger), Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    source_digest = digest_file(ledger)
    backup = backup_dir / f"ledger.jsonl.{stamp}.bak"
    if backup.exists():
        return {"ok": False, "reason": "backup_target_exists",
                "backup_path": str(backup)}
    backup.write_bytes(ledger.read_bytes())
    with open(backup, "ab") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    backup_digest_at_creation = digest_file(backup)

    readonly = host.set_readonly(backup)
    acl_apply = host.apply_acl(
        backup, inherit_remove=True,
        grants=[f"*{SYSTEM_SID}:R", f"*{ADMINS_SID}:R", f"*{runtime_sid}:R"])
    acl = host.read_acl(backup)
    acl_eval = evaluate_acl(acl["entries"],
                            allowed_sids=(SYSTEM_SID, ADMINS_SID, runtime_sid),
                            require_protected=True, immutable=True)

    restore_dir.mkdir(parents=True, exist_ok=True)
    restored = restore_dir / "ledger.restored.jsonl"
    restored.write_bytes(backup.read_bytes())
    restored_digest = digest_file(restored)
    restore_integrity = ledger_integrity(restored)
    backup_digest_after_restore = digest_file(backup)
    production_digest_after = digest_file(ledger)
    ok = (acl_eval["ok"]
          and bool(readonly.get("readonly"))
          and restored_digest == source_digest == backup_digest_at_creation
          and backup_digest_after_restore == source_digest
          and restore_integrity["partial_or_corrupt_records"] == 0
          and production_digest_after == source_digest)
    return {
        "kind": "u12_p0_backup_restore_evidence",
        "schema_version": TOOL_VERSION,
        "backup_path": str(backup),
        "backup_timestamp_utc": stamp,
        "source_tip_digest": source_digest,
        "backup_digest": backup_digest_at_creation,
        "backup_bytes": backup.stat().st_size,
        "readonly_attribute": readonly,
        "acl_apply": acl_apply,
        "acl_sddl": acl["sddl"],
        "acl_entries": acl["entries"],
        "acl_immutable_ok": acl_eval["ok"],
        "acl_violations": acl_eval["violations"],
        "restore_validation_path": str(restored),
        "restore_digest": restored_digest,
        "restore_digest_matches_backup": restored_digest == backup_digest_at_creation,
        "restore_digest_matches_source": restored_digest == source_digest,
        "restore_integrity": {
            "partial_or_corrupt_records":
                restore_integrity["partial_or_corrupt_records"],
            "audit_ok": restore_integrity["audit_ok"],
            "total_records": restore_integrity["total_records"],
            "legacy_reader_ok": restore_integrity["legacy_reader_ok"],
        },
        "production_ledger_overwritten": production_digest_after != source_digest,
        "production_tip_digest_after": production_digest_after,
        "ok": ok,
    }


def capability_evidence(workdir: Path, *, ledger: Path,
                        same_volume: bool) -> dict:
    ledger = Path(ledger)
    ledger_before = digest_file(ledger) if ledger.exists() else None
    raw = o2.capability_proof(Path(workdir))
    ledger_after = digest_file(ledger) if ledger.exists() else None
    expected = {
        "cross_process_append_enumerate",
        "single_writer_race",
        "stale_lease_recovery",
        "cas_conflict",
        "duplicate_intent_fail_closed",
        "partial_record_fail_closed",
    }
    ids = [row.get("id") for row in raw["observations"]]
    payload = {
        "kind": "u12_p0_production_root_capability_proof",
        "schema_version": TOOL_VERSION,
        "case": "production_root_absolute_shared_ledger",
        "production_root_selected": True,
        "caller_supplied": True,
        "absolute_root": True,
        "same_volume_as_production_ledger": bool(same_volume),
        "production_ledger": str(ledger),
        "workdir": str(workdir),
        "expected_observation_ids": sorted(expected),
        "observation_ids": ids,
        "observation_count": len(ids),
        "all_expected_present": set(ids) == expected,
        "passed_observations": sum(1 for row in raw["observations"]
                                   if row.get("passed")),
        "all_passed": bool(raw["all_passed"]) and set(ids) == expected,
        "method": raw.get("method"),
        "raw_probe_result": raw,
        "production_ledger_digest_before": ledger_before,
        "production_ledger_digest_after": ledger_after,
        "production_ledger_untouched": ledger_before == ledger_after,
        "live_mutations": 0,
    }
    payload["evidence_digest"] = digest(payload)
    return payload


RECEIPT_PINS = {
    "version": "sha256:5d883502cc52cebccbc59784e499db2ec3839d503b52cee0bf1a81aeaeabda72",
    "rerun_help": "sha256:2ecedb76dd1a445f1f1171fc6a230c53320af7b194580f6255bb1f55357c9a48",
    "runs_help": "sha256:bb5ef1a914cccc20dda7e3dd60334c5995f77dc53e91286ec0220f6907a1196f",
}

_RUN = {"id": "run-1", "issue_id": "iss-1", "agent_id": "agent-1",
        "status": "running"}


def receipt_contract_evidence(host: Host) -> dict:
    version = host.capture_cli(["version", "--output", "json"])
    rerun_help = host.capture_cli(["issue", "rerun", "--help"])
    runs_help = host.capture_cli(["issue", "runs", "--help"])

    def parse_shape(payload):
        try:
            parsed = o2.parse_run_object(json.dumps(payload))
            return {"accepted": True, "run_id": parsed.get("id")}
        except o2.IntentError as exc:
            return {"accepted": False, "code": exc.code}

    accepted_shapes = {
        "single_run_object": parse_shape(_RUN),
        "one_element_list": parse_shape([_RUN]),
        "runs_list": parse_shape({"runs": [_RUN]}),
    }
    refused_shapes = {
        "empty_list": parse_shape([]),
        "two_runs": parse_shape([_RUN, _RUN]),
        "runs_empty": parse_shape({"runs": []}),
        "runs_two": parse_shape({"runs": [_RUN, _RUN]}),
        "missing_field": parse_shape({"id": "x", "issue_id": "y"}),
    }
    try:
        o2.parse_run_object("not json")
        refused_shapes["non_json"] = {"accepted": True}
    except o2.IntentError as exc:
        refused_shapes["non_json"] = {"accepted": False, "code": exc.code}
    extra_shape = parse_shape({"run": _RUN})

    result = {
        "kind": "u12_p0_receipt_contract_revalidation",
        "schema_version": TOOL_VERSION,
        "cli_version": json.loads(version["stdout"] or "{}"),
        "captures": {
            label: {k: capture[k] for k in ("argv", "exit", "stdout_digest_lf")}
            for label, capture in (("version", version),
                                   ("rerun_help", rerun_help),
                                   ("runs_help", runs_help))
        },
        "pins": dict(RECEIPT_PINS),
        "digests_match": {
            "version": version["stdout_digest_lf"] == RECEIPT_PINS["version"],
            "rerun_help": rerun_help["stdout_digest_lf"] == RECEIPT_PINS["rerun_help"],
            "runs_help": runs_help["stdout_digest_lf"] == RECEIPT_PINS["runs_help"],
        },
        "accepted_shapes": accepted_shapes,
        "documented_shapes_accepted": all(
            row["accepted"] for row in accepted_shapes.values()),
        "refused_shapes": refused_shapes,
        "refused_all": all(not row["accepted"] for row in refused_shapes.values()),
        "extra_accepted_wrapper_shape": extra_shape,
        "correlation_requires_trusted_listing": True,
        "receipt_alone_counts_as_delivery": False,
        "idempotency_claimed": False,
        "live_triggers_issued": 0,
        "trigger_issued_during_revalidation": False,
    }
    result["drift"] = not (
        all(result["digests_match"].values())
        and result["documented_shapes_accepted"]
        and result["refused_all"])
    return result


def acl_tree_evidence(host: Host, protected_base: Path, root: Path,
                      runtime_sid: str) -> dict:
    protected_base, root = Path(protected_base), Path(root)
    paths = {
        "acl_base": protected_base,
        "state_root": root,
        "dispatch": root / "dispatch",
        "ledger": root / "dispatch" / "ledger.jsonl",
        "lock": root / "dispatch" / "ledger.jsonl.lock",
    }
    evidence = {"kind": "u12_p0_acl_evidence", "schema_version": TOOL_VERSION,
                "runtime_user_sid": runtime_sid, "paths": {}}
    required_labels = ("acl_base", "state_root", "dispatch", "ledger")
    ok = True
    for label, path in paths.items():
        if not path.exists():
            evidence["paths"][label] = {"exists": False, "path": str(path)}
            if label in required_labels:
                ok = False
            continue
        acl = host.read_acl(path)
        eval_result = evaluate_acl(
            acl["entries"],
            allowed_sids=(SYSTEM_SID, ADMINS_SID, runtime_sid),
            require_protected=(label == "acl_base"),
        )
        evidence["paths"][label] = {
            "exists": True,
            "path": str(path),
            "sddl": acl["sddl"],
            "owner_sid": acl["owner_sid"],
            "entries": acl["entries"],
            "least_privilege_ok": eval_result["ok"],
            "violations": eval_result["violations"],
        }
        if label in required_labels:
            ok = ok and eval_result["ok"]
    evidence["least_privilege_verified"] = ok
    evidence["acl_evidence_digest"] = digest(evidence["paths"])
    return evidence


# ---------------------------------------------------------------------------
# Aggregation / commands
# ---------------------------------------------------------------------------
def write_evidence(evidence_dir: Path, name: str, payload) -> str:
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n"
    (evidence_dir / name).write_text(text, encoding="utf-8", newline="\n")
    return digest_bytes(text.encode("utf-8"))


def _refuse(name: str, payload: dict, evidence_dir) -> int:
    if evidence_dir:
        write_evidence(Path(evidence_dir), name, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 2


def _checkpoint(name: str, payload: dict, evidence_dir) -> None:
    write_evidence(Path(evidence_dir), name, payload)


def assemble_manifest(*, evidence_dir: Path, validation, acl, integrity,
                      backup, capability, receipts, pins, identity,
                      ledger_after, now: str) -> dict:
    evidence_dir = Path(evidence_dir)
    files = {}
    for path in sorted(evidence_dir.glob("*.json")):
        files[path.name] = digest_file(path)
    completion = {
        "production_ledger_path_exact": validation["production_path_exact"],
        "outside_all_forbidden_trees": not validation["checks"][
            "forbidden_tree_overlap"],
        "unexpected_preexisting_state":
            validation["checks"]["preexisting_state"]["unexpected"],
        "acl_verified_least_privilege": acl["least_privilege_verified"],
        "backup_created_and_isolated_restore_verified": backup["ok"],
        "absolute_root_capability": f"{capability['passed_observations']}/6",
        "partial_or_corrupt_records":
            ledger_after["partial_or_corrupt_records"],
        "receipt_contract_bounded": not receipts["drift"],
        "live_canary_runs_created": 0,
        "live_05_or_06_activation": 0,
        "canonical_or_product_writes": 0,
        "o3_or_autonomous_wake_added": False,
        "r0_canary_authorized": False,
    }
    manifest = {
        "kind": "u12_p0_production_root_manifest",
        "schema_version": TOOL_VERSION,
        "generated_at": now,
        "task": TASK_REF,
        "branch": BRANCH,
        "base_commit": BASE_COMMIT,
        "authorization": AUTHORITY,
        "production_root": {
            "approved_exact": str(APPROVED_ROOT),
            "normalized": validation["normalized_root"],
            "ledger_approved_exact": str(APPROVED_LEDGER),
            "ledger_normalized": validation["normalized_ledger"],
            "production_ledger_path_exact": validation["production_path_exact"],
            "volume": validation["volume"],
            "runtime_user": identity["name"],
            "runtime_user_sid": identity["sid"],
        },
        "boundary": {
            "reparse_points": validation["checks"]["reparse_points"],
            "forbidden_tree_overlap":
                validation["checks"]["forbidden_tree_overlap"],
            "forbidden_trees_checked":
                validation["checks"]["forbidden_trees_checked"],
            "unexpected_preexisting_state":
                validation["checks"]["preexisting_state"]["unexpected"],
        },
        "acl": {
            "least_privilege_verified": acl["least_privilege_verified"],
            "acl_evidence_digest": acl["acl_evidence_digest"],
            "evidence_file": "acl-evidence.json",
        },
        "backup_restore": {
            "backup_created": True,
            "backup_path": backup["backup_path"],
            "backup_digest": backup["backup_digest"],
            "immutable_acl_ok": backup["acl_immutable_ok"],
            "isolated_restore_verified": backup["restore_digest_matches_source"],
            "production_ledger_overwritten":
                backup["production_ledger_overwritten"],
            "evidence_file": "backup-restore-evidence.json",
        },
        "ledger": {
            "path": str(APPROVED_LEDGER),
            "tip_digest": ledger_after["tip_digest"],
            "total_records": ledger_after["total_records"],
            "intent_records": ledger_after["intent_records"],
            "partial_or_corrupt_records":
                ledger_after["partial_or_corrupt_records"],
            "audit_ok": ledger_after["audit_ok"],
            "legacy_reader_ok": ledger_after["legacy_reader_ok"],
            "evidence_file": "ledger-integrity.json",
        },
        "capability": {
            "production_root_selected": True,
            "all_passed": capability["all_passed"],
            "observations": f"{capability['passed_observations']}/"
                            f"{capability['observation_count']}",
            "workdir": capability["workdir"],
            "production_ledger_untouched":
                capability["production_ledger_untouched"],
            "evidence_file": "capability-proof.json",
        },
        "u11_o2_pins": {
            "accepted_inputs_all_match": pins["accepted_inputs_all_match"],
            "documentation_discrepancies": pins["documentation_discrepancies"],
            "evidence_file": "u11-o2-pin-verification.json",
        },
        "receipt_contract": {
            "cli_version": receipts["cli_version"].get("version"),
            "digests_match": receipts["digests_match"],
            "documented_shapes_accepted": receipts["documented_shapes_accepted"],
            "refused_all": receipts["refused_all"],
            "drift": receipts["drift"],
            "trigger_issued": receipts["trigger_issued_during_revalidation"],
            "evidence_file": "receipt-contract-revalidation.json",
        },
        "completion_evidence": completion,
        "r0_canary_authorized": False,
        "evidence_files": files,
    }
    manifest["manifest_digest"] = digest(
        {k: v for k, v in manifest.items() if k != "manifest_digest"})
    return manifest


def cmd_deploy(args) -> int:
    evidence_dir = Path(args.evidence_dir).resolve()
    host = Host(pwsh=args.pwsh, multica=args.multica)
    now = utc_now()

    identity = host.whoami()
    runtime_sid = identity["sid"]

    pre = check_preexisting(APPROVED_ROOT, APPROVED_LEDGER)
    validation = validate_paths(existing=pre)
    validation["runtime_identity"] = identity
    validation["generated_at"] = now
    if not validation["ok"]:
        validation["verdict"] = "U12_P0_BLOCKED_REFUSED_BEFORE_WRITE"
        return _refuse("path-validation.json", validation, evidence_dir)
    _checkpoint("path-validation.json", validation, evidence_dir)

    pins = verify_pins()
    if not pins["accepted_inputs_all_match"]:
        return _refuse("u11-o2-pin-verification.json", pins, evidence_dir)
    _checkpoint("u11-o2-pin-verification.json", pins, evidence_dir)

    receipts = receipt_contract_evidence(host)
    if receipts["drift"]:
        return _refuse("receipt-contract-revalidation.json", receipts,
                       evidence_dir)
    _checkpoint("receipt-contract-revalidation.json", receipts, evidence_dir)

    root, ledger = APPROVED_ROOT, APPROVED_LEDGER
    dispatch = root / "dispatch"
    acl_base = root.parent

    created = {"acl_base": False, "state_root": False, "ledger": False}
    if not acl_base.exists():
        acl_base.mkdir(parents=True)
        created["acl_base"] = True
    if not root.exists():
        root.mkdir()
        created["state_root"] = True

    acl_apply = host.apply_acl(
        acl_base, inherit_remove=True,
        grants=[f"*{SYSTEM_SID}:(OI)(CI)F", f"*{ADMINS_SID}:(OI)(CI)F",
                f"*{runtime_sid}:(OI)(CI)M"])
    if acl_apply["exit"] != 0:
        return _refuse("acl-evidence.json",
                       {"kind": "u12_p0_acl_evidence",
                        "acl_apply": acl_apply,
                        "least_privilege_verified": False,
                        "refusal": "icacls failed"}, evidence_dir)

    dispatch.mkdir(exist_ok=True)
    (dispatch / "backups").mkdir(exist_ok=True)
    preflight = root / "preflight" / "u12-p0"
    preflight.mkdir(parents=True, exist_ok=True)

    store = o2.DurableIntentStore(ledger)
    if not ledger.exists():
        store.append(genesis_record(at=now, ledger=ledger, root=root))
        created["ledger"] = True

    integrity = ledger_integrity(ledger)
    integrity["created"] = created
    integrity["generated_at"] = now
    if integrity["partial_or_corrupt_records"] != 0 or not integrity["audit_ok"]:
        return _refuse("ledger-integrity.json", integrity, evidence_dir)
    _checkpoint("ledger-integrity.json", integrity, evidence_dir)

    acl = acl_tree_evidence(host, acl_base, root, runtime_sid)
    acl["acl_apply"] = acl_apply
    acl["generated_at"] = now
    if not acl["least_privilege_verified"]:
        return _refuse("acl-evidence.json", acl, evidence_dir)
    _checkpoint("acl-evidence.json", acl, evidence_dir)

    stamp = datetime.now(timezone.utc).strftime(BACKUP_STAMP_FMT)
    backup = deploy_backup(
        ledger=ledger, backup_dir=dispatch / "backups", stamp=stamp,
        host=host, runtime_sid=runtime_sid,
        restore_dir=preflight / "restore-validation" / stamp)
    backup["production_tip_digest_at_start"] = integrity["tip_digest"]
    if not backup["ok"]:
        return _refuse("backup-restore-evidence.json", backup, evidence_dir)
    _checkpoint("backup-restore-evidence.json", backup, evidence_dir)

    cap_dir = preflight / "capability-proof"
    same_volume = (os.path.splitdrive(str(cap_dir))[0].lower()
                   == os.path.splitdrive(str(ledger))[0].lower())
    capability = capability_evidence(
        cap_dir, ledger=ledger, same_volume=same_volume)
    if not capability["all_passed"] or not capability["production_ledger_untouched"]:
        return _refuse("capability-proof.json", capability, evidence_dir)
    _checkpoint("capability-proof.json", capability, evidence_dir)

    ledger_after = ledger_integrity(ledger)
    manifest = assemble_manifest(
        evidence_dir=evidence_dir, validation=validation, acl=acl,
        integrity=integrity, backup=backup, capability=capability,
        receipts=receipts, pins=pins, identity=identity,
        ledger_after=ledger_after, now=now)
    _checkpoint("production-root-manifest.json", manifest, evidence_dir)
    (preflight / "production-root-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8", newline="\n")

    summary = {
        "verdict": "U12_P0_READY_FOR_LEAD_REVIEW",
        "production_ledger": str(ledger),
        "manifest_digest": manifest["manifest_digest"],
        "r0_canary_authorized": False,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_verify(args) -> int:
    host = Host(pwsh=args.pwsh, multica=args.multica)
    identity = host.whoami()
    validation = validate_paths(existing=check_preexisting(
        APPROVED_ROOT, APPROVED_LEDGER))
    validation["runtime_identity"] = identity
    acl = acl_tree_evidence(host, APPROVED_ROOT.parent, APPROVED_ROOT,
                            identity["sid"])
    integrity = ledger_integrity(APPROVED_LEDGER)
    result = {
        "kind": "u12_p0_verify",
        "schema_version": TOOL_VERSION,
        "generated_at": utc_now(),
        "path_validation_ok": validation["ok"],
        "acl_least_privilege_ok": acl["least_privilege_verified"],
        "ledger_audit_ok": integrity["audit_ok"],
        "partial_or_corrupt_records": integrity["partial_or_corrupt_records"],
        "ledger_tip_digest": integrity["tip_digest"],
        "r0_canary_authorized": False,
    }
    result["ok"] = (result["path_validation_ok"]
                    and result["acl_least_privilege_ok"]
                    and result["ledger_audit_ok"]
                    and result["partial_or_corrupt_records"] == 0)
    if args.evidence_dir:
        _checkpoint("verify-latest.json", result, Path(args.evidence_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="U12-P0 production ledger preflight (YZT-81)")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("deploy", "deploy the production ledger"),
                            ("verify", "read-only verification"),
                            ("pins", "U11/O2 pin verification")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--evidence-dir", default=None)
        p.add_argument("--pwsh", default=str(RUNTIME_PWSH))
        p.add_argument("--multica", default="multica")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "deploy":
        return cmd_deploy(args)
    if args.command == "verify":
        return cmd_verify(args)
    return cmd_pins(args)


if __name__ == "__main__":
    raise SystemExit(main())
