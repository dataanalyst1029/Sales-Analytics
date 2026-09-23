"""Find out what the transactions API actually returns, before anything is built on it.

The warehouse schema, the ingestion loop and every metric downstream depend on
the exact field names, types and nesting of one transaction record. Guessing
those and correcting later means rewriting all three, so this script settles
them first, against the live API.

    python probe_api.py --ping                       # is the base URL reachable
    python probe_api.py --discover                   # which endpoint paths exist
    python probe_api.py --path /transactions --params from=2026-09-01 to=2026-09-01
    python probe_api.py --path /transactions --params date=2026-09-01 --save txn

What it prints is a *shape*: every field, its type, an example value, and -- for
a list of records -- which fields are missing on some of them. An optional field
discovered here is a nullable column later; an optional field discovered in
production is a 3am failed load.

Raw responses land in `samples/`, which is gitignored, because they contain real
sales data.
"""
import argparse
import datetime
import json
import os
import sys

from api import ApiClient, ApiError, find_rows, redact

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, 'samples')

#: Paths worth trying when nobody has told us the endpoint yet. Cheap: one GET
#: each, and a 404 is as informative as a 200.
CANDIDATES = [
    '/', '/health', '/status', '/version', '/docs', '/openapi.json',
    '/swagger.json', '/api-docs',
    '/transactions', '/transaction', '/api/transactions', '/v1/transactions',
    '/sales', '/api/sales', '/v1/sales',
    '/orders', '/api/orders', '/receipts', '/invoices',
    '/stores', '/branches', '/outlets', '/locations',
    '/products', '/items', '/menu',
    '/employees', '/staff', '/cashiers', '/users',
    '/payments', '/payment-methods',
    # Report endpoints. These are the ones the first version of this list
    # missed: the portal showed cost, tax and product category that
    # /transactions does not carry, and the figures were on these paths all
    # along. A guessed list is a floor on what exists, never a ceiling.
    '/sales-summary', '/sales-by-period', '/sales-book', '/reconciliation',
    '/storehub-product-movement', '/parent-products', '/exports',
]

MAX_EXAMPLE = 60


