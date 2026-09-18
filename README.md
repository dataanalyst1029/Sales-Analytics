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

Four views, one per question:

| View | |
|---|---|
| **Overview** | sales, transactions, average ticket, branches trading, the daily series, branch ranking, **Opportunities**, estate split, payment mix |
| **Hours** | transactions and sales by hour, a day-of-week × hour heatmap, and cashier throughput |
| **Products** | top products by value and by units, items per basket, average price |
| **Exceptions** | flagged and soft-deleted rows, discounting by branch, and the BIR statutory relief — senior, PWD, solo parent, NAC — with voids |

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

**Keeping it current** — `run_daily_update.cmd` pulls the last 3 days. Safe to
run as often as you like: rows upsert on the API's UUID, so a day already loaded
is corrected rather than duplicated. Three days rather than one, so a
late-posted or amended receipt is still caught. Point Task Scheduler at it to
have it run itself.

**A wider load** — `python ingest.py --dataset both --from 2026-01-01 --to 2026-03-31`.
`python ingest.py --status` says what is loaded and names any window that failed.

## Status

| | |
|---|---|
| `setup_env.py` | writes `.env`, proves the key against `/transactions`. Done. |
| `api.py` | HTTP client — auth, retries, pagination. Done. |
| `probe_api.py` | API shape discovery. Done. |
| `db/prisma/schema.prisma` | 8 tables, migrated. Done. |
| `ingest.py` | day-by-day idempotent loader for transactions and receipts. Done. |
| `load_products.py` | the 440-SKU catalogue, linked to lines. Done. |
| `map_storehub_products.py` | names the StoreHub product ids. Done. |
| `analytics_web.py` | the four-view dashboard. Done. |

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
