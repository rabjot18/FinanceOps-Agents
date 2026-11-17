"""FinanceOps orchestrator (Milestone 5): plain code that routes work.

It is not a model. Each worker round it:
1. applies due input events (scenario, generator, demo, later Stripe/Plaid),
   escalating anything that needs the owner (a dispute, a refused payment);
2. runs the reminder cadence (policy `cadence_days` after each due date):
   routine cases get a code-proposed reminder, cases that need judgement go
   to the collections agent (or to the owner when no model budget is set);
3. hands proposed actions to the autonomy rules (core.apply_autonomy), which
   execute only what the current policy marks `auto` and the kill switch allows.

Business rules stay in core.py; this module only decides what to call when.
Every step is idempotent, so a replayed round adds nothing.
"""
import logging
from datetime import timedelta

from core import _at

log = logging.getLogger("financeops.orchestrator")


def run_cycle(core, has_model=False):
    """One orchestration round. has_model: the worker can run investigations
    (an explicit model client). Returns a summary for logs and tests."""
    result = {"events": apply_events(core), "cadence": run_cadence(core),
              "judgement": [] if has_model else route_judgement(core)}
    result["autonomy"] = run_autonomy(core)
    return result


# --- 1. events ----------------------------------------------------------

def apply_events(core):
    """Apply due events in stored order. core.apply_event escalates a dispute
    or a refused event in the same transaction that marks it applied."""
    outcomes = []
    for event in core.due_events():
        try:
            outcomes.append(core.apply_event(event["id"]))
        except Exception as exc:  # one bad event must not stop the rest
            log.exception("event %s failed", event["id"])
            outcomes.append({"event": event["id"], "status": "error",
                             "detail": type(exc).__name__})
    return outcomes


def _evidence_ids(core, case_id):
    return [e["id"] for e in core.evidence(core.case(case_id)["invoice_id"])]


# --- 2. reminder cadence ------------------------------------------------

def run_cadence(core):
    """For each open invoice with a due date, act once on the latest cadence
    day that has arrived. Earlier days that were never handled (e.g. a fast
    replay) are recorded as skipped, so the customer never gets a burst."""
    policy = core.policy()
    if not policy or not policy["cadence_days"]:
        return []
    now = _at(core.clock())
    outcomes = []
    for row in core.cases():
        if row["state"] == "resolved" or row["due_at"] is None:
            continue
        due = _at(row["due_at"])
        elapsed = [d for d in policy["cadence_days"] if now >= due + timedelta(days=d)]
        if not elapsed:
            continue
        invoice_id = row["invoice_id"]
        job_id = f"cadence:{invoice_id}:d{elapsed[-1]}"
        if core.job_exists(job_id):
            continue
        for day in elapsed[:-1]:
            core.ensure_job(f"cadence:{invoice_id}:d{day}", invoice_id, "cadence_step", f"d{day}",
                            (due + timedelta(days=day)).isoformat(), status="done",
                            detail="skipped: a later reminder day had already arrived")
        try:
            detail = _cadence_step(core, row, elapsed[-1], policy, job_id)
        except Exception as exc:  # recorded once, not retried every round
            log.exception("cadence step %s failed", job_id)
            detail = f"error: {type(exc).__name__}"
        core.ensure_job(job_id, invoice_id, "cadence_step", f"d{elapsed[-1]}",
                        (due + timedelta(days=elapsed[-1])).isoformat(), status="done",
                        detail=detail)
        outcomes.append({"job": job_id, "detail": detail})
    return outcomes


