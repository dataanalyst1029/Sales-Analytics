"""Pull transactions and receipts from the API into the warehouse.

    python ingest.py --dataset both --from 2025-10-01 --to 2026-09-19
    python ingest.py --dataset transactions --recent 7
    python ingest.py --status

Loads one day at a time and upserts on the API's UUID, so it is safe to run
again over a range already loaded: rows are corrected, never duplicated. That
property is not a nicety here -- the API's `startDate`/`endDate` are BOTH
inclusive of the midnight instant, so consecutive windows genuinely overlap.

Each window writes a `load_run` row, so a day that failed or was never pulled is
a visible fact rather than a quietly missing bar on a chart.

The five things the probe found, and what this does about them:

  * `transactionDate` is Manila wall-clock wearing a `Z` suffix. Its clock face
    is read as local time; the true UTC instant is derived from it, not trusted
    from it.
  * Jul-Aug 2026 carry no clock at all. Those rows get `has_time = false` and a
    null hour, so they are excluded from day-part work instead of piling 767k
    sales onto midnight.
  * `transactionId` is not unique. The UUID `id` is the key.
  * Only `startDate`/`endDate` filter. Nothing else is sent.
  * Three payload shapes share one endpoint. Each row records which it was.
"""
import argparse
import datetime as dt
import io
import json
import re
import sys

import psycopg

from api import ApiClient

HERE = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
DB_ENV = __import__('os').path.join(HERE, 'db', '.env')

#: The Philippines does not observe DST and has held UTC+8 throughout the period
#: on file, so a fixed offset is exact here -- no tz database needed.
MANILA = dt.timezone(dt.timedelta(hours=8))


def database_url():
    for line in open(DB_ENV, encoding='utf-8'):
        m = re.match(r'\s*DATABASE_URL\s*=\s*(.+)\s*$', line)
        if m:
            url = m.group(1).strip().strip('"').strip("'")
            # `schema` is Prisma's, and libpq refuses the whole URL over it.
            return re.sub(r'[?&]schema=[^&]*', '', url)
    raise SystemExit('No DATABASE_URL in db/.env -- run the Prisma setup first.')


# -- parsing ---------------------------------------------------------------

def parse_local(s):
    """(business date, local hour, true UTC instant, had a clock) from an API date.

    The API hands back e.g. `2026-09-17T22:51:08.086Z`. That `Z` is wrong: the
    clock face is Manila local, proved by it running exactly 8 hours ahead of
    `rawData.storehub.transactionTime` on every StoreHub row. So the face value
    is read as local and the instant is computed, rather than the other way
    round.

    A row whose clock reads exactly midnight had no time in the source. A real
    sale at 00:00:00.000 local would be misread as timeless; on this data that
    is a handful of rows against 767k genuinely timeless ones, and treating
    those 767k as midnight sales would be the far larger error.
    """
    if not s:
        return None, None, None, False
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})', s)
    if not m:
        m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})', s or '')
        if not m2:
            return None, None, None, False
        d = dt.date(*map(int, m2.groups()))
        return d, None, None, False
    y, mo, da, hh, mi, ss = map(int, m.groups())
    local = dt.datetime(y, mo, da, hh, mi, ss, tzinfo=MANILA)
    has_time = not (hh == 0 and mi == 0 and ss == 0)
    return local.date(), (hh if has_time else None), \
        (local.astimezone(dt.timezone.utc) if has_time else None), has_time


def parse_hhmm(date_obj, raw):
    """Receipts carry the clock separately, as "1411" meaning 14:11."""
    if not date_obj or not raw:
        return None, None
    s = str(raw).strip()
    if not s.isdigit() or len(s) not in (3, 4):
        return None, None
    s = s.zfill(4)
    hh, mi = int(s[:2]), int(s[2:])
    if hh > 23 or mi > 59:
        return None, None
    local = dt.datetime(date_obj.year, date_obj.month, date_obj.day, hh, mi,
                        tzinfo=MANILA)
    return hh, local.astimezone(dt.timezone.utc)


def num(v, default=None):
    """The API sends money as strings, sometimes with thousands separators."""
    if v is None or v == '':
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(',', ''))
    except ValueError:
        return default


