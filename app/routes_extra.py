from .core import *

@app.post("/api/trades")
def create_trade(data: TradeIn):
    with db_conn() as db:
        asset_account = ensure_account(db, data.asset_account_id)
        cash_account = ensure_account(db, data.cash_account_id)
        if asset_account["id"] == cash_account["id"]:
            raise HTTPException(400, "Varlık ve ödeme hesapları farklı olmalı")
        if asset_account["asset_code"] == cash_account["asset_code"]:
            raise HTTPException(400, "İşlem için farklı türde iki hesap seçmelisin")
        if asset_account["kind"] == "credit_card" or cash_account["kind"] == "credit_card":
            raise HTTPException(400, "Varlık alım-satımında kredi kartı hesabı kullanılamaz")
        quotes = get_quote_map(db)
        if data.trade_type == "buy":
            source_id, target_id = cash_account["id"], asset_account["id"]
            source_amount, target_amount = data.cash_amount, data.asset_quantity
            category, label = "Varlık alımı", "Alım"
            cash_value_try = amount_to_try(cash_account["asset_code"], data.cash_amount, quotes)
        else:
            source_id, target_id = asset_account["id"], cash_account["id"]
            source_amount, target_amount = data.asset_quantity, data.cash_amount
            category, label = "Varlık satışı", "Satış"
            cash_value_try = amount_to_try(cash_account["asset_code"], data.cash_amount, quotes)
        entry_id = add_entry(db, entry_date=data.trade_date, entry_type="transfer",
            account_id=source_id, amount=source_amount, target_account_id=target_id,
            target_amount=target_amount, category=category,
            description=(data.description.strip() or f"{label}: {ASSETS[asset_account['asset_code']]}"),
            source_type="trade")
        db.execute(
            """INSERT INTO trade_details(entry_id,trade_type,asset_account_id,cash_account_id,asset_code,
               cash_asset_code,asset_quantity,cash_amount,cash_value_try) VALUES(?,?,?,?,?,?,?,?,?)""",
            (entry_id, data.trade_type, asset_account["id"], cash_account["id"],
             asset_account["asset_code"], cash_account["asset_code"], data.asset_quantity,
             data.cash_amount, cash_value_try))
    return {"id": entry_id, "cash_amount": data.cash_amount, "asset_quantity": data.asset_quantity}


