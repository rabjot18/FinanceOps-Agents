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


if __name__ == "__main__":
    unittest.main()
