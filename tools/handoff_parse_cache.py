#!/usr/bin/env python3
"""Bounded process-local parse reuse; NEVER caches discovery or latestness.

Every discovery still enumerates the complete authenticated live thread.
Only pure parsing of identical bytes under identical parser/schema revision
is reused. No disk cache or stale-envelope fallback is provided.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import Path


class ParseCache:
    def __init__(self, max_bytes: int = 8 * 1024 * 1024, max_entries: int = 256):
        if max_bytes <= 0 or max_entries <= 0:
            raise ValueError("positive cache bounds required")
        self.max_bytes, self.max_entries = max_bytes, max_entries
        self._rows = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = self.misses = 0

    def parse(self, namespace: tuple, revision: str, body: str, parser):
        raw = body.encode("utf-8")
        key = (namespace, revision, parser, hashlib.sha256(raw).digest())
        with self._lock:
            found = self._rows.get(key)
            if found is not None and found[0] == raw:
                self._rows.move_to_end(key)
                self.hits += 1
                return copy.deepcopy(found[1])
        value = parser(body)
        cost = len(raw) + len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        with self._lock:
            self.misses += 1
            if cost <= self.max_bytes:
                old = self._rows.pop(key, None)
                if old is not None:
                    self._bytes -= old[2]
                self._rows[key] = (raw, copy.deepcopy(value), cost)
                self._bytes += cost
                while self._bytes > self.max_bytes or len(self._rows) > self.max_entries:
                    _, evicted = self._rows.popitem(last=False)
                    self._bytes -= evicted[2]
        return copy.deepcopy(value)

    def stats(self):
        with self._lock:
            return {"hits": self.hits, "misses": self.misses, "entries": len(self._rows),
                    "accounted_bytes": self._bytes, "network_reads_saved": 0,
                    "latestness_source": "complete_live_thread"}


def parser_revision(root: Path) -> str:
    paths = [root / "tools" / n for n in ("chandoff_note.py", "chandoff.py", "schema_mini.py")]
    paths += sorted((root / "schemas").rglob("*.json"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
