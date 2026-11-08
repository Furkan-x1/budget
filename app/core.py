from __future__ import annotations

import asyncio
import base64
import calendar
import hmac
import os
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "budget.db"
RATES_URL = os.getenv("RATES_URL", "https://cdn.jsdelivr.net/gh/MehmetAkgul/currency-api-tr@main/v1/currencies/try-full.json")
RATES_REFRESH_MINUTES = max(5, int(os.getenv("RATES_REFRESH_MINUTES", "15")))
APP_PASSWORD = os.getenv("APP_PASSWORD", "")

ASSETS: dict[str, str] = {
    "TRY": "Türk lirası", "USD": "Amerikan doları", "EUR": "Euro", "GBP": "İngiliz sterlini",
    "CHF": "İsviçre frangı", "JPY": "Japon yeni", "CAD": "Kanada doları", "AUD": "Avustralya doları",
    "CNY": "Çin yuanı", "AED": "BAE dirhemi", "SAR": "Suudi riyali", "RUB": "Rus rublesi",
    "KWD": "Kuveyt dinarı", "GOLD_GRAM": "Gram altın (24 ayar)", "GOLD_CEYREK": "Çeyrek altın",
    "GOLD_HALF": "Yarım altın", "GOLD_FULL": "Tam altın", "GOLD_CUMHURIYET": "Cumhuriyet altını",
    "SILVER_GRAM": "Gram gümüş",
}
QUOTE_KEYS = {
    "USD": "usd", "EUR": "eur", "GBP": "gbp", "CHF": "chf", "JPY": "jpy", "CAD": "cad",
    "AUD": "aud", "CNY": "cny", "AED": "aed", "SAR": "sar", "RUB": "rub", "KWD": "kwd",
    "GOLD_GRAM": "xau_gram", "GOLD_CEYREK": "xau_ceyrek", "GOLD_HALF": "xau_yarim",
    "GOLD_FULL": "xau_tam", "GOLD_CUMHURIYET": "xau_cumhuriyet", "SILVER_GRAM": "xag_gram",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db_conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('cash','bank','credit_card','investment')),
            asset_code TEXT NOT NULL, opening_balance REAL NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, entry_date TEXT NOT NULL,
            entry_type TEXT NOT NULL CHECK(entry_type IN ('income','expense','transfer')),
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            amount REAL NOT NULL CHECK(amount > 0),
            target_account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
            target_amount REAL, category TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT 'manual',
            source_id INTEGER, occurrence_key TEXT, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(entry_date);
        CREATE INDEX IF NOT EXISTS idx_entries_account ON entries(account_id);
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            entry_type TEXT NOT NULL CHECK(entry_type IN ('income','expense')),
            amount REAL NOT NULL CHECK(amount > 0),
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            category TEXT NOT NULL DEFAULT '',
            frequency TEXT NOT NULL CHECK(frequency IN ('monthly','weekly','yearly')),
            day_of_month INTEGER NOT NULL DEFAULT 1, start_date TEXT NOT NULL,
            end_date TEXT, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS installment_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            total_amount REAL NOT NULL CHECK(total_amount > 0),
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
            category TEXT NOT NULL DEFAULT '',
            total_installments INTEGER NOT NULL CHECK(total_installments >= 1),
            current_installment INTEGER NOT NULL CHECK(current_installment >= 1),
            statement_date TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS completed_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL CHECK(source_type IN ('schedule','installment')),
            source_id INTEGER NOT NULL, occurrence_key TEXT NOT NULL,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE RESTRICT,
            completed_at TEXT NOT NULL, UNIQUE(source_type, source_id, occurrence_key)
        );
        CREATE TABLE IF NOT EXISTS occurrence_overrides (
            source_type TEXT NOT NULL CHECK(source_type IN ('schedule','installment')),
            source_id INTEGER NOT NULL,
            occurrence_key TEXT NOT NULL,
            due_date TEXT,
            amount REAL CHECK(amount IS NULL OR amount > 0),
            account_id INTEGER REFERENCES accounts(id) ON DELETE RESTRICT,
            category TEXT,
            description TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_type,source_id,occurrence_key)
        );
        CREATE TABLE IF NOT EXISTS quotes (
            asset_code TEXT PRIMARY KEY, bid REAL NOT NULL, ask REAL NOT NULL,
            rate_date TEXT NOT NULL, updated_at TEXT NOT NULL,
            is_stale INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES('base_currency', 'TRY')")
        db.execute(
            "INSERT OR IGNORE INTO quotes(asset_code,bid,ask,rate_date,updated_at,is_stale,source) "
            "VALUES('TRY',1,1,?,?,0,'Sabit')", (date.today().isoformat(), now_iso()),
        )
        db.executescript("""
        CREATE TABLE IF NOT EXISTS trade_details (
            entry_id INTEGER PRIMARY KEY REFERENCES entries(id) ON DELETE CASCADE,
            trade_type TEXT NOT NULL CHECK(trade_type IN ('buy','sell')),
            asset_account_id INTEGER NOT NULL REFERENCES accounts(id),
            cash_account_id INTEGER NOT NULL REFERENCES accounts(id),
            asset_code TEXT NOT NULL, cash_asset_code TEXT NOT NULL,
            asset_quantity REAL NOT NULL CHECK(asset_quantity > 0),
            cash_amount REAL NOT NULL CHECK(cash_amount > 0), cash_value_try REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            kind TEXT NOT NULL DEFAULT 'both' CHECK(kind IN ('income','expense','both')),
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            counterparty TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL CHECK(direction IN ('payable','receivable')),
            asset_code TEXT NOT NULL, original_amount REAL NOT NULL CHECK(original_amount > 0),
            outstanding REAL NOT NULL CHECK(outstanding >= 0), due_date TEXT,
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS debt_payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debt_id INTEGER NOT NULL REFERENCES debts(id) ON DELETE CASCADE,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE RESTRICT,
            amount REAL NOT NULL CHECK(amount > 0), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS debt_openings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            debt_id INTEGER NOT NULL REFERENCES debts(id) ON DELETE CASCADE,
            entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE RESTRICT
        );
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            target_amount REAL NOT NULL CHECK(target_amount > 0), asset_code TEXT NOT NULL,
            account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
            target_date TEXT, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS net_worth_snapshots (
            snapshot_date TEXT PRIMARY KEY, total_value REAL NOT NULL,
            rate_stale INTEGER NOT NULL DEFAULT 0
        );
        """)
        starter_categories = [
            ('Maaş','income'),('Ek gelir','income'),('Yatırım geliri','income'),
            ('Konut','expense'),('Market','expense'),('Ulaşım','expense'),('Yeme-içme','expense'),
            ('Faturalar','expense'),('Sağlık','expense'),('Eğlence','expense'),
            ('Alışveriş','expense'),('Abonelik','expense'),('Eğitim','expense'),
            ('Borç ödemesi','expense'),('Borç tahsilatı','income'),('Borç alınan para','income'),
            ('Verilen borç','expense'),('Diğer','both')
        ]
        db.executemany("INSERT OR IGNORE INTO categories(name,kind) VALUES(?,?)", starter_categories)


class AccountIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["cash", "bank", "credit_card", "investment"]
    asset_code: str
    opening_balance: float = 0
    note: str = Field(default="", max_length=250)

    @field_validator("asset_code")
    @classmethod
    def valid_asset(cls, value: str) -> str:
        value = value.upper()
        if value not in ASSETS:
            raise ValueError("Desteklenmeyen para/varlık türü")
        return value


class EntryIn(BaseModel):
    entry_date: date
    entry_type: Literal["income", "expense", "transfer"]
    account_id: int
    amount: float = Field(gt=0)
    target_account_id: int | None = None
    target_amount: float | None = Field(default=None, gt=0)
    category: str = Field(default="", max_length=60)
    description: str = Field(default="", max_length=160)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    entry_type: Literal["income", "expense"]
    amount: float = Field(gt=0)
    account_id: int
    category: str = Field(default="", max_length=60)
    frequency: Literal["monthly", "weekly", "yearly"] = "monthly"
    day_of_month: int = Field(default=1, ge=1, le=31)
    start_date: date
    end_date: date | None = None


class InstallmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    total_amount: float = Field(gt=0)
    account_id: int
    category: str = Field(default="Alışveriş", max_length=60)
    total_installments: int = Field(ge=1, le=120)
    current_installment: int = Field(ge=1, le=120)
    statement_date: date


class OccurrenceCompleteIn(BaseModel):
    occurrence_key: str


class TradeIn(BaseModel):
    trade_type: Literal["buy", "sell"]
    trade_date: date
    asset_account_id: int
    cash_account_id: int
    asset_quantity: float = Field(gt=0)
    cash_amount: float = Field(gt=0)
    description: str = Field(default="", max_length=160)


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["income", "expense", "both"] = "both"


class DebtIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    counterparty: str = Field(default="", max_length=100)
    direction: Literal["payable", "receivable"]
    asset_code: str
    amount: float = Field(gt=0)
    account_id: int | None = None
    due_date: date | None = None
    note: str = Field(default="", max_length=250)


class DebtPaymentIn(BaseModel):
    account_id: int
    amount: float = Field(gt=0)
    payment_date: date


class GoalIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_amount: float = Field(gt=0)
    asset_code: str
    account_id: int | None = None
    target_date: date | None = None
    note: str = Field(default="", max_length=250)


def rowdict(row: sqlite3.Row | None):
    return dict(row) if row is not None else None


def ensure_account(db: sqlite3.Connection, account_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Hesap bulunamadı")
    return row


def add_months(d: date, months: int) -> date:
    month0 = d.month - 1 + months
    year = d.year + month0 // 12
    month = month0 % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def occurrence_done(db: sqlite3.Connection, source_type: str, source_id: int, key: str) -> bool:
    return db.execute(
        "SELECT 1 FROM completed_occurrences WHERE source_type=? AND source_id=? AND occurrence_key=?",
        (source_type, source_id, key),
    ).fetchone() is not None


def account_balance(db: sqlite3.Connection, account: sqlite3.Row) -> float:
    balance = float(account["opening_balance"])
    account_id = int(account["id"])
    rows = db.execute("SELECT * FROM entries WHERE account_id=? OR target_account_id=?", (account_id, account_id)).fetchall()
    liability = account["kind"] == "credit_card"
    for entry in rows:
        if entry["account_id"] == account_id:
            amount = float(entry["amount"])
            if entry["entry_type"] == "income":
                balance += -amount if liability else amount
            elif entry["entry_type"] == "expense":
                balance += amount if liability else -amount
            elif entry["entry_type"] == "transfer":
                balance += amount if liability else -amount
        if entry["target_account_id"] == account_id and entry["entry_type"] == "transfer":
            amount = float(entry["target_amount"] or entry["amount"])
            balance += -amount if liability else amount
    return round(balance, 8)


def get_quote_map(db: sqlite3.Connection) -> dict[str, dict]:
    rows = db.execute("SELECT * FROM quotes").fetchall()
    return {r["asset_code"]: dict(r) for r in rows}


def value_in_try(asset_code: str, amount: float, quotes: dict[str, dict]) -> tuple[float, bool]:
    if asset_code == "TRY":
        return amount, False
    q = quotes.get(asset_code)
    if not q:
        return 0.0, True
    return amount * float(q["bid"]), bool(q["is_stale"])


def add_entry(db: sqlite3.Connection, *, entry_date: date, entry_type: str, account_id: int,
              amount: float, target_account_id: int | None = None, target_amount: float | None = None,
              category: str = "", description: str = "", source_type: str = "manual",
              source_id: int | None = None, occurrence_key: str | None = None) -> int:
    ensure_account(db, account_id)
    if entry_type == "transfer":
        if target_account_id is None or target_account_id == account_id:
            raise HTTPException(400, "Transfer için farklı bir hedef hesap seçilmeli")
        ensure_account(db, target_account_id)
        if target_amount is None:
            target_amount = amount
    elif target_account_id is not None:
        raise HTTPException(400, "Hedef hesap yalnızca transferde kullanılabilir")
    cur = db.execute(
        """INSERT INTO entries(entry_date,entry_type,account_id,amount,target_account_id,target_amount,
           category,description,source_type,source_id,occurrence_key,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (entry_date.isoformat(), entry_type, account_id, amount, target_account_id, target_amount,
         category, description, source_type, source_id, occurrence_key, now_iso()),
    )
    return int(cur.lastrowid)


def schedule_dates(schedule: sqlite3.Row, from_date: date, to_date: date) -> list[date]:
    start = date.fromisoformat(schedule["start_date"])
    end = date.fromisoformat(schedule["end_date"]) if schedule["end_date"] else None
    stop = min(to_date, end) if end else to_date
    if stop < start:
        return []
    dates: list[date] = []
    if schedule["frequency"] == "weekly":
        current = start
        while current <= stop:
            if current >= from_date:
                dates.append(current)
            current += timedelta(days=7)
    elif schedule["frequency"] == "yearly":
        current = start
        while current <= stop:
            if current >= from_date:
                dates.append(current)
            try:
                current = current.replace(year=current.year + 1)
            except ValueError:
                current = current.replace(year=current.year + 1, day=28)
    else:
        offset = 0
        while True:
            month0 = start.month - 1 + offset
            year = start.year + month0 // 12
            month = month0 % 12 + 1
            day = min(int(schedule["day_of_month"]), calendar.monthrange(year, month)[1])
            current = date(year, month, day)
            if current > stop:
                break
            if current >= start and current >= from_date:
                dates.append(current)
            offset += 1
    return dates


def occurrence_override(db: sqlite3.Connection, source_type: str, source_id: int, key: str) -> dict:
    row = db.execute(
        """SELECT due_date,amount,account_id,category,description
           FROM occurrence_overrides WHERE source_type=? AND source_id=? AND occurrence_key=?""",
        (source_type, source_id, key),
    ).fetchone()
    return dict(row) if row else {}


def apply_occurrence_override(db: sqlite3.Connection, item: dict) -> dict:
    override = occurrence_override(db, item["source_type"], item["source_id"], item["occurrence_key"])
    for field in ("due_date", "amount", "account_id", "category", "description"):
        if override.get(field) is not None:
            item[field] = override[field]
    return item


def collect_planned(db: sqlite3.Connection, from_date: date, to_date: date) -> list[dict]:
    items: list[dict] = []
    schedules = db.execute("SELECT * FROM schedules WHERE active=1").fetchall()
    for schedule in schedules:
        for due in schedule_dates(schedule, from_date, to_date):
            key = due.isoformat()
            if not occurrence_done(db, "schedule", schedule["id"], key):
                item = {
                    "source_type": "schedule", "source_id": schedule["id"], "occurrence_key": key,
                    "due_date": key, "entry_type": schedule["entry_type"], "amount": schedule["amount"],
                    "account_id": schedule["account_id"], "category": schedule["category"],
                    "description": schedule["name"], "name": schedule["name"],
                }
                items.append(apply_occurrence_override(db, item))
    plans = db.execute("SELECT * FROM installment_plans").fetchall()
    for plan in plans:
        current_date = date.fromisoformat(plan["statement_date"])
        first_date = add_months(current_date, -(int(plan["current_installment"]) - 1))
        per = round(float(plan["total_amount"]) / int(plan["total_installments"]), 2)
        for no in range(1, int(plan["total_installments"]) + 1):
            due = add_months(first_date, no - 1)
            if due < from_date or due > to_date or no <= int(plan["current_installment"]):
                continue
            key = str(no)
            if occurrence_done(db, "installment", plan["id"], key):
                continue
            amount = round(float(plan["total_amount"]) - per * (no - 1), 2) if no == int(plan["total_installments"]) else per
            item = {
                "source_type": "installment", "source_id": plan["id"], "occurrence_key": key,
                "due_date": due.isoformat(), "entry_type": "expense", "amount": amount,
                "account_id": plan["account_id"], "category": plan["category"],
                "description": f"{plan['name']} · {no}/{plan['total_installments']}",
                "name": plan["name"], "installment_no": no, "installment_total": plan["total_installments"],
            }
            items.append(apply_occurrence_override(db, item))
    items.sort(key=lambda x: (x["due_date"], x["description"].lower()))
    return items


def collect_occurrences(db: sqlite3.Connection, from_date: date, to_date: date) -> list[dict]:
    items: list[dict] = []
    schedules = db.execute("SELECT * FROM schedules").fetchall()
    for schedule in schedules:
        if not schedule["active"]:
            # Completed occurrences remain editable even after a schedule is paused.
            completed = db.execute(
                """SELECT occurrence_key FROM completed_occurrences
                   WHERE source_type='schedule' AND source_id=?""", (schedule["id"],)
            ).fetchall()
            allowed = {r["occurrence_key"] for r in completed}
            due_dates = [date.fromisoformat(k) for k in allowed if from_date <= date.fromisoformat(k) <= to_date]
        else:
            due_dates = schedule_dates(schedule, from_date, to_date)
        for due in due_dates:
            key = due.isoformat()
            completed = db.execute(
                """SELECT e.id,e.entry_date,e.amount,e.account_id,e.category,e.description,a.name AS account_name,
                          a.asset_code,cc.entry_id
                   FROM completed_occurrences cc JOIN entries e ON e.id=cc.entry_id
                   JOIN accounts a ON a.id=e.account_id
                   WHERE cc.source_type='schedule' AND cc.source_id=? AND cc.occurrence_key=?""",
                (schedule["id"], key),
            ).fetchone()
            if completed:
                items.append({
                    "source_type":"schedule","source_id":schedule["id"],"occurrence_key":key,
                    "status":"completed","entry_id":completed["id"],"due_date":completed["entry_date"],
                    "entry_type":schedule["entry_type"],"amount":completed["amount"],
                    "account_id":completed["account_id"],"account_name":completed["account_name"],
                    "asset_code":completed["asset_code"],"category":completed["category"],
                    "description":completed["description"],"name":schedule["name"],
                })
            else:
                item = apply_occurrence_override(db, {
                    "source_type":"schedule","source_id":schedule["id"],"occurrence_key":key,
                    "status":"pending","due_date":key,"entry_type":schedule["entry_type"],
                    "amount":schedule["amount"],"account_id":schedule["account_id"],
                    "category":schedule["category"],"description":schedule["name"],"name":schedule["name"],
                })
                account = db.execute("SELECT name,asset_code FROM accounts WHERE id=?", (item["account_id"],)).fetchone()
                item["account_name"] = account["name"] if account else "?"
                item["asset_code"] = account["asset_code"] if account else "TRY"
                items.append(item)
    plans = db.execute("SELECT * FROM installment_plans").fetchall()
    for plan in plans:
        statement = date.fromisoformat(plan["statement_date"])
        first_date = add_months(statement, -(int(plan["current_installment"]) - 1))
        per = round(float(plan["total_amount"]) / int(plan["total_installments"]), 2)
        for no in range(1, int(plan["total_installments"]) + 1):
            due = add_months(first_date, no - 1)
            if not from_date <= due <= to_date:
                continue
            key = str(no)
            completed = db.execute(
                """SELECT e.id,e.entry_date,e.amount,e.account_id,e.category,e.description,a.name AS account_name,
                          a.asset_code
                   FROM completed_occurrences cc JOIN entries e ON e.id=cc.entry_id
                   JOIN accounts a ON a.id=e.account_id
                   WHERE cc.source_type='installment' AND cc.source_id=? AND cc.occurrence_key=?""",
                (plan["id"], key),
            ).fetchone()
            if completed:
                items.append({
                    "source_type":"installment","source_id":plan["id"],"occurrence_key":key,
                    "status":"completed","entry_id":completed["id"],"due_date":completed["entry_date"],
                    "entry_type":"expense","amount":completed["amount"],"account_id":completed["account_id"],
                    "account_name":completed["account_name"],"asset_code":completed["asset_code"],
                    "category":completed["category"],"description":completed["description"],
                    "name":plan["name"],"installment_no":no,"installment_total":plan["total_installments"],
                })
            else:
                amount = round(float(plan["total_amount"]) - per * (no - 1), 2) if no == int(plan["total_installments"]) else per
                item = apply_occurrence_override(db, {
                    "source_type":"installment","source_id":plan["id"],"occurrence_key":key,
                    "status":"pending","due_date":due.isoformat(),"entry_type":"expense","amount":amount,
                    "account_id":plan["account_id"],"category":plan["category"],
                    "description":f"{plan['name']} · {no}/{plan['total_installments']}",
                    "name":plan["name"],"installment_no":no,"installment_total":plan["total_installments"],
                })
                account = db.execute("SELECT name,asset_code FROM accounts WHERE id=?", (item["account_id"],)).fetchone()
                item["account_name"] = account["name"] if account else "?"
                item["asset_code"] = account["asset_code"] if account else "TRY"
                items.append(item)
    items.sort(key=lambda x: (x["due_date"], x["description"].lower()))
    return items


async def refresh_quotes() -> dict:
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(RATES_URL, headers={"User-Agent": "LocalBudget/1.0"})
            response.raise_for_status()
            payload = response.json()
        raw_try = payload.get("try") or {}
        if not raw_try:
            raise ValueError("Kur yanıtında TRY fiyatları bulunamadı")
        updated_at = payload.get("updated_at") or now_iso()
        rate_date = payload.get("date") or date.today().isoformat()
        source_stale = bool(payload.get("is_stale", False))
        updated = 0
        with db_conn() as db:
            for asset_code, key in QUOTE_KEYS.items():
                quote = raw_try.get(key)
                if not isinstance(quote, dict) or quote.get("bid") is None or quote.get("ask") is None:
                    continue
                bid, ask = float(quote["bid"]), float(quote["ask"])
                if bid <= 0 or ask <= 0 or bid > ask * 1.5:
                    continue
                db.execute(
                    """INSERT INTO quotes(asset_code,bid,ask,rate_date,updated_at,is_stale,source)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(asset_code) DO UPDATE SET
                       bid=excluded.bid, ask=excluded.ask, rate_date=excluded.rate_date,
                       updated_at=excluded.updated_at, is_stale=excluded.is_stale, source=excluded.source""",
                    (asset_code, bid, ask, rate_date, updated_at, int(source_stale or quote.get("stale", False)),
                     str(quote.get("source") or "currency-api-tr")),
                )
                updated += 1
            db.execute("UPDATE quotes SET is_stale=1 WHERE asset_code!='TRY' AND updated_at!=?", (updated_at,))
        return {"ok": True, "updated": updated, "updated_at": updated_at, "is_stale": source_stale}
    except Exception as exc:
        with db_conn() as db:
            db.execute("UPDATE quotes SET is_stale=1 WHERE asset_code!='TRY'")
        return {"ok": False, "error": str(exc)}


async def quote_loop():
    while True:
        await refresh_quotes()
        await asyncio.sleep(RATES_REFRESH_MINUTES * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(quote_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Budget", version="0.2.0", lifespan=lifespan)
