"""Set up `.env` interactively, then prove the credentials work.

    python setup_env.py

Asks for the base URL and the API key, writes `.env`, and immediately makes a
real request so you find out now -- not halfway through a backfill -- whether
the key is accepted.

The key is typed straight into this prompt and written straight to `.env`. It is
not echoed to the screen, not passed on a command line (where it would land in
shell history), and not printed back. `.env` is gitignored.
"""
import getpass
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, '.env')

AUTH_MODES = ('bearer', 'header', 'basic', 'query', 'none')


def ask(prompt, default=''):
    d = ' [%s]' % default if default else ''
    try:
        v = input('%s%s: ' % (prompt, d)).strip()
    except EOFError:
        v = ''
    return v or default


#: Shorter than this and it is a slip, not a secret. Real API secrets are
#: long; a stray character from a failed paste is one or two.
MIN_SECRET = 12
#: Longer than this and the clipboard held something else -- a page of text, a
#: whole config file. Real secrets are not paragraphs.
MAX_SECRET = 200


def ask_secret(prompt):
    """Read without echoing, and refuse something too short to be a secret.

    Nothing appears on screen while typing, so a paste that silently failed
    looks exactly like a paste that worked. Checking the length is the only
    feedback available that does not put the secret on the screen.
    """
    if not sys.stdin.isatty():
        return input('%s: ' % prompt).strip()
    while True:
        v = getpass.getpass('%s (nothing appears as you type): ' % prompt).strip()
        if MIN_SECRET <= len(v) <= MAX_SECRET and '\n' not in v:
            print('  Got %d characters.' % len(v))
            return v
        if len(v) > MAX_SECRET or '\n' in v:
            print('  %d characters came through, which is far too long to be a'
                  % len(v))
            print('  key secret -- the clipboard held something else, like a')
            print('  page of text. A real secret is one line, 30-60 characters.')
        elif not v:
            print('  Nothing came through. In Command Prompt use RIGHT-CLICK to')
            print('  paste -- Ctrl+V often does not work at a hidden prompt.')
        else:
            print('  Only %d character%s came through, which is too short to be'
                  % (len(v), '' if len(v) == 1 else 's'))
            print('  a real key secret -- the paste probably failed. Use')
            print('  RIGHT-CLICK to paste, not Ctrl+V.')
        again = input('  Try again? (Y/n, or "force" to keep it anyway): ').strip().lower()
        if again == 'force':
            return v
        if again in ('n', 'no'):
            return v


def normalise_base(url):
    url = (url or '').strip().rstrip('/')
    if url and not re.match(r'^https?://', url):
        url = 'https://' + url
    return url


