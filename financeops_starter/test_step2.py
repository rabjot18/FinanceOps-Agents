"""Milestone 5 step 2: model spend and the daily budget, the agent's escalate
tool, replanning, and the owner briefing agent.

Scripted fake model only (see test_agent.FakeClient): no network, no cost.
Run: .venv/bin/python -m unittest test_step2 -v
"""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from anthropic.types import Message

import orchestrator
from adapters import SimulatedInbox
from agent import Investigator
from briefing import BriefingAgent
from core import (DEFAULT_AUTONOMY_RULES, DEFAULT_CADENCE_DAYS, MAX_REPLANS, Conflict,
                  FinanceCore, _at)
from test_agent import FakeClient, call, reply, text
from worker import run_once

CASE = "case-g-1"
DAY = 24 * 60 * 60


def briefing_reply(payload, usage=(2_000, 300), stop="end_turn"):
    return Message.model_validate({
        "id": "msg_brief", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": json.dumps(payload)}], "stop_reason": stop,
        "stop_sequence": None, "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}})


class Step2TestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "step2.sqlite3")
        self.core = FinanceCore(self.path, simulated_time=True)
        self.inbox = SimulatedInbox(self.path)
        self.core.set_policy(60, 7, 2, autonomy_rules=DEFAULT_AUTONOMY_RULES,
                             cadence_days=DEFAULT_CADENCE_DAYS, daily_model_budget_cents=400,
                             created_by="test")
        due = (_at(self.core.clock()) - timedelta(days=1)).isoformat()
        self.core.create_invoice("g-1", "Acme Design (synthetic)", 200_000, due_at=due)
        self.core.set_contact("g-1", "ap@example.com")
        self.core.add_evidence("g-1-c", "g-1", "Contract: fixed fee CAD 2,000.")

    def tearDown(self):
        self.core.close()
        self.temp.cleanup()

    def reply_from_customer(self, body="We dispute this: the second site visit never happened."):
        self.core.record_reply("g-1-r1", "g-1", body)


class SpendTests(Step2TestCase):
    def test_prices_follow_the_published_rates(self):
        cost = FinanceCore.model_cost_micro_cents
        self.assertEqual(cost("claude-opus-5-5", 1_000_000, 0), 400 * 1_000_000)  # USD 4.00
        self.assertEqual(cost("claude-opus-5-5", 0, 1_000_000), 2000 * 1_000_000)
        self.assertEqual(cost("claude-opus-5-5", 0, 0, cache_write=1_000_000), 500 * 1_000_000)
        self.assertEqual(cost("claude-haiku-5-5", 1_000_000, 1_000_000), 60 * 1_000_000)
        # An unknown model is priced like the most expensive one: the budget errs safe.
        self.assertEqual(cost("some-new-model", 1_000_000, 0), 400 * 1_000_000)

    def test_spend_counts_today_only_and_shows_on_the_summary(self):
        self.core.record_model_usage("collections", "claude-opus-5-5", None, 100_000, 10_000)
        self.assertEqual(self.core.spend_today_cents(), 60)  # 40 + 20 cents
        self.assertEqual(self.core.summary()["model_spend_today_cents"], 60)
        self.assertEqual(self.core.model_budget_left_cents(), 340)
        self.core.advance_simulated_time(DAY)
        self.assertEqual(self.core.spend_today_cents(), 0)


class EscalateToolTests(Step2TestCase):
    def test_the_agent_escalates_a_dispute_instead_of_asking(self):
        self.reply_from_customer()
        fake = FakeClient(
            reply(call("get_evidence")),
            reply(call("escalate_to_owner", {
                "reason": "dispute", "evidence_ids": ["g-1-r1"],
                "question": "The customer says a site visit never happened. Credit it or chase?"})),
            reply(text("Escalated to the owner."), stop="end_turn"))
        run = Investigator(self.core, fake).investigate(CASE)
        self.assertEqual((run["status"], run["action_id"]), ("escalated", None))
        [esc] = self.core.escalations(case_id=CASE)
        self.assertEqual((esc["reason"], esc["raised_by"]), ("agent_dispute", f"agent:{run['id']}"))
        self.assertTrue(esc["question"].startswith("Agent asks: "))
        self.assertEqual(self.core.case(CASE)["state"], "escalated")

    def test_escalating_needs_the_evidence_read_first_and_only_once(self):
        args = {"reason": "other", "evidence_ids": ["g-1-c"], "question": "Decide?"}
        fake = FakeClient(
            reply(call("escalate_to_owner", args, "tu_early")),
            reply(call("get_evidence"), call("escalate_to_owner", args, "tu_ok")),
            reply(call("escalate_to_owner", args, "tu_twice")),
            reply(text("done"), stop="end_turn"))
        run = Investigator(self.core, fake).investigate(CASE)
        results = {r["tool_use_id"]: r for m in fake.requests[1:] for r in m["messages"][-1]["content"]}
        self.assertTrue(results["tu_early"]["is_error"])
        self.assertNotIn("is_error", results["tu_ok"])
        self.assertTrue(results["tu_twice"]["is_error"])
        self.assertEqual(run["status"], "escalated")
        self.assertEqual(len(self.core.escalations(case_id=CASE)), 1)


class ReplanTests(Step2TestCase):
    def test_after_the_replan_limit_the_agent_escalates(self):
        for n in range(MAX_REPLANS + 1):  # each new plan supersedes the previous one
            self.core.propose(f"old-{n}", "g-1", "request_information", ["g-1-c"],
                              proposed_by=f"agent:run-{n}")
        self.assertEqual(self.core.replans("g-1"), MAX_REPLANS)
        proposal = {"kind": "request_information", "evidence_ids": ["g-1-c"],
                    "summary": "Ask again.", "findings": [], "unknowns": []}
        fake = FakeClient(reply(call("get_evidence"), call("propose_action", proposal)),
                          reply(text("done"), stop="end_turn"))
        run = Investigator(self.core, fake).investigate(CASE)
        self.assertEqual(run["status"], "escalated")
        self.assertEqual([e["reason"] for e in self.core.escalations(case_id=CASE)],
                         ["replan_limit"])


class ReviewFindingTests(Step2TestCase):
    def test_a_superseded_plan_cannot_be_approved(self):
        a1 = self.core.propose("a1", "g-1", "request_information", ["g-1-c"], proposed_by="agent:r1")
        self.core.propose("a2", "g-1", "request_information", ["g-1-c"], proposed_by="agent:r2")
        with self.assertRaises(Conflict):
            self.core.approve("a1", payload_hash=a1["payload_hash"])
        self.assertEqual(self.core.action("a1")["status"], "superseded")

    def test_without_rules_the_budget_does_not_stop_an_investigation(self):
        self.core.set_policy(60, 7, 2, autonomy_rules=[], daily_model_budget_cents=0,
                             created_by="test")
        self.reply_from_customer("Which invoice is this?")
        fake = FakeClient(reply(call("get_evidence")), reply(text("done"), stop="end_turn"))
        run = Investigator(self.core, fake, enforce_budget=True).investigate(CASE)
        self.assertEqual(run["status"], "no_proposal")
        self.assertEqual(self.core.escalations(case_id=CASE), [])

    def test_replans_reset_when_the_case_changes_and_ignore_cadence_plans(self):
        self.core.propose("o1", "g-1", "collection_reminder", ["g-1-c"], proposed_by="orchestrator")
        self.core.propose("o2", "g-1", "collection_reminder", ["g-1-c"], proposed_by="orchestrator")
        self.assertEqual(self.core.replans("g-1"), 0)  # cadence plans don't count
        self.core.propose("a1", "g-1", "request_information", ["g-1-c"], proposed_by="agent:r1")
        self.core.propose("a2", "g-1", "request_information", ["g-1-c"], proposed_by="agent:r2")
        self.assertEqual(self.core.replans("g-1"), 1)
        self.core.add_evidence("g-1-new", "g-1", "New remittance note.")
        self.assertEqual(self.core.replans("g-1"), 0)

    def test_the_replan_limit_needs_the_evidence_read_first(self):
        for n in range(MAX_REPLANS + 1):
            self.core.propose(f"old-{n}", "g-1", "request_information", ["g-1-c"],
                              proposed_by=f"agent:run-{n}")
        proposal = {"kind": "request_information", "evidence_ids": ["g-1-c", "made-up"],
                    "summary": "Ask again.", "findings": [], "unknowns": []}
        fake = FakeClient(reply(call("propose_action", proposal, "tu_early")),
                          reply(call("get_evidence"), call("propose_action", proposal)),
                          reply(text("done"), stop="end_turn"))
        Investigator(self.core, fake).investigate(CASE)
        self.assertTrue(fake.requests[1]["messages"][-1]["content"][0]["is_error"])
        [esc] = self.core.escalations(case_id=CASE)
        self.assertEqual(esc["evidence_ids"], ["g-1-c"])  # unknown id dropped


class BudgetTests(Step2TestCase):
    def test_an_automatic_run_stops_when_the_budget_runs_out(self):
        self.reply_from_customer("Which invoice is this?")
        expensive = (2_000_000, 0)  # USD 8.00 of input on Opus 5.5
        fake = FakeClient(reply(call("get_evidence"), usage=expensive))
        run = Investigator(self.core, fake, enforce_budget=True).investigate(CASE)
        self.assertEqual((run["status"], run["detail"]), ("escalated", "model_budget_exceeded"))
        self.assertEqual(len(fake.requests), 1)  # no second call
        self.assertEqual([e["reason"] for e in self.core.escalations(case_id=CASE)],
                         ["budget_exceeded"])

    def test_the_worker_runs_no_agent_once_the_budget_is_spent(self):
        self.reply_from_customer("Which invoice is this?")
        self.core.record_model_usage("collections", "claude-opus-5-5", None, 1_000_000, 0)

        def factory(_core):
            raise AssertionError("no agent may run over budget")
        result = run_once(self.core, self.inbox, factory)
        self.assertEqual(result["errors"], [])
        self.assertEqual([e["reason"] for e in self.core.escalations(case_id=CASE)],
                         ["needs_judgement"])

    def test_within_budget_the_worker_runs_the_agent(self):
        self.reply_from_customer("Please resend the invoice PDF.")
        proposal = {"kind": "request_information", "evidence_ids": ["g-1-r1"],
                    "summary": "Customer needs the PDF; confirm their AP address.",
                    "findings": [], "unknowns": []}
        fake = FakeClient(reply(call("get_evidence"), call("propose_action", proposal)),
                          reply(text("done"), stop="end_turn"))
        result = run_once(self.core, self.inbox,
                          lambda c: Investigator(c, fake, enforce_budget=True))
        self.assertEqual([i["status"] for i in result["investigations"]], ["proposed"])
        self.assertGreater(self.core.spend_today_cents(), 0)
        # By default the agent's message waits for the owner.
        self.assertEqual(self.inbox.messages(), [])
        self.assertEqual(len(self.core.attention()["approvals"]), 1)


class BriefingTests(Step2TestCase):
    PAYLOAD = {"headline": "One case needs you today.", "sections": [
        {"title": "Needs you", "items": [
            {"text": "Acme disputes a site visit.", "case_ids": [CASE, "case-made-up"]}]}]}

    def escalate(self):
        self.core.open_escalation(CASE, "dispute", "Customer disputes the work.", ["g-1-c"],
                                  "orchestrator", key="k1")

    def test_writes_one_briefing_a_day_from_code_computed_facts(self):
        self.escalate()
        fake = FakeClient(briefing_reply(self.PAYLOAD))
        brief = BriefingAgent(self.core, fake).run()
        self.assertEqual(brief["headline"], "One case needs you today.")
        self.assertEqual(brief["sections"][0]["items"][0]["case_ids"], [CASE])  # made-up id dropped
        request = fake.requests[0]
        self.assertEqual(request["model"], "claude-haiku-5-5")
        self.assertNotIn("tools", request)  # read-only: no tools at all
        facts = json.loads(request["messages"][0]["content"])
        self.assertEqual(facts["needs_owner"][0]["reason"], "dispute")
        self.assertGreater(self.core.spend_today_cents(), 0)
        self.assertIsNone(BriefingAgent(self.core, FakeClient()).run())  # not due again today
        self.assertIn("briefing_published", [i["kind"] for i in self.core.activity()["items"]])

    def test_bad_output_stores_nothing_and_is_tried_at_most_twice_a_day(self):
        for step in (briefing_reply({"headline": ""}), briefing_reply(self.PAYLOAD, stop="refusal")):
            with self.subTest(step=step.stop_reason):
                self.assertIsNone(BriefingAgent(self.core, FakeClient(step)).run())
        self.assertFalse(self.core.briefing_due())  # two attempts today: stop
        self.assertIsNone(BriefingAgent(self.core, FakeClient()).run())  # no third call
        self.core.advance_simulated_time(DAY)
        self.assertTrue(self.core.briefing_due())

    def test_the_orchestrator_respects_pause_and_budget(self):
        calls = []

        def briefing():
            calls.append(1)
            return None
        self.core.set_agent_paused("briefing", True)
        orchestrator.run_cycle(self.core, has_model=True, briefing=briefing)
        self.core.set_agent_paused("briefing", False)
        self.core.record_model_usage("collections", "claude-opus-5-5", None, 1_000_000, 0)
        orchestrator.run_cycle(self.core, has_model=True, briefing=briefing)
        self.assertEqual(calls, [])
        self.core.advance_simulated_time(DAY)  # a new day: budget available again
        orchestrator.run_cycle(self.core, has_model=True, briefing=briefing)
        self.assertEqual(calls, [1])


if __name__ == "__main__":
    unittest.main()
