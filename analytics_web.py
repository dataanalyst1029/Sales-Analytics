"""Browser dashboard over the sales analytics warehouse.

    python analytics_web.py              then open http://localhost:8001
    python analytics_web.py --port 8123 --no-browser

Binds to 127.0.0.1, so it is reachable from this PC only. Port 8001 rather than
8000, so this and the StoreHub recon UI can run side by side.

Four views, one per question the warehouse was built to answer:

    /            Overview     how sales are moving
    /hours       Hours        when the rush actually is
    /products    Products     what sells, and in what baskets
    /exceptions  Exceptions   flags, voids, discounts, statutory relief

Every page reads the same From/To, estate and branch filters, and every figure
is a query against the warehouse -- nothing is cached or pre-aggregated, so a
number on screen is always what the tables currently hold.

Nothing here invents coverage. The Alliance estate has no line detail and, for
Jul-Aug 2026, no clock; where a measure cannot be supported the page says so
instead of drawing an empty chart or averaging over rows that cannot answer.
"""
import argparse
import datetime as dt
import decimal
import html
import os
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

import auth
from ingest import database_url

# -- palette ---------------------------------------------------------------
# Validated categorical slots, sequential blue ramp, status colours and chrome.
# Roles are CSS custom properties so light/dark swap in one place.

CSS = """
:root {
  color-scheme: light;
  --surface-1:#fcfcfb; --plane:#f9f9f7;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --seq-100:#cde2fb; --seq-250:#86b6ef; --seq-400:#3987e5;
  --seq-500:#256abf; --seq-600:#184f95; --seq-700:#0d366b;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --up:#006300;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-1:#1a1a19; --plane:#0d0d0d;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --up:#0ca30c;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-1:#1a1a19; --plane:#0d0d0d;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,0.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
  --up:#0ca30c;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--plane); color:var(--text-primary);
  font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
header { background:var(--surface-1); border-bottom:1px solid var(--border);
  padding:14px 20px 0; position:sticky; top:0; z-index:20; }
h1 { margin:0 0 2px; font-size:17px; letter-spacing:-0.01em; }
.sub { color:var(--text-secondary); font-size:12px; margin-bottom:10px; }
nav { display:flex; gap:2px; }
nav a { padding:8px 14px; text-decoration:none; color:var(--text-secondary);
  border-bottom:2px solid transparent; font-size:13px; }
nav a.on { color:var(--text-primary); border-bottom-color:var(--s1); font-weight:600; }
nav a:hover { color:var(--text-primary); }
main { padding:18px 20px 60px; max-width:1500px; }
form.filters { display:flex; flex-wrap:wrap; gap:10px; align-items:end;
  background:var(--surface-1); border:1px solid var(--border); border-radius:8px;
  padding:12px 14px; margin-bottom:18px; }
label { display:block; font-size:11px; color:var(--muted); margin-bottom:3px;
  text-transform:uppercase; letter-spacing:0.04em; }
input,select,button { font:inherit; padding:6px 9px; border-radius:6px;
  border:1px solid var(--border); background:var(--surface-1);
  color:var(--text-primary); }
button { background:var(--s1); color:#fff; border-color:transparent;
  cursor:pointer; font-weight:600; padding:7px 16px; }
button:hover { filter:brightness(1.08); }
a.chip { font-size:12px; padding:5px 10px; border:1px solid var(--border);
  border-radius:99px; text-decoration:none; color:var(--text-secondary); }
a.chip:hover { color:var(--text-primary); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
  gap:12px; margin-bottom:18px; }
.tile { background:var(--surface-1); border:1px solid var(--border);
  border-radius:8px; padding:13px 15px; }
.tile .k { font-size:11px; color:var(--muted); text-transform:uppercase;
  letter-spacing:0.04em; }
.tile .v { font-size:23px; font-weight:650; letter-spacing:-0.02em;
  margin-top:3px; font-variant-numeric:tabular-nums; }
.tile .n { font-size:11px; color:var(--text-secondary); margin-top:2px; }
.card { background:var(--surface-1); border:1px solid var(--border);
  border-radius:8px; padding:15px 17px; margin-bottom:16px; }
.card h2 { margin:0 0 3px; font-size:14px; }
.card .note { color:var(--text-secondary); font-size:12px; margin:0 0 12px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media (max-width:1040px){ .grid2 { grid-template-columns:1fr; } }
table { border-collapse:collapse; width:100%; font-size:13px; }
th { text-align:left; font-size:11px; color:var(--muted); font-weight:600;
  text-transform:uppercase; letter-spacing:0.04em; padding:6px 8px;
  border-bottom:1px solid var(--border); position:sticky; top:0;
  background:var(--surface-1); }
td { padding:6px 8px; border-bottom:1px solid var(--grid); }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
.scroll { max-height:460px; overflow:auto; }
.bar { height:7px; border-radius:4px; background:var(--s1); display:block; }
.legend { display:flex; gap:14px; font-size:12px; color:var(--text-secondary);
  margin-bottom:8px; flex-wrap:wrap; }
.legend i { width:9px; height:9px; border-radius:2px; display:inline-block;
  margin-right:5px; }
.warn { border-left:3px solid var(--warning); padding:9px 12px; font-size:12.5px;
  background:var(--surface-1); border-radius:0 6px 6px 0; margin-bottom:14px;
  color:var(--text-secondary); }
.empty { color:var(--text-secondary); font-size:13px; padding:26px 0;
  text-align:center; }
/* Findings flow across the card's full width. auto-fit rather than a fixed
   column count, so one finding does not sit alone in a 1500px row and five do
   not squeeze into slivers. */
.ops { display:grid; grid-template-columns:repeat(auto-fit,minmax(310px,1fr));
  gap:18px 24px; align-items:start; }
.ops > div { break-inside:avoid; }
.ops table { font-size:12px; }
.ops td { padding:3px 6px; }
/* Branch multi-select. Checkboxes inside the form, so the browser submits the
   selection itself -- the script only opens, filters and counts. */
.ms { position:relative; }
.ms-btn { background:var(--surface-1); color:var(--text-primary); font-weight:400;
  border:1px solid var(--border); min-width:210px; text-align:left;
  display:flex; justify-content:space-between; gap:8px; align-items:center; }
.ms-btn .caret { color:var(--muted); }
.ms-panel { position:absolute; z-index:40; top:calc(100% + 4px); left:0;
  width:340px; background:var(--surface-1); border:1px solid var(--border);
  border-radius:8px; box-shadow:0 8px 28px rgba(0,0,0,.28); padding:10px; }
.ms-search { width:100%; margin-bottom:8px; }
.ms-acts { display:flex; gap:12px; font-size:11.5px; margin-bottom:8px;
  flex-wrap:wrap; }
.ms-acts a { color:var(--s1); text-decoration:none; }
.ms-acts a:hover { text-decoration:underline; }
.ms-list { max-height:280px; overflow:auto; border-top:1px solid var(--grid);
  border-bottom:1px solid var(--grid); padding:4px 0; }
.ms-row { display:flex; gap:8px; align-items:center; padding:4px 6px;
  border-radius:5px; font-size:13px; cursor:pointer; text-transform:none;
  letter-spacing:0; color:var(--text-primary); margin:0; }
.ms-row:hover { background:color-mix(in srgb,var(--s1) 10%, transparent); }
.ms-row input { margin:0; }
.ms-foot { display:flex; justify-content:space-between; align-items:center;
  padding-top:9px; font-size:11.5px; color:var(--muted); }
/* Sign-in and the approval gate */
.gate { max-width:460px; margin:9vh auto; padding:0 20px; text-align:center; }
.gate .card { padding:30px 28px; }
.gate h1 { font-size:20px; margin:0 0 6px; }
.gate p { color:var(--text-secondary); font-size:13.5px; margin:0 0 18px;
  line-height:1.6; }
.gsi { display:inline-flex; align-items:center; gap:11px; background:#fff;
  color:#1f1f1f; border:1px solid #dadce0; border-radius:6px; padding:11px 20px;
  font:500 14px/1 "Google Sans",Roboto,-apple-system,sans-serif;
  text-decoration:none; }
.gsi:hover { background:#f8f9fa; box-shadow:0 1px 3px rgba(60,64,67,.3); }
.who { margin-left:auto; display:flex; align-items:center; gap:10px;
  font-size:12px; color:var(--text-secondary); }
.who a { color:var(--text-secondary); text-decoration:none; }
.who a:hover { color:var(--text-primary); text-decoration:underline; }
.pill { background:var(--critical); color:#fff; border-radius:99px;
  padding:1px 7px; font-size:10.5px; font-weight:700; }
.utable td { vertical-align:middle; }
.badge { font-size:11px; padding:2px 8px; border-radius:99px; font-weight:600; }
.b-PENDING { background:color-mix(in srgb,var(--warning) 22%,transparent);
  color:var(--warning); }
.b-APPROVED { background:color-mix(in srgb,var(--good) 20%,transparent);
  color:var(--good); }
.b-REJECTED, .b-SUSPENDED {
  background:color-mix(in srgb,var(--critical) 20%,transparent);
  color:var(--critical); }
button.mini { padding:4px 11px; font-size:12px; font-weight:600; }
button.ghost { background:transparent; color:var(--text-secondary);
  border:1px solid var(--border); }
button.ghost:hover { color:var(--text-primary); }
#tip { position:fixed; pointer-events:none; opacity:0; transition:opacity .08s;
  background:var(--text-primary); color:var(--surface-1); font-size:12px;
  padding:6px 9px; border-radius:6px; z-index:99; white-space:nowrap;
  font-variant-numeric:tabular-nums; }
svg text { font-size:11px; fill:var(--muted); }
svg .lbl { fill:var(--text-secondary); }
"""

JS = """
const tip=document.createElement('div'); tip.id='tip'; document.body.appendChild(tip);
function wire(){
  document.querySelectorAll('[data-tip]').forEach(el=>{
    el.addEventListener('mousemove',e=>{
      tip.textContent=el.getAttribute('data-tip'); tip.style.opacity=1;
      let x=e.clientX+14, y=e.clientY-12;
      if(x+tip.offsetWidth>innerWidth-8) x=e.clientX-tip.offsetWidth-14;
      tip.style.left=x+'px'; tip.style.top=y+'px';
    });
    el.addEventListener('mouseleave',()=>tip.style.opacity=0);
  });
}
wire();
const msb=document.getElementById('msb'), msp=document.getElementById('msp');
if(msb){
  const rows=()=>[...msp.querySelectorAll('.ms-row')];
  const boxes=()=>[...msp.querySelectorAll('input[name=store]')];
  const count=()=>{
    const n=boxes().filter(b=>b.checked).length;
    document.getElementById('mscount').textContent =
      n===0 ? 'No branch ticked means all of them' : n+' selected';
    rows().forEach(r=>r.classList.toggle('on', r.querySelector('input').checked));
  };
  msb.addEventListener('click',e=>{e.stopPropagation(); msp.hidden=!msp.hidden; count();});
  document.addEventListener('click',e=>{ if(!msp.hidden && !msp.contains(e.target)) msp.hidden=true; });
  msp.addEventListener('click',e=>e.stopPropagation());
  msp.querySelector('.ms-search').addEventListener('input',e=>{
    const q=e.target.value.toLowerCase();
    rows().forEach(r=>{r.style.display=r.dataset.name.includes(q)?'':'none';});
  });
  msp.querySelectorAll('.ms-acts a').forEach(a=>a.addEventListener('click',e=>{
    e.preventDefault(); const act=a.dataset.act;
    boxes().forEach(b=>{
      const vis=b.closest('.ms-row').style.display!=='none';
      if(act==='all') b.checked=vis;
      else if(act==='none') b.checked=false;
      else b.checked = vis && b.dataset.estate===act;
    });
    count();
  }));
  msp.addEventListener('change',count);
  count();
}
const ops=document.getElementById('ops');
if(ops){
  const pp=location.pathname, base=(pp.length>1&&pp.slice(-1)==='/')?pp.slice(0,-1):pp;
  const view={'/':'overview','/hours':'hours','/products':'products',
              '/exceptions':'exceptions'}[base]||'overview';
  fetch('/insights'+(location.search||'?')+'&view='+view)
    .then(r=>r.text())
    .then(h=>{ops.innerHTML=h; wire();})
    .catch(e=>{ops.innerHTML='<div class="empty">Could not load the analysis.</div>';});
}
"""


