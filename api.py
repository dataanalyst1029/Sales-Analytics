"""Thin HTTP client for the internal transactions API.

Everything about *where* the API is and *how* it authenticates lives in `.env`,
so no URL or token is ever written into code. The client itself makes no
assumptions about the payload shape -- that is discovered with `probe_api.py`
and only then hard-coded into the ingestion layer.

    from api import ApiClient
    api = ApiClient.from_env()
    data = api.get_json('/transactions', {'from': '2026-09-01', 'to': '2026-09-01'})

Retries are deliberate and narrow: a 429 or a 5xx is retried with exponential
backoff because it is transient, and a 4xx is not, because retrying a bad
request just asks the same wrong question again.
"""
import base64
import json
import os
import random
import re
import time
import urllib.parse

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, '.env')

#: Retried, with backoff. Everything else fails fast and loud.
RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)


#: Settings this project reads. Listed so a real environment variable works
#: even when there is no `.env` at all -- which is how CI and a scheduled task
#: will run it.
ENV_KEYS = ('API_BASE_URL', 'API_AUTH_MODE', 'API_TOKEN', 'API_USER',
            'API_PASSWORD', 'API_AUTH_HEADER', 'API_AUTH_PARAM',
            'API_EXTRA_HEADERS', 'DATABASE_URL')


def load_env(path=ENV_FILE):
    """`.env` into a dict, with real environment variables winning.

    Kept dependency-free on purpose -- python-dotenv is not installed on this
    machine and one regex is cheaper than another package to keep current.
    """
    out = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    for k in set(ENV_KEYS) | set(out):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


class ApiError(RuntimeError):
    """An HTTP error the client decided not to retry."""

    def __init__(self, status, url, body):
        self.status = status
        self.url = url
        self.body = body
        super().__init__('HTTP %s from %s\n%s' % (status, url, body[:2000]))


