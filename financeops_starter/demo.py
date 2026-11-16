"""Run: python3 demo.py. Uses a temporary database; no credentials required."""
from tempfile import TemporaryDirectory
from pathlib import Path
from core import FinanceCore


def main():
    with TemporaryDirectory() as folder:
        app = FinanceCore(str(Path(folder) / "demo.sqlite3"))
        app.create_invoice("inv-1", "Demo customer", 1_200_000)
        app.add_evidence("contract-1", "inv-1", "CAD 12,000 due; no discount agreed.")
        app.record_settlement("bank-1", "inv-1", 1_000_000)
        print("After CAD 10,000 settlement:", app.invoice("inv-1"))
        app.propose("action-1", "inv-1", "collection_reminder", ["contract-1"])
        app.approve("action-1")
        print("Simulated action:", app.execute_simulated("action-1"))
        print("Sending reminder does NOT resolve invoice:", app.invoice("inv-1")["state"])
        app.record_settlement("bank-2", "inv-1", 200_000)
        print("After verified demo settlement:", app.invoice("inv-1"))
        app.close()


if __name__ == "__main__":
    main()
