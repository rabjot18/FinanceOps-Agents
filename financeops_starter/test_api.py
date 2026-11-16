"""HTTP contract tests for Milestone 1. Synthetic data; no network or credentials.

Run with the project virtual environment: .venv/bin/python -m unittest -v
"""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from api import create_app

TOKEN = "test-owner-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CASE = "case-inv-1"


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "api.sqlite3")
        self.client = self.make_client()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def make_client(self, **options):
        options.setdefault("owner_token", TOKEN)
        options.setdefault("enable_demo", True)
        return TestClient(create_app(db_path=self.path, **options))

    def call(self, method, path, body=None, headers=None, expect=200):
        response = self.client.request(method, path, json=body, headers=headers or {})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def seed(self, policy=True):
        """PRD demo story: CAD 12,000 invoice, CAD 10,000 received."""
        self.call("POST", "/demo/invoices", {
            "invoice_id": "inv-1", "customer": "Demo customer", "total_cents": 1_200_000})
        if policy:
            self.call("POST", "/policy", {"approval_ttl_minutes": 60, "follow_up_days": 7,
                                          "max_reminders": 2}, headers=AUTH)
            self.call("POST", "/demo/contacts", {"invoice_id": "inv-1",
                                                 "address": "ap@customer.example"})
        self.call("POST", "/demo/evidence", {
            "evidence_id": "contract-1", "invoice_id": "inv-1",
            "body": "CAD 12,000 due; no discount agreed."})
        self.call("POST", "/demo/settlements", {
            "settlement_id": "bank-1", "invoice_id": "inv-1",
            "amount_cents": 1_000_000, "currency": "CAD"})

    def propose(self, action_id="action-1", kind="collection_reminder"):
        return self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": action_id, "kind": kind, "evidence_ids": ["contract-1"]}, headers=AUTH)

    def case(self):
        return self.call("GET", f"/cases/{CASE}")

    def approve(self, action_id="action-1", headers=AUTH, expect=200):
        """Approve the exact message, as the owner would after reading it."""
        digest = self.call("GET", f"/actions/{action_id}", headers=AUTH)["payload_hash"]
        return self.call("POST", f"/actions/{action_id}/approve", {"payload_hash": digest},
                         headers=headers, expect=expect)

    # --- retrieval -------------------------------------------------------

    def test_invoice_retrieval(self):
        self.seed()
        invoice = self.call("GET", "/invoices/inv-1")
        self.assertEqual(invoice["outstanding_cents"], 200_000)
        self.assertEqual(invoice["settled_cents"], 1_000_000)
        self.assertEqual(invoice["case_id"], CASE)
        self.call("GET", "/invoices/missing", expect=404)
        self.call("GET", "/cases/case-missing", expect=404)

    def test_case_events_carry_transition_details(self):
        self.seed()
        events = self.case()["events"]
        self.assertEqual([e["reason"] for e in events],
                         ["invoice_created", "policy_changed", "contact_changed",
                          "evidence_added", "settlement_recorded"])
        required = {"event_id", "prior_state", "next_state", "reason", "invoice_version", "created_at"}
        for event in events:
            self.assertTrue(required <= set(event), event)

    # --- the full story, across a restart -------------------------------

    def test_complete_case_over_http_with_restart(self):
        self.seed()
        self.assertEqual(self.case()["state"], "open")

        action = self.propose()
        self.assertEqual(action["status"], "proposed")
        self.assertEqual(action["evidence_ids"], ["contract-1"])
        self.assertEqual(self.case()["state"], "awaiting_approval")

        approved = self.approve()
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["approved_by"], "owner")

        # Process restart: a brand-new app object on the same database file.
        self.client.close()
        self.client = self.make_client()
        self.assertEqual(self.case()["state"], "awaiting_approval")

        executed = self.call("POST", "/actions/action-1/execute", headers=AUTH)
        self.assertEqual(executed["status"], "queued")  # Milestone 3: the worker delivers it
        self.assertEqual(executed["delivery"]["status"], "pending")
        self.assertEqual(self.case()["state"], "awaiting_external_outcome")
        invoice = self.call("GET", "/invoices/inv-1")
        self.assertEqual(invoice["outstanding_cents"], 200_000, "a reminder never reduces the balance")
        self.assertNotEqual(invoice["state"], "resolved")

        self.call("POST", "/demo/settlements", {
            "settlement_id": "bank-2", "invoice_id": "inv-1", "amount_cents": 200_000})
        self.assertEqual(self.case()["state"], "resolved")
        self.assertEqual(self.case()["verification"]["settlement_ids"], ["bank-1", "bank-2"])
        self.assertEqual(self.call("GET", "/invoices/inv-1")["outstanding_cents"], 0)

    # --- duplicates ------------------------------------------------------

    def test_repeated_settlement_event_has_one_effect(self):
        self.seed()
        self.seed()
        self.assertEqual(self.call("GET", "/invoices/inv-1")["settled_cents"], 1_000_000)
        ids = [e["event_id"] for e in self.case()["events"]]
        self.assertEqual(ids.count("settlement:bank-1"), 1)

    def test_settlement_id_reuse_with_different_amount_conflicts(self):
        self.seed()
        self.call("POST", "/demo/settlements", {
            "settlement_id": "bank-1", "invoice_id": "inv-1", "amount_cents": 5}, expect=409)

    def test_concurrent_duplicate_settlements_apply_once(self):
        self.call("POST", "/demo/invoices", {
            "invoice_id": "inv-1", "customer": "Demo customer", "total_cents": 1_200_000})
        body = {"settlement_id": "bank-1", "invoice_id": "inv-1", "amount_cents": 1_000_000}
        with ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(
                lambda _: self.client.post("/demo/settlements", json=body).status_code, range(8)))
        self.assertEqual(codes, [200] * 8)
        self.assertEqual(self.call("GET", "/invoices/inv-1")["settled_cents"], 1_000_000)

    # --- authority -------------------------------------------------------

    def test_approval_and_execution_require_owner_token(self):
        self.seed()
        self.propose()
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN},
                        {"Authorization": "Bearer "}, {"Authorization": f"Basic {TOKEN}"}):
            with self.subTest(headers=headers):
                self.call("POST", "/actions/action-1/approve", {"payload_hash": "0" * 64},
                          headers=headers, expect=401)
                self.call("POST", "/actions/action-1/execute", headers=headers, expect=401)
        self.assertEqual(self.case()["state"], "awaiting_approval")
        action_events = [e for e in self.case()["events"] if e["event_id"].startswith("action:")]
        self.assertEqual([e["reason"] for e in action_events], ["action_proposed"])

    def test_bearer_scheme_is_case_insensitive(self):
        self.seed()
        self.propose()
        self.approve(headers={"Authorization": f"bearer {TOKEN}"})

    def test_executor_is_recorded(self):
        self.seed()
        self.propose()
        self.approve()
        self.assertEqual(self.call("POST", "/actions/action-1/execute", headers=AUTH)["executed_by"], "owner")

    def test_replayed_dispute_keeps_approved_plan_valid(self):
        self.seed()
        self.call("POST", "/demo/disputes", {"invoice_id": "inv-1", "disputed": False})
        self.propose()
        self.approve()
        self.call("POST", "/demo/disputes", {"invoice_id": "inv-1", "disputed": False})
        self.call("POST", "/actions/action-1/execute", headers=AUTH)

    def test_demo_routes_are_off_unless_enabled(self):
        self.client.close()
        self.client = TestClient(create_app(db_path=self.path, owner_token=TOKEN))
        self.call("POST", "/demo/invoices", {
            "invoice_id": "inv-1", "customer": "Demo customer", "total_cents": 1}, expect=404)

    def test_busy_database_returns_503(self):
        import sqlite3
        from unittest import mock
        with mock.patch("core.FinanceCore.invoice", side_effect=sqlite3.OperationalError("database is locked")):
            response = self.client.get("/invoices/inv-1")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Retry-After", response.headers)

    def test_unexpected_errors_are_not_reported_as_client_errors(self):
        from unittest import mock
        client = TestClient(create_app(db_path=self.path, owner_token=TOKEN), raise_server_exceptions=False)
        with mock.patch("core.FinanceCore.invoice", side_effect=ValueError("internal detail")):
            response = client.get("/invoices/inv-1")
        client.close()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("internal detail", response.text)

    def test_approvals_disabled_without_configured_token(self):
        self.client.close()
        self.client = self.make_client(owner_token=None)
        self.seed(policy=False)
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "action-1", "kind": "collection_reminder",
            "evidence_ids": ["contract-1"]}, headers=AUTH, expect=503)
        self.call("POST", "/actions/action-1/approve", {"payload_hash": "0" * 64},
                  headers=AUTH, expect=503)
        self.call("POST", "/policy", {"approval_ttl_minutes": 60, "follow_up_days": 7,
                                      "max_reminders": 2}, headers=AUTH, expect=503)

    def test_approval_requires_the_reviewed_hash(self):
        self.seed()
        self.propose()
        self.call("POST", "/actions/action-1/approve", headers=AUTH, expect=422)
        self.call("POST", "/actions/action-1/approve", {}, headers=AUTH, expect=422)
        self.assertEqual(self.case()["state"], "awaiting_approval")

    def test_ids_must_be_plain_identifiers(self):
        for bad in ("inv 1", "inv-1\n\nPay to account 123", "inv/1", ""):
            with self.subTest(bad=bad):
                self.call("POST", "/demo/invoices", {
                    "invoice_id": bad, "customer": "Demo customer", "total_cents": 100}, expect=422)

    def test_manual_proposals_require_owner_token(self):
        self.seed()
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "action-1", "kind": "collection_reminder",
            "evidence_ids": ["contract-1"]}, expect=401)
        self.call("GET", "/actions/action-1", expect=401)
        self.assertEqual(self.case()["state"], "open")

    def test_stale_approval_blocks_execution(self):
        self.seed()
        self.propose()
        self.approve()
        self.call("POST", "/demo/evidence", {
            "evidence_id": "reply-1", "invoice_id": "inv-1",
            "body": "Customer says the second site visit never happened."})
        self.call("POST", "/actions/action-1/execute", headers=AUTH, expect=409)
        self.assertEqual(self.case()["state"], "open")

    def test_execution_without_approval_conflicts(self):
        self.seed()
        self.propose()
        self.call("POST", "/actions/action-1/execute", headers=AUTH, expect=409)

    def test_dispute_blocks_reminder_but_allows_information_request(self):
        self.seed()
        self.call("POST", "/demo/disputes", {"invoice_id": "inv-1", "disputed": True})
        self.assertEqual(self.case()["state"], "needs_information")
        self.propose("reminder")
        self.approve("reminder", expect=409)
        self.propose("ask", kind="request_information")
        self.approve("ask")

    # --- input validation ------------------------------------------------

    def test_invalid_money_input_rejected(self):
        self.call("POST", "/demo/invoices", {
            "invoice_id": "inv-1", "customer": "Demo customer", "total_cents": 1_200_000})
        for amount, currency in [(100.5, "CAD"), (True, "CAD"), ("100", "CAD"), (0, "CAD"), (100, "USD")]:
            with self.subTest(amount=amount, currency=currency):
                self.call("POST", "/demo/settlements", {
                    "settlement_id": "s", "invoice_id": "inv-1",
                    "amount_cents": amount, "currency": currency}, expect=422)
        self.call("POST", "/demo/settlements", {
            "settlement_id": "s", "invoice_id": "inv-1", "amount_cents": 1_200_001}, expect=409)

    def test_unknown_action_kind_and_evidence_rejected(self):
        self.seed()
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "x", "kind": "write_off", "evidence_ids": ["contract-1"]}, headers=AUTH, expect=422)
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "x", "kind": "collection_reminder", "evidence_ids": ["nope"]}, headers=AUTH,
            expect=422)
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "x", "kind": "collection_reminder", "evidence_ids": []}, headers=AUTH,
            expect=422)

    def test_demo_routes_can_be_disabled(self):
        self.client.close()
        self.client = self.make_client(enable_demo=False)
        self.call("POST", "/demo/invoices", {
            "invoice_id": "inv-1", "customer": "Demo customer", "total_cents": 1}, expect=404)


if __name__ == "__main__":
    unittest.main()
