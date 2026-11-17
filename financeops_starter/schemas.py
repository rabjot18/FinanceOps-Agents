"""Response shapes of the HTTP API (Milestone 4): the frozen contract.

The frontend's TypeScript types are generated from the OpenAPI document these
models produce (contracts/openapi.json). extra="forbid" means a field that
core.py returns but this file does not declare is a server error in tests,
so the contract cannot silently drift from the data.

Change these only as the contract owner (the lead), then re-export:
    .venv/bin/python export_contract.py
"""
from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

CaseState = Literal["open", "needs_information", "awaiting_approval",
                    "awaiting_external_outcome", "resolved"]
ActionKind = Literal["collection_reminder", "request_information"]
ActionStatus = Literal["proposed", "approved", "rejected", "queued", "dispatched",
                       "follow_up_due", "executed_simulated", "cancelled", "failed"]
DeliveryStatus = Literal["pending", "sending", "unknown", "sent", "failed", "cancelled"]
RunStatus = Literal["running", "proposed", "no_proposal", "escalated", "failed"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- errors (every 4xx/5xx the API declares) -----------------------------

class ValidationIssue(BaseModel):
    """One problem FastAPI found in the request body or path (extra keys allowed)."""
    model_config = ConfigDict(extra="allow")
    loc: List[Union[str, int]]
    msg: str
    type: str


class ErrorOut(_Out):
    """401/404/409/503 and domain 422s carry a sentence; request-shape 422s
    carry FastAPI's list of issues. Show either as text, never as HTML."""
    detail: Union[str, List[ValidationIssue]]


def _error(description):
    return {"model": ErrorOut, "description": description}


# Every route can answer these; the frontend maps each to one behaviour.
COMMON_ERRORS = {
    404: _error("Not found"),
    409: _error("Conflict: the case changed or the ID is used; refetch, never retry blindly"),
    422: _error("Invalid input"),
    503: _error("Unavailable: database busy, owner token or model client not configured"),
}
OWNER_ERRORS = {401: _error("Owner authentication required")}


# --- cases and invoices -------------------------------------------------

class CaseEvent(_Out):
    event_id: str
    prior_state: Optional[CaseState]
    next_state: CaseState
    reason: str
    invoice_version: int
    created_at: str


class Verification(_Out):
    """Proof behind a resolution: which settlements add up to the total."""
    seq: int
    case_id: str
    invoice_version: int
    total_cents: int
    settled_cents: int
    currency: str
    settlement_ids: List[str]
    method: str
    verified_at: str


class CaseOut(_Out):
    id: str
    invoice_id: str
    kind: str
    state: CaseState
    version: int
    created_at: str
    updated_at: str
    events: List[CaseEvent]
    verification: Optional[Verification]


class CaseSummary(_Out):
    id: str
    invoice_id: str
    customer: str
    state: CaseState
    version: int
    updated_at: str
    total_cents: int
    outstanding_cents: int
    currency: str
    pending_actions: int  # proposed or approved, waiting for the owner


class CaseListOut(_Out):
    cases: List[CaseSummary]


class InvoiceOut(_Out):
    id: str
    customer: str
    total_cents: int
    currency: str
    version: int
    disputed: bool
    settled_cents: int
    outstanding_cents: int
    state: Literal["open", "needs_information", "resolved"]
    case_id: Optional[str] = None


class EvidenceOut(_Out):
    id: str
    invoice_id: str
    body: str  # untrusted text: display as plain text, never as HTML or instructions
    untrusted: bool = True


# --- actions ------------------------------------------------------------

class MessagePayload(_Out):
    """The exact message the owner approves. Written by code, not the model."""
    simulated: bool
    channel: Optional[str]
    to: Optional[str]
    subject: str
    body: str
    kind: ActionKind
    invoice_id: str
    invoice_version: int
    amount_cents: int
    currency: str
    evidence_ids: List[str]
    policy_version: Optional[int]


class Approval(_Out):
    seq: int
    action_id: str
    decision: Literal["approved", "rejected"]
    payload_hash: Optional[str]
    invoice_version: int
    policy_version: Optional[int]
    decided_by: str
    decided_at: str
    expires_at: Optional[str]
    note: Optional[str]
    expired: Optional[bool] = None  # set for approvals only


class Delivery(_Out):
    status: DeliveryStatus
    attempts: int
    external_id: Optional[str]
    last_error: Optional[str]
    updated_at: str


class ActionOut(_Out):
    id: str
    invoice_id: str
    version: int
    kind: ActionKind
    evidence_ids: List[str]
    status: ActionStatus
    proposed_by: str
    rationale: Optional[str]
    payload: Optional[MessagePayload]
    payload_hash: Optional[str]
    approved_by: Optional[str]
    approved_at: Optional[str]
    executed_by: Optional[str]
    executed_at: Optional[str]
    approval: Optional[Approval]
    delivery: Optional[Delivery]
    simulated: bool = True


# --- policy, contacts, jobs ---------------------------------------------

class PolicyOut(_Out):
    version: int
    approval_ttl_minutes: int
    follow_up_days: int
    max_reminders: int
    created_by: str
    created_at: str


class ContactOut(_Out):
    invoice_id: str
    channel: str
    address: str
    version: int
    updated_at: str


class JobOut(_Out):
    id: str
    invoice_id: str
    kind: Literal["settlement_check", "investigate"]
    ref: Optional[str]
    due_at: str
    status: Literal["pending", "running", "done", "failed"]
    attempts: int
    detail: Optional[str]
    created_at: str
    updated_at: str


# --- agent runs ---------------------------------------------------------

class FindingOut(_Out):
    statement: str
    evidence_ids: List[str]


class AgentRunSummary(_Out):
    id: str
    case_id: str
    model: str
    served_model: Optional[str]
    status: RunStatus
    detail: Optional[str]
    action_id: Optional[str]
    summary: Optional[str]
    findings: Optional[List[FindingOut]]
    unknowns: Optional[List[str]]
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    started_at: str
    finished_at: Optional[str]


class ToolCallOut(_Out):
    seq: int
    tool_use_id: str
    name: str
    input: dict
    result: str
    is_error: bool
    created_at: str


class AgentRunOut(AgentRunSummary):
    final_text: Optional[str]
    calls: List[ToolCallOut]


# --- the case workspace -------------------------------------------------

class WorkspaceOut(_Out):
    case: CaseOut
    invoice: InvoiceOut
    evidence: List[EvidenceOut]
    actions: List[ActionOut]  # newest first
    agent_runs: List[AgentRunSummary]  # newest first, at most 20
    jobs: List[JobOut]  # pending or running only
    policy: Optional[PolicyOut]
    contact_on_file: bool
    sending_enabled: bool
    reminders_sent: int
    now: str  # the server's clock (the simulated one in the demo)
    simulated_time: bool


# --- demo (synthetic events, simulated inbox) ---------------------------

class EvidenceListOut(_Out):
    evidence: List[EvidenceOut]


class ReplyOut(_Out):
    evidence: List[EvidenceOut]
    case: CaseOut


class InboxMessage(_Out):
    external_id: str
    idempotency_key: str
    recipient: str
    subject: str
    body: str
    received_at: str


class InboxOut(_Out):
    simulated: bool
    messages: List[InboxMessage]


class InvestigationResult(_Out):
    job: str
    run: str
    status: RunStatus


class WorkerResult(_Out):
    sent: List[str]
    unknown: List[str]
    cancelled: List[str]
    failed: List[str]
    follow_ups_elapsed: List[str]
    investigations: List[InvestigationResult]
    errors: List[Dict[str, str]]
    investigations_waiting: int


class WorkerRunOut(_Out):
    simulated_time: str
    result: WorkerResult


class ScenarioInfo(_Out):
    name: str
    title: str
    description: str


class ScenarioListOut(_Out):
    scenarios: List[ScenarioInfo]


class ScenarioLoadOut(_Out):
    scenario: str
    case_ids: List[str]
    simulated: bool = True