def _cadence_step(core, row, day, policy, job_id):
    invoice_id, case_id = row["invoice_id"], row["id"]
    invoice = core.invoice(invoice_id)
    evidence = [e["id"] for e in core.evidence(invoice_id)]
    if row["open_escalations"]:
        return "waiting for the owner (open escalation)"
    if invoice["disputed"]:
        core.open_escalation(case_id, "dispute",
                             f"Invoice {invoice_id} is disputed and day {day} after the due date "
                             "has passed. Decide the next step.", evidence, "orchestrator",
                             key=job_id)
        return "escalated: disputed"
    if core.actions_in_flight(invoice_id):
        return "a plan is already in progress"
    if not core.contact_on_file(invoice_id):
        core.open_escalation(case_id, "no_contact",
                             f"No contact is on file for invoice {invoice_id}, so no reminder can "
                             "be sent. Add a contact or decide the next step.", evidence,
                             "orchestrator", key=job_id)
        return "escalated: no contact"
    if not evidence:
        core.open_escalation(case_id, "missing_evidence",
                             f"Invoice {invoice_id} has no contract or other evidence on file, so a "
                             "reminder cannot cite anything. Add evidence or decide.", evidence,
                             "orchestrator", key=job_id)
        return "escalated: no evidence"
    if core.reminders_sent(invoice_id) >= policy["max_reminders"]:
        core.open_escalation(case_id, "reminder_limit",
                             f"Invoice {invoice_id} has had {policy['max_reminders']} reminders "
                             "(the policy limit) and is still unpaid. Decide the next step.",
                             evidence, "orchestrator", key=job_id)
        return "escalated: reminder limit"
    needs_judgement = (invoice["settled_cents"] > 0
                       or core.investigate_jobs(invoice_id, status="pending")
                       or core.has_unreviewed_evidence(invoice_id))
    if needs_judgement:
        # A partial payment, a customer reply or a new document: not routine,
        # the agent (or, without a model budget, the owner) looks first.
        core.ensure_job(f"investigate:{job_id}", invoice_id, "investigate", job_id, core.clock())
        return "needs judgement: investigation queued"
    action = core.propose(f"auto-{invoice_id}-d{day}", invoice_id, "collection_reminder", evidence,
                          proposed_by="orchestrator",
                          rationale=f"Day {day} after the due date with no payment: routine "
                                    "reminder from the policy cadence.")
    return f"proposed {action['id']}"


# --- judgement without a model budget -----------------------------------

def route_judgement(core):
    """Investigation jobs need a paid model call. When autonomy is on (the
    policy has rules), no model client is available and the daily model budget
    is 0, each waiting job becomes an escalation instead of sitting unseen.
    Policies without rules keep the Milestone 3 behaviour: jobs wait for an
    explicit model client. Spending against a non-zero budget is not enforced
    yet: that arrives with cost tracking in Milestone 5 step 2."""
    policy = core.policy()
    if (policy is None or not policy["autonomy_rules"]
            or policy["daily_model_budget_cents"] > 0):
        return []
    outcomes = []
    for job in core.investigate_jobs(status="pending"):
        try:
            if _escalate_judgement(core, job):
                outcomes.append(job["id"])
        except Exception:  # one bad job must not stop the rest
            log.exception("judgement routing for %s failed", job["id"])
    return outcomes


def _escalate_judgement(core, job):
    case_id = core.case_id(job["invoice_id"])
    if core.case(case_id)["state"] == "resolved":
        return False  # resolving already closes waiting investigations
    core.open_escalation(case_id, "needs_judgement",
                         f"Invoice {job['invoice_id']} needs a judgement call (a customer reply "
                         "or a partial payment) and the policy allows no automatic model calls. "
                         "Review the evidence and decide.", _evidence_ids(core, case_id),
                         "orchestrator", key=job["id"])
    if core.claim_job(job["id"]) is not None:
        core.finish_job(job["id"], "done", "escalated: no model budget")
    return True


# --- 3. autonomy --------------------------------------------------------

def run_autonomy(core):
    outcomes = []
    for action_id in core.auto_candidates():
        try:
            outcomes.append(core.apply_autonomy(action_id))
        except Exception as exc:  # one bad action must not stop the rest
            log.exception("autonomy for %s failed", action_id)
            outcomes.append({"action": action_id, "outcome": "error", "detail": type(exc).__name__})
    return outcomes
