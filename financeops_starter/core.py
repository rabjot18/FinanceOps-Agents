"""FinanceOps receivables core. Local synthetic data only; no external payments.

Milestone 1 adds persistent cases and an append-only case event log on top of
the starter's invoices, settlements, evidence, and actions. This module has no
web or AI dependencies: the HTTP layer (api.py) and the future agent both call it.

All amounts are CAD cents. Provider authenticity must be checked by a future
adapter before calling record_settlement().

Milestone 3 separates proposal, approval, dispatch, waiting and verification:
an owner approves the exact message (bound to its hash, with an expiry);
execute() only queues it in the outbox; worker.py delivers it to a simulated
inbox; a follow-up job checks for payment later; and a case resolves only when
recorded settlements add up to the invoice total (stored as a verification).
"""
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import messages

SCHEMA_VERSION = 6

ACTION_KINDS = {"collection_reminder", "request_information"}
# Action status after the owner's step, by stage. "executed_simulated" is the
# original in-process shortcut (execute_simulated), kept for the starter tests.
_PENDING = ("proposed", "approved")
_WAITING = ("queued", "dispatched", "executed_simulated")
_PAST_APPROVAL = ("queued", "dispatched", "follow_up_due", "executed_simulated")
_CLOSED = ("rejected", "cancelled", "failed", "superseded")
# Reminders that count toward the policy limit (sent, or on their way).
_REMINDER_COUNTED = ("queued", "dispatched", "follow_up_due", "executed_simulated")

MAX_DISPATCH_ATTEMPTS = 5
# A message still marked "sending" after this long: the worker died mid-send.
# The worker then asks the inbox whether it arrived before trying again.
SENDING_STALE_SECONDS = 120
MAX_JOB_ATTEMPTS = 3
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# How an investigation run can end (Milestone 2). "running" is the only
# state in which a run may still change; the database freezes the rest.
AGENT_RUN_OUTCOMES = {"proposed", "no_proposal", "escalated", "failed"}
# A run still "running" after this long was killed mid-way (crash, restart);
# the next investigation of that case marks it failed instead of waiting on it.
STALE_RUN_SECONDS = 15 * 60

# --- Milestone 5: autonomy, agents, escalations ----------------------------
AUTONOMY_MODES = {"auto", "approve", "blocked"}
DEFAULT_CADENCE_DAYS = [3, 7, 14]  # reminder days after the due date
AUTO_REMINDER_LIMIT_CENTS = 500_000  # CAD 5,000: the owner's chosen default
# Suggested rules for a new policy. Checked in order; the first match decides;
# no match means the owner approves. Stored policies from before Milestone 5
# have no rules, so nothing runs automatically until the owner saves new ones.
DEFAULT_AUTONOMY_RULES = [
    {"id": "reminder-small", "action_kind": "collection_reminder", "mode": "auto",
     "max_amount_cents": AUTO_REMINDER_LIMIT_CENTS, "require_undisputed": True,
     "require_evidence": True,
     "description": "Routine reminder on an undisputed invoice under CAD 5,000"},
    {"id": "request-info", "action_kind": "request_information", "mode": "auto",
     "max_amount_cents": None, "require_undisputed": False, "require_evidence": False,
     "description": "Ask the customer for missing information"},
]
# The agents the orchestrator may run. Models are configurable per agent.
AGENTS = {
    "collections": {"title": "Collections agent", "can_write": True,
                    "model_env": "FINANCEOPS_MODEL", "default_model": "claude-opus-5-5"},
    "briefing": {"title": "Owner briefing agent", "can_write": False,
                 "model_env": "FINANCEOPS_BRIEFING_MODEL", "default_model": "claude-haiku-5-5"},
}
ACTIVITY_KINDS = {
    "event_ingested", "case_state_changed", "action_proposed", "action_auto_approved",
    "action_approved", "action_rejected", "action_superseded", "message_queued",
    "message_sent", "message_cancelled", "agent_run_started", "agent_run_finished",
    "escalation_opened", "escalation_resolved", "briefing_published",
    "autonomy_paused", "autonomy_resumed", "agent_paused", "agent_resumed",
    "policy_changed"}
CASE_LIST_SORTS = {"updated", "outstanding", "overdue", "customer"}
MAX_PLAYBACK_SPEED = 7 * 24 * 60  # simulated minutes per real second (one week)
# The shared event format (Milestone 5). Every input — scenario, generator,
# demo route, later Stripe/Plaid adapters — becomes one of these kinds with
# exactly these payload fields. The orchestrator applies an event by calling
# the matching FinanceCore method (create_invoice, set_contact, add_evidence,
# record_settlement, set_disputed, record_reply).
EVENT_SPECS = {
    "invoice_created": {"invoice_id": str, "customer": str, "total_cents": int,
                        "due_at": (str, type(None))},
    "contact_set": {"invoice_id": str, "address": str},
    "evidence_added": {"evidence_id": str, "invoice_id": str, "body": str},
    "settlement_received": {"settlement_id": str, "invoice_id": str, "amount_cents": int,
                            "currency": str},
    "dispute_changed": {"invoice_id": str, "disputed": bool},
    "reply_received": {"evidence_id": str, "invoice_id": str, "body": str},
}
EVENT_SOURCES = {"scenario", "generator", "demo", "api", "stripe", "plaid", "csv"}
# Model prices in US cents per million tokens (Anthropic first-party API,
# November 2026). Cache writes (5-minute TTL) cost 1.25x input. A model not
# listed is priced as the most expensive one here, so the budget errs safe.
MODEL_PRICES = {
    "claude-opus-5-5": {"input": 400, "output": 2000, "cache_read": 20},
    "claude-haiku-5-5": {"input": 10, "output": 50, "cache_read": 1},
}
CACHE_WRITE_MULTIPLIER = 1.25
MAX_REPLANS = 2  # superseded agent plans per case before the owner is asked
# Agent information requests may go out automatically (under the rules) only
# when this is switched on, after a live evaluation with the real model shows
# no unauthorized actions. Off by default: the agent still investigates,
# escalates, replans and briefs on its own; its messages wait for the owner.
AGENT_MESSAGES_AUTOMATIC = os.environ.get("FINANCEOPS_AGENT_AUTOSEND", "0") == "1"
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


class NotFound(ValueError):
    """A referenced record does not exist."""


class Conflict(ValueError):
    """The request clashes with stored data or the current case/plan state."""


class InvalidInput(ValueError):
    """The caller supplied a malformed or unsupported value."""


# Phase 1 subset of the PRD §7 case states. A case's state is recomputed from
# stored facts after every change; this table says which moves are legal, so a
# bug that tries an illegal jump fails loudly instead of corrupting the case.
CASE_TRANSITIONS = {
    "open": {"awaiting_approval", "needs_information", "escalated", "resolved"},
    "needs_information": {"open", "awaiting_approval", "escalated", "resolved"},
    "awaiting_approval": {"open", "needs_information", "awaiting_external_outcome", "escalated",
                          "resolved"},
    "awaiting_external_outcome": {"open", "needs_information", "awaiting_approval", "escalated",
                                  "resolved"},
    # Milestone 5: an open escalation waits for the owner; resolving it moves on.
    "escalated": {"open", "needs_information", "awaiting_approval", "awaiting_external_outcome",
                  "resolved"},
    # Reopening on reversal/returned payment is a later milestone.
    "resolved": {"needs_information"},
}
# Proposers whose plans the autonomy rules may execute. The owner's own
# proposals always wait for the owner.
AUTOMATIC_PROPOSERS = ("orchestrator", "agent:")

_SCHEMA_V1 = [
    """CREATE TABLE IF NOT EXISTS invoices (
        id TEXT PRIMARY KEY, customer TEXT NOT NULL,
        total INTEGER NOT NULL CHECK(total > 0),
        version INTEGER NOT NULL DEFAULT 1,
        disputed INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS settlements (
        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
        amount INTEGER NOT NULL CHECK(amount > 0), currency TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS evidence (
        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, body TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS actions (
        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
        version INTEGER NOT NULL, kind TEXT NOT NULL,
        evidence_ids TEXT NOT NULL, status TEXT NOT NULL
    )""",
]

_SCHEMA_V2 = [
    """CREATE TABLE cases (
        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, state TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE case_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT NOT NULL REFERENCES cases(id),
        event_id TEXT NOT NULL,
        prior_state TEXT, next_state TEXT NOT NULL,
        reason TEXT NOT NULL, invoice_version INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(case_id, event_id)
    )""",
    # The event log is history: the database itself refuses edits and deletes.
    """CREATE TRIGGER case_events_no_update BEFORE UPDATE ON case_events
       BEGIN SELECT RAISE(ABORT, 'case_events is append-only'); END""",
    """CREATE TRIGGER case_events_no_delete BEFORE DELETE ON case_events
       BEGIN SELECT RAISE(ABORT, 'case_events is append-only'); END""",
    "ALTER TABLE actions ADD COLUMN approved_by TEXT",
    "ALTER TABLE actions ADD COLUMN approved_at TEXT",
    "ALTER TABLE actions ADD COLUMN executed_by TEXT",
    "ALTER TABLE actions ADD COLUMN executed_at TEXT",
]

_SCHEMA_V3 = [
    # Who proposed an action: "api" (a person/client) or "agent:<run id>".
    "ALTER TABLE actions ADD COLUMN proposed_by TEXT NOT NULL DEFAULT 'api'",
    """CREATE TABLE agent_runs (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        model TEXT NOT NULL, status TEXT NOT NULL, detail TEXT,
        action_id TEXT, summary TEXT, findings TEXT, unknowns TEXT, final_text TEXT,
        tool_calls INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        served_model TEXT,
        started_at TEXT NOT NULL, finished_at TEXT
    )""",
    """CREATE TABLE agent_tool_calls (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES agent_runs(id),
        tool_use_id TEXT NOT NULL, name TEXT NOT NULL,
        input TEXT NOT NULL, result TEXT NOT NULL, is_error INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TRIGGER agent_runs_frozen BEFORE UPDATE ON agent_runs WHEN OLD.status != 'running'
       BEGIN SELECT RAISE(ABORT, 'finished agent runs are read-only'); END""",
    """CREATE TRIGGER agent_runs_no_delete BEFORE DELETE ON agent_runs
       BEGIN SELECT RAISE(ABORT, 'agent_runs is append-only'); END""",
    """CREATE TRIGGER agent_tool_calls_no_update BEFORE UPDATE ON agent_tool_calls
       BEGIN SELECT RAISE(ABORT, 'agent_tool_calls is append-only'); END""",
    """CREATE TRIGGER agent_tool_calls_no_delete BEFORE DELETE ON agent_tool_calls
       BEGIN SELECT RAISE(ABORT, 'agent_tool_calls is append-only'); END""",
]


def _append_only(table):
    return [
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
    ]


