"""FinanceOps HTTP API (Milestones 1-4). Business rules live in core.py, not here.
Response and error shapes are frozen in schemas.py / contracts/openapi.json.

Run:  FINANCEOPS_ENABLE_DEMO=1 FINANCEOPS_OWNER_TOKEN=<secret> \\
        .venv/bin/uvicorn api:create_app --factory
Docs: http://127.0.0.1:8000/docs    Owner console: http://127.0.0.1:8000/app/

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
import os
import sqlite3
from typing import List, Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

import schemas
from core import Conflict, FinanceCore, InvalidInput, NotFound

_FROM_ENV = object()  # "read this setting from the environment"
# IDs end up in case events and in outgoing message text, so allow only plain
# identifier characters: no spaces, newlines or punctuation that could read as prose.
ID_PATTERN = r"^[A-Za-z0-9_.:-]+$"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields are an error, not ignored


class InvoiceIn(_Body):
    invoice_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    customer: StrictStr = Field(min_length=1, max_length=200)
    total_cents: StrictInt = Field(gt=0)


class EvidenceIn(_Body):
    evidence_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    invoice_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    body: StrictStr = Field(min_length=1, max_length=10_000)


class SettlementIn(_Body):
    settlement_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    invoice_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    amount_cents: StrictInt = Field(gt=0)
    currency: Literal["CAD"] = "CAD"


class DisputeIn(_Body):
    invoice_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    disputed: StrictBool


class ProposalIn(_Body):
    action_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    kind: Literal["collection_reminder", "request_information"]
    evidence_ids: List[StrictStr] = Field(min_length=1, max_length=50)
    rationale: Optional[StrictStr] = Field(default=None, max_length=1000)


class ApproveIn(_Body):
    # Required: the fingerprint of the message the owner reviewed (from
    # GET /actions/{id}), so nobody approves text they have not seen.
    payload_hash: StrictStr = Field(min_length=64, max_length=64)


class RejectIn(_Body):
    note: Optional[StrictStr] = Field(default=None, max_length=500)


class PolicyIn(_Body):
    approval_ttl_minutes: StrictInt
    follow_up_days: StrictInt
    max_reminders: StrictInt


class ContactIn(_Body):
    invoice_id: StrictStr = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    address: StrictStr = Field(min_length=3, max_length=254)


class WorkerRunIn(_Body):
    advance_minutes: StrictInt = Field(default=0, ge=0, le=365 * 24 * 60)
    run_investigations: StrictBool = False  # off by default: each one is a paid model call


def _action_out(fc, action_id):
    out = fc.action_detail(action_id)
    out["simulated"] = True  # the only delivery channel is the simulated inbox
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

    app = FastAPI(title="FinanceOps", version="0.4.0",
                  description="Receivables backend. Synthetic data; simulated actions only.",
                  responses=schemas.COMMON_ERRORS)

    # Only answer to local names. Blocks DNS rebinding: a web page that points
    # its own host name at 127.0.0.1 gets a 400 instead of the token-free demo
    # routes. "testserver" is the name FastAPI's TestClient uses.
    from starlette.middleware.trustedhost import TrustedHostMiddleware
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["127.0.0.1", "localhost", "testserver"])

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
        """One database connection per request, always closed afterwards.
        In demo mode the clock is the simulated one (/demo/worker/run moves it)."""
        instance = FinanceCore(db_path, simulated_time=enable_demo)
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

    @app.get("/invoices/{invoice_id}", response_model=schemas.InvoiceOut)
    def get_invoice(invoice_id: str, fc: FinanceCore = Depends(core)):
        return _invoice_out(fc.invoice(invoice_id))

    @app.get("/cases/{case_id}", response_model=schemas.CaseOut)
    def get_case(case_id: str, fc: FinanceCore = Depends(core)):
        return fc.case(case_id)

    # --- owner-only ------------------------------------------------------

    @app.get("/cases", response_model=schemas.CaseListOut, responses=schemas.OWNER_ERRORS)
    def list_cases(_: str = Depends(require_owner), fc: FinanceCore = Depends(core)):
        """Owner-only: lists customer names and amounts."""
        return {"cases": fc.cases()}

    @app.get("/cases/{case_id}/workspace", response_model=schemas.WorkspaceOut, responses=schemas.OWNER_ERRORS)
    def get_workspace(case_id: str, _: str = Depends(require_owner),
                      fc: FinanceCore = Depends(core)):
        """Everything the owner reviews for one case, as one snapshot. Owner-only:
        it holds customer text and the contact address. Evidence is untrusted."""
        return fc.workspace(case_id)

    @app.post("/cases/{case_id}/actions", response_model=schemas.ActionOut, responses=schemas.OWNER_ERRORS)
    def propose_action(case_id: str, body: ProposalIn, proposer: str = Depends(require_owner),
                       fc: FinanceCore = Depends(core)):
        """Manual proposal by the owner. The agent proposes via /investigate."""
        invoice_id = fc.case(case_id)["invoice_id"]
        action = fc.propose(body.action_id, invoice_id, body.kind, body.evidence_ids,
                            proposed_by=proposer, rationale=body.rationale)
        return _action_out(fc, action["id"])

    @app.get("/actions/{action_id}", response_model=schemas.ActionOut, responses=schemas.OWNER_ERRORS)
    def get_action(action_id: str, _: str = Depends(require_owner),
                   fc: FinanceCore = Depends(core)):
        """The approval view: exact message, recipient, hash, approval, delivery."""
        return _action_out(fc, action_id)

    @app.post("/actions/{action_id}/approve", response_model=schemas.ActionOut, responses=schemas.OWNER_ERRORS)
    def approve_action(action_id: str, body: ApproveIn,
                       approver: str = Depends(require_owner), fc: FinanceCore = Depends(core)):
        fc.approve(action_id, approver=approver, payload_hash=body.payload_hash)
        return _action_out(fc, action_id)

    @app.post("/actions/{action_id}/reject", response_model=schemas.ActionOut, responses=schemas.OWNER_ERRORS)
    def reject_action(action_id: str, body: Optional[RejectIn] = None,
                      rejecter: str = Depends(require_owner), fc: FinanceCore = Depends(core)):
        fc.reject(action_id, rejecter=rejecter, note=body.note if body else None)
        return _action_out(fc, action_id)

    @app.post("/actions/{action_id}/execute", response_model=schemas.ActionOut, responses=schemas.OWNER_ERRORS)
    def execute_action(action_id: str, executor: str = Depends(require_owner),
                       fc: FinanceCore = Depends(core)):
        """Queues the approved message in the outbox; the worker delivers it."""
        fc.execute(action_id, executor=executor)
        return _action_out(fc, action_id)

    @app.get("/policy", response_model=schemas.PolicyOut, responses=schemas.OWNER_ERRORS)
    def get_policy(_: str = Depends(require_owner), fc: FinanceCore = Depends(core)):
        policy = fc.policy()
        if policy is None:
            raise HTTPException(404, "No policy stored; sending is disabled")
        return policy

    @app.post("/policy", response_model=schemas.PolicyOut, responses=schemas.OWNER_ERRORS)
    def set_policy(body: PolicyIn, owner: str = Depends(require_owner),
                   fc: FinanceCore = Depends(core)):
        return fc.set_policy(body.approval_ttl_minutes, body.follow_up_days,
                             body.max_reminders, created_by=owner)

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

    @app.post("/cases/{case_id}/investigate", response_model=schemas.AgentRunOut, responses=schemas.OWNER_ERRORS)
    def investigate_case(case_id: str, _: str = Depends(require_owner),
                         fc: FinanceCore = Depends(core)):
        """Owner-only because each run calls a paid model. The result is a
        proposal at most; approval stays a separate owner step."""
        from agent import Investigator
        fc.case(case_id)  # 404 before any model call
        return Investigator(fc, get_model_client()).investigate(case_id)

    @app.get("/agent-runs/{run_id}", response_model=schemas.AgentRunOut, responses=schemas.OWNER_ERRORS)
    def get_agent_run(run_id: str, _: str = Depends(require_owner),
                      fc: FinanceCore = Depends(core)):
        """Owner-only: the trace includes full evidence text the agent read."""
        return fc.agent_run(run_id)

    # --- synthetic demo events (never a provider webhook) ----------------

    if enable_demo:
        from demo_routes import build_demo_router
        app.include_router(build_demo_router(core, require_owner, get_model_client, db_path))

    # The built React app (frontend/dist, from `npm run build`), same origin as
    # the API so no cross-site (CORS) rules are needed. Absent until it is built.
    dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")
    if os.path.isdir(dist):
        from fastapi.staticfiles import StaticFiles
        app.mount("/app", StaticFiles(directory=dist, html=True), name="app")

    return app
