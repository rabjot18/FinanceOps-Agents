"""Milestone 3, delivery and waiting: the worker, the simulated inbox, faults,
restarts, follow-ups, replies, and the whole story over HTTP.

Synthetic data, no network, no model cost (re-investigation uses the fake
client from test_agent.py). Run: .venv/bin/python -m unittest test_worker -v
"""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from adapters import SimulatedInbox
from agent import Investigator
from api import create_app
from core import Conflict, FinanceCore
from test_agent import FakeClient, call, reply, text
from test_outbox import CONTACT, M3TestCase
from worker import run_once


class SimulatedCrash(BaseException):
    """The process dies: nothing after this line runs, no handler cleans up."""


class CrashAfterAccept(SimulatedInbox):
    def send(self, *args):
        super().send(*args)
        raise SimulatedCrash()


class WorkerTestCase(M3TestCase):
    def setUp(self):
        super().setUp()
        self.inbox = SimulatedInbox(self.path)

    def run_worker(self, adapter=None, factory=None):
        return run_once(self.core, adapter or self.inbox, factory)

    def restart(self):
        """Process restart: a new core on the same database file and clock."""
        self.core.close()
        self.core = FinanceCore(self.path, clock=self.clock)

    def delivered(self):
        return self.inbox.messages()


class DeliveryTests(WorkerTestCase):
    def test_delivers_once_then_waits_for_money(self):
        self.queue()
        self.assertEqual(self.run_worker()["sent"], ["a1"])
        self.assertEqual(self.run_worker()["sent"], [])
        [message] = self.delivered()
        self.assertEqual(message["recipient"], CONTACT)
        self.assertEqual(message["idempotency_key"], "a1")
        self.assertIn("CAD 2,000.00 outstanding", message["body"])
        self.assertEqual(self.core.action("a1")["status"], "dispatched")
        self.assertEqual(self.state(), "awaiting_external_outcome")
        self.assertEqual(self.core.invoice("inv-1")["outstanding_cents"], 200_000,
                         "a delivered reminder is not a payment")
        job = self.core.job("check:a1")
        self.assertEqual((job["kind"], job["status"]), ("settlement_check", "pending"))
        self.assertIn("message_dispatched_simulated", self.reasons())

    def test_timeout_after_accept_checks_before_resending(self):
        inbox = SimulatedInbox(self.path, faults=["timeout_after_accept"])
        self.queue()
        self.assertEqual(self.run_worker(inbox)["unknown"], ["a1"])
        self.assertEqual(self.core.outbox_item("a1")["status"], "unknown")
        self.assertEqual(self.run_worker(inbox)["sent"], ["a1"])  # found by lookup
        self.run_worker(inbox)
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(self.core.outbox_item("a1")["attempts"], 1, "never sent a second time")
        self.assertIn("dispatch_outcome_unknown", self.reasons())

    def test_timeout_before_accept_resends_exactly_once(self):
        inbox = SimulatedInbox(self.path, faults=["timeout_before_accept"])
        self.queue()
        self.run_worker(inbox)
        self.assertEqual(self.delivered(), [])
        self.assertEqual(self.run_worker(inbox)["sent"], [], "waits before sending again")
        self.clock.advance(minutes=1)
        self.assertEqual(self.run_worker(inbox)["sent"], ["a1"])
        self.run_worker(inbox)
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(self.core.outbox_item("a1")["attempts"], 2)

    def test_crash_mid_send_then_restart_sends_no_duplicate(self):
        self.queue()
        with self.assertRaises(SimulatedCrash):
            self.run_worker(CrashAfterAccept(self.path))
        self.restart()
        self.assertEqual(self.core.outbox_item("a1")["status"], "sending")
        self.assertEqual(self.run_worker()["sent"], [], "too soon to assume the sender died")
        self.clock.advance(minutes=3)
        self.assertEqual(self.run_worker()["sent"], ["a1"])
        self.assertEqual(len(self.delivered()), 1)
        self.assertEqual(self.core.action("a1")["status"], "dispatched")

    def test_restart_between_execute_and_delivery_keeps_the_message(self):
        self.queue()
        self.restart()
        self.assertEqual(self.state(), "awaiting_external_outcome")
        self.assertEqual(self.run_worker()["sent"], ["a1"])

    def test_rejected_message_fails_without_retry(self):
        inbox = SimulatedInbox(self.path, faults=["reject"])
        self.queue()
        self.assertEqual(self.run_worker(inbox)["failed"], ["a1"])
        self.run_worker(inbox)
        self.assertEqual(self.delivered(), [])
        self.assertEqual(self.core.action("a1")["status"], "failed")
        self.assertEqual(self.state(), "open", "the owner must decide what to do next")

    def test_new_evidence_after_queueing_cancels_the_send(self):
        self.queue()
        self.core.add_evidence("reply-1", "inv-1", "Customer: the second visit never happened.")
        self.assertEqual(self.run_worker()["cancelled"], ["a1"])
        self.assertEqual(self.delivered(), [])
        self.assertEqual(self.core.action("a1")["status"], "cancelled")
        self.assertEqual(self.state(), "open")

    def test_payment_before_delivery_cancels_the_reminder(self):
        self.queue()
        self.core.record_settlement("bank-2", "inv-1", 200_000)
        self.assertEqual(self.run_worker()["cancelled"], ["a1"])
        self.assertEqual(self.delivered(), [])
        case = self.core.case_for_invoice("inv-1")
        self.assertEqual(case["state"], "resolved")
        self.assertEqual(case["verification"]["settlement_ids"], ["bank-1", "bank-2"])


