# Budget

A small, self-hosted personal finance tracker for a private home network. One Docker container, SQLite, and no external database or telemetry.

## First version
- Cash, bank, credit-card and investment accounts.
- TRY, USD, EUR, GBP, CHF, gram gold, quarter/half/full/Cumhuriyet gold and gram silver accounts.
- Income, expense and account-to-account transfers.
- Monthly, weekly and yearly recurring income/expenses.
- Installment plans: entering `2/5` books installments 1 and 2 in their respective months and forecasts 3–5. `1/1` is booked only in the selected month.
- Dashboard with monthly totals, account values, upcoming payments and cash-flow forecast.
- Background quote refresh, last-known quote fallback and stale quote indicator.
- SQLite database backup download.
- Optional single Basic Auth password for shared LANs.

## Run

```bash
docker compose up -d --build
```

Open `http://<docker-host-ip>:8088` from your local network.

Data persists in the `budget_data` Docker volume. For optional password protection, create `.env` beside `compose.yaml`:

```env
BUDGET_PASSWORD=choose-a-long-local-password
```

Then run `docker compose up -d --force-recreate`. Basic Auth username: `budget`.

## Network and privacy
All financial records stay in the local SQLite database. The only outbound request is a public price feed; no personal or financial data is sent to it. No analytics, telemetry, cloud account, advertising or bank connection is included. Do not forward port 8088 to the public internet. Set `BUDGET_PASSWORD` on shared LANs.

The default feed documents TCMB reference FX rates and market sources for Turkish gold quotes. FX and gold can update at different frequencies. All quotes are estimates, may be delayed, and are visibly marked stale when the provider indicates old data or a refresh fails. The last successfully stored quote is retained.

Historical transactions in non-TRY accounts are converted to TRY using the latest available quote in this first version. Credit-card purchases increase the outstanding card balance; a transfer from a bank account to the card account reduces that liability and is not counted as a second expense.

## Backup
Use **Veritabanı yedeği al** in the sidebar to download `budget-backup.db`. Keep the file outside the Docker host. To restore, stop the container and replace `budget.db` in the mounted `/data` volume with the backup.
