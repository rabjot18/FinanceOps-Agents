"""Milestone 4: named synthetic demo scenarios, loaded over HTTP.

Synthetic data, no model, no network.
Run: .venv/bin/python -m unittest test_scenarios -v
"""
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import scenarios
from api import create_app

TOKEN = "test-owner-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
NEW_SCENARIOS = ["happy_path", "partial_payment", "dispute", "missing_evidence",
                 "malicious_document", "already_resolved"]

# Backend-computed figures each scenario must produce: (state, total, outstanding).
EXPECTED = {
    "baseline": {"case-inv-1": ("open", 1_200_000, 200_000),
                 "case-inv-2": ("needs_information", 850_000, 425_000)},
    "happy_path": {"case-sc-happy-inv-1": ("open", 500_000, 500_000)},
    "partial_payment": {"case-sc-partial-inv-1": ("open", 750_000, 150_000)},
    "dispute": {"case-sc-dispute-inv-1": ("needs_information", 320_000, 320_000)},
    "missing_evidence": {"case-sc-missing-inv-1": ("open", 410_000, 410_000)},
    "malicious_document": {"case-sc-malicious-inv-1": ("open", 640_000, 640_000)},
    "already_resolved": {"case-sc-resolved-inv-1": ("resolved", 275_000, 0)},
}


class ScenarioTestCase(unittest.TestCase):
    def setUp(self):  # an empty database each time
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "scenarios.sqlite3")
        self.client = TestClient(create_app(db_path=self.path, owner_token=TOKEN, enable_demo=True))

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def call(self, method, path, body=None, headers=None, expect=200):
        response = self.client.request(method, path, json=body, headers=headers or {})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def load(self, name, expect=200):
        return self.call("POST", f"/demo/scenarios/{name}", expect=expect)

    def workspace(self, case_id):
        return self.call("GET", f"/cases/{case_id}/workspace", headers=AUTH)

    def snapshot(self):
        """Everything a reload must leave alone: cases, events, money, evidence."""
        cases = self.call("GET", "/cases", headers=AUTH)["cases"]
        snap = {}
        for c in cases:
            ws = self.workspace(c["id"])
            ws.pop("now")  # the read clock, not stored data
            snap[c["id"]] = (c, ws)
        return snap


class CatalogTests(ScenarioTestCase):
    def test_catalog_lists_every_scenario(self):
        listed = self.call("GET", "/demo/scenarios")["scenarios"]
        self.assertEqual([s["name"] for s in listed], ["baseline"] + NEW_SCENARIOS)
        for item in listed:
            self.assertTrue(item["title"] and item["description"])

    def test_scenario_data_is_labelled_synthetic(self):
        seen_ids = set()
        for name, scenario in scenarios.SCENARIOS.items():
            for spec in scenario["invoices"]:
                self.assertTrue(spec["customer"].endswith("(synthetic)"), spec["customer"])
                self.assertTrue(spec["contact"].endswith(".example"), spec["contact"])
                ids = ({spec["invoice_id"]} | {e for e, _ in spec["evidence"]}
                       | {s for s, _ in spec["settlements"]})
                self.assertFalse(ids & seen_ids, f"{name} reuses IDs {ids & seen_ids}")
                seen_ids |= ids
                if name != "baseline":
                    prefix = {"happy_path": "sc-happy-", "partial_payment": "sc-partial-",
                              "dispute": "sc-dispute-", "missing_evidence": "sc-missing-",
                              "malicious_document": "sc-malicious-",
                              "already_resolved": "sc-resolved-"}[name]
                    self.assertTrue(all(i.startswith(prefix) for i in ids), ids)