class ApiClient:
    def __init__(self, base_url, auth_mode='bearer', token='', user='',
                 password='', auth_header='X-API-Key', auth_param='api_key',
                 extra_headers=None, timeout=60, verbose=True):
        self.base_url = (base_url or '').rstrip('/')
        self.auth_mode = (auth_mode or 'none').lower()
        self.token = token or ''
        self.user = user or ''
        self.password = password or ''
        self.auth_header = auth_header or 'X-API-Key'
        self.auth_param = auth_param or 'api_key'
        self.extra_headers = extra_headers or {}
        self.timeout = timeout
        self.verbose = verbose
        self.session = requests.Session()
        self.last_response = None

    @classmethod
    def from_env(cls, path=ENV_FILE, **over):
        env = load_env(path)
        if not env.get('API_BASE_URL'):
            raise SystemExit(
                'API_BASE_URL is not set.\n'
                'Copy .env.example to .env and fill in the base URL and token.')
        extra = {}
        if env.get('API_EXTRA_HEADERS'):
            try:
                extra = json.loads(env['API_EXTRA_HEADERS'])
            except ValueError as e:
                raise SystemExit('API_EXTRA_HEADERS is not valid JSON: %s' % e)
        kw = dict(
            base_url=env.get('API_BASE_URL', ''),
            auth_mode=env.get('API_AUTH_MODE', 'bearer'),
            token=env.get('API_TOKEN', ''),
            user=env.get('API_USER', ''),
            password=env.get('API_PASSWORD', ''),
            auth_header=env.get('API_AUTH_HEADER', 'X-API-Key'),
            auth_param=env.get('API_AUTH_PARAM', 'api_key'),
            extra_headers=extra,
        )
        kw.update(over)
        return cls(**kw)

    # -- request plumbing --------------------------------------------------

    def _headers(self):
        h = {'Accept': 'application/json',
             'User-Agent': 'ribshack-sales-analytics/0.1'}
        if self.auth_mode == 'bearer' and self.token:
            h['Authorization'] = 'Bearer %s' % self.token
        elif self.auth_mode == 'header' and self.token:
            h[self.auth_header] = self.token
        elif self.auth_mode == 'basic' and (self.user or self.password):
            raw = ('%s:%s' % (self.user, self.password)).encode('utf-8')
            h['Authorization'] = 'Basic %s' % base64.b64encode(raw).decode('ascii')
        h.update(self.extra_headers)
        return h

    def url_for(self, path):
        if re.match(r'^https?://', path or ''):
            return path
        return '%s/%s' % (self.base_url, (path or '').lstrip('/'))

    def request(self, path, params=None, method='GET', json_body=None,
                retries=4):
        """One HTTP call, with backoff on transient failures.

        Returns the `requests.Response`. Raises `ApiError` on a non-retryable
        error status so a caller never silently parses an error page as data.
        """
        url = self.url_for(path)
        params = dict(params or {})
        if self.auth_mode == 'query' and self.token:
            params[self.auth_param] = self.token

        delay = 1.0
        last = None
        for attempt in range(1, retries + 1):
            try:
                r = self.session.request(
                    method, url, params=params, json=json_body,
                    headers=self._headers(), timeout=self.timeout)
            except requests.RequestException as e:
                last = e
                if attempt == retries:
                    raise
                self._log('  network error (%s), retry %d/%d in %.0fs'
                          % (type(e).__name__, attempt, retries, delay))
                time.sleep(delay)
                delay = min(delay * 2, 30) + random.random()
                continue

            self.last_response = r
            if r.status_code in RETRY_STATUS and attempt < retries:
                wait = delay
                # Honour Retry-After when the server sends one -- guessing
                # shorter than it asked is how you get rate-limited harder.
                ra = r.headers.get('Retry-After')
                if ra:
                    try:
                        wait = max(wait, float(ra))
                    except ValueError:
                        pass
                self._log('  HTTP %s, retry %d/%d in %.0fs'
                          % (r.status_code, attempt, retries, wait))
                time.sleep(wait)
                delay = min(delay * 2, 30) + random.random()
                continue

            if r.status_code >= 400:
                raise ApiError(r.status_code, r.url, r.text)
            return r

        raise last if last else RuntimeError('unreachable')

    def get_json(self, path, params=None):
        r = self.request(path, params)
        ctype = r.headers.get('Content-Type', '')
        if 'json' not in ctype.lower():
            raise ApiError(r.status_code, r.url,
                           'expected JSON, got Content-Type: %s\n%s'
                           % (ctype or '(none)', r.text[:1000]))
        return r.json()

    # -- pagination --------------------------------------------------------

    def paginate(self, path, params=None, rows_key=None, page_param='page',
                 limit_param='limit', limit=200, max_pages=10000,
                 style='page'):
        """Yield rows across pages.

        `style` is one of:
          page   -- ?page=1,2,3...   (stops on a short or empty page)
          offset -- ?offset=0,200... (stops on a short or empty page)
          cursor -- follows a `next`/`next_cursor` field in the response

        `rows_key` is the field holding the list of records; when omitted it is
        guessed per response by `find_rows`. Both are settled by `probe_api.py`
        before ingestion is written, so guessing is a convenience, not a
        dependency.
        """
        params = dict(params or {})
        if limit_param:
            params[limit_param] = limit
        page, offset, cursor, seen = 1, 0, None, 0

        for _ in range(max_pages):
            q = dict(params)
            if style == 'page':
                q[page_param] = page
            elif style == 'offset':
                q['offset'] = offset
            elif style == 'cursor' and cursor:
                q['cursor'] = cursor

            data = self.get_json(path, q)
            rows, key = find_rows(data, rows_key)
            if not rows:
                return
            for row in rows:
                yield row
            seen += len(rows)
            self._log('  page %-4d %5d rows (%d total)' % (page, len(rows), seen))

            if style == 'cursor':
                cursor = _first(data, ('next_cursor', 'nextCursor', 'next',
                                       'next_page_token', 'cursor'))
                if not cursor:
                    return
            else:
                if len(rows) < limit:
                    return
                page += 1
                offset += len(rows)

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)


def _first(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            v = obj.get(k)
            if v:
                return v
    return None


#: Field names APIs commonly hang the record list off.
ROW_KEYS = ('data', 'results', 'items', 'records', 'rows', 'transactions',
            'transaction', 'sales', 'orders', 'receipts', 'content', 'list',
            'payload', 'value')


def find_rows(data, rows_key=None):
    """(list of records, the key it came from) for a paged JSON response.

    A bare list is returned as-is. Otherwise the named key wins; failing that,
    the first known wrapper key holding a list of objects; failing that, the
    longest list of objects anywhere one level down.
    """
    if rows_key:
        cur = data
        for part in rows_key.split('.'):
            cur = (cur or {}).get(part) if isinstance(cur, dict) else None
        return (cur if isinstance(cur, list) else []), rows_key
    if isinstance(data, list):
        return data, ''
    if not isinstance(data, dict):
        return [], ''
    for k in ROW_KEYS:
        v = data.get(k)
        if isinstance(v, list) and (not v or isinstance(v[0], dict)):
            return v, k
        # one nesting level, e.g. {"data": {"transactions": [...]}}
        if isinstance(v, dict):
            for k2 in ROW_KEYS:
                v2 = v.get(k2)
                if isinstance(v2, list) and (not v2 or isinstance(v2[0], dict)):
                    return v2, '%s.%s' % (k, k2)
    best, best_key = [], ''
    for k, v in data.items():
        if isinstance(v, list) and v and isinstance(v[0], dict) and len(v) > len(best):
            best, best_key = v, k
    return best, best_key


def redact(url):
    """A URL safe to print -- token-ish query values masked."""
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    safe = [(k, '<redacted>' if re.search(r'key|token|secret|pass|auth', k, re.I)
             else v) for k, v in q]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(safe),
         parts.fragment))