def typename(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'bool'
    if isinstance(v, int):
        return 'int'
    if isinstance(v, float):
        return 'float'
    if isinstance(v, str):
        return 'str'
    if isinstance(v, list):
        return 'list'
    if isinstance(v, dict):
        return 'object'
    return type(v).__name__


def example(v):
    if isinstance(v, str):
        s = v if len(v) <= MAX_EXAMPLE else v[:MAX_EXAMPLE] + '...'
        return json.dumps(s, ensure_ascii=False)
    if isinstance(v, (int, float, bool)) or v is None:
        return json.dumps(v)
    return ''


def merge_records(rows, cap=200):
    """Field -> (types seen, an example, how many records had it).

    Union across records, not just the first one. The first record is a sample
    of size one and will happily hide every optional field in the payload.
    """
    fields = {}
    n = 0
    for row in rows[:cap]:
        if not isinstance(row, dict):
            continue
        n += 1
        for k, v in row.items():
            t, ex, cnt = fields.get(k, (set(), '', 0))
            t.add(typename(v))
            if not ex and v not in (None, '', [], {}):
                ex = example(v)
            fields[k] = (t, ex, cnt + 1)
    return fields, n


def print_shape(obj, indent=0, path='', seen_rows=None):
    """Recursive shape of a JSON value, one line per field."""
    pad = '  ' * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = '%s.%s' % (path, k) if path else k
            t = typename(v)
            if isinstance(v, dict):
                print('%s%-28s object' % (pad, k))
                print_shape(v, indent + 1, here)
            elif isinstance(v, list):
                inner = 'empty' if not v else typename(v[0])
                print('%s%-28s list[%s] x%d' % (pad, k, inner, len(v)))
                if v and isinstance(v[0], dict):
                    fields, n = merge_records(v)
                    for fk, (types, ex, cnt) in fields.items():
                        opt = '' if cnt == n else '  (in %d/%d)' % (cnt, n)
                        print('%s  %-26s %-14s %s%s'
                              % (pad, fk, '|'.join(sorted(types)), ex, opt))
                elif v:
                    print('%s  %s' % (pad, example(v[0])))
            else:
                print('%s%-28s %-14s %s' % (pad, k, t, example(v)))
    elif isinstance(obj, list):
        print('%stop level is a list of %d' % (pad, len(obj)))
        if obj and isinstance(obj[0], dict):
            fields, n = merge_records(obj)
            for fk, (types, ex, cnt) in fields.items():
                opt = '' if cnt == n else '  (in %d/%d)' % (cnt, n)
                print('%s  %-26s %-14s %s%s'
                      % (pad, fk, '|'.join(sorted(types)), ex, opt))


def hr(title):
    print('\n' + title)
    print('-' * max(60, len(title)))


def save(name, data):
    os.makedirs(SAMPLES, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    p = os.path.join(SAMPLES, '%s_%s.json' % (name, stamp))
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print('\nsaved  %s' % p)
    return p


def parse_params(pairs):
    out = {}
    for p in pairs or []:
        if '=' not in p:
            raise SystemExit('--params takes key=value, got: %s' % p)
        k, v = p.split('=', 1)
        out[k] = v
    return out


def cmd_ping(api):
    hr('Reachability')
    print('base   %s' % api.base_url)
    try:
        r = api.request('/', retries=1)
        print('GET /  HTTP %s  %s' % (r.status_code, r.headers.get('Content-Type', '')))
        print(r.text[:500])
    except ApiError as e:
        # A 401/404 still proves the host is up and speaking HTTP, which is
        # what this command is actually asking.
        print('GET /  HTTP %s  (host is reachable)' % e.status)
        print(e.body[:500])
    return 0


def cmd_discover(api, params):
    hr('Endpoint discovery')
    print('%-24s %-6s %-30s %s' % ('path', 'http', 'content-type', 'rows / note'))
    print('-' * 90)
    found = []
    for path in CANDIDATES:
        try:
            r = api.request(path, params, retries=1)
            status, body, ctype = r.status_code, r.text, r.headers.get('Content-Type', '')
        except ApiError as e:
            status, body, ctype = e.status, e.body, ''
        except Exception as e:
            print('%-24s %-6s %s' % (path, '-', type(e).__name__))
            continue

        note = ''
        if status < 400 and 'json' in ctype.lower():
            try:
                data = json.loads(body)
                rows, key = find_rows(data)
                note = ('%d rows in "%s"' % (len(rows), key)) if rows else 'json, no row list'
                found.append((path, len(rows), key))
            except ValueError:
                note = 'unparseable json'
        elif status < 400:
            note = '%d bytes' % len(body)
            found.append((path, 0, ''))
        else:
            note = (body[:60].replace('\n', ' ') if body else '')
        print('%-24s %-6s %-30s %s' % (path, status, ctype[:30], note))

    hr('Worth a closer look')
    if found:
        for path, n, key in sorted(found, key=lambda x: -x[1]):
            print('  python probe_api.py --path %s' % path)
    else:
        print('  Nothing responded. Check API_BASE_URL and the token, or pass')
        print('  the real path directly:  python probe_api.py --path /your/path')
    return 0


def cmd_path(api, path, params, save_as, rows_key, show):
    hr('GET %s' % path)
    if params:
        print('params %s' % json.dumps(params))
    try:
        r = api.request(path, params)
    except ApiError as e:
        print('HTTP %s  %s' % (e.status, redact(e.url)))
        print(e.body[:3000])
        return 1

    print('HTTP %s  %s' % (r.status_code, redact(r.url)))
    print('type   %s   %d bytes' % (r.headers.get('Content-Type', ''), len(r.content)))

    interesting = {k: v for k, v in r.headers.items()
                   if k.lower().startswith('x-') or 'link' in k.lower()
                   or 'rate' in k.lower() or 'page' in k.lower()}
    if interesting:
        hr('Headers worth knowing (pagination / rate limits)')
        for k, v in interesting.items():
            print('  %-30s %s' % (k, v[:100]))

    try:
        data = r.json()
    except ValueError:
        hr('Body is not JSON -- first 2000 characters')
        print(r.text[:2000])
        return 1

    rows, key = find_rows(data, rows_key)
    hr('Envelope')
    if isinstance(data, dict):
        for k, v in data.items():
            t = typename(v)
            n = ' x%d' % len(v) if isinstance(v, (list, dict)) else ''
            print('  %-26s %s%s %s' % (k, t, n, example(v)))
        print('\n  record list: %s' % ('"%s" (%d rows)' % (key, len(rows))
                                       if rows else 'not found -- pass --rows-key'))
    else:
        print('  top level is a %s of %d' % (typename(data), len(data) if isinstance(data, list) else 1))

    if rows:
        hr('Record shape  (%d records merged -- "(in n/m)" marks optional fields)'
           % min(len(rows), 200))
        fields, n = merge_records(rows)
        for fk, (types, ex, cnt) in sorted(fields.items()):
            opt = '' if cnt == n else '  (in %d/%d)' % (cnt, n)
            print('  %-28s %-16s %-40s%s' % (fk, '|'.join(sorted(types)), ex, opt))

        nested = [k for k, (t, _, _) in fields.items()
                  if 'list' in t or 'object' in t]
        if nested:
            hr('Nested structures, expanded from the first record that has them')
            for k in nested:
                src = next((row for row in rows
                            if isinstance(row, dict) and row.get(k)), None)
                if not src:
                    continue
                print('\n  %s:' % k)
                print_shape({k: src[k]}, indent=2)

        if show:
            hr('First %d record(s), verbatim' % show)
            print(json.dumps(rows[:show], indent=2, ensure_ascii=False)[:8000])
    else:
        hr('Full shape')
        print_shape(data, indent=1)

    if save_as:
        save(save_as, data)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Discover the transactions API shape before building on it.')
    ap.add_argument('--path', help='endpoint path, e.g. /transactions')
    ap.add_argument('--params', nargs='*', default=[],
                    help='query parameters as key=value')
    ap.add_argument('--discover', action='store_true',
                    help='try a list of likely endpoint paths')
    ap.add_argument('--ping', action='store_true', help='check the base URL responds')
    ap.add_argument('--save', metavar='NAME',
                    help='write the raw response to samples/NAME_<timestamp>.json')
    ap.add_argument('--rows-key', help='field holding the records, e.g. data.transactions')
    ap.add_argument('--show', type=int, default=0,
                    help='also print the first N records verbatim')
    a = ap.parse_args(argv)

    params = parse_params(a.params)
    api = ApiClient.from_env()

    if a.ping:
        return cmd_ping(api)
    if a.discover:
        return cmd_discover(api, params)
    if a.path:
        return cmd_path(api, a.path, params, a.save, a.rows_key, a.show)
    ap.print_help()
    print('\nStart with:  python probe_api.py --ping')
    return 2


if __name__ == '__main__':
    sys.exit(main())