def classify(raw):
    """Which of the three payload shapes this is, and which estate it belongs to."""
    raw = raw or {}
    if 'storehub' in raw:
        return 'storehub', 'STOREHUB'
    if 'Product ID' in raw:
        return 'alliance_line', 'ALLIANCE'
    if 'Account ID' in raw:
        return 'alliance_sale', 'ALLIANCE'
    return 'unknown', 'ALLIANCE'


def slugify(name):
    return re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-') or 'unknown'


# -- normalisation ---------------------------------------------------------

def norm_transaction(r):
    raw = r.get('rawData') or {}
    feed, estate = classify(raw)
    sh = raw.get('storehub') or {}

    bdate, hour, utc, has_time = parse_local(r.get('transactionDate'))
    if bdate is None:
        return None, [], []

    store = r.get('store') or {}
    amount = num(r.get('amount'), 0) or 0

    discount = num(sh.get('discount'))
    if feed == 'alliance_line':
        # LineTotal is gross, NetTotal is net and equals `amount` to the centavo
        # across a 1000-row sample -- so the difference is the discount.
        lt, nt = num(raw.get('LineTotal')), num(raw.get('NetTotal'))
        if lt is not None and nt is not None:
            discount = round(lt - nt, 2)

    row = {
        'source_id': r['id'],
        'store_source_id': r.get('storeId') or slugify(store.get('name')),
        'store_name': store.get('name') or '(unknown)',
        'store_code': store.get('code'),
        'estate': estate,
        'feed': feed,
        'ref_number': r.get('transactionId'),
        'business_date': bdate.isoformat(),
        'transacted_at': utc.isoformat() if utc else None,
        'has_time': has_time,
        'local_hour': hour,
        'amount': amount,
        'quantity': r.get('quantity') or 1,
        'payment_method': (r.get('paymentMethod') or sh.get('payments', [{}])[0].get('paymentMethod')
                           if sh.get('payments') else r.get('paymentMethod')),
        'cashier_name': r.get('cashierId') or raw.get('Cashier'),
        'status': r.get('status'),
        'is_deleted': bool(r.get('isDeleted')),
        'is_flagged': bool(r.get('isFlagged')),
        'flag_reason': r.get('flagReason'),
        'channel': sh.get('channel'),
        'register_id': sh.get('registerId'),
        'terminal_number': str(sh.get('terminalNumber') or raw.get('TM#') or '') or None,
        'invoice_number': sh.get('invoiceNumber'),
        'employee_id': sh.get('employeeId'),
        'sub_total': num(sh.get('subTotal')),
        'tax_amount': num(sh.get('tax'), num(r.get('taxAmount'))),
        'discount_amount': discount,
        'service_charge': num(sh.get('serviceCharge')),
        'rounded_amount': num(sh.get('roundedAmount')),
        'batch_id': r.get('batchId'),
        'batch_name': (r.get('batch') or {}).get('originalName'),
        'source_created_at': r.get('createdAt'),
    }

    items = []
    for it in (sh.get('items') or []):
        items.append({
            'product_source_id': str(it.get('productId') or ''),
            'product_name': None,          # StoreHub items carry no name
            'quantity': num(it.get('quantity'), 0) or 0,
            'unit_price': num(it.get('unitPrice'), 0) or 0,
            'sub_total': num(it.get('subTotal')),
            'tax_amount': num(it.get('tax')),
            'discount': num(it.get('discount'), 0) or 0,
            'line_total': num(it.get('total'), 0) or 0,
            'item_type': it.get('itemType'),
        })
    if not items and feed == 'alliance_line':
        # This feed IS a line -- the product sits on the row itself.
        items.append({
            'product_source_id': str(r.get('productCode') or raw.get('Product ID') or ''),
            'product_name': r.get('productName') or raw.get('Product Name'),
            'quantity': num(raw.get('Qty'), r.get('quantity') or 1) or 1,
            'unit_price': num(raw.get('Price'), 0) or 0,
            'sub_total': num(raw.get('LineTotal')),
            'tax_amount': None,
            'discount': discount or 0,
            'line_total': num(raw.get('NetTotal'), amount) or amount,
            'item_type': 'Item',
        })

    payments = [{'method': str(p.get('paymentMethod') or '').lower() or 'unknown',
                 'amount': num(p.get('amount'), 0) or 0}
                for p in (sh.get('payments') or [])]
    if not payments and r.get('paymentMethod'):
        payments.append({'method': str(r['paymentMethod']).lower(), 'amount': amount})

    return row, items, payments


