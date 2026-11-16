"""FinanceOps HTTP API (Milestone 1). Business rules live in core.py, not here.

Run:  FINANCEOPS_ENABLE_DEMO=1 FINANCEOPS_OWNER_TOKEN=<secret> \\
        .venv/bin/uvicorn api:create_app --factory
Docs: http://127.0.0.1:8000/docs

Environment:
  FINANCEOPS_DB           SQLite file (default financeops.sqlite3)
  FINANCEOPS_OWNER_TOKEN  owner approval token; unset = approvals disabled
  FINANCEOPS_ENABLE_DEMO  "1" exposes /demo/* synthetic-event routes (default off)

The owner token is a single shared secret for a local demo. It is not a user
login system and must not be treated as production authentication. The
/demo/* routes need no token, so enable them only on 127.0.0.1 (uvicorn's
default bind address), never on a shared network.
"""
import hmac
import json
import os
import sqlite3
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from core import Conflict, FinanceCore, InvalidInput, NotFound

_FROM_ENV = object()  # "read this setting from the environment"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are an error, not ignored


class InvoiceIn(_Body):
    invoice_id: StrictStr = Field(min_length=1, max_length=64)
    customer: StrictStr = Field(min_length=1, max_length=200)
    total_cents: StrictInt = Field(gt=0)


class EvidenceIn(_Body):
    evidence_id: StrictStr = Field(min_length=1, max_length=64)
    invoice_id: StrictStr = Field(min_length=1, max_length=64)
    body: StrictStr = Field(min_length=1, max_length=10_000)


class SettlementIn(_Body):
    settlement_id: StrictStr = Field(min_length=1, max_length=64)
    invoice_id: StrictStr = Field(min_length=1, max_length=64)
    amount_cents: StrictInt = Field(gt=0)
    currency: Literal["CAD"] = "CAD"


class DisputeIn(_Body):
    invoice_id: StrictStr = Field(min_length=1, max_length=64)
    disputed: StrictBool


class ProposalIn(_Body):
    action_id: StrictStr = Field(min_length=1, max_length=64)
    kind: Literal["collection_reminder", "request_information"]
    evidence_ids: List[StrictStr] = Field(min_length=1, max_length=50)


def _action_out(action):
    out = dict(action)
    out["evidence_ids"] = json.loads(out["evidence_ids"])
    out["simulated"] = True  # Milestone 1 has no real dispatch adapter
    return out


def _invoice_out(invoice):
    return {**invoice, "case_id": FinanceCore.case_id(invoice["id"])}


