#!/usr/bin/env python3
"""O2 capability-probe worker (YZT-79).

A minimal process entry point used by `chandoff_intent.capability_proof` to
prove cross-process durability, enumeration, single-writer lease and CAS over
one shared ledger path. Research/instrumentation only: it never touches the
platform and never leaves the given store path.

Usage:
  python tools/o2_store_probe.py <command> [flags]

Commands:
  append-intent --store P --intent-file F [--now T]
  enumerate --store P [--now T]
  claim --store P --intent ID --owner O --ttl N --now T
  seed-ready --store P --intent ID --owner O --now T
  race-issue --store P --intent ID --owner O --ttl N --now T
  state --store P --intent ID --now T
  transition --store P --intent ID --to S --expected-revision N --actor A [--now T]

Exit codes: 0 ok; 2 usage; 3 lease_held; 4 ledger_corruption; 5 cas_conflict;
6 other bounded O2 error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import chandoff_intent as o2  # noqa: E402

EXIT_BY_CODE = {
    "lease_held": 3,
    "ledger_corruption": 4,
    "cas_conflict": 5,
}


def emit(payload: dict, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return code


def fail(exc: o2.IntentError) -> int:
    return emit({"ok": False, "code": exc.code, "message": exc.message},
                EXIT_BY_CODE.get(exc.code, 6))


def store_of(args) -> o2.DurableIntentStore:
    return o2.DurableIntentStore(args.store)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="O2 capability probe worker")
    sub = parser.add_subparsers(dest="command", required=True)

    append = sub.add_parser("append-intent")
    append.add_argument("--store", required=True)
    append.add_argument("--intent-file", required=True)
    append.add_argument("--now", default=None)

    enum = sub.add_parser("enumerate")
    enum.add_argument("--store", required=True)
    enum.add_argument("--now", default=None)

    claim = sub.add_parser("claim")
    claim.add_argument("--store", required=True)
    claim.add_argument("--intent", required=True)
    claim.add_argument("--owner", required=True)
    claim.add_argument("--ttl", type=int, default=300)
    claim.add_argument("--now", default=None)

    seed = sub.add_parser("seed-ready")
    seed.add_argument("--store", required=True)
    seed.add_argument("--intent", required=True)
    seed.add_argument("--owner", required=True)
    seed.add_argument("--now", default=None)

    race = sub.add_parser("race-issue")
    race.add_argument("--store", required=True)
    race.add_argument("--intent", required=True)
    race.add_argument("--owner", required=True)
    race.add_argument("--ttl", type=int, default=300)
    race.add_argument("--now", default=None)

    state = sub.add_parser("state")
    state.add_argument("--store", required=True)
    state.add_argument("--intent", required=True)
    state.add_argument("--now", default=None)

    transition = sub.add_parser("transition")
    transition.add_argument("--store", required=True)
    transition.add_argument("--intent", required=True)
    transition.add_argument("--to", required=True)
    transition.add_argument("--expected-revision", type=int, required=True)
    transition.add_argument("--actor", required=True)
    transition.add_argument("--now", default=None)

    args = parser.parse_args(argv)
    now = getattr(args, "now", None)
    store = store_of(args)
    try:
        if args.command == "append-intent":
            fields = json.loads(Path(args.intent_file).read_text(encoding="utf-8"))
            record = store.record_intent(fields, now=now)
            intents = store.fold()["intents"]
            return emit({"ok": True, "intent_id": record["intent_id"],
                         "seq": record["seq"],
                         "visible_intents": len(intents)}, 0)
        if args.command == "enumerate":
            folded = store.fold()
            rows = [{"intent_id": i["intent_id"], "state": i["state"],
                     "issue_id": i["fields"].get("issue_id")}
                    for i in sorted(folded["intents"].values(),
                                    key=lambda v: v["intent_id"])]
            return emit({"ok": True, "intents": rows,
                         "ignored_records": folded["ignored_records"]}, 0)
        if args.command == "claim":
            result = store.claim(args.intent, args.owner, now=now,
                                 ttl_seconds=args.ttl)
            return emit({"ok": True,
                         "expired_previous": bool(
                             (result.get("record") or {}).get("expired_previous")),
                         "outcome": result.get("outcome")}, 0)
        if args.command == "seed-ready":
            store.claim(args.intent, args.owner, now=now, ttl_seconds=300)
            for target in (o2.S_TARGET_BOUND, o2.S_HANDOFF_PREPARED,
                           o2.S_HANDOFF_PUBLISHED, o2.S_TRIGGER_READY):
                intent = store.get(args.intent)
                store.transition(args.intent, target,
                                 expected_revision=intent["revision"],
                                 actor=args.owner, now=now)
            store.release(args.intent, args.owner, now=now)
            return emit({"ok": True,
                         "revision": store.get(args.intent)["revision"]}, 0)
        if args.command == "race-issue":
            store.claim(args.intent, args.owner, now=now,
                        ttl_seconds=args.ttl)
            intent = store.get(args.intent)
            transition = store.transition(
                args.intent, o2.S_TRIGGER_ISSUING,
                expected_revision=intent["revision"], actor=args.owner,
                now=now)
            return emit({"ok": True, "revision": transition["revision"],
                         "issued_by": args.owner}, 0)
        if args.command == "state":
            intent = store.get(args.intent)
            lease = store.lease_view(args.intent, now=now or o2.utc_now())
            return emit({
                "ok": True, "state": intent["state"],
                "revision": intent["revision"],
                "transitions_to_issuing": sum(
                    1 for t in intent["transitions"]
                    if t.get("to") == o2.S_TRIGGER_ISSUING),
                "lease": ({"holder": lease["holder"],
                           "active": lease["active"]} if lease else None)}, 0)
        if args.command == "transition":
            transition = store.transition(
                args.intent, args.to,
                expected_revision=args.expected_revision, actor=args.actor,
                now=now, reason="probe transition")
            return emit({"ok": True, "revision": transition["revision"]}, 0)
    except o2.IntentError as exc:
        return fail(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return emit({"ok": False, "code": "probe_error",
                     "message": f"{type(exc).__name__}: {exc}"}, 6)
    return emit({"ok": False, "code": "usage"}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
