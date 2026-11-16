import sqlite3
import tempfile
import unittest
from pathlib import Path
from core import FinanceCore


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.sqlite3")
        self.app = FinanceCore(self.path)
        self.app.create_invoice("i", "customer", 1_200_000)
        self.app.add_evidence("e", "i", "Approved terms: CAD 12,000.")

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def plan(self):
        return self.app.propose("a", "i", "collection_reminder", ["e"])

    def test_partial_payment(self):
        result = self.app.record_settlement("s", "i", 1_000_000)
        self.assertEqual(result["outstanding_cents"], 200_000)
        self.assertEqual(result["state"], "open")

    def test_duplicate_settlement(self):
        self.app.record_settlement("s", "i", 100)
        self.app.record_settlement("s", "i", 100)
        self.assertEqual(self.app.invoice("i")["settled_cents"], 100)

    def test_duplicate_conflict(self):
        self.app.record_settlement("s", "i", 100)
        with self.assertRaises(ValueError):
            self.app.record_settlement("s", "i", 200)

    def test_no_execution_without_approval(self):
        self.plan()
        with self.assertRaises(ValueError):
            self.app.execute_simulated("a")

    def test_action_does_not_close_case(self):
        self.plan()
        self.app.approve("a")
        self.app.execute_simulated("a")
        self.assertEqual(self.app.invoice("i")["state"], "open")
        self.assertEqual(self.app.execute_simulated("a")["status"], "executed_simulated")

    def test_stale_approval(self):
        self.plan()
        self.app.approve("a")
        self.app.add_evidence("e2", "i", "Customer disputes delivery.")
        with self.assertRaises(ValueError):
            self.app.execute_simulated("a")

    def test_disputed_invoice(self):
        self.app.set_disputed("i", True)
        self.plan()
        with self.assertRaises(ValueError):
            self.app.approve("a")

    def test_information_request_allowed_on_dispute(self):
        self.app.set_disputed("i", True)
        self.app.propose("a", "i", "request_information", ["e"])
        self.app.approve("a")
        self.assertEqual(self.app.execute_simulated("a")["status"], "executed_simulated")

    def test_unknown_evidence(self):
        with self.assertRaises(ValueError):
            self.app.propose("a", "i", "request_information", ["nonexistent"])

    def test_restart_and_resolution(self):
        self.app.record_settlement("s", "i", 1_200_000)
        self.app.close()
        self.app = FinanceCore(self.path)
        self.assertEqual(self.app.invoice("i")["state"], "resolved")

    def test_overpayment_currency_and_float_rejected(self):
        for amount, currency in [(1_200_001, "CAD"), (100, "USD"), (100.5, "CAD"), (True, "CAD")]:
            with self.subTest(amount=amount, currency=currency), self.assertRaises(ValueError):
                self.app.record_settlement("s", "i", amount, currency)