class WaitingTests(WorkerTestCase):
    def deliver(self):
        self.queue()
        self.run_worker()

    def test_follow_up_date_without_payment_reopens_the_case(self):
        self.deliver()
        self.clock.advance(days=6)
        self.assertEqual(self.run_worker()["follow_ups_elapsed"], [])
        self.clock.advance(days=1, minutes=1)
        self.assertEqual(self.run_worker()["follow_ups_elapsed"], ["check:a1"])
        self.assertEqual(self.state(), "open")
        self.assertEqual(self.core.action("a1")["status"], "follow_up_due")
        self.assertEqual(self.core.reminders_sent("inv-1"), 1, "it still counts toward the limit")
        self.assertIn("follow_up_elapsed", self.reasons())

    def test_payment_after_delivery_resolves_and_skips_the_follow_up(self):
        self.deliver()
        self.core.record_settlement("bank-2", "inv-1", 200_000)
        self.assertEqual(self.state(), "resolved")
        self.clock.advance(days=8)
        self.assertEqual(self.run_worker()["follow_ups_elapsed"], [])
        self.assertEqual(self.core.job("check:a1")["detail"], "not needed: the case moved on")
        self.assertEqual(self.state(), "resolved")

    def test_partial_payment_after_delivery_is_not_closure(self):
        self.deliver()
        self.core.record_settlement("bank-2", "inv-1", 100_000)
        case = self.core.case_for_invoice("inv-1")
        self.assertEqual(case["state"], "open")
        self.assertIsNone(case["verification"])

    def test_customer_reply_triggers_a_fresh_investigation(self):
        self.deliver()
        self.core.record_reply("reply-1", "inv-1",
                               "Customer: the second visit never happened, so we paid 10,000.")
        self.assertEqual(self.state(), "open")
        # Without a model client the job waits instead of spending money.
        self.assertEqual(self.run_worker()["investigations_waiting"], 1)

        fake = FakeClient(
            reply(call("get_evidence"), call("get_policy")),
            reply(call("propose_action", {
                "kind": "request_information", "evidence_ids": ["reply-1"],
                "summary": "Customer disputes the second visit; ask for their records.",
                "findings": [{"statement": "Customer says the second visit did not happen.",
                              "evidence_ids": ["reply-1"]}],
                "unknowns": ["Whether the second visit took place."]})),
            reply(text("Proposed."), stop="end_turn"))
        result = self.run_worker(factory=lambda core: Investigator(core, fake))
        [investigation] = result["investigations"]
        self.assertEqual(investigation["status"], "proposed")
        self.assertEqual(result["investigations_waiting"], 0)
        run = self.core.agent_run(investigation["run"])
        action = self.core.action_detail(run["action_id"])
        self.assertEqual(action["kind"], "request_information")
        self.assertEqual(action["evidence_ids"], ["reply-1"])
        self.assertEqual(action["rationale"], "Customer disputes the second visit; ask for their records.")
        self.assertEqual(self.state(), "awaiting_approval")
        policy_call = next(c for c in run["calls"] if c["name"] == "get_policy")
        self.assertIn('"reminders_already_sent": 1', policy_call["result"])
        self.assertNotIn(CONTACT, policy_call["result"], "the model never sees the address")
        self.assertEqual(self.run_worker(factory=lambda core: Investigator(core, fake))["investigations"], [])