# -- helpers ---------------------------------------------------------------

def peso(v):
    return '₱' + format(float(v or 0), ',.2f')


def peso0(v):
    return '₱' + format(float(v or 0), ',.0f')


def num(v):
    return format(int(v or 0), ',')


def esc(s):
    return html.escape(str(s if s is not None else ''))


def short(v):
    v = float(v or 0)
    for cut, suf in ((1e9, 'B'), (1e6, 'M'), (1e3, 'k')):
        if abs(v) >= cut:
            return '%.1f%s' % (v / cut, suf)
    return '%.0f' % v


#: Postgres cancels any dashboard query that runs longer than this, so one huge
#: date range cannot tie up the database for everyone else.
QUERY_TIMEOUT_S = int(os.environ.get('QUERY_TIMEOUT_SECONDS', 60))

#: How many requests may be worked on at once. Each holds at most one database
#: connection at a time, so this also caps the connections the dashboard can
#: take from Postgres (whose default limit is 100). Extra requests wait their
#: turn for up to QUEUE_WAIT_S, then get a "busy, try again" page rather than
#: piling onto the database until it refuses everyone.
MAX_BUSY = int(os.environ.get('MAX_CONCURRENT_REQUESTS', 8))
QUEUE_WAIT_S = 30
_busy = threading.BoundedSemaphore(MAX_BUSY)


class DB:
    def __init__(self):
        self.url = database_url()

    def q(self, sql, args=()):
        with psycopg.connect(self.url, connect_timeout=10,
                             options='-c statement_timeout=%d'
                                     % (QUERY_TIMEOUT_S * 1000)) as c:
            return c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None


# -- filters ---------------------------------------------------------------

class Filters:
    def __init__(self, db, qs, table='transaction'):
        # Bounds come from the table THIS view reads. `sales_summary` reaches
        # back to Jan 2025 while `transaction` starts that October, so a single
        # hardcoded source would make "Everything" quietly skip nine months on
        # the Alliance products tab and mislabel what is on file.
        lo, hi = db.one('SELECT min(business_date), max(business_date) '
                        'FROM "%s"' % table) or (None, None)
        self.data_lo, self.data_hi = lo, hi
        today = hi or dt.date.today()
        default_lo = max(lo, today - dt.timedelta(days=29)) if lo else today

        self.frm = self._date(qs.get('from', [None])[0], default_lo)
        self.to = self._date(qs.get('to', [None])[0], today)
        self.estate = qs.get('estate', [''])[0]
        if self.estate not in ('STOREHUB', 'ALLIANCE'):
            self.estate = ''
        #: True when the page dictates the estate rather than the reader
        #: choosing it. A forced value must not travel to the other tabs.
        self.estate_forced = False
        #: Branches dropped because they belong to the other estate.
        self.dropped_stores = 0
        self.bounds_table = table

        # Repeated ?store= parameters, so the browser's own form submission
        # carries a multi-select with no JavaScript involved in the round trip.
        # A single ?store=9 from an older bookmark still works.
        self.stores = []
        for v in qs.get('store', []):
            for part in str(v).split(','):
                try:
                    n = int(part)
                except (ValueError, TypeError):
                    continue
                if n and n not in self.stores:
                    self.stores.append(n)
        # An estate and a branch list can contradict each other: pick Alliance
        # branches, switch the estate to StoreHub, and every figure reads zero
        # with nothing to say why. The narrower filter wins and the discarded
        # branches are counted, so the page can explain itself.
        if self.estate and self.stores:
            self._drop_foreign_stores(db, self.estate)

    @staticmethod
    def _date(v, fallback):
        try:
            return dt.date.fromisoformat(v)
        except (TypeError, ValueError):
            return fallback

    def _drop_foreign_stores(self, db, estate):
        keep = {r[0] for r in db.q(
            'SELECT id FROM store WHERE estate = %s::"Estate" AND id = ANY(%s)',
            (estate, self.stores))}
        self.dropped_stores += len(self.stores) - len(keep)
        self.stores = [s for s in self.stores if s in keep]

    def force_estate(self, db, estate):
        """Pin this page to one estate, and drop branches from the other one.

        A branch selection made on a group page travels with the reader. Carried
        into a single-estate report it ANDs to nothing, and the page then says
        'Nothing in this range' — which reads as missing data rather than an
        incompatible filter. Dropping them, and counting what was dropped, keeps
        the report populated and the reason visible.
        """
        self.estate = estate
        self.estate_forced = True
        if self.stores:
            self._drop_foreign_stores(db, estate)
        return self

    def where(self, alias='t', estate=True):
        """SQL fragment + args shared by every query on the page.

        `estate=False` for the report tables: `sales_summary` and
        `product_movement` are each one estate by construction and carry no
        estate column, so adding the condition would fail rather than filter.
        """
        sql = ' %s.business_date BETWEEN %%s AND %%s' % alias
        args = [self.frm, self.to]
        if estate and self.estate:
            sql += ' AND %s.estate = %%s::"Estate"' % alias
            args.append(self.estate)
        if self.stores:
            sql += ' AND %s.store_id = ANY(%%s)' % alias
            args.append(self.stores)
        return sql, args

    def qs(self, keep_forced_estate=True, **over):
        """The current filter as a query string.

        `keep_forced_estate=False` leaves out an estate the page forced on
        itself. Without that, clicking from Alliance products to Products
        carries `estate=ALLIANCE` along and the general tabs look stuck on one
        estate with nothing explaining why.
        """
        est = '' if (self.estate_forced and not keep_forced_estate) else self.estate
        d = {'from': self.frm.isoformat(), 'to': self.to.isoformat(),
             'estate': est}
        d.update(over)
        pairs = [(k, v) for k, v in d.items() if v != '']
        # One pair per branch, so a link out of this page keeps the whole
        # selection rather than the first of it.
        pairs += [('store', s) for s in self.stores]
        return urllib.parse.urlencode(pairs)

    @property
    def days(self):
        return (self.to - self.frm).days + 1


def filter_bar(db, f, path=''):
    stores = db.q('SELECT id, name, estate::text FROM store ORDER BY name')
    counts = dict(db.q('SELECT estate::text, count(*) FROM store GROUP BY 1'))

    # Branches outside the chosen estate are dropped rather than shown and
    # ignored: ticking one while an estate filter is on would AND to nothing,
    # and an empty dashboard with no explanation is the worst kind of answer.
    shown = [r for r in stores if not f.estate or r[2] == f.estate]
    boxes = []
    for sid, nm, est in shown:
        on = sid in f.stores
        boxes.append(
            '<label class="ms-row%s" data-name="%s"><input type="checkbox" '
            'name="store" value="%d"%s data-estate="%s"><span>%s</span></label>'
            % (' on' if on else '', esc(nm.lower()), sid, ' checked' if on else '',
               est, esc(nm)))

    if not f.stores:
        label = 'All branches (%d)' % len(shown)
    elif len(f.stores) == 1:
        one = next((r[1] for r in stores if r[0] == f.stores[0]), 'one branch')
        label = one
    else:
        label = '%d branches selected' % len(f.stores)

    est_opts = ''.join(
        '<option value="%s"%s>%s</option>' % (v, ' selected' if f.estate == v else '', l)
        for v, l in (('', 'Both estates (%d branches)' % len(stores)),
                     ('STOREHUB', 'StoreHub (%d branches)' % counts.get('STOREHUB', 0)),
                     ('ALLIANCE', 'Alliance (%d branches)' % counts.get('ALLIANCE', 0))))
    # Disabled rather than hidden: the reader can see which estate the report
    # covers, and that it is the report's nature rather than a filter they set.
    # A disabled input submits nothing, so the page re-forces it server side.
    est_attrs = ''
    est_hint = ''
    if f.estate_forced:
        est_attrs = ' disabled title="This report covers one estate only"'
        est_hint = ('<div style="font-size:10.5px;color:var(--muted);'
                    'margin-top:3px">fixed for this report</div>')

    span = ('On file: %s to %s' % (f.data_lo, f.data_hi)) if f.data_lo else ''
    if span and f.bounds_table != 'transaction':
        span += ' (this report)'
    if f.dropped_stores:
        span = ('<span style="color:var(--warning)">%d branch(es) dropped — '
                'not in this estate</span><br>' % f.dropped_stores) + span
    quick = []
    for text, d0, d1 in (
            ('Last 7 days', (f.data_hi or dt.date.today()) - dt.timedelta(days=6), f.data_hi),
            ('Last 30 days', (f.data_hi or dt.date.today()) - dt.timedelta(days=29), f.data_hi),
            ('Everything', f.data_lo, f.data_hi)):
        if d0 and d1:
            quick.append('<a class="chip" href="%s?%s">%s</a>'
                         % (path, f.qs(**{'from': d0.isoformat(), 'to': d1.isoformat()}), text))

    return """
<form class="filters" method="get" action="%s">
  <div><label>From</label><input type="date" name="from" value="%s"></div>
  <div><label>To</label><input type="date" name="to" value="%s"></div>
  <div><label>Estate</label><select name="estate"%s>%s</select>%s</div>
  <div class="ms">
    <label>Branches</label>
    <button type="button" class="ms-btn" id="msb">%s <span class="caret">&#9662;</span></button>
    <div class="ms-panel" id="msp" hidden>
      <input type="search" class="ms-search" placeholder="Type to filter&hellip;"
             autocomplete="off">
      <div class="ms-acts"><a href="#" data-act="all">Select all</a>
        <a href="#" data-act="none">Clear</a>
        <a href="#" data-act="STOREHUB">StoreHub only</a>
        <a href="#" data-act="ALLIANCE">Alliance only</a></div>
      <div class="ms-list">%s</div>
      <div class="ms-foot"><span id="mscount"></span>
        <button type="submit">Apply</button></div>
    </div>
  </div>
  <button type="submit">Apply</button>
  %s
  <div style="margin-left:auto;color:var(--muted);font-size:11px;text-align:right">%s</div>
</form>""" % (path, f.frm, f.to, est_attrs, est_opts, est_hint, esc(label),
              ''.join(boxes), ' '.join(quick), span)


# -- chart primitives ------------------------------------------------------