class CaseTests(unittest.TestCase):
    """Milestone 1: persistent cases with an append-only event log."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.sqlite3")
        self.app = FinanceCore(self.path)
        self.app.create_invoice("i", "customer", 1_200_000)
        self.app.add_evidence("e", "i", "Approved terms: CAD 12,000.")

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def state(self):
        return self.app.case_for_invoice("i")["state"]

    def reasons(self):
        return [e["reason"] for e in self.app.case_for_invoice("i")["events"]]

    def test_invoice_opens_a_case(self):
        case = self.app.case_for_invoice("i")
        self.assertEqual(case["id"], FinanceCore.case_id("i"))
        self.assertEqual(case["state"], "open")
        self.assertEqual(self.reasons(), ["invoice_created", "evidence_added"])

    def test_events_record_transition_details(self):
        self.app.record_settlement("s", "i", 1_000_000)
        event = self.app.case_for_invoice("i")["events"][-1]
        self.assertEqual(event["event_id"], "settlement:s")
        self.assertEqual((event["prior_state"], event["next_state"]), ("open", "open"))
        self.assertEqual(event["reason"], "settlement_recorded")
        self.assertEqual(event["invoice_version"], self.app.invoice("i")["version"])
        self.assertTrue(event["created_at"])

    def test_lifecycle_through_approval_execution_and_settlement(self):
        self.app.record_settlement("s1", "i", 1_000_000)
        self.app.propose("a", "i", "collection_reminder", ["e"])
        self.assertEqual(self.state(), "awaiting_approval")
        self.app.approve("a")
        self.assertEqual(self.state(), "awaiting_approval")
        self.app.execute_simulated("a")
        self.assertEqual(self.state(), "awaiting_external_outcome")
        self.assertEqual(self.app.invoice("i")["outstanding_cents"], 200_000)
        self.app.record_settlement("s2", "i", 200_000)
        self.assertEqual(self.state(), "resolved")

    def test_dispute_moves_case_to_needs_information(self):
        self.app.set_disputed("i", True)
        self.assertEqual(self.state(), "needs_information")
        self.app.set_disputed("i", False)
        self.assertEqual(self.state(), "open")

    def test_new_evidence_returns_pending_plan_to_open(self):
        self.app.propose("a", "i", "collection_reminder", ["e"])
        self.app.add_evidence("e2", "i", "Customer disputes delivery.")
        self.assertEqual(self.state(), "open")

    def test_replayed_events_are_logged_once(self):
        self.app.record_settlement("s", "i", 100)
        self.app.record_settlement("s", "i", 100)
        self.app.add_evidence("e", "i", "Approved terms: CAD 12,000.")
        self.app.propose("a", "i", "collection_reminder", ["e"])
        self.app.propose("a", "i", "collection_reminder", ["e"])
        self.app.approve("a")
        self.app.approve("a")
        ids = [e["event_id"] for e in self.app.case_for_invoice("i")["events"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("settlement:s"), 1)

    def test_replayed_dispute_is_a_no_op(self):
        self.app.set_disputed("i", True)
        self.app.propose("a", "i", "request_information", ["e"])
        self.app.approve("a")
        version = self.app.invoice("i")["version"]
        self.app.set_disputed("i", True)  # retried event: same value
        self.assertEqual(self.app.invoice("i")["version"], version)
        self.assertEqual(self.reasons().count("dispute_opened"), 1)
        self.assertEqual(self.app.execute_simulated("a")["status"], "executed_simulated")

    def test_execution_records_executor(self):
        self.app.propose("a", "i", "collection_reminder", ["e"])
        self.app.approve("a")
        action = self.app.execute_simulated("a", executor="owner")
        self.assertEqual(action["executed_by"], "owner")
        self.assertTrue(action["executed_at"])

    def test_invalid_input_has_its_own_error_type(self):
        from core import InvalidInput
        for call in (lambda: self.app.record_settlement("s", "i", 1.5),
                     lambda: self.app.propose("a", "i", "write_off", ["e"]),
                     lambda: self.app.propose("a", "i", "collection_reminder", ["nope"])):
            with self.assertRaises(InvalidInput):
                call()

    def test_case_state_agrees_with_invoice_state(self):
        self.assertEqual(self.state(), self.app.invoice("i")["state"])
        self.app.set_disputed("i", True)
        self.assertEqual(self.state(), self.app.invoice("i")["state"])
        self.app.set_disputed("i", False)
        self.app.record_settlement("s", "i", 1_200_000)
        self.assertEqual(self.state(), self.app.invoice("i")["state"])

    def test_resolved_case_rejects_new_proposals(self):
        self.app.record_settlement("s", "i", 1_200_000)
        with self.assertRaisesRegex(ValueError, "resolved"):
            self.app.propose("a", "i", "request_information", ["e"])
        self.assertEqual(self.state(), "resolved")

    def test_approval_records_approver(self):
        self.app.propose("a", "i", "collection_reminder", ["e"])
        action = self.app.approve("a", approver="owner")
        self.assertEqual(action["approved_by"], "owner")
        self.assertTrue(action["approved_at"])

    def test_event_log_is_append_only(self):
        for sql in ("UPDATE case_events SET reason='tampered'", "DELETE FROM case_events"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                with self.app.transaction():
                    self.app.db.execute(sql)

    def test_case_survives_restart(self):
        self.app.propose("a", "i", "collection_reminder", ["e"])
        before = self.app.case_for_invoice("i")
        self.app.close()
        self.app = FinanceCore(self.path)
        self.assertEqual(self.app.case_for_invoice("i"), before)

    def test_unknown_case(self):
        with self.assertRaises(ValueError):
            self.app.case("case-missing")


class MigrationTests(unittest.TestCase):
    def test_upgrades_a_starter_database(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "old.sqlite3")
            old = sqlite3.connect(path)
            old.executescript("""
            CREATE TABLE invoices (id TEXT PRIMARY KEY, customer TEXT NOT NULL,
                total INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                disputed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE settlements (id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
                amount INTEGER NOT NULL, currency TEXT NOT NULL);
            CREATE TABLE evidence (id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TABLE actions (id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
                version INTEGER NOT NULL, kind TEXT NOT NULL,
                evidence_ids TEXT NOT NULL, status TEXT NOT NULL);
            INSERT INTO invoices VALUES ('old', 'customer', 500, 2, 0);
            INSERT INTO settlements VALUES ('s', 'old', 500, 'CAD');
            """)
            old.close()
            app = FinanceCore(path)
            try:
                case = app.case_for_invoice("old")
                self.assertEqual(case["state"], "resolved")
                self.assertEqual([e["reason"] for e in case["events"]], ["migrated"])
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
