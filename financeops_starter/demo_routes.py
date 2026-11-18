"""Synthetic demo routes (/demo/*). Never a provider webhook.

Mounted by api.create_app only when FINANCEOPS_ENABLE_DEMO=1. They need no
token (except paid re-investigations), so enable them only on 127.0.0.1.
Response shapes are frozen in schemas.py; request shapes come from api.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header

import generator
import scenarios
import schemas
from adapters import SimulatedInbox
from core import FinanceCore


def build_demo_router(core, require_owner, get_model_client, db_path):
    """core: the per-request FinanceCore dependency; require_owner(authorization)
    raises unless the owner token matches; get_model_client() returns the model client."""
    # Imported here: api.py imports this module, and the request models live there.
    from api import (ClockIn, ContactIn, DemoRunIn, DisputeIn, EvidenceIn, GeneratorIn, InvoiceIn,
                     SettlementIn, WorkerRunIn, _invoice_out)

    demo = APIRouter(prefix="/demo", tags=["demo: synthetic events"])

    @demo.post("/invoices", response_model=schemas.InvoiceOut)
    def demo_invoice(body: InvoiceIn, fc: FinanceCore = Depends(core)):
        return _invoice_out(fc.create_invoice(body.invoice_id, body.customer, body.total_cents,
                                              due_at=body.due_at))

    @demo.post("/evidence", response_model=schemas.EvidenceListOut)
    def demo_evidence(body: EvidenceIn, fc: FinanceCore = Depends(core)):
        fc.add_evidence(body.evidence_id, body.invoice_id, body.body)
        return {"evidence": fc.evidence(body.invoice_id)}

    @demo.post("/settlements", response_model=schemas.InvoiceOut)
    def demo_settlement(body: SettlementIn, fc: FinanceCore = Depends(core)):
        return _invoice_out(fc.record_settlement(
            body.settlement_id, body.invoice_id, body.amount_cents, body.currency))

    @demo.post("/disputes", response_model=schemas.InvoiceOut)
    def demo_dispute(body: DisputeIn, fc: FinanceCore = Depends(core)):
        fc.set_disputed(body.invoice_id, body.disputed)
        return _invoice_out(fc.invoice(body.invoice_id))

    @demo.post("/contacts", response_model=schemas.ContactOut)
    def demo_contact(body: ContactIn, fc: FinanceCore = Depends(core)):
        return fc.set_contact(body.invoice_id, body.address)

    @demo.post("/replies", response_model=schemas.ReplyOut)
    def demo_reply(body: EvidenceIn, fc: FinanceCore = Depends(core)):
        """A synthetic customer reply: new evidence plus a re-investigation job."""
        fc.record_reply(body.evidence_id, body.invoice_id, body.body)
        return {"evidence": fc.evidence(body.invoice_id),
                "case": fc.case_for_invoice(body.invoice_id)}

    @demo.get("/inbox", response_model=schemas.InboxOut)
    def demo_inbox():
        return {"simulated": True, "messages": SimulatedInbox(db_path).messages()}

    @demo.post("/worker/run", response_model=schemas.WorkerRunOut,
               responses=schemas.OWNER_ERRORS)  # 401 only when re-investigating
    def demo_worker_run(body: Optional[WorkerRunIn] = None, fc: FinanceCore = Depends(core),
                        authorization: Optional[str] = Header(default=None)):
        """Run one worker round, optionally after moving the SIMULATED clock forward.
        Re-investigations call a paid model, so they need the owner token."""
        from worker import run_once
        body = body or WorkerRunIn()
        if body.run_investigations:
            require_owner(authorization)
        if body.advance_minutes:
            fc.advance_simulated_time(body.advance_minutes * 60)
        factory = None
        if body.run_investigations:
            from agent import Investigator
            client = get_model_client()
            factory = lambda c: Investigator(c, client, enforce_budget=True)  # noqa: E731
        result = run_once(fc, SimulatedInbox(db_path), factory)
        return {"simulated_time": fc.clock(), "result": result}

    @demo.get("/scenarios", response_model=schemas.ScenarioListOut)
    def demo_scenarios():
        return {"scenarios": scenarios.catalog()}

    @demo.post("/scenarios/{name}", response_model=schemas.ScenarioLoadOut)
    def demo_load_scenario(name: str, fc: FinanceCore = Depends(core)):
        """Add a named synthetic scenario's cases. Repeatable; deletes nothing."""
        return {"scenario": name, "case_ids": scenarios.load(fc, name)}

    # --- Milestone 5: simulated clock playback and the business generator ---

    @demo.get("/clock", response_model=schemas.ClockOut)
    def demo_clock(fc: FinanceCore = Depends(core)):
        return fc.clock_status()

    @demo.post("/clock", response_model=schemas.ClockOut)
    def demo_set_clock(body: ClockIn, fc: FinanceCore = Depends(core)):
        """play / pause / speed (simulated minutes per real second). While
        playing, the worker loop moves the SIMULATED clock forward."""
        return fc.set_playback(body.action, body.speed)

    @demo.post("/generator/run", response_model=schemas.GeneratorRunOut)
    def demo_generate(body: GeneratorIn, fc: FinanceCore = Depends(core)):
        """Generate a fictional business (same seed, same data) and schedule its timeline."""
        # Starts at the current simulated time; a repeated seed adds nothing, the
        # same seed with another size is a 409. The orchestrator applies events.
        return generator.load(fc, body.seed, body.months, body.customers)

    @demo.get("/generator/truth", response_model=schemas.TruthOut)
    def demo_truth(seed: int, fc: FinanceCore = Depends(core)):
        """The planted problems and their expected outcomes, for evaluation."""
        # NotFound (404) for a seed that was never generated.
        return generator.truth(fc, seed)

    # --- Milestone 6: one button runs a reactive business ----------------

    @demo.post("/run", response_model=schemas.DemoRunOut)
    def demo_run(body: DemoRunIn, fc: FinanceCore = Depends(core)):
        """Load a reactive synthetic business (simulated customers answer what the
        agents actually send) and start playback. The worker loop does the rest."""
        return generator.load_reactive(fc, body.seed, body.customers, body.months, body.speed)

    return demo