def bars_v(rows, w=1000, h=210, pad_l=52, value=peso0, label=lambda r: r[0],
           colour='var(--s1)'):
    """Vertical bars. rows = [(label, value), ...]. 4px rounded data-end."""
    if not rows:
        return '<div class="empty">Nothing in this range.</div>'
    vmax = max((float(r[1] or 0) for r in rows), default=0) or 1
    n = len(rows)
    pad_b, pad_t = 26, 10
    iw = w - pad_l - 12
    step = iw / n
    # Capped, so a range holding one or two days draws a readable bar rather
    # than a wall of colour half the panel wide.
    bw = max(1.0, min(iw / n * 0.72, 46.0))
    out = ['<svg viewBox="0 0 %d %d" width="100%%" preserveAspectRatio="none" '
           'style="height:%dpx;display:block">' % (w, h, h)]
    for g in range(5):
        y = pad_t + (h - pad_t - pad_b) * g / 4
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="var(--grid)" '
                   'stroke-width="1"/>' % (pad_l, y, w - 12, y))
        out.append('<text x="%d" y="%.1f" text-anchor="end">%s</text>'
                   % (pad_l - 7, y + 3.5, short(vmax * (4 - g) / 4)))
    for i, r in enumerate(rows):
        v = float(r[1] or 0)
        bh = max(1.5, (h - pad_t - pad_b) * (v / vmax))
        x = pad_l + i * step + (step - bw) / 2
        y = h - pad_b - bh
        out.append('<rect x="%.2f" y="%.2f" width="%.2f" height="%.2f" rx="4" '
                   'fill="%s" data-tip="%s &middot; %s"/>'
                   % (x, y, bw, bh, colour, esc(label(r)), value(v)))
    every = max(1, n // 14)
    for i, r in enumerate(rows):
        if i % every == 0:
            out.append('<text class="lbl" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                       % (pad_l + i * step + step / 2, h - 8, esc(str(label(r))[:10])))
    out.append('</svg>')
    return ''.join(out)


def bars_h(rows, value=peso0, sub=None, colour='var(--s1)', limit=20):
    """Horizontal ranked bars as a table -- labels always legible, no rotation."""
    rows = rows[:limit]
    if not rows:
        return '<div class="empty">Nothing in this range.</div>'
    vmax = max((float(r[1] or 0) for r in rows), default=0) or 1
    out = ['<table><tbody>']
    for r in rows:
        v = float(r[1] or 0)
        extra = ('<td class="n" style="color:var(--text-secondary)">%s</td>' % sub(r)) if sub else ''
        out.append(
            '<tr><td style="max-width:230px;overflow:hidden;text-overflow:ellipsis;'
            'white-space:nowrap">%s</td>'
            '<td style="width:44%%"><span class="bar" style="width:%.1f%%;background:%s" '
            'data-tip="%s &middot; %s"></span></td>'
            '<td class="n">%s</td>%s</tr>'
            % (esc(r[0]), max(1.0, v / vmax * 100), colour, esc(r[0]), value(v), value(v), extra))
    out.append('</tbody></table>')
    return ''.join(out)


def heatmap(cells, rows_lab, cols_lab, value=num):
    """Sequential one-hue heatmap. cells = {(row, col): value}."""
    if not cells:
        return '<div class="empty">No timed rows in this range.</div>'
    vmax = max(cells.values()) or 1
    ramp = ['var(--seq-100)', 'var(--seq-250)', 'var(--seq-400)',
            'var(--seq-500)', 'var(--seq-600)', 'var(--seq-700)']
    cw, ch, pad_l, pad_t = 34, 26, 44, 20
    w = pad_l + len(cols_lab) * cw + 10
    h = pad_t + len(rows_lab) * ch + 6
    out = ['<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto;'
           'display:block">' % (w, h, w)]
    for j, c in enumerate(cols_lab):
        out.append('<text x="%.1f" y="%d" text-anchor="middle">%s</text>'
                   % (pad_l + j * cw + cw / 2, pad_t - 6, esc(c)))
    for i, rl in enumerate(rows_lab):
        out.append('<text x="%d" y="%.1f" text-anchor="end">%s</text>'
                   % (pad_l - 7, pad_t + i * ch + ch / 2 + 3.5, esc(rl)))
        for j, c in enumerate(cols_lab):
            v = cells.get((rl, c), 0)
            if v:
                idx = min(len(ramp) - 1, int(v / vmax * len(ramp)))
                fill = ramp[idx]
            else:
                fill = 'var(--grid)'
            out.append('<rect x="%.1f" y="%.1f" width="%d" height="%d" rx="3" '
                       'fill="%s" stroke="var(--surface-1)" stroke-width="2" '
                       'data-tip="%s %s &middot; %s"/>'
                       % (pad_l + j * cw, pad_t + i * ch, cw, ch, fill,
                          esc(rl), esc(c), value(v)))
    out.append('</svg>')
    return ''.join(out)


def tile(k, v, note=''):
    return ('<div class="tile"><div class="k">%s</div><div class="v">%s</div>'
            '<div class="n">%s</div></div>' % (esc(k), v, note))


# -- pages -----------------------------------------------------------------

NAV = (('/', 'Overview'), ('/hours', 'Hours'), ('/products', 'Products'),
       ('/exceptions', 'Exceptions'), ('/product-report', 'Alliance products'),
       ('/storehub-report', 'StoreHub products'))


def shell(title, inner, head_extra=''):
    """A bare page with the dashboard's styling but no nav or filters -- for
    sign-in, the holding page and errors, which exist outside the dashboard."""
    return """<!doctype html><html><head><meta charset="utf-8">
<title>%s &middot; Sales analytics</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>%s</style>%s</head><body><div class="gate">%s</div></body></html>""" % (
        esc(title), CSS, head_extra, inner)


GOOGLE_G = ('<svg width="18" height="18" viewBox="0 0 18 18"><path fill="#4285F4" '
            'd="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.49h4.84a4.14 4.14 0 0 1-1.8 '
            '2.72v2.26h2.92a8.78 8.78 0 0 0 2.68-6.63z"/><path fill="#34A853" '
            'd="M9 18c2.43 0 4.47-.8 5.96-2.17l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 '
            '0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"/><path fill="#FBBC05" '
            'd="M3.97 10.73a5.4 5.4 0 0 1 0-3.46V4.94H.96a9 9 0 0 0 0 8.12l3-2.33z"/>'
            '<path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59C13.46.9 '
            '11.43 0 9 0A9 9 0 0 0 .96 4.94l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"/>'
            '</svg>')


def login_page(msg='', kind='note'):
    colour = {'note': 'var(--text-secondary)', 'error': 'var(--critical)'}[kind]
    banner = ('<p style="color:%s">%s</p>' % (colour, esc(msg))) if msg else ''
    return shell('Sign in', """
<div class="card">
  <h1>Sales analytics</h1>
  <p>Ribshack group &mdash; sales, hours, products and exceptions.</p>
  %s
  <a class="gsi" href="/auth/start">%s Sign in with Google</a>
  <p style="margin-top:20px;font-size:12px">New accounts need an administrator's
    approval before they can see anything.</p>
</div>""" % (banner, GOOGLE_G))


def pending_page(user):
    if user['status'] == 'REJECTED':
        head, body = ('Access declined',
                      'An administrator has declined access for <strong>%s</strong>. '
                      'If you think that is wrong, speak to them directly &mdash; '
                      'signing in again will not change it.' % esc(user['email']))
    elif user['status'] == 'SUSPENDED':
        head, body = ('Access suspended',
                      'Access for <strong>%s</strong> has been suspended. Speak to '
                      'an administrator.' % esc(user['email']))
    else:
        head, body = ('Your account is pending approval',
                      'You are signed in as <strong>%s</strong>, and an administrator '
                      'has been asked to approve your access. Nothing is visible until '
                      'they do. You will not be notified automatically &mdash; sign in '
                      'again once they tell you it is done.' % esc(user['email']))
    return shell(head, """
<div class="card">
  <h1>%s</h1>
  <p>%s</p>
  <a class="chip" href="/logout">Sign out</a>
</div>""" % (esc(head), body))


def admin_users_page(db, f, me):
    rows = auth.list_users()
    body = []
    for (uid, email, name, status, role, req, dec, by, last, count) in rows:
        actions = []
        if status != 'APPROVED':
            actions.append('<button class="mini" name="approve" value="%d">Approve</button>' % uid)
        if status == 'PENDING':
            actions.append('<button class="mini ghost" name="reject" value="%d">Reject</button>' % uid)
        if status == 'APPROVED' and email.lower() != me['email'].lower():
            actions.append('<button class="mini ghost" name="suspend" value="%d">Suspend</button>' % uid)
        if role != 'ADMIN' and status == 'APPROVED':
            actions.append('<button class="mini ghost" name="promote" value="%d">Make admin</button>' % uid)
        if email.lower() == me['email'].lower():
            actions.append('<span style="color:var(--muted);font-size:11.5px">you</span>')
        body.append(
            '<tr><td><strong>%s</strong><br>'
            '<span style="color:var(--muted);font-size:11.5px">%s</span></td>'
            '<td><span class="badge b-%s">%s</span>%s</td>'
            '<td style="font-size:11.5px;color:var(--text-secondary)">%s</td>'
            '<td style="font-size:11.5px;color:var(--text-secondary)">%s</td>'
            '<td style="white-space:nowrap">%s</td></tr>'
            % (esc(name or email.split('@')[0]), esc(email), status, status,
               ' <span class="badge" style="background:color-mix(in srgb,'
               'var(--s1) 20%,transparent);color:var(--s1)">ADMIN</span>'
               if role == 'ADMIN' else '',
               req.strftime('%d %b %Y') if req else '',
               ('%s, %d sign-in(s)' % (last.strftime('%d %b'), count)) if last else 'never',
               ' '.join(actions)))

    pend = sum(1 for r in rows if r[3] == 'PENDING')
    note = ('<strong>%d account(s) waiting.</strong> Approving one lets that person '
            'see every branch and every figure in this dashboard, so treat it as '
            'granting access to the group\'s whole sales position.' % pend) if pend \
        else 'Nobody is waiting. New sign-ins will appear here.'

    return """
<div class="warn">%s</div>
<div class="card">
  <h2>Users</h2>
  <p class="note">Anyone who has signed in with Google. A new account is PENDING
    until approved here, and sees only a holding page meanwhile.</p>
  <form method="post" action="/admin/users">
  <div class="scroll"><table class="utable">
    <thead><tr><th>Person</th><th>Status</th><th>Requested</th><th>Last seen</th>
      <th>Actions</th></tr></thead>
    <tbody>%s</tbody>
  </table></div>
  </form>
</div>""" % (note, ''.join(body) or
              '<tr><td colspan="5" class="empty">Nobody has signed in yet.</td></tr>')


def describe_device(ua):
    """'Chrome on Windows' from a User-Agent string -- enough to tell your own
    devices apart, not a fingerprint."""
    ua = ua or ''
    browser = next((name for key, name in (('Edg/', 'Edge'), ('OPR/', 'Opera'),
                                           ('Firefox/', 'Firefox'),
                                           ('Chrome/', 'Chrome'),
                                           ('Safari/', 'Safari')) if key in ua),
                   'Unknown browser')
    system = next((name for key, name in (('Windows', 'Windows'),
                                          ('Android', 'Android'),
                                          ('iPhone', 'iPhone'), ('iPad', 'iPad'),
                                          ('Mac OS X', 'Mac'), ('CrOS', 'ChromeOS'),
                                          ('Linux', 'Linux')) if key in ua), '')
    return '%s on %s' % (browser, system) if system else browser


def account_page(me, done=''):
    rows = auth.list_sessions(me['id'])
    others = sum(1 for r in rows if r[0] != me['session'])
    body = ''.join(
        '<tr><td><strong>%s</strong>%s</td>'
        '<td style="font-size:11.5px;color:var(--text-secondary)">%s</td>'
        '<td style="font-size:11.5px;color:var(--text-secondary)">%s</td></tr>'
        % (esc(describe_device(ua)),
           ' <span class="badge b-APPROVED">this device</span>'
           if sid == me['session'] else '',
           created.strftime('%d %b %Y, %H:%M'), seen.strftime('%d %b, %H:%M'))
        for sid, created, seen, ua in rows)
    notice = 'Signed out of every other device.' if done == 'others' else ''
    return """
%s
<div class="card">
  <h2>Account</h2>
  <p class="note">Signed in as <strong>%s</strong>. <em>Sign out</em> in the
    header ends this device only. If you signed in on a shared or lost device,
    end it from here.</p>
  <div class="scroll"><table class="utable">
    <thead><tr><th>Device</th><th>Signed in</th><th>Last active</th></tr></thead>
    <tbody>%s</tbody>
  </table></div>
  <form method="post" action="/account" style="margin-top:14px">
    <button class="mini" name="end" value="others"%s>Sign out of all other devices</button>
    <button class="mini ghost" name="end" value="all">Sign out everywhere</button>
  </form>
</div>""" % ('<div class="warn">%s</div>' % esc(notice) if notice else '',
             esc(me['email']), body,
             '' if others else ' disabled title="No other devices are signed in"')


def page(title, path, f, db, body, me=None):
    nav = ''.join(
        '<a href="%s?%s" class="%s">%s</a>'
        # Only the current page keeps an estate it forced on itself. Another
        # forced tab sets its own, and carrying ALLIANCE into a link labelled
        # "StoreHub products" would read as a contradiction.
        % (p, f.qs(keep_forced_estate=(p == path)),
           'on' if p == path else '', l)
        for p, l in NAV)
    who = ''
    if me:
        pend = auth.pending_count() if me.get('role') == 'ADMIN' else 0
        who = ('<div class="who">%s<span>%s</span>'
               '<a href="/account">Account</a><a href="/logout">Sign out</a></div>'
               % (('<a href="/admin/users">Users <span class="pill">%d</span></a>'
                   % pend) if pend else
                  ('<a href="/admin/users">Users</a>' if me.get('role') == 'ADMIN' else ''),
                  esc(me.get('email', ''))))
    return """<!doctype html><html><head><meta charset="utf-8">
<title>%s &middot; Sales analytics</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>%s</style></head><body>
<header>
<div style="display:flex;align-items:baseline;gap:14px">
  <h1>Sales analytics</h1>%s
</div>
<div class="sub">Ribshack group &middot; %s to %s &middot; %d days%s</div>
<nav>%s</nav></header>
<main>%s%s</main><script>%s</script></body></html>""" % (
        esc(title), CSS, who, f.frm, f.to, f.days,
        (' &middot; ' + ('StoreHub estate' if f.estate == 'STOREHUB'
                         else 'Alliance estate' if f.estate == 'ALLIANCE' else 'both estates')),
        nav, filter_bar(db, f, path), body, JS)


def view_overview(db, f):
    w, a = f.where()
    head = db.one('SELECT count(*), coalesce(sum(amount),0), '
                  'count(DISTINCT store_id), count(DISTINCT business_date), '
                  'coalesce(sum(quantity),0) '
                  'FROM "transaction" t WHERE' + w, a)
    n, total, stores, days, units = head
    avg = (float(total) / n) if n else 0

    # Previous window of equal length, for a like-for-like change.
    span = dt.timedelta(days=f.days)
    p_args = [f.frm - span, f.frm - dt.timedelta(days=1)] + a[2:]
    p_w = w.replace('t.business_date BETWEEN %s AND %s',
                    't.business_date BETWEEN %s AND %s')
    prev = db.one('SELECT count(*), coalesce(sum(amount),0) FROM "transaction" t WHERE' + p_w,
                  p_args)
    delta = ''
    if prev and prev[1] and float(prev[1]) > 0:
        pc = (float(total) - float(prev[1])) / float(prev[1]) * 100
        col = 'var(--up)' if pc >= 0 else 'var(--critical)'
        delta = ('<span style="color:%s">%+.1f%%</span> vs previous %d days'
                 % (col, pc, f.days))

    daily = db.q('SELECT business_date, sum(amount) FROM "transaction" t WHERE' + w +
                 ' GROUP BY 1 ORDER BY 1', a)
    by_store = db.q('SELECT s.name, sum(t.amount), count(*) FROM "transaction" t '
                    'JOIN store s ON s.id = t.store_id WHERE' + w +
                    ' GROUP BY 1 ORDER BY 2 DESC', a)
    by_estate = db.q('SELECT t.estate::text, count(*), sum(t.amount) '
                     'FROM "transaction" t WHERE' + w + ' GROUP BY 1 ORDER BY 3 DESC', a)
    by_pay = db.q("SELECT coalesce(nullif(t.payment_method,''),'(unknown)'), "
                  'sum(t.amount) FROM "transaction" t WHERE' + w +
                  ' GROUP BY 1 ORDER BY 2 DESC', a)

    tiles = ''.join([
        tile('Sales', peso0(total), delta),
        tile('Transactions', num(n), '%s units' % num(units)),
        tile('Average ticket', peso(avg)),
        tile('Branches trading', num(stores)),
        tile('Days with sales', num(days), 'of %d in range' % f.days),
    ])

    est_rows = ''.join(
        '<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td>'
        '<td class="n">%s</td></tr>'
        % ('StoreHub' if e == 'STOREHUB' else 'Alliance', num(c), peso0(s),
           peso(float(s) / c if c else 0))
        for e, c, s in by_estate)

    return """
<div class="tiles">%s</div>
<div class="card"><h2>Daily sales</h2>
  <p class="note">Every transaction in range, by trading day. Hover a bar for the day's total.</p>
  %s</div>
<div class="card"><h2>Opportunities</h2>
  <p class="note">Why the weakest day above was weak, and what would actually move it.
    Computed from your own figures &mdash; every claim carries the numbers it came
    from. Nothing is sent anywhere.</p>
  <div id="ops" class="ops"><div class="empty">Analysing&hellip;</div></div></div>
<div class="grid2">
  <div class="card"><h2>Branches by sales</h2>
    <p class="note">Top 20 of %d trading in this range.</p>%s</div>
  <div class="card"><h2>Estate split</h2>
    <p class="note">The two feeds are different outlets, not the same sales twice.</p>
    <table><thead><tr><th>Estate</th><th class="n">Transactions</th>
      <th class="n">Sales</th><th class="n">Avg ticket</th></tr></thead>
      <tbody>%s</tbody></table>
    <h2 style="margin-top:18px">Payment methods</h2>%s</div>
</div>""" % (tiles, bars_v(daily), len(by_store),
             bars_h(by_store, sub=lambda r: num(r[2])), est_rows,
             bars_h(by_pay, limit=10, colour='var(--s3)'))


def view_hours(db, f):
    w, a = f.where()
    cov = db.one('SELECT count(*), count(*) FILTER (WHERE has_time) '
                 'FROM "transaction" t WHERE' + w, a)
    total, timed = cov
    pct = (timed / total * 100) if total else 0

    warn = ''
    if total and timed < total:
        warn = ('<div class="warn"><strong>%s of %s rows in this range carry a clock '
                '(%.0f%%).</strong> The Alliance estate\'s 2026 export records a date '
                'and no time &mdash; July and August are entirely timeless. Everything '
                'below is computed on the timed rows only, never by treating a missing '
                'time as midnight.</div>' % (num(timed), num(total), pct))

    if not timed:
        return warn + ('<div class="card"><div class="empty">No rows in this range '
                       'carry a time of day, so there is no hourly pattern to show. '
                       'Try a range inside May, June or September 2026.</div></div>')

    wt = w + ' AND t.has_time'
    hours = db.q('SELECT local_hour, count(*), sum(amount) FROM "transaction" t WHERE'
                 + wt + ' GROUP BY 1 ORDER BY 1', a)
    hrows = [('%02d:00' % h, c) for h, c, s in hours]
    hsales = [('%02d:00' % h, s) for h, c, s in hours]

    dow = db.q("SELECT trim(to_char(t.business_date,'Dy')), t.local_hour, count(*) "
               'FROM "transaction" t WHERE' + wt + ' GROUP BY 1,2', a)
    cells = {(d, '%02d' % h): c for d, h, c in dow}
    order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    hrs_present = sorted({h for _, h, _ in dow})
    cols = ['%02d' % h for h in hrs_present]

    cash = db.q('SELECT cashier_name, count(*), sum(amount), '
                'count(DISTINCT business_date) FROM "transaction" t WHERE' + wt +
                " AND cashier_name IS NOT NULL AND cashier_name <> '' "
                ' GROUP BY 1 ORDER BY 2 DESC LIMIT 25', a)
    crows = ''.join(
        '<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td>'
        '<td class="n">%s</td><td class="n">%s</td></tr>'
        % (esc(c), num(n), peso0(s), num(d), num(round(n / d)) if d else '-')
        for c, n, s, d in cash)

    peak = max(hours, key=lambda r: r[1])
    quiet = min(hours, key=lambda r: r[1])

    return warn + """
<div class="tiles">%s</div>
<div class="grid2">
  <div class="card"><h2>Transactions by hour</h2>
    <p class="note">Manila local time, derived from the true instant &mdash; not the
      API's mislabelled <code>Z</code>.</p>%s</div>
  <div class="card"><h2>Sales by hour</h2>
    <p class="note">The same hours by value, which is where staffing cost is judged.</p>%s</div>
</div>
<div class="card"><h2>What the day&rsquo;s shape is telling you</h2>
  <p class="note">Where the hours concentrate, what the quiet ones cost, and how throughput varies. Computed from your own figures &mdash; every claim carries the numbers it came from. Nothing is sent anywhere.</p>
  <div id="ops" class="ops"><div class="empty">Analysing&hellip;</div></div></div>
<div class="card"><h2>Day of week against hour</h2>
  <p class="note">Transaction count per cell. Darker is busier.</p>%s</div>
<div class="card"><h2>Cashiers</h2>
  <p class="note">Timed rows only, top 25 by transaction count.</p>
  <div class="scroll"><table><thead><tr><th>Cashier</th><th class="n">Transactions</th>
  <th class="n">Sales</th><th class="n">Days worked</th><th class="n">Txns/day</th>
  </tr></thead><tbody>%s</tbody></table></div></div>""" % (
        ''.join([tile('Timed rows', num(timed), '%.0f%% of the range' % pct),
                 tile('Busiest hour', '%02d:00' % peak[0], '%s transactions' % num(peak[1])),
                 tile('Quietest trading hour', '%02d:00' % quiet[0],
                      '%s transactions' % num(quiet[1])),
                 tile('Hours trading', num(len(hours)))]),
        bars_v(hrows, h=200, value=num), bars_v(hsales, h=200, colour='var(--s2)'),
        heatmap(cells, order, cols), crows or
        '<tr><td colspan="5" class="empty">No cashier names on these rows.</td></tr>')


def view_products(db, f):
    w, a = f.where()
    lines = db.one('SELECT count(*), coalesce(sum(i.quantity),0), '
                   'coalesce(sum(i.line_total),0) FROM transaction_item i '
                   'JOIN "transaction" t ON t.id = i.transaction_id WHERE' + w, a)
    n_lines, units, line_val = lines
    cover = db.one('SELECT count(*) FILTER (WHERE t.feed = %s), count(*) '
                   'FROM "transaction" t WHERE' + w, ['alliance_sale'] + a)
    flat, total = cover

    warn = ''
    if flat:
        warn = ('<div class="warn"><strong>%s of %s transactions in this range carry no '
                'line detail.</strong> The Alliance estate\'s 2026 sale-level export '
                'records a total and a tender, with no products at all. Product figures '
                'below cover only the transactions that do have lines.</div>'
                % (num(flat), num(total)))

    if not n_lines:
        return warn + ('<div class="card"><div class="empty">No line detail in this '
                       'range.</div></div>')

    top = db.q("""SELECT coalesce(p.name, 'id ' || i.product_source_id),
                         sum(i.line_total), sum(i.quantity), count(*)
                    FROM transaction_item i
                    JOIN "transaction" t ON t.id = i.transaction_id
               LEFT JOIN product p ON p.id = i.product_id
                   WHERE""" + w + ' GROUP BY 1 ORDER BY 2 DESC LIMIT 25', a)
    byunits = db.q("""SELECT coalesce(p.name, 'id ' || i.product_source_id),
                             sum(i.quantity)
                        FROM transaction_item i
                        JOIN "transaction" t ON t.id = i.transaction_id
                   LEFT JOIN product p ON p.id = i.product_id
                       WHERE""" + w + ' GROUP BY 1 ORDER BY 2 DESC LIMIT 15', a)
    basket = db.one("""SELECT avg(c), max(c) FROM (
                         SELECT t.id, count(*) c FROM "transaction" t
                           JOIN transaction_item i ON i.transaction_id = t.id
                          WHERE""" + w + ' GROUP BY 1) x', a)
    unnamed = db.one('SELECT count(*) FROM transaction_item i '
                     'JOIN "transaction" t ON t.id = i.transaction_id '
                     'WHERE' + w + ' AND i.product_id IS NULL', a)[0]

    rows = ''.join(
        '<tr><td>%s</td><td class="n">%s</td><td class="n">%s</td>'
        '<td class="n">%s</td><td class="n">%s</td></tr>'
        % (esc(nm), peso0(val), num(q), num(c), peso(float(val) / float(q) if q else 0))
        for nm, val, q, c in top)

    note = ''
    if unnamed:
        note = ('<p class="note">%s line(s) show as <code>id &hellip;</code>: the '
                'StoreHub estate\'s product ids are not in the API\'s catalogue, so no '
                'name exists to show yet.</p>' % num(unnamed))

    return warn + """
<div class="tiles">%s</div>
<div class="card"><h2>What the mix is telling you</h2>
  <p class="note">Where sales concentrate, what the tail costs, and which baskets are left unattached. Computed from your own figures &mdash; every claim carries the numbers it came from. Nothing is sent anywhere.</p>
  <div id="ops" class="ops"><div class="empty">Analysing&hellip;</div></div></div>
<div class="card"><h2>Top products by sales</h2>%s
  <div class="scroll"><table><thead><tr><th>Product</th><th class="n">Sales</th>
  <th class="n">Units</th><th class="n">Lines</th><th class="n">Avg price</th>
  </tr></thead><tbody>%s</tbody></table></div></div>
<div class="card"><h2>Top products by units</h2>
  <p class="note">Volume rather than value &mdash; the two orders rarely match.</p>%s</div>
""" % (''.join([tile('Line items', num(n_lines)),
                tile('Units sold', num(units)),
                tile('Line value', peso0(line_val)),
                tile('Items per basket', '%.2f' % float(basket[0] or 0),
                     'largest basket %s' % num(basket[1] or 0))]),
       note, rows, bars_h(byunits, value=num, colour='var(--s3)', limit=15))


def view_exceptions(db, f):
    w, a = f.where()
    ex = db.one('SELECT count(*) FILTER (WHERE is_flagged), '
                'count(*) FILTER (WHERE is_deleted), '
                'coalesce(sum(amount) FILTER (WHERE is_flagged),0), '
                'coalesce(sum(discount_amount),0), count(*) '
                'FROM "transaction" t WHERE' + w, a)
    flagged, deleted, flagged_amt, disc, n = ex

    rw = ' r.business_date BETWEEN %s AND %s'
    rargs = [f.frm, f.to]
    if f.stores:
        rw += ' AND r.store_id = ANY(%s)'
        rargs.append(f.stores)
    rec = db.one("""SELECT count(*), coalesce(sum(void_amount),0),
                           coalesce(sum(senior),0), coalesce(sum(pwd),0),
                           coalesce(sum(solo_parent),0), coalesce(sum(nac),0),
                           coalesce(sum(discount),0), coalesce(sum(total_gross),0),
                           count(*) FILTER (WHERE NOT posted)
                      FROM receipt r WHERE""" + rw, rargs) if not f.estate == 'STOREHUB' else None

    flag_rows = db.q('SELECT s.name, t.ref_number, t.business_date, t.amount, '
                     't.flag_reason FROM "transaction" t JOIN store s ON s.id=t.store_id '
                     'WHERE' + w + ' AND t.is_flagged ORDER BY t.amount DESC LIMIT 40', a)
    frows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td class="n">%s</td>'
                    '<td>%s</td></tr>'
                    % (esc(s), esc(r or ''), d, peso(v), esc(fr or '(none given)'))
                    for s, r, d, v, fr in flag_rows)

    disc_by_store = db.q('SELECT s.name, sum(t.discount_amount) FROM "transaction" t '
                         'JOIN store s ON s.id=t.store_id WHERE' + w +
                         ' AND t.discount_amount > 0 GROUP BY 1 ORDER BY 2 DESC', a)

    tiles = [tile('Flagged', num(flagged),
                  peso0(flagged_amt) + ' of sales' if flagged else 'none in range'),
             tile('Soft-deleted', num(deleted),
                  'still stored, excluded from sales' if deleted else 'none in range'),
             tile('Discounts given', peso0(disc),
                  '%.2f%% of sales' % (float(disc) / float(db.one(
                      'SELECT coalesce(sum(amount),1) FROM "transaction" t WHERE' + w,
                      a)[0]) * 100) if disc else '')]
    stat = ''
    if rec and rec[0]:
        cnt, void, sen, pwd, solo, nac, rdisc, gross, unposted = rec
        tiles.append(tile('Receipts on file', num(cnt), peso0(gross) + ' gross'))
        stat = """
<div class="card"><h2>Statutory relief and voids</h2>
  <p class="note">From the BIR receipt register, which covers the Alliance estate
   from August 2026. These are the figures a filing turns on, so each is kept apart
   rather than folded into one discount total.</p>
  <table><thead><tr><th>Measure</th><th class="n">Amount</th>
    <th class="n">Share of gross</th></tr></thead><tbody>%s</tbody></table>
  %s</div>""" % (
            ''.join('<tr><td>%s</td><td class="n">%s</td><td class="n">%.2f%%</td></tr>'
                    % (lab, peso(v), (float(v) / float(gross) * 100) if gross else 0)
                    for lab, v in (('Senior citizen', sen), ('PWD', pwd),
                                   ('Solo parent', solo), ('NAC', nac),
                                   ('Other discounts', rdisc), ('Voided', void))),
            ('<p class="note" style="margin-top:10px">%s receipt(s) are not posted.</p>'
             % num(unposted)) if unposted else '')

    return """
<div class="tiles">%s</div>
<div class="card"><h2>Where to look first</h2>
  <p class="note">Outliers measured against the group, with what each is and is not evidence of. Computed from your own figures &mdash; every claim carries the numbers it came from. Nothing is sent anywhere.</p>
  <div id="ops" class="ops"><div class="empty">Analysing&hellip;</div></div></div>
%s
<div class="card"><h2>Discounts by branch</h2>
  <p class="note">Where discounting concentrates. A branch far above the others is
   worth a look before it is worth an accusation.</p>%s</div>
<div class="card"><h2>Flagged transactions</h2>
  <p class="note">Rows the source system marked for review. Top 40 by value.</p>
  <div class="scroll"><table><thead><tr><th>Branch</th><th>Ref</th><th>Date</th>
  <th class="n">Amount</th><th>Reason</th></tr></thead><tbody>%s</tbody></table></div>
</div>""" % (''.join(tiles), stat, bars_h(disc_by_store, colour='var(--s2)'),
             frows or '<tr><td colspan="5" class="empty">Nothing flagged in this '
                      'range.</td></tr>')


#: Each finding kind gets the colour its meaning already has -- status colours
#: for states, categorical slots for observations. Never colour alone: every
#: block carries its own heading and figures.
OPS_COLOUR = {'warn': 'var(--critical)', 'gap': 'var(--warning)',
              'action': 'var(--s1)', 'detail': 'var(--muted)',
              'pattern': 'var(--s3)', 'bundle': 'var(--s2)',
              'good': 'var(--good)', 'none': 'var(--muted)',
              'lever': 'var(--good)'}


def _evidence_value(v):
    """Format one evidence figure.

    Decimal is handled explicitly, not lumped in with str: every `sum()` in
    these queries comes back as Decimal, and treating it as text printed raw
    values like `15926533.50` -- no separators, no currency -- in the one place
    the panel is asking to be believed.

    The convention, which the finding writes to rather than the renderer
    guessing: a Decimal or float IS an amount in pesos, a plain int is a count of
    things, and anything that is neither -- a ratio, a rate, a units figure --
    arrives pre-formatted as a string carrying its own unit. Type alone cannot
    tell ₱1.66 from 1.66 items, and it briefly did not: items-per-basket rendered
    with a peso sign until this rule was made explicit.
    """
    if isinstance(v, bool):
        return esc(v)
    if isinstance(v, (float, decimal.Decimal)):
        return peso(v)
    if isinstance(v, int):
        return num(v)
    return esc(v)


def render_insights(db, f, view='overview'):
    """The Opportunities panel, served on its own so the page never waits on it."""
    import insights
    fn = insights.VIEWS.get(view, insights.analyse)
    try:
        findings = fn(db, f)
    except Exception:
        import traceback
        return ('<div class="empty">The analysis failed.<br><small>%s</small></div>'
                % esc(traceback.format_exc()[-400:]))
    if not findings:
        return '<div class="empty">Nothing to report for this range.</div>'

    out = []
    for fd in findings:
        ev = ''
        if fd['evidence']:
            # A finding may name its columns. A product name beside a bare
            # figure does not say whether the figure is money, units or
            # baskets, and the reader should never have to infer it from the
            # presence of a currency symbol.
            head = ''
            if fd.get('head'):
                head = ('<tr><th>%s</th><th class="n">%s</th></tr>'
                        % (esc(fd['head'][0]), esc(fd['head'][1])))
            ev = '<table style="margin-top:8px">' + head + '%s</table>' % ''.join(
                '<tr><td style="color:var(--text-secondary)">%s</td>'
                '<td class="n">%s</td></tr>'
                % (esc(k), _evidence_value(v))
                for k, v in fd['evidence'])
        lever = fd['kind'] == 'lever'
        out.append(
            '<div style="border-left:%dpx solid %s;padding:%s;'
            'margin-bottom:16px%s">'
            '<div style="font-weight:600;margin-bottom:3px">%s</div>'
            '<div style="color:var(--text-secondary);font-size:12.5px">%s</div>%s</div>'
            % (4 if lever else 3, OPS_COLOUR.get(fd['kind'], 'var(--muted)'),
               '10px 12px 10px 14px' if lever else '2px 0 2px 12px',
               ';grid-column:1/-1;background:color-mix(in srgb,var(--good) 7%,'
               'transparent);border-radius:0 6px 6px 0' if lever else '',
               esc(fd['title']), esc(fd['body']), ev))
    return ''.join(out)



#: Rows drawn on screen. The CSV carries everything; a browser table past a few
#: thousand rows stops being a table and starts being a scroll bar.
REPORT_CAP = 3000

#: Rows drawn on screen. The CSV carries everything; a browser table past a
#: few thousand rows stops being a table and starts being a scroll bar.
REPORT_CAP = 3000


def coverage_note(db, f, estate, what, table='transaction_item'):
    """'4 of the 44 branches you selected have <what> in this range.'

    Without this, a branch filter set to 44 and a report showing 4 reads as a
    bug. The gap is real and saying so is the difference between a reader
    trusting the number and doubting the page.
    """
    rw, ra = f.where('t', estate=False)      # the report tables carry no estate
    if table == 'transaction_item':
        tw, ta = f.where('t')                # `transaction` carries an estate
        with_rows = db.one('SELECT count(DISTINCT t.store_id) FROM transaction_item i '
                           'JOIN "transaction" t ON t.id = i.transaction_id WHERE'
                           + tw, ta)[0]
        traded = db.one('SELECT count(DISTINCT store_id) FROM "transaction" t WHERE'
                        + tw, ta)[0]
    else:
        with_rows = db.one('SELECT count(DISTINCT store_id) FROM "%s" t WHERE' % table
                           + rw, ra)[0]
        # `traded` is only meaningful from the same table. Counting it off
        # `transaction` and comparing it with a row count from `sales_summary`
        # produced "49 of 49 have rows" next to "5 did not trade" -- two true
        # figures from two different sources, which together read as nonsense.
        traded = with_rows

    selected = len(f.stores) or db.one(
        'SELECT count(*) FROM store WHERE estate = %s::"Estate"', (estate,))[0]

    bits = []
    if f.dropped_stores:
        bits.append('%s branch(es) you had selected belong to the other estate '
                    'and were left out of this report.' % num(f.dropped_stores))
    bits.append('<strong>%s of the %s branch(es) in scope have %s in this '
                'range.</strong>' % (num(with_rows), num(selected), what))
    if traded > with_rows:
        bits.append('%s traded without appearing in this report.'
                    % num(traded - with_rows))
    if selected > traded:
        bits.append('%s sent nothing in these dates.' % num(selected - traded))
    if with_rows and with_rows < selected:
        bits.append('This is the feed, not the filter: the figures below are '
                    'complete for the branches that reported.')
    return '<div class="warn">%s</div>' % ' '.join(bits)


def _qty(v):
    """Quantities are Decimal but almost always whole -- print them as people
    write them, with the decimals only when they are actually there."""
    fv = float(v or 0)
    return format(int(fv), ',') if fv == int(fv) else format(fv, ',.3f')


def _pct_cell(v):
    return ('<td class="n">%.2f%%</td>' % v) if v is not None else \
           '<td class="n" style="color:var(--muted)">&mdash;</td>'


def _money_cell(v):
    return ('<td class="n">%s</td>' % peso(v)) if v is not None else \
           '<td class="n" style="color:var(--muted)">&mdash;</td>'


# ---------------------------------------------------------------------------
# Alliance products -- from the API's /sales-summary
# ---------------------------------------------------------------------------

ALLIANCE_COLS = ['Branch', 'Date', 'Product ID', 'Product Name', 'Qty',
                 'Gross Sales', '%', 'Cost', 'Tax', 'Gross Profit', 'GP %']


def alliance_rows(db, f):
    w, a = f.where('t', estate=False)
    return db.q("""SELECT s.name, t.business_date, t.product_id, t.product_name,
                          sum(t.quantity), sum(t.gross_sales),
                          sum(t.cost), sum(t.tax), sum(t.gross_profit),
                          count(*) FILTER (WHERE t.cost IS NULL)
                     FROM sales_summary t
                     JOIN store s ON s.id = t.store_id
                    WHERE""" + w + """
                 GROUP BY 1, 2, 3, 4
                 ORDER BY 1, 2, 4""", a)


def view_product_report(db, f):
    rows = alliance_rows(db, f)
    if not rows:
        return ('<div class="card"><div class="empty">Nothing in this range.<br><br>'
                'The Alliance daily product summary is loaded from the API\'s '
                '<code>/sales-summary</code> endpoint. If this range should have '
                'data, run <code>python ingest_reports.py --dataset sales-summary '
                '--from &lt;date&gt; --to &lt;date&gt;</code>.</div></div>')

    grand = float(sum(r[5] or 0 for r in rows)) or 1.0
    qty_t = float(sum(r[4] or 0 for r in rows))
    cost_t = sum(float(r[6]) for r in rows if r[6] is not None)
    tax_t = sum(float(r[7]) for r in rows if r[7] is not None)
    gp_t = sum(float(r[8]) for r in rows if r[8] is not None)
    no_cost = sum(1 for r in rows if r[6] is None)

    body = []
    for r in rows[:REPORT_CAP]:
        gsales = float(r[5] or 0)
        gp = float(r[8]) if r[8] is not None else None
        body.append(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
            '<td class="n">%s</td><td class="n">%s</td><td class="n">%.2f%%</td>'
            '%s%s%s%s</tr>'
            % (esc(r[0]), r[1], esc(r[2] or ''), esc(r[3]), _qty(r[4]),
               peso(gsales), gsales / grand * 100,
               _money_cell(r[6]), _money_cell(r[7]), _money_cell(r[8]),
               _pct_cell((gp / gsales * 100) if (gp is not None and gsales) else None)))

    cut = ''
    if len(rows) > REPORT_CAP:
        cut = ('<p class="note" style="margin-top:10px">Showing the first %s of %s '
               'rows. The CSV contains all of them.</p>'
               % (num(REPORT_CAP), num(len(rows))))

    note = ''
    if no_cost:
        note = ('<div class="warn">%s of %s rows carry no cost, so their Gross '
                'Profit and GP%% read &mdash; rather than being computed against a '
                'zero. The totals below sum only the rows that do.'
                % (num(no_cost), num(len(rows))))
        note += '</div>'

    return """
<div class="tiles">%s</div>
%s%s
<div class="card">
  <h2>Alliance product sales by branch and day</h2>
  <p class="note">Straight from the API's own <code>/sales-summary</code> report
    &mdash; one row per branch, per day, per product, with cost, tax and gross
    profit as the source states them. <strong>%%</strong> is the row's share of
    Gross Sales.
    <a href="/product-report.csv?%s" style="color:var(--s1)">Download CSV</a></p>
  <div class="scroll"><table>
    <thead><tr><th>Branch</th><th>Date</th><th>Product ID</th><th>Product Name</th>
      <th class="n">Qty</th><th class="n">Gross Sales</th><th class="n">%%</th>
      <th class="n">Cost</th><th class="n">Tax</th><th class="n">Gross Profit</th>
      <th class="n">GP %%</th></tr></thead>
    <tbody>%s</tbody>
    <tfoot><tr style="font-weight:600;border-top:2px solid var(--border)">
      <td colspan="4">Total &mdash; %s rows</td><td class="n">%s</td>
      <td class="n">%s</td><td class="n">100.00%%</td><td class="n">%s</td>
      <td class="n">%s</td><td class="n">%s</td><td class="n">%.2f%%</td>
    </tr></tfoot>
  </table></div>%s
</div>""" % (
        ''.join([tile('Rows', num(len(rows))),
                 tile('Branches', num(len({r[0] for r in rows}))),
                 tile('Products', num(len({r[3] for r in rows}))),
                 tile('Qty', _qty(qty_t)),
                 tile('Gross sales', peso(grand)),
                 tile('Gross profit', peso(gp_t),
                      '%.1f%% of gross sales' % (gp_t / grand * 100) if grand else '')]),
        coverage_note(db, f, 'ALLIANCE', 'sales-summary rows', 'sales_summary'),
        note, f.qs(), ''.join(body), num(len(rows)), _qty(qty_t), peso(grand),
        peso(cost_t), peso(tax_t), peso(gp_t),
        (gp_t / grand * 100) if grand else 0, cut)


def product_report_csv(db, f):
    import csv
    import io as _io
    rows = alliance_rows(db, f)
    grand = float(sum(r[5] or 0 for r in rows)) or 1.0
    buf = _io.StringIO()
    wr = csv.writer(buf, lineterminator='\n')
    wr.writerow(ALLIANCE_COLS)
    for r in rows:
        gs = float(r[5] or 0)
        gp = float(r[8]) if r[8] is not None else None
        wr.writerow([r[0], r[1], r[2] or '', r[3], _qty(r[4]), '%.2f' % gs,
                     '%.4f' % (gs / grand * 100),
                     '%.2f' % float(r[6]) if r[6] is not None else '',
                     '%.2f' % float(r[7]) if r[7] is not None else '',
                     '%.2f' % gp if gp is not None else '',
                     '%.4f' % (gp / gs * 100) if (gp is not None and gs) else ''])
    wr.writerow(['Total', '', '', '', _qty(sum(float(r[4] or 0) for r in rows)),
                 '%.2f' % grand, '100.0000',
                 '%.2f' % sum(float(r[6]) for r in rows if r[6] is not None),
                 '%.2f' % sum(float(r[7]) for r in rows if r[7] is not None),
                 '%.2f' % sum(float(r[8]) for r in rows if r[8] is not None), ''])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# StoreHub products -- from the API's /storehub-product-movement
# ---------------------------------------------------------------------------

STOREHUB_COLS = ['Branch', 'Date', 'Product Name', 'Product Category', 'SKU ID',
                 'Total Items Sold', 'Total Sales', 'Total Sales Returned',
                 'Total Discount', 'Discount %', 'Item Net Sales', 'Average Cost',
                 'Average Net Sales', 'Gross Profit', 'Gross Profit %']


def storehub_rows(db, f):
    w, a = f.where('t', estate=False)
    return db.q("""SELECT s.name, t.business_date, t.product_name,
                          t.product_category, t.sku_id,
                          sum(t.total_items_sold), sum(t.total_sales),
                          sum(t.total_sales_returned), sum(t.total_discount),
                          sum(t.item_net_sales), avg(t.average_cost),
                          avg(t.average_net_sales), sum(t.gross_profit)
                     FROM product_movement t
                     JOIN store s ON s.id = t.store_id
                    WHERE""" + w + """
                 GROUP BY 1, 2, 3, 4, 5
                 ORDER BY 1, 2, 3""", a)


def view_storehub_report(db, f):
    rows = storehub_rows(db, f)
    if not rows:
        return ('<div class="card"><div class="empty">Nothing in this range.<br><br>'
                'The StoreHub product movement report is loaded from the API\'s '
                '<code>/storehub-product-movement</code> endpoint, which starts in '
                'May 2026. Run <code>python ingest_reports.py --dataset '
                'product-movement --from &lt;date&gt; --to &lt;date&gt;</code> if a '
                'range is missing.</div></div>')

    qty_t = float(sum(r[5] or 0 for r in rows))
    sales_t = float(sum(r[6] or 0 for r in rows))
    ret_t = float(sum(r[7] or 0 for r in rows))
    disc_t = float(sum(r[8] or 0 for r in rows))
    net_t = float(sum(r[9] or 0 for r in rows))
    gp_t = sum(float(r[12]) for r in rows if r[12] is not None)
    with_cat = sum(1 for r in rows if r[3])

    body = []
    dash = '<span style="color:var(--muted)">&mdash;</span>'
    for r in rows[:REPORT_CAP]:
        sales, net = float(r[6] or 0), float(r[9] or 0)
        disc = float(r[8] or 0)
        gp = float(r[12]) if r[12] is not None else None
        body.append(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>'
            '<td class="n">%s</td><td class="n">%s</td><td class="n">%s</td>'
            '<td class="n">%s</td><td class="n">%.2f%%</td><td class="n">%s</td>'
            '%s%s%s%s</tr>'
            % (esc(r[0]), r[1], esc(r[2]), esc(r[3]) if r[3] else dash,
               esc(r[4]) if r[4] else dash, _qty(r[5]), peso(sales), peso(r[7]),
               peso(disc), (disc / (sales + disc) * 100) if (sales + disc) else 0,
               peso(net), _money_cell(r[10]), _money_cell(r[11]), _money_cell(r[12]),
               _pct_cell((gp / net * 100) if (gp is not None and net) else None)))

    cut = ''
    if len(rows) > REPORT_CAP:
        cut = ('<p class="note" style="margin-top:10px">Showing the first %s of %s '
               'rows. The CSV contains all of them.</p>'
               % (num(REPORT_CAP), num(len(rows))))

    return """
<div class="tiles">%s</div>
%s
<div class="card">
  <h2>StoreHub product sales by branch and day</h2>
  <p class="note">Straight from the API's own
    <code>/storehub-product-movement</code> report &mdash; the back-office "Sales
    by Product" figures as the source states them, including product category
    and SKU. %s of %s rows carry a category.
    <a href="/storehub-report.csv?%s" style="color:var(--s1)">Download CSV</a></p>
  <div class="scroll"><table>
    <thead><tr><th>Branch</th><th>Date</th><th>Product Name</th>
      <th>Product Category</th><th>SKU ID</th>
      <th class="n">Total Items Sold</th><th class="n">Total Sales</th>
      <th class="n">Total Sales Returned</th><th class="n">Total Discount</th>
      <th class="n">Discount %%</th><th class="n">Item Net Sales</th>
      <th class="n">Average Cost</th><th class="n">Average Net Sales</th>
      <th class="n">Gross Profit</th><th class="n">Gross Profit %%</th></tr></thead>
    <tbody>%s</tbody>
    <tfoot><tr style="font-weight:600;border-top:2px solid var(--border)">
      <td colspan="5">Total &mdash; %s rows</td><td class="n">%s</td>
      <td class="n">%s</td><td class="n">%s</td><td class="n">%s</td>
      <td class="n">%.2f%%</td><td class="n">%s</td><td class="n">&mdash;</td>
      <td class="n">&mdash;</td><td class="n">%s</td><td class="n">%.2f%%</td>
    </tr></tfoot>
  </table></div>%s
</div>""" % (
        ''.join([tile('Rows', num(len(rows))),
                 tile('Branches', num(len({r[0] for r in rows}))),
                 tile('Products', num(len({r[2] for r in rows}))),
                 tile('Items sold', _qty(qty_t)),
                 tile('Total sales', peso(sales_t)),
                 tile('Net sales', peso(net_t))]),
        coverage_note(db, f, 'STOREHUB', 'product-movement rows', 'product_movement'),
        num(with_cat), num(len(rows)), f.qs(), ''.join(body), num(len(rows)),
        _qty(qty_t), peso(sales_t), peso(ret_t), peso(disc_t),
        (disc_t / (sales_t + disc_t) * 100) if (sales_t + disc_t) else 0,
        peso(net_t), peso(gp_t), (gp_t / net_t * 100) if net_t else 0, cut)


def storehub_report_csv(db, f):
    import csv
    import io as _io
    rows = storehub_rows(db, f)
    buf = _io.StringIO()
    wr = csv.writer(buf, lineterminator='\n')
    wr.writerow(STOREHUB_COLS)
    for r in rows:
        sales, disc, net = float(r[6] or 0), float(r[8] or 0), float(r[9] or 0)
        gp = float(r[12]) if r[12] is not None else None
        wr.writerow([r[0], r[1], r[2], r[3] or '', r[4] or '', _qty(r[5]),
                     '%.2f' % sales, '%.2f' % float(r[7] or 0), '%.2f' % disc,
                     '%.2f' % ((disc / (sales + disc) * 100) if (sales + disc) else 0),
                     '%.2f' % net,
                     '%.2f' % float(r[10]) if r[10] is not None else '',
                     '%.2f' % float(r[11]) if r[11] is not None else '',
                     '%.2f' % gp if gp is not None else '',
                     '%.2f' % ((gp / net * 100) if (gp is not None and net) else 0)
                     if gp is not None else ''])
    return buf.getvalue()


def products_to_categorise_csv(db, f):
    """Kept as a fallback: the API now supplies the category, so this is only
    useful for products it has not covered."""
    import csv
    import io as _io
    w, a = f.where('t', estate=False)
    rows = db.q("""SELECT t.product_name, min(t.sku_id), sum(t.total_items_sold),
                          sum(t.total_sales), min(t.product_category)
                     FROM product_movement t WHERE""" + w + """
                 GROUP BY 1 ORDER BY 3 DESC""", a)
    buf = _io.StringIO()
    wr = csv.writer(buf, lineterminator='\n')
    wr.writerow(['Product Name', 'SKU ID', 'Product Category',
                 'Units sold (reference)', 'Sales (reference)'])
    for nm, sku, qty, sales, cat in rows:
        wr.writerow([nm, sku or '', cat or '', _qty(qty), '%.2f' % float(sales or 0)])
    return buf.getvalue()


VIEWS = {'/': ('Overview', view_overview), '/hours': ('Hours', view_hours),
         '/products': ('Products', view_products),
         '/exceptions': ('Exceptions', view_exceptions),
         '/product-report': ('Alliance products', view_product_report),
         '/storehub-report': ('StoreHub products', view_storehub_report)}

#: Views that only make sense for one estate. The filter is forced rather than
#: merely defaulted, so the branch list, the query and the heading can never
#: disagree with what the page is actually showing.
FORCED_ESTATE = {'/product-report': 'ALLIANCE', '/storehub-report': 'STOREHUB'}

#: Which table's dates bound each view. Only the report tabs differ, and they
#: differ by nine months, so it matters.
BOUNDS_TABLE = {'/product-report': 'sales_summary',
                '/storehub-report': 'product_movement'}


#: Reachable without being signed in. Everything else is gated.
OPEN_PATHS = ('/login', '/auth/start', '/auth/callback', '/logout')


class Handler(BaseHTTPRequestHandler):
    server_version = 'SalesAnalytics/1.0'

    # -- small helpers ------------------------------------------------------

    def _send(self, body, status=200, ctype='text/html; charset=utf-8', extra=()):
        raw = body.encode('utf-8') if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _redirect(self, to, extra=()):
        self.send_response(302)
        self.send_header('Location', to)
        self.send_header('Content-Length', '0')
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()

    def end_headers(self):
        # On every response, errors included: no MIME sniffing, no framing by
        # another site (the admin buttons must not be clickable through an
        # invisible iframe), and no dashboard URLs leaking in Referer headers.
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        super().end_headers()

    def _cookie(self, value, hours):
        """HttpOnly so no page script can read it; SameSite=Lax so it survives
        the redirect back from Google but is not sent from other sites; Secure
        when the public address is https, so it never travels unencrypted."""
        return ('Set-Cookie',
                'sa_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s'
                % (value, hours * 3600, '; Secure' if auth.https_only() else ''))

    def _gate(self, path):
        """(user, response_sent).

        Returns the signed-in user, or sends the sign-in / holding page and
        reports that it handled the request.

        With Google not yet configured the dashboard stays open, exactly as it
        was before sign-in existed. It binds to 127.0.0.1, so that is the same
        exposure as yesterday -- and locking someone out of their own dashboard
        because they have not been to the Google console yet would be a worse
        failure than the one it prevents.
        """
        if not auth.configured():
            return None, False

        me = auth.current_user(self.headers.get('Cookie'), auth.config()['secret'])

        if path in OPEN_PATHS:
            return me, False
        if not me:
            self._redirect('/login')
            return None, True
        if me['status'] != 'APPROVED':
            self._send(pending_page(me), 403)
            return me, True
        if path.startswith('/admin') and me['role'] != 'ADMIN':
            self._send(shell('Not allowed',
                             '<div class="card"><h1>Not allowed</h1><p>That page is '
                             'for administrators. You are signed in as %s.</p>'
                             '<a class="chip" href="/">Back to the dashboard</a>'
                             '</div>' % esc(me['email'])), 403)
            return me, True
        return me, False

    # -- sign-in ------------------------------------------------------------

    def _auth_routes(self, path, u, me):
        if path == '/login':
            if me and me['status'] == 'APPROVED':
                self._redirect('/')
            elif me:
                self._send(pending_page(me), 403)
            else:
                q = urllib.parse.parse_qs(u.query)
                msg = (q.get('msg') or [''])[0]
                self._send(login_page(msg, 'error' if msg else 'note'))
            return True

        if path == '/auth/start':
            self._redirect(auth.start_url(self.headers.get('Host')))
            return True

        if path == '/auth/callback':
            q = urllib.parse.parse_qs(u.query)
            if q.get('error'):
                self._redirect('/login?msg=' + urllib.parse.quote(
                    'Google reported: %s' % q['error'][0]))
                return True
            claims, err = auth.exchange((q.get('code') or [''])[0],
                                        (q.get('state') or [''])[0],
                                        self.headers.get('Host'))
            if err:
                self._redirect('/login?msg=' + urllib.parse.quote(err))
                return True
            user = auth.upsert_from_google(claims, auth.config()['admins'])
            if not user:
                self._redirect('/login?msg=' + urllib.parse.quote(
                    'That Google account gave no email address.'))
                return True
            cookie = self._cookie(
                auth.create_session(user, self.headers.get('User-Agent'),
                                    auth.config()['secret']),
                auth.SESSION_HOURS)
            self._redirect('/' if user['status'] == 'APPROVED' else '/pending',
                           extra=(cookie,))
            return True

        if path == '/logout':
            # Clearing the browser's cookie is not enough -- a copy of it would
            # still work. Deleting the session row kills every copy of this
            # browser's cookie. Other devices stay signed in; the Account page
            # is where to end those.
            if me:
                auth.end_session(me['session'])
            self._redirect('/login', extra=(self._cookie('', 0),))
            return True

        if path == '/pending':
            if me:
                self._send(pending_page(me), 403 if me['status'] != 'APPROVED' else 200)
            else:
                self._redirect('/login')
            return True
        return False

    # -- keeping one request from hurting the rest --------------------------

    #: A client that connects and then goes quiet is dropped after this many
    #: seconds instead of holding a thread forever.
    timeout = 60

    def send_response(self, *a, **k):
        # Remembered so an error after the reply has started is not answered
        # with a second, garbled reply on top of the first.
        self._responded = True
        super().send_response(*a, **k)

    def do_GET(self):
        self._guarded(self._get)

    def do_POST(self):
        self._guarded(self._post)

    def _guarded(self, handler):
        """Run one request so that nothing it does can take the server down.

        At most MAX_BUSY requests work at once; the rest wait their turn. A
        query past QUERY_TIMEOUT_S is cancelled by Postgres. Whatever goes
        wrong, the user gets a plain page saying what to do, the full detail
        goes to the log, and the server carries on serving everyone else.
        """
        self._responded = False
        if not _busy.acquire(timeout=QUEUE_WAIT_S):
            self._fail(503, 'The dashboard is busy',
                       'Too many reports are being worked out at once. '
                       'Wait a few seconds and reload the page.')
            return
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                        # the browser gave up; nobody to answer
        except psycopg.errors.QueryCanceled:
            self._fail(503, 'That took too long',
                       'This view ran past the %d-second limit and was stopped '
                       'so it could not slow the dashboard down for everyone '
                       'else. Try a shorter date range or fewer branches.'
                       % QUERY_TIMEOUT_S)
        except psycopg.OperationalError:
            self._log_failure()
            self._fail(503, 'Database unavailable',
                       'The dashboard could not reach its database. Nothing is '
                       'lost -- try again in a minute.')
        except Exception:
            self._log_failure()
            self._fail(500, 'That page failed',
                       'Something went wrong building this page, and it has '
                       'been logged. The rest of the dashboard is unaffected.')
        finally:
            _busy.release()

    def _log_failure(self):
        import traceback
        sys.stderr.write('%s %s failed:\n%s' % (self.command, self.path,
                                                traceback.format_exc()))
        sys.stderr.flush()

    def _fail(self, status, title, message):
        if self._responded:
            return                      # too late for a clean error page
        if urllib.parse.urlsplit(self.path).path.startswith('/insights'):
            # Fetched into a panel of an already-drawn page, not navigated to.
            body = '<div class="empty">%s. %s</div>' % (esc(title), esc(message))
        else:
            body = shell(title, '<div class="card"><h1>%s</h1><p>%s</p>'
                                '<a class="chip" href="">'
                                'Try again</a> <a class="chip" href="/">Back to the '
                                'dashboard</a></div>' % (esc(title), esc(message)))
        extra = (('Retry-After', '10'),) if status == 503 else ()
        try:
            self._send(body, status, extra=extra)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _post(self):
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip('/') or '/'
        me, done = self._gate(path)
        if done:
            return
        allowed = ((path == '/account' and me) or
                   (path == '/admin/users' and me and me['role'] == 'ADMIN'))
        if not allowed:
            self.send_error(404, 'No such page')
            return

        # The only forms are a couple of buttons: a few bytes. Refuse anything
        # that claims to be large rather than reading it into memory.
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= 65536:
            self.send_error(413, 'Request too large')
            return
        form = urllib.parse.parse_qs(self.rfile.read(length).decode('utf-8'))

        if path == '/account':
            which = (form.get('end') or [''])[0]
            if which == 'others':
                auth.end_other_sessions(me['id'], keep_sid=me['session'])
                self._redirect('/account?done=others')
            elif which == 'all':
                auth.end_other_sessions(me['id'])
                self._redirect('/login?msg=' + urllib.parse.quote(
                    'You have been signed out on every device.'),
                    extra=(self._cookie('', 0),))
            else:
                self._redirect('/account')
            return

        # One button, one decision -- the name carries the verb and the value
        # carries the user id, so a malformed post does nothing rather than
        # something arbitrary.
        for field, (status, role) in (('approve', ('APPROVED', None)),
                                      ('reject', ('REJECTED', None)),
                                      ('suspend', ('SUSPENDED', None)),
                                      ('promote', ('APPROVED', 'ADMIN'))):
            if field in form:
                try:
                    uid = int(form[field][0])
                except (ValueError, IndexError):
                    break
                current = None
                for row in auth.list_users():
                    if row[0] == uid:
                        current = row
                        break
                if current:
                    auth.set_status(uid, status, role or current[4], me['email'])
                break
        self._redirect('/admin/users')

    def _get(self):
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip('/') or '/'

        me, done = self._gate(path)
        if done:
            return
        if self._auth_routes(path, u, me):
            return

        if path == '/admin/users':
            db = DB()
            f = Filters(db, urllib.parse.parse_qs(u.query))
            self._send(page('Users', path, f, db, admin_users_page(db, f, me), me))
            return

        if path == '/account':
            if not me:                  # sign-in switched off: no account to show
                self._redirect('/')
                return
            db = DB()
            q = urllib.parse.parse_qs(u.query)
            f = Filters(db, q)
            self._send(page('Account', path, f, db,
                            account_page(me, (q.get('done') or [''])[0]), me))
            return

        # The Opportunities analysis reads the whole range and can take seconds
        # on a year of data. Served separately so the dashboard renders at once
        # and fills this panel in when it is ready.
        if path == '/insights':
            db = DB()
            q = urllib.parse.parse_qs(u.query)
            frag = render_insights(db, Filters(db, q),
                                   (q.get('view') or ['overview'])[0])
            raw = frag.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path == '/products-to-categorise.csv':
            db = DB()
            f = Filters(db, urllib.parse.parse_qs(u.query), 'product_movement')
            f.force_estate(db, 'STOREHUB')
            raw = products_to_categorise_csv(db, f).encode('utf-8-sig')
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition',
                             'attachment; filename="products_to_categorise.csv"')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path in ('/product-report.csv', '/storehub-report.csv'):
            alliance = path.startswith('/product-report')
            db = DB()
            f = Filters(db, urllib.parse.parse_qs(u.query),
                        'sales_summary' if alliance else 'product_movement')
            f.force_estate(db, 'ALLIANCE' if alliance else 'STOREHUB')
            raw = (product_report_csv(db, f) if alliance
                   else storehub_report_csv(db, f)).encode('utf-8-sig')
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition',
                             'attachment; filename="%s_product_sales_%s_%s.csv"'
                             % ('alliance' if alliance else 'storehub', f.frm, f.to))
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if path not in VIEWS:
            self.send_error(404, 'No such page')
            return
        title, fn = VIEWS[path]
        # A failure here is answered by _guarded: a plain error page for the
        # user, the traceback to the log -- never the traceback to the browser,
        # which would show SQL and file paths to anyone who can sign in.
        db = DB()
        f = Filters(db, urllib.parse.parse_qs(u.query),
                    BOUNDS_TABLE.get(path, 'transaction'))
        if path in FORCED_ESTATE:
            f.force_estate(db, FORCED_ESTATE[path])
        if f.data_lo is None:
            body = ('<div class="card"><div class="empty">The warehouse is empty. '
                    'Run <code>python ingest.py --dataset both --recent 7</code> '
                    'first.</div></div>')
            out = page(title, path, f, db, body, me)
        else:
            out = page(title, path, f, db, fn(db, f), me)
        raw = out.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def lan_addresses():
    """The addresses other machines could use to reach this one."""
    import socket
    names = []
    host = socket.gethostname()
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith('127.') and ip not in names:
                names.append(ip)
    except socket.gaierror:
        pass
    return host, names


