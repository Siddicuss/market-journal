"""Pull fills, settlements and market details from Kalshi into the store."""
import time
from datetime import datetime

import polymarket
from kalshi_client import KalshiError
from trades import build_trades, normalize_fill

OVERLAP = 86400  # re-read the last day each sync so nothing is missed


def _ts(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def normalize_market(raw):
    ticker = raw.get('ticker')
    value = raw.get('settlement_value_dollars')
    return {
        'ticker': ticker, 'title': raw.get('title') or ticker,
        'subtitle': raw.get('yes_sub_title') or raw.get('subtitle') or '',
        'series': (raw.get('event_ticker') or ticker or '').split('-')[0],
        'event_ticker': raw.get('event_ticker'),
        'open_ts': _ts(raw.get('open_time')), 'close_ts': _ts(raw.get('close_time')),
        'settled_ts': _ts(raw.get('settlement_ts')),
        'result': raw.get('result') or None,
        'value': float(value) * 100 if value not in (None, '') else None,
        'status': raw.get('status'), 'raw': raw,
    }


def normalize_settlement(raw):
    return {
        'ticker': raw.get('ticker'), 'result': raw.get('market_result') or None,
        'settled_ts': _ts(raw.get('settled_time')),
        'fee': float(raw.get('fee_cost') or 0), 'revenue': float(raw.get('revenue') or 0) / 100,
        'value': raw.get('value'), 'raw': raw,
    }


def rebuild(store, polymarket_trades=True):
    trades = build_trades(store.fills(), store.markets(), store.settlements())
    if polymarket_trades:
        trades += polymarket.build_trades(store.pm_activity(), store.pm_markets())
    trades.sort(key=lambda t: t['open_ts'] or 0)
    store.replace_trades(trades)
    return trades


def sync(client, store, progress=lambda message: None):
    started = time.time()
    first = not store.get_meta('synced_once', False)
    latest = store.latest_fill_ts()
    since = None if first or latest is None else int(latest - OVERLAP)

    progress('Checking Kalshi archive cutoff')
    try:
        cutoff = _ts(client.get('/historical/cutoff').get('trades_created_ts'))
    except KalshiError as exc:
        if exc.status in (0, 401):
            raise
        cutoff = None

    raws = []
    if first or (cutoff and latest and latest < cutoff):
        progress('Downloading archived fills')
        try:
            raws += client.pages('/historical/fills', 'fills', min_ts=since, limit=100)
        except KalshiError as exc:
            if exc.status in (0, 401):
                raise
    progress('Downloading recent fills')
    raws += client.fills(min_ts=since)
    fills = [normalize_fill(r) for r in raws]
    keep = [(f, r) for f, r in zip(fills, raws) if f['ticker'] and f['ts']]
    if keep:
        store.upsert_fills([f for f, _ in keep], [r for _, r in keep])

    progress('Downloading settlements')
    settle_since = store.get_meta('settlements_since')
    settlements = [normalize_settlement(r) for r in client.settlements(min_ts=settle_since)]
    store.upsert_settlements([s for s in settlements if s['ticker']])
    store.set_meta('settlements_since', int(started - OVERLAP))

    known = store.markets()
    tickers = sorted({f['ticker'] for f in store.fills()})
    needed = [t for t in tickers if t not in known or not known[t].get('result')]
    progress(f'Looking up {len(needed)} markets' if needed else 'Markets up to date')
    found = []
    for i in range(0, len(needed), 20):
        chunk = needed[i:i + 20]
        try:
            found += [normalize_market(m) for m in client.markets(chunk)]
        except KalshiError as exc:
            if exc.status in (0, 401):
                raise
    missing = set(needed) - {m['ticker'] for m in found}
    for ticker in sorted(missing):
        for historical in (True, False):
            try:
                market = client.market(ticker, historical=historical)
            except KalshiError as exc:
                if exc.status in (0, 401):
                    raise
                continue
            if market:
                found.append(normalize_market(market))
                break
    if found:
        store.upsert_markets(found)

    known_series = store.series()
    for series in sorted({m['series'] for m in store.markets().values() if m['series']}):
        if (known_series.get(series) or {}).get('title'):
            continue
        try:
            info = client.series(series)
        except KalshiError as exc:
            if exc.status in (0, 401):
                raise
            continue
        if info:
            store.upsert_series(series, info.get('title') or '', info.get('category') or '')

    progress('Building trades')
    trades = rebuild(store)
    try:
        balance = client.balance()
        store.set_meta('balance', {'cash': (balance.get('balance') or 0) / 100,
                                   'portfolio': (balance.get('portfolio_value') or 0) / 100})
    except KalshiError as exc:
        if exc.status in (0, 401):
            raise
    store.set_meta('synced_once', True)
    store.set_meta('last_sync', time.time())
    return {'new_fills': len(keep), 'trades': len(trades), 'seconds': round(time.time() - started, 1)}


def candles_for(client, store, trade, market):
    """Minute candles for the trade's market, cached once the market has closed."""
    ticker = trade['ticker']
    fetched, data = store.candles(ticker)
    closed = market and market.get('result')
    if data is not None and (closed or (fetched and time.time() - fetched < 60)):
        return data
    start = (market or {}).get('open_ts') or (trade['open_ts'] - 3600)
    end = (market or {}).get('close_ts') or (trade['close_ts'] or time.time())
    end = min(end, time.time())
    # Long markets: keep the window around the trade so the request stays small.
    if end - start > 3 * 86400:
        start = max(start, trade['open_ts'] - 86400)
        end = min(end, (trade['close_ts'] or time.time()) + 86400)
    series = (market or {}).get('series') or ticker.split('-')[0]
    rows = []
    for historical in (False, True):
        try:
            rows = client.candles(series, ticker, start, end, historical=historical)
        except KalshiError as exc:
            if exc.status in (0, 401):
                raise
            rows = []
        if rows:
            break
    points = []
    for c in rows:
        def close(part):
            v = c.get(part) or {}
            value = v.get('close_dollars', v.get('close'))
            return float(value) if value not in (None, '') else None
        bid, ask, last = close('yes_bid'), close('yes_ask'), close('price')
        mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else None
        points.append({'t': c.get('end_period_ts'), 'bid': bid, 'ask': ask, 'last': last, 'mid': mid})
    store.save_candles(ticker, points)
    return points