class ReviewFixTests(WorkerTestCase):
    """Paths found by the Milestone 3 reviews."""

    def test_approval_that_expires_before_delivery_is_not_sent(self):
        self.propose()
        self.core.approve("a1")
        self.clock.advance(minutes=59)
        self.core.execute("a1")
        self.clock.advance(minutes=2)  # the worker was down until after expiry
        self.assertEqual(self.run_worker()["cancelled"], ["a1"])
        self.assertEqual(self.delivered(), [])
        self.assertIn("expired", self.core.outbox_item("a1")["last_error"])

    def test_retry_of_a_send_that_started_in_time_still_completes(self):
        inbox = SimulatedInbox(self.path, faults=["timeout_before_accept"])
        self.queue()
        self.run_worker(inbox)
        self.clock.advance(minutes=61)  # approval has expired by now
        self.assertEqual(self.run_worker(inbox)["sent"], ["a1"])

    def test_repeated_timeouts_end_in_failure_without_any_message(self):
        inbox = SimulatedInbox(self.path, faults=["timeout_before_accept"] * 5)
        self.queue()
        for _ in range(6):
            self.run_worker(inbox)
            self.clock.advance(minutes=60)
        self.assertEqual(self.core.outbox_item("a1")["status"], "failed")
        self.assertEqual(self.core.action("a1")["status"], "failed")
        self.assertEqual(self.delivered(), [])

    def test_one_broken_item_does_not_stop_the_round(self):
        class BrokenForA1(SimulatedInbox):
            def send(self, key, *args):
                if key == "a1":
                    raise RuntimeError("bug in adapter")
                return super().send(key, *args)
        self.queue("a1")
        self.queue("a2")
        with self.assertLogs("financeops.worker", level="ERROR"):
            result = self.run_worker(BrokenForA1(self.path))
        self.assertEqual(result["errors"], [{"item": "a1", "error": "RuntimeError"}])
        self.assertEqual(result["sent"], ["a2"])

    def test_failed_model_run_retries_the_reply_later(self):
        self.core.record_reply("reply-1", "inv-1", "Customer: we will pay on Friday.")
        broken = FakeClient(reply(text("cut off"), stop="max_tokens"))
        result = self.run_worker(factory=lambda core: Investigator(core, broken))
        self.assertEqual(result["investigations"][0]["status"], "failed")
        job = self.core.job("investigate:reply-1")
        self.assertEqual((job["status"], job["attempts"]), ("pending", 1))
        self.assertEqual(self.run_worker(factory=lambda core: Investigator(core, broken))
                         ["investigations"], [], "waits before retrying")
        self.clock.advance(minutes=5)
        working = FakeClient(reply(text("Nothing to propose."), stop="end_turn"))
        result = self.run_worker(factory=lambda core: Investigator(core, working))
        self.assertEqual(result["investigations"][0]["status"], "no_proposal")
        self.assertEqual(self.core.job("investigate:reply-1")["status"], "done")

    def test_busy_case_does_not_use_up_job_attempts(self):
        self.core.record_reply("reply-1", "inv-1", "Customer: we will pay on Friday.")
        self.core.start_agent_run("case-inv-1", "claude-opus-5-5")  # someone else's run
        idle = FakeClient()
        for _ in range(3):
            self.run_worker(factory=lambda core: Investigator(core, idle))
            self.clock.advance(minutes=4)
        job = self.core.job("investigate:reply-1")
        self.assertEqual((job["status"], job["attempts"]), ("pending", 0))
        self.assertEqual(idle.requests, [])

    def test_action_from_before_milestone_3_cannot_be_approved(self):
        self.propose()
        with self.core.transaction():
            self.core.db.execute("UPDATE actions SET payload=NULL, payload_hash=NULL WHERE id='a1'")
        with self.assertRaisesRegex(Conflict, "predates"):
            self.core.approve("a1")
        self.core.reject("a1")

    def test_replayed_delivery_confirmation_changes_nothing(self):
        self.queue()
        self.run_worker()
        first = self.core.outbox_item("a1")["external_id"]
        self.core.mark_sent("a1", "sim-other")
        self.assertEqual(self.core.outbox_item("a1")["external_id"], first)
        self.assertEqual(self.reasons().count("message_dispatched_simulated"), 1)


TOKEN = "test-owner-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CASE = "case-inv-1"


