"""Milestone 5 step 0: the frozen contract's routes and the schema V5 migration.

Covers what step 0 implements for real (case list filters, due dates, policy
autonomy fields, the kill switch, agents, escalations, the "Needs you" queue,
the summary, the demo clock) and checks that routes built in later steps
answer 503 "not built yet" instead of pretending. Synthetic data, no model.
Run: .venv/bin/python -m unittest test_milestone5_contract -v
"""
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api import create_app
from core import DEFAULT_AUTONOMY_RULES, DEFAULT_CADENCE_DAYS, SCHEMA_VERSION, FinanceCore

TOKEN = "test-owner-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
POLICY = {"approval_ttl_minutes": 60, "follow_up_days": 7, "max_reminders": 2}


class Milestone5TestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "m5.sqlite3")
        self.client = TestClient(create_app(db_path=self.path, owner_token=TOKEN, enable_demo=True))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=AUTH, expect=200):
        response = self.client.request(method, path, json=body, headers=headers)
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def invoice(self, invoice_id, customer, total_cents, due_at=None):
        body = {"invoice_id": invoice_id, "customer": customer, "total_cents": total_cents}
        if due_at:
            body["due_at"] = due_at
        return self.call("POST", "/demo/invoices", body, headers={})

    def core(self):
        return FinanceCore(self.path, simulated_time=True)


class OwnerRoutesTests(Milestone5TestCase):
    def test_new_owner_routes_need_the_token(self):
        for method, path in (("GET", "/attention"), ("GET", "/summary"), ("GET", "/activity"),
                             ("GET", "/autonomy"), ("POST", "/autonomy/pause"),
                             ("POST", "/autonomy/resume"), ("GET", "/agents"),
                             ("POST", "/agents/collections/pause"), ("GET", "/briefings/latest"),
                             ("POST", "/escalations/x/resolve")):
            with self.subTest(route=f"{method} {path}"):
                self.call(method, path, headers={}, expect=401)

    def test_routes_for_later_steps_say_not_built(self):
        detail = self.call("POST", "/policy/preview", POLICY, expect=503)["detail"]
        self.assertIn("Not built yet", detail)
        self.call("POST", "/demo/generator/run", {"seed": 1}, headers={}, expect=503)
        self.call("GET", "/demo/generator/truth?seed=1", headers={}, expect=503)


class CaseListTests(Milestone5TestCase):
    def setUp(self):
        super().setUp()
        self.invoice("a-1", "Acme Design (synthetic)", 100_000, due_at="2020-01-01T00:00:00+00:00")
        self.invoice("b-1", "Birch Clinic (synthetic)", 300_000)
        self.invoice("c-1", "Cedar Studio (synthetic)", 200_000, due_at="2999-01-01T00:00:00-05:00")

    def test_due_dates_are_stored_in_utc_and_drive_days_overdue(self):
        cedar = self.call("GET", "/invoices/c-1", headers={})
        self.assertEqual(cedar["due_at"], "2999-01-01T05:00:00+00:00")
        rows = {c["invoice_id"]: c for c in self.call("GET", "/cases")["cases"]}
        self.assertGreater(rows["a-1"]["days_overdue"], 365)
        self.assertIsNone(rows["b-1"]["days_overdue"])  # no due date
        self.assertIsNone(rows["c-1"]["days_overdue"])  # not due yet
        self.assertEqual(rows["a-1"]["handled_by"], "none")
        self.assertFalse(rows["a-1"]["needs_owner"])

    def test_bad_due_dates_are_refused(self):
        for value in ("tomorrow", "2026-12-01T00:00:00"):  # not a date; no time zone
            with self.subTest(value=value):
                self.call("POST", "/demo/invoices", {"invoice_id": "d-1", "customer": "x",
                                                     "total_cents": 1, "due_at": value},
                          headers={}, expect=422)

    def test_filter_search_sort_and_pages(self):
        page = self.call("GET", "/cases?sort=outstanding&limit=2")
        self.assertEqual([c["invoice_id"] for c in page["cases"]], ["b-1", "c-1"])
        self.assertEqual((page["total"], page["next_cursor"]), (3, "2"))
        rest = self.call("GET", f"/cases?sort=outstanding&limit=2&cursor={page['next_cursor']}")
        self.assertEqual(([c["invoice_id"] for c in rest["cases"]], rest["next_cursor"]),
                         (["a-1"], None))
        self.assertEqual([c["invoice_id"] for c in self.call("GET", "/cases?q=birch")["cases"]],
                         ["b-1"])
        self.assertEqual(self.call("GET", "/cases?sort=overdue")["cases"][0]["invoice_id"], "a-1")
        self.assertEqual(self.call("GET", "/cases?state=resolved")["total"], 0)
        self.call("GET", "/cases?sort=nonsense", expect=422)
        self.call("GET", "/cases?cursor=abc", expect=422)