def norm_receipt(r):
    bdate, _, _, _ = parse_local(r.get('date'))
    if bdate is None:
        return None
    hour, utc = parse_hhmm(bdate, r.get('time'))
    store = r.get('store') or {}
    return {
        'source_id': r['id'],
        'store_source_id': r.get('storeId') or slugify(store.get('name')),
        'store_name': store.get('name') or '(unknown)',
        'store_code': store.get('code'),
        'estate': 'ALLIANCE',
        'receipt_number': r.get('receiptNumber'),
        'business_date': bdate.isoformat(),
        'time_raw': r.get('time'),
        'local_hour': hour,
        'transacted_at': utc.isoformat() if utc else None,
        'terminal_number': r.get('terminalNumber'),
        'cashier': r.get('cashier'),
        'serviced_by': r.get('servicedBy'),
        'customer_name': r.get('customerName'),
        'posted': bool(r.get('posted')),
        'total_gross': num(r.get('totalGross'), 0) or 0,
        'discount': num(r.get('discount'), 0) or 0,
        'void_amount': num(r.get('voidAmount'), 0) or 0,
        'service': num(r.get('service'), 0) or 0,
        'senior': num(r.get('senior'), 0) or 0,
        'pwd': num(r.get('pwd'), 0) or 0,
        'nac': num(r.get('nac'), 0) or 0,
        'solo_parent': num(r.get('soloParent'), 0) or 0,
        'mov': num(r.get('mov'), 0) or 0,
        'diplomat': num(r.get('diplomat'), 0) or 0,
        'evat': num(r.get('evat'), 0) or 0,
        'tax': num(r.get('tax'), 0) or 0,
        'local_tax': num(r.get('localTax'), 0) or 0,
        'amusement_tax': num(r.get('amusementTax'), 0) or 0,
        'ewt': num(r.get('ewt'), 0) or 0,
        'feedback_rating': r.get('feedbackRating'),
        'batch_id': r.get('batchId'),
        'source_created_at': r.get('createdAt'),
    }


# -- writing ---------------------------------------------------------------

TXN_COLS = ['source_id', 'store_id', 'estate', 'feed', 'ref_number', 'business_date',
            'transacted_at', 'has_time', 'local_hour', 'amount', 'quantity',
            'payment_method', 'cashier_name', 'status', 'is_deleted', 'is_flagged',
            'flag_reason', 'channel', 'register_id', 'terminal_number',
            'invoice_number', 'employee_id', 'sub_total', 'tax_amount',
            'discount_amount', 'service_charge', 'rounded_amount', 'batch_id',
            'batch_name', 'source_created_at']

RCP_COLS = ['source_id', 'store_id', 'receipt_number', 'business_date', 'time_raw',
            'local_hour', 'terminal_number', 'cashier', 'serviced_by',
            'customer_name', 'posted', 'total_gross', 'discount', 'void_amount',
            'service', 'senior', 'pwd', 'nac', 'solo_parent', 'mov', 'diplomat',
            'evat', 'tax', 'local_tax', 'amusement_tax', 'ewt', 'feedback_rating',
            'batch_id', 'source_created_at']


