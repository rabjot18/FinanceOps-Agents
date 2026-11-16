"""Phase 1 reference core. Local synthetic data only; no external payments.

This is a persistent receivables example, not the completed FinanceOps app.
All amounts are CAD cents. Provider authenticity must be checked by a future
adapter before calling record_settlement().
"""
import json
import sqlite3
from contextlib import contextmanager


class FinanceCore:
    def __init__(self, path="financeops.sqlite3"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY, customer TEXT NOT NULL,
            total INTEGER NOT NULL CHECK(total > 0),
            version INTEGER NOT NULL DEFAULT 1,
            disputed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settlements (
            id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
            amount INTEGER NOT NULL CHECK(amount > 0), currency TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence (
            id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, body TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
            version INTEGER NOT NULL, kind TEXT NOT NULL,
            evidence_ids TEXT NOT NULL, status TEXT NOT NULL
        );
        """)

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

    @staticmethod
    def money(value):
        if type(value) is not int or value <= 0:
            raise ValueError("Use positive integer cents")

    def invoice(self, invoice_id):
        row = self.db.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown invoice")
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
                    raise ValueError("Invoice ID reused with different data")
            else:
                self.db.execute("INSERT INTO invoices(id,customer,total) VALUES(?,?,?)",
                                (invoice_id, customer, total_cents))
        return self.invoice(invoice_id)

    def add_evidence(self, evidence_id, invoice_id, body):
        with self.transaction():
            self.invoice(invoice_id)
            prior = self.db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if prior:
                if prior["invoice_id"] != invoice_id or prior["body"] != body:
                    raise ValueError("Evidence is immutable; use a new ID")
            else:
                self.db.execute("INSERT INTO evidence VALUES(?,?,?)", (evidence_id, invoice_id, body))
                self.db.execute("UPDATE invoices SET version=version+1 WHERE id=?", (invoice_id,))

    def evidence(self, invoice_id):
        self.invoice(invoice_id)
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM evidence WHERE invoice_id=? ORDER BY id", (invoice_id,))]

    def set_disputed(self, invoice_id, disputed):
        if type(disputed) is not bool:
            raise ValueError("disputed must be boolean")
        with self.transaction():
            self.invoice(invoice_id)
            self.db.execute("UPDATE invoices SET disputed=?,version=version+1 WHERE id=?",
                            (int(disputed), invoice_id))

    def record_settlement(self, settlement_id, invoice_id, amount_cents, currency="CAD"):
        """Demo trusted-adapter boundary; never expose directly as a public webhook."""
        self.money(amount_cents)
        if currency != "CAD":
            raise ValueError("Only CAD supported")
        with self.transaction():
            current = self.invoice(invoice_id)
            prior = self.db.execute("SELECT * FROM settlements WHERE id=?", (settlement_id,)).fetchone()
            if prior:
                if (prior["invoice_id"], prior["amount"], prior["currency"]) != (
                    invoice_id, amount_cents, currency
                ):
                    raise ValueError("Settlement ID reused with different data")
            else:
                if amount_cents > current["outstanding_cents"]:
                    raise ValueError("Overpayment requires review; do not auto-allocate")
                self.db.execute("INSERT INTO settlements VALUES(?,?,?,?)",
                                (settlement_id, invoice_id, amount_cents, currency))
                self.db.execute("UPDATE invoices SET version=version+1 WHERE id=?", (invoice_id,))
        return self.invoice(invoice_id)

    def propose(self, action_id, invoice_id, kind, evidence_ids):
        """The runtime agent may propose these actions, never approve itself."""
        if kind not in {"collection_reminder", "request_information"}:
            raise ValueError("Unsupported action")
        if not isinstance(evidence_ids, list) or not evidence_ids or not all(
            isinstance(x, str) for x in evidence_ids
        ):
            raise ValueError("At least one evidence reference is required")
        refs = json.dumps(sorted(set(evidence_ids)))
        with self.transaction():
            current = self.invoice(invoice_id)
            known = {e["id"] for e in self.evidence(invoice_id)}
            if not set(evidence_ids) <= known:
                raise ValueError("Unknown evidence reference")
            prior = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
            if prior:
                if (prior["invoice_id"], prior["version"], prior["kind"], prior["evidence_ids"]) != (
                    invoice_id, current["version"], kind, refs
                ):
                    raise ValueError("Action ID reused with different or stale data")
            else:
                self.db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?)", (
                    action_id, invoice_id, current["version"], kind, refs, "proposed"))
        return self.action(action_id)

    def action(self, action_id):
        row = self.db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown action")
        return dict(row)

    def validate_action(self, action):
        current = self.invoice(action["invoice_id"])
        if action["version"] != current["version"]:
            raise ValueError("Stale plan: investigate and propose again")
        if action["kind"] == "collection_reminder" and (
            current["disputed"] or current["outstanding_cents"] == 0
        ):
            raise ValueError("Cannot chase a disputed or fully settled invoice")

    def approve(self, action_id):
        """Local owner operation only. Future HTTP route needs authentication."""
        with self.transaction():
            action = self.action(action_id)
            if action["status"] == "executed_simulated":
                return action
            self.validate_action(action)
            self.db.execute("UPDATE actions SET status='approved' WHERE id=?", (action_id,))
        return self.action(action_id)

    def execute_simulated(self, action_id):
        """Records simulated dispatch; no email or money moves."""
        with self.transaction():
            action = self.action(action_id)
            if action["status"] == "executed_simulated":
                return action
            if action["status"] != "approved":
                raise ValueError("Owner approval required")
            self.validate_action(action)
            self.db.execute("UPDATE actions SET status='executed_simulated' WHERE id=?", (action_id,))
        return self.action(action_id)