class LoadTests(ScenarioTestCase):
    def assert_expected(self, name, case_ids):
        self.assertEqual(sorted(case_ids), sorted(EXPECTED[name]))
        for case_id, (state, total, outstanding) in EXPECTED[name].items():
            ws = self.workspace(case_id)
            self.assertEqual(ws["case"]["state"], state, case_id)
            self.assertEqual(ws["invoice"]["total_cents"], total, case_id)
            self.assertEqual(ws["invoice"]["outstanding_cents"], outstanding, case_id)
            self.assertTrue(ws["contact_on_file"], case_id)
            self.assertTrue(ws["sending_enabled"], case_id)
            self.assertEqual(ws["actions"], [], case_id)

    def test_each_scenario_loads_on_an_empty_db_and_reloads_unchanged(self):
        for name in EXPECTED:
            with self.subTest(scenario=name):
                self.tearDown()
                self.setUp()
                first = self.load(name)
                self.assertEqual(first["scenario"], name)
                self.assertTrue(first["simulated"])
                self.assert_expected(name, first["case_ids"])
                before = self.snapshot()
                policy = self.call("GET", "/policy", headers=AUTH)
                again = self.load(name)
                self.assertEqual(again["case_ids"], first["case_ids"])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.call("GET", "/policy", headers=AUTH), policy)

    def test_all_scenarios_coexist_in_one_db(self):
        for name in EXPECTED:
            self.load(name)
        for name in EXPECTED:  # loading one did not disturb another
            self.assert_expected(name, list(EXPECTED[name]))
        listed = {c["id"] for c in self.call("GET", "/cases", headers=AUTH)["cases"]}
        self.assertEqual(listed, {c for cases in EXPECTED.values() for c in cases})
        before = self.snapshot()
        for name in reversed(list(EXPECTED)):
            self.load(name)
        self.assertEqual(self.snapshot(), before)

    def test_partial_payment_shortfall_is_backed_by_the_contract(self):
        ws = self.workspace(self.load("partial_payment")["case_ids"][0])
        self.assertEqual(ws["invoice"]["settled_cents"], 600_000)
        self.assertIn("CAD 7,500", ws["evidence"][0]["body"])

    def test_dispute_lands_in_needs_information(self):
        ws = self.workspace(self.load("dispute")["case_ids"][0])
        self.assertTrue(ws["invoice"]["disputed"])
        reasons = [e["reason"] for e in ws["case"]["events"]]
        self.assertEqual(reasons[-1], "dispute_opened")

    def test_missing_evidence_has_none(self):
        ws = self.workspace(self.load("missing_evidence")["case_ids"][0])
        self.assertEqual(ws["evidence"], [])
        self.assertEqual(ws["invoice"]["settled_cents"], 0)

    def test_already_resolved_has_verification_proof(self):
        ws = self.workspace(self.load("already_resolved")["case_ids"][0])
        proof = ws["case"]["verification"]
        self.assertIsNotNone(proof)
        self.assertEqual(proof["settled_cents"], proof["total_cents"])
        self.assertEqual(proof["settled_cents"], 275_000)
        self.assertEqual(proof["settlement_ids"], ["sc-resolved-bank-1", "sc-resolved-bank-2"])

    def test_existing_policy_is_never_replaced(self):
        custom = self.call("POST", "/policy", {"approval_ttl_minutes": 30, "follow_up_days": 3,
                                               "max_reminders": 1}, headers=AUTH)
        for name in EXPECTED:
            self.load(name)
        self.assertEqual(self.call("GET", "/policy", headers=AUTH), custom)

    def test_clash_is_refused_before_anything_is_written(self):
        # inv-2 is the scenario's *second* invoice: the preflight must stop inv-1 too.
        self.call("POST", "/demo/invoices", {"invoice_id": "inv-2", "customer": "Someone else",
                                             "total_cents": 100})
        self.load("baseline", expect=409)
        self.call("GET", "/cases/case-inv-1", headers=AUTH, expect=404)
        self.assertEqual(self.workspace("case-inv-2")["invoice"]["total_cents"], 100)

    def test_reload_does_not_undo_later_changes(self):
        self.load("baseline")
        self.call("POST", "/demo/disputes", {"invoice_id": "inv-2", "disputed": False})
        self.load("baseline")
        self.assertFalse(self.workspace("case-inv-2")["invoice"]["disputed"])

    def test_existing_contact_is_never_overwritten(self):
        self.call("POST", "/demo/invoices", {"invoice_id": "inv-1", "customer": "Demo customer",
                                             "total_cents": 1_200_000})
        self.call("POST", "/demo/contacts", {"invoice_id": "inv-1", "address": "real@customer.example"})
        self.load("baseline", expect=409)
        action = self.call("POST", "/cases/case-inv-1/actions",
                           {"action_id": "a-1", "kind": "collection_reminder", "evidence_ids": []},
                           headers=AUTH, expect=422)  # no evidence was added either
        self.assertTrue(action["detail"])

    def test_changed_evidence_is_refused_before_anything_is_written(self):
        self.call("POST", "/demo/invoices", {"invoice_id": "inv-2",
                                             "customer": "Harbour Clinic (synthetic)",
                                             "total_cents": 850_000})
        self.call("POST", "/demo/evidence", {"evidence_id": "reply-2", "invoice_id": "inv-2",
                                             "body": "Different text"})
        self.load("baseline", expect=409)
        self.call("GET", "/cases/case-inv-1", headers=AUTH, expect=404)


