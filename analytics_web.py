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


class DB:
    def __init__(self):
        self.url = database_url()

    def q(self, sql, args=()):
        with psycopg.connect(self.url) as c:
            return c.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None


# -- filters ---------------------------------------------------------------

class Filters:
    def __init__(self, db, qs):
        lo, hi = db.one("SELECT min(business_date), max(business_date) "
                        'FROM "transaction"') or (None, None)
        self.data_lo, self.data_hi = lo, hi
        today = hi or dt.date.today()
        default_lo = max(lo, today - dt.timedelta(days=29)) if lo else today

        self.frm = self._date(qs.get('from', [None])[0], default_lo)
        self.to = self._date(qs.get('to', [None])[0], today)
        self.estate = qs.get('estate', [''])[0]
        if self.estate not in ('STOREHUB', 'ALLIANCE'):
            self.estate = ''
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

    @staticmethod
    def _date(v, fallback):
        try:
            return dt.date.fromisoformat(v)
        except (TypeError, ValueError):
            return fallback

    def where(self, alias='t'):
        """SQL fragment + args shared by every query on the page."""
        sql = ' %s.business_date BETWEEN %%s AND %%s' % alias
        args = [self.frm, self.to]
        if self.estate:
            sql += ' AND %s.estate = %%s::"Estate"' % alias
            args.append(self.estate)
        if self.stores:
            sql += ' AND %s.store_id = ANY(%%s)' % alias
            args.append(self.stores)
        return sql, args

    def qs(self, **over):
        d = {'from': self.frm.isoformat(), 'to': self.to.isoformat(),
             'estate': self.estate}
        d.update(over)
        pairs = [(k, v) for k, v in d.items() if v != '']
        # One pair per branch, so a link out of this page keeps the whole
        # selection rather than the first of it.
        pairs += [('store', s) for s in self.stores]
        return urllib.parse.urlencode(pairs)

    @property
    def days(self):
        return (self.to - self.frm).days + 1


def filter_bar(db, f, path):
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

    span = 'On file: %s to %s' % (f.data_lo, f.data_hi) if f.data_lo else ''
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
  <div><label>Estate</label><select name="estate">%s</select></div>
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
  <div style="margin-left:auto;color:var(--muted);font-size:11px">%s</div>
</form>""" % (path, f.frm, f.to, est_opts, esc(label), ''.join(boxes),
              ' '.join(quick), esc(span))


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
       ('/exceptions', 'Exceptions'))


def page(title, path, f, db, body):
    nav = ''.join('<a href="%s?%s" class="%s">%s</a>'
                  % (p, f.qs(), 'on' if p == path else '', l) for p, l in NAV)
    return """<!doctype html><html><head><meta charset="utf-8">
<title>%s &middot; Sales analytics</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>%s</style></head><body>
<header><h1>Sales analytics</h1>
<div class="sub">Ribshack group &middot; %s to %s &middot; %d days%s</div>
<nav>%s</nav></header>
<main>%s%s</main><script>%s</script></body></html>""" % (
        esc(title), CSS, f.frm, f.to, f.days,
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


VIEWS = {'/': ('Overview', view_overview), '/hours': ('Hours', view_hours),
         '/products': ('Products', view_products),
         '/exceptions': ('Exceptions', view_exceptions)}


class Handler(BaseHTTPRequestHandler):
    server_version = 'SalesAnalytics/1.0'

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip('/') or '/'

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

        if path not in VIEWS:
            self.send_error(404, 'No such page')
            return
        title, fn = VIEWS[path]
        try:
            db = DB()
            f = Filters(db, urllib.parse.parse_qs(u.query))
            if f.data_lo is None:
                body = ('<div class="card"><div class="empty">The warehouse is empty. '
                        'Run <code>python ingest.py --dataset both --recent 7</code> '
                        'first.</div></div>')
                out = page(title, path, f, db, body)
            else:
                out = page(title, path, f, db, fn(db, f))
        except Exception as e:
            import traceback
            out = ('<!doctype html><meta charset="utf-8"><style>%s</style>'
                   '<main><div class="card"><h2>That page failed</h2><pre>%s</pre>'
                   '</div></main>' % (CSS, esc(traceback.format_exc()[-3000:])))
        raw = out.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description='Sales analytics dashboard')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8001)))
    ap.add_argument('--no-browser', action='store_true')
    a = ap.parse_args()

    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    url = 'http://localhost:%d/' % a.port
    print('Sales analytics on %s' % url)
    print('Reachable from this PC only. Ctrl+C to stop.')
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