class Warehouse:
    def __init__(self, conn):
        self.conn = conn
        self.stores = {}
        for sid, pk in conn.execute('SELECT source_id, id FROM store').fetchall():
            self.stores[sid] = pk

    def store_id(self, source_id, name, code, estate):
        if source_id in self.stores:
            return self.stores[source_id]
        pk = self.conn.execute(
            """INSERT INTO store (source_id, name, code, estate, slug)
               VALUES (%s, %s, %s, %s::"Estate", %s)
               ON CONFLICT (source_id) DO UPDATE SET name = EXCLUDED.name
               RETURNING id""",
            (source_id, name, code, estate, slugify(name))).fetchone()[0]
        self.stores[source_id] = pk
        return pk

    def _copy(self, table, cols, rows):
        """COPY into an unlogged staging table -- far faster than row inserts."""
        stage = 'stage_%s' % table
        self.conn.execute('DROP TABLE IF EXISTS %s' % stage)
        self.conn.execute(
            'CREATE TEMP TABLE %s (LIKE "%s" INCLUDING DEFAULTS) ON COMMIT DROP'
            % (stage, table))
        collist = ', '.join('"%s"' % c for c in cols)
        with self.conn.cursor().copy(
                'COPY %s (%s) FROM STDIN' % (stage, collist)) as cp:
            for r in rows:
                cp.write_row([r.get(c) for c in cols])
        return stage

    def upsert_transactions(self, rows):
        if not rows:
            return 0
        for r in rows:
            r['store_id'] = self.store_id(r.pop('store_source_id'),
                                          r.pop('store_name'), r.pop('store_code'),
                                          r['estate'])
        stage = self._copy('transaction', TXN_COLS, rows)
        sets = ', '.join('"%s" = EXCLUDED."%s"' % (c, c)
                         for c in TXN_COLS if c != 'source_id')
        cols = ', '.join('"%s"' % c for c in TXN_COLS)
        self.conn.execute(
            'INSERT INTO "transaction" (%s) SELECT %s FROM %s '
            'ON CONFLICT (source_id) DO UPDATE SET %s' % (cols, cols, stage, sets))
        return len(rows)

    def write_children(self, by_source, items_by_source, payments_by_source):
        """Replace the item and payment lines of the transactions just written.

        Replaced rather than merged: a line has no stable id of its own in the
        payload, so the only honest way to re-load an amended sale is to drop
        what was there and write what the API now says.
        """
        if not by_source:
            return
        ids = dict(self.conn.execute(
            'SELECT source_id, id FROM "transaction" WHERE source_id = ANY(%s)',
            (list(by_source),)).fetchall())
        pks = list(ids.values())
        if not pks:
            return
        self.conn.execute('DELETE FROM transaction_item WHERE transaction_id = ANY(%s)', (pks,))
        self.conn.execute('DELETE FROM payment WHERE transaction_id = ANY(%s)', (pks,))

        item_rows = []
        for sid, lines in items_by_source.items():
            pk = ids.get(sid)
            if pk:
                for ln in lines:
                    ln = dict(ln, transaction_id=pk)
                    item_rows.append(ln)
        if item_rows:
            cols = ['transaction_id', 'product_source_id', 'product_name', 'quantity',
                    'unit_price', 'sub_total', 'tax_amount', 'discount', 'line_total',
                    'item_type']
            stage = self._copy('transaction_item', cols, item_rows)
            cl = ', '.join('"%s"' % c for c in cols)
            self.conn.execute('INSERT INTO transaction_item (%s) SELECT %s FROM %s'
                              % (cl, cl, stage))

        pay_rows = []
        for sid, ps in payments_by_source.items():
            pk = ids.get(sid)
            if pk:
                for p in ps:
                    pay_rows.append(dict(p, transaction_id=pk))
        if pay_rows:
            cols = ['transaction_id', 'method', 'amount']
            stage = self._copy('payment', cols, pay_rows)
            cl = ', '.join('"%s"' % c for c in cols)
            self.conn.execute('INSERT INTO payment (%s) SELECT %s FROM %s'
                              % (cl, cl, stage))

    def upsert_receipts(self, rows):
        if not rows:
            return 0
        for r in rows:
            r['store_id'] = self.store_id(r.pop('store_source_id'),
                                          r.pop('store_name'), r.pop('store_code'),
                                          r.pop('estate'))
            r.pop('transacted_at', None)
        stage = self._copy('receipt', RCP_COLS, rows)
        sets = ', '.join('"%s" = EXCLUDED."%s"' % (c, c)
                         for c in RCP_COLS if c != 'source_id')
        cols = ', '.join('"%s"' % c for c in RCP_COLS)
        self.conn.execute(
            'INSERT INTO receipt (%s) SELECT %s FROM %s '
            'ON CONFLICT (source_id) DO UPDATE SET %s' % (cols, cols, stage, sets))
        return len(rows)

    def log_run(self, dataset, d0, d1, ok, fetched, written, api_total, error=None):
        self.conn.execute(
            """INSERT INTO load_run (dataset, from_date, to_date, finished_at, ok,
                                     rows_fetched, rows_written, api_total, error)
               VALUES (%s,%s,%s,now(),%s,%s,%s,%s,%s)""",
            (dataset, d0, d1, ok, fetched, written, api_total, error))


