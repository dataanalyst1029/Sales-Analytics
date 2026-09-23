"""Load the API's own report endpoints into the warehouse.

    python ingest_reports.py --dataset both --from 2025-10-01 --to 2026-09-22
    python ingest_reports.py --recent 7
    python ingest_reports.py --status

Two endpoints, both already aggregated to one row per branch, per day, per
product -- which is exactly the grain the product reports need:

    /sales-summary              Alliance estate. Carries cost, tax and gross
                                profit, which the raw /transactions feed does
                                not, and covers every month and every branch
                                rather than only the few whose line-level
                                spreadsheets were uploaded.

    /storehub-product-movement  StoreHub estate. Column for column the
                                back-office "Sales by Product" report, including
                                product category and the real SKU.

These are kept separate from `ingest.py`'s transaction load on purpose. That one
holds receipt-level detail and answers "when, who, what basket"; these hold the
business's own published daily figures. Loading both means the two can be
compared, and a disagreement between them is a finding rather than a mystery.

Same shape as the transaction loader: one day per window, upsert on the API's
UUID, a `load_run` row per window so a gap is visible.
"""
import argparse
import datetime as dt
import sys

import psycopg

from api import ApiClient
from ingest import Warehouse, database_url, days, num, parse_local, slugify

DATASETS = {
    'sales-summary': {
        'path': '/sales-summary',
        'table': 'sales_summary',
        'estate': 'ALLIANCE',
    },
    'product-movement': {
        'path': '/storehub-product-movement',
        'table': 'product_movement',
        'estate': 'STOREHUB',
    },
}

SS_COLS = ['source_id', 'store_id', 'business_date', 'product_id', 'product_name',
           'quantity', 'gross_sales', 'cost', 'tax', 'gross_profit', 'batch_id']

PM_COLS = ['source_id', 'store_id', 'business_date', 'product_name',
           'product_category', 'sku_id', 'total_items_sold', 'total_sales',
           'total_sales_returned', 'total_discount', 'discount_percent',
           'item_net_sales', 'average_cost', 'average_net_sales', 'gross_profit',
           'gross_profit_percent', 'batch_id']


