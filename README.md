# Sales analytics

Pulls transactions from the internal API into a Postgres warehouse and answers
questions about them: how sales are moving, when the rush actually is, what sells
with what, and where the voids and discounts are concentrated.

Separate from **storehub sales by period**, which stays as it is. That tool
reconciles three monthly file exports and proves they tie; this one reads
receipt-level detail continuously and asks what the numbers mean. Different jobs,
different failure modes, so different projects — they can share the Postgres
server without sharing a schema.

## Running it

**The dashboard** — double-click **`run_dashboard.cmd`**, then work at
<http://localhost:8001>. Port 8001 rather than 8000, so this and the StoreHub
recon UI run side by side. Binds to 127.0.0.1, so it is reachable from this PC
only. Leave the console window open while you use it.

Six views:

| View | |
|---|---|
| **Overview** | sales, transactions, average ticket, branches trading, the daily series, branch ranking, **Opportunities**, estate split, payment mix |
| **Hours** | transactions and sales by hour, a day-of-week × hour heatmap, and cashier throughput |
| **Products** | top products by value and by units, items per basket, average price |
| **Exceptions** | flagged and soft-deleted rows, discounting by branch, and the BIR statutory relief — senior, PWD, solo parent, NAC — with voids |
| **Alliance products** | Branch, Date, Product ID, Product Name, Qty, Gross Sales, %, Cost, Tax, Gross Profit, GP % — one row per branch per day per product, with a CSV download |
| **StoreHub products** | Branch, Date, Product Name, Product Category, SKU ID, Total Items Sold, Total Sales, Total Sales Returned, Total Discount, Discount %, Item Net Sales, Average Cost, Average Net Sales, Gross Profit, Gross Profit % |

Every view shares one From/To, estate and branch filter, and the selection
travels with you: switching tabs, or using a quick-range chip, keeps the branches
you picked. Nothing is cached, so a figure on screen is what the tables hold at
that moment.

**The branch filter takes any number of branches.** Click it and you get a
searchable list with checkboxes — type `sm city` to narrow to those seven, then
**Select all** to take the filtered set, or **StoreHub only** / **Alliance only**
for a whole estate. Ticking nothing means all of them, which the panel says
rather than leaving you to guess. The selection is carried as repeated `store=`
parameters, so the browser submits it natively and a filtered view can still be
bookmarked or pasted to someone else. An older single-branch link keeps working.

Branches outside the chosen estate are dropped from the list rather than shown
and ignored — ticking one while an estate filter is on would AND to nothing, and
an empty dashboard with no explanation is the worst kind of answer.

### Who can open it

Out of the box the dashboard is open and bound to 127.0.0.1, so only this PC
reaches it. To let other people in, turn on Google sign-in:

```bash
python setup_google.py
```

It walks through the Google Cloud console steps, prints the exact redirect URI to
paste there, and asks for the client id, the client secret (hidden as you type)
and at least one **admin email**. The secret goes straight into `.env`, which is
gitignored; it is never echoed and never put on a command line.

Once configured, every page requires a signed-in account:

| Who | What they get |
|---|---|
| Not signed in | the sign-in page; every other path, including the CSVs and the analysis endpoint, redirects there |
| New account | created **PENDING** — a holding page saying an administrator has been asked to approve them, and nothing else |
| Approved | the dashboard |
| Admin | the dashboard plus **Users**, with a count of who is waiting |
| Rejected / suspended | told so plainly; signing in again does not reset it |

An address in `ADMIN_EMAILS` is approved as ADMIN on its first sign-in. Without
that nobody could approve anybody and the first person to install this would be
locked out of their own dashboard.

Approval is a real decision, not a formality: this dashboard shows the group's
entire sales position, so the default for an unknown face is no. Every decision
records who made it and when, and a rejected address that signs in again gets the
same answer rather than a fresh PENDING row.

Turning it off again with `python setup_google.py --off` reopens the dashboard
but **keeps every account and approval**, so switching it back on restores
exactly who had access.

Two things worth knowing:

