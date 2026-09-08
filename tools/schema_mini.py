#!/usr/bin/env python3
"""Minimal JSON-Schema validator for the subset used by V1.1 schemas.

Supports: type (object/array/string/number/integer/boolean), required,
properties, additionalProperties:false, enum, const, pattern, minItems,
maxItems, items, allOf, if/then/else, $ref (file.json#/pointer or same-file
#/...), format:date-time. Anything else raises KeyError/ValueError loudly.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


def _pointer(doc, pointer: str):
    if pointer in ("", "/"):
        return doc
    cur = doc
    for raw in pointer.strip("/").split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, dict):
            if key not in cur:
                raise KeyError(f"pointer {pointer!r} unresolved at {key!r}")
            cur = cur[key]
        elif isinstance(cur, list):
            cur = cur[int(key)]
        else:
            raise KeyError(f"pointer {pointer!r} walks into scalar")
    return cur


def load_schema_file(name: str):
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def resolve_ref(root, ref: str, local_doc):
    if ref.startswith("#"):
        return _pointer(local_doc, ref[1:])
    file_part, _, ptr = ref.partition("#")
    doc = load_schema_file(file_part)
    return _pointer(doc, ptr) if ptr else doc


class Schema:
    def __init__(self, root: dict, local_doc: dict):
        self.root = root
        self.local = local_doc

    def validate(self, value, schema=None, path="$") -> list[str]:
        s = schema if schema is not None else self.root
        if "$ref" in s:
            return self.validate(value, resolve_ref(self.root, s["$ref"], self.local), path)
        if "allOf" in s:
            errs = []
            for sub in s["allOf"]:
                errs += self.validate(value, sub, path)
            rest = {k: v for k, v in s.items() if k != "allOf"}
            if rest:
                errs += self.validate(value, rest, path)
            return errs
        if "if" in s:
            hit = not self.validate(value, s["if"], path)
            if "then" in s and hit:
                return self.validate(value, s["then"], path)
            if "else" in s and not hit:
                return self.validate(value, s["else"], path)
            return []
        t = s.get("type")
        if t == "object":
            if not isinstance(value, dict):
                return [f"{path}: expected object"]
            errs = []
            for k in s.get("required", []):
                if k not in value:
                    errs.append(f"{path}: missing {k!r}")
            props = s.get("properties", {})
            for k, v in value.items():
                if k in props:
                    errs += self.validate(v, props[k], f"{path}.{k}")
                elif s.get("additionalProperties") is False:
                    errs.append(f"{path}: unexpected property {k!r}")
            return errs
        if t == "array":
            if not isinstance(value, list):
                return [f"{path}: expected array"]
            errs = []
            if "minItems" in s and len(value) < s["minItems"]:
                errs.append(f"{path}: fewer than {s['minItems']} items")
            if "maxItems" in s and len(value) > s["maxItems"]:
                errs.append(f"{path}: more than {s['maxItems']} items")
            if "items" in s:
                for i, item in enumerate(value):
                    errs += self.validate(item, s["items"], f"{path}[{i}]")
            return errs
        if t == "string":
            if not isinstance(value, str):
                return [f"{path}: expected string"]
            errs = []
            if "pattern" in s and not re.search(s["pattern"], value):
                errs.append(f"{path}: {value!r} fails pattern {s['pattern']!r}")
            if s.get("format") == "date-time":
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    errs.append(f"{path}: invalid date-time {value!r}")
            return errs
        if t == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
            return [] if ok else [f"{path}: expected integer"]
        if t == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
            return [] if ok else [f"{path}: expected number"]
        if t == "boolean":
            return [] if isinstance(value, bool) else [f"{path}: expected boolean"]
        if "const" in s:
            return [] if value == s["const"] else [f"{path}: must be {s['const']!r}"]
        if "enum" in s:
            return [] if value in s["enum"] else [f"{path}: {value!r} not in enum"]
        return []
