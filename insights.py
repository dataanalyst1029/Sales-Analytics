"""Work out why a day under-performed, and what could be done about it.

Every finding here is computed from the warehouse and carries the figures it was
derived from. Nothing is generated prose and nothing is estimated: if the data
cannot support a conclusion, the conclusion is not offered.

The reasoning follows the way a trading day actually decomposes:

    sales = transactions x average ticket

so a shortfall is either fewer people or smaller baskets, and those two call for
completely different responses. Telling them apart is most of the value here --
"run a promotion" is useless advice when the problem is that nobody came in, and
a footfall campaign is wasted when the tills were busy but the baskets were thin.

Then, before blaming the day: a Sunday that trades below a Tuesday is not an
under-performing Sunday. Each day is measured against the median of nearby days
of the same weekday -- local, so it moves with the trend, and a median so one
freak day does not set the bar for its neighbours.

Expected transactions come from that same local basis, which is what makes the
two effects sum exactly to the shortfall they explain.

Each tab ends with a `lever`: the one action its data most supports, sized in
pesos. The sizing is ARITHMETIC ON A STATED ASSUMPTION, never a forecast -- it
answers "if this moved by X, what is that worth?", with X written on the face of
it, so the assumption can be argued with instead of hidden. Nothing here knows
whether an offer will work; it knows only what the prize is if it does, which is
what decides whether the offer is worth designing at all.

What is deliberately NOT concluded:

  * No causes outside the data. The warehouse knows nothing about weather,
    holidays, mall footfall or a competitor opening, so no finding claims them.
  * No product advice for the Alliance estate, which has no line detail at all.
  * Nothing from a range too short to have an average worth comparing against.
"""
import collections
import datetime as dt

#: Below this many trading days there is no meaningful "normal" to compare a day
#: against, and any finding would be noise dressed up as insight.
MIN_DAYS = 6

#: A day has to miss its expected level by this much before it is worth raising.
#: Under this, day-to-day variation explains it.
MATERIAL_GAP = 0.08

#: Weekday averages need this many instances before they are trusted over the
#: overall average -- two Sundays do not establish what a Sunday looks like.
MIN_WEEKDAY_SAMPLE = 3

#: Under this share of its expected level, a day is far likelier to be missing
#: data than to have genuinely traded that badly. A branch does not lose 90% of
#: its sales and stay open, so calling that a promotion opportunity would send
#: someone chasing a loading gap with a discount.
DATA_GAP_RATIO = 0.35

#: Co-occurrence is scanned over at most this many of the most recent days in
#: range. The self-join is the expensive part of this module, and what sells
#: together does not need a year of history to establish.
PAIR_WINDOW_DAYS = 45


def _day(d):
    """'Tue 8 Sep'. Built by hand because %-d is POSIX-only and %#d is Windows-only,
    and this has to render the same on both."""
    return '%s %d %s' % (d.strftime('%a'), d.day, d.strftime('%b'))


def _expected_txn(sales, txns, d, min_sample):
    """Expected transactions for a day, on the SAME basis as expected sales.

    Deliberately kept in step with `expected()`: if one is a local weekday median
    and the other a period-wide mean, the traffic and basket effects stop summing
    to the shortfall, and the panel contradicts itself in its own evidence table.
    """
    near = [txns[k] for k in sales
            if k != d and k.weekday() == d.weekday() and abs((k - d).days) <= 28]
    if len(near) >= min_sample:
        return _median(near)
    near = [txns[k] for k in sales if k != d and abs((k - d).days) <= 14]
    if len(near) >= 5:
        return _median(near)
    return sum(txns.values()) / len(txns)


def _median(vals):
    v = sorted(vals)
    n = len(v)
    if not n:
        return 0.0
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _pct(a, b):
    return ((float(a) - float(b)) / float(b) * 100) if b else 0.0


