"""U02/YZT-69 role-binding validator (fail-closed).

Proves, from repo data only:
1. the binding file covers exactly the six current logical roles
   (team-context/roles/*.yaml) — no missing, no extra, no retired role;
2. each role binds exactly once and no agent_id is used twice;
3. every bound agent_id exists (non-archived) in the frozen platform roster
   capture taken from the live workspace;
4. zero agent UUIDs appear inside Memory Core or Native API surfaces.

Exit 0 = all invariants hold; exit 1 with explicit failures otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BINDING = ROOT / "adapters" / "multica" / "role-bindings" / "role-bindings.v2.json"
ROSTER = ROOT / "adapters" / "multica" / "role-bindings" / "platform-roster-2026-09-10.json"
PROFILES = ROOT / "team-context" / "roles"
MEMORY_CORE_DIRS = ["team-context", "project-context", "memory", "chains", "sources", "schemas"]
NATIVE_API_DIRS = ["schemas/context-handoff"]

EXPECTED_ROLES = {
    "engineering-lead",
    "context-engineer",
    "solution-architect",
    "software-engineer",
    "delivery-reviewer",
    "qa",
}
RETIRED_ROLES = {"feature-reviewer", "ops-sre"}


def main() -> int:
    failures: list[str] = []

    binding = json.loads(BINDING.read_text(encoding="utf-8"))
    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    live = {a["agent_id"]: a for a in roster["agents"] if not a["archived"]}

    profile_roles = {p.stem for p in PROFILES.glob("*.yaml")}
    if profile_roles != EXPECTED_ROLES:
        failures.append(
            f"role profiles {sorted(profile_roles)} != expected V2.2 roster {sorted(EXPECTED_ROLES)}"
        )
    retired_leftover = profile_roles & RETIRED_ROLES
    if retired_leftover:
        failures.append(f"retired roles still have active profiles: {sorted(retired_leftover)}")

    bindings = binding.get("bindings") or []
    roles = [b.get("logical_role") for b in bindings]
    agent_ids = [b.get("agent_id") for b in bindings]
    if sorted(roles) != sorted(EXPECTED_ROLES):
        failures.append(f"binding roles {sorted(roles)} != expected roster {sorted(EXPECTED_ROLES)}")
    if len(set(agent_ids)) != len(agent_ids):
        dupes = sorted({a for a in agent_ids if agent_ids.count(a) > 1})
        failures.append(f"agent_id bound more than once: {dupes}")
    for b in bindings:
        role, aid = b.get("logical_role"), b.get("agent_id")
        if role in RETIRED_ROLES:
            failures.append(f"retired role present in bindings: {role}")
        if aid not in live:
            failures.append(f"binding agent {aid} ({role}) missing or archived in platform roster capture")
        elif live[aid]["name"] != b.get("agent_name"):
            failures.append(
                f"agent_name drift for {role}: binding={b.get('agent_name')!r} "
                f"roster={live[aid]['name']!r}"
            )

    uuids = [b["agent_id"] for b in bindings] + [
        a["agent_id"] for a in binding.get("non_team_agents") or []
    ]
    for rel in MEMORY_CORE_DIRS + NATIVE_API_DIRS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in {".yaml", ".yml", ".json", ".md", ".py", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for u in uuids:
                if u in text:
                    failures.append(f"agent UUID {u} leaked into {path.relative_to(ROOT).as_posix()}")

    if failures:
        print("ROLE-BINDING VALIDATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        "ROLE-BINDING VALIDATION OK: "
        f"{len(EXPECTED_ROLES)} roles bound exactly once; "
        f"{len(uuids)} UUIDs verified absent from Memory Core and Native API; "
        "roster capture matches bindings."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
