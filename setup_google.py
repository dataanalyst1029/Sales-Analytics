"""Turn on Google sign-in, and name the first administrator.

    python setup_google.py
    python setup_google.py --status
    python setup_google.py --off

Until this is run the dashboard behaves exactly as it did before sign-in
existed: open, on 127.0.0.1 only. Once the client id and secret are in place
every page requires a signed-in, approved account.

What you need first, from https://console.cloud.google.com/apis/credentials :

  1. Create project (or pick one)  ->  APIs & Services  ->  OAuth consent screen
     - User type: Internal if everyone is on your Workspace, else External
     - Scopes: just the default openid / email / profile
  2. Credentials -> Create credentials -> OAuth client ID
     - Application type: Web application
     - Authorised redirect URI: exactly the one this script prints
  3. Copy the Client ID and Client Secret it shows you

The client secret is typed into a hidden prompt here and written straight to
`.env`. It is never echoed, never passed on a command line where the shell would
keep it, and `.env` is gitignored.
"""
import argparse
import getpass
import io
import os
import re
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, '.env')

KEYS = ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'ADMIN_EMAILS',
        'SESSION_SECRET', 'PUBLIC_BASE_URL')


def read_env():
    out = {}
    if os.path.exists(ENV):
        for line in io.open(ENV, encoding='utf-8'):
            m = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def write_env(values):
    """Rewrite only the keys we own, leaving every other line untouched."""
    lines, seen = [], set()
    if os.path.exists(ENV):
        for line in io.open(ENV, encoding='utf-8'):
            m = re.match(r'([A-Za-z_][A-Za-z0-9_]*)\s*=', line.strip())
            key = m.group(1) if m else None
            if key in values:
                lines.append('%s=%s\n' % (key, values[key]))
                seen.add(key)
            else:
                lines.append(line if line.endswith('\n') else line + '\n')
    for k, v in values.items():
        if k not in seen:
            lines.append('%s=%s\n' % (k, v))
    with io.open(ENV, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    try:
        os.chmod(ENV, 0o600)
    except OSError:
        pass


def status():
    env = read_env()
    on = bool(env.get('GOOGLE_CLIENT_ID') and env.get('GOOGLE_CLIENT_SECRET')
              and env.get('SESSION_SECRET'))
    print('Google sign-in: %s' % ('ON' if on else 'OFF (dashboard is open on this PC)'))
    for k in KEYS:
        v = env.get(k, '')
        if k == 'GOOGLE_CLIENT_SECRET':
            v = ('set, %d characters' % len(v)) if v else '(not set)'
        print('  %-22s %s' % (k, v or '(not set)'))
    try:
        import auth
        users = auth.list_users()
        print('\n%d account(s) on file:' % len(users))
        for u in users[:12]:
            print('  %-34s %-9s %s' % (u[1][:34], u[3], u[4]))
    except Exception as e:
        print('\n(could not read app_user: %s)' % e)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--status', action='store_true', help='what is configured')
    ap.add_argument('--off', action='store_true',
                    help='turn sign-in off again (the dashboard reopens)')
    ap.add_argument('--port', default='8001')
    a = ap.parse_args()

    if a.status:
        return status()
    if a.off:
        write_env({'GOOGLE_CLIENT_ID': '', 'GOOGLE_CLIENT_SECRET': ''})
        print('Sign-in is off. The dashboard is open again on this PC.')
        print('Accounts and their approvals are kept, so turning it back on '
              'restores exactly who had access.')
        return 0

    env = read_env()
    print(__doc__.split('What you need first')[0].strip())
    print('-' * 68)

    base = (env.get('PUBLIC_BASE_URL') or '').rstrip('/')
    print('\n1. Where will people open this dashboard?')
    print('   Just you, on this PC          ->  press Enter')
    print('   Others on the office network  ->  http://<this-pc-name>:%s' % a.port)
    got = input('\n   Base URL [http://localhost:%s]: ' % a.port).strip().rstrip('/')
    base = got or ''
    shown = base or 'http://localhost:%s' % a.port

    print('\n2. Put this EXACT line in the Google console, under')
    print('   "Authorised redirect URIs":\n')
    print('       %s/auth/callback\n' % shown)
    print('   Google matches it character for character. A trailing slash or')
    print('   http vs https will fail with redirect_uri_mismatch.')
    input('   Press Enter once that is saved in the console ...')

    print('\n3. The client id (it ends in .apps.googleusercontent.com)')
    cid = input('   GOOGLE_CLIENT_ID: ').strip()
    if not cid:
        print('\nNothing entered, so nothing was changed.')
        return 2
    if not cid.endswith('.apps.googleusercontent.com'):
        print('   Note: that does not look like a Google client id, but carrying on.')

    print('\n4. The client secret (nothing appears as you type)')
    sec = getpass.getpass('   GOOGLE_CLIENT_SECRET: ').strip()
    if len(sec) < 8:
        print('\n   That is too short to be a client secret. Nothing was changed.')
        return 2
    print('   Got %d characters.' % len(sec))

    print('\n5. Who administers access? These addresses are approved as ADMIN on')
    print('   their first sign-in, so somebody can approve everyone else.')
    print('   Without at least one, nobody could ever be let in.')
    admins = input('   ADMIN_EMAILS (comma separated): ').strip()
    if not admins:
        print('\n   No administrator given. Nothing was changed, because this would')
        print('   lock every account out at PENDING with nobody able to approve.')
        return 2

    values = {
        'GOOGLE_CLIENT_ID': cid,
        'GOOGLE_CLIENT_SECRET': sec,
        'ADMIN_EMAILS': admins,
        'SESSION_SECRET': env.get('SESSION_SECRET') or secrets.token_urlsafe(48),
        'PUBLIC_BASE_URL': base,
    }
    write_env(values)
    print('\nWritten to %s' % ENV)
    print('\nRestart the dashboard, then open %s' % shown)
    print('Sign in with one of: %s' % admins)
    print('\nEveryone else who signs in lands as PENDING and sees a holding page')
    print('until you approve them under "Users" in the header.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