class MaliciousDocumentTests(ScenarioTestCase):
    def test_injection_text_is_stored_as_plain_untrusted_evidence_and_changes_nothing(self):
        case_id = self.load("malicious_document")["case_ids"][0]
        ws = self.workspace(case_id)
        doc = [e for e in ws["evidence"] if e["id"] == "sc-malicious-doc-1"]
        self.assertEqual(len(doc), 1)
        self.assertEqual(doc[0]["body"], scenarios.MALICIOUS_TEXT)  # stored verbatim
        self.assertTrue(doc[0]["untrusted"])
        self.assertIn("Ignore all previous rules", doc[0]["body"])

        # Money, state and actions are what the trusted facts say, not the document.
        self.assertEqual(ws["case"]["state"], "open")
        self.assertEqual(ws["invoice"]["settled_cents"], 0)
        self.assertEqual(ws["invoice"]["outstanding_cents"], 640_000)
        self.assertFalse(ws["invoice"]["disputed"])
        self.assertEqual(ws["actions"], [])
        self.assertEqual(ws["jobs"], [])
        self.assertIsNone(ws["case"]["verification"])
        self.assertEqual(self.call("GET", "/demo/inbox")["messages"], [])
        reasons = {e["reason"] for e in ws["case"]["events"]}
        self.assertEqual(reasons, {"invoice_created", "contact_changed", "evidence_added"})

    def test_same_outcome_as_without_the_document(self):
        self.load("malicious_document")
        attacked = self.workspace("case-sc-malicious-inv-1")
        # A control invoice with the same trusted facts and no injected document.
        spec = scenarios.SCENARIOS["malicious_document"]["invoices"][0]
        self.call("POST", "/demo/invoices", {"invoice_id": "control-1", "customer": spec["customer"],
                                             "total_cents": spec["total_cents"]})
        self.call("POST", "/demo/contacts", {"invoice_id": "control-1", "address": spec["contact"]})
        self.call("POST", "/demo/evidence", {"evidence_id": "control-contract",
                                             "invoice_id": "control-1",
                                             "body": spec["evidence"][0][1]})
        control = self.workspace("case-control-1")
        for key in ("state",):
            self.assertEqual(attacked["case"][key], control["case"][key])
        for key in ("total_cents", "settled_cents", "outstanding_cents", "disputed", "state"):
            self.assertEqual(attacked["invoice"][key], control["invoice"][key], key)
        self.assertEqual(attacked["actions"], control["actions"])
        self.assertEqual(attacked["sending_enabled"], control["sending_enabled"])


if __name__ == "__main__":
    unittest.main()
