"""Synthetic demo routes (/demo/*). Never a provider webhook.

Mounted by api.create_app only when FINANCEOPS_ENABLE_DEMO=1. They need no
token (except paid re-investigations), so enable them only on 127.0.0.1.
Response shapes are frozen in schemas.py; request shapes come from api.py.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Header

import scenarios
import schemas
from adapters import SimulatedInbox
from core import FinanceCore


def build_demo_router(core, require_owner, get_model_client, db_path):
    """core: the per-request FinanceCore dependency; require_owner(authorization)
    raises unless the owner token matches; get_model_client() returns the model client."""
    # Imported here: api.py imports this module, and the request models live there.
    from api import ContactIn, DisputeIn, EvidenceIn, InvoiceIn, SettlementIn, WorkerRunIn, _invoice_out

    demo = APIRouter(prefix="/demo", tags=["demo: synthetic events"])

    @demo.post("/invoices", response_model=schemas.InvoiceOut)
    def demo_invoice(body: InvoiceIn, fc: FinanceCore = Depends(core)):
        return _invoice_out(fc.create_invoice(body.invoice_id, body.customer, body.total_cents))

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

    @demo.post("/worker/run", response_model=schemas.WorkerRunOut)
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
            factory = lambda c: Investigator(c, client)  # noqa: E731
        result = run_once(fc, SimulatedInbox(db_path), factory)
        return {"simulated_time": fc.clock(), "result": result}

    @demo.get("/scenarios", response_model=schemas.ScenarioListOut)
    def demo_scenarios():
        return {"scenarios": scenarios.catalog()}

    @demo.post("/scenarios/{name}", response_model=schemas.ScenarioLoadOut)
    def demo_load_scenario(name: str, fc: FinanceCore = Depends(core)):
        """Add a named synthetic scenario's cases. Repeatable; deletes nothing."""
        return {"scenario": name, "case_ids": scenarios.load(fc, name)}

    return demo
