"""Response shapes of the HTTP API (Milestones 4-5): the frozen contract.

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
                    "awaiting_external_outcome", "escalated", "resolved"]
ActionKind = Literal["collection_reminder", "request_information"]
# "superseded": a newer plan for the same case replaced it (kept, never deleted).
ActionStatus = Literal["proposed", "approved", "rejected", "queued", "dispatched",
                       "follow_up_due", "executed_simulated", "cancelled", "failed",
                       "superseded"]
DeliveryStatus = Literal["pending", "sending", "unknown", "sent", "failed", "cancelled"]
RunStatus = Literal["running", "proposed", "no_proposal", "escalated", "failed"]
AgentName = Literal["collections", "briefing"]
AutonomyMode = Literal["auto", "approve", "blocked"]
JobKind = Literal["settlement_check", "investigate", "cadence_step", "briefing"]
# Who moved a case last: the owner, a policy rule (automatic), or nobody yet.
HandledBy = Literal["owner", "policy", "agent", "none"]


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
    due_at: Optional[str]
    days_overdue: Optional[int]  # None when not due, paid, or no due date
    open_escalations: int
    needs_owner: bool  # pending actions or open escalations
    handled_by: HandledBy


class CaseListOut(_Out):
    cases: List[CaseSummary]
    total: int  # matching cases across all pages
    next_cursor: Optional[str]  # pass back as ?cursor= for the next page


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
    due_at: Optional[str]
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


class AutonomyDecision(_Out):
    """How the policy classified an action, evaluated by code (never the model)."""
    mode: AutonomyMode
    rule_id: Optional[str]  # None: no rule matched, so the owner decides
    policy_version: int
    reasons: List[str]  # short code-written reasons, e.g. "invoice disputed"
    decided_at: str


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
    autonomy: Optional[AutonomyDecision]  # None for actions from before Milestone 5
    superseded_by: Optional[str]  # the newer action that replaced this one
    simulated: bool = True


# --- policy, contacts, jobs ---------------------------------------------

class AutonomyRule(_Out):
    """One autonomy rule. Rules are checked in order; the first match decides.
    No match means the owner approves."""
    id: str
    action_kind: ActionKind
    mode: AutonomyMode
    max_amount_cents: Optional[int]  # None: any amount
    require_undisputed: bool
    require_evidence: bool  # at least one evidence document on file
    description: str


class PolicyOut(_Out):
    version: int
    approval_ttl_minutes: int
    follow_up_days: int
    max_reminders: int
    # Empty for policies stored before Milestone 5: nothing runs automatically.
    autonomy_rules: List[AutonomyRule]
    cadence_days: List[int]  # reminder days after the due date, e.g. [3, 7, 14]
    daily_model_budget_cents: int  # 0: no automatic model calls
    created_by: str
    created_at: str


class PolicyPreviewOut(_Out):
    """What a draft policy would do to the open cases right now."""
    open_cases: int
    auto: int
    approve: int
    blocked: int


class ContactOut(_Out):
    invoice_id: str
    channel: str
    address: str
    version: int
    updated_at: str


class JobOut(_Out):
    id: str
    invoice_id: str
    kind: JobKind
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
    agent: AgentName
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


# --- autonomy, escalations, activity, briefings, agents (Milestone 5) ---

class AutonomyStatus(_Out):
    paused: bool  # the owner's kill switch: nothing executes automatically
    changed_by: Optional[str]
    changed_at: Optional[str]
    reason: Optional[str]


class EscalationOut(_Out):
    id: str
    case_id: str
    reason: str  # code, e.g. "dispute", "replan_limit", "budget_exceeded"
    question: str  # what the owner is asked; may quote model text: plain text only
    evidence_ids: List[str]
    raised_by: str  # "orchestrator", "policy", or "agent:<run id>"
    created_at: str
    resolved_at: Optional[str]
    resolved_by: Optional[str]
    resolution_note: Optional[str]


class AttentionApproval(_Out):
    case_id: str
    customer: str
    outstanding_cents: int
    currency: str
    action: ActionOut


class AttentionEscalation(_Out):
    customer: str
    outstanding_cents: int
    currency: str
    escalation: EscalationOut


class AttentionOut(_Out):
    """The owner's "Needs you" queue, oldest first."""
    approvals: List[AttentionApproval]
    escalations: List[AttentionEscalation]
    autonomy: AutonomyStatus


ActivityKind = Literal[
    "event_ingested", "case_state_changed", "action_proposed", "action_auto_approved",
    "action_approved", "action_rejected", "action_superseded", "message_queued",
    "message_sent", "message_cancelled", "agent_run_started", "agent_run_finished",
    "escalation_opened", "escalation_resolved", "briefing_published",
    "autonomy_paused", "autonomy_resumed", "agent_paused", "agent_resumed",
    "policy_changed"]


class ActivityItem(_Out):
    seq: int
    kind: ActivityKind
    actor: str  # "owner", "policy:v<n>/rule:<id>", "agent:<name>", "worker", "orchestrator"
    case_id: Optional[str]
    ref: Optional[str]  # the action, run, escalation or event it is about
    summary: str  # written by code from stored facts
    synthetic: bool
    created_at: str


class ActivityOut(_Out):
    items: List[ActivityItem]  # oldest first
    cursor: int  # highest seq returned (or the one passed); pass back as ?after=


class SummaryOut(_Out):
    """Headline numbers, computed by the backend; the UI never adds them up."""
    currency: str
    outstanding_cents: int
    overdue_cents: int
    open_cases: int
    overdue_cases: int
    awaiting_owner: int  # approvals plus open escalations
    auto_handled_7d: int  # actions approved by a policy rule in the last 7 days
    resolved_7d: int
    model_spend_today_cents: Optional[int]  # None until cost tracking exists
    autonomy_paused: bool
    now: str
    simulated_time: bool


class BriefingItem(_Out):
    text: str  # model-written: plain text only
    case_ids: List[str]
    evidence_ids: List[str]


class BriefingSection(_Out):
    title: str
    items: List[BriefingItem]


class BriefingOut(_Out):
    id: str
    run_id: Optional[str]
    period_start: str
    period_end: str
    headline: str
    sections: List[BriefingSection]
    created_at: str


class AgentInfo(_Out):
    name: AgentName
    title: str
    model: str
    can_write: bool  # False: read-only agent
    paused: bool
    runs_today: int
    outcomes_7d: Dict[str, int]  # run status -> count
    last_run_at: Optional[str]


class AgentListOut(_Out):
    agents: List[AgentInfo]


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
    escalations: List[EscalationOut]  # newest first, open and resolved
    autonomy: AutonomyStatus
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


class ClockOut(_Out):
    now: str
    simulated: bool
    playing: bool  # while playing, the worker loop moves the clock forward
    speed: int  # simulated minutes per real second
    offset_seconds: int


ProblemKind = Literal[
    "none", "late_payer", "partial_unexplained", "partial_agreed_credit", "disputed_work",
    "missing_contract", "mistyped_reference", "promise_to_pay", "claims_paid",
    "prompt_injection"]
ExpectedOutcome = Literal["resolved", "reminded_automatically", "information_requested",
                          "escalated", "owner_approval"]


class GeneratorRunOut(_Out):
    seed: int
    months: int
    customers: int
    invoices: int
    events_scheduled: int
    first_event_at: str
    last_event_at: str
    simulated: bool = True


class TruthItem(_Out):
    invoice_id: str
    problem: ProblemKind
    expected: List[ExpectedOutcome]  # any of these counts as correct
    note: str


class TruthOut(_Out):
    seed: int
    items: List[TruthItem]
