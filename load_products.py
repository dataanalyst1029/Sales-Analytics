"""Load the product catalogue and link it to the lines that reference it.

    python load_products.py

`/products` is a derived catalogue of 440 SKUs (`50007M`, `C007SC` ...) drawn
from the Alliance estate's line-level feed. It does NOT cover the StoreHub
estate, whose items carry Mongo ObjectIds instead -- those lines keep their id
and a null product until names are mapped in from the recon tool's exports.

Runs separately from `ingest.py` because the catalogue changes on its own
schedule and is 440 rows, not a million.
"""
import sys

import psycopg

from api import ApiClient
from ingest import database_url


def main():
    api = ApiClient.from_env(); api.verbose = False
    rows, page = [], 1
    while True:
        j = api.get_json('/products', {'limit': 500, 'page': page})
        rows += j.get('data') or []
        pg = j.get('pagination') or {}
        if len(rows) >= (pg.get('total') or 0) or not j.get('data'):
            break
        page += 1
    print('fetched %d products' % len(rows))

    with psycopg.connect(database_url()) as c:
        for r in rows:
            c.execute(
                """INSERT INTO product (source_id, name, estate)
                   VALUES (%s, %s, 'ALLIANCE'::"Estate")
                   ON CONFLICT (source_id) DO UPDATE SET name = EXCLUDED.name""",
                (str(r['productId']), r.get('productName') or '(unnamed)'))
        c.commit()

        linked = c.execute(
            """UPDATE transaction_item ti SET product_id = p.id
                 FROM product p
                WHERE p.source_id = ti.product_source_id
                  AND ti.product_id IS DISTINCT FROM p.id""").rowcount
        c.commit()

        total, named, unnamed = c.execute(
            """SELECT count(*), count(product_id),
                      count(*) FILTER (WHERE product_id IS NULL)
                 FROM transaction_item""").fetchone()
        print('product rows in warehouse: %d' %
              c.execute('SELECT count(*) FROM product').fetchone()[0])
        print('linked %d line(s) this run' % linked)
        print('lines: %d total, %d linked to a catalogue product, %d not'
              % (total, named, unnamed))
        if unnamed:
            print('\nUnlinked lines are the StoreHub estate -- their ids are Mongo')
            print('ObjectIds the catalogue does not carry. Names for those have to')
            print('come from the recon tool\'s product exports.')


if __name__ == '__main__':
    sys.exit(main() or 0)
