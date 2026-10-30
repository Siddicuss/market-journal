"""Performance statistics over closed trades. All times are local."""
from collections import defaultdict
from datetime import date, datetime, timedelta

WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def local(ts):
    return datetime.fromtimestamp(ts)


def day_key(ts):
    return local(ts).strftime('%Y-%m-%d')


def week_start(day):
    """Monday of the week containing `day` (date or 'YYYY-MM-DD')."""
    if isinstance(day, str):
        day = date.fromisoformat(day)
    return day - timedelta(days=day.weekday())


def closed(trades):
    return [t for t in trades if t['status'] == 'closed' and t['close_ts']]


def summary(trades):
    trades = sorted(closed(trades), key=lambda t: t['close_ts'])
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] < 0]
    gross_win = sum(t['pnl'] for t in wins)
    gross_loss = -sum(t['pnl'] for t in losses)
    contracts = sum(t['contracts'] for t in trades)
    net = sum(t['pnl'] for t in trades)
    equity = peak = drawdown = 0.0
    for t in trades:
        equity += t['pnl']
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    streak = longest_win = longest_loss = run = 0
    for t in trades:
        if t['pnl'] > 0:
            run = run + 1 if run > 0 else 1
        elif t['pnl'] < 0:
            run = run - 1 if run < 0 else -1
        else:
            run = 0
        longest_win, longest_loss = max(longest_win, run), max(longest_loss, -run)
    streak = run
    holds = [t['hold_seconds'] for t in trades if t['hold_seconds'] is not None]
    count = len(trades)
    return {
        'trades': count, 'wins': len(wins), 'losses': len(losses),
        'scratches': count - len(wins) - len(losses),
        'win_rate': len(wins) / count * 100 if count else None,
        'net_pnl': net, 'gross_pnl': sum(t['pnl_gross'] for t in trades), 'fees': sum(t['fees'] for t in trades),
        'avg_win': gross_win / len(wins) if wins else None,
        'avg_loss': -gross_loss / len(losses) if losses else None,
        'profit_factor': gross_win / gross_loss if gross_loss else None,
        'expectancy': net / count if count else None,
        'per_contract': net / contracts if contracts else None,
        'contracts': contracts, 'volume': sum(t['cost'] for t in trades),
        'largest_win': max((t['pnl'] for t in wins), default=None),
        'largest_loss': min((t['pnl'] for t in losses), default=None),
        'max_drawdown': drawdown, 'streak': streak,
        'longest_win_streak': longest_win, 'longest_loss_streak': longest_loss,
        'avg_hold': sum(holds) / len(holds) if holds else None,
    }


def equity_curve(trades):
    points, equity, peak = [], 0.0, 0.0
    for t in sorted(closed(trades), key=lambda t: t['close_ts']):
        equity += t['pnl']
        peak = max(peak, equity)
        points.append({'t': t['close_ts'], 'equity': round(equity, 6), 'drawdown': round(equity - peak, 6), 'id': t['id']})
    return points


def by_day(trades):
    days = defaultdict(lambda: {'pnl': 0.0, 'trades': 0, 'wins': 0, 'fees': 0.0})
    for t in closed(trades):
        d = days[day_key(t['close_ts'])]
        d['pnl'] += t['pnl']
        d['trades'] += 1
        d['wins'] += t['pnl'] > 0
        d['fees'] += t['fees']
    return dict(days)