def analyse(db, f):
    """[finding, ...] for the current filter. Each is a dict the page renders.

    finding = {'kind', 'title', 'body', 'evidence' (list of (label, value))}
    """
    w, a = f.where()

    daily = db.q('SELECT business_date, sum(amount), count(*) '
                 'FROM "transaction" t WHERE' + w +
                 ' GROUP BY 1 ORDER BY 1', a)
    if len(daily) < MIN_DAYS:
        return [{'kind': 'none',
                 'title': 'Not enough days to compare',
                 'body': 'This range has %d trading day(s). At least %d are needed '
                         'before one day can be called low against a normal. Widen '
                         'the range and the analysis appears.'
                         % (len(daily), MIN_DAYS),
                 'evidence': []}]

    sales = {d: float(s) for d, s, _ in daily}
    txns = {d: int(n) for d, _, n in daily}
    mean_sales = sum(sales.values()) / len(sales)

    by_dow = collections.defaultdict(list)
    for d in sales:
        by_dow[d.weekday()].append(sales[d])
    dow_mean = {k: sum(v) / len(v) for k, v in by_dow.items()}

    def expected(d):
        """What this day should have done, judged LOCALLY.

        A whole-range average is worthless over a long window here: this feed
        grew from ~18k transactions a month in late 2025 to ~584k by August
        2026, so a year-wide mean would mark almost every early day as a
        catastrophe and every recent one as a triumph. Neither is about trading.

        So the baseline is the same weekday within four weeks either side, which
        moves with the trend and with any change in what is being fed in. Median
        rather than mean, so one freak day does not set the bar for its
        neighbours.
        """
        near = [sales[k] for k in sales
                if k != d and k.weekday() == d.weekday() and abs((k - d).days) <= 28]
        if len(near) >= MIN_WEEKDAY_SAMPLE:
            return _median(near), 'nearby %ss' % d.strftime('%A')
        near = [sales[k] for k in sales if k != d and abs((k - d).days) <= 14]
        if len(near) >= 5:
            return _median(near), 'the surrounding fortnight'
        return mean_sales, 'the period average'

    # The newest day on file is partial by definition -- the API is still
    # collecting it -- so it is never the "weakest day". Reporting it as one
    # would raise a false alarm every single day.
    latest = db.one('SELECT max(business_date) FROM "transaction"')[0]
    gaps = []
    for d, v in sales.items():
        if d == latest:
            continue
        exp, basis = expected(d)
        if exp:
            gaps.append((v - exp, (v - exp) / exp, d, exp, basis))
    gaps.sort()
    if not gaps:
        return [{'kind': 'none', 'title': 'Not enough complete days to compare',
                 'body': 'Every day in this range is either the still-collecting '
                         'latest day or has no comparison to measure against.',
                 'evidence': []}]

    findings = []

    # Days so far below their level that missing data is the likelier
    # explanation are pulled out first, and excluded from the trading analysis.
    suspect = [g for g in gaps if g[3] and (g[0] + g[3]) < g[3] * DATA_GAP_RATIO]
    if suspect:
        findings.append({
            'kind': 'warn',
            'title': '%d day(s) look like missing data, not weak trading'
                     % len(suspect),
            'body': ('Each of these came in under %.0f%% of its expected level. A '
                     'branch does not lose that share of a day and stay open, so '
                     'these read as a gap in what was loaded or sent. They are '
                     'excluded from the analysis below. Worth confirming against '
                     'the branch before treating any of them as a sales problem.'
                     % (DATA_GAP_RATIO * 100)),
            'evidence': [(_day(g[2]), '%s vs %s expected'
                          % (_money(g[0] + g[3]), _money(g[3]))) for g in suspect[:6]]})
        gaps = [g for g in gaps if g not in suspect]
        if not gaps:
            return findings

    worst_gap, worst_rel, worst_day, worst_exp, basis = gaps[0]

    if worst_rel > -MATERIAL_GAP:
        findings.append({
            'kind': 'good',
            'title': 'No day stands out as weak',
            'body': 'The softest day in this range, %s, came in %.1f%% under %s '
                    '— inside normal day-to-day variation. There is no '
                    'shortfall here worth chasing.'
                    % (_day(worst_day), abs(worst_rel * 100), basis),
            'evidence': [('Softest day', _day(worst_day)),
                         ('Its sales', worst_gap + worst_exp),
                         ('Expected', worst_exp)]})
        return findings + _weekday_finding(dow_mean, by_dow, mean_sales) + \
            _pairs_finding(db, f, w, a)

    # -- decompose the worst day: traffic or basket size? --------------------
    # Both halves MUST come from the same baseline as the expected sales figure,
    # or the two effects will not add up to the gap they are meant to explain --
    # and the panel prints a shortfall beside components that sum to something
    # else entirely.
    exp_txn = _expected_txn(sales, txns, worst_day, MIN_WEEKDAY_SAMPLE)
    exp_ticket = (worst_exp / exp_txn) if exp_txn else 0.0
    act_txn = txns[worst_day]
    act_ticket = (sales[worst_day] / act_txn) if act_txn else 0.0

    # (T-T0)*P0 + (P-P0)*T  ==  T*P - T0*P0, exactly the shortfall.
    traffic_effect = (act_txn - exp_txn) * exp_ticket
    ticket_effect = (act_ticket - exp_ticket) * act_txn

    # The culprit is whichever effect is most NEGATIVE, not whichever is largest
    # in absolute terms: a ticket that rose while footfall collapsed did not
    # cause the shortfall, it is what stopped it being worse.
    driver = 'traffic' if traffic_effect <= ticket_effect else 'ticket'
    both_down = traffic_effect < 0 and ticket_effect < 0
    if both_down:
        cause = 'fewer customers and smaller baskets together'
    elif driver == 'traffic':
        cause = ('fewer customers \u2014 baskets actually grew, which softened it'
                 if ticket_effect > 0 else 'fewer customers, not smaller baskets')
    else:
        cause = ('smaller baskets \u2014 footfall actually rose, which softened it'
                 if traffic_effect > 0 else 'smaller baskets, not fewer customers')

    findings.append({
        'kind': 'gap',
        'title': '%s was the weakest day, %.0f%% under %s'
                 % (_day(worst_day), abs(worst_rel * 100), basis),
        'body': ('The shortfall is %s \u2014 %s. Transactions ran %s against a usual '
                 '%s, and the average ticket was %s against a usual %s.'
                 % (_money(abs(worst_gap)), cause,
                    '{:,}'.format(act_txn), '{:,.0f}'.format(exp_txn),
                    _money(act_ticket), _money(exp_ticket))),
        'evidence': [('Sales that day', sales[worst_day]),
                     ('Expected', worst_exp),
                     ('Shortfall', worst_gap),
                     ('From footfall', traffic_effect),
                     ('From basket size', ticket_effect)]})

    # -- what to do about it --------------------------------------------------
    ticket_off = _pct(act_ticket, exp_ticket)
    traffic_off = _pct(act_txn, exp_txn)

    if both_down:
        findings.append({
            'kind': 'action',
            'title': 'Both fell that day \u2014 %s by more'
                     % ('footfall' if driver == 'traffic' else 'basket size'),
            'body': ('%.0f%% fewer customers AND a ticket %.0f%% below usual, so this '
                     'is not a single lever. %s accounts for the larger share and is '
                     'where to start, but an attachment offer alone would leave the '
                     'footfall gap untouched, and a footfall campaign alone would pull '
                     'in customers who are currently spending below normal anyway.'
                     % (abs(traffic_off), abs(ticket_off),
                        'Footfall' if driver == 'traffic' else 'Basket size')),
            'evidence': [('Transactions that day', act_txn),
                         ('Usual transactions', round(exp_txn)),
                         ('Average ticket', act_ticket),
                         ('Usual average ticket', exp_ticket),
                         ('From footfall', traffic_effect),
                         ('From basket size', ticket_effect)]})
    elif driver == 'traffic':
        findings.append({
            'kind': 'action',
            'title': 'A footfall problem, not a basket problem',
            'body': ('Each basket was %s, so the customers who came behaved normally '
                     '\u2014 there were simply %s fewer of them. A discount on what '
                     'they already buy would give away margin on customers you were '
                     'always going to get. What moves this number is a reason to '
                     'visit: a time-boxed offer promoted outside the store, or a '
                     'bundle advertised at the mall entrance rather than at the till.'
                     % (('%.0f%% bigger than usual' % ticket_off) if ticket_off > 1
                        else ('within %.0f%% of usual' % max(1.0, abs(ticket_off))),
                        '{:,.0f}'.format(abs(exp_txn - act_txn)))),
            'evidence': [('Transactions that day', act_txn),
                         ('Usual transactions', round(exp_txn)),
                         ('Missing customers', round(exp_txn - act_txn)),
                         ('Cost of those customers', traffic_effect)]})
    else:
        findings.append({
            'kind': 'action',
            'title': 'A basket problem \u2014 the customers came, and spent less',
            'body': ('Footfall was %s, so the lever is what goes into each basket '
                     'rather than how many baskets there are. An attachment offer at '
                     'the till \u2014 a drink or a side priced to make adding it '
                     'obvious \u2014 targets exactly this gap. Recovering %s per '
                     "ticket closes the whole shortfall at that day's footfall."
                     % (('%.0f%% above usual' % traffic_off) if traffic_off > 1
                        else ('within %.0f%% of usual' % max(1.0, abs(traffic_off))),
                        _money(exp_ticket - act_ticket))),
            'evidence': [('Average ticket', act_ticket),
                         ('Usual average ticket', exp_ticket),
                         ('Gap per ticket', act_ticket - exp_ticket),
                         ('Transactions that day', act_txn),
                         ('Cost of the smaller basket', ticket_effect)]})


    findings += _basket_finding(db, f, w, a, worst_day)
    findings += _product_finding(db, f, w, a, worst_day, len(sales))
    findings += _weekday_finding(dow_mean, by_dow, mean_sales)
    findings += _pairs_finding(db, f, w, a)
    return findings


