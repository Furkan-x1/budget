# Budget

A compact, single-user personal finance tracker for a private home network. One Docker container, one SQLite database, no external database, no account system, no telemetry.

## Features

- **Accounts:** cash, bank, credit-card and investment accounts.
- **Currencies:** TRY, USD, EUR, GBP, CHF, JPY, CAD, AUD, CNY, AED, SAR, RUB and KWD.
- **Gold:** gram, quarter, half, full and Cumhuriyet accounts. Metal accounts store physical quantity (grams or pieces), not a TL balance.
- **Manual buy/sell records:** select the asset account and the paying/receiving account, then enter the actual amount deducted or actually received and the exact asset quantity. Fees, tax, commission and spread are captured in the real cost/proceeds. Buys and sells are transfers, not income/expenses. Market bid/ask prices only value the current portfolio.
- **Credit cards:** card liability is kept as a separate account. Purchases raise the amount owed; paying the card is a transfer from the bank account and is not counted as a second expense.
- **Transactions:** income, expenses and account-to-account transfers.
- **Recurring transactions:** monthly, weekly or yearly income/expense schedules.
- **Installments:** enter total price, current installment (2/5) and statement date. The current and earlier installments are booked into their months; later installments are forecast. 1/1 is booked only in its selected month.
- **Custom categories:** add, rename and delete categories. Categories used in historical transactions are protected from deletion.
- **Debts and receivables:** optional initial account movement; partial payments and collections update the linked account and remaining balance. Debt principal movements do not inflate normal income/expense totals.
- **Savings goals:** link each goal to one account in the same currency/asset. Progress is calculated from that account balance, without creating a duplicate asset.
- **Reports:** monthly income/expense bars, expense categories and daily net-worth history.
- **Dark theme:** remembers the selected theme in the browser.
- **Individual editing:** change the date, amount, account, category and description of one future salary/payment/installment without changing the recurring template. Past generated transactions can be edited like other transactions.
- **Date format:** all full date fields use Turkish day-month-year format (GG.AA.YYYY).
- **Quotes:** background refresh every 15 minutes; last successful values are retained and stale quotes are labelled.
- **Backup:** download the SQLite database from the sidebar.
- **Optional LAN password:** set BUDGET_PASSWORD for one Basic Auth password.

## Clone and start

Because this repository is private, clone it while authenticated with an account that has access:

~~~bash
git clone https://github.com/Furkan-x1/budget.git
cd budget
cp .env.example .env
~~~

Edit .env and set BUDGET_PASSWORD to a long password when the LAN is shared. Then start the app:

~~~bash
docker compose up -d --build
~~~

## Update without losing records

The application stores records in the named Docker volume `budget_data`. Pulling a new version and rebuilding the container does not remove that volume. Before updating, download a database backup from the sidebar. Then, in the existing clone directory:

~~~bash
git pull
docker compose up -d --build
~~~

**Do not run `docker compose down -v`** when updating; the `-v` option removes the persistent database volume. The new version creates only its additional `occurrence_overrides` table if it does not already exist. Existing accounts, entries, schedules, installment plans, debts and goals are kept.

## Docker Compose

~~~bash
docker compose up -d --build
~~~

Open http://<docker-host-ip>:8088 on your local network. Data persists in the budget_data Docker volume. The container uses Europe/Istanbul for local dates.

Optional password: create .env beside compose.yaml:

~~~env
BUDGET_PASSWORD=choose-a-long-local-password
~~~

Then recreate the container:

~~~bash
docker compose up -d --force-recreate
~~~

Basic Auth username: budget.

## Local network and privacy

- Financial records stay in SQLite in the persistent volume.
- No bank integration, cloud account, external database, analytics, advertisements or telemetry.
- The only outbound traffic is a public market-price JSON feed. No personal or financial data is sent to the provider.
- The default feed is currency-api-tr (RATES_URL can override it). It provides currency and Turkish gold bid/ask quotes. FX reference prices and gold quotes can update at different speeds; they are estimates, not guaranteed execution prices.
- When refresh fails, the last successful quote is retained and marked stale. Manual trade amounts are never overwritten by market prices.
- Do not expose port 8088 to the public internet. On a shared LAN, set BUDGET_PASSWORD and restrict access with the host firewall.

## Accounting behavior

- Account balances remain in their native unit. Portfolio values use the latest available bid quote (the indicative price you could sell at).
- Buying decreases the selected payment account by the entered real cost and increases the asset account by the exact quantity. Selling decreases the asset quantity and increases the receiving account by the entered net proceeds.
- Fees, tax and spread are included in the manually entered cash amount. Buys and sells do not inflate income/expense totals.
- Payable debts reduce net worth; receivables increase it. Credit-card balances reduce net worth too.
- Net-worth history stores one snapshot per day when the dashboard is opened; that day's latest opening updates the snapshot.
- Historical non-TRY cashflow charts are converted using current quotes because historical daily FX rates are not stored. Net-worth snapshots preserve the TL value captured on each date.

## Backup and restore

Use the sidebar's database backup action. Keep a copy outside the Docker host. To restore, stop the container and replace budget.db in the named volume mounted at /data with the backup.

## Development

Run the API smoke test with:

~~~bash
python -m unittest discover -s tests
~~~

Run locally with:

~~~bash
python -m pip install -r requirements.txt
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
~~~
