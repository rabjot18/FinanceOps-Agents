"""The HTTP contract is frozen (Milestone 4): the API must match contracts/openapi.json.

If this fails after an intended API change, the contract owner re-exports with
`.venv/bin/python export_contract.py` and regenerates the frontend types.
"""
import json
import unittest

from export_contract import CONTRACT, current


class ContractTests(unittest.TestCase):
    def test_api_matches_the_frozen_contract(self):
        self.assertTrue(CONTRACT.exists(), "run export_contract.py once to create the snapshot")
        frozen, live = json.loads(CONTRACT.read_text()), json.loads(current())
        drifted = sorted(set(frozen["paths"]) ^ set(live["paths"]))
        self.assertEqual(drifted, [], "routes added or removed")
        self.assertEqual(frozen, live, "route or schema shapes changed")

    def test_every_route_declares_its_response_shape(self):
        paths = json.loads(CONTRACT.read_text())["paths"]
        for path, methods in paths.items():
            for method, spec in methods.items():
                schema = spec["responses"]["200"]["content"]["application/json"]["schema"]
                with self.subTest(route=f"{method.upper()} {path}"):
                    self.assertTrue(schema, "untyped response: the frontend would guess")

    def test_every_route_declares_its_error_shape(self):
        spec = json.loads(CONTRACT.read_text())
        error = {"$ref": "#/components/schemas/ErrorOut"}
        for path, methods in spec["paths"].items():
            for method, op in methods.items():
                for status in ("404", "409", "422", "503"):
                    with self.subTest(route=f"{method.upper()} {path}", status=status):
                        self.assertEqual(op["responses"][status]["content"]["application/json"]
                                         ["schema"], error)

    def test_owner_routes_declare_401(self):
        paths = json.loads(CONTRACT.read_text())["paths"]
        for method, path in (("get", "/cases"), ("get", "/cases/{case_id}/workspace"),
                             ("post", "/actions/{action_id}/approve"),
                             ("post", "/cases/{case_id}/investigate")):
            with self.subTest(route=f"{method.upper()} {path}"):
                self.assertIn("401", paths[path][method]["responses"])
        self.assertNotIn("401", paths["/invoices/{invoice_id}"]["get"]["responses"])

    def test_invoices_carry_cents_and_currency(self):
        invoice = json.loads(CONTRACT.read_text())["components"]["schemas"]["InvoiceOut"]
        self.assertIn("total_cents", invoice["properties"])
        self.assertIn("currency", invoice["properties"])
        self.assertNotIn("total", invoice["properties"])


if __name__ == "__main__":
    unittest.main()