def _basket_finding(db, f, w, a, day):
    """Items per basket on the weak day against the rest of the range."""
    row = db.one("""SELECT avg(c) FILTER (WHERE d = %s), avg(c) FILTER (WHERE d <> %s)
                      FROM (SELECT t.business_date d, t.id, count(*) c
                              FROM "transaction" t
                              JOIN transaction_item i ON i.transaction_id = t.id
                             WHERE""" + w + ' GROUP BY 1,2) x', [day, day] + a)
    if not row or row[0] is None or row[1] is None:
        return []
    on, off = float(row[0]), float(row[1])
    if off and abs(_pct(on, off)) < 3:
        return []
    return [{
        'kind': 'detail',
        'title': 'Baskets held %.2f items that day against %.2f normally' % (on, off),
        'body': ('%s items per basket. %s'
                 % ('%.1f%% fewer' % abs(_pct(on, off)) if on < off
                    else '%.1f%% more' % _pct(on, off),
                    'A bundle that puts a second item in the basket is the direct '
                    'answer to this one.' if on < off else
                    'Basket size was not the problem on this day.')),
        'evidence': [('Items per basket, that day', '%.2f items' % on),
                     ('Items per basket, other days', '%.2f items' % off)]}]


def _product_finding(db, f, w, a, day, n_days):
    """Which products sold below their own daily norm on the weak day."""
    rows = db.q("""SELECT coalesce(p.name, 'id ' || i.product_source_id) nm,
                          sum(i.quantity) FILTER (WHERE t.business_date = %s) on_day,
                          sum(i.quantity) FILTER (WHERE t.business_date <> %s) other
                     FROM transaction_item i
                     JOIN "transaction" t ON t.id = i.transaction_id
                LEFT JOIN product p ON p.id = i.product_id
                    WHERE""" + w + ' GROUP BY 1', [day, day] + a)
    if not rows:
        return []
    shortfalls = []
    for nm, on_day, other in rows:
        on_day = float(on_day or 0)
        norm = float(other or 0) / max(1, n_days - 1)
        if norm >= 5 and on_day < norm:          # ignore products that barely sell
            shortfalls.append((norm - on_day, nm, on_day, norm))
    if not shortfalls:
        return []
    shortfalls.sort(reverse=True)
    top = shortfalls[:5]
    lost = sum(s[0] for s in top)
    return [{
        'kind': 'detail',
        'title': 'Where the units went missing',
        'body': ('These five account for %.0f fewer units than a normal day in this '
                 'range. They are the lines a promotion on this day would have to '
                 'move to close the gap.' % lost),
        'head': ('Product', 'Units that day vs usual'),
        'evidence': [(nm, '%s sold vs %s usual, in units' % (_fmt(on_day), _fmt(norm)))
                     for _, nm, on_day, norm in top]}]


