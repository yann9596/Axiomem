#!/usr/bin/env python3
"""Minimal YAML-subset parser for the V1.1 canonical files (zero-dependency).

Handles exactly the constructs used by the V1.1 canonical documents:
block mappings, block sequences ("- scalar" and "- key: value" with deeper
continuation lines), block scalars (|, |-, >, >-), plain or quoted scalars,
ints, floats, booleans, null, and full-line / trailing comments.
Anything outside this subset raises an error (never silently mis-parsed).
"""
from __future__ import annotations

import re
from typing import Any


class YamlError(ValueError):
    pass


def _split_kv(stripped: str) -> tuple[str, str]:
    quote = None
    for i, ch in enumerate(stripped):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == ":":
            if i + 1 == len(stripped):
                return stripped[:i].strip(), ""
            if stripped[i + 1] == " ":
                return stripped[:i].strip(), stripped[i + 1:].strip()
    raise YamlError(f"no key separator in line: {stripped!r}")


def _scalar(text: str) -> Any:
    t = text.strip()
    if t in ("", "~", "null", "Null", "NULL"):
        return None
    if t == "[]":
        return []
    if t == "{}":
        return {}
    if t.startswith("[") and t.endswith("]"):
        inner = t[1:-1].strip()
        return [] if not inner else [_scalar(seg) for seg in inner.split(",")]
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        body = t[1:-1]
        if t[0] == '"':
            body = body.replace('\\"', '"').replace("\\\\", "\\")
        return body
    low = t.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(t)
        except ValueError:
            pass
    return t


def _strip_comment(line: str) -> str:
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def parse_yaml(text: str) -> Any:
    lines = text.splitlines()

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def block_scalar(pos: int, key_ind: int, marker: str):
        keep = not marker.endswith("-")
        folded = marker.startswith(">")
        body: list[str] = []
        i = pos
        while i < len(lines):
            b = lines[i]
            if b.strip() == "":
                body.append("")
                i += 1
                continue
            if indent_of(b) <= key_ind:
                break
            body.append(b[key_ind + 1:] if len(b) > key_ind + 1 else "")
            i += 1
        while body and body[-1] == "":
            body.pop()
        if folded:
            paras: list[str] = []
            cur: list[str] = []
            for seg in body:
                if seg == "":
                    if cur:
                        paras.append(" ".join(cur))
                        cur = []
                    paras.append("")
                else:
                    cur.append(seg.strip())
            if cur:
                paras.append(" ".join(cur))
            out = "\n".join(paras)
        else:
            out = "\n".join(body)
        if keep and out:
            out += "\n"
        return out, i

    def parse_block(pos: int, min_indent: int):
        result = None
        while pos < len(lines):
            line = lines[pos]
            stripped = _strip_comment(line).strip()
            if stripped == "":
                pos += 1
                continue
            ind = indent_of(line)
            if ind < min_indent:
                break
            if stripped.startswith("- ") or stripped == "-":
                if isinstance(result, dict):
                    raise YamlError(f"mixed sequence/mapping at line {pos + 1}")
                if result is None:
                    result = []
                rest = stripped[1:].strip()
                if rest == "":
                    pos += 1
                    value, pos = parse_block(pos, ind + 1)
                    result.append(value)
                    continue
                # "- key: value" map start (rest contains a key separator)
                try:
                    key, val = _split_kv(rest)
                    is_map = True
                except YamlError:
                    key, val, is_map = None, None, False
                if is_map:
                    entry: dict = {}
                    pos += 1
                    if val in ("", "|", "|-", "|+", ">", ">-", ">+"):
                        if val and val[0] in "|>":
                            entry[key], pos = block_scalar(pos, ind, val)
                        else:
                            entry[key], pos = parse_block(pos, ind + 1)
                    else:
                        entry[key] = _scalar(val)
                    # continuation lines of this entry at deeper indent
                    while pos < len(lines):
                        nxt = lines[pos]
                        nstripped = _strip_comment(nxt).strip()
                        if nstripped == "":
                            pos += 1
                            continue
                        nind = indent_of(nxt)
                        if nind <= ind or nstripped.startswith("- "):
                            break
                        k2, v2 = _split_kv(nstripped)
                        pos += 1
                        if v2 in ("", "|", "|-", "|+", ">", ">-", ">+"):
                            if v2 and v2[0] in "|>":
                                entry[k2], pos = block_scalar(pos, nind, v2)
                            else:
                                entry[k2], pos = parse_block(pos, nind + 1)
                        else:
                            entry[k2] = _scalar(v2)
                    result.append(entry)
                    continue
                result.append(_scalar(rest))
                pos += 1
                continue
            if isinstance(result, list):
                break
            if result is None:
                result = {}
            key, val = _split_kv(stripped)
            pos += 1
            if val in ("", "|", "|-", "|+", ">", ">-", ">+"):
                if val and val[0] in "|>":
                    result[key], pos = block_scalar(pos, ind, val)
                else:
                    result[key], pos = parse_block(pos, ind + 1)
            else:
                result[key] = _scalar(val)
        return (result if result is not None else {}), pos

    value, _ = parse_block(0, 0)
    return value


def load_file(path) -> Any:
    from pathlib import Path
    if isinstance(path, str):
        path = Path(path)
    return parse_yaml(path.read_text(encoding="utf-8"))
