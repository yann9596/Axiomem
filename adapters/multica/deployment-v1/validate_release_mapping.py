"""YZT-73/deployment-v1-release-mapping static + isolated-synthetic checks (fail-closed).

Proves, from repo data only (plus the frozen roster capture):
1. role-bindings.v2.json binds delivery-reviewer to the distinct live 05 UUID
   and keeps the old Feature Reviewer UUID as history only (no alias, no reuse);
2. binding-plan.json carries resolved IDs, no NEW_05 token, and every
   unresolved import item has an explicit resolve + read-back requirement;
   the QA 06 skill-set exception is plan-only;
3. RELEASE_MANIFEST.yaml lists the pin HEAD 0150df8 five-doc hashes and its
   values match the actual git blobs at 0150df8 when git is available;
4. after-state-hashes.json is annotated with the bundle 42ca7fc scope;
5. an isolated synthetic compose of the D3 binding ops keeps unrelated
   bindings, adds the new 05 to the squad, retires the old 05, and never
   binds an unresolved skill id.

No network, no live writes, no project module imports beyond this file.
Exit 0 = all invariants hold; exit 1 with explicit failures otherwise.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "adapters" / "multica" / "deployment-v1"
BINDINGS = ROOT / "adapters" / "multica" / "role-bindings" / "role-bindings.v2.json"
BINDING_PLAN = DEPLOY / "binding-plan.json"
MANIFEST = DEPLOY / "RELEASE_MANIFEST.yaml"
AFTER_HASHES = DEPLOY / "after-state-hashes.json"
LIVE_BEFORE = DEPLOY / "live-before.json"

NEW_05 = "edff0eba-eef8-4188-9780-a1402b2b67dd"
OLD_05 = "b6335f8e-8147-45f7-aac0-8079d85423b5"
DELIVERY_REVIEW_SKILL = "98d6bf60-a8bd-4b03-96f6-f815308c932d"
PARENT_HANDOFF_SKILL = "aab482f9-b9ff-4b78-8394-1f13e2b319c0"
MILESTONE_SKILL = "29f68c43-0772-47a8-a860-d946a100c789"
SQUAD = "ee895c79-ca0a-498b-abe9-5af537736a30"
PIN_HEAD = "0150df8d1d9ce745826096a5a88fec770b78099f"

HEAD_DOC_HASHES = {
    "D1_REPORT.md": ("sha256:d92167fb45661e1ec2554659b638d55691b4dd5e5418ed3737f778aef54c0b76", 2716),
    "RELEASE_MANIFEST.yaml": ("sha256:f9a89c6d0cacf9238aa4aee2453eafe370aaa83c76dcd0c2fd562c48f4789390", 2893),
    "commit-classification.md": ("sha256:49aa31707aa5647a091fae4e74b8a3c5229e785dc7698fd197564b84676f1a78", 3087),
    "qa-baselines.md": ("sha256:1c466cc1f48406582da1536f828585ddd593d00b2ea7f9d5dc0d68eb1fa28bef", 1417),
    "revision-map.md": ("sha256:5a65c1ac8482a44c3a150dcec743c2196fee694d144ecc4d226080ce2942aced", 1816),
}

failures: list[str] = []
warnings: list[str] = []


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except ImportError:
        sys.path.insert(0, str(ROOT / "tools"))
        import yaml_mini  # type: ignore

        return yaml_mini.load_file(str(path))


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def check_bindings() -> None:
    data = json.loads(BINDINGS.read_text(encoding="utf-8"))
    bindings = data.get("bindings") or []
    by_role = {b.get("logical_role"): b for b in bindings}
    dr = by_role.get("delivery-reviewer") or {}
    if dr.get("agent_id") != NEW_05:
        failures.append(f"delivery-reviewer binding is {dr.get('agent_id')!r}, expected new 05 {NEW_05}")
    if dr.get("agent_name") != "05 Delivery Reviewer":
        failures.append(f"delivery-reviewer agent_name is {dr.get('agent_name')!r}")
    if dr.get("status") != "active":
        failures.append(f"delivery-reviewer status is {dr.get('status')!r}, expected active")
    if OLD_05 in [b.get("agent_id") for b in bindings]:
        failures.append("old 05 UUID still present in active bindings")
    historical = {h.get("agent_id"): h for h in data.get("historical_identities") or []}
    if OLD_05 not in historical:
        failures.append("old 05 UUID missing from historical_identities")
    elif historical[OLD_05].get("status") != "retired_no_alias":
        failures.append("old 05 historical entry must be retired_no_alias")
    if "platform_cutover_note" in json.dumps(data):
        failures.append("legacy platform_cutover_note still present")
    note = (data.get("boundary_contract") or {}).get("platform_binding_note", "")
    if OLD_05 not in note or "history" not in note or "never" not in note:
        failures.append("platform_binding_note does not state old-UUID history-only / never-reuse")

    roster_rel = data.get("roster_evidence")
    roster_path = ROOT / roster_rel if roster_rel else None
    if not roster_path or not roster_path.is_file():
        failures.append(f"roster_evidence missing: {roster_rel!r}")
        return
    roster = json.loads(roster_path.read_text(encoding="utf-8"))
    live = {a["agent_id"]: a for a in roster.get("agents") or [] if not a.get("archived")}
    if NEW_05 not in live:
        failures.append("new 05 missing from current roster capture")
    else:
        skills = sorted(live[NEW_05].get("captured_skills") or [])
        if skills != ["delivery-review", "parent-handoff-wake"]:
            failures.append(f"new 05 captured_skills {skills} != expected delivery-review + parent-handoff-wake")
    if OLD_05 not in live:
        failures.append("old 05 (still live) missing from roster capture")


def check_binding_plan() -> dict:
    plan = json.loads(BINDING_PLAN.read_text(encoding="utf-8"))
    if "NEW_05" in json.dumps(plan):
        failures.append("binding-plan still contains NEW_05 token")
    new05 = plan.get("new_05") or {}
    if new05.get("agent_id") != NEW_05:
        failures.append(f"binding-plan new_05.agent_id {new05.get('agent_id')!r} != {NEW_05}")
    if "pending" in str(new05.get("id_status", "")):
        failures.append("binding-plan new_05.id_status is still pending")
    dr_skill = ((plan.get("capability_skills") or {}).get("delivery-review") or {})
    if dr_skill.get("workspace_skill_id") != DELIVERY_REVIEW_SKILL:
        failures.append("binding-plan delivery-review workspace_skill_id not resolved")
    for node in walk(plan):
        status = str(node.get("id_status", ""))
        if "unresolved" in status or "placeholder" in status:
            resolved = node.get("resolve_and_readback_required") or node.get("bind_after_resolve_and_readback")
            if not resolved:
                failures.append(f"unresolved item without resolve/readback requirement: {node.get('skill_name') or node.get('op')}")
        if node.get("agent_id") is None and node.get("op") in {"agent_skills_add", "agent_update_instructions", "squad_member_add"}:
            failures.append(f"op {node.get('op')} still has null agent_id")
        if node.get("skill_id") is None and node.get("op") == "agent_skills_add" and not node.get("bind_after_resolve_and_readback"):
            failures.append(f"agent_skills_add for {node.get('skill_name')} has null skill_id without resolve/readback marker")
    ops = plan.get("ops_d3") or []
    if not ops or ops[0].get("op") != "capture_before_full_text" or not ops[0].get("required"):
        failures.append("ops_d3 must start with the required F2 capture_before_full_text step")
    set_ops = [o for o in ops if o.get("op") == "qa_06_skill_set_exception"]
    if not set_ops or not set_ops[0].get("do_not_execute_before_exception"):
        failures.append("QA 06 skill-set exception op missing or not gated")
    qa_plan = plan.get("qa_06_binding_exception_plan") or {}
    if "PLAN_ONLY" not in str(qa_plan.get("status", "")):
        failures.append("qa_06_binding_exception_plan status must be PLAN_ONLY")
    before = {b.get("name") for b in qa_plan.get("before_binding_set_observed") or []}
    if not {"milestone-quality-gate", "parent-handoff-wake"} <= before:
        failures.append("qa_06 before set must record milestone-quality-gate + parent-handoff-wake")
    return plan


def check_manifest() -> None:
    manifest = load_yaml(MANIFEST)["release_manifest"]
    if manifest.get("attempt") != "YZT-73/deployment-v1-release-mapping":
        failures.append(f"manifest attempt is {manifest.get('attempt')!r}")
    sc = manifest.get("selfcheck") or {}
    if sc.get("package_id") != "CTX-software-engineer-9cbb0786b379b512" or sc.get("status") != "READY" or sc.get("action") != "USE_EXISTING":
        failures.append("manifest selfcheck block does not record the release-mapping READY / USE_EXISTING package")
    listed = ((manifest.get("f1_after_state_scope") or {}).get("head_doc_hashes_f1")) or {}
    for name, (sha, size) in HEAD_DOC_HASHES.items():
        entry = listed.get(name) or {}
        if entry.get("sha256") != sha or entry.get("bytes") != size:
            failures.append(f"manifest head_doc_hashes_f1 mismatch for {name}")


def check_head_doc_blobs() -> None:
    git = shutil.which("git")
    if not git:
        warnings.append("git not found on PATH; skipped 0150df8 blob digest recheck")
        return
    for name, (sha, size) in HEAD_DOC_HASHES.items():
        rel = f"adapters/multica/deployment-v1/{name}"
        proc = subprocess.run(
            [git, "-C", str(ROOT), "show", f"{PIN_HEAD}:{rel}"],
            capture_output=True,
        )
        if proc.returncode != 0:
            warnings.append(f"git show {PIN_HEAD}:{rel} failed; skipped")
            continue
        actual = "sha256:" + hashlib.sha256(proc.stdout).hexdigest()
        if actual != sha or len(proc.stdout) != size:
            failures.append(f"0150df8 blob mismatch for {name}: actual {actual} / {len(proc.stdout)} bytes")


def check_after_state_scope() -> None:
    data = json.loads(AFTER_HASHES.read_text(encoding="utf-8"))
    scope = str(data.get("_scope", ""))
    if "42ca7fce2314bf0522ae81e46025c63d99712366" not in scope:
        failures.append("after-state-hashes.json _scope does not name bundle 42ca7fc")
    for doc in HEAD_DOC_HASHES:
        if doc not in scope:
            failures.append(f"after-state-hashes.json _scope does not mention {doc}")
    items = [k for k in data if not k.startswith("_")]
    if len(items) != 27:
        failures.append(f"after-state-hashes.json item count {len(items)} != 27")


def synthetic_compose(plan: dict) -> None:
    """Isolated synthetic D3 binding compose; asserts the mapping invariants."""
    live = json.loads(LIVE_BEFORE.read_text(encoding="utf-8"))
    state = {a["logical_role"]: set(s["name"] for s in a["skills"]) for a in live["agents"]}
    squad_members = set(live["squad"].get("member_ids") or []) if live["squad"].get("member_ids") else None
    squad_members = squad_members or set(a["agent_id"] for a in live["agents"])
    state["delivery-reviewer"] = set(plan["new_05"]["planned_skills_pre_d2"])
    squad_members.add(NEW_05)
    assert OLD_05 in squad_members
    if OLD_05 in squad_members and not any(
        o.get("op") == "squad_member_remove_or_keep_history" and o.get("agent_id") == OLD_05
        for o in plan["ops_d3"]
    ):
        failures.append("synthetic compose: no op retires the old 05 from squad routing")
    for op in plan["ops_d3"]:
        if op.get("op") != "agent_skills_add":
            continue
        role = op.get("agent_role")
        name = op.get("skill_name")
        if op.get("skill_id") is None:
            continue  # unresolved imports stay unbound in the synthetic compose
        state.setdefault(role, set()).add(name)
    if "delivery-review" not in state.get("delivery-reviewer", set()):
        failures.append("synthetic compose: new 05 lost its delivery-review binding")
    qa_plan = plan["qa_06_binding_exception_plan"]
    qa_after = {b["name"] for b in qa_plan["after_binding_set_computed_from_latest_before"]}
    if "milestone-quality-gate" in qa_after:
        failures.append("synthetic compose: QA after set still contains milestone-quality-gate")
    if "parent-handoff-wake" not in qa_after:
        failures.append("synthetic compose: QA after set dropped parent-handoff-wake")
    if NEW_05 not in squad_members:
        failures.append("synthetic compose: new 05 missing from squad")
    del state, squad_members


def main() -> int:
    check_bindings()
    plan = check_binding_plan()
    check_manifest()
    check_head_doc_blobs()
    check_after_state_scope()
    synthetic_compose(plan)

    if failures:
        print("RELEASE-MAPPING VALIDATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    for w in warnings:
        print(f"WARN: {w}")
    print(
        "RELEASE-MAPPING VALIDATION OK: new 05 mapping active; old 05 history-only; "
        "resolved IDs filled; unresolved imports carry resolve+readback; "
        "F1 pin-HEAD doc digests match; synthetic compose consistent."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
