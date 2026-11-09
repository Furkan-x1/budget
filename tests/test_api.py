"""Small end-to-end API smoke test for the local finance rules.

Run with: python -m unittest discover -s tests
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app.core as budget
import app.main  # noqa: F401 - registers the API routes on the core app


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

        # Large TRY goal amounts are accepted, and a total goal tracks the
        # same portfolio net-worth calculation used on the dashboard.
        total_goal = client.post("/api/goals", json={
            "name": "Toplam varlık hedefi", "target_amount": 500000,
            "asset_code": "TRY", "account_id": None, "tracking_type": "total",
        })
        self.assertEqual(total_goal.status_code, 200, total_goal.text)
        total_goals = client.get("/api/goals").json()
        total_goal_item = next(g for g in total_goals if g["id"] == total_goal.json()["id"])
        current_dashboard = client.get("/api/dashboard?month=2026-11").json()
        self.assertEqual(total_goal_item["tracking_type"], "total")
        self.assertEqual(total_goal_item["progress"], current_dashboard["total_net_worth"])
        self.assertEqual(total_goal_item["target_amount"], 500000)

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
        self.assertEqual(len(analytics.json()["daily"]), 30)
        self.assertIn("savings_rate", analytics.json()["summary"])
        self.assertIn("net", analytics.json()["monthly"][-1])

        # A transferred asset purchase/sale must not be counted as income or expense.
        self.assertEqual(dashboard.json()["income"], 0)
        self.assertEqual(dashboard.json()["expense"], 0)

        # A one-month salary adjustment changes only that occurrence, not the template.
        salary = client.post("/api/schedules", json={
            "name": "Maaş", "entry_type": "income", "amount": 50000,
            "account_id": bank, "category": "Maaş", "frequency": "monthly",
            "day_of_month": 15, "start_date": "2026-11-15",
        })
        self.assertEqual(salary.status_code, 200, salary.text)
        salary_id = salary.json()["id"]
        changed_salary = client.patch(f"/api/occurrences/schedule/{salary_id}/2026-12-15", json={
            "due_date": "2026-12-20", "amount": 42000,
            "description": "Maaş (izin kesintisi)",
        })
        self.assertEqual(changed_salary.status_code, 200, changed_salary.text)
        december = client.get("/api/forecast?month=2026-12").json()
        salary_forecast = next(x for x in december if x["source_type"] == "schedule")
        self.assertEqual(salary_forecast["amount"], 42000)
        self.assertEqual(salary_forecast["due_date"], "2026-12-20")
        # Other months still use the original recurring amount.
        january = client.get("/api/forecast?month=2027-01").json()
        january_salary = next(x for x in january if x["source_type"] == "schedule")
        self.assertEqual(january_salary["amount"], 50000)
        completed_salary = client.post(f"/api/schedules/{salary_id}/complete", json={
            "occurrence_key": "2026-12-15",
        })
        self.assertEqual(completed_salary.status_code, 200, completed_salary.text)
        salary_entry_id = completed_salary.json()["id"]
        edited_past = client.patch(f"/api/entries/{salary_entry_id}", json={
            "entry_date": "2026-12-21", "amount": 43000,
        })
        self.assertEqual(edited_past.status_code, 200, edited_past.text)
        saved_salary = next(x for x in client.get("/api/entries?month=2026-12").json()
                            if x["id"] == salary_entry_id)
        self.assertEqual(saved_salary["entry_date"], "2026-12-21")
        self.assertEqual(saved_salary["amount"], 43000)

        # A single future installment can be adjusted without changing other installments.
        installment = client.post("/api/installments", json={
            "name": "Laptop", "total_amount": 8000, "account_id": bank,
            "category": "Alışveriş", "total_installments": 4,
            "current_installment": 2, "statement_date": "2026-11-08",
        })
        self.assertEqual(installment.status_code, 200, installment.text)
        plan_id = installment.json()["id"]
        changed_installment = client.patch(f"/api/occurrences/installment/{plan_id}/3", json={
            "due_date": "2026-12-05", "amount": 1700, "description": "Laptop · özel taksit",
        })
        self.assertEqual(changed_installment.status_code, 200, changed_installment.text)
        december = client.get("/api/forecast?month=2026-12").json()
        laptop_forecast = next(x for x in december if x["source_type"] == "installment")
        self.assertEqual(laptop_forecast["amount"], 1700)
        self.assertEqual(laptop_forecast["due_date"], "2026-12-05")
        completed_installment = client.post(f"/api/installments/{plan_id}/complete", json={
            "occurrence_key": "3",
        })
        self.assertEqual(completed_installment.status_code, 200, completed_installment.text)
        laptop_entry = client.patch(f"/api/entries/{completed_installment.json()['id']}", json={
            "entry_date": "2026-12-06", "amount": 1750,
        })
        self.assertEqual(laptop_entry.status_code, 200, laptop_entry.text)

        # A future first installment must remain pending; completing it can be reversed.
        future_statement = budget.add_months(budget.date.today().replace(day=1), 1)
        future_month = future_statement.strftime("%Y-%m")
        future_date = future_statement.isoformat()
        future_plan = client.post("/api/installments", json={
            "name": "Gelecek tarihli alışveriş", "total_amount": 3000, "account_id": bank,
            "category": "Alışveriş", "total_installments": 3,
            "current_installment": 1, "statement_date": future_date,
        })
        self.assertEqual(future_plan.status_code, 200, future_plan.text)
        self.assertEqual(future_plan.json()["historical_installments_added"], 0)
        self.assertEqual(future_plan.json()["planned_installments"], 3)
        future_plan_id = future_plan.json()["id"]
        future_items = client.get(
            f"/api/occurrences?from_date={future_date}&to_date={(budget.add_months(future_statement, 1)-budget.timedelta(days=1)).isoformat()}"
        ).json()
        first_future = next(x for x in future_items
                            if x["source_type"] == "installment" and x["source_id"] == future_plan_id
                            and x["occurrence_key"] == "1")
        self.assertEqual(first_future["status"], "pending")
        self.assertEqual(first_future["due_date"], future_date)

        balance_before_completion = next(a for a in client.get("/api/accounts").json() if a["id"] == bank)["balance"]
        future_completed = client.post(f"/api/installments/{future_plan_id}/complete", json={
            "occurrence_key": "1",
        })
        self.assertEqual(future_completed.status_code, 200, future_completed.text)
        completed_items = client.get(
            f"/api/occurrences?from_date={future_date}&to_date={future_date}"
        ).json()
        completed_future = next(x for x in completed_items if x["source_id"] == future_plan_id)
        self.assertEqual(completed_future["status"], "completed")
        balance_after_completion = next(a for a in client.get("/api/accounts").json() if a["id"] == bank)["balance"]
        self.assertAlmostEqual(balance_before_completion - balance_after_completion, 1000)

        reverted = client.post(
            f"/api/occurrences/installment/{future_plan_id}/1/uncomplete",
        )
        self.assertEqual(reverted.status_code, 200, reverted.text)
        pending_items = client.get(
            f"/api/occurrences?from_date={future_date}&to_date={future_date}"
        ).json()
        pending_future = next(x for x in pending_items if x["source_id"] == future_plan_id)
        self.assertEqual(pending_future["status"], "pending")
        self.assertEqual(pending_future["due_date"], future_date)
        self.assertEqual(pending_future["amount"], 1000)
        balance_after_revert = next(a for a in client.get("/api/accounts").json() if a["id"] == bank)["balance"]
        self.assertAlmostEqual(balance_after_revert, balance_before_completion)

        # Month outlook combines actual entries and pending scheduled income/expenses.
        outlook = client.get("/api/month-outlook?month=2026-12")
        self.assertEqual(outlook.status_code, 200, outlook.text)
        outlook_data = outlook.json()
        self.assertIn("actual_income", outlook_data)
        self.assertIn("planned_income", outlook_data)
        self.assertIn("total_expense", outlook_data)
        self.assertAlmostEqual(outlook_data["projected_net"], outlook_data["total_income"] - outlook_data["total_expense"])
        self.assertGreaterEqual(outlook_data["planned_count"], 1)
        self.assertIn("planned_income_items", outlook_data)
        self.assertIn("planned_expense_items", outlook_data)
        future_plan_detail = next(
            item for item in outlook_data["planned_expense_items"]
            if item["description"] == "Gelecek tarihli alışveriş · 1/3"
        )
        self.assertEqual(future_plan_detail["amount_try"], 1000)
        self.assertEqual(future_plan_detail["account_name"], "TRY banka")

        # The same occurrence can be marked as completed again after reversing it.
        completed_again = client.post(f"/api/installments/{future_plan_id}/complete", json={
            "occurrence_key": "1",
        })
        self.assertEqual(completed_again.status_code, 200, completed_again.text)


if __name__ == "__main__":
    unittest.main()