@app.get("/api/categories")
def list_categories():
    with db_conn() as db:
        return [dict(r) for r in db.execute("SELECT * FROM categories WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()]


@app.post("/api/categories")
def create_category(data: CategoryIn):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Kategori adı boş olamaz")
    with db_conn() as db:
        try:
            cur = db.execute("INSERT INTO categories(name,kind) VALUES(?,?)", (name, data.kind))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Bu kategori zaten mevcut")
    return {"id": cur.lastrowid}


@app.patch("/api/categories/{category_id}")
def update_category(category_id: int, data: CategoryIn):
    name = data.name.strip()
    if not name:
        raise HTTPException(400, "Kategori adı boş olamaz")
    with db_conn() as db:
        row = db.execute("SELECT name FROM categories WHERE id=? AND active=1", (category_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Kategori bulunamadı")
        old_name = row["name"]
        try:
            db.execute("UPDATE categories SET name=?,kind=? WHERE id=?", (name, data.kind, category_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Bu isimde başka bir kategori zaten var")
        for table in ("entries", "schedules", "installment_plans"):
            db.execute(f"UPDATE {table} SET category=? WHERE category=?", (name, old_name))
    return {"ok": True}


@app.delete("/api/categories/{category_id}")
def delete_category(category_id: int):
    with db_conn() as db:
        row = db.execute("SELECT name FROM categories WHERE id=? AND active=1", (category_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Kategori bulunamadı")
        used = db.execute("SELECT COUNT(*) AS n FROM entries WHERE category=?", (row["name"],)).fetchone()["n"]
        used += db.execute("SELECT COUNT(*) AS n FROM schedules WHERE category=?", (row["name"],)).fetchone()["n"]
        used += db.execute("SELECT COUNT(*) AS n FROM installment_plans WHERE category=?", (row["name"],)).fetchone()["n"]
        if used:
            raise HTTPException(409, "Bu kategori geçmiş işlemlerde kullanılıyor; silmek yerine geçmişini koruyoruz.")
        db.execute("UPDATE categories SET active=0 WHERE id=?", (category_id,))
    return {"ok": True}


@app.get("/api/debts")
def list_debts():
    with db_conn() as db:
        return [dict(r) for r in db.execute("SELECT * FROM debts ORDER BY CASE WHEN outstanding=0 THEN 1 ELSE 0 END,due_date,id DESC").fetchall()]


@app.post("/api/debts")
def create_debt(data: DebtIn):
    if data.asset_code not in ASSETS:
        raise HTTPException(400, "Desteklenmeyen para/varlık türü")
    with db_conn() as db:
        opening_account = None
        if data.account_id is not None:
            opening_account = ensure_account(db, data.account_id)
            if opening_account["asset_code"] != data.asset_code:
                raise HTTPException(400, "Borçla bağlantılı hesap aynı para/varlık türünde olmalı")
            if opening_account["kind"] == "credit_card":
                raise HTTPException(400, "Borç açılışı için banka veya nakit hesabı seçmelisin")
        cur = db.execute(
            """INSERT INTO debts(name,counterparty,direction,asset_code,original_amount,outstanding,due_date,note,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (data.name.strip(), data.counterparty.strip(), data.direction, data.asset_code,
             data.amount, data.amount, data.due_date.isoformat() if data.due_date else None,
             data.note.strip(), now_iso()))
        debt_id = int(cur.lastrowid)
        if opening_account is not None:
            initial_type = "income" if data.direction == "payable" else "expense"
            initial_category = "Borç alınan para" if data.direction == "payable" else "Verilen borç"
            entry_id = add_entry(db, entry_date=date.today(), entry_type=initial_type,
                account_id=data.account_id, amount=data.amount, category=initial_category,
                description=f"{data.name.strip()} · ilk borç hareketi",
                source_type="debt_opening", source_id=debt_id)
            db.execute("INSERT INTO debt_openings(debt_id,entry_id) VALUES(?,?)", (debt_id, entry_id))
    return {"id": debt_id}


@app.post("/api/debts/{debt_id}/payment")
def pay_debt(debt_id: int, data: DebtPaymentIn):
    with db_conn() as db:
        debt = db.execute("SELECT * FROM debts WHERE id=?", (debt_id,)).fetchone()
        if debt is None:
            raise HTTPException(404, "Borç/alacak kaydı bulunamadı")
        account = ensure_account(db, data.account_id)
        if account["asset_code"] != debt["asset_code"]:
            raise HTTPException(400, f"Ödeme hesabı {debt['asset_code']} cinsinden olmalı")
        if account["kind"] == "credit_card":
            raise HTTPException(400, "Ödeme için nakit veya banka hesabı seçmelisin")
        if data.amount > float(debt["outstanding"]) + 0.000001:
            raise HTTPException(400, "Ödeme kalan borç/alacak tutarından büyük olamaz")
        entry_type = "expense" if debt["direction"] == "payable" else "income"
        category = "Borç ödemesi" if entry_type == "expense" else "Borç tahsilatı"
        entry_id = add_entry(db, entry_date=data.payment_date, entry_type=entry_type,
            account_id=data.account_id, amount=data.amount, category=category,
            description=f"{debt['name']} · ödeme/tahsilat", source_type="debt", source_id=debt_id)
        db.execute("INSERT INTO debt_payments(debt_id,entry_id,amount,created_at) VALUES(?,?,?,?)",
                   (debt_id, entry_id, data.amount, now_iso()))
        db.execute("UPDATE debts SET outstanding=MAX(0,outstanding-?) WHERE id=?", (data.amount, debt_id))
        outstanding = db.execute("SELECT outstanding FROM debts WHERE id=?", (debt_id,)).fetchone()["outstanding"]
    return {"id": entry_id, "outstanding": outstanding}


@app.delete("/api/debts/{debt_id}")
def delete_debt(debt_id: int):
    with db_conn() as db:
        if db.execute("SELECT 1 FROM debts WHERE id=?", (debt_id,)).fetchone() is None:
            raise HTTPException(404, "Borç/alacak kaydı bulunamadı")
        used = db.execute("SELECT COUNT(*) AS n FROM debt_payments WHERE debt_id=?", (debt_id,)).fetchone()["n"]
        used += db.execute("SELECT COUNT(*) AS n FROM debt_openings WHERE debt_id=?", (debt_id,)).fetchone()["n"]
        if used:
            raise HTTPException(409, "Hesap hareketi veya ödeme geçmişi bulunan borç/alacak silinemez")
        db.execute("DELETE FROM debts WHERE id=?", (debt_id,))
    return {"ok": True}


@app.get("/api/goals")
def list_goals():
    with db_conn() as db:
        rows = db.execute("""SELECT g.*,a.name AS account_name,a.asset_code AS account_asset_code
                              FROM goals g LEFT JOIN accounts a ON a.id=g.account_id ORDER BY g.id DESC""").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if row["account_id"]:
                account = ensure_account(db, row["account_id"])
                item["progress"] = max(0, account_balance(db, account))
            else:
                item["progress"] = 0
            item["progress_percent"] = min(100, item["progress"] / item["target_amount"] * 100)
            item["remaining"] = max(0, item["target_amount"] - item["progress"])
            result.append(item)
    return result


@app.post("/api/goals")
def create_goal(data: GoalIn):
    if data.asset_code not in ASSETS:
        raise HTTPException(400, "Desteklenmeyen para/varlık türü")
    with db_conn() as db:
        if data.account_id is not None:
            account = ensure_account(db, data.account_id)
            if account["asset_code"] != data.asset_code:
                raise HTTPException(400, "Hedef hesabının para/varlık türü hedefle aynı olmalı")
        cur = db.execute(
            "INSERT INTO goals(name,target_amount,asset_code,account_id,target_date,note,created_at) VALUES(?,?,?,?,?,?,?)",
            (data.name.strip(), data.target_amount, data.asset_code, data.account_id,
             data.target_date.isoformat() if data.target_date else None, data.note.strip(), now_iso()))
    return {"id": cur.lastrowid}


@app.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int):
    with db_conn() as db:
        cur = db.execute("DELETE FROM goals WHERE id=?", (goal_id,))
        if not cur.rowcount:
            raise HTTPException(404, "Hedef bulunamadı")
    return {"ok": True}


@app.get("/api/analytics")
def analytics(month: str | None = None, months: int = 12):
    selected = month or date.today().strftime("%Y-%m")
    try:
        selected_date = date.fromisoformat(selected + "-01")
    except ValueError:
        raise HTTPException(400, "Ay YYYY-AA formatında olmalı")
    months = min(max(months, 3), 36)
    with db_conn() as db:
        quotes = get_quote_map(db)
        monthly = []
        for offset in range(months - 1, -1, -1):
            start = add_months(selected_date, -offset)
            key = start.strftime("%Y-%m")
            rows = db.execute(
                """SELECT e.entry_type,e.amount,a.asset_code FROM entries e JOIN accounts a ON a.id=e.account_id
                   WHERE substr(e.entry_date,1,7)=? AND e.entry_type IN ('income','expense')
               AND COALESCE(e.category,'') NOT IN ('Borç ödemesi','Borç tahsilatı','Borç alınan para','Verilen borç')""", (key,)).fetchall()
            income = sum(amount_to_try(r["asset_code"], float(r["amount"]), quotes) for r in rows if r["entry_type"] == "income")
            expense = sum(amount_to_try(r["asset_code"], float(r["amount"]), quotes) for r in rows if r["entry_type"] == "expense")
            monthly.append({"month": key, "income": round(income, 2), "expense": round(expense, 2)})
        category_rows = db.execute(
            """SELECT e.category,e.amount,a.asset_code FROM entries e JOIN accounts a ON a.id=e.account_id
               WHERE substr(e.entry_date,1,7)=? AND e.entry_type='expense'
               AND COALESCE(e.category,'') NOT IN ('Borç ödemesi','Borç tahsilatı','Borç alınan para','Verilen borç')""", (selected,)).fetchall()
        category_totals: dict[str, float] = {}
        for row in category_rows:
            cat = row["category"] or "Kategorisiz"
            category_totals[cat] = category_totals.get(cat, 0) + amount_to_try(row["asset_code"], float(row["amount"]), quotes)
        snapshots = [dict(r) for r in db.execute(
            "SELECT * FROM net_worth_snapshots ORDER BY snapshot_date DESC LIMIT 365").fetchall()][::-1]
    return {"month": selected, "monthly": monthly,
            "categories": [{"name": k, "amount": round(v, 2)} for k, v in sorted(category_totals.items(), key=lambda x: -x[1])],
            "net_worth_history": snapshots,
            "note": "Geçmiş dövizli işlemlerin TL karşılığı mevcut kurlarla hesaplanır; net varlık grafiği ise kaydedildiği günün değerini korur."}


@app.get("/api/backup")
def backup():
    if not DB_PATH.exists():
        raise HTTPException(404, "Henüz veritabanı bulunamadı")
    return FileResponse(DB_PATH, media_type="application/octet-stream", filename="budget-backup.db")


class EntryUpdate(BaseModel):
    entry_date: date | None = None
    amount: float | None = Field(default=None, gt=0)
    target_amount: float | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=160)
    trade_asset_quantity: float | None = Field(default=None, gt=0)
    trade_cash_amount: float | None = Field(default=None, gt=0)


class OccurrenceUpdate(BaseModel):
    due_date: date | None = None
    amount: float | None = Field(default=None, gt=0)
    account_id: int | None = None
    category: str | None = Field(default=None, max_length=60)
    description: str | None = Field(default=None, max_length=160)


def update_entry_record(db: sqlite3.Connection, entry: sqlite3.Row, fields: dict) -> None:
    updates: dict = {}
    for key in ("entry_date", "category", "description"):
        if key in fields and fields[key] is not None:
            value = fields[key].isoformat() if key == "entry_date" else fields[key].strip()
            updates[key] = value

    trade = db.execute("SELECT * FROM trade_details WHERE entry_id=?", (entry["id"],)).fetchone()
    if trade:
        quantity = float(fields.get("trade_asset_quantity", trade["asset_quantity"]))
        cash_amount = float(fields.get("trade_cash_amount", trade["cash_amount"]))
        if "amount" in fields or "target_amount" in fields:
            raise HTTPException(400, "Alım/satım için varlık miktarı ve gerçek nakit tutarı ayrı düzenlenmeli")
        if quantity <= 0 or cash_amount <= 0:
            raise HTTPException(400, "Alım/satım tutarları sıfırdan büyük olmalı")
        if trade["trade_type"] == "buy":
            updates["amount"] = cash_amount
            updates["target_amount"] = quantity
        else:
            updates["amount"] = quantity
            updates["target_amount"] = cash_amount
        quotes = get_quote_map(db)
        cash_value_try = amount_to_try(trade["cash_asset_code"], cash_amount, quotes)
        db.execute(
            """UPDATE trade_details SET asset_quantity=?,cash_amount=?,cash_value_try=?
               WHERE entry_id=?""", (quantity, cash_amount, cash_value_try, entry["id"])
        )
    elif entry["source_type"] in ("debt", "debt_opening") and "amount" in fields:
        new_amount = float(fields["amount"])
        if entry["source_type"] == "debt":
            payment = db.execute("SELECT debt_id FROM debt_payments WHERE entry_id=?", (entry["id"],)).fetchone()
            if payment:
                debt = db.execute("SELECT * FROM debts WHERE id=?", (payment["debt_id"],)).fetchone()
                paid_other = db.execute(
                    "SELECT COALESCE(SUM(amount),0) AS total FROM debt_payments WHERE debt_id=? AND entry_id!=?",
                    (payment["debt_id"], entry["id"]),
                ).fetchone()["total"]
                if new_amount + float(paid_other) > float(debt["original_amount"]) + 0.000001:
                    raise HTTPException(400, "Düzenlenen ödemelerin toplamı borcun ilk tutarını aşamaz")
                db.execute("UPDATE debt_payments SET amount=? WHERE entry_id=?", (new_amount, entry["id"]))
                db.execute("UPDATE debts SET outstanding=MAX(0,original_amount-(SELECT COALESCE(SUM(amount),0) FROM debt_payments WHERE debt_id=?)) WHERE id=?",
                           (payment["debt_id"], payment["debt_id"]))
        else:
            opening = db.execute("SELECT debt_id FROM debt_openings WHERE entry_id=?", (entry["id"],)).fetchone()
            if opening:
                paid = db.execute("SELECT COALESCE(SUM(amount),0) AS total FROM debt_payments WHERE debt_id=?",
                                  (opening["debt_id"],)).fetchone()["total"]
                if new_amount + 0.000001 < float(paid):
                    raise HTTPException(400, "İlk borç tutarı, daha önce yapılan ödemelerin toplamından küçük olamaz")
                db.execute("UPDATE debts SET original_amount=?,outstanding=? WHERE id=?",
                           (new_amount, new_amount-float(paid), opening["debt_id"]))
        updates["amount"] = new_amount
    elif "amount" in fields:
        updates["amount"] = float(fields["amount"])

    if "target_amount" in fields and not trade:
        if entry["entry_type"] != "transfer":
            raise HTTPException(400, "Hedef tutar yalnızca transferlerde düzenlenebilir")
        updates["target_amount"] = float(fields["target_amount"])

    if updates:
        assignments = ", ".join(f"{key}=?" for key in updates)
        db.execute(f"UPDATE entries SET {assignments} WHERE id=?", (*updates.values(), entry["id"]))


@app.patch("/api/entries/{entry_id}")
def update_entry(entry_id: int, data: EntryUpdate):
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "Düzenlenecek alan gönderilmedi")
    with db_conn() as db:
        entry = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if entry is None:
            raise HTTPException(404, "İşlem bulunamadı")
        update_entry_record(db, entry, fields)
        updated = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    return {"ok": True, "entry": dict(updated)}


@app.get("/api/occurrences")
def list_occurrences(from_date: date | None = None, to_date: date | None = None):
    today = date.today()
    start = from_date or add_months(today.replace(day=1), -6)
    end = to_date or (add_months(today.replace(day=1), 12) - timedelta(days=1))
    if end < start:
        raise HTTPException(400, "Bitiş tarihi başlangıçtan önce olamaz")
    if (end - start).days > 1100:
        raise HTTPException(400, "Tek istekte en fazla yaklaşık 3 yıllık dönem listelenebilir")
    with db_conn() as db:
        items = collect_occurrences(db, start, end)
    return items


@app.patch("/api/occurrences/{source_type}/{source_id}/{occurrence_key}")
def update_occurrence(source_type: Literal["schedule", "installment"], source_id: int,
                      occurrence_key: str, data: OccurrenceUpdate):
    fields = data.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "Düzenlenecek alan gönderilmedi")
    with db_conn() as db:
        completed = db.execute(
            """SELECT e.* FROM completed_occurrences c JOIN entries e ON e.id=c.entry_id
               WHERE c.source_type=? AND c.source_id=? AND c.occurrence_key=?""",
            (source_type, source_id, occurrence_key),
        ).fetchone()
        if completed:
            patch = {
                "entry_date": fields.get("due_date"),
                "amount": fields.get("amount"),
                "category": fields.get("category"),
                "description": fields.get("description"),
            }
            patch = {k: v for k, v in patch.items() if v is not None}
            update_entry_record(db, completed, patch)
            return {"ok": True, "status": "completed", "entry_id": completed["id"]}

        if source_type == "schedule":
            schedule = db.execute("SELECT * FROM schedules WHERE id=?", (source_id,)).fetchone()
            if schedule is None:
                raise HTTPException(404, "Tekrarlanan işlem bulunamadı")
            try:
                canonical_date = date.fromisoformat(occurrence_key)
            except ValueError:
                raise HTTPException(400, "Tekrarlanan işlem anahtarı geçersiz")
            if canonical_date not in schedule_dates(schedule, canonical_date, canonical_date):
                raise HTTPException(404, "Bu tarih tekrarlanan işlem takviminde bulunamadı")
            base_account_id = schedule["account_id"]
        else:
            plan = db.execute("SELECT * FROM installment_plans WHERE id=?", (source_id,)).fetchone()
            if plan is None:
                raise HTTPException(404, "Taksit planı bulunamadı")
            try:
                no = int(occurrence_key)
            except ValueError:
                raise HTTPException(400, "Taksit numarası geçersiz")
            if no < 1 or no > int(plan["total_installments"]):
                raise HTTPException(404, "Bu taksit plan içinde bulunamadı")
            base_account_id = plan["account_id"]

        if "account_id" in fields and fields["account_id"] is not None:
            ensure_account(db, fields["account_id"])
        old = occurrence_override(db, source_type, source_id, occurrence_key)
        values = {
            "due_date": fields.get("due_date", old.get("due_date")),
            "amount": fields.get("amount", old.get("amount")),
            "account_id": fields.get("account_id", old.get("account_id") or base_account_id),
            "category": fields.get("category", old.get("category")),
            "description": fields.get("description", old.get("description")),
        }
        db.execute(
            """INSERT INTO occurrence_overrides(source_type,source_id,occurrence_key,due_date,amount,
               account_id,category,description,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_type,source_id,occurrence_key) DO UPDATE SET
               due_date=excluded.due_date,amount=excluded.amount,account_id=excluded.account_id,
               category=excluded.category,description=excluded.description,updated_at=excluded.updated_at""",
            (source_type, source_id, occurrence_key,
             values["due_date"].isoformat() if isinstance(values["due_date"], date) else values["due_date"],
             values["amount"], values["account_id"], values["category"], values["description"], now_iso()),
        )
    return {"ok": True, "status": "pending"}
