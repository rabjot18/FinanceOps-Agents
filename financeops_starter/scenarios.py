"""Named synthetic demo scenarios (Milestone 4). Synthetic data only.

Each scenario adds its own invoices through the normal core methods, so every
rule (versions, events, closure proof) applies exactly as in real use.
Loading is additive and repeatable: IDs are fixed per scenario, so loading
twice changes nothing, and nothing is ever deleted (the history tables are
append-only by design).

Owned by the scenarios worker in Milestone 4; the route contract lives in
schemas.py (ScenarioInfo, ScenarioLoadOut) and is frozen.
"""
from core import FinanceCore, NotFound

DEFAULT_POLICY = dict(approval_ttl_minutes=60, follow_up_days=7, max_reminders=2)


def _ensure_policy(core):
    # Never replace a policy someone changed: a new version would expire approvals.
    if core.policy() is None:
        core.set_policy(**DEFAULT_POLICY, created_by="scenario")


def _baseline(core):
    """The PRD demo story: a CAD 2,000 shortfall, and a disputed clinic invoice."""
    _ensure_policy(core)
    core.create_invoice("inv-1", "Demo customer (synthetic)", 1_200_000)
    core.set_contact("inv-1", "ap@demo-customer.example")
    core.add_evidence("contract-1", "inv-1", "CAD 12,000 due; no discount agreed.")
    core.record_settlement("bank-1", "inv-1", 1_000_000)

    core.create_invoice("inv-2", "Harbour Clinic (synthetic)", 850_000)
    core.set_contact("inv-2", "billing@harbour-clinic.example")
    core.add_evidence("contract-2", "inv-2", "Agreement: two on-site visits, CAD 4,250 each.")
    core.add_evidence("reply-2", "inv-2", "Customer email: the second site visit never happened; "
                                          "we will not pay the rest until this is sorted out.")
    core.record_settlement("bank-2a", "inv-2", 425_000)
    core.set_disputed("inv-2", True)
    return ["inv-1", "inv-2"]


SCENARIOS = {
    "baseline": {
        "title": "Shortfall and dispute",
        "description": "CAD 2,000 short on one invoice; a clinic disputes a missed site visit.",
        "load": _baseline,
    },
}


def catalog():
    return [{"name": name, "title": s["title"], "description": s["description"]}
            for name, s in SCENARIOS.items()]


def load(core, name):
    """Load a scenario; returns the case IDs it touches. Safe to repeat."""
    if name not in SCENARIOS:
        raise NotFound("Unknown scenario")
    return [FinanceCore.case_id(i) for i in SCENARIOS[name]["load"](core)]