def _bucket_price(t):
    cents = int(t['entry_price'] * 100)
    low = min(90, cents // 10 * 10)
    return f'{low}-{low + 10}¢', low


def _bucket_minutes(t):
    m = t.get('minutes_left')
    if m is None:
        return 'Unknown', 9999
    for limit, label in ((1, '< 1 min'), (3, '1-3 min'), (5, '3-5 min'), (10, '5-10 min'), (15, '10-15 min'),
                         (60, '15-60 min'), (240, '1-4 h'), (1440, '4-24 h')):
        if m < limit:
            return label, limit
    return '> 1 day', 99999


def _bucket_hold(t):
    s = t.get('hold_seconds')
    if s is None:
        return 'Open', 99999999
    for limit, label in ((60, '< 1 min'), (300, '1-5 min'), (900, '5-15 min'), (3600, '15-60 min'),
                         (86400, '1-24 h')):
        if s < limit:
            return label, limit
    return '> 1 day', 9999999


def _size_bucket(t):
    c = t['contracts']
    if c < 2:
        return '1 contract', 2
    for limit, label in ((6, '2-5'), (11, '6-10'), (26, '11-25'), (101, '26-100')):
        if c < limit:
            return label + ' contracts', limit
    return '100+ contracts', 99999


PLATFORM_NAMES = {'kalshi': 'Kalshi', 'polymarket': 'Polymarket'}

GROUPS = {
    'platform': lambda t: [(PLATFORM_NAMES.get(t.get('platform') or 'kalshi', 'Kalshi'), t.get('platform') or 'kalshi')],
    'series': lambda t: [(t.get('series_name') or t['series'], (t.get('series_name') or t['series']).lower())],
    'side': lambda t: [(t['side'].upper(), t['side'])] if t['side'] in ('yes', 'no') else [('Other outcome', '~')],
    'tag': lambda t: [(x['name'], x['name'].lower()) for x in t['tags'] if x['kind'] == 'tag'] or [('(no tag)', '~')],
    'mistake': lambda t: [(x['name'], x['name'].lower()) for x in t['tags'] if x['kind'] == 'mistake'] or [('(no mistake)', '~')],
    'hour': lambda t: [(local(t['open_ts']).strftime('%I %p').lstrip('0'), local(t['open_ts']).hour)],
    'weekday': lambda t: [(WEEKDAYS[local(t['open_ts']).weekday()], local(t['open_ts']).weekday())],
    'entry_price': lambda t: [_bucket_price(t)],
    'minutes_left': lambda t: [_bucket_minutes(t)],
    'hold': lambda t: [_bucket_hold(t)],
    'size': lambda t: [_size_bucket(t)],
    'exit': lambda t: [('Held to settlement', 0) if t['close_reason'] == 'settled' else ('Sold before close', 1)],
    'liquidity': lambda t: [('Unknown', 3)] if not t['maker_fills'] and not t['taker_fills'] else [('Taker only', 0) if not t['maker_fills'] else ('Maker only', 1) if not t['taker_fills'] else ('Mixed', 2)],
}


def breakdown(trades, group):
    keyfn = GROUPS[group]
    buckets, order = defaultdict(list), {}
    for t in closed(trades):
        for label, sort in keyfn(t):
            buckets[label].append(t)
            order[label] = sort
    rows = []
    for label, items in buckets.items():
        s = summary(items)
        rows.append({'label': label, 'sort': order[label], **{k: s[k] for k in (
            'trades', 'win_rate', 'net_pnl', 'gross_pnl', 'fees', 'expectancy', 'per_contract', 'profit_factor',
            'avg_win', 'avg_loss', 'contracts')}})
    rows.sort(key=lambda r: (isinstance(r['sort'], str), r['sort']))
    return rows


def calibration(trades):
    """Entry price vs how often that side actually won the market."""
    buckets = defaultdict(list)
    for t in trades:
        if t.get('side_won') is None:
            continue
        low = min(90, int(t['entry_price'] * 100) // 10 * 10)
        buckets[low].append(t)
    rows = []
    for low in sorted(buckets):
        items = buckets[low]
        weight = sum(t['contracts'] for t in items)
        implied = sum(t['entry_price'] * t['contracts'] for t in items) / weight * 100
        actual = sum(t['contracts'] for t in items if t['side_won']) / weight * 100
        rows.append({'label': f'{low}-{low + 10}¢', 'trades': len(items), 'implied': implied, 'actual': actual,
                     'edge': actual - implied})
    return rows


def filter_trades(trades, q):
    """Filter by query dict: start, end, platform, series, side, outcome, status, tag, search.

    Dates match the close day (the open day for open trades), like the calendar and weekly review.
    """
    out = []
    start, end = q.get('start'), q.get('end')
    tag = q.get('tag')
    search = (q.get('search') or '').lower().strip()
    for t in trades:
        day = day_key(t['close_ts'] or t['open_ts'])
        if start and day < start:
            continue
        if end and day > end:
            continue
        if q.get('platform') and (t.get('platform') or 'kalshi') != q['platform']:
            continue
        if q.get('series') and t['series'] != q['series']:
            continue
        if q.get('side') and t['side'] != q['side']:
            continue
        if q.get('status') and t['status'] != q['status']:
            continue
        outcome = q.get('outcome')
        if outcome == 'win' and not (t['status'] == 'closed' and t['pnl'] > 0):
            continue
        if outcome == 'loss' and not (t['status'] == 'closed' and t['pnl'] < 0):
            continue
        if tag and not any(str(x['id']) == str(tag) for x in t['tags']):
            continue
        haystack = ' '.join([t['ticker'], t['title'], t.get('note') or ''] + [x['name'] for x in t['tags']]).lower()
        if search and search not in haystack:
            continue
        out.append(t)
    return out


def calendar_month(trades, year, month, notes):
    days = by_day(trades)
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    cells = []
    d = first
    while d < nxt:
        key = d.isoformat()
        info = days.get(key)
        cells.append({'day': key, 'pnl': info['pnl'] if info else None, 'trades': info['trades'] if info else 0,
                      'wins': info['wins'] if info else 0, 'has_note': key in notes})
        d += timedelta(days=1)
    month_trades = [t for t in closed(trades) if day_key(t['close_ts'])[:7] == first.strftime('%Y-%m')]
    weeks = defaultdict(float)
    for t in month_trades:
        weeks[week_start(day_key(t['close_ts'])).isoformat()] += t['pnl']
    return {'year': year, 'month': month, 'first_weekday': first.weekday(), 'days': cells,
            'summary': summary(month_trades), 'weeks': dict(weeks)}


def week_report(trades, week, review):
    start = week_start(week)
    end = start + timedelta(days=7)
    prev_start = start - timedelta(days=7)

    def in_range(t, a, b):
        return a.isoformat() <= day_key(t['close_ts']) < b.isoformat()

    done = closed(trades)
    this = [t for t in done if in_range(t, start, end)]
    prev = [t for t in done if in_range(t, prev_start, start)]
    daily = by_day(this)
    days = []
    for i in range(7):
        d = (start + timedelta(days=i)).isoformat()
        info = daily.get(d) or {'pnl': 0.0, 'trades': 0, 'wins': 0}
        days.append({'day': d, 'label': WEEKDAYS[i], **info})
    ranked = sorted(this, key=lambda t: t['pnl'])
    tag_counts = defaultdict(lambda: {'count': 0, 'pnl': 0.0})
    for t in this:
        for x in t['tags']:
            c = tag_counts[(x['kind'], x['name'])]
            c['count'] += 1
            c['pnl'] += t['pnl']
    tags = [{'kind': k, 'name': n, **v} for (k, n), v in tag_counts.items()]
    tags.sort(key=lambda x: -x['count'])
    slim = lambda t: {k: t.get(k) for k in ('id', 'platform', 'ticker', 'title', 'series_name', 'side', 'pnl', 'contracts', 'entry_price', 'close_ts')}
    return {
        'week': start.isoformat(), 'end': (end - timedelta(days=1)).isoformat(),
        'summary': summary(this), 'previous': summary(prev), 'days': days,
        'best': [slim(t) for t in reversed(ranked[-3:]) if t['pnl'] > 0],
        'worst': [slim(t) for t in ranked[:3] if t['pnl'] < 0],
        'tags': [x for x in tags if x['kind'] == 'tag'][:8],
        'mistakes': [x for x in tags if x['kind'] == 'mistake'][:8],
        'review': review,
    }


def week_list(trades, reviews):
    weeks = defaultdict(list)
    for t in closed(trades):
        weeks[week_start(day_key(t['close_ts'])).isoformat()].append(t)
    for w in reviews:
        weeks.setdefault(w, [])
    rows = []
    for w in sorted(weeks, reverse=True):
        s = summary(weeks[w])
        r = reviews.get(w) or {}
        rows.append({'week': w, 'trades': s['trades'], 'net_pnl': s['net_pnl'], 'win_rate': s['win_rate'],
                     'reviewed': bool((r.get('worked') or '') + (r.get('didnt') or '') + (r.get('focus') or ''))})
    return rows