def _weekday_finding(dow_mean, by_dow, mean_sales):
    """A weekday that is persistently below the others is a standing opportunity."""
    named = [(v, k) for k, v in dow_mean.items() if len(by_dow[k]) >= MIN_WEEKDAY_SAMPLE]
    if len(named) < 3:
        return []
    named.sort()
    low_v, low_k = named[0]
    high_v, high_k = named[-1]
    if not high_v or _pct(low_v, high_v) > -12:
        return []
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return [{
        'kind': 'pattern',
        'title': '%ss run %.0f%% below %ss, every week'
                 % (days[low_k], abs(_pct(low_v, high_v)), days[high_k]),
        'body': ('This is a standing pattern across %d %ss in the range, not a one-off '
                 'bad day. A recurring %s offer is worth more than reacting to any '
                 'single date, because it applies every week.'
                 % (len(by_dow[low_k]), days[low_k], days[low_k])),
        'evidence': [('%s average' % days[low_k], low_v),
                     ('%s average' % days[high_k], high_v),
                     ('Gap per %s' % days[low_k], low_v - high_v)]}]


def _pairs_finding(db, f, w, a):
    """Products that already sell together -- the evidence base for a bundle."""
    # Narrow the window before the self-join, not after: on the full range this
    # query touches 1.8M line pairs and takes ten seconds.
    import datetime as _dt
    w2, a2 = w, list(a)
    span = (f.to - f.frm).days
    if span > PAIR_WINDOW_DAYS:
        a2[0] = f.to - _dt.timedelta(days=PAIR_WINDOW_DAYS)
    rows = db.q("""SELECT p1.name, p2.name, count(*) n
                     FROM transaction_item x
                     JOIN transaction_item y
                       ON y.transaction_id = x.transaction_id
                      AND y.product_id > x.product_id
                     JOIN "transaction" t ON t.id = x.transaction_id
                     JOIN product p1 ON p1.id = x.product_id
                     JOIN product p2 ON p2.id = y.product_id
                    WHERE""" + w + """
                    GROUP BY 1,2 HAVING count(*) >= 20
                    ORDER BY n DESC LIMIT 6""", a2)
    if not rows:
        return []
    return [{
        'kind': 'bundle',
        'title': 'Pairs that already sell together',
        'body': ('These combinations customers choose on their own. A bundle built on '
                 'one of these is priced against a habit that already exists, rather '
                 'than trying to create one.%s'
                 % ('' if span <= PAIR_WINDOW_DAYS else
                    ' Measured over the most recent %d days of the range.'
                    % PAIR_WINDOW_DAYS)),
        'head': ('Pair', 'Baskets holding both'),
        'evidence': [('%s + %s' % (p1, p2), '%s baskets' % '{:,}'.format(n))
                     for p1, p2, n in rows]}]


def _money(v):
    return '₱' + format(float(v or 0), ',.2f')


# ---------------------------------------------------------------------------
# Hours
# ---------------------------------------------------------------------------

#: Trading hours quieter than this share of the peak hour are worth questioning
#: as opening hours -- staff are paid the same whether anyone comes in or not.
QUIET_HOUR_SHARE = 0.25


