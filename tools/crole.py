#!/usr/bin/env python3
"""Role Context Profile application for V1.1 assembly (YZT-42).

Role policy is a default retrieval policy, not an ACL. It must change at least
one content dimension (checkpoint slice, object selection, ranking, or budget).
"""
from __future__ import annotations


def policy_of(profile: dict) -> dict:
    policy = profile.get("retrieval_policy")
    if not isinstance(policy, dict):
        raise ValueError(f"role profile {profile.get('role')!r} missing retrieval_policy")
    for req in ("baseline_context", "preferred_memory", "conditional_memory",
                "default_exclude", "priority"):
        if req not in policy:
            raise ValueError(f"retrieval_policy missing {req}")
    return policy


def _needles(policy: dict, *fields: str) -> set[str]:
    out: set[str] = set()
    use = fields or ("baseline_context", "preferred_memory", "conditional_memory")
    for field in use:
        for item in policy.get(field) or []:
            if isinstance(item, str) and item.strip() and item.strip() != "nothing":
                out.add(item.strip())
    return out


def default_excludes(policy: dict) -> set[str]:
    return {x.strip() for x in (policy.get("default_exclude") or []) if isinstance(x, str)}


def excludes_nothing(policy: dict) -> bool:
    ex = default_excludes(policy)
    return ex == {"nothing"}


def wants_team_posture(policy: dict) -> bool:
    needles = _needles(policy, "baseline_context")
    return any(n == "team.checkpoint" or n.startswith("team.checkpoint.posture")
               for n in needles)


def wants_team_slice(policy: dict) -> bool:
    needles = _needles(policy, "baseline_context")
    return any(n in ("team.checkpoint", "team.checkpoint.task_relevant_slice")
               for n in needles)


def wants_project_slice(policy: dict) -> bool:
    baseline = _needles(policy, "baseline_context")
    if "project_internal_detail" in default_excludes(policy):
        return any("current_project.checkpoint" in n for n in baseline)
    needles = _needles(policy)
    return any("current_project.checkpoint" in n for n in needles)


def wants_team_conflicts(policy: dict) -> bool:
    needles = _needles(policy)
    return "blocking_issues.open_conflicts" in needles or wants_team_posture(policy)


def relevance_required(policy: dict, kind: str) -> bool:
    if excludes_nothing(policy):
        return False
    needles = _needles(policy)
    bases = {
        "rule": ("current_project.rules", "team.rules"),
        "current_fact": ("current_project.current_facts",),
        "case": ("current_project.cases",),
    }.get(kind, ())
    for base in bases:
        if base in needles:
            return False
        if f"{base}.relevant" in needles or f"{base}.metadata" in needles:
            return True
    return True


def kind_limit(policy: dict, kind: str, default: int) -> int:
    """Budget: preferred-memory order can enlarge or shrink a kind's cap."""
    preferred = [x.strip() for x in (policy.get("preferred_memory") or [])
                 if isinstance(x, str)]
    kind_tokens = {
        "rule": "rules",
        "current_fact": "current_facts",
        "case": "cases",
        "checkpoint": "checkpoint",
    }
    token = kind_tokens.get(kind, kind)
    hits = [i for i, item in enumerate(preferred) if token in item]
    if not hits:
        return max(1, default // 2)
    if min(hits) == 0:
        return default
    return max(2, default)


def role_rank(doc: dict, role: str) -> tuple:
    relevant = doc.get("relevant_roles") or []
    listed = 0 if role in relevant else 1
    return (listed, doc.get("id") or "")


def posture_entries(team_checkpoint: dict) -> list[dict]:
    out = []
    for proj in team_checkpoint.get("projects") or []:
        pid = proj.get("project_id")
        if not pid:
            continue
        out.append({
            "checkpoint": "team",
            "section": "posture",
            "id": f"posture-{pid}",
            "summary": f"{pid}: phase={proj.get('phase')} (derived from registry)",
            "refs": [f"registry://{pid}"],
        })
    return out


def content_fingerprint(pkg: dict) -> dict:
    """Content-level signature. Excludes role labels and timestamps."""
    def slice_fp(entries):
        return [(e.get("id"), e.get("checkpoint"), e.get("section"),
                 (e.get("summary") or "")[:80])
                for e in entries or []]

    return {
        "team_state_slice": slice_fp(pkg.get("team_state_slice")),
        "project_state_slice": slice_fp(pkg.get("project_state_slice")),
        "rules": [r.get("ref") for r in pkg.get("rules") or []],
        "current_facts": [f.get("ref") for f in pkg.get("current_facts") or []],
        "cases": [c.get("ref") for c in pkg.get("cases") or []],
        "open_conflicts": [c.get("id") for c in pkg.get("open_conflicts") or []],
        "blocked_by": list(pkg.get("blocked_by") or []),
        "source_refs": list(pkg.get("source_refs") or []),
    }


def role_content_differs(pkg_a: dict, pkg_b: dict) -> bool:
    return content_fingerprint(pkg_a) != content_fingerprint(pkg_b)