def n(v, default=None):
    """The API sends every figure as a string. Blank means absent, not zero."""
    if v is None or v == '':
        return default
    try:
        return float(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return default


def norm_sales_summary(r):
    store = r.get('store') or {}
    bdate, _, _, _ = parse_local(r.get('date'))
    if bdate is None:
        return None
    return {
        'source_id': r['id'],
        'store_source_id': r.get('storeId') or slugify(store.get('name')),
        'store_name': store.get('name') or '(unknown)',
        'store_code': store.get('code'),
        'estate': 'ALLIANCE',
        'business_date': bdate.isoformat(),
        'product_id': r.get('productId'),
        'product_name': r.get('productName') or '(unnamed)',
        'quantity': n(r.get('quantity'), 0) or 0,
        'gross_sales': n(r.get('grossSales'), 0) or 0,
        # Left as NULL where the source is blank. Cost in particular must never
        # default to zero: that would silently make gross profit equal gross
        # sales and report a 100% margin on every line.
        'cost': n(r.get('cost')),
        'tax': n(r.get('tax')),
        'gross_profit': n(r.get('grossProfit')),
        'batch_id': r.get('batchId'),
    }


def norm_product_movement(r):
    store = r.get('store') or {}
    bdate, _, _, _ = parse_local(r.get('date'))
    if bdate is None:
        return None
    return {
        'source_id': r['id'],
        'store_source_id': r.get('storeId') or slugify(store.get('name')),
        'store_name': store.get('name') or '(unknown)',
        'store_code': store.get('code'),
        'estate': 'STOREHUB',
        'business_date': bdate.isoformat(),
        'product_name': r.get('productName') or '(unnamed)',
        'product_category': r.get('productCategory'),
        'sku_id': r.get('skuId'),
        'total_items_sold': n(r.get('totalItemsSold'), 0) or 0,
        'total_sales': n(r.get('totalSales'), 0) or 0,
        'total_sales_returned': n(r.get('totalSalesReturned'), 0) or 0,
        'total_discount': n(r.get('totalDiscount'), 0) or 0,
        'discount_percent': n(r.get('discountPercent')),
        'item_net_sales': n(r.get('itemNetSales'), 0) or 0,
        'average_cost': n(r.get('averageCost')),
        'average_net_sales': n(r.get('averageNetSales')),
        'gross_profit': n(r.get('grossProfit')),
        'gross_profit_percent': n(r.get('grossProfitPercent')),
        'batch_id': r.get('batchId'),
    }


def upsert(wh, table, cols, rows):
    if not rows:
        return 0
    for r in rows:
        r['store_id'] = wh.store_id(r.pop('store_source_id'), r.pop('store_name'),
                                    r.pop('store_code'), r.pop('estate'))
    stage = wh._copy(table, cols, rows)
    sets = ', '.join('"%s" = EXCLUDED."%s"' % (c, c) for c in cols if c != 'source_id')
    cl = ', '.join('"%s"' % c for c in cols)
    wh.conn.execute('INSERT INTO "%s" (%s) SELECT %s FROM %s '
                    'ON CONFLICT (source_id) DO UPDATE SET %s'
                    % (table, cl, cl, stage, sets))
    return len(rows)


def pull_day(api, path, day):
    out, page = [], 1
    while True:
        j = api.get_json(path, {'startDate': day.isoformat(),
                                'endDate': (day + dt.timedelta(days=1)).isoformat(),
                                'limit': 5000, 'page': page})
        rows = j.get('data') or []
        out += rows
        pg = j.get('pagination') or {}
        if not rows or len(out) >= (pg.get('total') or 0) or page >= (pg.get('pages') or 1):
            return out, pg.get('total')
        page += 1


def run(dataset, d0, d1, verbose=True):
    spec = DATASETS[dataset]
    norm = norm_sales_summary if dataset == 'sales-summary' else norm_product_movement
    cols = SS_COLS if dataset == 'sales-summary' else PM_COLS
    api = ApiClient.from_env(); api.verbose = False
    written = 0
    with psycopg.connect(database_url(), autocommit=False) as conn:
        wh = Warehouse(conn)
        for day in days(d0, d1):
            try:
                raw, api_total = pull_day(api, spec['path'], day)
                rows = []
                for r in raw:
                    x = norm(r)
                    # The API's end bound is inclusive, so the next day's
                    # midnight rows come along too; keep only the day asked for.
                    if x and x['business_date'] == day.isoformat():
                        rows.append(x)
                got = upsert(wh, spec['table'], cols, rows)
                wh.log_run(dataset, day, day, True, len(raw), got, api_total)
                conn.commit()
                written += got
                if verbose:
                    print('  %s %-17s fetched %6d  wrote %6d  (running %d)'
                          % (day, dataset, len(raw), got, written), flush=True)
            except Exception as e:
                conn.rollback()
                wh.log_run(dataset, day, day, False, 0, 0, None, repr(e)[:500])
                conn.commit()
                print('  %s %-17s FAILED %s' % (day, dataset, repr(e)[:180]), flush=True)
    return written


def status():
    with psycopg.connect(database_url()) as c:
        print('%-18s %12s %14s %12s' % ('table', 'rows', 'min date', 'max date'))
        for t in ('sales_summary', 'product_movement'):
            r = c.execute('SELECT count(*), min(business_date), max(business_date) '
                          'FROM "%s"' % t).fetchone()
            print('%-18s %12s %14s %12s'
                  % (t, format(r[0], ','), r[1] or '', r[2] or ''))
        print()
        for t, col in (('sales_summary', 'cost'), ('product_movement', 'average_cost')):
            r = c.execute('SELECT count(*), count(%s) FROM "%s"' % (col, t)).fetchone()
            print('%-18s %s of %s rows carry %s'
                  % (t, format(r[1], ','), format(r[0], ','), col))
        bad = c.execute("SELECT count(*) FROM load_run WHERE ok = false AND dataset "
                        "IN ('sales-summary','product-movement')").fetchone()[0]
        print('\nfailed windows: %d' % bad)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dataset', default='both',
                    choices=['sales-summary', 'product-movement', 'both'])
    ap.add_argument('--from', dest='d0')
    ap.add_argument('--to', dest='d1')
    ap.add_argument('--recent', type=int)
    ap.add_argument('--status', action='store_true')
    a = ap.parse_args()

    if a.status:
        return status()

    today = dt.date.today()
    if a.recent:
        d0, d1 = today - dt.timedelta(days=a.recent - 1), today
    else:
        if not a.d0 or not a.d1:
            ap.error('--from and --to are required (or --recent N)')
        d0, d1 = dt.date.fromisoformat(a.d0), dt.date.fromisoformat(a.d1)

    sets = (['sales-summary', 'product-movement'] if a.dataset == 'both'
            else [a.dataset])
    for s in sets:
        print('\n=== %s  %s .. %s ===' % (s, d0, d1), flush=True)
        print('--- %s: %d rows ---' % (s, run(s, d0, d1)), flush=True)
    print()
    status()


if __name__ == '__main__':
    sys.exit(main() or 0)