class PolicyTests(Milestone5TestCase):
    def test_autonomy_fields_default_to_nothing_automatic(self):
        policy = self.call("POST", "/policy", POLICY)
        self.assertEqual((policy["autonomy_rules"], policy["cadence_days"],
                          policy["daily_model_budget_cents"]), ([], [], 0))

    def test_rules_are_stored_and_omitted_fields_keep_their_values(self):
        saved = self.call("POST", "/policy", {**POLICY, "autonomy_rules": DEFAULT_AUTONOMY_RULES,
                                              "cadence_days": DEFAULT_CADENCE_DAYS,
                                              "daily_model_budget_cents": 500})
        self.assertEqual(saved["autonomy_rules"], DEFAULT_AUTONOMY_RULES)
        self.assertEqual(saved["autonomy_rules"][0]["max_amount_cents"], 500_000)  # CAD 5,000
        again = self.call("POST", "/policy", POLICY)  # same values: same version
        self.assertEqual(again["version"], saved["version"])
        changed = self.call("POST", "/policy", {**POLICY, "max_reminders": 3})
        self.assertEqual(changed["version"], saved["version"] + 1)
        self.assertEqual(changed["autonomy_rules"], DEFAULT_AUTONOMY_RULES)
        self.assertEqual(changed["cadence_days"], DEFAULT_CADENCE_DAYS)
        kinds = [i["kind"] for i in self.call("GET", "/activity")["items"]]
        self.assertEqual(kinds, ["policy_changed", "policy_changed"])

    def test_bad_rules_are_refused(self):
        rule = DEFAULT_AUTONOMY_RULES[0]
        for bad in ({"autonomy_rules": [rule, rule]},  # duplicate id
                    {"autonomy_rules": [{**rule, "id": "Has Spaces"}]},
                    {"autonomy_rules": [{**rule, "mode": "always"}]},
                    {"cadence_days": [7, 3]},
                    {"daily_model_budget_cents": -1}):
            with self.subTest(bad=bad):
                self.call("POST", "/policy", {**POLICY, **bad}, expect=422)


class ControlTests(Milestone5TestCase):
    def test_kill_switch_records_activity_once(self):
        self.assertFalse(self.call("GET", "/autonomy")["paused"])
        paused = self.call("POST", "/autonomy/pause", {"reason": "checking a complaint"})
        self.assertEqual((paused["paused"], paused["changed_by"], paused["reason"]),
                         (True, "owner", "checking a complaint"))
        self.call("POST", "/autonomy/pause")  # already paused: no second item
        self.assertTrue(self.call("GET", "/summary")["autonomy_paused"])
        self.assertFalse(self.call("POST", "/autonomy/resume")["paused"])
        feed = self.call("GET", "/activity")
        self.assertEqual([i["kind"] for i in feed["items"]], ["autonomy_paused", "autonomy_resumed"])
        self.assertTrue(all(i["synthetic"] for i in feed["items"]))
        self.assertEqual(self.call("GET", f"/activity?after={feed['cursor']}"),
                         {"items": [], "cursor": feed["cursor"]})

    def test_agents_can_be_paused(self):
        agents = {a["name"]: a for a in self.call("GET", "/agents")["agents"]}
        self.assertEqual(set(agents), {"collections", "briefing"})
        self.assertTrue(agents["collections"]["can_write"])
        self.assertFalse(agents["briefing"]["can_write"])
        self.assertTrue(self.call("POST", "/agents/briefing/pause")["paused"])
        self.assertFalse(self.call("POST", "/agents/briefing/resume")["paused"])
        self.call("POST", "/agents/payroll/pause", expect=404)

    def test_no_briefing_yet_is_404(self):
        self.call("GET", "/briefings/latest", expect=404)


class AttentionTests(Milestone5TestCase):
    def setUp(self):
        super().setUp()
        self.invoice("inv-1", "Acme Design (synthetic)", 100_000)
        self.call("POST", "/demo/evidence", {"evidence_id": "e-1", "invoice_id": "inv-1",
                                             "body": "Contract: CAD 1,000."}, headers={})

    def escalate(self, escalation_id="esc-1"):
        core = self.core()
        try:
            with core.transaction():
                core.db.execute(
                    "INSERT INTO escalations(id, case_id, reason, question, evidence_ids, "
                    "raised_by, created_at) VALUES(?,?,?,?,?,?,?)",
                    (escalation_id, "case-inv-1", "dispute", "Customer disputes the work. Next?",
                     json.dumps(["e-1"]), "orchestrator", core.clock()))
        finally:
            core.close()

    def test_needs_you_lists_pending_actions_and_open_escalations(self):
        self.call("POST", "/policy", POLICY)
        self.call("POST", "/cases/case-inv-1/actions",
                  {"action_id": "a-1", "kind": "request_information", "evidence_ids": ["e-1"]})
        self.escalate()
        queue = self.call("GET", "/attention")
        self.assertEqual([a["action"]["id"] for a in queue["approvals"]], ["a-1"])
        self.assertEqual(queue["approvals"][0]["outstanding_cents"], 100_000)
        self.assertIsNone(queue["approvals"][0]["action"]["autonomy"])
        self.assertEqual([e["escalation"]["id"] for e in queue["escalations"]], ["esc-1"])
        self.assertEqual(self.call("GET", "/summary")["awaiting_owner"], 2)
        row = self.call("GET", "/cases")["cases"][0]
        self.assertEqual((row["open_escalations"], row["needs_owner"]), (1, True))
        ws = self.call("GET", "/cases/case-inv-1/workspace")
        self.assertEqual([e["id"] for e in ws["escalations"]], ["esc-1"])

    def test_resolving_an_escalation_once(self):
        self.escalate()
        done = self.call("POST", "/escalations/esc-1/resolve", {"note": "Asked for photos"})
        self.assertEqual((done["resolved_by"], done["resolution_note"]), ("owner", "Asked for photos"))
        self.call("POST", "/escalations/esc-1/resolve", expect=409)
        self.call("POST", "/escalations/nope/resolve", expect=404)
        self.assertEqual(self.call("GET", "/attention")["escalations"], [])

    def test_summary_figures(self):
        self.call("POST", "/demo/settlements", {"settlement_id": "s-1", "invoice_id": "inv-1",
                                                "amount_cents": 40_000}, headers={})
        summary = self.call("GET", "/summary")
        self.assertEqual((summary["outstanding_cents"], summary["open_cases"],
                          summary["resolved_7d"], summary["model_spend_today_cents"]),
                         (60_000, 1, 0, None))
        self.assertTrue(summary["simulated_time"])


