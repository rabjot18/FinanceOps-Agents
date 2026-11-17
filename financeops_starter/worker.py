"""FinanceOps worker (Milestones 3 and 5): orchestrates, delivers queued messages
and runs due jobs.

    .venv/bin/python worker.py              # one round, then exit
    .venv/bin/python worker.py --loop       # keep going every few seconds
    .venv/bin/python worker.py --loop --investigate   # also run re-investigations (COSTS MONEY)

Uses FINANCEOPS_DB like the API. Run ONE worker per database. With
FINANCEOPS_ENABLE_DEMO=1 it follows the demo's simulated clock.

Delivery rules:
- Re-check each message just before sending; cancel it if the case changed.
- If the outcome of a send is unknown (timeout, crash), ask the inbox
  whether it arrived BEFORE sending again. The same idempotency key is used
  every time, so even a repeat is stored once.
- A delivered message changes no money. Only a settlement does.

Milestone 5: each round starts with orchestrator.run_cycle (apply due events,
reminder cadence, autonomy rules) and runs the autonomy rules again after
investigations. In the demo, a playing simulated clock moves forward between
rounds (speed x elapsed real time).
"""
import argparse
import logging
import os
import sys
import time

import orchestrator
from adapters import DispatchRejected, DispatchTimeout, SimulatedInbox
from core import Conflict, FinanceCore

log = logging.getLogger("financeops.worker")
INVESTIGATE_RETRY_MINUTES = 5


def run_once(core, adapter, investigator_factory=None):
    """One round: deliver what is due, then run due jobs. Returns a summary.

    investigator_factory(core) -> Investigator, or None to leave
    re-investigation jobs waiting (they cost a model call).
    """
    result = {"sent": [], "unknown": [], "cancelled": [], "failed": [],
              "follow_ups_elapsed": [], "investigations": [], "errors": []}
    # Orchestration first, so a message queued by an automatic rule is
    # delivered in this same round. Its summary goes to the log; the owner
    # sees it in the activity feed. (The returned shape is a frozen contract.)
    try:
        log.debug("orchestration: %s", orchestrator.run_cycle(
            core, has_model=investigator_factory is not None))
    except Exception as exc:
        log.exception("orchestration failed")
        result["errors"].append({"item": "orchestrator", "error": type(exc).__name__})
    # One broken item must not stop the others: log it, report it, carry on.
    # An item left "sending" is picked up again after SENDING_STALE_SECONDS.
    for item_id in core.dispatch_candidates():
        try:
            _deliver(core, adapter, item_id, result)
        except Exception as exc:
            log.exception("delivery of %s failed", item_id)
            result["errors"].append({"item": item_id, "error": type(exc).__name__})
    for job in core.due_jobs():
        try:
            if job["kind"] == "settlement_check":
                if core.run_settlement_check(job["id"])["elapsed"]:
                    result["follow_ups_elapsed"].append(job["id"])
            elif (job["kind"] == "investigate" and investigator_factory is not None
                  and not core.agent_paused("collections")):
                _investigate(core, investigator_factory, job["id"], result)
        except Exception as exc:
            log.exception("job %s failed", job["id"])
            result["errors"].append({"job": job["id"], "error": type(exc).__name__})
    if result["investigations"]:
        # The agent's new proposals: apply the autonomy rules now; delivery
        # follows next round.
        orchestrator.run_autonomy(core)
    result["investigations_waiting"] = core.waiting_jobs("investigate")
    return result


def _deliver(core, adapter, item_id, result):
    item = core.outbox_item(item_id)
    if item["status"] in ("unknown", "sending"):
        # We don't know whether the last attempt arrived. Ask first.
        external_id = adapter.lookup(item_id)
        if external_id:
            core.mark_sent(item_id, external_id)
            result["sent"].append(item_id)
            return
    claimed = core.claim_dispatch(item_id)
    if claimed is None:
        status = core.outbox_item(item_id)["status"]
        if status in ("cancelled", "failed"):
            result[status].append(item_id)
        return
    payload = claimed["payload"]
    try:
        external_id = adapter.send(item_id, payload["to"], payload["subject"], payload["body"])
    except DispatchTimeout as exc:
        core.mark_unknown(item_id, str(exc))
        result["unknown"].append(item_id)
        return
    except DispatchRejected as exc:
        core.mark_failed(item_id, str(exc))
        result["failed"].append(item_id)
        return
    core.mark_sent(item_id, external_id)
    result["sent"].append(item_id)


def _investigate(core, investigator_factory, job_id, result):
    job = core.claim_job(job_id)
    if job is None:
        return
    try:
        run = investigator_factory(core).investigate(FinanceCore.case_id(job["invoice_id"]))
    except Conflict as exc:  # e.g. another investigation is running: try later
        core.finish_job(job_id, "pending", f"retry: {exc}", retry_minutes=INVESTIGATE_RETRY_MINUTES,
                        count_attempt=False)
        return
    except Exception as exc:
        core.finish_job(job_id, "failed", f"error: {type(exc).__name__}")
        raise  # run_once logs it and moves on to the next job
    if run["status"] == "failed":
        # API error, refusal, truncation: the reply still needs a look. Retry
        # later; claim_job gives up after MAX_JOB_ATTEMPTS.
        core.finish_job(job_id, "pending", f"run {run['id']} failed: {run['detail']}",
                        retry_minutes=INVESTIGATE_RETRY_MINUTES)
    else:
        core.finish_job(job_id, "done", f"run {run['id']}: {run['status']}")
    result["investigations"].append({"job": job_id, "run": run["id"], "status": run["status"]})


def main():
    parser = argparse.ArgumentParser(description="FinanceOps worker")
    parser.add_argument("--loop", action="store_true", help="keep running")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between rounds")
    parser.add_argument("--investigate", action="store_true",
                        help="run re-investigation jobs with the real model (costs money)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    path = os.environ.get("FINANCEOPS_DB", "financeops.sqlite3")
    simulated = os.environ.get("FINANCEOPS_ENABLE_DEMO", "0") == "1"
    adapter = SimulatedInbox(path)
    factory = None
    if args.investigate:
        import anthropic
        from agent import CLIENT_MAX_RETRIES, CLIENT_TIMEOUT_SECONDS, Investigator
        client = anthropic.Anthropic(timeout=CLIENT_TIMEOUT_SECONDS, max_retries=CLIENT_MAX_RETRIES)
        factory = lambda core: Investigator(core, client)  # noqa: E731

    last_round = time.monotonic()
    while True:
        core = FinanceCore(path, simulated_time=simulated)
        try:
            now = time.monotonic()
            core.tick_playback(now - last_round)  # demo: a playing clock moves on
            last_round = now
            log.info("round: %s", run_once(core, adapter, factory))
        except Exception:
            if not args.loop:
                raise
            log.exception("worker round failed; retrying next round")
        finally:
            core.close()
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