- **The session cookie is HttpOnly, SameSite=Lax and HMAC-signed**, and lasts 12
  hours. A tampered or unsigned cookie is rejected and lands on the sign-in page.
  **Signing out is real, not just a cleared cookie.** Every signed-in browser is
  a row in `app_session` (holding only a SHA-256 of the cookie's token), and a
  cookie whose row is gone is refused. *Sign out* ends this device only;
  **Account** in the header lists your signed-in devices and has *Sign out of
  all other devices* and *Sign out everywhere*. Being rejected or suspended
  ends all of a user's sessions. Expired rows are cleared on each sign-in.
  When `PUBLIC_BASE_URL` is `https://…` it is also marked **Secure**, so the
  browser never sends it over plain http. Every response carries
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and
  `Referrer-Policy: same-origin`.
- **Sign-in does not by itself expose the dashboard to the network** — see below.

### Letting colleagues reach it

```bash
run_dashboard_network.cmd
```

That binds to every interface instead of 127.0.0.1, and prints the addresses
people should type. **It refuses to start unless Google sign-in is configured**,
because an open dashboard on the network publishes every branch's sales, costs
and margins to anyone who can reach the port. That is a decision worth making on
purpose, so it cannot be reached by typing a flag. (`--i-accept-no-sign-in`
overrides it for a closed lab; it is deliberately undocumented in `--help`.)

Windows Firewall will probably still block the port. If colleagues cannot
connect, run this once from an **administrator** prompt — it is a change to your
machine's security settings, so it is yours to make, not something this project
does behind your back:

```
netsh advfirewall firewall add rule name="Sales Analytics 8001" dir=in action=allow protocol=TCP localport=8001
```

To start in network mode at every logon: `install_autostart.cmd network`.

One thing to get right in the Google console: the **Authorised redirect URI** has
to match the address people actually type, character for character. If everyone
will use `http://your-pc-name:8001`, that is what goes in the console and what
`setup_google.py` should be given as the base URL — not `localhost`. A mismatch
shows up as `redirect_uri_mismatch` at sign-in.

### Deploying on a Linux server

On a server the dashboard should not face the network itself. It listens on
`127.0.0.1:8087`, and Nginx in front of it is the only way in — adding HTTPS,
so sales figures and the session cookie are never sent unencrypted.

```
browser ──https:443──▶ Nginx ──http──▶ 127.0.0.1:8087  analytics_web.py ──▶ PostgreSQL
```

Everything needed is in `deploy/linux/`:

| File | What it does |
|---|---|
| `sales-analytics.service` | systemd unit: runs the dashboard on 127.0.0.1:8087 with `--behind-proxy`, restarts it on failure, as an unprivileged `salesapp` user |
| `nginx-sales-analytics.conf` | HTTPS on 443, http→https redirect, HSTS, proxies to 8087 |
| `backup_db.sh` | compressed `pg_dump` of the warehouse, keeps 30 days |
| `sales-analytics-backup.service` / `.timer` | runs the backup nightly at 02:00 |

In order, on the server:

```bash
# 1. Code, an unprivileged user, and a virtualenv
sudo useradd --system --home /opt/sales-analytics --shell /usr/sbin/nologin salesapp
sudo git clone https://github.com/dataanalyst1029/Sales-Analytics /opt/sales-analytics
cd /opt/sales-analytics
sudo python3 -m venv .venv
sudo .venv/bin/pip install psycopg[binary] requests
#    then copy in .env and db/.env (never committed), and:
sudo chown -R salesapp:salesapp /opt/sales-analytics
sudo chmod 600 .env db/.env

# 1b. Database tables -- also re-run after every git pull that adds a migration.
#     The dashboard will not sign anyone in against an out-of-date schema.
cd db && sudo -u salesapp npm ci && sudo -u salesapp npx prisma migrate deploy && cd ..

# 2. Sign-in, with the https address people will type -- no port
sudo -u salesapp .venv/bin/python setup_google.py
#    Base URL: https://sales.example.com
#    Google console redirect URI: https://sales.example.com/auth/callback

# 3. The dashboard
sudo cp deploy/linux/sales-analytics.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sales-analytics

# 4. Nginx -- edit the server name and certificate paths first
sudo cp deploy/linux/nginx-sales-analytics.conf /etc/nginx/conf.d/sales-analytics.conf
sudo nginx -t && sudo systemctl reload nginx

# 5. Nightly backups
sudo mkdir -p /var/backups/sales-analytics
sudo chown salesapp:salesapp /var/backups/sales-analytics
sudo cp deploy/linux/sales-analytics-backup.* /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sales-analytics-backup.timer
sudo systemctl start sales-analytics-backup      # once now, to prove it works
```

Open 443 (and 80, for the redirect) in the server firewall — **not** 8087:
`sudo ufw allow 443/tcp && sudo ufw allow 80/tcp`, or on RHEL-family
`sudo firewall-cmd --permanent --add-service=https --add-service=http && sudo firewall-cmd --reload`.

`--behind-proxy` refuses to start unless Google sign-in **and**
`PUBLIC_BASE_URL` are set: the socket is 127.0.0.1, but the audience is the
network, so it is held to the same rule as `--host 0.0.0.0`. `PUBLIC_BASE_URL`
also pins the Google redirect to the real address instead of trusting whatever
`Host` header arrives, and an `https://` value is what marks the cookie Secure.

**Staying up under load.** No single request, user or query can take the
dashboard down for everyone else:

| Layer | Limit | What the user sees past it |
|---|---|---|
| Nginx | 5 requests/s per visitor (burst 20), 10 connections per visitor | `429` — slow down |
| App | at most `MAX_CONCURRENT_REQUESTS` (8) requests working at once; the rest queue for 30 s | "The dashboard is busy — reload" |
| Postgres | any query past `QUERY_TIMEOUT_SECONDS` (60) is cancelled | "That took too long — try a shorter date range" |
| App | database unreachable, or any bug in a page | a plain error page; the traceback goes to `journalctl -u sales-analytics`, never to the browser |
| systemd | 1 GB memory, 256 threads, 2 CPUs | the dashboard alone is restarted within 5 s; the server is untouched |

Eight at once also means the dashboard never holds more than eight Postgres
connections, well inside the default limit of 100. Raise both environment values
in the service file if many people use it at the same time.

**Backups.** Nothing in this system deletes sales history, so the database is
the only copy of it. `backup_db.sh` keeps 30 nightly dumps in
`/var/backups/sales-analytics` (`KEEP_DAYS` to change it). Copy that directory
off the server as well. Restore with
`pg_restore --clean --if-exists -d "<DATABASE_URL>" <file>.dump`.

### The analysis panels

Every tab carries one, each answering that tab's own question. They are computed
by `insights.py` from your own figures — no external service, no API key, nothing sent anywhere, and every claim
carries the numbers it came from.

| Tab | What its panel works out |
|---|---|
| **Overview** | why the weakest day was weak, split into footfall vs basket size, the products that went missing, standing weekday patterns, and pairs that already sell together |
| **Hours** | how much of the day rides on three hours, what the quiet hours cost, lunch against dinner, and the spread in cashier throughput |
| **Products** | how few lines make half the sales, what the tail costs, which products are most often bought **alone**, and what is moving within the range |
| **Exceptions** | discount and statutory-relief outliers measured against the group median, flag clusters, and unposted receipts |

Each tab ends with a **“Do this first”** lever — the one action its data most
supports, sized in pesos. The sizing is arithmetic on a stated assumption, never
a forecast: it answers *“if this moved by X, what is that worth?”* with X written
on the face of it, so the assumption can be argued with rather than hidden.
Nothing here knows whether an offer will work; it knows what the prize is if it
does, which is what decides whether the offer is worth designing.

Growth is always sized against the thing's **own** current level. An early draft
sized the quiet-hours lever against the lunch peak and produced ₱17M from a
₱1M base — it was quietly assuming a 06:00 hour would reach 40% of lunch, a 40×
increase. A prize that large would have been acted on, and it was fiction.

The Overview panel reasons the way a trading day decomposes, `sales =
transactions × average ticket`, because the two halves call for opposite
responses: an attachment offer is wasted when nobody came in, and a footfall
campaign is wasted when the tills were busy but the baskets were thin. It reports
three cases — footfall, basket, or both — and never claims one when the evidence
shows the other also moved. The two effects are computed off the same baseline as
the expected figure, so they sum to the shortfall exactly rather than
approximately.

Four things it deliberately will not do:

- **No baseline from the whole range.** This feed grew from ~18k transactions a
  month to ~584k, so a year-wide average would mark every early day a disaster
  and every recent one a triumph. Each day is judged against the median of nearby
  days of the same weekday, which moves with the trend.
- **Never flags the newest day.** It is still being collected, so it would raise
  a false alarm every single day.
- **Separates missing data from weak trading.** A day under 35% of its expected
  level is reported as a likely loading gap, not a sales problem — a branch does
  not lose 90% of a day and stay open, and sending someone to discount their way
  out of a data gap helps nobody.
- **No causes it cannot see.** The warehouse knows nothing about weather,
  holidays or a competitor opening, so no finding claims them.
- **No measure on rows that cannot carry it.** The Hours panel states how many
  rows have a clock and reads only those. The Products panel says so when the
  range holds sale-level rows with no line detail. The Exceptions panel drops the
  receipt-register findings entirely when the page is filtered to StoreHub,
  because that register covers the Alliance estate only.
- **Outliers are questions, not verdicts.** A branch discounting at twice the
  group median gets named, alongside the legitimate explanations that look
  identical from here.

### The two product reports come from the API's own report endpoints

Both product tabs read purpose-built endpoints, not anything derived from the
raw transaction feed:

| Tab | Endpoint | Rows | Carries |
|---|---|---|---|
| **Alliance products** | `/sales-summary` | 1,556,134 (Jan 2025 → Sep 2026) | product id, name, qty, gross sales, **cost, tax, gross profit** |
| **StoreHub products** | `/storehub-product-movement` | 142,028 (Jan → Sep 2026) | **product category, SKU**, items sold, sales, returns, discount, net sales, average cost, gross profit |

Both are loaded in full and tie exactly to the API's own totals, with 0 failed
windows. The StoreHub tab matches the portal's own "StoreHub Product Movement"
view row for row — 7,517 records for January 2026 on both sides, same figures to
the centavo.

`/sales-summary` reaches back to **January 2025**, nearly a year earlier than the
transaction feed, so year-on-year comparison is possible on the Alliance side
even though `/transactions` starts in October 2025. Assuming one source's
coverage applies to another is the same mistake as assuming a guessed path list
is the whole API — both cost a rebuild here.

They land in their own tables, `sales_summary` and `product_movement`, loaded by
`ingest_reports.py`. Kept separate from `transaction` deliberately: the grain is
different — already aggregated to one row per branch, day and product — and the
two sources must stay independently comparable, because disagreeing with each
other is exactly the sort of thing worth noticing. The API even publishes its own
`/reconciliation` endpoint that ties them together.

```bash
python ingest_reports.py --dataset both --recent 7
python ingest_reports.py --status
```

**These endpoints were missed on the first pass**, and it cost real work. The
discovery probe tried about thirty guessed paths, found six, and the reports were
then built the hard way out of `/transactions` — which carries no cost, no tax
and no category, so those columns were reported as impossible and a `costs.csv` /
`products.csv` mechanism was built to supply them by hand. All of that is now
deleted: the figures were in the API the whole time, on paths the probe's list
did not contain (`/sales-summary`, `/sales-by-period`, `/sales-book`,
`/storehub-product-movement`, `/reconciliation`, `/exports`).

The lesson is in `probe_api.py`'s candidate list, which now includes them:
a fixed list of guesses is a floor on what exists, never a ceiling. Where a
portal shows a figure the API "does not have", the endpoint is the thing to go
looking for.

### Coverage is stated on the page

Selecting 44 branches and seeing 4 in a report looks like a broken filter. Both
product tabs now say which it is — how many branches in scope appear in this
report, how many traded without appearing, and how many did not trade at all.

### Two families of tab, two sources

| | Reads | Named by |
|---|---|---|
| **Overview, Hours, Products, Exceptions** | `transaction`, `transaction_item`, `receipt` — receipt-level detail | `load_products.py` (Alliance SKUs) and `map_storehub_products.py` (StoreHub Mongo ids) |
| **Alliance products, StoreHub products** | `sales_summary`, `product_movement` — the API's own daily reports | the endpoints themselves |

The report endpoints supersede the CSV-lookup workarounds, **not** the two
product-naming scripts. Those still name 1,799,890 of 1,817,554 line items, and
without them the basket analysis, the product mix and "where the units went
missing" would all show Mongo ids. Re-run them after a wider transaction
backfill, or after a new recon conversion.

**Keeping it current** — `run_daily_update.cmd` pulls the last 6 days of all
four datasets: transactions, receipts, sales summary and product movement. Safe
to run as often as you like, because every loader upserts on the API's own UUID —
a day already loaded is corrected rather than duplicated. Six days rather than
one, so a late-posted figure is caught and a couple of missed days catch
themselves up.

**Running itself** — `install_autostart.cmd` registers two Task Scheduler
entries: the dashboard at logon, and the update at 06:30 daily. Both run as you,
in your own session, needing no admin rights and touching nothing system-wide.
`uninstall_autostart.cmd` removes them. Without this the dashboard stops at every
reboot and the data quietly goes stale, which is how a working tool becomes a
misleading one.

**A wider load** — `python ingest.py --dataset both --from 2026-01-01 --to 2026-03-31`.
`python ingest.py --status` says what is loaded and names any window that failed.

## Status

| | |
|---|---|
| `setup_env.py` | writes `.env`, proves the key against `/transactions`. Done. |
| `api.py` | HTTP client — auth, retries, pagination. Done. |
| `probe_api.py` | API shape discovery. Done. |
| `db/prisma/schema.prisma` | 11 tables, migrated. Done. |
| `ingest.py` | day-by-day idempotent loader for transactions and receipts. Done. |
| `ingest_reports.py` | loader for `/sales-summary` and `/storehub-product-movement`. Done. |
| `load_products.py` | the 440-SKU catalogue, linked to lines. Done. |
| `map_storehub_products.py` | names the StoreHub product ids. Done. |
| `insights.py` | the analysis panel behind every tab. Done. |
| `auth.py` | Google sign-in, sessions, the approval gate. Done. |
| `setup_google.py` | turns sign-in on; asks for the credentials. Done. |
| `analytics_web.py` | the six-view dashboard. Done. |
| `run_dashboard.cmd` | start the dashboard with a console window. |
| `run_dashboard_quiet.cmd` | the same, windowless, for the scheduled task. |
| `run_daily_update.cmd` | pull the last 6 days of all four datasets. |
| `install_autostart.cmd` | dashboard at logon, update at 06:30. `uninstall_autostart.cmd` undoes it. |

### How the StoreHub products got their names

StoreHub line items carry Mongo ObjectIds and no name, and the API's `/products`
catalogue covers only the Alliance SKUs — `/products?search=<objectid>` really
does return nothing, and there is no `/menu`, `/items` or detail route that says
more.

The names were recovered from the recon tool's `report.json` files instead, by
matching on the numbers. For one branch on one day, this warehouse holds
(product id → units, value) and the recon report holds (product name → units,
value). Where a (units, value) pair is unique on both sides that day, the id and
the name are the same product — two products would have to sell an identical
number of units for an identical amount, in the same branch on the same day, to
be confused.

Each branch-day is an independent vote. Across 457 branch-days, 84% of id-days
matched, and the leading mappings are unanimous: `Iced Tea 16oz` agreed on
432 of 432 days, `Pork BBQ Meal` 426 of 426, with no dissent. 266 of 344 ids were
named this way; 1,813,317 lines now carry a name and 4,237 do not.

The 47 ids left ambiguous are deliberately unnamed — each was seen on a single
day, which is not enough to rule out coincidence. A visible id is better than a
wrong name on a sales report. Re-run `python map_storehub_products.py` after any
new recon conversion and more of them will resolve.

## What the API actually contains

Probed 18 Sep 2026 against `https://datahub.ribshack.info/api/v1`. Endpoints that
exist: `/transactions`, `/receipts`, `/products`, `/users`, `/health`,
`/analytics/summary`. No `/stores`, and nothing behind the `transactions:export`
scope that answers to an obvious name.

**1,565,612 transactions, Oct 2025 to Sep 2026.** Monthly counts jump from ~18k
(late 2025) to ~584k (Aug 2026), which is a change in what is being fed in, not a
change in trade.

### It is two estates in one table, not one

| Feed | Branches | Detail |
|---|---|---|
| `rawData.source = storehub-api` | 20 `Ribshack …` branches — the same estate the recon tool converts | Full StoreHub object: line items, payments, register, terminal, cashier, invoice number, service charge, and every statutory discount (senior, PWD, solo parent, athlete, medal of valor) |
| Alliance export (`rawData` has `Account ID`) | 44 others — `SM SEASIDE`, `ROB GALLERIA`, `KCC COTABATO` … | Flat only: amount, tender, cashier, `Ref #`, `TM#`, and a **date with no time** |

On 16 Sep these were 6,935 and 14,307 rows. **Zero `transactionId` overlap**, so
they are different outlets rather than the same sales counted twice. One name
appears on both sides — `METRO AYALA CEBU` vs `Ribshack - Metro Ayala Cebu`, 6
rows — which is worth confirming is not a genuine double-count.

A third shape covers Oct 2025 to ~Apr 2026: `productName` populated per row, real
timestamps, no StoreHub block.

### Five things that will bite if they are not handled

**`transactionDate` is Manila local time wearing a `Z`.** It is exactly 8 hours
ahead of `rawData.storehub.transactionTime` on every row. Treating it as real UTC
shifts every sale 8 hours and moves late-evening trade into the next day.

**Jul and Aug 2026 have no time of day at all** — every row sits at
`00:00:00.000Z`. Those two months are 767k transactions, roughly half the
dataset, and no hourly or day-part analysis is possible for them.

**`transactionId` is not unique.** 333 collisions inside a single day on the
Alliance feed. The UUID `id` is the only safe key.

**`startDate`/`endDate` are the only date filters that work.** `from`/`to`,
`dateFrom`/`dateTo` and `date` are accepted, silently ignored, and return all
1.5M rows — a loader using them would look like it was filtering while pulling
everything.

**Both bounds are inclusive of the midnight instant**, so `[Aug-01, Sep-01]` and
`[Sep-01, Oct-01]` both contain Sep 1's date-only rows. Summing the months
overshoots the true total by 29,180. Idempotent upsert on `id` absorbs this.

Also: `amount` is a **string**, `limit` works up to at least 5000 (314 pages for
a full pull), and `isDeleted` / `isFlagged` / `flagReason` give the exceptions
work a head start.

## The warehouse

Eight tables, migrated by Prisma (`db/prisma/schema.prisma`), in their own
`sales_analytics` database — not `storehub_recon`, which is the audited
month-end position and must not move. This one is rebuildable from the API at
any time.

```
store ──┬── transaction ──┬── transaction_item ── product
        │                 └── payment
        └── receipt                                 load_run
```

Which view each answers:

| Question | What it reads |
|---|---|
| Sales & trends | `transaction` by `business_date` × `store` |
| Hours & staffing | `transaction.local_hour` × day of week × `cashier_name`, timed rows only |
| Products & baskets | `transaction_item`, with items-per-basket from its parent |
| Exceptions | `transaction.is_flagged` / `is_deleted` / `discount_amount`, and `receipt` for statutory relief and voids |

Four decisions are load-bearing, and each is commented at the column it explains:

**Business date, not timestamp.** Trading past midnight books to the day that
opened. Every daily figure keys on `business_date`, derived from Manila local
time — never from the API's mislabelled `Z`.

**`has_time` is explicit.** A row with no clock is marked as such and excluded
from hour-of-day work, rather than counted at midnight. Without it, 767k timeless
rows would invent a 00:00 spike larger than the real lunch peak.

**Nothing is deleted on load.** Flagged and soft-deleted rows stay as rows,
filtered out of sales measures and made the subject of the Exceptions view.
Deleting them would make the control questions unanswerable — which is the wall
the January T2 Mactan investigation ran into.

**`feed` is recorded, not inferred.** Three payload shapes share one endpoint, and
a measure valid on one is meaningless on another. The shape is stored so a query
can exclude what cannot answer it, instead of guessing from the date.

## Where this meets the existing tool

Once both hold the same month, the API's own total for a branch can be checked
against that branch's reconciled `product_total` in `storehub_recon`. If they
disagree, one of the two sources is wrong and it is worth knowing which — the
January T2 Airport Mactan gap (₱4,539.55 across 10 receipts the file exports
exclude) is exactly the kind of thing a second independent source settles
immediately. Not built yet; noted because it is nearly free once both sides exist.
