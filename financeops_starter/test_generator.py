"""Milestone 5 step 1b: the synthetic business generator and its demo routes.

Synthetic data only; no network, no model.
Run: .venv/bin/python -m unittest test_generator -v
"""
import re
import tempfile
import typing
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import generator
import schemas
from api import ID_PATTERN, create_app
from core import EVENT_SPECS, Conflict, FinanceCore, InvalidInput, NotFound

SMALL = dict(months=3, customers=8)


class GenerateTests(unittest.TestCase):
    def test_same_seed_same_business(self):
        self.assertEqual(generator.generate(42), generator.generate(42))
        start = "2030-03-01T12:00:00+00:00"
        self.assertEqual(generator.generate(7, start=start), generator.generate(7, start=start))

    def test_different_seed_differs(self):
        a, b = generator.generate(1, **SMALL), generator.generate(2, **SMALL)
        self.assertNotEqual(a["events"], b["events"])
        self.assertNotEqual(a["truth"], b["truth"])

    def test_start_only_shifts_time(self):
        a = generator.generate(5, **SMALL)
        b = generator.generate(5, start="2031-06-01T00:00:00+00:00", **SMALL)
        self.assertEqual([e["ref"] for e in a["events"]], [e["ref"] for e in b["events"]])
        self.assertEqual(a["truth"], b["truth"])
        self.assertGreaterEqual(b["first_event_at"], "2031-06-01T00:00:00+00:00")

    def test_every_problem_kind_appears_across_a_few_seeds(self):
        seen = {t["problem"] for seed in range(1, 6) for t in generator.generate(seed)["truth"]}
        self.assertEqual(seen, set(typing.get_args(schemas.ProblemKind)))

    def test_problems_are_planted_at_a_realistic_rate(self):
        truth = generator.generate(42)["truth"]
        planted = sum(t["problem"] != "none" for t in truth)
        self.assertTrue(0.15 * len(truth) <= planted <= 0.5 * len(truth), planted)

    def test_truth_items_match_the_frozen_schema(self):
        business = generator.generate(3)
        invoice_ids = {e["payload"]["invoice_id"] for e in business["events"]
                       if e["kind"] == "invoice_created"}
        self.assertEqual(len(business["truth"]), business["invoices"])
        self.assertEqual({t["invoice_id"] for t in business["truth"]}, invoice_ids)
        for item in business["truth"]:
            schemas.TruthItem.model_validate(item)
            self.assertTrue(item["expected"])

    def test_events_use_exactly_the_shared_format(self):
        business = generator.generate(11)
        times = [e["at"] for e in business["events"]]
        self.assertEqual(times, sorted(times))
        refs = [e["ref"] for e in business["events"]]
        self.assertEqual(len(refs), len(set(refs)))
        for event in business["events"]:
            spec = EVENT_SPECS[event["kind"]]
            self.assertEqual(set(event["payload"]), set(spec), event)
            for field, value in event["payload"].items():
                self.assertIsInstance(value, spec[field])
                if field.endswith("_id"):
                    self.assertRegex(value, ID_PATTERN)
                if field.endswith("_cents"):
                    self.assertIs(type(value), int)
                    self.assertGreater(value, 0)
            if event["kind"] == "settlement_received":
                self.assertEqual(event["payload"]["currency"], "CAD")

    def test_names_are_labelled_synthetic_and_addresses_reserved(self):
        for event in generator.generate(9)["events"]:
            payload = event["payload"]
            if event["kind"] == "invoice_created":
                self.assertTrue(payload["customer"].endswith("(synthetic)"))
            if event["kind"] == "contact_set":
                self.assertTrue(payload["address"].endswith(".example.com"))

    def test_each_invoice_story_starts_with_its_creation(self):
        first = {}
        for event in generator.generate(13)["events"]:
            first.setdefault(event["payload"]["invoice_id"], event["kind"])
        self.assertEqual(set(first.values()), {"invoice_created"})

    def test_wording_varies(self):
        bodies = [e["payload"]["body"] for seed in range(1, 6)
                  for e in generator.generate(seed)["events"] if e["kind"] == "reply_received"]
        openings = {re.sub(r"INV-\S+", "", b)[:25] for b in bodies}
        self.assertGreater(len(openings), 8)

    def test_bad_sizes_are_refused(self):
        for kwargs in ({"months": 0}, {"months": 13}, {"customers": 0}, {"customers": 201},
                       {"start": "2026-01-01T00:00:00"}):
            with self.subTest(**kwargs), self.assertRaises(InvalidInput):
                generator.generate(1, **kwargs)
        with self.assertRaises(InvalidInput):
            generator.generate(-1)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "gen.sqlite3")
        self.core = FinanceCore(self.path, simulated_time=True)

    def tearDown(self):
        self.core.close()
        self.temp.cleanup()

    def count(self, table):
        return self.core.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_every_event_passes_schedule_event_validation(self):
        out = generator.load(self.core, 21, **SMALL)
        schemas.GeneratorRunOut.model_validate(out)
        self.assertEqual(self.count("events"), out["events_scheduled"])
        rows = self.core.db.execute("SELECT source, synthetic, source_ref FROM events").fetchall()
        self.assertTrue(all(r[0] == "generator" and r[1] == 1 and r[2].startswith("21:")
                            for r in rows))

    def test_timeline_starts_at_the_core_clock_and_is_not_applied(self):
        before = self.core.clock()
        out = generator.load(self.core, 22, **SMALL)
        run = generator.stored_run(self.core, 22)
        self.assertEqual(run["created_at"][:16], before[:16])
        self.assertGreaterEqual(out["first_event_at"], run["created_at"])
        self.assertEqual(self.count("invoices"), 0)  # the orchestrator applies events
        self.assertEqual(self.count("events WHERE routed_at IS NOT NULL"), 0)

    def test_loading_twice_adds_nothing(self):
        first = generator.load(self.core, 23, **SMALL)
        events, policies = self.count("events"), self.count("policies")
        self.core.advance_simulated_time(5 * 24 * 3600)  # a later clock changes nothing
        self.assertEqual(generator.load(self.core, 23, **SMALL), first)
        self.assertEqual((self.count("events"), self.count("policies"),
                          self.count("generator_runs")), (events, policies, 1))

    def test_same_seed_different_size_is_a_conflict(self):
        generator.load(self.core, 24, **SMALL)
        with self.assertRaises(Conflict):
            generator.load(self.core, 24, months=4, customers=8)
        with self.assertRaises(Conflict):
            generator.load(self.core, 24, months=3, customers=9)

    def test_two_seeds_coexist(self):
        a = generator.load(self.core, 25, **SMALL)
        b = generator.load(self.core, 26, **SMALL)
        self.assertEqual(self.count("events"), a["events_scheduled"] + b["events_scheduled"])

    def test_policy_written_only_when_missing(self):
        generator.load(self.core, 27, **SMALL)
        policy = self.core.policy()
        self.assertEqual((policy["version"], policy["created_by"]), (1, "generator"))
        self.assertTrue(policy["autonomy_rules"])
        self.assertEqual(policy["daily_model_budget_cents"], 400)  # the owner's USD 4.00 a day
        generator.load(self.core, 28, **SMALL)
        self.assertEqual(self.core.policy()["version"], 1)

    def test_existing_policy_is_kept(self):
        self.core.set_policy(30, 3, 1)
        generator.load(self.core, 29, **SMALL)
        policy = self.core.policy()
        self.assertEqual((policy["version"], policy["created_by"]), (1, "owner"))

    def test_truth_is_stored(self):
        generator.load(self.core, 30, **SMALL)
        stored = generator.truth(self.core, 30)
        self.assertEqual(stored, {"seed": 30, "items": generator.generate(30, **SMALL)["truth"]})
        schemas.TruthOut.model_validate(stored)
        with self.assertRaises(NotFound):
            generator.truth(self.core, 31)


class GeneratorRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "routes.sqlite3")
        self.client = TestClient(create_app(db_path=self.path, owner_token="t", enable_demo=True))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_run_then_truth_over_http(self):
        body = {"seed": 42, "months": 2, "customers": 6}
        run = self.client.post("/demo/generator/run", json=body)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["seed"], 42)
        self.assertTrue(run.json()["simulated"])
        self.assertGreater(run.json()["events_scheduled"], 0)
        again = self.client.post("/demo/generator/run", json=body)
        self.assertEqual(again.json(), run.json())
        truth = self.client.get("/demo/generator/truth?seed=42")
        self.assertEqual(truth.status_code, 200, truth.text)
        self.assertEqual(len(truth.json()["items"]), run.json()["invoices"])
        core = FinanceCore(self.path)
        try:
            self.assertEqual(core.db.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                             run.json()["events_scheduled"])
        finally:
            core.close()

    def test_errors(self):
        self.assertEqual(self.client.get("/demo/generator/truth?seed=5").status_code, 404)
        self.client.post("/demo/generator/run", json={"seed": 5, "months": 1, "customers": 2})
        clash = self.client.post("/demo/generator/run",
                                 json={"seed": 5, "months": 2, "customers": 2})
        self.assertEqual(clash.status_code, 409)
        self.assertEqual(self.client.post("/demo/generator/run",
                                          json={"seed": 5, "months": 13}).status_code, 422)

    def test_routes_are_demo_only(self):
        off = TestClient(create_app(db_path=self.path, owner_token="t", enable_demo=False))
        try:
            self.assertEqual(off.post("/demo/generator/run", json={"seed": 1}).status_code, 404)
        finally:
            off.close()


if __name__ == "__main__":
    unittest.main()
