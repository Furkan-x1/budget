"""Small end-to-end API smoke test for the local finance rules.

Run with: python -m unittest discover -s tests
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as budget


async def quiet_quote_loop():
    while True:
        await asyncio.sleep(3600)


class BudgetApiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="budget-test-")
        budget.DATA_DIR = Path(cls.temp_dir.name)
        budget.DB_PATH = budget.DATA_DIR / "budget.db"
        budget.quote_loop = quiet_quote_loop
        cls.client_context = TestClient(budget.app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        cls.temp_dir.cleanup()

    def test_asset_trades_debts_goals_and_forecast(self):
        client = self.client
        with budget.db_conn() as db:
            for code, bid, ask in (("GOLD_GRAM", 6000, 6100), ("USD", 50, 51)):
                db.execute(
                    """INSERT INTO quotes(asset_code,bid,ask,rate_date,updated_at,is_stale,source)
                       VALUES(?,?,?,?,?,?,?)""",
                    (code, bid, ask, "2026-11-08", budget.now_iso(), 0, "test"),
                )

        bank = client.post("/api/accounts", json={
            "name": "TRY banka", "kind": "bank", "asset_code": "TRY", "opening_balance": 50000,
        }).json()["id"]
        gold = client.post("/api/accounts", json={
            "name": "Gram altın", "kind": "investment", "asset_code": "GOLD_GRAM", "opening_balance": 0,
        }).json()["id"]

        buy = client.post("/api/trades", json={
            "trade_type": "buy", "trade_date": "2026-11-08", "asset_account_id": gold,
            "cash_account_id": bank, "asset_quantity": 2, "cash_amount": 12350,
            "description": "Makas ve vergi dahil gerçek maliyet",
        })
        self.assertEqual(buy.status_code, 200, buy.text)
        sale = client.post("/api/trades", json={
            "trade_type": "sell", "trade_date": "2026-11-08", "asset_account_id": gold,
            "cash_account_id": bank, "asset_quantity": 0.5, "cash_amount": 3150,
        })
        self.assertEqual(sale.status_code, 200, sale.text)
        accounts = {a["id"]: a for a in client.get("/api/accounts").json()}
        self.assertEqual(accounts[bank]["balance"], 40800)
        self.assertEqual(accounts[gold]["balance"], 1.5)

        # Linking the initial movement prevents debt/receivable from becoming a
        # phantom asset or liability in the balance sheet.
        receivable = client.post("/api/debts", json={
            "name": "Verdiğim borç", "direction": "receivable", "asset_code": "TRY",
            "amount": 2000, "account_id": bank,
        })
        self.assertEqual(receivable.status_code, 200, receivable.text)
        self.assertEqual(client.get("/api/debts").json()[0]["outstanding"], 2000)
        accounts = {a["id"]: a for a in client.get("/api/accounts").json()}
        self.assertEqual(accounts[bank]["balance"], 38800)

        partial = client.post(f"/api/debts/{receivable.json()['id']}/payment", json={
            "account_id": bank, "amount": 500, "payment_date": "2026-11-08",
        })
        self.assertEqual(partial.status_code, 200, partial.text)
        self.assertEqual(partial.json()["outstanding"], 1500)

        goal = client.post("/api/goals", json={
            "name": "Altın hedefi", "target_amount": 3, "asset_code": "GOLD_GRAM", "account_id": gold,
        })
        self.assertEqual(goal.status_code, 200, goal.text)
        self.assertEqual(client.get("/api/goals").json()[0]["progress"], 1.5)

        category = client.post("/api/categories", json={"name": "Hobi", "kind": "expense"})
        self.assertEqual(category.status_code, 200, category.text)
        self.assertEqual(client.patch(f"/api/categories/{category.json()['id']}", json={
            "name": "Hobiler", "kind": "expense",
        }).status_code, 200)
        self.assertIn("Hobiler", client.get("/api/meta").json()["categories"])

        dashboard = client.get("/api/dashboard?month=2026-11")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        analytics = client.get("/api/analytics?month=2026-11")
        self.assertEqual(analytics.status_code, 200, analytics.text)
        self.assertEqual(len(analytics.json()["monthly"]), 12)
        self.assertEqual(len(analytics.json()["net_worth_history"]), 1)

        # A transferred asset purchase/sale must not be counted as income or expense.
        self.assertEqual(dashboard.json()["income"], 0)
        self.assertEqual(dashboard.json()["expense"], 0)


if __name__ == "__main__":
    unittest.main()