class DemoClockTests(Milestone5TestCase):
    def test_play_pause_and_speed(self):
        clock = self.call("GET", "/demo/clock", headers={})
        self.assertEqual((clock["playing"], clock["speed"], clock["simulated"]), (False, 60, True))
        self.assertTrue(self.call("POST", "/demo/clock", {"action": "play"}, headers={})["playing"])
        self.assertEqual(self.call("POST", "/demo/clock", {"action": "speed", "speed": 1440},
                                   headers={})["speed"], 1440)
        self.assertFalse(self.call("POST", "/demo/clock", {"action": "pause"}, headers={})["playing"])
        self.call("POST", "/demo/clock", {"action": "speed"}, headers={}, expect=422)


class MigrationV5Tests(unittest.TestCase):
    def test_milestone_4_database_upgrades_with_data_intact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "m4.sqlite3")
            core = FinanceCore(path)
            core.create_invoice("i", "c", 500)
            core.set_policy(60, 7, 2)
            core.add_evidence("e", "i", "terms")
            core.propose("a", "i", "collection_reminder", ["e"])
            core.db.execute(
                "INSERT INTO jobs VALUES('j','i','settlement_check',NULL,'2026-01-01T00:00:00+00:00',"
                "'pending',0,NULL,'2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
            core.db.commit()
            # Rewind to a Milestone 4 (v4) database: drop every v5 object and
            # restore the v4 jobs table with its narrower CHECK.
            with core.transaction():
                for sql in (
                    *(f"DROP TABLE {t}" for t in ("events", "activity", "escalations",
                                                   "autonomy_state", "agent_controls", "briefings",
                                                   "sim_playback", "generator_runs")),
                    "ALTER TABLE invoices DROP COLUMN due_at",
                    "ALTER TABLE actions DROP COLUMN autonomy",
                    "ALTER TABLE actions DROP COLUMN superseded_by",
                    "ALTER TABLE policies DROP COLUMN autonomy_rules",
                    "ALTER TABLE policies DROP COLUMN cadence_days",
                    "ALTER TABLE policies DROP COLUMN daily_model_budget_cents",
                    "ALTER TABLE agent_runs DROP COLUMN agent",
                    "ALTER TABLE jobs RENAME TO jobs_old",
                    """CREATE TABLE jobs (
                        id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL REFERENCES invoices(id),
                        kind TEXT NOT NULL CHECK(kind IN ('settlement_check', 'investigate')),
                        ref TEXT, due_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed')),
                        attempts INTEGER NOT NULL DEFAULT 0, detail TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                    "INSERT INTO jobs SELECT * FROM jobs_old",
                    "DROP TABLE jobs_old",
                    "UPDATE schema_meta SET version=4",
                ):
                    core.db.execute(sql)
            core.close()
            core = FinanceCore(path)
            try:
                self.assertEqual(core._schema_version(), SCHEMA_VERSION)
                self.assertEqual(core.invoice("i")["due_at"], None)
                policy = core.policy()
                self.assertEqual((policy["version"], policy["autonomy_rules"],
                                  policy["daily_model_budget_cents"]), (1, [], 0))
                self.assertIsNone(core.action_detail("a")["autonomy"])
                self.assertEqual(core.job("j")["kind"], "settlement_check")
                self.assertFalse(core.autonomy_status()["paused"])
                with core.transaction():  # the new job kinds are accepted
                    core.db.execute(
                        "INSERT INTO jobs VALUES('k','i','cadence_step',NULL,'x','pending',0,NULL,'x','x')")
            finally:
                core.close()


if __name__ == "__main__":
    unittest.main()
