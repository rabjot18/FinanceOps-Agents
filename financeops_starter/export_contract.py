"""Write the frozen HTTP contract to contracts/openapi.json (Milestone 4).

    .venv/bin/python export_contract.py          # rewrite the snapshot
    .venv/bin/python export_contract.py --check  # exit 1 if the API drifted

The snapshot is what the frontend's TypeScript types are generated from
(frontend: npm run gen:api). Only the contract owner re-exports it; a change
here means the frontend must be regenerated and re-checked in the same change.
The demo routes are included, as the demo lab screen calls them.
"""
import json
import sys
import tempfile
from pathlib import Path

CONTRACT = Path(__file__).parent / "contracts" / "openapi.json"


def current():
    from api import create_app
    with tempfile.TemporaryDirectory() as temp:
        app = create_app(db_path=str(Path(temp) / "contract.sqlite3"), owner_token="contract",
                         enable_demo=True, agent_client=object())
        return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main():
    text = current()
    if "--check" in sys.argv:
        if not CONTRACT.exists() or CONTRACT.read_text() != text:
            print("API contract drifted from contracts/openapi.json", file=sys.stderr)
            return 1
        print("contract unchanged")
        return 0
    CONTRACT.parent.mkdir(exist_ok=True)
    CONTRACT.write_text(text)
    print(f"wrote {CONTRACT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