class HttpStoryTests(unittest.TestCase):
    """BUILD_PLAN Phase 3 acceptance, through the real HTTP API."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "http.sqlite3")
        self.client = self.make_client()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def make_client(self):
        return TestClient(create_app(db_path=self.path, owner_token=TOKEN, enable_demo=True))

    def call(self, method, path, body=None, headers=None, expect=200):
        response = self.client.request(method, path, json=body, headers=headers or {})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def state(self):
        return self.call("GET", f"/cases/{CASE}")["state"]

    def test_full_receivable_loop_with_restart(self):
        self.call("POST", "/policy", {"approval_ttl_minutes": 60, "follow_up_days": 7,
                                      "max_reminders": 2}, headers=AUTH)
        self.call("POST", "/demo/invoices", {"invoice_id": "inv-1", "customer": "Demo customer",
                                             "total_cents": 1_200_000})
        self.call("POST", "/demo/contacts", {"invoice_id": "inv-1", "address": CONTACT})
        self.call("POST", "/demo/evidence", {"evidence_id": "contract-1", "invoice_id": "inv-1",
                                             "body": "CAD 12,000 due; no discount agreed."})
        self.call("POST", "/demo/settlements", {"settlement_id": "bank-1", "invoice_id": "inv-1",
                                                "amount_cents": 1_000_000})
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "r1", "kind": "collection_reminder", "evidence_ids": ["contract-1"]},
            headers=AUTH)

        # The owner reads the exact message and approves THAT message.
        action = self.call("GET", "/actions/r1", headers=AUTH)
        self.assertEqual(action["payload"]["to"], CONTACT)
        self.call("POST", "/actions/r1/approve", {"payload_hash": "f" * 64}, headers=AUTH, expect=409)
        approved = self.call("POST", "/actions/r1/approve",
                             {"payload_hash": action["payload_hash"]}, headers=AUTH)
        self.assertTrue(approved["approval"]["expires_at"])
        self.assertEqual(self.call("POST", "/actions/r1/execute", headers=AUTH)["status"], "queued")
        self.assertEqual(self.call("GET", "/demo/inbox")["messages"], [], "execute sends nothing")

        self.client.close()  # restart before the worker runs
        self.client = self.make_client()
        run = self.call("POST", "/demo/worker/run", {})
        self.assertEqual(run["result"]["sent"], ["r1"])
        self.call("POST", "/demo/worker/run", {})
        self.assertEqual(len(self.call("GET", "/demo/inbox")["messages"]), 1)
        self.assertEqual(self.call("GET", "/actions/r1", headers=AUTH)["delivery"]["status"], "sent")
        self.assertEqual(self.state(), "awaiting_external_outcome")
        self.assertEqual(self.call("GET", "/invoices/inv-1")["outstanding_cents"], 200_000)

        # A week of simulated time passes with no payment.
        run = self.call("POST", "/demo/worker/run", {"advance_minutes": 7 * 24 * 60 + 1})
        self.assertEqual(run["result"]["follow_ups_elapsed"], ["check:r1"])
        self.assertEqual(self.state(), "open")

        # The customer replies; a re-investigation job waits for the owner's go-ahead.
        self.call("POST", "/demo/replies", {"evidence_id": "reply-1", "invoice_id": "inv-1",
                                            "body": "We will pay the balance on Friday."})
        self.assertEqual(self.call("POST", "/demo/worker/run", {})["result"]["investigations_waiting"], 1)

        # Only a settlement closes the case, with stored proof.
        self.call("POST", "/demo/settlements", {"settlement_id": "bank-2", "invoice_id": "inv-1",
                                                "amount_cents": 200_000})
        case = self.call("GET", f"/cases/{CASE}")
        self.assertEqual(case["state"], "resolved")
        self.assertEqual(case["verification"]["settlement_ids"], ["bank-1", "bank-2"])
        self.assertEqual(self.call("POST", "/demo/worker/run", {})["result"]["investigations_waiting"], 0)

    def test_owner_routes_require_the_token(self):
        for method, path, body in (("GET", "/policy", None),
                                   ("POST", "/policy", {"approval_ttl_minutes": 60,
                                                        "follow_up_days": 7, "max_reminders": 2}),
                                   ("GET", "/actions/r1", None),
                                   ("POST", "/actions/r1/reject", None)):
            with self.subTest(path=path):
                self.call(method, path, body, expect=401)

    def test_reject_over_http(self):
        self.call("POST", "/demo/invoices", {"invoice_id": "inv-1", "customer": "Demo customer",
                                             "total_cents": 1_200_000})
        self.call("POST", "/demo/evidence", {"evidence_id": "contract-1", "invoice_id": "inv-1",
                                             "body": "CAD 12,000 due."})
        self.call("POST", f"/cases/{CASE}/actions", {
            "action_id": "r1", "kind": "collection_reminder", "evidence_ids": ["contract-1"]},
            headers=AUTH)
        rejected = self.call("POST", "/actions/r1/reject", {"note": "Phone them first"}, headers=AUTH)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["approval"]["note"], "Phone them first")
        self.call("POST", "/actions/r1/execute", headers=AUTH, expect=409)

    def test_paid_investigations_in_demo_worker_need_the_owner_token(self):
        self.call("POST", "/demo/worker/run", {"run_investigations": True}, expect=401)
        self.call("POST", "/demo/worker/run", {"run_investigations": False})

    def test_missing_policy_is_404_and_worker_route_is_demo_only(self):
        self.call("GET", "/policy", headers=AUTH, expect=404)
        self.client.close()
        self.client = TestClient(create_app(db_path=self.path, owner_token=TOKEN, enable_demo=False))
        self.call("POST", "/demo/worker/run", {}, expect=404)


if __name__ == "__main__":
    unittest.main()
