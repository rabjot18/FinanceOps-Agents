"""Seed the PRD demo story into the local database. Synthetic data only.

Run: python3 seed.py   (uses FINANCEOPS_DB, default financeops.sqlite3)

Creates a CAD 12,000 invoice with its contract evidence and a CAD 10,000
settlement, leaving CAD 2,000 outstanding. Safe to run repeatedly.
"""
import os

from core import FinanceCore


def main():
    path = os.environ.get("FINANCEOPS_DB", "financeops.sqlite3")
    app = FinanceCore(path)
    try:
        app.create_invoice("inv-1", "Demo customer (synthetic)", 1_200_000)
        app.add_evidence("contract-1", "inv-1", "CAD 12,000 due; no discount agreed.")
        app.record_settlement("bank-1", "inv-1", 1_000_000)
        invoice = app.invoice("inv-1")
        case = app.case_for_invoice("inv-1")
        print(f"Seeded {path}")
        print(f"  invoice inv-1: outstanding CAD {invoice['outstanding_cents'] / 100:,.2f}")
        print(f"  case {case['id']}: state={case['state']}, events={len(case['events'])}")
    finally:
        app.close()


if __name__ == "__main__":
    main()
