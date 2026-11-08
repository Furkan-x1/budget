from .core import *

@app.middleware("http")
async def optional_lan_password(request: Request, call_next):
    # Optional single-password gate for shared LANs. No login system or user table.
    if APP_PASSWORD:
        auth = request.headers.get("authorization", "")
        valid = False
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:], validate=True).decode("utf-8")
                username, separator, password = decoded.partition(":")
                valid = bool(separator and username == "budget" and hmac.compare_digest(password, APP_PASSWORD))
            except Exception:
                valid = False
        if not valid:
            from starlette.responses import Response
            return Response(content="Kimlik doğrulaması gerekli.", status_code=401,
                            headers={"WWW-Authenticate": 'Basic realm="Budget"'})
    return await call_next(request)


@app.get("/api/status")
def status():
    with db_conn() as db:
        count = db.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
        quotes = get_quote_map(db)
    return {"ok": True, "accounts": count, "quotes_updated_at": max((q["updated_at"] for q in quotes.values()), default=None)}


@app.get("/api/meta")
def meta():
    with db_conn() as db:
        categories = [dict(r) for r in db.execute("SELECT id,name,kind FROM categories WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()]
    public_assets = {k: v for k, v in ASSETS.items() if k != "SILVER_GRAM"}
    return {"assets": public_assets, "account_kinds": {
        "cash": "Nakit", "bank": "Banka", "credit_card": "Kredi kartı", "investment": "Yatırım"
    }, "categories": [c["name"] for c in categories], "category_records": categories}


@app.get("/api/accounts")
def list_accounts():
    with db_conn() as db:
        rows = db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        quotes = get_quote_map(db)
        result = []
        for row in rows:
            item = dict(row)
            item["balance"] = account_balance(db, row)
            item["value_try"], item["rate_stale"] = value_in_try(row["asset_code"], item["balance"], quotes)
            if row["kind"] == "credit_card":
                item["value_try"] *= -1
            item["asset_label"] = ASSETS.get(row["asset_code"], row["asset_code"])
            quote = quotes.get(row["asset_code"])
            item["quote_bid"] = float(quote["bid"]) if quote else None
            item["quote_ask"] = float(quote["ask"]) if quote else None
            item["quote_source"] = quote["source"] if quote else None
            item["quote_updated_at"] = quote["updated_at"] if quote else None
            result.append(item)
    return result


@app.post("/api/accounts")
def create_account(data: AccountIn):
    if data.kind == "credit_card" and data.asset_code.startswith("GOLD_"):
        raise HTTPException(400, "Kredi kartı hesabı altın cinsinden olamaz")
    with db_conn() as db:
        cur = db.execute(
            "INSERT INTO accounts(name,kind,asset_code,opening_balance,note,created_at) VALUES(?,?,?,?,?,?)",
            (data.name.strip(), data.kind, data.asset_code, data.opening_balance, data.note.strip(), now_iso()),
        )
        return {"id": cur.lastrowid}


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: int):
    with db_conn() as db:
        account = ensure_account(db, account_id)
        if abs(account_balance(db, account)) > 0.000001:
            raise HTTPException(409, "Bakiyesi sıfırlanmadan hesap silinemez. Önce tutarı başka hesaba aktar.")
        used = db.execute(
            "SELECT (SELECT COUNT(*) FROM entries WHERE account_id=? OR target_account_id=?) + "
            "(SELECT COUNT(*) FROM schedules WHERE account_id=?) + "
            "(SELECT COUNT(*) FROM installment_plans WHERE account_id=?) AS n",
            (account_id, account_id, account_id, account_id),
        ).fetchone()["n"]
        if used:
            raise HTTPException(409, "Bu hesap geçmiş işlemlerde kullanılıyor; kayıt bütünlüğü için silinemez.")
        cur = db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Hesap bulunamadı")
    return {"ok": True}


@app.get("/api/entries")
def list_entries(month: str | None = None, limit: int = 100):
    limit = min(max(limit, 1), 500)
    with db_conn() as db:
        if month:
            rows = db.execute(
                """SELECT e.*, a.name AS account_name, a.asset_code AS asset_code,
                   ta.name AS target_account_name,ta.asset_code AS target_asset_code,
                   td.trade_type,td.asset_quantity,td.cash_amount,td.asset_code AS traded_asset_code,td.cash_asset_code
                   FROM entries e JOIN accounts a ON a.id=e.account_id
                   LEFT JOIN accounts ta ON ta.id=e.target_account_id LEFT JOIN trade_details td ON td.entry_id=e.id
                   WHERE substr(e.entry_date,1,7)=? ORDER BY e.entry_date DESC,e.id DESC LIMIT ?""",
                (month, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """SELECT e.*, a.name AS account_name, a.asset_code AS asset_code,
                   ta.name AS target_account_name,ta.asset_code AS target_asset_code,
                   td.trade_type,td.asset_quantity,td.cash_amount,td.asset_code AS traded_asset_code,td.cash_asset_code
                   FROM entries e JOIN accounts a ON a.id=e.account_id
                   LEFT JOIN accounts ta ON ta.id=e.target_account_id LEFT JOIN trade_details td ON td.entry_id=e.id
                   ORDER BY e.entry_date DESC,e.id DESC LIMIT ?""", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/entries")
def create_entry(data: EntryIn):
    with db_conn() as db:
        account = ensure_account(db, data.account_id)
        if data.entry_type == "transfer":
            if data.target_account_id is None:
                raise HTTPException(400, "Hedef hesap seçilmeli")
            target = ensure_account(db, data.target_account_id)
            if account["asset_code"] != target["asset_code"] and data.target_amount is None:
                raise HTTPException(400, "Farklı para/varlık türleri arasında transfer için hedef miktarı girilmeli")
        entry_id = add_entry(
            db, entry_date=data.entry_date, entry_type=data.entry_type, account_id=data.account_id,
            amount=data.amount, target_account_id=data.target_account_id, target_amount=data.target_amount,
            category=data.category.strip(), description=data.description.strip(),
        )
    return {"id": entry_id}


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int):
    with db_conn() as db:
        linked = db.execute("SELECT 1 FROM completed_occurrences WHERE entry_id=?", (entry_id,)).fetchone()
        if linked:
            raise HTTPException(409, "Tekrarlanan/taksitli işlem kaydı buradan silinemez; plan üzerinden yönetilmelidir.")
        if db.execute("SELECT 1 FROM debt_payments WHERE entry_id=? OR entry_id IN (SELECT entry_id FROM debt_openings)", (entry_id,)).fetchone():
            raise HTTPException(409, "Borç açılış/ödeme kaydı borç geçmişiyle bağlantılıdır; buradan silinemez.")
        cur = db.execute("DELETE FROM entries WHERE id=?", (entry_id,))
        if not cur.rowcount:
            raise HTTPException(404, "İşlem bulunamadı")
    return {"ok": True}


@app.get("/api/schedules")
def list_schedules():
    with db_conn() as db:
        rows = db.execute("SELECT * FROM schedules ORDER BY active DESC,id DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/schedules")
def create_schedule(data: ScheduleIn):
    if data.end_date and data.end_date < data.start_date:
        raise HTTPException(400, "Bitiş tarihi başlangıçtan önce olamaz")
    with db_conn() as db:
        ensure_account(db, data.account_id)
        cur = db.execute(
            """INSERT INTO schedules(name,entry_type,amount,account_id,category,frequency,day_of_month,
               start_date,end_date,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (data.name.strip(), data.entry_type, data.amount, data.account_id, data.category.strip(),
             data.frequency, data.day_of_month, data.start_date.isoformat(),
             data.end_date.isoformat() if data.end_date else None, now_iso()),
        )
    return {"id": cur.lastrowid}


@app.patch("/api/schedules/{schedule_id}/toggle")
def toggle_schedule(schedule_id: int):
    with db_conn() as db:
        row = db.execute("SELECT active FROM schedules WHERE id=?", (schedule_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Tekrarlanan işlem bulunamadı")
        active = 0 if row["active"] else 1
        db.execute("UPDATE schedules SET active=? WHERE id=?", (active, schedule_id))
    return {"active": bool(active)}


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    with db_conn() as db:
        db.execute("UPDATE schedules SET active=0 WHERE id=?", (schedule_id,))
    return {"ok": True}


@app.post("/api/schedules/{schedule_id}/complete")
def complete_schedule(schedule_id: int, data: OccurrenceCompleteIn):
    try:
        due = date.fromisoformat(data.occurrence_key)
    except ValueError:
        raise HTTPException(400, "Geçersiz işlem tarihi")
    with db_conn() as db:
        schedule = db.execute("SELECT * FROM schedules WHERE id=? AND active=1", (schedule_id,)).fetchone()
        if schedule is None:
            raise HTTPException(404, "Aktif tekrarlanan işlem bulunamadı")
        dates = schedule_dates(schedule, due, due)
        if due not in dates:
            raise HTTPException(400, "Bu tarih tekrarlanan işlem takvimine uymuyor")
        if occurrence_done(db, "schedule", schedule_id, data.occurrence_key):
            raise HTTPException(409, "Bu işlem zaten tamamlanmış")
        override = occurrence_override(db, "schedule", schedule_id, data.occurrence_key)
        actual_due = date.fromisoformat(override["due_date"]) if override.get("due_date") else due
        actual_account = int(override.get("account_id") or schedule["account_id"])
        actual_amount = float(override.get("amount") or schedule["amount"])
        actual_category = override.get("category") if override.get("category") is not None else schedule["category"]
        actual_description = override.get("description") if override.get("description") is not None else schedule["name"]
        entry_id = add_entry(db, entry_date=actual_due, entry_type=schedule["entry_type"],
            account_id=actual_account, amount=actual_amount,
            category=actual_category, description=actual_description,
            source_type="schedule", source_id=schedule_id, occurrence_key=data.occurrence_key)
        db.execute("DELETE FROM occurrence_overrides WHERE source_type='schedule' AND source_id=? AND occurrence_key=?",
                   (schedule_id, data.occurrence_key))
        db.execute(
            "INSERT INTO completed_occurrences(source_type,source_id,occurrence_key,entry_id,completed_at) VALUES('schedule',?,?,?,?)",
            (schedule_id, data.occurrence_key, entry_id, now_iso()),
        )
    return {"id": entry_id}


@app.post("/api/installments")
def create_installment(data: InstallmentIn):
    if data.current_installment > data.total_installments:
        raise HTTPException(400, "Mevcut taksit numarası toplam taksit sayısından büyük olamaz")
    with db_conn() as db:
        ensure_account(db, data.account_id)
        cur = db.execute(
            """INSERT INTO installment_plans(name,total_amount,account_id,category,total_installments,
               current_installment,statement_date,created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (data.name.strip(), data.total_amount, data.account_id, data.category.strip(),
             data.total_installments, data.current_installment, data.statement_date.isoformat(), now_iso()),
        )
        plan_id = int(cur.lastrowid)
        first_date = add_months(data.statement_date, -(data.current_installment - 1))
        per = round(data.total_amount / data.total_installments, 2)
        historical_installments_added = 0
        for no in range(1, data.current_installment):
            due = add_months(first_date, no - 1)
            if due >= date.today():
                continue
            amount = round(data.total_amount - per * (no - 1), 2) if no == data.total_installments else per
            entry_id = add_entry(db, entry_date=due, entry_type="expense", account_id=data.account_id,
                amount=amount, category=data.category.strip(),
                description=f"{data.name.strip()} · {no}/{data.total_installments}",
                source_type="installment", source_id=plan_id, occurrence_key=str(no))
            db.execute(
                "INSERT INTO completed_occurrences(source_type,source_id,occurrence_key,entry_id,completed_at) VALUES('installment',?,?,?,?)",
                (plan_id, str(no), entry_id, now_iso()),
            )
            historical_installments_added += 1
    return {
        "id": plan_id,
        "historical_installments_added": historical_installments_added,
        "planned_installments": data.total_installments - historical_installments_added,
    }


@app.get("/api/installments")
def list_installments():
    with db_conn() as db:
        rows = db.execute(
            """SELECT p.*,a.name AS account_name,
                      (SELECT COUNT(*) FROM completed_occurrences c
                       WHERE c.source_type='installment' AND c.source_id=p.id) AS completed_installments
               FROM installment_plans p JOIN accounts a ON a.id=p.account_id ORDER BY p.id DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/installments/{plan_id}/complete")
def complete_installment(plan_id: int, data: OccurrenceCompleteIn):
    try:
        no = int(data.occurrence_key)
    except ValueError:
        raise HTTPException(400, "Geçersiz taksit numarası")
    with db_conn() as db:
        plan = db.execute("SELECT * FROM installment_plans WHERE id=?", (plan_id,)).fetchone()
        if plan is None:
            raise HTTPException(404, "Taksit planı bulunamadı")
        if no < 1 or no > int(plan["total_installments"]):
            raise HTTPException(400, "Taksit numarası planın kapsamı dışında")
        if occurrence_done(db, "installment", plan_id, str(no)):
            raise HTTPException(409, "Bu taksit zaten işaretlenmiş")
        current_date = date.fromisoformat(plan["statement_date"])
        first_date = add_months(current_date, -(int(plan["current_installment"]) - 1))
        due = add_months(first_date, no - 1)
        per = round(float(plan["total_amount"]) / int(plan["total_installments"]), 2)
        amount = round(float(plan["total_amount"]) - per * (no - 1), 2) if no == int(plan["total_installments"]) else per
        key = str(no)
        override = occurrence_override(db, "installment", plan_id, key)
        actual_due = date.fromisoformat(override["due_date"]) if override.get("due_date") else due
        actual_account = int(override.get("account_id") or plan["account_id"])
        actual_amount = float(override.get("amount") or amount)
        actual_category = override.get("category") if override.get("category") is not None else plan["category"]
        actual_description = override.get("description") if override.get("description") is not None else f"{plan['name']} · {no}/{plan['total_installments']}"
        entry_id = add_entry(db, entry_date=actual_due, entry_type="expense",
            account_id=actual_account, amount=actual_amount, category=actual_category,
            description=actual_description,
            source_type="installment", source_id=plan_id, occurrence_key=key)
        db.execute("DELETE FROM occurrence_overrides WHERE source_type='installment' AND source_id=? AND occurrence_key=?",
                   (plan_id, key))
        db.execute(
            "INSERT INTO completed_occurrences(source_type,source_id,occurrence_key,entry_id,completed_at) VALUES('installment',?,?,?,?)",
            (plan_id, str(no), entry_id, now_iso()),
        )
    return {"id": entry_id}


@app.get("/api/forecast")
def forecast(month: str | None = None):
    month = month or date.today().strftime("%Y-%m")
    try:
        start = date.fromisoformat(month + "-01")
    except ValueError:
        raise HTTPException(400, "Ay YYYY-AA formatında olmalı")
    end = add_months(start, 1) - timedelta(days=1)
    with db_conn() as db:
        items = collect_planned(db, start, end)
        accounts = {r["id"]: dict(r) for r in db.execute("SELECT * FROM accounts").fetchall()}
        for item in items:
            account = accounts.get(item["account_id"])
            item["account_name"] = account["name"] if account else "?"
            item["asset_code"] = account["asset_code"] if account else "TRY"
    return items


@app.get("/api/quotes")
def list_quotes():
    with db_conn() as db:
        quotes = get_quote_map(db)
    return {code: {**q, "label": ASSETS.get(code, code)} for code, q in quotes.items()}


@app.post("/api/quotes/refresh")
async def force_refresh_quotes():
    return await refresh_quotes()


def amount_to_try(asset_code: str, amount: float, quotes: dict[str, dict]) -> float:
    if asset_code == "TRY":
        return float(amount)
    quote = quotes.get(asset_code)
    return float(amount) * float(quote["bid"]) if quote else 0.0


@app.get("/api/dashboard")
def dashboard(month: str | None = None):
    month = month or date.today().strftime("%Y-%m")
    try:
        month_start = date.fromisoformat(month + "-01")
    except ValueError:
        raise HTTPException(400, "Ay YYYY-AA formatında olmalı")
    month_end = add_months(month_start, 1) - timedelta(days=1)
    with db_conn() as db:
        quotes = get_quote_map(db)
        sums = db.execute(
            """SELECT e.entry_type,e.amount,a.asset_code FROM entries e JOIN accounts a ON a.id=e.account_id
               WHERE substr(e.entry_date,1,7)=? AND e.entry_type IN ('income','expense')
               AND COALESCE(e.category,'') NOT IN ('Borç ödemesi','Borç tahsilatı','Borç alınan para','Verilen borç')""",
            (month,),
        ).fetchall()
        totals = {"income": 0.0, "expense": 0.0}
        for r in sums:
            totals[r["entry_type"]] += amount_to_try(r["asset_code"], float(r["amount"]), quotes)
        planned = collect_planned(db, month_start, month_end)
        accounts_by_id = {r["id"]: r for r in db.execute("SELECT * FROM accounts").fetchall()}
        planned_income = sum(amount_to_try(accounts_by_id[x["account_id"]]["asset_code"], x["amount"], quotes)
                             for x in planned if x["entry_type"] == "income")
        planned_expense = sum(amount_to_try(accounts_by_id[x["account_id"]]["asset_code"], x["amount"], quotes)
                              for x in planned if x["entry_type"] == "expense")
        for item in planned:
            account = accounts_by_id.get(item["account_id"])
            item["account_name"] = account["name"] if account else "?"
            item["asset_code"] = account["asset_code"] if account else "TRY"
        account_rows = db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        accounts = []
        total_net_worth = 0.0
        stale_accounts = False
        for row in account_rows:
            bal = account_balance(db, row)
            val, stale = value_in_try(row["asset_code"], bal, quotes)
            signed_value = -val if row["kind"] == "credit_card" else val
            total_net_worth += signed_value
            stale_accounts = stale_accounts or stale
            accounts.append({"id": row["id"], "name": row["name"], "kind": row["kind"],
                             "asset_code": row["asset_code"], "balance": bal,
                             "value_try": signed_value, "rate_stale": stale})
        debt_rows = db.execute("SELECT direction,asset_code,outstanding FROM debts WHERE outstanding>0").fetchall()
        debt_net_worth = 0.0
        for debt in debt_rows:
            amount_try = amount_to_try(debt["asset_code"], float(debt["outstanding"]), quotes)
            debt_net_worth += amount_try if debt["direction"] == "receivable" else -amount_try
            if debt["asset_code"] != "TRY" and debt["asset_code"] not in quotes:
                stale_accounts = True
        total_net_worth += debt_net_worth
        db.execute(
            "INSERT INTO net_worth_snapshots(snapshot_date,total_value,rate_stale) VALUES(?,?,?) "
            "ON CONFLICT(snapshot_date) DO UPDATE SET total_value=excluded.total_value,rate_stale=excluded.rate_stale",
            (date.today().isoformat(), round(total_net_worth, 2), int(stale_accounts)),
        )
        recent = db.execute(
            """SELECT e.*,a.name AS account_name,a.asset_code,ta.name AS target_account_name,
                      ta.asset_code AS target_asset_code,td.trade_type,td.asset_quantity,td.cash_amount,
                      td.asset_code AS traded_asset_code,td.cash_asset_code
               FROM entries e JOIN accounts a ON a.id=e.account_id
               LEFT JOIN accounts ta ON ta.id=e.target_account_id LEFT JOIN trade_details td ON td.entry_id=e.id
               ORDER BY e.entry_date DESC,e.id DESC LIMIT 8"""
        ).fetchall()
        quote_time = max((q["updated_at"] for q in quotes.values() if q["asset_code"] != "TRY"), default=None)
        quote_stale = any(q["is_stale"] for code, q in quotes.items() if code != "TRY")
    return {
        "month": month, "income": totals.get("income", 0), "expense": totals.get("expense", 0),
        "net": totals.get("income", 0) - totals.get("expense", 0),
        "planned_income": planned_income, "planned_expense": planned_expense,
        "forecast_net": totals.get("income", 0) + planned_income - totals.get("expense", 0) - planned_expense,
        "total_net_worth": total_net_worth, "debt_net_worth": debt_net_worth, "accounts": accounts,
        "upcoming": planned[:8], "recent": [dict(r) for r in recent],
        "quote_updated_at": quote_time, "quote_stale": quote_stale or stale_accounts,
    }
