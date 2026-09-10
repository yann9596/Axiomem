#!/usr/bin/env python3
"""Artifact Contract Runtime (U10 / YZT-71).

Framework-neutral, outside Memory Core and outside the Frozen T00 Public
Schema. Exact identity/version resolution, type-level semantic validation,
ARTIFACT_READY_CHECK, deterministic dependency export into existing T00
task_evidence / source_refs, stale/supersede invalidation, and Option A
R0/R1/R2 routing contracts.

This module never writes canonical memory, never amends T00 schemas, never
creates a dispatch database, and never creates or triggers Review/QA work.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from schema_mini import Schema, load_schema_file  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_DIR = ROOT / "schemas" / "artifact-contract"
TOOLS = Path(__file__).resolve().parent

SCHEMA_VERSION = "1.0"
CORE_ARTIFACT_TYPES = (
    "issue_definition",
    "context_package",
    "product_expectation",
    "design_baseline",
    "implementation",
    "delivery_review",
    "qa_acceptance",
)
STATUSES = (
    "draft",
    "ready",
    "ready_for_review",
    "changes_required",
    "accepted",
    "superseded",
    "stale",
)
REVIEW_LEVELS = ("R0", "R1", "R2")
INPUT_BLOCKED_STATUSES = frozenset({"draft", "changes_required", "superseded", "stale"})
FORBIDDEN_VERSION_TOKENS = frozenset({
    "latest", "current", "newest", "head", "any", "*",
    "当前", "最新", "当前代码",
})
PROSE_VERSION_HINTS = ("大概", "左右", "差不多")
REF_GRAMMAR = re.compile(r"^(multica|adr|doc|repo|registry|project|git)://\S+$")
DELIVERY_VERDICTS = frozenset({"APPROVE", "CHANGES_REQUIRED", "ESCALATE"})
QA_VERDICTS = frozenset({"PASS", "CONDITIONAL_PASS", "FAIL"})

# U10 never dispatches. Kept as an explicit invariant for routing results.
TRIGGERS_CREATED = 0


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_name: str, instance) -> list:
    file_part, _, ptr = schema_name.partition("#")
    schema_doc = load_schema_file(file_part)
    if ptr:
        cur = schema_doc
        for raw in ptr.strip("/").split("/"):
            cur = cur[raw]
        return Schema(cur, schema_doc).validate(instance, path="$")
    return Schema(schema_doc, schema_doc).validate(instance, path="$")


def load_catalog() -> dict:
    doc = _load_json(CONTRACT_DIR / "catalog.json")
    errs = _validate("artifact-contract/catalog.schema.json", doc)
    if errs:
        raise ValueError("catalog invalid: " + "; ".join(errs))
    types = [t["artifact_type"] for t in doc["types"]]
    if tuple(types) != CORE_ARTIFACT_TYPES:
        raise ValueError(f"catalog types {types} != {list(CORE_ARTIFACT_TYPES)}")
    return doc


def load_routing_contracts() -> dict:
    doc = _load_json(CONTRACT_DIR / "routing.json")
    errs = _validate("artifact-contract/routing.schema.json", doc)
    if errs:
        raise ValueError("routing contracts invalid: " + "; ".join(errs))
    for level, spec in doc["levels"].items():
        if spec["producer_auto_triggers_delivery_reviewer"]:
            raise ValueError(f"{level} forbids producer auto-trigger")
        if spec["delivery_reviewer_auto_triggers_qa"]:
            raise ValueError(f"{level} forbids reviewer auto-trigger of QA")
    return doc


def catalog_entry(artifact_type: str, catalog: dict | None = None) -> dict:
    cat = catalog if catalog is not None else load_catalog()
    for row in cat["types"]:
        if row["artifact_type"] == artifact_type:
            return row
    raise KeyError(f"unknown artifact_type: {artifact_type!r}")


def artifact_contract_revision() -> str:
    """Content digest of the machine-readable contract + executable runtime."""
    entries = []
    for path in sorted(CONTRACT_DIR.iterdir()):
        if path.name == "README.md" or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            payload = canonical_json(json.loads(text))
        else:
            payload = text.replace("\r\n", "\n")
        rel = path.relative_to(ROOT).as_posix()
        entries.append([rel, payload])
    py = (TOOLS / "cartifact.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    entries.append(["tools/cartifact.py", py])
    entries.sort()
    return sha256_text(canonical_json(entries))


def artifact_ref_str(ref: dict) -> str:
    return f"{ref.get('artifact_type','')}:{ref.get('artifact_id','')}@{ref.get('version','')}"


def is_exact_version(value) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or v != value.strip():
        return False
    if v.lower() in FORBIDDEN_VERSION_TOKENS or v in FORBIDDEN_VERSION_TOKENS:
        return False
    if any(h in v for h in PROSE_VERSION_HINTS):
        return False
    if re.search(r"\s", v):
        return False
    return True


def _semantic_present(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        if value.get("not_applicable") is True:
            return bool(str(value.get("reason") or "").strip())
        return bool(value)
    if isinstance(value, list):
        return True  # empty list is an explicit answer (none)
    if isinstance(value, bool):
        return True
    return True


def _na_forbidden(value) -> bool:
    return isinstance(value, dict) and value.get("not_applicable") is True


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------

def validate_envelope(envelope: dict, catalog: dict | None = None,
                      review_level: str | None = None) -> list:
    """Return error dicts. Empty list means the envelope is machine-valid."""
    errors = []
    schema_errs = _validate("artifact-contract/common-envelope.schema.json", envelope)
    for e in schema_errs:
        errors.append({"code": "ENVELOPE_SCHEMA", "message": e})
    if schema_errs:
        return errors
    if not is_exact_version(envelope.get("version")):
        errors.append({
            "code": "VERSION_NOT_EXACT",
            "message": (
                f"version {envelope.get('version')!r} is not an exact token "
                "(latest / current / free-prose are forbidden)"
            ),
        })
    try:
        entry = catalog_entry(envelope["artifact_type"], catalog)
    except KeyError as exc:
        errors.append({"code": "UNKNOWN_TYPE", "message": str(exc)})
        return errors
    if envelope.get("owner_role") != entry["owner_role"]:
        errors.append({
            "code": "OWNER_ROLE_MISMATCH",
            "message": (
                f"owner_role {envelope.get('owner_role')!r} != catalog "
                f"{entry['owner_role']!r}"
            ),
        })
    errors.extend(_validate_relations(envelope, entry))
    errors.extend(validate_semantics(envelope, catalog, review_level))
    return errors


def _validate_relations(envelope: dict, entry: dict) -> list:
    errors = []
    rules = entry["relation_rules"]
    based = envelope.get("based_on") or []
    if len(based) < rules["based_on_min"]:
        errors.append({
            "code": "BASED_ON_MISSING",
            "message": (
                f"{envelope['artifact_type']} requires at least "
                f"{rules['based_on_min']} based_on ref(s)"
            ),
        })
    for ref in based:
        if not is_exact_version(ref.get("version")):
            errors.append({
                "code": "BASED_ON_VERSION_NOT_EXACT",
                "message": f"based_on {artifact_ref_str(ref)} is not exact",
            })
    if rules["requires_reviewed_artifact"] and not envelope.get("reviewed_artifact"):
        errors.append({
            "code": "REVIEWED_ARTIFACT_MISSING",
            "message": "delivery_review requires reviewed_artifact with exact version",
        })
    reviewed = envelope.get("reviewed_artifact")
    if reviewed and not is_exact_version(reviewed.get("version")):
        errors.append({
            "code": "REVIEWED_ARTIFACT_VERSION_NOT_EXACT",
            "message": f"reviewed_artifact {artifact_ref_str(reviewed)} is not exact",
        })
    if rules["requires_validated_against"]:
        have = envelope.get("validated_against") or []
        have_types = {r.get("artifact_type") for r in have}
        needed = set(rules.get("validated_against_types") or [])
        missing = sorted(needed - have_types)
        if missing:
            errors.append({
                "code": "VALIDATED_AGAINST_MISSING",
                "message": (
                    "qa_acceptance requires validated_against types: "
                    + ", ".join(missing)
                ),
            })
        for ref in have:
            if not is_exact_version(ref.get("version")):
                errors.append({
                    "code": "VALIDATED_AGAINST_VERSION_NOT_EXACT",
                    "message": f"validated_against {artifact_ref_str(ref)} is not exact",
                })
    if envelope.get("supersedes"):
        sup = envelope["supersedes"]
        if not is_exact_version(sup.get("version")):
            errors.append({
                "code": "SUPERSEDES_VERSION_NOT_EXACT",
                "message": f"supersedes {artifact_ref_str(sup)} is not exact",
            })
    return errors


def validate_semantics(envelope: dict, catalog: dict | None = None,
                       review_level: str | None = None) -> list:
    errors = []
    entry = catalog_entry(envelope["artifact_type"], catalog)
    semantics = envelope.get("semantics")
    if not isinstance(semantics, dict):
        return [{"code": "SEMANTICS_MISSING", "message": "semantics must be an object"}]
    if envelope["artifact_type"] == "context_package" and isinstance(semantics.get("package"), dict):
        pkg_errs = _validate("context-package.schema.json", semantics["package"])
        for e in pkg_errs:
            errors.append({"code": "CONTEXT_PACKAGE_SCHEMA", "message": e})
        # T00 package already answers the semantic contract; extra keys optional.
        if not pkg_errs:
            return errors
    required_keys = []
    if review_level == "R0":
        required_keys = list(entry["r0_required_keys"])
    else:
        required_keys = [k["key"] for k in entry["semantic_keys"] if k["required"]]
    for key in required_keys:
        if key not in semantics or not _semantic_present(semantics.get(key)):
            errors.append({
                "code": "SEMANTIC_KEY_MISSING",
                "message": f"required semantic key {key!r} is missing or empty",
                "key": key,
            })
        elif review_level != "R0" and _na_forbidden(semantics.get(key)):
            # required keys may not hide behind not_applicable
            spec = next((k for k in entry["semantic_keys"] if k["key"] == key), None)
            if spec and spec["required"]:
                errors.append({
                    "code": "SEMANTIC_KEY_NOT_APPLICABLE",
                    "message": f"required semantic key {key!r} cannot be not_applicable",
                    "key": key,
                })
    if envelope["artifact_type"] == "delivery_review":
        verdict = semantics.get("verdict")
        if verdict not in DELIVERY_VERDICTS:
            errors.append({
                "code": "VERDICT_INVALID",
                "message": f"delivery_review verdict {verdict!r} not in {sorted(DELIVERY_VERDICTS)}",
            })
    if envelope["artifact_type"] == "qa_acceptance":
        verdict = semantics.get("verdict")
        if verdict not in QA_VERDICTS:
            errors.append({
                "code": "VERDICT_INVALID",
                "message": f"qa_acceptance verdict {verdict!r} not in {sorted(QA_VERDICTS)}",
            })
    return errors


# ---------------------------------------------------------------------------
# Store and version resolution
# ---------------------------------------------------------------------------

class ArtifactStore:
    """Caller-supplied envelope collection. Not a dispatch or memory database."""

    def __init__(self, envelopes=None):
        self.envelopes: list = []
        for env in envelopes or []:
            self.add(env, validate=False)

    def add(self, envelope: dict, validate: bool = True) -> None:
        if validate:
            errs = validate_envelope(envelope)
            if errs:
                raise ValueError("invalid envelope: " + "; ".join(e["message"] for e in errs))
        key = (envelope["artifact_id"], envelope["version"])
        existing = self._find_key(key)
        if existing is not None:
            raise ValueError(
                f"ambiguous identity: {envelope['artifact_id']}@{envelope['version']} "
                "already present"
            )
        same_id = [e for e in self.envelopes if e["artifact_id"] == envelope["artifact_id"]]
        for e in same_id:
            if e["artifact_type"] != envelope["artifact_type"]:
                raise ValueError(
                    f"artifact_id {envelope['artifact_id']!r} already bound to type "
                    f"{e['artifact_type']!r}"
                )
        self.envelopes.append(copy.deepcopy(envelope))

    def _find_key(self, key) -> dict | None:
        aid, ver = key
        hits = [e for e in self.envelopes
                if e["artifact_id"] == aid and e["version"] == ver]
        if len(hits) > 1:
            raise ValueError(f"store corrupt: multiple rows for {aid}@{ver}")
        return hits[0] if hits else None

    def get(self, artifact_id: str, version: str) -> dict | None:
        return self._find_key((artifact_id, version))

    def all_for(self, artifact_id: str) -> list:
        return [e for e in self.envelopes if e["artifact_id"] == artifact_id]


def resolve_exact(store: ArtifactStore, artifact_id: str, version,
                  artifact_type: str | None = None) -> dict:
    """Fail closed unless one exact envelope matches."""
    if not is_exact_version(version):
        return {
            "ok": False,
            "envelope": None,
            "code": "VERSION_NOT_EXACT",
            "message": (
                f"refused to resolve {artifact_id}@{version!r}: "
                "latest / current / missing / free-prose versions fail closed"
            ),
        }
    hits = [e for e in store.envelopes
            if e["artifact_id"] == artifact_id and e["version"] == version]
    if artifact_type:
        typed = [e for e in hits if e["artifact_type"] == artifact_type]
        if hits and not typed:
            return {
                "ok": False,
                "envelope": None,
                "code": "WRONG_TYPE",
                "message": (
                    f"{artifact_id}@{version} has type "
                    f"{hits[0]['artifact_type']!r}, expected {artifact_type!r}"
                ),
            }
        hits = typed
    if not hits:
        return {
            "ok": False,
            "envelope": None,
            "code": "MISSING",
            "message": f"no envelope for {artifact_id}@{version}",
        }
    if len(hits) > 1:
        return {
            "ok": False,
            "envelope": None,
            "code": "AMBIGUOUS",
            "message": f"multiple envelopes for {artifact_id}@{version}",
        }
    return {"ok": True, "envelope": hits[0], "code": "OK", "message": "resolved"}


def authoritative_version(store: ArtifactStore, artifact_id: str,
                          artifact_type: str | None = None) -> dict:
    """Unique current (not superseded, not stale) version of an identity."""
    rows = store.all_for(artifact_id)
    if artifact_type:
        rows = [e for e in rows if e["artifact_type"] == artifact_type]
    live = [e for e in rows if e["status"] not in {"superseded", "stale"}]
    if not live:
        return {
            "ok": False,
            "envelope": None,
            "version": None,
            "code": "MISSING",
            "message": f"no authoritative version for {artifact_id}",
        }
    if len(live) > 1:
        versions = sorted({e["version"] for e in live})
        return {
            "ok": False,
            "envelope": None,
            "version": None,
            "code": "AMBIGUOUS",
            "message": (
                f"authoritative version for {artifact_id} is ambiguous: {versions}"
            ),
        }
    env = live[0]
    return {
        "ok": True,
        "envelope": env,
        "version": env["version"],
        "code": "OK",
        "message": "resolved",
    }


# ---------------------------------------------------------------------------
# Dependency set / T00 export
# ---------------------------------------------------------------------------

def normalize_requirements(requirements) -> list:
    out = []
    for raw in requirements or []:
        rec = {
            "artifact_type": raw["artifact_type"],
            "artifact_id": raw["artifact_id"],
            "version": raw.get("version") if raw.get("version") is not None else "",
            "required": bool(raw.get("required", True)),
        }
        if raw.get("ref"):
            rec["ref"] = raw["ref"]
        out.append(rec)
    out.sort(key=lambda r: (r["artifact_type"], r["artifact_id"], r["version"],
                            not r["required"]))
    return out


def dependency_digest(requirements) -> str:
    payload = [{"artifact_type": r["artifact_type"],
                "artifact_id": r["artifact_id"],
                "version": r["version"],
                "required": r["required"]}
               for r in normalize_requirements(requirements)]
    return sha256_text(canonical_json(payload))


def dependency_changed(previous_digest: str, requirements) -> bool:
    return previous_digest != dependency_digest(requirements)


def export_t00_surfaces(store: ArtifactStore | None, requirements) -> dict:
    """Export through existing package.task_evidence / package.source_refs only."""
    deps = normalize_requirements(requirements)
    digest = dependency_digest(deps)
    evidence = []
    source_refs = []
    for rec in deps:
        item = {
            "kind": "artifact_dependency",
            "artifact_id": rec["artifact_id"],
            "artifact_type": rec["artifact_type"],
            "version": rec["version"],
            "required": rec["required"],
        }
        locator = rec.get("ref")
        if store is not None:
            env = store.get(rec["artifact_id"], rec["version"])
            if env and env.get("locator"):
                locator = locator or env["locator"]
        if locator:
            item["ref"] = locator
            if REF_GRAMMAR.match(locator):
                source_refs.append(locator)
        evidence.append(item)
    evidence.append({
        "kind": "artifact_dependency_set",
        "dependency_digest": digest,
        "count": len(deps),
    })
    source_refs = sorted(set(source_refs))
    return {
        "task_evidence": evidence,
        "source_refs": source_refs,
        "dependency_digest": digest,
        "kind": "artifact_dependency_set",
        "schema_version": SCHEMA_VERSION,
        "dependencies": deps,
    }


# ---------------------------------------------------------------------------
# ARTIFACT_READY_CHECK
# ---------------------------------------------------------------------------

def _failure(req, code, reason, owner, route, extra=None) -> dict:
    row = {
        "artifact_id": req.get("artifact_id") or "",
        "artifact_type": req.get("artifact_type") or "",
        "version": req.get("version") if req.get("version") is not None else "",
        "ref": extra.get("ref") if extra and extra.get("ref") else artifact_ref_str(req),
        "reason_code": code,
        "reason": reason,
        "correction_owner": owner,
        "route": route,
    }
    return row


def _implied_requirements(request: dict, catalog: dict, routing: dict) -> list:
    """R1/R2 implied baselines. Missing implied types fail closed."""
    extra = []
    level = request.get("review_level")
    role = request.get("target_role")
    if level not in REVIEW_LEVELS:
        return extra
    spec = routing["levels"][level]
    have_types = {r.get("artifact_type") for r in request.get("requirements") or []}
    needed = []
    if role == "delivery-reviewer":
        needed = list(spec["required_review_input_types"])
    if role == "qa":
        needed = list(spec["required_qa_baseline_types"])
    for t in needed:
        if t not in have_types:
            extra.append({
                "artifact_type": t,
                "artifact_id": "",
                "version": "",
                "required": True,
                "_implied": True,
            })
    return extra


def artifact_ready_check(store: ArtifactStore, request: dict,
                         catalog: dict | None = None,
                         routing: dict | None = None) -> dict:
    cat = catalog if catalog is not None else load_catalog()
    routing = routing if routing is not None else load_routing_contracts()
    req_errs = _validate(
        "artifact-contract/readiness.schema.json#/$defs/request", request)
    failures = []
    if req_errs:
        for e in req_errs:
            failures.append(_failure(
                {"artifact_id": "", "artifact_type": "", "version": ""},
                "REQUEST_INVALID", e, "engineering-lead", "lead_replan",
            ))
        return _ready_result(failures, request.get("requirements") or [])

    checks = {
        "expected_artifact_type_present": True,
        "authoritative_version_resolved": True,
        "status_allowed_for_target_role": True,
        "superseded": False,
        "stale": False,
        "required_semantics_present": True,
        "upstream_refs_resolvable": True,
    }
    target_role = request["target_role"]
    review_level = request.get("review_level")
    requirements = list(request.get("requirements") or [])
    implied = _implied_requirements(request, cat, routing)
    for row in implied:
        failures.append(_failure(
            row, "QA_GATE_BLOCKED" if target_role == "qa" else "REQUIRED_TYPE_MISSING",
            (
                f"{target_role} at {review_level} requires exact "
                f"{row['artifact_type']} version; none was supplied"
            ),
            "engineering-lead", "lead_replan",
        ))
        checks["expected_artifact_type_present"] = False
        checks["authoritative_version_resolved"] = False

    for req in requirements:
        if not req.get("required", True):
            # Optional deps still fail closed if a version is named but unresolvable.
            if req.get("version") in (None, ""):
                continue
        _check_one_requirement(store, req, target_role, review_level, cat,
                               checks, failures)

    result = _ready_result(failures, requirements, checks)
    result_errs = _validate(
        "artifact-contract/readiness.schema.json#/$defs/result", result)
    if result_errs:
        raise ValueError("ready-check result schema: " + "; ".join(result_errs))
    return result


def _check_one_requirement(store, req, target_role, review_level, catalog,
                           checks, failures) -> None:
    try:
        entry = catalog_entry(req["artifact_type"], catalog)
    except KeyError:
        checks["expected_artifact_type_present"] = False
        failures.append(_failure(
            req, "UNKNOWN_TYPE",
            f"unknown artifact_type {req.get('artifact_type')!r}",
            "engineering-lead", "lead_replan",
        ))
        return
    owner = entry["correction_owner"]
    resolved = resolve_exact(store, req.get("artifact_id") or "", req.get("version"),
                             req.get("artifact_type"))
    if not resolved["ok"]:
        checks["authoritative_version_resolved"] = False
        if resolved["code"] == "MISSING":
            checks["expected_artifact_type_present"] = False
        route = "lead_replan" if resolved["code"] in {"AMBIGUOUS", "VERSION_NOT_EXACT"} else "producer_correction"
        owner_for = "engineering-lead" if route == "lead_replan" else owner
        failures.append(_failure(req, resolved["code"], resolved["message"],
                                 owner_for, route))
        return
    env = resolved["envelope"]
    auth = authoritative_version(store, env["artifact_id"], env["artifact_type"])
    if not auth["ok"]:
        checks["authoritative_version_resolved"] = False
        failures.append(_failure(
            req, auth["code"], auth["message"], "engineering-lead", "lead_replan",
        ))
    elif auth["version"] != env["version"] and env["status"] not in {"superseded", "stale"}:
        # Named version exists but is not the unique live version — still
        # acceptable only when it itself is a valid live status. Ambiguous live
        # set already failed above.
        pass

    if env["status"] == "superseded":
        checks["superseded"] = True
        checks["authoritative_version_resolved"] = False
        failures.append(_failure(
            req, "SUPERSEDED",
            f"{artifact_ref_str(req)} is superseded and cannot be a required input",
            owner, "producer_correction",
            extra={"ref": env.get("locator") or artifact_ref_str(req)},
        ))
        return
    if env["status"] == "stale":
        checks["stale"] = True
        checks["authoritative_version_resolved"] = False
        failures.append(_failure(
            req, "STALE",
            f"{artifact_ref_str(req)} is stale and cannot be a required input",
            owner, "producer_correction",
            extra={"ref": env.get("locator") or artifact_ref_str(req)},
        ))
        return
    allowed = set(entry["allowed_input_statuses"])
    if env["status"] not in allowed or env["status"] in INPUT_BLOCKED_STATUSES:
        checks["status_allowed_for_target_role"] = False
        failures.append(_failure(
            req, "STATUS_NOT_ALLOWED",
            (
                f"{artifact_ref_str(req)} status {env['status']!r} is not eligible "
                f"input for {target_role}"
            ),
            owner, "producer_correction",
        ))
    sem_errs = validate_semantics(env, catalog, review_level)
    if sem_errs:
        checks["required_semantics_present"] = False
        failures.append(_failure(
            req, "MISSING_SEMANTICS",
            "; ".join(e["message"] for e in sem_errs),
            owner, "producer_correction",
        ))
    env_errs = validate_envelope(env, catalog, review_level)
    rel_codes = {e["code"] for e in env_errs}
    if rel_codes & {
        "BASED_ON_MISSING", "REVIEWED_ARTIFACT_MISSING",
        "VALIDATED_AGAINST_MISSING", "BASED_ON_VERSION_NOT_EXACT",
        "REVIEWED_ARTIFACT_VERSION_NOT_EXACT",
        "VALIDATED_AGAINST_VERSION_NOT_EXACT",
    }:
        checks["upstream_refs_resolvable"] = False
        failures.append(_failure(
            req, "UNRESOLVED_UPSTREAM",
            "; ".join(e["message"] for e in env_errs if e["code"] in rel_codes),
            owner, "producer_correction",
        ))
    else:
        for ref in list(env.get("based_on") or []) + (
            [env["reviewed_artifact"]] if env.get("reviewed_artifact") else []
        ) + list(env.get("validated_against") or []):
            up = resolve_exact(store, ref["artifact_id"], ref["version"],
                               ref.get("artifact_type"))
            if not up["ok"]:
                checks["upstream_refs_resolvable"] = False
                failures.append(_failure(
                    req, "UNRESOLVED_UPSTREAM",
                    f"{artifact_ref_str(req)} upstream {artifact_ref_str(ref)}: {up['message']}",
                    owner, "producer_correction",
                ))
            elif up["envelope"]["status"] in {"superseded", "stale"}:
                checks["upstream_refs_resolvable"] = False
                if up["envelope"]["status"] == "stale":
                    checks["stale"] = True
                else:
                    checks["superseded"] = True
                failures.append(_failure(
                    req, up["envelope"]["status"].upper(),
                    (
                        f"{artifact_ref_str(req)} upstream {artifact_ref_str(ref)} "
                        f"is {up['envelope']['status']}"
                    ),
                    owner, "producer_correction",
                ))
    if env["artifact_type"] == "delivery_review" and target_role == "qa":
        verdict = (env.get("semantics") or {}).get("verdict")
        if verdict != "APPROVE":
            checks["status_allowed_for_target_role"] = False
            failures.append(_failure(
                req, "QA_GATE_BLOCKED",
                (
                    f"QA requires relevant delivery_review verdict APPROVE, "
                    f"got {verdict!r}"
                ),
                "engineering-lead", "lead_replan",
            ))


def _ready_result(failures, requirements, checks=None) -> dict:
    if checks is None:
        checks = {
            "expected_artifact_type_present": False,
            "authoritative_version_resolved": False,
            "status_allowed_for_target_role": False,
            "superseded": False,
            "stale": False,
            "required_semantics_present": False,
            "upstream_refs_resolvable": False,
        }
    not_ready = bool(failures)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "artifact_ready_check_result",
        "status": "ARTIFACT_NOT_READY" if not_ready else "ARTIFACT_READY",
        "checks": checks,
        "failures": failures,
        "blocks_handoff": not_ready,
        "dependency_digest": dependency_digest(requirements),
    }


# ---------------------------------------------------------------------------
# Traceability / invalidation
# ---------------------------------------------------------------------------

def _refs_of(envelope: dict) -> list:
    refs = list(envelope.get("based_on") or [])
    if envelope.get("reviewed_artifact"):
        refs.append(envelope["reviewed_artifact"])
    refs.extend(envelope.get("validated_against") or [])
    if envelope.get("supersedes"):
        refs.append(envelope["supersedes"])
    return refs


def _points_at(envelope: dict, artifact_id: str, version: str) -> bool:
    for ref in _refs_of(envelope):
        if ref.get("artifact_id") == artifact_id and ref.get("version") == version:
            return True
    return False


def apply_supersede(store: ArtifactStore, new_envelope: dict) -> dict:
    """Mark the superseded envelope and stale downstream consumers.

    Historical Review/QA attempts keep their original verdict. Status becomes
    stale. A new attempt must be a new envelope; this function never rewrites
    FAIL / CHANGES_REQUIRED / PASS / APPROVE.
    """
    sup = new_envelope.get("supersedes")
    if not sup:
        raise ValueError("apply_supersede requires new_envelope.supersedes")
    target = store.get(sup["artifact_id"], sup["version"])
    if target is None:
        raise ValueError(f"superseded target missing: {artifact_ref_str(sup)}")
    preserved = []
    stale_ids = []
    target["status"] = "superseded"
    if new_envelope["artifact_id"] == target["artifact_id"]:
        # same identity, new version
        if store.get(new_envelope["artifact_id"], new_envelope["version"]) is None:
            store.add(new_envelope, validate=False)
    else:
        if store.get(new_envelope["artifact_id"], new_envelope["version"]) is None:
            store.add(new_envelope, validate=False)
    for env in store.envelopes:
        if env is target or (
            env["artifact_id"] == new_envelope["artifact_id"]
            and env["version"] == new_envelope["version"]
        ):
            continue
        if env["status"] in {"superseded"}:
            continue
        if _points_at(env, target["artifact_id"], target["version"]):
            verdict = (env.get("semantics") or {}).get("verdict")
            preserved.append({
                "artifact_id": env["artifact_id"],
                "version": env["version"],
                "artifact_type": env["artifact_type"],
                "previous_status": env["status"],
                "verdict": verdict,
            })
            env["status"] = "stale"
            stale_ids.append(f"{env['artifact_id']}@{env['version']}")
    return {
        "superseded": artifact_ref_str(sup),
        "replacement": artifact_ref_str(new_envelope),
        "stale": stale_ids,
        "preserved_verdicts": preserved,
        "rewritten_verdicts": 0,
    }


def open_new_attempt(store: ArtifactStore, previous: dict, new_envelope: dict) -> dict:
    """Record a new Review/QA attempt. Never edits the previous verdict in place."""
    prev = store.get(previous["artifact_id"], previous["version"])
    if prev is None:
        raise ValueError("previous attempt missing")
    old_verdict = (prev.get("semantics") or {}).get("verdict")
    new_verdict = (new_envelope.get("semantics") or {}).get("verdict")
    if old_verdict in {"FAIL", "CHANGES_REQUIRED"} and new_verdict in {"PASS", "APPROVE"}:
        if (new_envelope["artifact_id"], new_envelope["version"]) == (
            prev["artifact_id"], prev["version"]
        ):
            raise ValueError(
                "cannot rewrite historical FAIL/CHANGES_REQUIRED into PASS/APPROVE"
            )
    if store.get(new_envelope["artifact_id"], new_envelope["version"]) is None:
        store.add(new_envelope, validate=False)
    return {
        "previous": artifact_ref_str(prev),
        "previous_verdict": old_verdict,
        "new_attempt": artifact_ref_str(new_envelope),
        "new_verdict": new_verdict,
        "history_preserved": True,
    }


# ---------------------------------------------------------------------------
# Routing contracts (no triggers)
# ---------------------------------------------------------------------------

def routing_contract(level: str, routing: dict | None = None) -> dict:
    doc = routing if routing is not None else load_routing_contracts()
    if level not in doc["levels"]:
        raise KeyError(f"unknown review_level {level!r}")
    return doc["levels"][level]


def validate_routing(plan: dict, routing: dict | None = None) -> dict:
    """Machine-check a proposed Option A routing plan. Never dispatches."""
    doc = routing if routing is not None else load_routing_contracts()
    violations = []
    level = plan.get("review_level")
    if level not in REVIEW_LEVELS:
        violations.append({
            "code": "UNKNOWN_REVIEW_LEVEL",
            "message": f"review_level {level!r} is not R0/R1/R2",
        })
        return _routing_result(plan, violations)
    spec = doc["levels"][level]
    bool_fields = (
        "independent_delivery_review_attempt",
        "independent_qa_attempt",
        "producer_auto_triggers_delivery_reviewer",
        "delivery_reviewer_auto_triggers_qa",
        "lead_mediated",
    )
    for field in bool_fields:
        if field in plan and bool(plan[field]) != bool(spec[field]):
            violations.append({
                "code": "ROUTING_MISMATCH",
                "message": f"{field} expected {spec[field]}, got {plan.get(field)}",
            })
    if plan.get("producer_auto_triggers_delivery_reviewer"):
        violations.append({
            "code": "AUTO_TRIGGER_FORBIDDEN",
            "message": "producer must not auto-trigger delivery-reviewer",
        })
    if plan.get("delivery_reviewer_auto_triggers_qa"):
        violations.append({
            "code": "AUTO_TRIGGER_FORBIDDEN",
            "message": "delivery-reviewer must not auto-trigger QA",
        })
    if spec["producer_auto_triggers_delivery_reviewer"] or spec["delivery_reviewer_auto_triggers_qa"]:
        violations.append({
            "code": "CONTRACT_CORRUPT",
            "message": "stored routing contract itself forbids auto-triggers",
        })
    if level == "R0":
        if spec["requires_delivery_review"] or spec["requires_qa"]:
            violations.append({
                "code": "R0_MUST_NOT_REQUIRE_05_06",
                "message": "R0 must not require delivery review or QA",
            })
        if spec["independent_delivery_review_attempt"] or spec["independent_qa_attempt"]:
            violations.append({
                "code": "R0_MUST_STAY_IN_PRODUCER_TASK",
                "message": "R0 review stays in the producer task",
            })
    if level in {"R1", "R2"} and not spec["lead_mediated"]:
        violations.append({
            "code": "LEAD_MEDIATION_REQUIRED",
            "message": f"{level} must be lead-mediated",
        })
    if spec["requires_delivery_review"]:
        inp = plan.get("review_input") or {}
        if not (inp.get("artifact_id") and is_exact_version(inp.get("version"))):
            violations.append({
                "code": "R1_EXACT_REVIEW_INPUT_REQUIRED",
                "message": "R1/R2 delivery review requires an exact implementation/artifact version",
            })
    if spec["requires_qa"]:
        baselines = plan.get("qa_baselines") or []
        have = {b.get("artifact_type") for b in baselines}
        missing = [t for t in spec["required_qa_baseline_types"] if t not in have]
        if missing:
            violations.append({
                "code": "R2_EXACT_QA_BASELINES_REQUIRED",
                "message": "QA baselines missing exact types: " + ", ".join(missing),
            })
        for b in baselines:
            if not is_exact_version(b.get("version")):
                violations.append({
                    "code": "R2_EXACT_QA_BASELINES_REQUIRED",
                    "message": f"QA baseline {artifact_ref_str(b)} is not exact",
                })
    if plan.get("triggers_created", 0) not in (0, None):
        violations.append({
            "code": "TRIGGER_CREATED",
            "message": "artifact-contract runtime must not create Review/QA runs",
        })
    return _routing_result(plan, violations, spec)


def _routing_result(plan, violations, spec=None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "routing_check_result",
        "review_level": plan.get("review_level"),
        "ok": not violations,
        "violations": violations,
        "triggers_created": TRIGGERS_CREATED,
        "option": "A",
        "contract": spec,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _store_from_file(path: str) -> ArtifactStore:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "envelopes" in raw:
        envelopes = raw["envelopes"]
    elif isinstance(raw, list):
        envelopes = raw
    else:
        raise ValueError("store file must be a list or {envelopes: [...]}")
    return ArtifactStore(envelopes)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Artifact Contract Runtime (U10)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("revision", help="print artifact_contract_revision")
    sub.add_parser("catalog", help="print the seven-type catalog")
    ve = sub.add_parser("validate-envelope")
    ve.add_argument("--file", required=True)
    ve.add_argument("--review-level", choices=REVIEW_LEVELS)
    rs = sub.add_parser("resolve")
    rs.add_argument("--store", required=True)
    rs.add_argument("--artifact-id", required=True)
    rs.add_argument("--version", required=True)
    rs.add_argument("--artifact-type")
    rc = sub.add_parser("ready-check")
    rc.add_argument("--store", required=True)
    rc.add_argument("--request-file", required=True)
    dd = sub.add_parser("dependency-digest")
    dd.add_argument("--request-file", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("--store", required=True)
    ex.add_argument("--request-file", required=True)
    rt = sub.add_parser("routing-check")
    rt.add_argument("--request-file", required=True)
    sp = sub.add_parser("supersede")
    sp.add_argument("--store", required=True)
    sp.add_argument("--new-file", required=True)
    args = parser.parse_args(argv)

    def dump(obj) -> int:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 0

    if args.command == "revision":
        return dump({
            "artifact_contract_revision": artifact_contract_revision(),
        })
    if args.command == "catalog":
        return dump(load_catalog())
    if args.command == "validate-envelope":
        env = json.loads(Path(args.file).read_text(encoding="utf-8"))
        errs = validate_envelope(env, review_level=args.review_level)
        print(json.dumps({"ok": not errs, "errors": errs}, ensure_ascii=False, indent=2))
        return 0 if not errs else 2
    if args.command == "resolve":
        store = _store_from_file(args.store)
        return dump(resolve_exact(store, args.artifact_id, args.version,
                                  args.artifact_type))
    if args.command == "ready-check":
        store = _store_from_file(args.store)
        request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        result = artifact_ready_check(store, request)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ARTIFACT_READY" else 2
    if args.command == "dependency-digest":
        request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        reqs = request.get("requirements", request.get("dependencies", request))
        return dump({
            "dependency_digest": dependency_digest(reqs),
            "dependencies": normalize_requirements(reqs),
        })
    if args.command == "export":
        store = _store_from_file(args.store)
        request = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        reqs = request.get("requirements", request.get("dependencies"))
        return dump(export_t00_surfaces(store, reqs))
    if args.command == "routing-check":
        plan = json.loads(Path(args.request_file).read_text(encoding="utf-8"))
        result = validate_routing(plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    if args.command == "supersede":
        store = _store_from_file(args.store)
        new_env = json.loads(Path(args.new_file).read_text(encoding="utf-8"))
        report = apply_supersede(store, new_env)
        report["envelopes"] = store.envelopes
        return dump(report)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