def write_env(values):
    lines = [
        '# Written by setup_env.py. Gitignored -- keep the real key out of git.',
        '',
        'API_BASE_URL=%s' % values['API_BASE_URL'],
        'API_AUTH_MODE=%s' % values['API_AUTH_MODE'],
        'API_TOKEN=%s' % values['API_TOKEN'],
    ]
    if values['API_AUTH_MODE'] == 'header':
        lines.append('API_AUTH_HEADER=%s' % values['API_AUTH_HEADER'])
    if values['API_AUTH_MODE'] == 'query':
        lines.append('API_AUTH_PARAM=%s' % values['API_AUTH_PARAM'])
    if values['API_AUTH_MODE'] == 'basic':
        lines.append('API_USER=%s' % values.get('API_USER', ''))
        lines.append('API_PASSWORD=%s' % values['API_TOKEN'])
    lines.append('')
    with open(ENV_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    # Best effort on Windows; harmless if it does nothing.
    try:
        os.chmod(ENV_FILE, 0o600)
    except OSError:
        pass


#: Tried in order until one gives a verdict. The root is useless for this --
#: an API that 404s its own root 404s it for everyone, key or no key, so a 404
#: there says nothing at all about whether the credentials work. Only an
#: endpoint that demands auth can answer the question.
VERIFY_PATHS = ('/transactions', '/sales', '/orders', '/stores', '/me')


def verify():
    """Prove the key is accepted, by asking something that requires it."""
    # Imported here so a missing `requests` is reported after the file is
    # written rather than instead of it.
    from api import ApiClient, ApiError

    api = ApiClient.from_env()
    print('\nChecking %s ...' % api.base_url)

    refused = None
    for path in VERIFY_PATHS:
        try:
            r = api.request(path, {'limit': 1}, retries=1)
        except ApiError as e:
            if e.status in (401, 403):
                refused = (path, e.status, e.body)
                print('  %-16s HTTP %s  refused' % (path, e.status))
                continue
            print('  %-16s HTTP %s' % (path, e.status))
            continue
        except Exception as e:
            print('  Could not connect: %s: %s' % (type(e).__name__, e))
            print('  Check the base URL, and whether this needs the company VPN.')
            return False

        print('  %-16s HTTP %s  ACCEPTED' % (path, r.status_code))
        print('\n  The key works. %s returned data.' % path)
        return True

    if refused:
        path, status, body = refused
        print('\n  HTTP %s -- the host is up but REFUSED the key.' % status)
        print('  Response: %s' % body[:300])
        print('\n  Usual causes, in order of likelihood:')
        print('   * the secret was not pasted in full (right-click pastes in cmd)')
        print('   * you used the key ID where the key SECRET belongs')
        print('   * wrong API_AUTH_MODE -- this API takes bearer OR basic')
        print('   * the key lacks the scope for this endpoint')
        return False

    print('\n  Nothing here demanded a key, so this cannot confirm it works.')
    print('  Run:  python probe_api.py --discover')
    return False


def main():
    print(__doc__.strip().split('\n')[0])
    print('=' * 64)

    if os.path.exists(ENV_FILE):
        print('\n.env already exists.')
        if ask('Overwrite it? (y/N)', 'N').lower() not in ('y', 'yes'):
            print('Left alone. Nothing changed.')
            return 0

    print('\n1. Where is the API?')
    print('   The host your key belongs to, e.g. https://api.example.com')
    print('   or https://example.com/api/v1 -- no trailing slash needed.')
    base = normalise_base(ask('\n   API_BASE_URL'))
    if not base:
        print('\nNo URL given, so there is nothing to write. Nothing changed.')
        return 2

    print('\n2. How does it want the key?')
    print('   bearer  Authorization: Bearer <key>     (most common)')
    print('   header  X-API-Key: <key>                (or another header name)')
    print('   query   ?api_key=<key>')
    print('   basic   HTTP basic auth')
    mode = ask('\n   API_AUTH_MODE', 'bearer').lower()
    if mode not in AUTH_MODES:
        print('   Not one of %s -- using bearer.' % ', '.join(AUTH_MODES))
        mode = 'bearer'

    values = {'API_BASE_URL': base, 'API_AUTH_MODE': mode,
              'API_AUTH_HEADER': 'X-API-Key', 'API_AUTH_PARAM': 'api_key'}
    if mode == 'header':
        values['API_AUTH_HEADER'] = ask('   Header name', 'X-API-Key')
    if mode == 'query':
        values['API_AUTH_PARAM'] = ask('   Query parameter name', 'api_key')
    if mode == 'basic':
        values['API_USER'] = ask('   Username')

    print('\n3. The key itself.')
    print('   Paste the FULL secret, not the key ID from the portal listing.')
    print('   If you only have the short id, generate a new key and copy the')
    print('   secret it shows you once.')
    values['API_TOKEN'] = ask_secret('\n   API_TOKEN')

    write_env(values)
    print('\nWrote %s  (%d characters of key, not shown)'
          % (ENV_FILE, len(values['API_TOKEN'])))

    ok = verify()
    print('\n' + '-' * 64)
    if ok:
        print('Next:  python probe_api.py --discover')
    else:
        print('Fix the above, then run this again:  python setup_env.py')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
