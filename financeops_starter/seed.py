"""Seed the PRD demo story into the local database. Synthetic data only.

Run: python3 seed.py   (uses FINANCEOPS_DB, default financeops.sqlite3)

Case 1: a CAD 12,000 invoice with its contract and a CAD 10,000 settlement,
leaving CAD 2,000 outstanding. Case 2: a CAD 8,500 invoice half paid, where
the customer disputes delivery. Safe to run repeatedly.
"""
import os

import scenarios
from core import FinanceCore


def main():
    path = os.environ.get("FINANCEOPS_DB", "financeops.sqlite3")
    app = FinanceCore(path)
    try:
        # Policy v1 (approvals last 60 minutes, check for payment 7 days after a
        # reminder, at most 2 reminders) unless one exists, then both cases.
        scenarios.load(app, "baseline")

        print(f"Seeded {path}")
        for invoice_id in ("inv-1", "inv-2"):
            invoice = app.invoice(invoice_id)
            case = app.case_for_invoice(invoice_id)
            print(f"  {case['id']}: outstanding CAD {invoice['outstanding_cents'] / 100:,.2f}, "
                  f"state={case['state']}")
    finally:
        app.close()


if __name__ == "__main__":
    main()