# -- the loop --------------------------------------------------------------

def days(d0, d1):
    d = d0
    while d <= d1:
        yield d
        d += dt.timedelta(days=1)


def pull_day(api, path, day):
    """Every row the API has for one local day, following pagination."""
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
    api = ApiClient.from_env(); api.verbose = False
    url = database_url()
    total_written = 0
    with psycopg.connect(url, autocommit=False) as conn:
        wh = Warehouse(conn)
        for day in days(d0, d1):
            try:
                if dataset == 'transactions':
                    raw, api_total = pull_day(api, '/transactions', day)
                    rows, items, pays = [], {}, {}
                    for r in raw:
                        n, it, pm = norm_transaction(r)
                        if not n:
                            continue
                        # The API's inclusive end bound drags in the next day's
                        # midnight rows; keep only the day actually asked for.
                        if n['business_date'] != day.isoformat():
                            continue
                        rows.append(n)
                        if it:
                            items[n['source_id']] = it
                        if pm:
                            pays[n['source_id']] = pm
                    n = wh.upsert_transactions(rows)
                    wh.write_children({r['source_id'] for r in rows}, items, pays)
                else:
                    raw, api_total = pull_day(api, '/receipts', day)
                    rows = []
                    for r in raw:
                        nr = norm_receipt(r)
                        if nr and nr['business_date'] == day.isoformat():
                            rows.append(nr)
                    n = wh.upsert_receipts(rows)
                wh.log_run(dataset, day, day, True, len(raw), n, api_total)
                conn.commit()
                total_written += n
                if verbose:
                    print('  %s %-12s fetched %6d  wrote %6d  (running %d)'
                          % (day, dataset, len(raw), n, total_written), flush=True)
            except Exception as e:
                conn.rollback()
                wh.log_run(dataset, day, day, False, 0, 0, None, repr(e)[:500])
                conn.commit()
                print('  %s %-12s FAILED %s' % (day, dataset, repr(e)[:200]), flush=True)
    return total_written


def status():
    with psycopg.connect(database_url()) as c:
        print('%-16s %10s %14s %12s' % ('table', 'rows', 'min date', 'max date'))
        for t, d in (('transaction', 'business_date'), ('receipt', 'business_date'),
                     ('transaction_item', None), ('payment', None), ('store', None),
                     ('product', None), ('load_run', None)):
            n = c.execute('SELECT count(*) FROM "%s"' % t).fetchone()[0]
            if d:
                lo, hi = c.execute('SELECT min(%s), max(%s) FROM "%s"' % (d, d, t)).fetchone()
            else:
                lo = hi = ''
            print('%-16s %10d %14s %12s' % (t, n, lo or '', hi or ''))
        bad = c.execute("SELECT count(*) FROM load_run WHERE ok = false").fetchone()[0]
        if bad:
            print('\n%d failed window(s):' % bad)
            for r in c.execute("SELECT dataset, from_date, error FROM load_run "
                               "WHERE ok = false ORDER BY from_date LIMIT 20"):
                print('  %s %s  %s' % (r[0], r[1], (r[2] or '')[:120]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dataset', default='both',
                    choices=['transactions', 'receipts', 'both'])
    ap.add_argument('--from', dest='d0', help='YYYY-MM-DD')
    ap.add_argument('--to', dest='d1', help='YYYY-MM-DD (inclusive)')
    ap.add_argument('--recent', type=int, help='load the last N days instead')
    ap.add_argument('--status', action='store_true', help='what is loaded')
    a = ap.parse_args()

    if a.status:
        return status()

    today = dt.date.today()
    if a.recent:
        d0, d1 = today - dt.timedelta(days=a.recent - 1), today
    else:
        if not a.d0 or not a.d1:
            ap.error('--from and --to are required (or use --recent N)')
        d0 = dt.date.fromisoformat(a.d0)
        d1 = dt.date.fromisoformat(a.d1)

    sets = ['transactions', 'receipts'] if a.dataset == 'both' else [a.dataset]
    for s in sets:
        print('\n=== %s  %s .. %s ===' % (s, d0, d1), flush=True)
        n = run(s, d0, d1)
        print('--- %s: %d rows written ---' % (s, n), flush=True)
    print()
    status()


if __name__ == '__main__':
    sys.exit(main() or 0)