def main():
    ap = argparse.ArgumentParser(description='Sales analytics dashboard')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8001)))
    ap.add_argument('--no-browser', action='store_true')
    ap.add_argument('--host', default='127.0.0.1',
                    help='0.0.0.0 to accept connections from the network. '
                         'Requires Google sign-in to be configured first.')
    ap.add_argument('--behind-proxy', action='store_true',
                    help='a reverse proxy (Nginx) publishes this to the network. '
                         'Listens on --host as usual, but is held to the same '
                         'rules as --host 0.0.0.0: sign-in and PUBLIC_BASE_URL '
                         'are required.')
    ap.add_argument('--i-accept-no-sign-in', action='store_true',
                    help=argparse.SUPPRESS)
    a = ap.parse_args()

    # Behind a proxy the socket is 127.0.0.1, but the audience is the network.
    network = a.behind_proxy or a.host not in ('127.0.0.1', 'localhost', '::1')

    if a.behind_proxy and not auth.config()['base']:
        print('Refusing to start behind a proxy without PUBLIC_BASE_URL.')
        print('')
        print('It fixes the Google redirect to the real address instead of')
        print('trusting whatever Host header arrives, and an https:// value')
        print('marks the session cookie Secure. Set it in .env, e.g.')
        print('')
        print('    PUBLIC_BASE_URL=https://sales.example.com')
        print('')
        print('(python setup_google.py asks for it.)')
        return 2

    # Serving this to the network without sign-in publishes every branch's
    # sales, costs and margins to anyone who can reach the port. That is a
    # decision someone should make on purpose, so it cannot be arrived at by
    # typing a flag: the server refuses, and says how to fix it properly.
    if network and not auth.configured() and not a.i_accept_no_sign_in:
        print('Refusing to listen on %s with sign-in switched off.'
              % ('the network (behind a proxy)' if a.behind_proxy else a.host))
        print('')
        print('Anyone who could reach this port would see every branch, every')
        print('figure and every margin, with no login at all.')
        print('')
        print('Turn sign-in on first:')
        print('')
        print('    python setup_google.py')
        print('')
        print('then start it again the same way. If you genuinely want an open')
        print('dashboard on this network -- a closed lab, say -- pass')
        print('--i-accept-no-sign-in and it will start.')
        return 2

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    local = 'http://localhost:%d/' % a.port
    print('Sales analytics on %s' % local)

    if a.behind_proxy:
        print('Published by the reverse proxy as %s/' % auth.config()['base'])
        print('Sign-in is on. New accounts land as PENDING until approved.')
        if not auth.https_only():
            print('')
            print('*** PUBLIC_BASE_URL is plain http: sales figures and the session')
            print('    cookie cross the network unencrypted. Put a certificate on')
            print('    the proxy and change it to https://. ***')
    elif network:
        host, ips = lan_addresses()
        print('Also reachable from the network as:')
        for ip in ips:
            print('    http://%s:%d/' % (ip, a.port))
        print('    http://%s:%d/' % (host, a.port))
        if not auth.configured():
            print('')
            print('*** NO SIGN-IN. Everyone on this network can read everything. ***')
        else:
            print('')
            print('Sign-in is on. New accounts land as PENDING until approved.')
            if not auth.config()['base']:
                print('PUBLIC_BASE_URL is unset, so the Google redirect is built')
                print('from each request\'s Host header. If sign-in fails with')
                print('redirect_uri_mismatch, run setup_google.py and give the')
                print('exact address people will type.')
        print('')
        print('Windows Firewall may still block this. If colleagues cannot')
        print('connect, allow the port from an ADMIN prompt:')
        print('    netsh advfirewall firewall add rule '
              'name="Sales Analytics %d" dir=in action=allow '
              'protocol=TCP localport=%d' % (a.port, a.port))
    else:
        print('Reachable from this PC only. Ctrl+C to stop.')

    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(local)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('')
        print('Stopped.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
