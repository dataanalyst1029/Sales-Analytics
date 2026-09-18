"""Give the StoreHub estate's product ids their names, by matching the numbers.

    python map_storehub_products.py --dry-run
    python map_storehub_products.py

StoreHub line items carry Mongo ObjectIds (`690ab892c64230000705a4ff`) and no
name, and the API's `/products` catalogue covers only the Alliance SKUs -- so
`/products?search=<objectid>` genuinely returns nothing. The names do exist, in
the recon tool's `report.json` files, which record name, quantity and sales per
product per day.

Both sides therefore describe the same sales at the same grain: for one branch on
one day, this warehouse has (product id -> units, value) and the recon report has
(product name -> units, value). Where a (units, value) pair is unique on both
sides that day, the id and the name are the same product. That is a proof from
the numbers, not a fuzzy string match -- two products would have to sell an
identical number of units for an identical amount, on the same day, in the same
branch, to be confused.

Confidence comes from repetition: each branch-day is an independent vote, and a
mapping is only written when several days agree and effectively none dissent.
Anything short of that is reported as ambiguous and left unnamed, because a
wrong name on a sales report is worse than a visible id.
"""
import argparse
import collections
import json
import os
import re
import sys

import psycopg

from ingest import database_url

RECON_OUT = r"C:/Users/ASUS/Downloads/storehub sales by period/output"

#: A mapping needs this many days agreeing, and this share of that id's votes,
#: before it is written. Two independent days already make a coincidence
#: unlikely; the share is what guards against a genuine conflict.
MIN_VOTES = 2
MIN_SHARE = 0.80


def norm(s):
    return re.sub(r'[^a-z0-9]', '', (s or '').lower()).replace('ribshack', '')


def run_base(d):
    return re.sub(r'_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}$', '', d)


def collect_votes(conn, verbose=True):
    """(votes, stats) from every recon run that lines up with a warehouse store."""
    stores = {norm(n): (i, n) for i, n in
              conn.execute("SELECT id, name FROM store WHERE estate = 'STOREHUB'")}
    if not os.path.isdir(RECON_OUT):
        raise SystemExit('Recon output folder not found: %s' % RECON_OUT)

    runs = [d for d in os.listdir(RECON_OUT)
            if os.path.isfile(os.path.join(RECON_OUT, d, 'report.json'))
            and not d.startswith('FAILED')]

    votes = collections.defaultdict(collections.Counter)
    stats = {'runs': 0, 'skipped': [], 'days': 0, 'ids_seen': set(), 'matches': 0,
             'considered': 0}

    for d in sorted(runs):
        key = norm(run_base(d))
        if key not in stores:
            stats['skipped'].append(d)
            continue
        store_id, store_name = stores[key]
        try:
            rep = json.load(open(os.path.join(RECON_OUT, d, 'report.json'),
                                 encoding='utf-8'))
        except (ValueError, OSError) as e:
            stats['skipped'].append('%s (%s)' % (d, e))
            continue
        pdaily = rep.get('product_daily') or []
        if not pdaily:
            # Only populated when the product export carried a Date column.
            stats['skipped'].append('%s (no product_daily)' % d)
            continue
        stats['runs'] += 1

        by_day = collections.defaultdict(list)
        for r in pdaily:
            by_day[r['d']].append(r)

        run_hits = run_seen = 0
        for day, rows in by_day.items():
            wh = conn.execute(
                """SELECT i.product_source_id, sum(i.quantity), sum(i.line_total)
                     FROM transaction_item i
                     JOIN "transaction" t ON t.id = i.transaction_id
                    WHERE t.store_id = %s AND t.business_date = %s
                    GROUP BY 1""", (store_id, day)).fetchall()
            if not wh:
                continue
            stats['days'] += 1

            # Index the recon side by its (units, value) pair; a pair claimed by
            # two different names that day is ambiguous and helps nobody.
            rk = collections.defaultdict(list)
            for r in rows:
                rk[(round(float(r['qty']), 2), round(float(r['sales']), 2))].append(r['name'])

            # The same guard on this side: an id sharing a pair with another id
            # cannot be told apart either.
            wk = collections.Counter((round(float(q), 2), round(float(s), 2))
                                     for _, q, s in wh)

            for pid, q, s in wh:
                stats['ids_seen'].add(pid)
                run_seen += 1
                stats['considered'] += 1
                k = (round(float(q), 2), round(float(s), 2))
                names = rk.get(k)
                if names and len(names) == 1 and wk[k] == 1:
                    votes[pid][names[0]] += 1
                    run_hits += 1
                    stats['matches'] += 1
        if verbose:
            print('  %-42s %4d day(s), %5d id-days, %5d matched'
                  % (store_name[:42], len(by_day), run_seen, run_hits))
    return votes, stats


def decide(votes):
    """(accepted, ambiguous) -- a mapping is only accepted when the days agree."""
    accepted, ambiguous = {}, {}
    for pid, cnt in votes.items():
        total = sum(cnt.values())
        name, n = cnt.most_common(1)[0]
        if n >= MIN_VOTES and n / total >= MIN_SHARE:
            accepted[pid] = (name, n, total)
        else:
            ambiguous[pid] = cnt.most_common(3)
    return accepted, ambiguous


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would be written, change nothing')
    a = ap.parse_args()

    with psycopg.connect(database_url()) as conn:
        print('Matching warehouse product ids against the recon reports ...\n')
        votes, stats = collect_votes(conn)
        accepted, ambiguous = decide(votes)

        print('\n%d recon run(s) used, %d branch-day(s), %d id-day(s) considered, '
              '%d matched (%.0f%%)'
              % (stats['runs'], stats['days'], stats['considered'], stats['matches'],
                 stats['matches'] / max(1, stats['considered']) * 100))
        if stats['skipped']:
            print('%d run(s) skipped: %s' % (len(stats['skipped']),
                                             ', '.join(stats['skipped'][:4])))
        print('\n%d distinct product id(s) seen, %d named, %d left ambiguous'
              % (len(stats['ids_seen']), len(accepted), len(ambiguous)))

        if ambiguous:
            print('\nAmbiguous -- left unnamed on purpose:')
            for pid, top in list(ambiguous.items())[:10]:
                print('  %s  %s' % (pid, '; '.join('%s x%d' % (n, c) for n, c in top)))

        sample = sorted(accepted.items(), key=lambda kv: -kv[1][1])[:12]
        print('\nStrongest mappings:')
        for pid, (name, n, total) in sample:
            print('  %-26s -> %-38s %d/%d days' % (pid[:26], name[:38], n, total))

        if a.dry_run:
            print('\nDry run -- nothing written.')
            return 0

        for pid, (name, _, _) in accepted.items():
            conn.execute(
                """INSERT INTO product (source_id, name, estate)
                   VALUES (%s, %s, 'STOREHUB'::"Estate")
                   ON CONFLICT (source_id) DO UPDATE SET name = EXCLUDED.name""",
                (pid, name))
        conn.commit()

        linked = conn.execute(
            """UPDATE transaction_item ti SET product_id = p.id
                 FROM product p
                WHERE p.source_id = ti.product_source_id
                  AND ti.product_id IS DISTINCT FROM p.id""").rowcount
        conn.commit()

        named, unnamed = conn.execute(
            """SELECT count(product_id), count(*) FILTER (WHERE product_id IS NULL)
                 FROM transaction_item""").fetchone()
        print('\nWrote %d product name(s); linked %d line(s).' % (len(accepted), linked))
        print('Lines now: %s named, %s still unnamed.'
              % (format(named, ','), format(unnamed, ',')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
