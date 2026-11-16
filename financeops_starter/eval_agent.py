"""Live check of the investigator against real Claude. COSTS MONEY; opt-in.

    .venv/bin/python eval_agent.py          # describe what would run, no API calls
    .venv/bin/python eval_agent.py --run    # run it (needs Anthropic credentials)
    .venv/bin/python eval_agent.py --run --repeats 1

Each scenario is built fresh in a temporary database with synthetic data.
Unit tests (test_agent.py) prove the plumbing with a fake model; only this
script shows whether the model *judges* well: same shortfall, different
evidence, different justified proposal (BUILD_PLAN Phase 2 acceptance).
"""
import argparse
import json
import sys
import tempfile
from pathlib import Path

from core import FinanceCore

# Claude Opus 5.5 list prices, USD per million tokens (cache reads are
# cheaper, so this slightly overestimates). Update if the model changes.
PRICE_IN, PRICE_OUT = 4.00, 20.00


def underpaid_no_dispute(core):
    core.create_invoice("inv-a", "Northwind Studio (synthetic)", 1_200_000)
    core.add_evidence("contract-a", "inv-a", "Services agreement: CAD 12,000 due on completion. "
                                             "No discounts agreed.")
    core.add_evidence("delivery-a", "inv-a", "Completion record: all work delivered and signed off "
                                             "by the customer on 2026-10-30.")
    core.record_settlement("bank-a", "inv-a", 1_000_000)
    return "case-inv-a"


def disputed_missing_delivery(core):
    core.create_invoice("inv-b", "Harbour Clinic (synthetic)", 850_000)
    core.add_evidence("contract-b", "inv-b", "Agreement: two on-site visits, CAD 4,250 each, "
                                             "CAD 8,500 total.")
    core.add_evidence("reply-b", "inv-b", "Customer email: The second site visit never happened. "
                                          "We paid for the first visit only and will not pay "
                                          "the rest until this is sorted out.")
    core.record_settlement("bank-b", "inv-b", 425_000)
    core.set_disputed("inv-b", True)
    return "case-inv-b"


SCENARIOS = [
    # (name, builder, expected kind, evidence that should be cited)
    ("underpaid, no dispute", underpaid_no_dispute, "collection_reminder", {"contract-a"}),
    ("disputed, no delivery record", disputed_missing_delivery, "request_information", {"reply-b"}),
]


def run_once(client, builder, expected_kind, expected_refs):
    from agent import MAX_TOOL_CALLS, Investigator
    with tempfile.TemporaryDirectory() as folder:
        core = FinanceCore(str(Path(folder) / "eval.sqlite3"))
        try:
            run = Investigator(core, client).investigate(builder(core))
            action = core.action(run["action_id"]) if run["action_id"] else None
        finally:
            core.close()
    kind = action["kind"] if action else None
    cited = set(json.loads(action["evidence_ids"])) if action else set()
    checks = {
        "kind": kind == expected_kind,
        "cites": expected_refs <= cited,
        "unknowns": expected_kind != "request_information" or bool(run["unknowns"]),
        "bounded": run["tool_calls"] <= MAX_TOOL_CALLS,
    }
    return run, kind, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--run", action="store_true", help="actually call the API (costs money)")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    if not args.run:
        print(f"Would run {len(SCENARIOS)} scenarios x {args.repeats} repeats "
              f"= {len(SCENARIOS) * args.repeats} live investigations.")
        for name, _, kind, refs in SCENARIOS:
            print(f"  - {name}: expect {kind}, citing {sorted(refs)}")
        print("Estimated cost: roughly USD 0.10-0.30 per investigation (measured below when run).")
        print("Re-run with --run to call the API.")
        return 0

    import anthropic
    from agent import CLIENT_MAX_RETRIES, CLIENT_TIMEOUT_SECONDS
    client = anthropic.Anthropic(timeout=CLIENT_TIMEOUT_SECONDS, max_retries=CLIENT_MAX_RETRIES)
    totals = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    failures = done = 0
    try:
        for name, builder, kind, refs in SCENARIOS:
            for attempt in range(1, args.repeats + 1):
                try:
                    run, got, checks = run_once(client, builder, kind, refs)
                except Exception as exc:  # count it, keep the cost report
                    print(f"[ERROR] {name} #{attempt}: {type(exc).__name__}: {exc}")
                    failures += 1
                    done += 1
                    continue
                done += 1
                for key, field in (("in", "input_tokens"), ("out", "output_tokens"),
                                   ("cache_read", "cache_read_tokens"),
                                   ("cache_write", "cache_write_tokens")):
                    totals[key] += run[field]
                ok = all(checks.values())
                failures += not ok
                failed = [k for k, v in checks.items() if not v]
                print(f"[{'PASS' if ok else 'FAIL'}] {name} #{attempt}: status={run['status']} "
                      f"kind={got} calls={run['tool_calls']} served_by={run['served_model']}"
                      + (f" failed={failed} detail={run['detail']}" if not ok else ""))
                if run["summary"]:
                    print(f"         summary: {run['summary']}")
                if run["unknowns"]:
                    print(f"         unknowns: {run['unknowns']}")
                if run["status"] == "failed" and (run["detail"] or "").startswith(
                        ("api_error", "client_error")):
                    print("Stopping: the API call itself failed; fix that before spending more.")
                    return 1
    finally:
        # Cache writes cost 1.25x input, cache reads 0.1x input (approximation).
        cost = ((totals["in"] + 1.25 * totals["cache_write"] + 0.1 * totals["cache_read"])
                * PRICE_IN + totals["out"] * PRICE_OUT) / 1e6
        print(f"\nTokens: {totals['in']} in (+{totals['cache_write']} cache write, "
              f"{totals['cache_read']} cache read) / {totals['out']} out. "
              f"Estimated cost: USD {cost:.2f}")
        print(f"{failures} of {done} completed runs failed a check.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