def create_app(db_path=_FROM_ENV, owner_token=_FROM_ENV, enable_demo=_FROM_ENV,
               agent_client=None):
    """agent_client: an Anthropic client (or a test fake). None = create the
    real client on first use, so the server starts without model credentials."""
    if db_path is _FROM_ENV:
        db_path = os.environ.get("FINANCEOPS_DB", "financeops.sqlite3")
    if owner_token is _FROM_ENV:
        owner_token = os.environ.get("FINANCEOPS_OWNER_TOKEN") or None
    if enable_demo is _FROM_ENV:
        enable_demo = os.environ.get("FINANCEOPS_ENABLE_DEMO", "0") == "1"

    FinanceCore(db_path).close()  # apply schema migrations once at startup

    app = FastAPI(title="FinanceOps", version="0.1.0",
                  description="Milestone 1 receivables backend. Synthetic data; simulated actions only.")

    # Turn domain errors into HTTP status codes in one place.
    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(Conflict)
    async def _conflict(_: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InvalidInput)
    async def _invalid(_: Request, exc: InvalidInput):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    # Another request holds the write lock for longer than the 5 s timeout.
    # Any other unexpected error stays a plain 500 with no internal details.
    @app.exception_handler(sqlite3.OperationalError)
    async def _busy(_: Request, exc: sqlite3.OperationalError):
        if "locked" not in str(exc) and "busy" not in str(exc):
            raise exc
        return JSONResponse(status_code=503, content={"detail": "Database busy; retry shortly"},
                            headers={"Retry-After": "1"})

    def core():
        """One database connection per request, always closed afterwards."""
        instance = FinanceCore(db_path)
        try:
            yield instance
        finally:
            instance.close()

    def require_owner(authorization: Optional[str] = Header(default=None)) -> str:
        if not owner_token:
            raise HTTPException(503, "Approvals are disabled: no owner token is configured")
        scheme, _, supplied = (authorization or "").partition(" ")
        # compare_digest takes the same time whether the first or last character
        # differs, so the token cannot be guessed by timing responses.
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.encode(), owner_token.encode()
        ):
            raise HTTPException(401, "Owner authentication required",
                                headers={"WWW-Authenticate": "Bearer"})
        return "owner"

    # --- reads -----------------------------------------------------------

    @app.get("/invoices/{invoice_id}")
    def get_invoice(invoice_id: str, fc: FinanceCore = Depends(core)):
        return _invoice_out(fc.invoice(invoice_id))

    @app.get("/cases/{case_id}")
    def get_case(case_id: str, fc: FinanceCore = Depends(core)):
        return fc.case(case_id)

    # --- proposals (the future agent's write path) -----------------------

    @app.post("/cases/{case_id}/actions")
    def propose_action(case_id: str, body: ProposalIn, fc: FinanceCore = Depends(core)):
        invoice_id = fc.case(case_id)["invoice_id"]
        return _action_out(fc.propose(body.action_id, invoice_id, body.kind, body.evidence_ids))

    # --- owner-only ------------------------------------------------------

    @app.post("/actions/{action_id}/approve")
    def approve_action(action_id: str, approver: str = Depends(require_owner),
                       fc: FinanceCore = Depends(core)):
        return _action_out(fc.approve(action_id, approver=approver))

    @app.post("/actions/{action_id}/execute")
    def execute_action(action_id: str, executor: str = Depends(require_owner),
                       fc: FinanceCore = Depends(core)):
        return _action_out(fc.execute_simulated(action_id, executor=executor))

    # --- AI investigation (Milestone 2) ----------------------------------

    model_client = {"client": agent_client}

    def get_model_client():
        if model_client["client"] is None:
            import anthropic
            from agent import CLIENT_MAX_RETRIES, CLIENT_TIMEOUT_SECONDS
            try:
                model_client["client"] = anthropic.Anthropic(
                    timeout=CLIENT_TIMEOUT_SECONDS, max_retries=CLIENT_MAX_RETRIES)
            except anthropic.AnthropicError:
                raise HTTPException(503, "Model client is not configured")
        return model_client["client"]

    @app.post("/cases/{case_id}/investigate")
    def investigate_case(case_id: str, _: str = Depends(require_owner),
                         fc: FinanceCore = Depends(core)):
        """Owner-only because each run calls a paid model. The result is a
        proposal at most; approval stays a separate owner step."""
        from agent import Investigator
        fc.case(case_id)  # 404 before any model call
        return Investigator(fc, get_model_client()).investigate(case_id)

    @app.get("/agent-runs/{run_id}")
    def get_agent_run(run_id: str, _: str = Depends(require_owner),
                      fc: FinanceCore = Depends(core)):
        """Owner-only: the trace includes full evidence text the agent read."""
        return fc.agent_run(run_id)

    # --- synthetic demo events (never a provider webhook) ----------------

    if enable_demo:
        demo = APIRouter(prefix="/demo", tags=["demo: synthetic events"])

        @demo.post("/invoices")
        def demo_invoice(body: InvoiceIn, fc: FinanceCore = Depends(core)):
            return _invoice_out(fc.create_invoice(body.invoice_id, body.customer, body.total_cents))

        @demo.post("/evidence")
        def demo_evidence(body: EvidenceIn, fc: FinanceCore = Depends(core)):
            fc.add_evidence(body.evidence_id, body.invoice_id, body.body)
            return {"evidence": fc.evidence(body.invoice_id)}

        @demo.post("/settlements")
        def demo_settlement(body: SettlementIn, fc: FinanceCore = Depends(core)):
            return _invoice_out(fc.record_settlement(
                body.settlement_id, body.invoice_id, body.amount_cents, body.currency))

        @demo.post("/disputes")
        def demo_dispute(body: DisputeIn, fc: FinanceCore = Depends(core)):
            fc.set_disputed(body.invoice_id, body.disputed)
            return _invoice_out(fc.invoice(body.invoice_id))

        app.include_router(demo)

    return app