_SCHEMA_V4 = [
    # Each policy change is a new version; the newest one applies.
    """CREATE TABLE policies (
        version INTEGER PRIMARY KEY,
        approval_ttl_minutes INTEGER NOT NULL CHECK(approval_ttl_minutes > 0),
        follow_up_days INTEGER NOT NULL CHECK(follow_up_days > 0),
        max_reminders INTEGER NOT NULL CHECK(max_reminders >= 0),
        created_by TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    *_append_only("policies"),
    """CREATE TABLE contacts (
        invoice_id TEXT PRIMARY KEY REFERENCES invoices(id),
        channel TEXT NOT NULL, address TEXT NOT NULL,
        version INTEGER NOT NULL, updated_at TEXT NOT NULL
    )""",
    # The exact message (JSON) and its fingerprint; the agent's summary.
    "ALTER TABLE actions ADD COLUMN payload TEXT",
    "ALTER TABLE actions ADD COLUMN payload_hash TEXT",
    "ALTER TABLE actions ADD COLUMN rationale TEXT",
    """CREATE TABLE approvals (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        action_id TEXT NOT NULL REFERENCES actions(id),
        decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected')),
        payload_hash TEXT, invoice_version INTEGER NOT NULL, policy_version INTEGER,
        decided_by TEXT NOT NULL, decided_at TEXT NOT NULL, expires_at TEXT, note TEXT
    )""",
    *_append_only("approvals"),
    # Transactional outbox: written in the same transaction as the owner's
    # execute step. The row ID is the action ID and doubles as the
    # idempotency key the inbox uses to ignore repeats.
    """CREATE TABLE outbox (
        id TEXT PRIMARY KEY REFERENCES actions(id),
        invoice_id TEXT NOT NULL, channel TEXT NOT NULL, recipient TEXT NOT NULL,
        payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
            ('pending', 'sending', 'unknown', 'sent', 'failed', 'cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL, claimed_at TEXT,
        external_id TEXT, last_error TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    # Timers that survive restarts. IDs are derived from what caused them,
    # so a replayed event cannot schedule the same job twice.
    """CREATE TABLE jobs (
        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id),
        kind TEXT NOT NULL CHECK(kind IN ('settlement_check', 'investigate')),
        ref TEXT, due_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'done', 'failed')),
        attempts INTEGER NOT NULL DEFAULT 0, detail TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    # Proof behind every resolution: which settlements add up to the total.
    """CREATE TABLE case_verifications (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id TEXT NOT NULL REFERENCES cases(id),
        invoice_version INTEGER NOT NULL,
        total_cents INTEGER NOT NULL, settled_cents INTEGER NOT NULL,
        currency TEXT NOT NULL, settlement_ids TEXT NOT NULL,
        method TEXT NOT NULL, verified_at TEXT NOT NULL,
        UNIQUE(case_id, invoice_version)
    )""",
    *_append_only("case_verifications"),
    # Demo-only simulated clock: an offset added to real time, so "7 days
    # later" can be shown without waiting. Used only when simulated_time=True.
    "CREATE TABLE sim_clock (id INTEGER PRIMARY KEY CHECK(id = 1), offset_seconds INTEGER NOT NULL)",
    "INSERT INTO sim_clock VALUES (1, 0)",
]

_SCHEMA_V6 = [
    # Every model call's tokens, for the daily model budget (Milestone 5 step 2).
    """CREATE TABLE model_usage (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, model TEXT NOT NULL,
        ref TEXT, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,
        cache_read_tokens INTEGER NOT NULL, cache_write_tokens INTEGER NOT NULL,
        cost_micro_cents INTEGER NOT NULL, created_at TEXT NOT NULL
    )""",
]

_SCHEMA_V5 = [
    # Milestone 5. Due dates drive the reminder cadence.
    "ALTER TABLE invoices ADD COLUMN due_at TEXT",
    # The autonomy decision (JSON) made by code when the action was stored,
    # and the newer action that replaced this one after a replan.
    "ALTER TABLE actions ADD COLUMN autonomy TEXT",
    "ALTER TABLE actions ADD COLUMN superseded_by TEXT",
    # NULL rules: a pre-Milestone 5 policy, under which nothing runs automatically.
    "ALTER TABLE policies ADD COLUMN autonomy_rules TEXT",
    "ALTER TABLE policies ADD COLUMN cadence_days TEXT",
    "ALTER TABLE policies ADD COLUMN daily_model_budget_cents INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agent_runs ADD COLUMN agent TEXT NOT NULL DEFAULT 'collections'",
    # Jobs gain two kinds; SQLite cannot change a CHECK, so rebuild the table.
    """CREATE TABLE jobs_v5 (
        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id),
        kind TEXT NOT NULL CHECK(kind IN
            ('settlement_check', 'investigate', 'cadence_step', 'briefing')),
        ref TEXT, due_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'done', 'failed')),
        attempts INTEGER NOT NULL DEFAULT 0, detail TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    "INSERT INTO jobs_v5 SELECT id, invoice_id, kind, ref, due_at, status, attempts, detail, "
    "created_at, updated_at FROM jobs",
    "DROP TABLE jobs",
    "ALTER TABLE jobs_v5 RENAME TO jobs",
    # Every input, normalized, with where it came from. dedupe_key makes a
    # replayed or re-imported event a no-op.
    """CREATE TABLE events (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL,
        source TEXT NOT NULL CHECK(source IN
            ('scenario', 'generator', 'demo', 'api', 'stripe', 'plaid', 'csv')),
        source_ref TEXT, dedupe_key TEXT NOT NULL UNIQUE,
        synthetic INTEGER NOT NULL, payload TEXT NOT NULL,
        received_at TEXT NOT NULL, routed_at TEXT
    )""",
    # What the system did, for the owner's live feed. Written by code.
    """CREATE TABLE activity (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, actor TEXT NOT NULL,
        case_id TEXT, ref TEXT, summary TEXT NOT NULL,
        synthetic INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
    )""",
    *_append_only("activity"),
    """CREATE TABLE escalations (
        id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
        reason TEXT NOT NULL, question TEXT NOT NULL, evidence_ids TEXT NOT NULL,
        raised_by TEXT NOT NULL, created_at TEXT NOT NULL,
        resolved_at TEXT, resolved_by TEXT, resolution_note TEXT
    )""",
    # The owner's kill switch for automatic execution.
    """CREATE TABLE autonomy_state (
        id INTEGER PRIMARY KEY CHECK(id = 1), paused INTEGER NOT NULL,
        changed_by TEXT, changed_at TEXT, reason TEXT
    )""",
    "INSERT INTO autonomy_state VALUES (1, 0, NULL, NULL, NULL)",
    """CREATE TABLE agent_controls (
        name TEXT PRIMARY KEY, paused INTEGER NOT NULL, changed_by TEXT, changed_at TEXT
    )""",
    """CREATE TABLE briefings (
        id TEXT PRIMARY KEY, run_id TEXT, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
        headline TEXT NOT NULL, sections TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    *_append_only("briefings"),
    # Demo only: whether the simulated clock is playing, and how fast.
    """CREATE TABLE sim_playback (
        id INTEGER PRIMARY KEY CHECK(id = 1), playing INTEGER NOT NULL,
        speed INTEGER NOT NULL, updated_at TEXT
    )""",
    "INSERT INTO sim_playback VALUES (1, 0, 60, NULL)",
    # Demo only: generated businesses and their ground truth (JSON).
    """CREATE TABLE generator_runs (
        seed INTEGER PRIMARY KEY, months INTEGER NOT NULL, customers INTEGER NOT NULL,
        truth TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
]


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _at(iso):
    return datetime.fromisoformat(iso)


def _later(iso, **delta):
    return (_at(iso) + timedelta(**delta)).isoformat()


class FinanceCore:
    def __init__(self, path="financeops.sqlite3", clock=_utc_now, simulated_time=False):
        """simulated_time: demo only. The clock becomes real time plus the
        stored offset that advance_simulated_time() moves forward."""
        # check_same_thread=False: the web server may open the connection on one
        # worker thread and use it on another. Each FinanceCore is still used by
        # one request at a time; it is never shared between requests.
        self.db = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.clock = clock
        try:
            # WAL lets readers continue while one writer holds the lock. The
            # setting is stored in the file, so only switch it once.
            if self.db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                self.db.execute("PRAGMA journal_mode=WAL")
            self._migrate()
        except Exception:
            self.db.close()  # never leak the connection if setup fails
            raise
        self.simulated_time = simulated_time
        if simulated_time:
            self.clock = self._simulated_now

    def _simulated_now(self):
        offset = self.db.execute("SELECT offset_seconds FROM sim_clock").fetchone()[0]
        return (datetime.now(timezone.utc) + timedelta(seconds=offset)).isoformat()

    def advance_simulated_time(self, seconds):
        if not self.simulated_time:
            raise Conflict("Simulated time is off; it is only available in the demo")
        if type(seconds) is not int or seconds < 0:
            raise InvalidInput("seconds must be a non-negative integer")
        with self.transaction():
            self.db.execute("UPDATE sim_clock SET offset_seconds=offset_seconds+?", (seconds,))
        return self.clock()

    # --- schema ----------------------------------------------------------

    def _schema_version(self):
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone():
            return 0
        return self.db.execute("SELECT version FROM schema_meta").fetchone()[0]

    def _migrate(self):
        if self._schema_version() >= SCHEMA_VERSION:
            return
        with self.transaction():
            current = self._schema_version()  # re-check under the write lock
            if current < 1:
                for statement in _SCHEMA_V1:
                    self.db.execute(statement)
            if current < 2:
                for statement in _SCHEMA_V2:
                    self.db.execute(statement)
            if current < 3:
                for statement in _SCHEMA_V3:
                    self.db.execute(statement)
            if current < 4:
                for statement in _SCHEMA_V4:
                    self.db.execute(statement)
            if current < 5:
                for statement in _SCHEMA_V5:
                    self.db.execute(statement)
            if current < 6:
                for statement in _SCHEMA_V6:
                    self.db.execute(statement)
            if current < 2:
                # Backfill cases only once every table the rules read exists.
                for row in self.db.execute("SELECT id FROM invoices ORDER BY id").fetchall():
                    self._record(row["id"], "migration:v2", "migrated")
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
            self.db.execute("DELETE FROM schema_meta")
            self.db.execute("INSERT INTO schema_meta VALUES (?)", (SCHEMA_VERSION,))

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:  # includes Ctrl-C in the worker loop
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    # --- money and invoices ----------------------------------------------

    @staticmethod
    def money(value):
        if type(value) is not int or value <= 0:
            raise InvalidInput("Use positive integer cents")

    def invoice(self, invoice_id):
        row = self.db.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown invoice")
        settled = self.db.execute(
            "SELECT COALESCE(SUM(amount),0) FROM settlements WHERE invoice_id=?",
            (invoice_id,),
        ).fetchone()[0]
        result = dict(row)
        # Money leaves core as explicit cents with its currency (CAD only for now).
        result["total_cents"] = result.pop("total")
        result.update(settled_cents=settled, outstanding_cents=row["total"] - settled,
                      currency="CAD")
        result["state"] = (
            "needs_information" if row["disputed"] else
            "resolved" if settled == row["total"] else "open"
        )
        return result

    @staticmethod
    def _timestamp(value, name):
        """An ISO 8601 date-time with a time zone, normalized to UTC."""
        if type(value) is not str:
            raise InvalidInput(f"{name} must be an ISO 8601 date-time")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise InvalidInput(f"{name} must be an ISO 8601 date-time") from None
        if parsed.tzinfo is None:
            raise InvalidInput(f"{name} needs a time zone, e.g. 2026-11-30T00:00:00+00:00")
        return parsed.astimezone(timezone.utc).isoformat()

    def create_invoice(self, invoice_id, customer, total_cents, due_at=None):
        self.money(total_cents)
        if due_at is not None:
            due_at = self._timestamp(due_at, "due_at")
        with self.transaction():
            prior = self.db.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
            if prior:
                if (prior["customer"] != customer or prior["total"] != total_cents
                        or (due_at is not None and prior["due_at"] != due_at)):
                    raise Conflict("Invoice ID reused with different data")
            else:
                self.db.execute("INSERT INTO invoices(id,customer,total,due_at) VALUES(?,?,?,?)",
                                (invoice_id, customer, total_cents, due_at))
                self._record(invoice_id, f"invoice:{invoice_id}", "invoice_created")
        return self.invoice(invoice_id)

    def add_evidence(self, evidence_id, invoice_id, body):
        with self.transaction():
            self._store_evidence(evidence_id, invoice_id, body, "evidence_added")

    def record_reply(self, evidence_id, invoice_id, body):
        """A customer reply: stored as evidence (which makes older plans stale)
        and, in the same transaction, a job to investigate the case again."""
        with self.transaction():
            if self._store_evidence(evidence_id, invoice_id, body, "customer_reply_received"):
                now = self.clock()
                self.db.execute(
                    "INSERT OR IGNORE INTO jobs(id,invoice_id,kind,ref,due_at,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (f"investigate:{evidence_id}", invoice_id, "investigate", evidence_id, now,
                     "pending", now, now))

    def _store_evidence(self, evidence_id, invoice_id, body, reason):
        """Returns True if this is new evidence, False for a harmless replay."""
        self.invoice(invoice_id)
        prior = self.db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
        if prior:
            if prior["invoice_id"] != invoice_id or prior["body"] != body:
                raise Conflict("Evidence is immutable; use a new ID")
            return False
        self.db.execute("INSERT INTO evidence VALUES(?,?,?)", (evidence_id, invoice_id, body))
        self.db.execute("UPDATE invoices SET version=version+1 WHERE id=?", (invoice_id,))
        self._record(invoice_id, f"evidence:{evidence_id}", reason)
        return True

    def evidence(self, invoice_id):
        self.invoice(invoice_id)
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM evidence WHERE invoice_id=? ORDER BY id", (invoice_id,))]

    def set_disputed(self, invoice_id, disputed):
        if type(disputed) is not bool:
            raise InvalidInput("disputed must be boolean")
        with self.transaction():
            if bool(self.invoice(invoice_id)["disputed"]) == disputed:
                return  # replayed or redundant event: nothing changed, plans stay valid
            self.db.execute("UPDATE invoices SET disputed=?,version=version+1 WHERE id=?",
                            (int(disputed), invoice_id))
            version = self.invoice(invoice_id)["version"]
            self._record(invoice_id, f"dispute:v{version}",
                         "dispute_opened" if disputed else "dispute_cleared")

    def record_settlement(self, settlement_id, invoice_id, amount_cents, currency="CAD"):
        """Demo trusted-adapter boundary; never expose directly as a public webhook."""
        self.money(amount_cents)
        if currency != "CAD":
            raise InvalidInput("Only CAD supported")
        with self.transaction():
            current = self.invoice(invoice_id)
            prior = self.db.execute("SELECT * FROM settlements WHERE id=?", (settlement_id,)).fetchone()
            if prior:
                if (prior["invoice_id"], prior["amount"], prior["currency"]) != (
                    invoice_id, amount_cents, currency
                ):
                    raise Conflict("Settlement ID reused with different data")
            else:
                if amount_cents > current["outstanding_cents"]:
                    raise Conflict("Overpayment requires review; do not auto-allocate")
                self.db.execute("INSERT INTO settlements VALUES(?,?,?,?)",
                                (settlement_id, invoice_id, amount_cents, currency))
                self.db.execute("UPDATE invoices SET version=version+1 WHERE id=?", (invoice_id,))
                self._record(invoice_id, f"settlement:{settlement_id}", "settlement_recorded")
        return self.invoice(invoice_id)

    # --- actions ---------------------------------------------------------

    def propose(self, action_id, invoice_id, kind, evidence_ids,
                proposed_by="api", expected_version=None, rationale=None):
        """The runtime agent may propose these actions, never approve itself.

        expected_version: the invoice version the proposer last read. If the
        records changed since then, the proposal is refused so the proposer
        must look at the new evidence first. The exact outgoing message is
        rendered by code here and stored with its hash; rationale is the
        proposer's short explanation, shown to the owner, never sent.
        """
        if kind not in ACTION_KINDS:
            raise InvalidInput("Unsupported action")
        if not isinstance(evidence_ids, list) or not evidence_ids or not all(
            isinstance(x, str) for x in evidence_ids
        ):
            raise InvalidInput("At least one evidence reference is required")
        refs = json.dumps(sorted(set(evidence_ids)))
        with self.transaction():
            current = self.invoice(invoice_id)
            if expected_version is not None and expected_version != current["version"]:
                raise Conflict("Case records changed since you read them; read them again before proposing")
            known = {e["id"] for e in self.evidence(invoice_id)}
            if not set(evidence_ids) <= known:
                raise InvalidInput("Unknown evidence reference")
            prior = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if not prior and self._derive_state(invoice_id) == "resolved":
                raise Conflict("Case is resolved; no new actions can be proposed")
            if prior:
                if (prior["invoice_id"], prior["version"], prior["kind"], prior["evidence_ids"]) != (
                    invoice_id, current["version"], kind, refs
                ):
                    raise Conflict("Action ID reused with different or stale data")
            else:
                payload = self._render(kind, invoice_id, evidence_ids)
                self.db.execute(
                    "INSERT INTO actions(id,invoice_id,version,kind,evidence_ids,status,proposed_by,"
                    "payload,payload_hash,rationale) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (action_id, invoice_id, current["version"], kind, refs, "proposed", proposed_by,
                     messages.canonical_json(payload), messages.payload_hash(payload), rationale))
                if proposed_by.startswith(AUTOMATIC_PROPOSERS):
                    # A newer automatic plan replaces older ones still waiting.
                    for old in self.db.execute(
                            "SELECT id FROM actions WHERE invoice_id=? AND id != ? AND "
                            "status='proposed' AND (proposed_by='orchestrator' OR "
                            "proposed_by LIKE 'agent:%')", (invoice_id, action_id)).fetchall():
                        self.db.execute("UPDATE actions SET status='superseded', superseded_by=? "
                                        "WHERE id=?", (action_id, old["id"]))
                        self._activity("action_superseded", proposed_by,
                                       f"Replaced plan {old['id']} with {action_id}",
                                       case_id=self.case_id(invoice_id), ref=old["id"])
                # Code decides autonomy after the proposal is stored; the
                # proposer (model or orchestrator) never sees or sets it.
                decision = self._autonomy_for(self.action(action_id))
                self.db.execute("UPDATE actions SET autonomy=? WHERE id=?",
                                (json.dumps(decision), action_id))
                self._record(invoice_id, f"action:{action_id}:proposed", "action_proposed")
                self._activity("action_proposed", proposed_by,
                               f"Proposed {kind.replace('_', ' ')} for {invoice_id} "
                               f"({decision['mode']}: {'; '.join(decision['reasons'])})",
                               case_id=self.case_id(invoice_id), ref=action_id)
        return self.action(action_id)

    def action(self, action_id):
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown action")
        return dict(row)

    def action_detail(self, action_id):
        """Everything the owner needs to decide: exact message, approval, delivery."""
        action = self.action(action_id)
        action["evidence_ids"] = json.loads(action["evidence_ids"])
        action["payload"] = json.loads(action["payload"]) if action["payload"] else None
        action["autonomy"] = json.loads(action["autonomy"]) if action["autonomy"] else None
        approval = self._latest_approval(action_id)
        action["approval"] = dict(approval) if approval else None
        if approval and approval["decision"] == "approved":
            action["approval"]["expired"] = self._expired(approval, self.clock())
        outbox = self.db.execute(
            "SELECT status, attempts, external_id, last_error, updated_at FROM outbox WHERE id=?",
            (action_id,)).fetchone()
        action["delivery"] = dict(outbox) if outbox else None
        return action

    def _render(self, kind, invoice_id, evidence_ids):
        return messages.render(kind, self.invoice(invoice_id), evidence_ids,
                               self._contact(invoice_id), self.policy())

    def validate_action(self, action):
        """Is this action still right for the case as it is now? Raises Conflict if not."""
        current = self.invoice(action["invoice_id"])
        if action["version"] != current["version"]:
            raise Conflict("Stale plan: investigate and propose again")
        if action["kind"] == "collection_reminder" and (
            current["disputed"] or current["outstanding_cents"] == 0
        ):
            raise Conflict("Cannot chase a disputed or fully settled invoice")
        # Actions from before Milestone 3 have no stored message to compare.
        if action.get("payload_hash"):
            fresh = self._render(action["kind"], action["invoice_id"],
                                 json.loads(action["evidence_ids"]))
            if messages.payload_hash(fresh) != action["payload_hash"]:
                raise Conflict("The message changed since it was proposed (recipient or policy); "
                               "propose again")
        policy = self.policy()
        if action["kind"] == "collection_reminder" and policy is not None:
            if self.reminders_sent(action["invoice_id"], excluding=action["id"]) >= policy["max_reminders"]:
                raise Conflict(f"Reminder limit reached under policy v{policy['version']}; "
                               "the owner must decide the next step")

    def reminders_sent(self, invoice_id, excluding=None):
        marks = ",".join("?" * len(_REMINDER_COUNTED))
        return self.db.execute(
            f"SELECT COUNT(*) FROM actions WHERE invoice_id=? AND kind='collection_reminder' "
            f"AND id != ? AND status IN ({marks})",
            (invoice_id, excluding or "", *_REMINDER_COUNTED)).fetchone()[0]

    def approve(self, action_id, approver="owner", payload_hash=None):
        """Approve the exact message. The caller (api.py) must authenticate the approver.

        payload_hash: the fingerprint of the message the owner looked at. If
        given, it must match, so nobody approves text they did not see. The
        approval expires after the policy's approval_ttl_minutes.
        """
        with self.transaction():
            self._approve_locked(action_id, approver, payload_hash)
        return self.action(action_id)

    def _approve_locked(self, action_id, approver, payload_hash):
        """approve() inside the caller's transaction."""
        action = self.action(action_id)
        if action["status"] in _PAST_APPROVAL:
            return action
        if action["status"] in _CLOSED:
            raise Conflict(f"Action is {action['status']}; propose again")
        if not action["payload_hash"]:
            raise Conflict("This action predates exact-message approval; reject it and propose again")
        self.validate_action(action)
        if payload_hash is not None and payload_hash != action["payload_hash"]:
            raise Conflict("This is not the message you reviewed; reload it and approve again")
        now = self.clock()
        latest = self._latest_approval(action_id)
        if action["status"] == "approved" and latest is not None and not self._expired(latest, now):
            return action  # replayed approval: nothing new
        policy = self.policy()
        expires_at = _later(now, minutes=policy["approval_ttl_minutes"]) if policy else None
        self.db.execute(
            "INSERT INTO approvals(action_id,decision,payload_hash,invoice_version,policy_version,"
            "decided_by,decided_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (action_id, "approved", action["payload_hash"], action["version"],
             policy["version"] if policy else None, approver, now, expires_at))
        self.db.execute(
            "UPDATE actions SET status='approved', approved_by=?, approved_at=? WHERE id=?",
            (approver, now, action_id))
        count = self.db.execute("SELECT COUNT(*) FROM approvals WHERE action_id=? AND "
                                "decision='approved'", (action_id,)).fetchone()[0]
        if count == 1:
            self._record(action["invoice_id"], f"action:{action_id}:approved", "action_approved")
        else:
            self._record(action["invoice_id"], f"action:{action_id}:approved:{count}",
                         "action_reapproved")
        automatic = approver.startswith("policy:")
        self._activity("action_auto_approved" if automatic else "action_approved", approver,
                       f"{'Automatically approved' if automatic else 'Approved'} "
                       f"{action['kind'].replace('_', ' ')} for {action['invoice_id']}",
                       case_id=self.case_id(action["invoice_id"]), ref=action_id)

    def reject(self, action_id, rejecter="owner", note=None):
        with self.transaction():
            self._reject_locked(action_id, rejecter, note)
        return self.action(action_id)

    def _reject_locked(self, action_id, rejecter, note):
        """reject() inside the caller's transaction."""
        action = self.action(action_id)
        if action["status"] == "rejected":
            return action
        if action["status"] not in _PENDING:
            raise Conflict(f"Only a pending action can be rejected; this one is {action['status']}")
        self.db.execute(
            "INSERT INTO approvals(action_id,decision,payload_hash,invoice_version,policy_version,"
            "decided_by,decided_at,note) VALUES(?,?,?,?,?,?,?,?)",
            (action_id, "rejected", action["payload_hash"], action["version"],
             (self.policy() or {}).get("version"), rejecter, self.clock(), note))
        self.db.execute("UPDATE actions SET status='rejected' WHERE id=?", (action_id,))
        self._record(action["invoice_id"], f"action:{action_id}:rejected", "action_rejected")
        self._activity("action_rejected", rejecter,
                       f"Rejected {action['kind'].replace('_', ' ')} for {action['invoice_id']}",
                       case_id=self.case_id(action["invoice_id"]), ref=action_id)

    def _latest_approval(self, action_id):
        return self.db.execute("SELECT * FROM approvals WHERE action_id=? ORDER BY seq DESC LIMIT 1",
                               (action_id,)).fetchone()

    @staticmethod
    def _expired(approval, now):
        return approval["expires_at"] is not None and _at(now) >= _at(approval["expires_at"])

    def execute(self, action_id, executor="owner"):
        """Queue the approved message for delivery. Sends nothing by itself.

        The outbox row and the status change are saved in one transaction, so
        a crash can never leave one without the other. worker.py delivers it.
        """
        with self.transaction():
            self._execute_locked(action_id, executor)
        return self.action(action_id)

    def _execute_locked(self, action_id, executor):
        """execute() inside the caller's transaction."""
        action = self.action(action_id)
        if action["status"] in ("queued", "dispatched", "follow_up_due"):
            return action
        if action["status"] != "approved":
            raise Conflict("Owner approval required")
        self.validate_action(action)
        if not action["payload_hash"]:
            raise Conflict("This action predates exact-message approval; propose it again")
        policy = self.policy()
        if policy is None:
            raise Conflict("No policy is stored, so sending is disabled")
        payload = json.loads(action["payload"])
        if not payload["to"]:
            raise Conflict("No contact on file for this customer; add one and propose again")
        now = self.clock()
        approval = self._latest_approval(action_id)
        if (approval is None or approval["decision"] != "approved"
                or approval["payload_hash"] != action["payload_hash"]
                or approval["expires_at"] is None):
            raise Conflict("No approval matches this exact message; approve it again")
        if self._expired(approval, now):
            raise Conflict(f"Approval expired at {approval['expires_at']}; review and approve again")
        self.db.execute(
            "INSERT INTO outbox(id,invoice_id,channel,recipient,payload,payload_hash,status,"
            "next_attempt_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (action_id, action["invoice_id"], payload["channel"], payload["to"],
             action["payload"], action["payload_hash"], "pending", now, now, now))
        self.db.execute(
            "UPDATE actions SET status='queued', executed_by=?, executed_at=? WHERE id=?",
            (executor, now, action_id))
        self._record(action["invoice_id"], f"action:{action_id}:queued", "action_queued")
        self._activity("message_queued", executor,
                       f"Queued {action['kind'].replace('_', ' ')} to {payload['to']} "
                       "(simulated inbox)",
                       case_id=self.case_id(action["invoice_id"]), ref=action_id)

    def execute_simulated(self, action_id, executor="owner"):
        """Original starter shortcut: records simulated dispatch in-process,
        with no outbox and no message. Kept for the starter tests and demo.py;
        the HTTP API uses execute() instead."""
        with self.transaction():
            action = self.action(action_id)
            if action["status"] == "executed_simulated":
                return action
            if action["status"] != "approved":
                raise Conflict("Owner approval required")
            self.validate_action(action)
            self.db.execute(
                "UPDATE actions SET status='executed_simulated', executed_by=?, executed_at=? WHERE id=?",
                (executor, self.clock(), action_id))
            self._record(action["invoice_id"], f"action:{action_id}:executed", "action_executed_simulated")
        return self.action(action_id)

    # --- cases -----------------------------------------------------------

    @staticmethod
    def case_id(invoice_id):
        return f"case-{invoice_id}"

    def case(self, case_id):
        # One read transaction, so the state and the events are the same snapshot.
        self.db.execute("BEGIN")
        try:
            return self._case_snapshot(case_id)
        finally:
            self.db.rollback()  # read-only: nothing to commit

    def _case_snapshot(self, case_id):
        row = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown case")
        result = dict(row)
        result["events"] = [dict(r) for r in self.db.execute(
            "SELECT event_id, prior_state, next_state, reason, invoice_version, created_at "
            "FROM case_events WHERE case_id=? ORDER BY seq", (case_id,))]
        result["verification"] = None
        if row["state"] == "resolved":
            proof = self.db.execute(
                "SELECT * FROM case_verifications WHERE case_id=? ORDER BY seq DESC LIMIT 1",
                (case_id,)).fetchone()
            if proof:
                result["verification"] = {**dict(proof),
                                          "settlement_ids": json.loads(proof["settlement_ids"])}
        return result

    def cases(self):
        """Every case with the figures a list screen needs, newest change first.
        Money is computed here, never by the caller."""
        rows = self.db.execute(
            "SELECT c.id, c.invoice_id, c.state, c.version, c.updated_at, i.customer, "
            "i.total AS total_cents, "
            "i.total - COALESCE((SELECT SUM(amount) FROM settlements s "
            "                    WHERE s.invoice_id = i.id), 0) AS outstanding_cents, "
            "(SELECT COUNT(*) FROM actions a WHERE a.invoice_id = i.id "
            f"  AND a.status IN ({','.join('?' * len(_PENDING))})) AS pending_actions "
            "FROM cases c JOIN invoices i ON i.id = c.invoice_id "
            "ORDER BY c.updated_at DESC, c.id", _PENDING).fetchall()
        now = _at(self.clock())
        result = []
        for r in rows:
            row = {**dict(r), "currency": "CAD"}
            row["due_at"] = self.db.execute("SELECT due_at FROM invoices WHERE id=?",
                                            (r["invoice_id"],)).fetchone()[0]
            row["days_overdue"] = self._days_overdue(row["due_at"], r["outstanding_cents"], now)
            row["open_escalations"] = self.db.execute(
                "SELECT COUNT(*) FROM escalations WHERE case_id=? AND resolved_at IS NULL",
                (r["id"],)).fetchone()[0]
            row["needs_owner"] = r["pending_actions"] > 0 or row["open_escalations"] > 0
            row["handled_by"] = self._handled_by(r["invoice_id"])
            result.append(row)
        return result

    @staticmethod
    def _days_overdue(due_at, outstanding_cents, now):
        if due_at is None or outstanding_cents <= 0 or now <= _at(due_at):
            return None
        return (now - _at(due_at)).days

    def _handled_by(self, invoice_id):
        """Who moved this case last: a policy rule, the owner, or the agent."""
        latest = self.db.execute(
            "SELECT proposed_by, approved_by FROM actions WHERE invoice_id=? "
            "ORDER BY rowid DESC LIMIT 1", (invoice_id,)).fetchone()
        if latest is None:
            return "none"
        if (latest["approved_by"] or "").startswith("policy:"):
            return "policy"
        if latest["approved_by"]:
            return "owner"
        return "agent" if latest["proposed_by"].startswith("agent:") else "owner"

    def case_list(self, state=None, q=None, sort="updated", limit=50, cursor=None):
        """One page of the case list, filtered and sorted by the backend."""
        if sort not in CASE_LIST_SORTS:
            raise InvalidInput(f"sort must be one of {sorted(CASE_LIST_SORTS)}")
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidInput("limit must be an integer from 1 to 200")
        if cursor is not None and not (cursor.isdigit() and len(cursor) <= 9):
            raise InvalidInput("cursor must come from a previous page")
        rows = self.cases()
        if state is not None:
            rows = [r for r in rows if r["state"] == state]
        if q:
            needle = q.casefold()
            rows = [r for r in rows if needle in r["customer"].casefold()
                    or needle in r["invoice_id"].casefold() or needle in r["id"].casefold()]
        if sort == "outstanding":
            rows.sort(key=lambda r: (-r["outstanding_cents"], r["id"]))
        elif sort == "overdue":
            rows.sort(key=lambda r: (-(r["days_overdue"] if r["days_overdue"] is not None
                                       else -1), r["id"]))
        elif sort == "customer":
            rows.sort(key=lambda r: (r["customer"].casefold(), r["id"]))
        start = int(cursor) if cursor else 0
        page = rows[start:start + limit]
        more = start + limit < len(rows)
        return {"cases": page, "total": len(rows),
                "next_cursor": str(start + limit) if more else None}

    def workspace(self, case_id):
        """Everything the owner reviews for one case, read as one snapshot.
        Evidence is customer- or document-supplied text: labelled untrusted."""
        self.db.execute("BEGIN")
        try:
            case = self._case_snapshot(case_id)
            invoice_id = case["invoice_id"]
            action_ids = [r["id"] for r in self.db.execute(
                "SELECT id FROM actions WHERE invoice_id=? ORDER BY rowid DESC", (invoice_id,))]
            runs = self.db.execute(
                "SELECT id FROM agent_runs WHERE case_id=? ORDER BY started_at DESC, id LIMIT 20",
                (case_id,)).fetchall()
            policy = self.policy()
            contact = self._contact(invoice_id) is not None
            return {
                "case": case,
                "invoice": self.invoice(invoice_id),
                "evidence": [{**e, "untrusted": True} for e in self.evidence(invoice_id)],
                "actions": [self.action_detail(a) for a in action_ids],
                "agent_runs": [self._run_summary(r["id"]) for r in runs],
                "jobs": [dict(r) for r in self.db.execute(
                    "SELECT * FROM jobs WHERE invoice_id=? AND status IN ('pending','running') "
                    "ORDER BY due_at, id", (invoice_id,))],
                "policy": policy,
                "contact_on_file": contact,
                "sending_enabled": policy is not None and contact,
                "reminders_sent": self.reminders_sent(invoice_id),
                "escalations": self.escalations(case_id=case_id),
                "autonomy": self.autonomy_status(),
                "now": self.clock(),
                "simulated_time": self.simulated_time,
            }
        finally:
            self.db.rollback()

    def _run_summary(self, run_id):
        """A run's conclusions without its raw trace (that stays at agent_run())."""
        run = self.agent_run(run_id)
        run.pop("calls")
        run.pop("final_text")
        return run

    def case_for_invoice(self, invoice_id):
        return self.case(self.case_id(invoice_id))

    def _case_row(self, case_id):
        row = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown case")
        return row

    # --- agent runs (Milestone 2) ----------------------------------------
    # A run's trace is stored as it happens, so a crash mid-run still leaves
    # a record. The model's private reasoning is never passed to these methods.

    def start_agent_run(self, case_id, model, agent="collections"):
        if agent not in AGENTS:
            raise InvalidInput("Unknown agent")
        run_id = f"run-{uuid.uuid4().hex}"
        with self.transaction():
            invoice_id = self._case_row(case_id)["invoice_id"]
            if self._derive_state(invoice_id) == "resolved":
                raise Conflict("Case is resolved; nothing to investigate")
            now = self.clock()
            for other in self.db.execute(
                "SELECT id, started_at FROM agent_runs WHERE case_id=? AND status='running'",
                (case_id,)).fetchall():
                age = (datetime.fromisoformat(now) - datetime.fromisoformat(other["started_at"]))
                if age.total_seconds() < STALE_RUN_SECONDS:
                    raise Conflict("An investigation is already running for this case")
                self.db.execute("UPDATE agent_runs SET status='failed', detail='abandoned', "
                                "finished_at=? WHERE id=?", (now, other["id"]))
                self._record(invoice_id, f"agent:{other['id']}:finished", "investigation_failed")
            self.db.execute(
                "INSERT INTO agent_runs(id,case_id,model,status,started_at,agent) VALUES(?,?,?,?,?,?)",
                (run_id, case_id, model, "running", self.clock(), agent))
            self._record(invoice_id, f"agent:{run_id}:started", "investigation_started")
            self._activity("agent_run_started", f"agent:{agent}",
                           f"{AGENTS[agent]['title']} started on {invoice_id}",
                           case_id=case_id, ref=run_id)
        return run_id

    def record_agent_call(self, run_id, tool_use_id, name, tool_input, result, is_error):
        with self.transaction():
            self._running_run(run_id)
            self.db.execute(
                "INSERT INTO agent_tool_calls(run_id,tool_use_id,name,input,result,is_error,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (run_id, tool_use_id, name, json.dumps(tool_input, sort_keys=True), result,
                 int(bool(is_error)), self.clock()))
            self.db.execute("UPDATE agent_runs SET tool_calls=tool_calls+1 WHERE id=?", (run_id,))

    def add_agent_usage(self, run_id, input_tokens, output_tokens,
                        cache_read_tokens=0, cache_write_tokens=0, served_model=None):
        with self.transaction():
            run = self._running_run(run_id)
            self._record_model_usage(run["agent"], served_model or run["model"], run_id,
                                     input_tokens, output_tokens, cache_read_tokens,
                                     cache_write_tokens)
            self.db.execute(
                "UPDATE agent_runs SET input_tokens=input_tokens+?, output_tokens=output_tokens+?, "
                "cache_read_tokens=cache_read_tokens+?, cache_write_tokens=cache_write_tokens+?, "
                "served_model=COALESCE(?, served_model) WHERE id=?",
                (int(input_tokens or 0), int(output_tokens or 0), int(cache_read_tokens or 0),
                 int(cache_write_tokens or 0), served_model, run_id))

    def finish_agent_run(self, run_id, status, detail=None, action_id=None,
                         proposal=None, final_text=None):
        if status not in AGENT_RUN_OUTCOMES:
            raise InvalidInput("Unknown agent run outcome")
        proposal = proposal or {}
        with self.transaction():
            run = self._running_run(run_id)
            self.db.execute(
                "UPDATE agent_runs SET status=?, detail=?, action_id=?, summary=?, findings=?, "
                "unknowns=?, final_text=?, finished_at=? WHERE id=?",
                (status, detail, action_id, proposal.get("summary"),
                 json.dumps(proposal["findings"]) if "findings" in proposal else None,
                 json.dumps(proposal["unknowns"]) if "unknowns" in proposal else None,
                 (final_text or "")[:2000] or None, self.clock(), run_id))
            invoice_id = self._case_row(run["case_id"])["invoice_id"]
            self._record(invoice_id, f"agent:{run_id}:finished", f"investigation_{status}")
            self._activity("agent_run_finished", f"agent:{run['agent']}",
                           f"{AGENTS[run['agent']]['title']} finished on {invoice_id}: {status}",
                           case_id=run["case_id"], ref=run_id)
        return self.agent_run(run_id)

    def fail_agent_run_if_running(self, run_id, detail):
        """Best-effort cleanup after an unexpected error; never raises Conflict."""
        try:
            return self.finish_agent_run(run_id, "failed", detail)
        except Conflict:
            return self.agent_run(run_id)  # it had already finished

    def _running_run(self, run_id):
        run = self.db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise NotFound("Unknown agent run")
        if run["status"] != "running":
            raise Conflict("Agent run already finished")
        return run

    def agent_run(self, run_id):
        row = self.db.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown agent run")
        result = dict(row)
        for field in ("findings", "unknowns"):
            result[field] = json.loads(result[field]) if result[field] else None
        result["calls"] = []
        for call in self.db.execute(
            "SELECT seq,tool_use_id,name,input,result,is_error,created_at "
            "FROM agent_tool_calls WHERE run_id=? ORDER BY seq", (run_id,)
        ):
            call = dict(call)
            call["input"] = json.loads(call["input"])
            call["is_error"] = bool(call["is_error"])
            result["calls"].append(call)
        return result

    # --- policy and contacts (Milestone 3) -------------------------------

    def policy(self):
        row = self.db.execute("SELECT * FROM policies ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            return None
        policy = dict(row)
        policy["autonomy_rules"] = json.loads(row["autonomy_rules"] or "[]")
        policy["cadence_days"] = json.loads(row["cadence_days"] or "[]")
        return policy

    @staticmethod
    def _check_rules(rules):
        if type(rules) is not list or len(rules) > 20:
            raise InvalidInput("autonomy_rules must be a list of at most 20 rules")
        keys = {"id", "action_kind", "mode", "max_amount_cents", "require_undisputed",
                "require_evidence", "description"}
        seen = set()
        for rule in rules:
            if type(rule) is not dict or set(rule) != keys:
                raise InvalidInput(f"each rule needs exactly these fields: {sorted(keys)}")
            if type(rule["id"]) is not str or not _RULE_ID.match(rule["id"]) or rule["id"] in seen:
                raise InvalidInput("rule ids must be unique, lower-case letters, digits and -")
            seen.add(rule["id"])
            if rule["action_kind"] not in ACTION_KINDS:
                raise InvalidInput(f"rule {rule['id']}: unknown action_kind")
            if rule["mode"] not in AUTONOMY_MODES:
                raise InvalidInput(f"rule {rule['id']}: mode must be auto, approve or blocked")
            cap = rule["max_amount_cents"]
            if cap is not None and (type(cap) is not int or cap <= 0):
                raise InvalidInput(f"rule {rule['id']}: max_amount_cents must be positive cents or null")
            if type(rule["require_undisputed"]) is not bool or type(rule["require_evidence"]) is not bool:
                raise InvalidInput(f"rule {rule['id']}: require_* fields must be true or false")
            if type(rule["description"]) is not str or not 1 <= len(rule["description"]) <= 200:
                raise InvalidInput(f"rule {rule['id']}: description must be 1-200 characters")

    @staticmethod
    def _check_cadence(days):
        if (type(days) is not list or len(days) > 10
                or any(type(d) is not int or not 1 <= d <= 365 for d in days)
                or days != sorted(set(days))):
            raise InvalidInput("cadence_days must be up to 10 increasing whole days from 1 to 365")

    def set_policy(self, approval_ttl_minutes, follow_up_days, max_reminders, created_by="owner",
                   autonomy_rules=None, cadence_days=None, daily_model_budget_cents=None):
        """Store a new policy version. Approvals given under an older version
        stop matching (the version is part of the approved message).
        The Milestone 5 fields are optional: None keeps the current value."""
        for name, value, low, high in (("approval_ttl_minutes", approval_ttl_minutes, 1, 7 * 24 * 60),
                                       ("follow_up_days", follow_up_days, 1, 365),
                                       ("max_reminders", max_reminders, 0, 20)):
            if type(value) is not int or not low <= value <= high:
                raise InvalidInput(f"{name} must be an integer from {low} to {high}")
        if autonomy_rules is not None:
            self._check_rules(autonomy_rules)
        if cadence_days is not None:
            self._check_cadence(cadence_days)
        if daily_model_budget_cents is not None and (
                type(daily_model_budget_cents) is not int
                or not 0 <= daily_model_budget_cents <= 1_000_000):
            raise InvalidInput("daily_model_budget_cents must be an integer from 0 to 1000000")
        with self.transaction():
            latest = self.policy()
            if autonomy_rules is None:
                autonomy_rules = latest["autonomy_rules"] if latest else []
            if cadence_days is None:
                cadence_days = latest["cadence_days"] if latest else []
            if daily_model_budget_cents is None:
                daily_model_budget_cents = latest["daily_model_budget_cents"] if latest else 0
            values = (approval_ttl_minutes, follow_up_days, max_reminders,
                      autonomy_rules, cadence_days, daily_model_budget_cents)
            if latest and tuple(latest[k] for k in (
                    "approval_ttl_minutes", "follow_up_days", "max_reminders", "autonomy_rules",
                    "cadence_days", "daily_model_budget_cents")) == values:
                return latest  # unchanged: keep the version, keep approvals valid
            version = (latest["version"] if latest else 0) + 1
            now = self.clock()
            self.db.execute(
                "INSERT INTO policies(version, approval_ttl_minutes, follow_up_days, max_reminders, "
                "autonomy_rules, cadence_days, daily_model_budget_cents, created_by, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (version, approval_ttl_minutes, follow_up_days, max_reminders,
                 json.dumps(autonomy_rules), json.dumps(cadence_days), daily_model_budget_cents,
                 created_by, now))
            for row in self.db.execute("SELECT invoice_id FROM cases WHERE state != 'resolved'").fetchall():
                self._record(row["invoice_id"], f"policy:v{version}", "policy_changed")
            self._activity("policy_changed", created_by, f"Policy version {version} saved",
                           ref=f"policy:v{version}")
        return self.policy()

    def contact_on_file(self, invoice_id):
        return self._contact(invoice_id) is not None

    def _contact(self, invoice_id):
        row = self.db.execute("SELECT * FROM contacts WHERE invoice_id=?", (invoice_id,)).fetchone()
        return dict(row) if row else None

    def set_contact(self, invoice_id, address, channel=messages.SIMULATED_CHANNEL):
        """Where messages for this invoice go. Only the simulated inbox exists."""
        if channel != messages.SIMULATED_CHANNEL:
            raise InvalidInput("Only the simulated inbox channel is available")
        if not isinstance(address, str) or len(address) > 254 or not _EMAIL.match(address):
            raise InvalidInput("address must look like an email address")
        with self.transaction():
            self.invoice(invoice_id)
            prior = self._contact(invoice_id)
            if prior and (prior["address"], prior["channel"]) == (address, channel):
                return prior
            version = (prior["version"] if prior else 0) + 1
            self.db.execute(
                "INSERT INTO contacts VALUES(?,?,?,?,?) ON CONFLICT(invoice_id) DO UPDATE SET "
                "channel=excluded.channel, address=excluded.address, version=excluded.version, "
                "updated_at=excluded.updated_at",
                (invoice_id, channel, address, version, self.clock()))
            self._record(invoice_id, f"contact:{invoice_id}:v{version}", "contact_changed")
        return self._contact(invoice_id)

    # --- outbox: delivery of queued messages (Milestone 3) -------------
    # worker.py drives these. Each step is its own short transaction; the
    # actual send happens between them, outside any database lock.

    def outbox_item(self, item_id):
        row = self.db.execute("SELECT * FROM outbox WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown outbox item")
        return dict(row)

    def _dispatch_due(self, item, now):
        if item["status"] in ("pending", "unknown"):
            return _at(item["next_attempt_at"]) <= _at(now)
        if item["status"] == "sending":
            return _at(item["claimed_at"]) + timedelta(seconds=SENDING_STALE_SECONDS) <= _at(now)
        return False

    def dispatch_candidates(self):
        """Items to work on now. "unknown" items are always listed, because
        asking the inbox (lookup) is free; resending them waits for backoff."""
        now = self.clock()
        rows = self.db.execute("SELECT * FROM outbox WHERE status IN ('pending','unknown','sending') "
                               "ORDER BY created_at, id").fetchall()
        return [row["id"] for row in rows
                if row["status"] == "unknown" or self._dispatch_due(row, now)]

    def claim_dispatch(self, item_id):
        """Mark an item as being sent, after re-checking it is still right to
        send. Returns the message to send, or None if it must not be sent."""
        with self.transaction():
            item = self.outbox_item(item_id)
            now = self.clock()
            if not self._dispatch_due(item, now):
                return None
            action = self.action(item_id)
            if (item["attempts"] == 0 and (action["approved_by"] or "").startswith("policy:")
                    and self.autonomy_status()["paused"]):
                return None  # kill switch: hold automatic messages; expiry still applies
            if item["attempts"] >= MAX_DISPATCH_ATTEMPTS:
                self._end_dispatch(item, "failed", "too many attempts")
                return None
            try:
                # New evidence, a payment or a changed recipient since approval.
                self.validate_action(action)
                approval = self._latest_approval(item_id)
                if approval is None or approval["payload_hash"] != item["payload_hash"]:
                    raise Conflict("No approval matches this message")
                # First attempt only: the approval must still be valid when the
                # message actually leaves. Retries after an unknown outcome
                # finish a send that already started in time.
                if item["attempts"] == 0 and self._expired(approval, now):
                    raise Conflict("Approval expired before delivery; approve again")
            except Conflict as exc:
                self._end_dispatch(item, "cancelled", str(exc))
                return None
            self.db.execute(
                "UPDATE outbox SET status='sending', attempts=attempts+1, claimed_at=?, updated_at=? "
                "WHERE id=?", (now, now, item_id))
        return {**self.outbox_item(item_id), "payload": json.loads(item["payload"])}

    def mark_sent(self, item_id, external_id):
        """The inbox confirmed it has the message. Schedules the payment check.
        Note: this changes nothing about money; only a settlement does."""
        with self.transaction():
            item = self.outbox_item(item_id)
            if item["status"] == "sent":
                return item
            if item["status"] not in ("pending", "sending", "unknown"):
                raise Conflict(f"Outbox item is {item['status']}")
            now = self.clock()
            self.db.execute("UPDATE outbox SET status='sent', external_id=?, last_error=NULL, "
                            "updated_at=? WHERE id=?", (external_id, now, item_id))
            self.db.execute("UPDATE actions SET status='dispatched' WHERE id=?", (item_id,))
            policy_version = json.loads(item["payload"])["policy_version"]
            policy = self.db.execute("SELECT * FROM policies WHERE version=?", (policy_version,)).fetchone()
            self.db.execute(
                "INSERT OR IGNORE INTO jobs(id,invoice_id,kind,ref,due_at,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (f"check:{item_id}", item["invoice_id"], "settlement_check", item_id,
                 _later(now, days=policy["follow_up_days"]), "pending", now, now))
            self._record(item["invoice_id"], f"outbox:{item_id}:sent", "message_dispatched_simulated")
            self._activity("message_sent", "worker",
                           f"Delivered to the simulated inbox for {item['invoice_id']} (no real email)",
                           case_id=self.case_id(item["invoice_id"]), ref=item_id)
        return self.outbox_item(item_id)

    def mark_unknown(self, item_id, error):
        """No answer from the inbox: it may or may not have the message. The
        next worker round asks the inbox before sending again."""
        with self.transaction():
            item = self.outbox_item(item_id)
            if item["status"] != "sending":
                raise Conflict(f"Outbox item is {item['status']}, not sending")
            now = self.clock()
            # Wait before sending again: 1, 2, 4 ... minutes, at most an hour.
            retry_at = _later(now, minutes=min(2 ** (item["attempts"] - 1), 60))
            self.db.execute("UPDATE outbox SET status='unknown', last_error=?, next_attempt_at=?, "
                            "updated_at=? WHERE id=?", (error[:500], retry_at, now, item_id))
            self._record(item["invoice_id"], f"outbox:{item_id}:unknown:{item['attempts']}",
                         "dispatch_outcome_unknown")
        return self.outbox_item(item_id)

    def mark_failed(self, item_id, error):
        """The inbox refused the message; sending the same request again won't help."""
        with self.transaction():
            item = self.outbox_item(item_id)
            if item["status"] != "sending":
                raise Conflict(f"Outbox item is {item['status']}, not sending")
            self._end_dispatch(item, "failed", error)
        return self.outbox_item(item_id)

    def _end_dispatch(self, item, status, reason):
        """Stop a delivery for good (inside a transaction): cancelled or failed."""
        now = self.clock()
        self.db.execute("UPDATE outbox SET status=?, last_error=?, updated_at=? WHERE id=?",
                        (status, reason[:500], now, item["id"]))
        self.db.execute("UPDATE actions SET status=? WHERE id=?", (status, item["id"]))
        self._record(item["invoice_id"], f"outbox:{item['id']}:{status}", f"dispatch_{status}")
        if status == "cancelled":
            self._activity("message_cancelled", "worker",
                           f"Cancelled a queued message for {item['invoice_id']}: {reason[:120]}",
                           case_id=self.case_id(item["invoice_id"]), ref=item["id"])

    # --- jobs: follow-ups and re-investigation (Milestone 3) -----------

    def job(self, job_id):
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown job")
        return dict(row)

    def _job_due(self, job, now):
        if job["status"] == "pending":
            return _at(job["due_at"]) <= _at(now)
        if job["status"] == "running":  # its worker died; try again
            return _at(job["updated_at"]) + timedelta(seconds=STALE_RUN_SECONDS) <= _at(now)
        return False

    def due_jobs(self):
        now = self.clock()
        rows = self.db.execute("SELECT * FROM jobs WHERE status IN ('pending','running') "
                               "ORDER BY due_at, id").fetchall()
        return [dict(row) for row in rows if self._job_due(row, now)]

    def waiting_jobs(self, kind):
        return self.db.execute("SELECT COUNT(*) FROM jobs WHERE kind=? AND status IN "
                               "('pending','running')", (kind,)).fetchone()[0]

    def run_settlement_check(self, job_id):
        """Follow-up date reached. If the reminder is still current and money is
        still owed, the case goes back to open for the next decision."""
        with self.transaction():
            job = self.job(job_id)
            if job["kind"] != "settlement_check":
                raise InvalidInput("Not a settlement check")
            if not self._job_due(job, self.clock()):
                return {**job, "elapsed": False}
            action = self.action(job["ref"])
            invoice = self.invoice(job["invoice_id"])
            still_waiting = (action["status"] == "dispatched"
                             and action["version"] == invoice["version"]
                             and invoice["outstanding_cents"] > 0)
            if still_waiting:
                self.db.execute("UPDATE actions SET status='follow_up_due' WHERE id=?", (action["id"],))
            self._finish_job(job_id, "done", "no settlement by the follow-up date" if still_waiting
                             else "not needed: the case moved on")
            if still_waiting:
                self._record(job["invoice_id"], f"job:{job_id}", "follow_up_elapsed")
        return {**self.job(job_id), "elapsed": still_waiting}

    def claim_job(self, job_id):
        with self.transaction():
            job = self.job(job_id)
            if not self._job_due(job, self.clock()):
                return None
            if self._derive_state(job["invoice_id"]) == "resolved":
                self._finish_job(job_id, "done", "not needed: case resolved")
                return None
            if job["attempts"] >= MAX_JOB_ATTEMPTS:
                self._finish_job(job_id, "failed", f"gave up after {job['attempts']} attempts")
                return None
            self.db.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? "
                            "WHERE id=?", (self.clock(), job_id))
        return self.job(job_id)

    def finish_job(self, job_id, status, detail, retry_minutes=None, count_attempt=True):
        """count_attempt=False: the job never really started (e.g. the case was
        busy), so this round does not use up one of its MAX_JOB_ATTEMPTS."""
        with self.transaction():
            self._finish_job(job_id, status, detail, retry_minutes)
            if not count_attempt:
                self.db.execute("UPDATE jobs SET attempts=MAX(attempts-1, 0) WHERE id=?", (job_id,))
        return self.job(job_id)

    def _finish_job(self, job_id, status, detail, retry_minutes=None):
        if status not in ("done", "failed", "pending"):
            raise InvalidInput("Unknown job status")
        now = self.clock()
        due = _later(now, minutes=retry_minutes or 0) if status == "pending" else None
        self.db.execute("UPDATE jobs SET status=?, detail=?, updated_at=?, due_at=COALESCE(?, due_at) "
                        "WHERE id=?", (status, (detail or "")[:500], now, due, job_id))

    # --- closure verification (Milestone 3) -----------------------------

    def _verify_closure(self, case_id, invoice_id, now):
        """Prove the money before resolving: settlements must add up to the
        invoice total in CAD. A sent message is never part of this."""
        invoice = self.invoice(invoice_id)
        rows = self.db.execute("SELECT id, amount, currency FROM settlements WHERE invoice_id=? "
                               "ORDER BY id", (invoice_id,)).fetchall()
        settled = sum(r["amount"] for r in rows)
        currencies = {r["currency"] for r in rows}
        if settled != invoice["total_cents"] or currencies != {"CAD"}:
            raise Conflict("Closure check failed: settlements do not equal the invoice total in CAD")
        self.db.execute(
            "INSERT OR IGNORE INTO case_verifications(case_id,invoice_version,total_cents,settled_cents,"
            "currency,settlement_ids,method,verified_at) VALUES(?,?,?,?,?,?,?,?)",
            (case_id, invoice["version"], invoice["total_cents"], settled, "CAD",
             json.dumps([r["id"] for r in rows]), "settlements_sum_to_total", now))

    # --- Milestone 5: events (the shared input format) ------------------

    def schedule_event(self, kind, payload, source, source_ref, at=None, synthetic=True):
        """Store one normalized input event, to be applied by the orchestrator
        once the clock reaches `at` (default: now). `source:source_ref` is the
        dedupe key: storing the same event again is a no-op; reusing the key
        with a different payload is a Conflict. Returns the stored event."""
        spec = EVENT_SPECS.get(kind)
        if spec is None:
            raise InvalidInput(f"kind must be one of {sorted(EVENT_SPECS)}")
        if source not in EVENT_SOURCES:
            raise InvalidInput(f"source must be one of {sorted(EVENT_SOURCES)}")
        if type(source_ref) is not str or not 1 <= len(source_ref) <= 200:
            raise InvalidInput("source_ref must be 1-200 characters")
        if type(payload) is not dict or set(payload) != set(spec):
            raise InvalidInput(f"{kind} payload needs exactly: {sorted(spec)}")
        for field, expected in spec.items():
            value = payload[field]
            # bool is an int in Python; never accept True as an amount.
            if not isinstance(value, expected) or (expected is int and type(value) is bool):
                raise InvalidInput(f"{kind}.{field} has the wrong type")
        at = self.clock() if at is None else self._timestamp(at, "at")
        dedupe_key = f"{source}:{source_ref}"
        body = json.dumps(payload, sort_keys=True)
        with self.transaction():
            prior = self.db.execute("SELECT * FROM events WHERE dedupe_key=?",
                                    (dedupe_key,)).fetchone()
            if prior:
                if prior["kind"] != kind or prior["payload"] != body:
                    raise Conflict("Event key reused with different data")
                return self._event_out(prior)
            event_id = "evt-" + hashlib.sha256(dedupe_key.encode()).hexdigest()[:24]
            self.db.execute(
                "INSERT INTO events(id, kind, source, source_ref, dedupe_key, synthetic, payload, "
                "received_at, routed_at) VALUES(?,?,?,?,?,?,?,?,NULL)",
                (event_id, kind, source, source_ref, dedupe_key, int(bool(synthetic)), body, at))
            row = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._event_out(row)

    @staticmethod
    def _event_out(row):
        return {**dict(row), "payload": json.loads(row["payload"]),
                "synthetic": bool(row["synthetic"])}

    def due_events(self, limit=500):
        """Events not yet applied whose time has come, oldest first."""
        return [self._event_out(r) for r in self.db.execute(
            "SELECT * FROM events WHERE routed_at IS NULL AND received_at <= ? "
            "ORDER BY received_at, rowid LIMIT ?", (self.clock(), limit))]

    # --- Milestone 5: autonomy rules (code decides, never the model) ----

    def _evaluate_rules(self, rules, policy_version, kind, invoice, evidence_count,
                        open_escalations, proposed_by, amount_cents):
        """First matching rule decides; no match means the owner approves."""
        def decision(mode, rule_id, *reasons):
            return {"mode": mode, "rule_id": rule_id, "policy_version": policy_version,
                    "reasons": list(reasons), "decided_at": self.clock()}
        if not proposed_by.startswith(AUTOMATIC_PROPOSERS):
            return decision("approve", None, "proposed by the owner")
        if policy_version == 0:
            return decision("approve", None, "no policy stored")
        if not rules:
            return decision("approve", None, f"policy v{policy_version} has no autonomy rules")
        if open_escalations:
            return decision("approve", None, "case has an open escalation")
        if proposed_by.startswith("agent:") and kind == "collection_reminder":
            # The agent only looks at non-routine cases (a reply, a partial
            # payment, a new document); chasing after one is the owner's call.
            return decision("approve", None, "agent proposed a reminder on a non-routine case: "
                                             "the owner decides")
        if proposed_by.startswith("agent:") and not AGENT_MESSAGES_AUTOMATIC:
            return decision("approve", None, "agent messages wait for the owner until a live "
                                             "evaluation passes (FINANCEOPS_AGENT_AUTOSEND)")
        if kind == "collection_reminder" and evidence_count > 1:
            # Replies or documents beyond the original contract can change the
            # story (already paid, promised a date, disputed): no autopilot.
            return decision("approve", None, "the case has replies or documents beyond the "
                                             "contract: the owner decides")
        for rule in rules:
            if rule["action_kind"] != kind:
                continue
            if rule["max_amount_cents"] is not None and amount_cents > rule["max_amount_cents"]:
                continue
            if rule["require_undisputed"] and invoice["disputed"]:
                continue
            if rule["require_evidence"] and evidence_count == 0:
                continue
            reason = f"rule {rule['id']}: {rule['description']}"
            return decision(rule["mode"], rule["id"], reason)
        if invoice["disputed"]:
            return decision("approve", None, "invoice disputed")
        return decision("approve", None, "no autonomy rule matched")

    def _autonomy_for(self, action):
        """The autonomy decision for a stored action under the current policy."""
        policy = self.policy()
        invoice = self.invoice(action["invoice_id"])
        payload = json.loads(action["payload"]) if action.get("payload") else {}
        return self._evaluate_rules(
            policy["autonomy_rules"] if policy else [], policy["version"] if policy else 0,
            action["kind"], invoice,
            self.db.execute("SELECT COUNT(*) FROM evidence WHERE invoice_id=?",
                            (invoice["id"],)).fetchone()[0],
            self.db.execute("SELECT COUNT(*) FROM escalations WHERE case_id=? AND resolved_at IS NULL",
                            (self.case_id(invoice["id"]),)).fetchone()[0],
            action["proposed_by"], payload.get("amount_cents", invoice["outstanding_cents"]))

    def auto_candidates(self):
        """Proposed actions the autonomy rules may execute, oldest first."""
        return [r["id"] for r in self.db.execute(
            "SELECT id FROM actions WHERE status='proposed' AND autonomy IS NOT NULL "
            "AND json_extract(autonomy, '$.mode') IN ('auto', 'blocked') ORDER BY rowid")]

    def apply_autonomy(self, action_id):
        """Execute what the rules allow, re-checked now (the case or policy may
        have changed since the proposal). One transaction: re-evaluate, check
        the kill switch, approve and queue together, so nothing slips between
        the checks and the send. Every approve()/execute() check still applies."""
        with self.transaction():
            action = self.action(action_id)
            if action["status"] != "proposed":
                return {"action": action_id, "outcome": "skipped", "detail": action["status"]}
            decision = self._autonomy_for(action)
            self.db.execute("UPDATE actions SET autonomy=? WHERE id=?",
                            (json.dumps(decision), action_id))
            actor = f"policy:v{decision['policy_version']}/rule:{decision['rule_id']}"
            if decision["mode"] == "approve":
                return {"action": action_id, "outcome": "owner",
                        "detail": "; ".join(decision["reasons"])}
            if decision["mode"] == "blocked":
                self._open_escalation_locked(
                    self.case_id(action["invoice_id"]), "blocked_by_policy",
                    f"A {action['kind'].replace('_', ' ')} was blocked by policy rule "
                    f"{decision['rule_id']}. Decide the next step.",
                    json.loads(action["evidence_ids"]), "policy", key=action_id)
                self._reject_locked(action_id, actor, "Blocked by policy")
                return {"action": action_id, "outcome": "blocked", "detail": decision["rule_id"]}
            if self.autonomy_status()["paused"]:
                return {"action": action_id, "outcome": "paused", "detail": "autonomy paused by owner"}
            self.db.execute("SAVEPOINT autonomy_send")
            try:
                self.validate_action(action)
                self._approve_locked(action_id, actor, action["payload_hash"])
                self._execute_locked(action_id, actor)
            except Conflict as exc:
                # Undo a half-done approval, then retire the plan so it is not
                # retried every round; the next cadence day or the owner moves on.
                self.db.execute("ROLLBACK TO autonomy_send")
                self._reject_locked(action_id, actor, f"No longer valid: {str(exc)[:200]}")
                return {"action": action_id, "outcome": "invalid", "detail": str(exc)}
            finally:
                self.db.execute("RELEASE autonomy_send")
        return {"action": action_id, "outcome": "queued", "detail": decision["rule_id"]}

    def preview_policy(self, autonomy_rules):
        """How a draft rule list would treat each open case's next step now."""
        self._check_rules(autonomy_rules)
        counts = {"open_cases": 0, "auto": 0, "approve": 0, "blocked": 0}
        version = (self.policy() or {"version": 0})["version"] + 1  # as if saved next
        for row in self.cases():
            if row["state"] == "resolved":
                continue
            invoice = self.invoice(row["invoice_id"])
            evidence_count = self.db.execute("SELECT COUNT(*) FROM evidence WHERE invoice_id=?",
                                             (invoice["id"],)).fetchone()[0]
            kind = ("request_information" if invoice["disputed"] or evidence_count == 0
                    else "collection_reminder")
            mode = self._evaluate_rules(autonomy_rules, version, kind, invoice, evidence_count,
                                        row["open_escalations"], "orchestrator",
                                        invoice["outstanding_cents"])["mode"]
            counts["open_cases"] += 1
            counts[mode] += 1
        return counts

    # --- Milestone 5: escalations and event application -----------------

    def open_escalation(self, case_id, reason, question, evidence_ids, raised_by, key):
        """Ask the owner. `key` makes it idempotent: the same cause escalates
        once. Returns the escalation, or None for a resolved case."""
        with self.transaction():
            return self._open_escalation_locked(case_id, reason, question, evidence_ids,
                                                raised_by, key)

    def _open_escalation_locked(self, case_id, reason, question, evidence_ids, raised_by, key):
        escalation_id = "esc-" + hashlib.sha256(f"{case_id}:{reason}:{key}".encode()).hexdigest()[:16]
        prior = self.db.execute("SELECT * FROM escalations WHERE id=?", (escalation_id,)).fetchone()
        if prior:
            return self._escalation_out(prior)
        invoice_id = self._case_row(case_id)["invoice_id"]
        if self._derive_state(invoice_id) == "resolved":
            return None
        self.db.execute(
            "INSERT INTO escalations(id, case_id, reason, question, evidence_ids, raised_by, "
            "created_at) VALUES(?,?,?,?,?,?,?)",
            (escalation_id, case_id, reason, question[:1000], json.dumps(evidence_ids),
             raised_by, self.clock()))
        self._record(invoice_id, f"escalation:{escalation_id}:opened", f"escalated_{reason}")
        self._activity("escalation_opened", raised_by, f"Needs you: {question[:200]}",
                       case_id=case_id, ref=escalation_id)
        row = self.db.execute("SELECT * FROM escalations WHERE id=?", (escalation_id,)).fetchone()
        return self._escalation_out(row)

    @staticmethod
    def _cad(cents):
        return f"CAD {cents // 100:,}.{cents % 100:02d}"

    def apply_event(self, event_id):
        """Apply one stored event through the normal core method, then mark it
        routed. Each method is idempotent, so a crash between the two steps
        re-applies harmlessly. A refused event is recorded, never retried."""
        row = self.db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown event")
        event = self._event_out(row)
        if event["routed_at"] is not None:
            return {"event": event_id, "kind": event["kind"], "status": "already_applied",
                    "detail": None}
        p = event["payload"]
        handlers = {
            "invoice_created": lambda: self.create_invoice(p["invoice_id"], p["customer"],
                                                           p["total_cents"], due_at=p["due_at"]),
            "contact_set": lambda: self.set_contact(p["invoice_id"], p["address"]),
            "evidence_added": lambda: self.add_evidence(p["evidence_id"], p["invoice_id"], p["body"]),
            "settlement_received": lambda: self.record_settlement(
                p["settlement_id"], p["invoice_id"], p["amount_cents"], p["currency"]),
            "dispute_changed": lambda: self.set_disputed(p["invoice_id"], p["disputed"]),
            "reply_received": lambda: self.record_reply(p["evidence_id"], p["invoice_id"], p["body"]),
        }
        summaries = {
            "invoice_created": lambda: f"Invoice {p['invoice_id']} for {self._cad(p['total_cents'])} "
                                       f"to {p['customer']}",
            "contact_set": lambda: f"Contact on file for {p['invoice_id']}",
            "evidence_added": lambda: f"Evidence {p['evidence_id']} added to {p['invoice_id']}",
            "settlement_received": lambda: f"Payment of {self._cad(p['amount_cents'])} received "
                                           f"for {p['invoice_id']}",
            "dispute_changed": lambda: f"Invoice {p['invoice_id']} "
                                       f"{'disputed' if p['disputed'] else 'no longer disputed'}",
            "reply_received": lambda: f"Customer reply {p['evidence_id']} on {p['invoice_id']} "
                                      "(untrusted text)",
        }
        status, detail = "applied", None
        try:
            handlers[event["kind"]]()
        except (Conflict, InvalidInput, NotFound) as exc:
            status, detail = "rejected", str(exc)
        invoice_id = p.get("invoice_id")
        case_id = self.case_id(invoice_id) if invoice_id else None
        with self.transaction():
            self.db.execute("UPDATE events SET routed_at=? WHERE id=? AND routed_at IS NULL",
                            (self.clock(), event_id))
            known_case = case_id and self.db.execute("SELECT 1 FROM cases WHERE id=?",
                                                     (case_id,)).fetchone()
            self._activity("event_ingested", f"source:{event['source']}",
                           summaries[event["kind"]]() + ("" if status == "applied"
                                                         else f" (not applied: {detail})"),
                           case_id=case_id if known_case else None, ref=event_id,
                           synthetic=event["synthetic"])
            # Escalate in the same transaction that marks the event applied,
            # so a crash cannot consume the event and lose the escalation.
            if known_case:
                evidence = [e["id"] for e in self.evidence(invoice_id)]
                if status == "rejected":
                    self._open_escalation_locked(
                        case_id, "event_rejected",
                        f"An incoming {event['kind'].replace('_', ' ')} could not be applied: "
                        f"{detail[:300]}. Review it.", evidence, "orchestrator", key=event_id)
                elif event["kind"] == "dispute_changed" and p["disputed"]:
                    self._open_escalation_locked(
                        case_id, "dispute",
                        f"The customer disputes invoice {invoice_id}. Automatic reminders stop "
                        "while it is disputed. Review the evidence and decide the next step.",
                        evidence, "orchestrator", key=event_id)
        return {"event": event_id, "kind": event["kind"], "status": status, "detail": detail,
                "case_id": case_id if known_case else None}

    # --- Milestone 5 step 2: model spend, replans, briefings ------------

    @staticmethod
    def model_cost_micro_cents(model, input_tokens, output_tokens, cache_read=0, cache_write=0):
        """Cost in millionths of a US cent (integer, no rounding drift)."""
        price = MODEL_PRICES.get(model) or max(MODEL_PRICES.values(), key=lambda p: p["output"])
        return int(input_tokens * price["input"] + output_tokens * price["output"]
                   + cache_read * price["cache_read"]
                   + cache_write * price["input"] * CACHE_WRITE_MULTIPLIER)

    def _record_model_usage(self, agent, model, ref, input_tokens, output_tokens,
                            cache_read=0, cache_write=0):
        """Inside the caller's transaction: one model call's tokens and cost."""
        tokens = [int(x or 0) for x in (input_tokens, output_tokens, cache_read, cache_write)]
        self.db.execute(
            "INSERT INTO model_usage(agent, model, ref, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cost_micro_cents, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (agent, model, ref, *tokens, self.model_cost_micro_cents(model, *tokens), self.clock()))

    def record_model_usage(self, agent, model, ref, input_tokens, output_tokens,
                           cache_read=0, cache_write=0):
        with self.transaction():
            self._record_model_usage(agent, model, ref, input_tokens, output_tokens,
                                     cache_read, cache_write)

    def spend_today_cents(self):
        """Model spend since midnight UTC on the (simulated) clock, rounded up."""
        today = _at(self.clock()).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        micro = self.db.execute("SELECT COALESCE(SUM(cost_micro_cents), 0) FROM model_usage "
                                "WHERE created_at >= ?", (today,)).fetchone()[0]
        return -(-micro // 1_000_000)

    def budget_enforced(self):
        """The daily model budget applies when the policy has autonomy rules.
        Policies without rules keep the Milestone 3 behaviour (an explicit
        model client decides)."""
        policy = self.policy()
        return bool(policy and policy["autonomy_rules"])

    def model_budget_left_cents(self):
        """What the policy still allows today for automatic model calls (0 = none)."""
        policy = self.policy()
        budget = policy["daily_model_budget_cents"] if policy else 0
        return max(budget - self.spend_today_cents(), 0)

    def replans(self, invoice_id):
        """Automatic plans on this case that a newer plan replaced."""
        version = self.invoice(invoice_id)["version"]  # new evidence or payment resets it
        return self.db.execute("SELECT COUNT(*) FROM actions WHERE invoice_id=? AND version=? "
                               "AND status='superseded' AND proposed_by LIKE 'agent:%'",
                               (invoice_id, version)).fetchone()[0]

    def briefing_due(self):
        """True when no briefing exists for today's (simulated) date and fewer
        than 2 attempts were made today (a failing call is not retried all day)."""
        now = _at(self.clock())
        today = now.date().isoformat()
        latest = self.db.execute("SELECT MAX(period_end) FROM briefings").fetchone()[0]
        if latest is not None and _at(latest).date().isoformat() >= today:
            return False
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        attempts = self.db.execute("SELECT COUNT(*) FROM model_usage WHERE agent='briefing' "
                                   "AND created_at >= ?", (midnight,)).fetchone()[0]
        return attempts < 2

    def briefing_facts(self):
        """The facts the briefing agent may describe, computed by code."""
        now = self.clock()
        since = _later(now, days=-1)
        summary = self.summary()
        attention = self.attention()
        counts = {r["kind"]: r["n"] for r in self.db.execute(
            "SELECT kind, COUNT(*) AS n FROM activity WHERE created_at >= ? GROUP BY kind",
            (since,))}
        overdue = sorted((r for r in self.cases() if r["days_overdue"] is not None
                          and r["state"] != "resolved"),
                         key=lambda r: -r["days_overdue"])[:5]
        return {
            "period_start": since, "period_end": now, "currency": "CAD",
            "summary": {k: summary[k] for k in ("outstanding_cents", "overdue_cents", "open_cases",
                                                "overdue_cases", "awaiting_owner",
                                                "auto_handled_7d", "resolved_7d")},
            "last_24h": counts,
            "needs_owner": [
                {"case_id": a["case_id"], "kind": a["action"]["kind"],
                 "outstanding_cents": a["outstanding_cents"]} for a in attention["approvals"]
            ] + [
                {"case_id": e["escalation"]["case_id"], "reason": e["escalation"]["reason"],
                 "outstanding_cents": e["outstanding_cents"]} for e in attention["escalations"]],
            "most_overdue": [{"case_id": r["id"], "days_overdue": r["days_overdue"],
                              "outstanding_cents": r["outstanding_cents"]} for r in overdue],
        }

    def store_briefing(self, period_start, period_end, headline, sections, run_ref=None):
        briefing_id = f"brief-{uuid.uuid4().hex[:16]}"
        with self.transaction():
            self.db.execute(
                "INSERT INTO briefings(id, run_id, period_start, period_end, headline, sections, "
                "created_at) VALUES(?,?,?,?,?,?,?)",
                (briefing_id, run_ref, period_start, period_end, headline[:300],
                 json.dumps(sections), self.clock()))
            self._activity("briefing_published", "agent:briefing",
                           "Owner briefing published for the last 24 hours", ref=briefing_id)
        return self.latest_briefing()

    # --- Milestone 5: activity, autonomy switch, escalations, agents -----

    def _activity(self, kind, actor, summary, case_id=None, ref=None, synthetic=True):
        """Append one item to the owner's activity feed. Call inside the
        caller's transaction. The summary is written by code, never the model."""
        if kind not in ACTIVITY_KINDS:
            raise ValueError(f"unknown activity kind {kind}")  # a bug, not bad input
        self.db.execute(
            "INSERT INTO activity(kind, actor, case_id, ref, summary, synthetic, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (kind, actor, case_id, ref, summary[:300], int(bool(synthetic)), self.clock()))

    def activity(self, after=0, limit=100):
        if type(after) is not int or after < 0:
            raise InvalidInput("after must be a non-negative integer")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise InvalidInput("limit must be an integer from 1 to 500")
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM activity WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit))]
        for row in rows:
            row["synthetic"] = bool(row["synthetic"])
        return {"items": rows, "cursor": rows[-1]["seq"] if rows else after}

    def autonomy_status(self):
        row = self.db.execute("SELECT * FROM autonomy_state").fetchone()
        return {"paused": bool(row["paused"]), "changed_by": row["changed_by"],
                "changed_at": row["changed_at"], "reason": row["reason"]}

    def set_autonomy_paused(self, paused, changed_by="owner", reason=None):
        """The owner's kill switch. While paused, nothing executes automatically;
        proposals still wait for approval as usual."""
        if reason is not None and (type(reason) is not str or len(reason) > 500):
            raise InvalidInput("reason must be at most 500 characters")
        with self.transaction():
            if self.autonomy_status()["paused"] == bool(paused):
                return self.autonomy_status()  # already so: no new activity
            self.db.execute(
                "UPDATE autonomy_state SET paused=?, changed_by=?, changed_at=?, reason=?",
                (int(bool(paused)), changed_by, self.clock(), reason))
            self._activity("autonomy_paused" if paused else "autonomy_resumed", changed_by,
                           "Automatic actions paused" if paused else "Automatic actions resumed")
        return self.autonomy_status()

    @staticmethod
    def _escalation_out(row):
        return {**dict(row), "evidence_ids": json.loads(row["evidence_ids"])}

    def escalations(self, case_id=None, open_only=False):
        sql, args = "SELECT * FROM escalations WHERE 1=1", []
        if case_id is not None:
            sql, args = sql + " AND case_id=?", [*args, case_id]
        if open_only:
            sql += " AND resolved_at IS NULL"
        return [self._escalation_out(r) for r in self.db.execute(
            sql + " ORDER BY created_at DESC, id", args)]

    def resolve_escalation(self, escalation_id, resolved_by="owner", note=None):
        if note is not None and (type(note) is not str or len(note) > 500):
            raise InvalidInput("note must be at most 500 characters")
        with self.transaction():
            row = self.db.execute("SELECT * FROM escalations WHERE id=?",
                                  (escalation_id,)).fetchone()
            if row is None:
                raise NotFound("Unknown escalation")
            if row["resolved_at"] is not None:
                raise Conflict("Escalation is already resolved")
            self.db.execute(
                "UPDATE escalations SET resolved_at=?, resolved_by=?, resolution_note=? WHERE id=?",
                (self.clock(), resolved_by, note, escalation_id))
            self._record(self._case_row(row["case_id"])["invoice_id"],
                         f"escalation:{escalation_id}:resolved", "escalation_resolved")
            self._activity("escalation_resolved", resolved_by, "Escalation resolved by the owner",
                           case_id=row["case_id"], ref=escalation_id)
            row = self.db.execute("SELECT * FROM escalations WHERE id=?",
                                  (escalation_id,)).fetchone()
        return self._escalation_out(row)

    def _owner_approvals(self):
        """Actions waiting for the owner (approve or execute), oldest first.
        Same test as _derive_state: only valid plans on the current version."""
        result = []
        for row in self.db.execute(
                f"SELECT * FROM actions WHERE status IN ({','.join('?' * len(_PENDING))}) "
                "ORDER BY rowid", _PENDING).fetchall():
            invoice = self.invoice(row["invoice_id"])
            if row["version"] != invoice["version"]:
                continue
            try:
                self.validate_action(dict(row))
            except Conflict:
                continue
            result.append((invoice, row["id"]))
        return result

    def attention(self):
        """The owner's "Needs you" queue: approvals and open escalations."""
        self.db.execute("BEGIN")
        try:
            approvals = [{"case_id": self.case_id(inv["id"]), "customer": inv["customer"],
                          "outstanding_cents": inv["outstanding_cents"], "currency": inv["currency"],
                          "action": {**self.action_detail(action_id), "simulated": True}}
                         for inv, action_id in self._owner_approvals()]
            escalations = []
            for esc in reversed(self.escalations(open_only=True)):  # oldest first
                inv = self.invoice(self._case_row(esc["case_id"])["invoice_id"])
                escalations.append({"customer": inv["customer"],
                                    "outstanding_cents": inv["outstanding_cents"],
                                    "currency": inv["currency"], "escalation": esc})
            return {"approvals": approvals, "escalations": escalations,
                    "autonomy": self.autonomy_status()}
        finally:
            self.db.rollback()

    def summary(self):
        """Headline numbers for the owner's home screen."""
        self.db.execute("BEGIN")
        try:
            now = self.clock()
            week_ago = _later(now, days=-7)
            open_rows = [r for r in self.cases() if r["state"] != "resolved"]
            overdue = [r for r in open_rows if r["days_overdue"] is not None]
            open_escalations = self.db.execute(
                "SELECT COUNT(*) FROM escalations WHERE resolved_at IS NULL").fetchone()[0]
            return {
                "currency": "CAD",
                "outstanding_cents": sum(r["outstanding_cents"] for r in open_rows),
                "overdue_cents": sum(r["outstanding_cents"] for r in overdue),
                "open_cases": len(open_rows),
                "overdue_cases": len(overdue),
                "awaiting_owner": len(self._owner_approvals()) + open_escalations,
                "auto_handled_7d": self.db.execute(
                    "SELECT COUNT(*) FROM approvals WHERE decision='approved' "
                    "AND decided_by LIKE 'policy:%' AND decided_at >= ?", (week_ago,)).fetchone()[0],
                "resolved_7d": self.db.execute(
                    "SELECT COUNT(*) FROM case_verifications WHERE verified_at >= ?",
                    (week_ago,)).fetchone()[0],
                "model_spend_today_cents": self.spend_today_cents(),
                "autonomy_paused": self.autonomy_status()["paused"],
                "now": now,
                "simulated_time": self.simulated_time,
            }
        finally:
            self.db.rollback()

    def latest_briefing(self):
        row = self.db.execute(
            "SELECT * FROM briefings ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        if row is None:
            raise NotFound("No briefing yet")
        return {**dict(row), "sections": json.loads(row["sections"])}

    @staticmethod
    def agent_model(name):
        spec = AGENTS[name]
        return os.environ.get(spec["model_env"], spec["default_model"])

    def _agent_info(self, name):
        spec = AGENTS[name]
        now = self.clock()
        today = _at(now).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        control = self.db.execute("SELECT paused FROM agent_controls WHERE name=?",
                                  (name,)).fetchone()
        outcomes = {r["status"]: r["n"] for r in self.db.execute(
            "SELECT status, COUNT(*) AS n FROM agent_runs WHERE agent=? AND started_at >= ? "
            "GROUP BY status", (name, _later(now, days=-7)))}
        return {
            "name": name, "title": spec["title"], "model": self.agent_model(name),
            "can_write": spec["can_write"], "paused": bool(control and control["paused"]),
            "runs_today": self.db.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE agent=? AND started_at >= ?",
                (name, today)).fetchone()[0],
            "outcomes_7d": outcomes,
            "last_run_at": self.db.execute(
                "SELECT MAX(started_at) FROM agent_runs WHERE agent=?", (name,)).fetchone()[0],
        }

    def agents(self):
        return [self._agent_info(name) for name in AGENTS]

    def agent_paused(self, name):
        row = self.db.execute("SELECT paused FROM agent_controls WHERE name=?", (name,)).fetchone()
        return bool(row and row["paused"])

    def set_agent_paused(self, name, paused, changed_by="owner"):
        if name not in AGENTS:
            raise NotFound("Unknown agent")
        with self.transaction():
            if self._agent_info(name)["paused"] != bool(paused):
                self.db.execute(
                    "INSERT INTO agent_controls(name, paused, changed_by, changed_at) VALUES(?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET paused=excluded.paused, "
                    "changed_by=excluded.changed_by, changed_at=excluded.changed_at",
                    (name, int(bool(paused)), changed_by, self.clock()))
                self._activity("agent_paused" if paused else "agent_resumed", changed_by,
                               f"{AGENTS[name]['title']} {'paused' if paused else 'resumed'}",
                               ref=f"agent:{name}")
        return self._agent_info(name)

    def ensure_job(self, job_id, invoice_id, kind, ref, due_at, status="pending", detail=None):
        """Create a job once (IDs come from their cause). True if it is new."""
        now = self.clock()
        with self.transaction():
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO jobs(id,invoice_id,kind,ref,due_at,status,detail,created_at,"
                "updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (job_id, invoice_id, kind, ref, due_at, status, detail, now, now))
            return cursor.rowcount == 1

    def actions_in_flight(self, invoice_id):
        """Actions not yet delivered: proposed, approved or queued. A delivered
        reminder that waits for payment does not block the next cadence step."""
        return self.db.execute(
            "SELECT COUNT(*) FROM actions WHERE invoice_id=? AND status IN "
            "('proposed', 'approved', 'queued')", (invoice_id,)).fetchone()[0]

    def has_unreviewed_evidence(self, invoice_id):
        """True if a document or reply arrived after the first one (the
        original contract) and nobody has looked since: no finished agent run,
        resolved escalation, or approve/reject decision on the case after it.
        New evidence can change the story (e.g. "paid under another reference"),
        so the cadence must not keep chasing on autopilot."""
        case_id = self.case_id(invoice_id)
        evidence = [r["seq"] for r in self.db.execute(
            "SELECT seq FROM case_events WHERE case_id=? AND reason IN "
            "('evidence_added', 'customer_reply_received') ORDER BY seq", (case_id,))]
        if len(evidence) < 2:
            return False
        reviewed = self.db.execute(
            "SELECT MAX(seq) FROM case_events WHERE case_id=? AND (reason IN "
            "('investigation_proposed', 'investigation_no_proposal', 'investigation_escalated', "
            "'escalation_resolved', 'action_approved', 'action_rejected'))",
            (case_id,)).fetchone()[0] or 0
        return evidence[-1] > reviewed

    def job_exists(self, job_id):
        return self.db.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is not None

    def investigate_jobs(self, invoice_id=None, status=None):
        sql, args = "SELECT * FROM jobs WHERE kind='investigate'", []
        if invoice_id is not None:
            sql, args = sql + " AND invoice_id=?", [*args, invoice_id]
        if status is not None:
            sql, args = sql + " AND status=?", [*args, status]
        return [dict(r) for r in self.db.execute(sql + " ORDER BY due_at, id", args)]

    # --- demo clock playback (Milestone 5) -------------------------------

    def tick_playback(self, real_seconds):
        """Demo only: while playing, move the simulated clock by speed x elapsed
        real time. Returns the simulated seconds added (0 when paused)."""
        if not self.simulated_time or real_seconds <= 0:
            return 0
        playback = self.db.execute("SELECT playing, speed FROM sim_playback").fetchone()
        if not playback["playing"]:
            return 0
        seconds = int(playback["speed"] * 60 * real_seconds)
        if seconds:
            self.advance_simulated_time(seconds)
        return seconds

    def clock_status(self):
        if not self.simulated_time:
            raise Conflict("Simulated time is off; it is only available in the demo")
        playback = self.db.execute("SELECT * FROM sim_playback").fetchone()
        return {"now": self.clock(), "simulated": True, "playing": bool(playback["playing"]),
                "speed": playback["speed"],
                "offset_seconds": self.db.execute(
                    "SELECT offset_seconds FROM sim_clock").fetchone()[0]}

    def set_playback(self, action, speed=None):
        """play / pause / speed. The worker loop moves the clock while playing."""
        if not self.simulated_time:
            raise Conflict("Simulated time is off; it is only available in the demo")
        if action not in ("play", "pause", "speed"):
            raise InvalidInput("action must be play, pause or speed")
        if action == "speed" and (type(speed) is not int or not 1 <= speed <= MAX_PLAYBACK_SPEED):
            raise InvalidInput(f"speed must be an integer from 1 to {MAX_PLAYBACK_SPEED}")
        with self.transaction():
            if action == "speed":
                self.db.execute("UPDATE sim_playback SET speed=?, updated_at=?", (speed, self.clock()))
            else:
                self.db.execute("UPDATE sim_playback SET playing=?, updated_at=?",
                                (int(action == "play"), self.clock()))
        return self.clock_status()

    def _derive_state(self, invoice_id):
        """Work out where the case stands from stored facts only.

        Only actions planned against the invoice's *current* version count:
        any new evidence, payment, or dispute change makes older plans stale.
        "awaiting_approval" covers both proposed and approved-not-yet-executed
        actions: the owner's step is not finished until execution. A queued or
        delivered message means we wait; once its follow-up date passes with
        no payment ("follow_up_due"), the case needs a new decision.
        """
        current = self.invoice(invoice_id)
        actions = [dict(r) for r in self.db.execute(
            "SELECT * FROM actions WHERE invoice_id=? AND version=?",
            (invoice_id, current["version"]))]
        for action in actions:
            if action["status"] in _PENDING:
                try:
                    self.validate_action(action)
                except Conflict:
                    continue  # cannot be approved, so it is not really pending
                return "awaiting_approval"
        if any(a["status"] in _WAITING for a in actions):
            return "awaiting_external_outcome"
        if current["outstanding_cents"] == 0 and not current["disputed"]:
            return "resolved"  # paid in full: _record closes any open escalation
        if self.db.execute("SELECT 1 FROM escalations WHERE case_id=? AND resolved_at IS NULL",
                           (self.case_id(invoice_id),)).fetchone():
            return "escalated"
        if current["disputed"]:
            return "needs_information"
        if current["outstanding_cents"] == 0:
            return "resolved"
        return "open"

    def _record(self, invoice_id, event_id, reason):
        """Append one case event and move the case to its derived state.

        Must run inside the caller's transaction, so the change and its log
        entry are saved together or not at all. Replayed event IDs are ignored.
        """
        now = self.clock()
        case_id = self.case_id(invoice_id)
        next_state = self._derive_state(invoice_id)
        case = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if case is None:
            prior_state = None
            self.db.execute(
                "INSERT INTO cases(id,invoice_id,kind,state,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (case_id, invoice_id, "receivable", next_state, now, now))
        else:
            prior_state = case["state"]
            if next_state != prior_state and next_state not in CASE_TRANSITIONS[prior_state]:
                raise Conflict(f"Case cannot move from {prior_state} to {next_state}")
            if next_state == "resolved" and prior_state != "resolved":
                self._verify_closure(case_id, invoice_id, now)
                self.db.execute(
                    "UPDATE escalations SET resolved_at=?, resolved_by='system', "
                    "resolution_note='closed: the case was paid in full' "
                    "WHERE case_id=? AND resolved_at IS NULL", (now, case_id))
                # Paid in full: waiting re-investigations have nothing left to do.
                self.db.execute(
                    "UPDATE jobs SET status='done', detail='not needed: case resolved', updated_at=? "
                    "WHERE invoice_id=? AND kind='investigate' AND status='pending'",
                    (now, invoice_id))
        if self.db.execute("SELECT 1 FROM case_events WHERE case_id=? AND event_id=?",
                           (case_id, event_id)).fetchone():
            return  # this event was already recorded; a replay changes nothing
        self.db.execute(
            "INSERT INTO case_events"
            "(case_id,event_id,prior_state,next_state,reason,invoice_version,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (case_id, event_id, prior_state, next_state, reason,
             self.invoice(invoice_id)["version"], now))
        if prior_state is not None and prior_state != next_state:
            self._activity("case_state_changed", "system",
                           f"Case moved from {prior_state} to {next_state} ({reason})",
                           case_id=case_id, ref=event_id)
        self.db.execute("UPDATE cases SET state=?, version=version+1, updated_at=? WHERE id=?",
                        (next_state, now, case_id))
