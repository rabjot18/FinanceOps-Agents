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
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import messages

SCHEMA_VERSION = 4

ACTION_KINDS = {"collection_reminder", "request_information"}
# Action status after the owner's step, by stage. "executed_simulated" is the
# original in-process shortcut (execute_simulated), kept for the starter tests.
_PENDING = ("proposed", "approved")
_WAITING = ("queued", "dispatched", "executed_simulated")
_PAST_APPROVAL = ("queued", "dispatched", "follow_up_due", "executed_simulated")
_CLOSED = ("rejected", "cancelled", "failed")
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
    "open": {"awaiting_approval", "needs_information", "resolved"},
    "needs_information": {"open", "awaiting_approval", "resolved"},
    "awaiting_approval": {"open", "needs_information", "awaiting_external_outcome", "resolved"},
    "awaiting_external_outcome": {"open", "needs_information", "awaiting_approval", "resolved"},
    # Reopening on reversal/returned payment is a later milestone.
    "resolved": {"needs_information"},
}

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
        result.update(settled_cents=settled, outstanding_cents=row["total"] - settled)
        result["state"] = (
            "needs_information" if row["disputed"] else
            "resolved" if settled == row["total"] else "open"
        )
        return result

    def create_invoice(self, invoice_id, customer, total_cents):
        self.money(total_cents)
        with self.transaction():
            prior = self.db.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
            if prior:
                if prior["customer"] != customer or prior["total"] != total_cents:
                    raise Conflict("Invoice ID reused with different data")
            else:
                self.db.execute("INSERT INTO invoices(id,customer,total) VALUES(?,?,?)",
                                (invoice_id, customer, total_cents))
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
                self._record(invoice_id, f"action:{action_id}:proposed", "action_proposed")
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
        return self.action(action_id)

    def reject(self, action_id, rejecter="owner", note=None):
        with self.transaction():
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
        return self.action(action_id)

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
        return self.action(action_id)

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
        return [{**dict(r), "currency": "CAD"} for r in rows]

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

    def start_agent_run(self, case_id, model):
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
                "INSERT INTO agent_runs(id,case_id,model,status,started_at) VALUES(?,?,?,?,?)",
                (run_id, case_id, model, "running", self.clock()))
            self._record(invoice_id, f"agent:{run_id}:started", "investigation_started")
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
            self._running_run(run_id)
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
        return dict(row) if row else None

    def set_policy(self, approval_ttl_minutes, follow_up_days, max_reminders, created_by="owner"):
        """Store a new policy version. Approvals given under an older version
        stop matching (the version is part of the approved message)."""
        for name, value, low, high in (("approval_ttl_minutes", approval_ttl_minutes, 1, 7 * 24 * 60),
                                       ("follow_up_days", follow_up_days, 1, 365),
                                       ("max_reminders", max_reminders, 0, 20)):
            if type(value) is not int or not low <= value <= high:
                raise InvalidInput(f"{name} must be an integer from {low} to {high}")
        with self.transaction():
            latest = self.policy()
            values = (approval_ttl_minutes, follow_up_days, max_reminders)
            if latest and (latest["approval_ttl_minutes"], latest["follow_up_days"],
                           latest["max_reminders"]) == values:
                return latest  # unchanged: keep the version, keep approvals valid
            version = (latest["version"] if latest else 0) + 1
            self.db.execute("INSERT INTO policies VALUES(?,?,?,?,?,?)",
                            (version, *values, created_by, self.clock()))
            for row in self.db.execute("SELECT invoice_id FROM cases WHERE state != 'resolved'").fetchall():
                self._record(row["invoice_id"], f"policy:v{version}", "policy_changed")
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
        if settled != invoice["total"] or currencies != {"CAD"}:
            raise Conflict("Closure check failed: settlements do not equal the invoice total in CAD")
        self.db.execute(
            "INSERT OR IGNORE INTO case_verifications(case_id,invoice_version,total_cents,settled_cents,"
            "currency,settlement_ids,method,verified_at) VALUES(?,?,?,?,?,?,?,?)",
            (case_id, invoice["version"], invoice["total"], settled, "CAD",
             json.dumps([r["id"] for r in rows]), "settlements_sum_to_total", now))

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
        self.db.execute("UPDATE cases SET state=?, version=version+1, updated_at=? WHERE id=?",
                        (next_state, now, case_id))
