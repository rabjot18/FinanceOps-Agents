"""FinanceOps receivables core. Local synthetic data only; no external payments.

Milestone 1 adds persistent cases and an append-only case event log on top of
the starter's invoices, settlements, evidence, and actions. This module has no
web or AI dependencies: the HTTP layer (api.py) and the future agent both call it.

All amounts are CAD cents. Provider authenticity must be checked by a future
adapter before calling record_settlement().
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

SCHEMA_VERSION = 3

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


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class FinanceCore:
    def __init__(self, path="financeops.sqlite3", clock=_utc_now):
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
                for row in self.db.execute("SELECT id FROM invoices ORDER BY id").fetchall():
                    self._record(row["id"], "migration:v2", "migrated")
            if current < 3:
                for statement in _SCHEMA_V3:
                    self.db.execute(statement)
            self.db.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
            self.db.execute("DELETE FROM schema_meta")
            self.db.execute("INSERT INTO schema_meta VALUES (?)", (SCHEMA_VERSION,))

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except Exception:
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
            self.invoice(invoice_id)
            prior = self.db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if prior:
                if prior["invoice_id"] != invoice_id or prior["body"] != body:
                    raise Conflict("Evidence is immutable; use a new ID")
            else:
                self.db.execute("INSERT INTO evidence VALUES(?,?,?)", (evidence_id, invoice_id, body))
                self.db.execute("UPDATE invoices SET version=version+1 WHERE id=?", (invoice_id,))
                self._record(invoice_id, f"evidence:{evidence_id}", "evidence_added")

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
                proposed_by="api", expected_version=None):
        """The runtime agent may propose these actions, never approve itself.

        expected_version: the invoice version the proposer last read. If the
        records changed since then, the proposal is refused so the proposer
        must look at the new evidence first.
        """
        if kind not in {"collection_reminder", "request_information"}:
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
                self.db.execute(
                    "INSERT INTO actions(id,invoice_id,version,kind,evidence_ids,status,proposed_by) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (action_id, invoice_id, current["version"], kind, refs, "proposed", proposed_by))
                self._record(invoice_id, f"action:{action_id}:proposed", "action_proposed")
        return self.action(action_id)

    def action(self, action_id):
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise NotFound("Unknown action")
        return dict(row)

    def validate_action(self, action):
        current = self.invoice(action["invoice_id"])
        if action["version"] != current["version"]:
            raise Conflict("Stale plan: investigate and propose again")
        if action["kind"] == "collection_reminder" and (
            current["disputed"] or current["outstanding_cents"] == 0
        ):
            raise Conflict("Cannot chase a disputed or fully settled invoice")

    def approve(self, action_id, approver="owner"):
        """Records who approved. The caller (api.py) must authenticate the approver."""
        with self.transaction():
            action = self.action(action_id)
            if action["status"] == "executed_simulated":
                return action
            self.validate_action(action)
            if action["status"] != "approved":
                self.db.execute(
                    "UPDATE actions SET status='approved', approved_by=?, approved_at=? WHERE id=?",
                    (approver, self.clock(), action_id))
                self._record(action["invoice_id"], f"action:{action_id}:approved", "action_approved")
        return self.action(action_id)

    def execute_simulated(self, action_id, executor="owner"):
        """Records simulated dispatch; no email or money moves."""
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
            row = self.db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if row is None:
                raise NotFound("Unknown case")
            result = dict(row)
            result["events"] = [dict(r) for r in self.db.execute(
                "SELECT event_id, prior_state, next_state, reason, invoice_version, created_at "
                "FROM case_events WHERE case_id=? ORDER BY seq", (case_id,))]
        finally:
            self.db.rollback()  # read-only: nothing to commit
        return result

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

    def _derive_state(self, invoice_id):
        """Work out where the case stands from stored facts only.

        Only actions planned against the invoice's *current* version count:
        any new evidence, payment, or dispute change makes older plans stale.
        "awaiting_approval" covers both proposed and approved-not-yet-executed
        actions: the owner's step is not finished until execution.
        """
        current = self.invoice(invoice_id)
        actions = [dict(r) for r in self.db.execute(
            "SELECT * FROM actions WHERE invoice_id=? AND version=?",
            (invoice_id, current["version"]))]
        for action in actions:
            if action["status"] in ("proposed", "approved"):
                try:
                    self.validate_action(action)
                except Conflict:
                    continue  # cannot be approved, so it is not really pending
                return "awaiting_approval"
        if any(a["status"] == "executed_simulated" for a in actions):
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
