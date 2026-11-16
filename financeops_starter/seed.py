"""Seed the PRD demo story into the local database. Synthetic data only.

Run: python3 seed.py   (uses FINANCEOPS_DB, default financeops.sqlite3)

Case 1: a CAD 12,000 invoice with its contract and a CAD 10,000 settlement,
leaving CAD 2,000 outstanding. Case 2: a CAD 8,500 invoice half paid, where
the customer disputes delivery. Safe to run repeatedly.
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

        # Scenario 2: same kind of shortfall, but the customer disputes delivery.
        app.create_invoice("inv-2", "Harbour Clinic (synthetic)", 850_000)
        app.add_evidence("contract-2", "inv-2", "Agreement: two on-site visits, CAD 4,250 each.")
        app.add_evidence("reply-2", "inv-2", "Customer email: the second site visit never happened; "
                                             "we will not pay the rest until this is sorted out.")
        app.record_settlement("bank-2a", "inv-2", 425_000)
        app.set_disputed("inv-2", True)

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
