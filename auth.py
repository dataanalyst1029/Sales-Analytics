"""Google sign-in, and the approval gate behind it.

Nobody reaches the dashboard by knowing its URL. Signing in with Google proves
only who someone is; whether they may look at the group's sales position is a
separate decision an admin makes. A new account is created PENDING and sees a
holding page until then.

The flow, server side throughout:

    /login              the sign-in page
    /auth/start         redirect to Google, with a one-time `state`
    /auth/callback      exchange the code, upsert the user, set the cookie
    /pending            what a PENDING or REJECTED account sees
    /admin/users        approve, reject, suspend, promote
    /logout             drop the session

Configuration lives in `.env`, written by `setup_google.py`:

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET   from Google Cloud Console
    ADMIN_EMAILS                             comma-separated; these become ADMIN
                                             on first sign-in, so there is
                                             somebody able to approve the rest
    SESSION_SECRET                           signs the cookie
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse

import psycopg
import requests

from api import load_env
from ingest import database_url

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'

#: How long a signed-in session lasts before Google is asked again.
SESSION_HOURS = 12

#: One-time `state` values, to prove the callback answers a request we made and
#: not a link someone was sent. Kept in memory: they live seconds, and a restart
#: losing them costs a re-click, not a security hole.
_pending_states = {}
STATE_TTL = 600


def config():
    env = load_env()
    return {
        'client_id': env.get('GOOGLE_CLIENT_ID', ''),
        'client_secret': env.get('GOOGLE_CLIENT_SECRET', ''),
        'admins': [e.strip().lower() for e in
                   (env.get('ADMIN_EMAILS', '') or '').split(',') if e.strip()],
        'secret': env.get('SESSION_SECRET', ''),
        'base': (env.get('PUBLIC_BASE_URL', '') or '').rstrip('/'),
    }


def configured():
    c = config()
    return bool(c['client_id'] and c['client_secret'] and c['secret'])


def redirect_uri(host_header, cfg=None):
    """Where Google sends the browser back.

    Derived from the request's own Host header rather than hardcoded, so the
    same build works on localhost and on a machine other people reach. It must
    match an Authorised redirect URI in the Google console exactly.
    """
    cfg = cfg or config()
    if cfg['base']:
        return cfg['base'] + '/auth/callback'
    return 'http://%s/auth/callback' % (host_header or 'localhost:8001')


# -- session cookie ---------------------------------------------------------

def _sign(payload, secret):
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return '%s.%s' % (raw, sig)


def _unsign(token, secret):
    """The payload, or None. Compared with compare_digest so a wrong signature
    takes the same time to reject as a right one."""
    try:
        raw, sig = (token or '').rsplit('.', 1)
    except ValueError:
        return None
    want = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return None
    try:
        pad = '=' * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
    except (ValueError, TypeError):
        return None
    if payload.get('exp', 0) < time.time():
        return None
    return payload


def make_session(email, secret):
    return _sign({'email': email, 'exp': time.time() + SESSION_HOURS * 3600}, secret)


def read_session(cookie_header, secret):
    for part in (cookie_header or '').split(';'):
        k, _, v = part.strip().partition('=')
        if k == 'sa_session':
            return _unsign(v, secret)
    return None


# -- the user table ---------------------------------------------------------

def db():
    return psycopg.connect(database_url())


def get_user(email):
    if not email:
        return None
    with db() as c:
        r = c.execute("""SELECT id, email, name, status::text, role::text, picture_url
                           FROM app_user WHERE lower(email) = lower(%s)""",
                      (email,)).fetchone()
    if not r:
        return None
    return dict(zip(('id', 'email', 'name', 'status', 'role', 'picture'), r))


def upsert_from_google(claims, admins):
    """Record the sign-in and return the user.

    An address listed in ADMIN_EMAILS is admitted as an approved ADMIN on first
    sign-in. Without that there is nobody able to approve anybody, and the first
    person to install this would be locked out of their own dashboard.

    Everyone else arrives PENDING. A returning user keeps whatever status they
    already have -- signing in again is not a way to reset a rejection.
    """
    email = (claims.get('email') or '').strip()
    if not email:
        return None
    is_admin = email.lower() in admins
    with db() as c:
        c.execute("""
            INSERT INTO app_user (google_sub, email, name, picture_url, status, role,
                                  last_login_at, login_count, decided_at, decided_by)
            VALUES (%s, %s, %s, %s, %s::"UserStatus", %s::"UserRole", now(), 1,
                    CASE WHEN %s THEN now() ELSE NULL END,
                    CASE WHEN %s THEN 'ADMIN_EMAILS' ELSE NULL END)
            ON CONFLICT (email) DO UPDATE
               SET google_sub  = COALESCE(app_user.google_sub, EXCLUDED.google_sub),
                   name        = COALESCE(EXCLUDED.name, app_user.name),
                   picture_url = COALESCE(EXCLUDED.picture_url, app_user.picture_url),
                   last_login_at = now(),
                   login_count = app_user.login_count + 1,
                   -- An ADMIN_EMAILS address is promoted even if it signed in
                   -- earlier as a pending viewer; nothing else changes status.
                   status = CASE WHEN %s THEN 'APPROVED'::"UserStatus"
                                 ELSE app_user.status END,
                   role   = CASE WHEN %s THEN 'ADMIN'::"UserRole"
                                 ELSE app_user.role END
            """, (claims.get('sub'), email, claims.get('name'),
                  claims.get('picture'),
                  'APPROVED' if is_admin else 'PENDING',
                  'ADMIN' if is_admin else 'VIEWER',
                  is_admin, is_admin, is_admin, is_admin))
        c.commit()
    return get_user(email)


def list_users():
    with db() as c:
        return c.execute("""SELECT id, email, name, status::text, role::text,
                                   requested_at, decided_at, decided_by,
                                   last_login_at, login_count
                              FROM app_user
                             ORDER BY (status = 'PENDING') DESC, requested_at DESC"""
                         ).fetchall()


def set_status(user_id, status, role, by_email):
    with db() as c:
        c.execute("""UPDATE app_user
                        SET status = %s::"UserStatus", role = %s::"UserRole",
                            decided_at = now(), decided_by = %s
                      WHERE id = %s""", (status, role, by_email, user_id))
        c.commit()


def pending_count():
    with db() as c:
        return c.execute("SELECT count(*) FROM app_user WHERE status = 'PENDING'"
                         ).fetchone()[0]


# -- the Google round trip --------------------------------------------------

def start_url(host_header):
    cfg = config()
    state = secrets.token_urlsafe(24)
    _pending_states[state] = time.time()
    for k, t in list(_pending_states.items()):
        if time.time() - t > STATE_TTL:
            _pending_states.pop(k, None)
    q = urllib.parse.urlencode({
        'client_id': cfg['client_id'],
        'redirect_uri': redirect_uri(host_header, cfg),
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        # Always show the chooser: on a shared PC, silently reusing whoever
        # signed in last is how the wrong person ends up looking at this.
        'prompt': 'select_account',
    })
    return '%s?%s' % (AUTH_URL, q)


def exchange(code, state, host_header):
    """(claims, error). Claims come from Google over TLS, not from the browser.

    The `state` must be one this server issued, which is what stops a link
    someone was emailed from completing a sign-in on their behalf.

    The id_token's signature is not re-verified here. It arrived in the body of
    a direct HTTPS response from Google's token endpoint, over a connection this
    process opened and verified -- it is not a value the client handed us, so
    there is no untrusted path for a forged token to arrive by.
    """
    if state not in _pending_states:
        return None, 'That sign-in link has expired or was not started here. Try again.'
    _pending_states.pop(state, None)

    cfg = config()
    try:
        r = requests.post(TOKEN_URL, timeout=30, data={
            'code': code,
            'client_id': cfg['client_id'],
            'client_secret': cfg['client_secret'],
            'redirect_uri': redirect_uri(host_header, cfg),
            'grant_type': 'authorization_code',
        })
    except requests.RequestException as e:
        return None, 'Could not reach Google: %s' % type(e).__name__
    if r.status_code >= 400:
        detail = ''
        try:
            j = r.json()
            detail = j.get('error_description') or j.get('error') or ''
        except ValueError:
            detail = r.text[:200]
        return None, 'Google refused the sign-in: %s' % detail

    tok = r.json().get('id_token')
    if not tok:
        return None, 'Google returned no id_token.'
    try:
        body = tok.split('.')[1]
        claims = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except (ValueError, IndexError, TypeError):
        return None, 'Could not read the id_token.'
    if not claims.get('email_verified', True):
        return None, 'That Google account has no verified email address.'
    return claims, None