def analyse_hours(db, f):
    """Where the day's shape creates a staffing or trading decision."""
    w, a = f.where()
    cov = db.one('SELECT count(*), count(*) FILTER (WHERE has_time) '
                 'FROM "transaction" t WHERE' + w, a)
    total, timed = cov or (0, 0)
    if not timed:
        return [{'kind': 'none',
                 'title': 'No timed rows in this range',
                 'body': 'Every row here carries a date and no clock, so there is no '
                         'hourly shape to read. The Alliance 2026 export records no '
                         'time at all; try a range inside May, June or September 2026, '
                         'or filter to the StoreHub estate.',
                 'evidence': []}]

    findings = []
    if timed < total:
        findings.append({
            'kind': 'warn',
            'title': 'Read on %s of %s rows' % (_fmt(timed), _fmt(total)),
            'body': ('The rest carry no time of day, so they cannot contribute to any '
                     'hourly figure. Everything below describes the %.0f%% that can be '
                     'placed in a day \u2014 it is not a sample of the whole, it is the '
                     'whole of what has a clock.' % (timed / total * 100)),
            'evidence': []})

    wt = w + ' AND t.has_time'
    hours = db.q('SELECT local_hour, count(*), sum(amount) FROM "transaction" t '
                 'WHERE' + wt + ' GROUP BY 1 ORDER BY 1', a)
    if not hours:
        return findings

    tot_txn = sum(h[1] for h in hours)
    tot_val = float(sum(h[2] for h in hours))
    peak = max(hours, key=lambda r: r[1])

    # How much of the day rides on its busiest three hours.
    top3 = sorted(hours, key=lambda r: -r[1])[:3]
    share = sum(h[1] for h in top3) / tot_txn * 100
    findings.append({
        'kind': 'pattern',
        'title': 'Three hours carry %.0f%% of the day' % share,
        'body': ('%s are when this business actually happens. Staffing, prep and any '
                 'offer meant to shift volume all have to be judged against these '
                 'hours \u2014 an offer that fills an already-full hour moves queue '
                 'length, not sales.'
                 % ', '.join('%02d:00' % h[0] for h in sorted(top3, key=lambda r: r[0]))),
        'head': ('Hour', 'Transactions and sales'),
        'evidence': [('%02d:00' % h[0], '%s txns \u00b7 %s' % (_fmt(h[1]), _money(h[2])))
                     for h in sorted(top3, key=lambda r: -r[1])]})

    # Hours open but barely trading.
    quiet = [h for h in hours if h[1] < peak[1] * QUIET_HOUR_SHARE]
    if quiet:
        q_txn = sum(h[1] for h in quiet)
        q_val = float(sum(h[2] for h in quiet))
        findings.append({
            'kind': 'action',
            'title': '%d hour(s) trade under %.0f%% of the peak'
                     % (len(quiet), QUIET_HOUR_SHARE * 100),
            'body': ('Together they take %s \u2014 %.1f%% of sales across %.0f%% of the '
                     'trading day. Staff cost the same in those hours as in the busy '
                     'ones. Either they are a mall-hours obligation, in which case they '
                     'are a fixed cost and worth staffing to the minimum, or they are '
                     'a genuine opportunity for an off-peak offer that does not '
                     'discount the peak.'
                     % (_money(q_val), q_val / tot_val * 100 if tot_val else 0,
                        len(quiet) / len(hours) * 100)),
            'head': ('Hour', 'Transactions and sales'),
            'evidence': [('%02d:00' % h[0], '%s txns \u00b7 %s' % (_fmt(h[1]), _money(h[2])))
                         for h in quiet[:8]]})

    # Lunch against dinner -- the two services a food business runs.
    lunch = [h for h in hours if 11 <= h[0] <= 14]
    dinner = [h for h in hours if 17 <= h[0] <= 20]
    if lunch and dinner:
        lv = float(sum(h[2] for h in lunch))
        dv = float(sum(h[2] for h in dinner))
        bigger, smaller = ('Lunch', 'dinner') if lv > dv else ('Dinner', 'lunch')
        findings.append({
            'kind': 'detail',
            'title': '%s outsells %s by %.0f%%'
                     % (bigger, smaller, abs(_pct(max(lv, dv), min(lv, dv)))),
            'body': ('Lunch is 11:00-14:00 and dinner 17:00-20:00. The weaker service '
                     'is where a time-boxed offer costs least, because it discounts '
                     'the hours you are not already filling.'),
            'evidence': [('Lunch 11-14', lv), ('Dinner 17-20', dv),
                         ('Rest of day', tot_val - lv - dv)]})

    # Cashier throughput. Hours worked are not recorded, so throughput is
    # measured per hour the cashier actually appears on a receipt -- which is
    # the only defensible denominator available.
    cash = db.q('SELECT cashier_name, count(*), '
                'count(DISTINCT (business_date, local_hour)) '
                'FROM "transaction" t WHERE' + wt +
                " AND cashier_name IS NOT NULL AND cashier_name <> ''"
                ' GROUP BY 1 HAVING count(DISTINCT (business_date, local_hour)) >= 20'
                ' ORDER BY 2 DESC LIMIT 200', a)
    rates = sorted(((c[1] / c[2], c[0], c[1], c[2]) for c in cash), reverse=True)
    if len(rates) >= 5:
        fast, slow = rates[0], rates[-1]
        med = _median([r[0] for r in rates])
        findings.append({
            'kind': 'detail',
            'title': 'Throughput ranges %.1f to %.1f transactions an hour'
                     % (slow[0], fast[0]),
            'body': ('Measured per hour each cashier actually appears on a receipt, '
                     'across %d cashiers with at least 20 such hours. This is a '
                     'throughput spread, not a performance ranking \u2014 a quiet '
                     'branch and a busy one are not comparable, and this range spans '
                     'both. Read it per branch before drawing any conclusion about a '
                     'person.' % len(rates)),
            'evidence': [('Median', '%.1f / hour' % med),
                         ('Highest \u2014 %s' % fast[1], '%.1f / hour' % fast[0]),
                         ('Lowest \u2014 %s' % slow[1], '%.1f / hour' % slow[0])]})

    # The action this tab's data most supports, with the prize attached.
    #
    # Sized against those hours' OWN current level, never against the peak. A
    # 06:00 hour doing 73 transactions will not reach 40% of a lunch peak doing
    # 16,071, and quoting that as the prize would put an eight-figure number on
    # a four-figure opportunity. Growth is expressed as a share of what the
    # hours already do, which is the only base that survives being questioned.
    if quiet:
        q_val = float(sum(h[2] for h in quiet))
        q_txn = sum(h[1] for h in quiet)
        hour_list = ', '.join('%02d:00' % h[0] for h in sorted(quiet, key=lambda r: r[0]))
        findings.append(_lever(
            'Do this first: an off-peak offer on the %d quietest hours' % len(quiet),
            'Those hours \u2014 %s \u2014 are already staffed and already open, so '
            'their cost is sunk and anything extra they take is close to pure '
            'contribution. They currently take %s across %s transactions. IF an offer '
            'time-boxed to them lifted that by a given share, this is what each share '
            'is worth over this range. The shares are multiplication on what those '
            'hours already do, not a forecast and not a claim that any particular '
            'offer works; pick the one you would defend and judge the offer against '
            'it. Keep it inside those hours so it never discounts the peak, which '
            'needs no help.' % (hour_list, _money(q_val), _fmt(q_txn)),
            [('Quiet hours take now', q_val),
             ('Share of all sales', '%.1f%%' % (q_val / tot_val * 100 if tot_val else 0)),
             ('If they grow 10%', q_val * 0.10),
             ('If they grow 25%', q_val * 0.25),
             ('If they grow 50%', q_val * 0.50)]))
    return findings


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def analyse_products(db, f):
    """Where the menu concentrates, where it drags, and what is sold alone."""
    w, a = f.where()
    n_lines = db.one('SELECT count(*) FROM transaction_item i '
                     'JOIN "transaction" t ON t.id = i.transaction_id WHERE' + w, a)[0]
    if not n_lines:
        return [{'kind': 'none',
                 'title': 'No line detail in this range',
                 'body': 'The Alliance sale-level export carries a total and a tender '
                         'and no products at all, so there is nothing to analyse here. '
                         'Filter to the StoreHub estate, or pick a range before '
                         'May 2026 where the Alliance feed was line-level.',
                 'evidence': []}]

    findings = []
    rows = db.q("""SELECT coalesce(p.name, 'id ' || i.product_source_id) nm,
                          sum(i.line_total) v, sum(i.quantity) q
                     FROM transaction_item i
                     JOIN "transaction" t ON t.id = i.transaction_id
                LEFT JOIN product p ON p.id = i.product_id
                    WHERE""" + w + ' GROUP BY 1 ORDER BY 2 DESC', a)
    total = float(sum(r[1] for r in rows)) or 1.0

    # Concentration -- how much of the business rests on a handful of lines.
    cum, n_for_half = 0.0, 0
    for i, r in enumerate(rows, 1):
        cum += float(r[1])
        if cum >= total / 2:
            n_for_half = i
            break
    top5 = sum(float(r[1]) for r in rows[:5]) / total * 100
    findings.append({
        'kind': 'pattern',
        'title': '%d product(s) make half of all sales' % n_for_half,
        'body': ('Out of %d selling in this range. The top five alone are %.0f%%. '
                 'That concentration cuts both ways: a promotion on any of them moves '
                 'the whole number, and a supply problem on any of them does the same '
                 'damage in the other direction.' % (len(rows), top5)),
        'head': ('Product', 'Sales in range'),
        'evidence': [(r[0], r[1]) for r in rows[:5]]})

    # The tail -- lines that occupy the menu without earning their place.
    tail = [r for r in rows if float(r[1]) / total < 0.001]
    zero = sum(1 for r in tail if float(r[1]) == 0)
    if len(tail) >= 5:
        tv = sum(float(r[1]) for r in tail)
        findings.append({
            'kind': 'action',
            'title': '%d line(s) together are %.1f%% of sales'
                     % (len(tail), tv / total * 100),
            'body': ('Each is under 0.1%% of sales on its own. They still take menu '
                     'space, prep time, stock and training.%s Worth asking of each '
                     'whether it is a deliberate anchor, a genuine niche, or simply '
                     'never removed \u2014 this cannot tell you which, only that the '
                     'question is worth asking.'
                     % ('' if not zero else
                        ' %d of them recorded sales of exactly zero, which usually '
                        'means a giveaway, a staff meal or a demo line rather than a '
                        'product anyone declined to buy.' % zero)),
            # Largest of the tail: the borderline cases, where the decision
            # actually is a decision. A list of zeroes proves only that zero
            # sorts first.
            'head': ('Line', 'Sales in range'),
            'evidence': [(r[0], r[1]) for r in tail[:6]]})

    # Single-item baskets: the clearest attachment opportunity there is.
    basket = db.one("""SELECT count(*) FILTER (WHERE c = 1), count(*), avg(amt)
                         FROM (SELECT t.id, count(*) c, max(t.amount) amt
                                 FROM "transaction" t
                                 JOIN transaction_item i ON i.transaction_id = t.id
                                WHERE""" + w + ' GROUP BY 1) x', a)
    if basket and basket[1]:
        solo, allb = basket[0], basket[1]
        if solo and solo / allb > 0.15:
            alone = db.q("""SELECT coalesce(p.name, 'id ' || i.product_source_id), count(*)
                              FROM (SELECT t.id FROM "transaction" t
                                      JOIN transaction_item i2 ON i2.transaction_id = t.id
                                     WHERE""" + w + """ GROUP BY 1 HAVING count(*) = 1) s
                              JOIN transaction_item i ON i.transaction_id = s.id
                         LEFT JOIN product p ON p.id = i.product_id
                             GROUP BY 1 ORDER BY 2 DESC LIMIT 6""", a)
            findings.append({
                'kind': 'action',
                'title': '%.0f%% of baskets hold a single item' % (solo / allb * 100),
                'body': ('%s baskets out of %s. These are the attachment opportunity in '
                         'its purest form \u2014 a customer already at the till, buying '
                         'one thing. The lines below are what they are buying alone '
                         'most often, so they are where a paired offer has the most '
                         'baskets to work on.' % (_fmt(solo), _fmt(allb))),
                'head': ('Product', 'Bought alone'),
                'evidence': [(r[0], '%s solo baskets' % _fmt(r[1])) for r in alone]})

    # Movement across the range, first half against second.
    mid = f.frm + dt.timedelta(days=(f.to - f.frm).days // 2)
    if (f.to - f.frm).days >= 13:
        mv = db.q("""SELECT coalesce(p.name, 'id ' || i.product_source_id) nm,
                            sum(i.quantity) FILTER (WHERE t.business_date < %s) a1,
                            sum(i.quantity) FILTER (WHERE t.business_date >= %s) a2
                       FROM transaction_item i
                       JOIN "transaction" t ON t.id = i.transaction_id
                  LEFT JOIN product p ON p.id = i.product_id
                      WHERE""" + w + ' GROUP BY 1', [mid, mid] + a)
        moves = []
        for nm, a1, a2 in mv:
            a1, a2 = float(a1 or 0), float(a2 or 0)
            if a1 >= 50:
                moves.append((_pct(a2, a1), nm, a1, a2))
        if moves:
            moves.sort()
            down, up = moves[:3], moves[-3:][::-1]
            findings.append({
                'kind': 'detail',
                'title': 'Mix is shifting within the range',
                'body': ('Units in the second half of the range against the first, for '
                         'lines selling at least 50 units in the first half. A falling '
                         'line is worth a reason before it is worth a promotion \u2014 '
                         'it may be seasonal, out of stock, or newly priced.'),
                'head': ('Product', 'Units, first half \u2192 second'),
                'evidence': ([('\u25bc %s' % m[1], '%.0f%% \u00b7 %s \u2192 %s units'
                               % (m[0], _fmt(m[2]), _fmt(m[3]))) for m in down] +
                             [('\u25b2 %s' % m[1], '+%.0f%% \u00b7 %s \u2192 %s units'
                               % (m[0], _fmt(m[2]), _fmt(m[3]))) for m in up
                              if m[0] > 0])})

    # The action this tab's data most supports.
    if basket and basket[1] and basket[0]:
        solo, allb = basket[0], basket[1]
        # Price the attachment off what a cheap second line actually costs here,
        # not off an invented figure: the median line value in this range.
        line_vals = sorted(float(r[1]) / float(r[2]) for r in rows
                           if r[2] and float(r[2]) > 0)
        typical = _median(line_vals) if line_vals else 0.0
        gains = [(rate, solo * rate * typical) for rate in (0.05, 0.10, 0.20)]
        findings.append(_lever(
            'Do this first: attach a second item to single-item baskets',
            'There are %s baskets in this range holding exactly one thing \u2014 '
            '%.0f%% of all of them. Those customers are already at the till, already '
            'paying, already decided; no footfall spend is needed to reach them. A '
            'typical second line here is worth %s. IF a till prompt or a paired price '
            'converted some share of them, this is what each share is worth over this '
            'range. Pick the share you think is realistic and judge the offer against '
            'that \u2014 the figures are multiplication, not prediction. The lines '
            'listed above as most often bought alone are where to aim it.'
            % (_fmt(solo), solo / allb * 100, _money(typical)),
            [('Single-item baskets', '%s of %s' % (_fmt(solo), _fmt(allb))),
             ('Typical second line', typical)] +
            [('If %.0f%% attach' % (r * 100), g) for r, g in gains]))
    return findings


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

#: A branch this many times the group's median discount rate is worth a look.
#: Not an accusation -- a legitimate mall promotion looks exactly like this.
OUTLIER_MULTIPLE = 2.0


def analyse_exceptions(db, f):
    """Where control exceptions concentrate -- and what they are not evidence of."""
    w, a = f.where()
    findings = []

    disc = db.q('SELECT s.name, sum(t.discount_amount), sum(t.amount), count(*) '
                'FROM "transaction" t JOIN store s ON s.id = t.store_id '
                'WHERE' + w + ' AND t.discount_amount IS NOT NULL '
                'GROUP BY 1 HAVING sum(t.amount) > 0 ORDER BY 2 DESC', a)
    rates = [(float(d) / float(v) * 100, n, float(d), float(v))
             for n, d, v, _ in disc if v and float(v) > 0]
    if len(rates) >= 5:
        med = _median([r[0] for r in rates])
        out = [r for r in rates if med > 0 and r[0] > med * OUTLIER_MULTIPLE]
        out.sort(reverse=True)
        if out:
            findings.append({
                'kind': 'gap',
                'title': '%d branch(es) discount at over %.1f%% of sales'
                         % (len(out), med * OUTLIER_MULTIPLE),
                'body': ('The group median is %.2f%%. A branch well above it is worth '
                         'asking about, not accusing: a local mall promotion, a heavy '
                         'senior-citizen mix or a staff discount policy all look '
                         'exactly like this from here. What this can tell you is where '
                         'to ask first.' % med),
                'head': ('Branch', 'Discount rate and amount'),
                'evidence': [(r[1], '%.2f%% \u00b7 %s' % (r[0], _money(r[2])))
                             for r in out[:6]]})
        else:
            findings.append({
                'kind': 'good',
                'title': 'Discounting is consistent across branches',
                'body': ('Group median %.2f%% of sales, and no branch runs at more than '
                         '%.0fx that. There is no outlier here worth investigating.'
                         % (med, OUTLIER_MULTIPLE)),
                'evidence': [('Median rate', '%.2f%%' % med),
                             ('Highest', '%.2f%% \u2014 %s' % (rates and max(rates)[0] or 0,
                                                               max(rates)[1] if rates else '')),
                             ('Branches compared', len(rates))]})

    flagged = db.q('SELECT s.name, count(*), sum(t.amount) FROM "transaction" t '
                   'JOIN store s ON s.id = t.store_id WHERE' + w +
                   ' AND t.is_flagged GROUP BY 1 ORDER BY 2 DESC LIMIT 6', a)
    if flagged:
        findings.append({
            'kind': 'action',
            'title': 'Flags are concentrated in %d branch(es)' % len(flagged),
            'body': ('Rows the source system marked for review. A cluster in one branch '
                     'usually means one recurring cause \u2014 a till procedure, a '
                     'training gap, a device \u2014 rather than many separate ones, so '
                     'it is normally one fix.'),
            'head': ('Branch', 'Flagged rows and value'),
            'evidence': [(r[0], '%s flagged \u00b7 %s' % (_fmt(r[1]), _money(r[2])))
                         for r in flagged]})

    # The receipt register holds the Alliance estate only. With the page filtered
    # to StoreHub it must contribute nothing, or the panel names Alliance branches
    # under a StoreHub heading.
    if f.estate == 'STOREHUB':
        if not findings:
            findings.append({
                'kind': 'good',
                'title': 'Nothing stands out in the StoreHub estate',
                'body': 'No discount outlier and no flagged cluster in the branches '
                        'and dates selected. The statutory relief and void analysis '
                        'needs the receipt register, which covers the Alliance estate '
                        'only \u2014 clear the estate filter to see it.',
                'evidence': []})
        return findings

    rw = ' r.business_date BETWEEN %s AND %s'
    rargs = [f.frm, f.to]
    if f.stores:
        rw += ' AND r.store_id = ANY(%s)'
        rargs.append(f.stores)
    rel = db.q("""SELECT s.name, sum(r.senior + r.pwd + r.solo_parent + r.nac),
                         sum(r.total_gross), sum(r.void_amount),
                         count(*) FILTER (WHERE NOT r.posted), count(*)
                    FROM receipt r JOIN store s ON s.id = r.store_id
                   WHERE""" + rw + ' GROUP BY 1 HAVING sum(r.total_gross) > 0', rargs)
    if len(rel) >= 5:
        rr = [(float(x[1]) / float(x[2]) * 100, x[0], float(x[1])) for x in rel]
        med = _median([r[0] for r in rr])
        out = sorted((r for r in rr if med > 0 and r[0] > med * OUTLIER_MULTIPLE),
                     reverse=True)
        if out:
            findings.append({
                'kind': 'gap',
                'title': 'Statutory relief runs high at %d branch(es)' % len(out),
                'body': ('Senior, PWD, solo-parent and NAC relief as a share of gross. '
                         'The median branch is %.2f%%. A branch far above it may simply '
                         'serve an older catchment \u2014 but statutory relief is also '
                         'the discount most open to misuse, and it is the one BIR will '
                         'ask about. Worth reconciling against ID logs before anything '
                         'else is concluded.' % med),
                'head': ('Branch', 'Relief rate and amount'),
                'evidence': [(r[1], '%.2f%% \u00b7 %s' % (r[0], _money(r[2])))
                             for r in out[:6]]})
        unposted = sum(x[4] for x in rel)
        if unposted:
            findings.append({
                'kind': 'warn',
                'title': '%s receipt(s) are not posted' % _fmt(unposted),
                'body': ('Out of %s in range. An unposted receipt is recorded but not '
                         'yet committed, so it may or may not be in the sales figures '
                         'the rest of this dashboard shows. Worth clearing before any '
                         'period is closed on these numbers.'
                         % _fmt(sum(x[5] for x in rel))),
                'evidence': [(x[0], '%s unposted' % _fmt(x[4]))
                             for x in sorted(rel, key=lambda y: -y[4])[:5] if x[4]]})

    # The action this tab's data most supports: what the outliers are worth.
    if len(rates) >= 5:
        med = _median([r[0] for r in rates])
        excess = sum(r[3] * (r[0] - med) / 100 for r in rates if r[0] > med)
        if excess > 0:
            findings.append(_lever(
                'Do this first: ask the %d branch(es) above the median why'
                % sum(1 for r in rates if r[0] > med),
                'Every branch discounting above the group median of %.2f%% accounts '
                'for %s of discount in this range beyond what the median rate would '
                'have given. That is NOT %s of savings waiting to be taken \u2014 much '
                'of it will be legitimate, and some branches genuinely face a '
                'different catchment or a mall-mandated promotion. It is the size of '
                'the question. A gap this large justifies asking; a gap of a few '
                'thousand would not.'
                % (med, _money(excess), _money(excess)),
                [('Excess over median rate', excess),
                 ('Group median rate', '%.2f%%' % med),
                 ('Branches above it', sum(1 for r in rates if r[0] > med)),
                 ('Highest', '%.2f%% \u2014 %s' % (max(rates)[0], max(rates)[1]))]))

    if not findings:
        findings.append({
            'kind': 'good',
            'title': 'Nothing stands out in this range',
            'body': 'No discount outlier, no flagged cluster and no unposted backlog '
                    'in the branches and dates selected.',
            'evidence': []})
    return findings


def _lever(title, body, evidence):
    """The recommended action for a tab, sized and clearly labelled as such."""
    return {'kind': 'lever', 'title': title, 'body': body, 'evidence': evidence}


def _fmt(n):
    return '{:,}'.format(int(n or 0))


VIEWS = {'overview': analyse, 'hours': analyse_hours,
         'products': analyse_products, 'exceptions': analyse_exceptions}
