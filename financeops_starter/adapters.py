"""Outgoing message adapters (Milestone 3). Only a SIMULATED inbox exists:
nothing leaves this machine and no real email is sent.

The simulated inbox plays the part of an outside service. It keeps its own
table and its own database connection, so (like a real provider) it can accept
a message even if our side crashes before recording that it did. It honours an
idempotency key: sending the same key twice stores one message.

For tests, `faults` makes the next send() calls misbehave, one fault per call:
  "timeout_before_accept"  no answer, and the message was NOT stored
  "timeout_after_accept"   no answer, but the message WAS stored
  "reject"                 the inbox refuses the message
"""
import sqlite3
import uuid
from collections import deque
from contextlib import closing
from datetime import datetime, timezone

FAULTS = {"timeout_before_accept", "timeout_after_accept", "reject"}


class DispatchTimeout(Exception):
    """No answer: the message may or may not have arrived. Check before resending."""


class DispatchRejected(Exception):
    """The receiver refused the message; sending the same request again won't help."""


class SimulatedInbox:
    name = "simulated_inbox"

    def __init__(self, path, faults=()):
        unknown = set(faults) - FAULTS
        if unknown:
            raise ValueError(f"Unknown faults: {sorted(unknown)}")
        self.path = path
        self.faults = deque(faults)
        with self._db() as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS simulated_inbox (
                external_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
                received_at TEXT NOT NULL)""")

    def _db(self):
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return closing(db)

    def send(self, key, recipient, subject, body):
        """Store the message once per key. Returns the inbox's message ID."""
        fault = self.faults.popleft() if self.faults else None
        if fault == "timeout_before_accept":
            raise DispatchTimeout("simulated timeout before the inbox accepted the message")
        if fault == "reject":
            raise DispatchRejected("simulated inbox refused the message")
        try:
            with self._db() as db, db:
                db.execute(
                    "INSERT INTO simulated_inbox"
                    "(external_id,idempotency_key,recipient,subject,body,received_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING",
                    (f"sim-{uuid.uuid4().hex}", key, recipient, subject, body,
                     datetime.now(timezone.utc).isoformat()))
                external_id = db.execute("SELECT external_id FROM simulated_inbox "
                                         "WHERE idempotency_key=?", (key,)).fetchone()[0]
        except sqlite3.OperationalError as exc:  # e.g. busy: it may or may not have stored it
            raise DispatchTimeout(f"inbox unavailable: {exc}") from exc
        if fault == "timeout_after_accept":
            raise DispatchTimeout("simulated timeout after the inbox accepted the message")
        return external_id

    def lookup(self, key):
        """Did a message with this key arrive? Returns its ID or None."""
        with self._db() as db:
            row = db.execute("SELECT external_id FROM simulated_inbox WHERE idempotency_key=?",
                             (key,)).fetchone()
        return row["external_id"] if row else None

    def messages(self):
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM simulated_inbox ORDER BY received_at, external_id")]
