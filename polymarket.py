"""Turn a Polymarket wallet's public activity into journal trades.

Polymarket has one token per outcome, and a position is always long a token.
Each token is tracked on its own: a trade opens on the first buy (or split),
closes when sells (or merges) bring it back to zero, or settles when the market
resolves. Amounts use the USDC that actually moved, so P&L is after fees.
"""
import json
import time
from datetime import datetime
from decimal import Decimal as D

from polymarket_client import PolymarketError

ZERO = D(0)
DUST = D('0.000001')
OVERLAP = 86400
ORDER = {'SPLIT': 0, 'BUY': 1, 'SELL': 2, 'MERGE': 3, 'REDEEM': 4}


def _ts(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(' ', 'T').replace('Z', '+00:00')
    if text[-3] in '+-' and text[-3:].lstrip('+-').isdigit():
        text += ':00'  # "+00" -> "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _list(value):
    if isinstance(value, list):
        return value
    try:
        return json.loads(value or '[]')
    except ValueError:
        return []


def normalize_activity(raw):
    """A v2 /activity row reduced to what the journal needs."""
    return {
        'ts': float(raw.get('timestamp') or 0), 'type': raw.get('type') or '',
        'condition_id': raw.get('condition_id') or '', 'asset': raw.get('token_id') or '',
        'side': raw.get('side') or '', 'size': float(raw.get('size') or 0),
        'usdc': float(raw.get('usdc_size') or 0), 'price': float(raw.get('price') or 0),
        'outcome': raw.get('outcome') or '', 'slug': raw.get('slug') or '', 'title': raw.get('title') or '',
        'event_slug': raw.get('event_slug') or '', 'tx': raw.get('transaction_hash') or '',
    }


def normalize_market(raw):
    outcomes, prices, tokens = _list(raw.get('outcomes')), _list(raw.get('outcomePrices')), _list(raw.get('clobTokenIds'))
    event = (raw.get('events') or [{}])[0]
    series = (event.get('series') or [{}])[0]
    values = [float(p) for p in prices if p not in (None, '')]
    resolved = bool(raw.get('closed')) and len(values) == len(outcomes) and all(v in (0.0, 1.0) for v in values)
    return {
        'condition_id': raw.get('conditionId'), 'slug': raw.get('slug') or '', 'question': raw.get('question') or '',
        'outcomes': outcomes, 'prices': values, 'tokens': [str(t) for t in tokens],
        'closed': bool(raw.get('closed')), 'resolved': resolved,
        'winner': outcomes[values.index(1.0)] if resolved and 1.0 in values else None,
        'start_ts': _ts(raw.get('startDate')), 'end_ts': _ts(raw.get('endDate')),
        'closed_ts': _ts(raw.get('closedTime')),
        'series_slug': series.get('slug') or event.get('slug') or raw.get('slug') or '',
        'series_title': series.get('title') or event.get('title') or raw.get('question') or '',
    }


def side_label(outcome):
    return outcome.lower() if outcome.lower() in ('yes', 'no') else outcome


class _Trade:
    def __init__(self, row, asset, outcome):
        self.id = f"pm:{row['tx'] or int(row['ts'])}:{asset[-10:]}"
        self.asset, self.outcome = asset, outcome
        self.open_ts, self.close_ts, self.close_reason = row['ts'], None, None
        self.bought = self.cost = self.sold = self.proceeds = self.settled = self.payout = self.fees = ZERO
        self.position = self.peak = ZERO
        self.fills = []

    def add(self, row, action, qty, cash, price, fee):
        if action in ('buy', 'split'):
            self.bought += qty
            self.cost += cash
            self.position += qty
        else:
            self.sold += qty
            self.proceeds += cash
            self.position -= qty
        self.fees += fee
        self.peak = max(self.peak, self.position)
        self.fills.append({'fill_id': f"{row['tx']}:{len(self.fills)}", 'ts': row['ts'], 'action': 'buy' if action in ('buy', 'split') else 'sell',
                           'kind': action, 'side': side_label(self.outcome), 'qty': float(qty), 'price': float(price),
                           'fee': float(fee), 'is_taker': None})

    def result(self, first, market):
        pnl = self.proceeds + self.payout - self.cost
        exit_qty = self.sold + self.settled
        end = market.get('end_ts')
        side = side_label(self.outcome)
        winner = market.get('winner')
        return {
            'id': self.id, 'platform': 'polymarket', 'ticker': first['slug'] or market.get('slug') or first['condition_id'],
            'subaccount': 0, 'side': side, 'token_id': self.asset, 'condition_id': first['condition_id'],
            'status': 'closed' if self.close_reason else 'open', 'close_reason': self.close_reason,
            'open_ts': self.open_ts, 'close_ts': self.close_ts,
            'contracts': float(self.bought), 'peak': float(self.peak),
            'entry_price': float(round(self.cost / self.bought, 6)) if self.bought else 0.0,
            'exit_price': float(round((self.proceeds + self.payout) / exit_qty, 6)) if exit_qty else None,
            'cost': float(self.cost), 'proceeds': float(self.proceeds), 'payout': float(self.payout),
            'sold_qty': float(self.sold), 'settled_qty': float(self.settled),
            'fees': float(self.fees), 'pnl_gross': float(pnl + self.fees), 'pnl': float(pnl),
            'return_pct': float(pnl / self.cost * 100) if self.cost else 0.0,
            'hold_seconds': (self.close_ts - self.open_ts) if self.close_ts else None,
            'minutes_left': (end - self.open_ts) / 60 if end else None,
            'series': 'pm:' + (market.get('series_slug') or first['event_slug'] or first['slug']),
            'title': first['title'] or market.get('question') or first['slug'],
            'market_result': winner.lower() if winner and side in ('yes', 'no') else winner,
            'side_won': (winner == self.outcome) if winner else None,
            'taker_fills': 0, 'maker_fills': 0, 'fills': self.fills,
        }


def build_trades(rows, markets=None):
    """rows: normalized activity; markets: {condition_id: normalize_market(...)}."""
    markets = markets or {}
    by_condition = {}
    for row in rows:
        if row['condition_id'] and row['type'] in ('TRADE', 'SPLIT', 'MERGE', 'REDEEM'):
            by_condition.setdefault(row['condition_id'], []).append(row)
    out = []
    for condition, items in by_condition.items():
        market = markets.get(condition) or {}
        outcomes = dict(zip(market.get('tokens') or [], market.get('outcomes') or []))
        for r in items:
            if r['asset'] and r['outcome']:
                outcomes.setdefault(r['asset'], r['outcome'])
        items.sort(key=lambda r: (r['ts'], ORDER.get(r['side'] if r['type'] == 'TRADE' else r['type'], 9)))
        first = next((r for r in items if r['type'] == 'TRADE'), items[0])
        open_trades, done = {}, []

        def close(trade, ts, reason):
            trade.close_ts, trade.close_reason = ts, reason
            done.append(trade)
            open_trades.pop(trade.asset, None)

        def settle(ts):
            payouts = dict(zip(market.get('outcomes') or [], market.get('prices') or []))
            for trade in list(open_trades.values()):
                value = D(str(payouts.get(trade.outcome, 0)))
                trade.settled, trade.payout = trade.position, trade.position * value
                trade.position = ZERO
                close(trade, ts, 'settled')

        for r in items:
            size, cash = D(str(r['size'])), D(str(r['usdc']))
            if r['type'] == 'TRADE' and r['asset'] and size > 0:
                trade = open_trades.get(r['asset'])
                price = D(str(r['price']))
                if r['side'] == 'BUY':
                    if trade is None:
                        trade = open_trades[r['asset']] = _Trade(r, r['asset'], outcomes.get(r['asset'], r['outcome']))
                    trade.add(r, 'buy', size, cash, price, max(ZERO, cash - size * price))
                elif r['side'] == 'SELL' and trade is not None:
                    qty = min(size, trade.position)  # shares from rewards or conversions aren't tracked
                    share = qty / size
                    trade.add(r, 'sell', qty, cash * share, price, max(ZERO, size * price - cash) * share)
                    if trade.position <= DUST:
                        close(trade, r['ts'], 'sold')
            elif r['type'] == 'SPLIT' and size > 0 and len(market.get('tokens') or []) == 2:
                for token in market['tokens']:
                    trade = open_trades.get(token)
                    if trade is None:
                        trade = open_trades[token] = _Trade(r, token, outcomes.get(token, ''))
                    trade.add(r, 'split', size, cash / 2, D('0.5'), ZERO)
            elif r['type'] == 'MERGE' and size > 0:
                for trade in list(open_trades.values()):
                    qty = min(size, trade.position)
                    trade.add(r, 'merge', qty, cash / 2 * qty / size, D('0.5'), ZERO)
                    if trade.position <= DUST:
                        close(trade, r['ts'], 'sold')
            elif r['type'] == 'REDEEM' and market.get('resolved'):
                settle(r['ts'])
        if open_trades and market.get('resolved'):
            settle(market.get('closed_ts') or market.get('end_ts') or items[-1]['ts'])
        done.extend(open_trades.values())
        out.extend(t.result(first, market) for t in done)
    out.sort(key=lambda t: t['open_ts'] or 0)
    return out


def sync(client, store, progress=lambda message: None):
    """Download new activity and market details for the saved wallet."""
    started = time.time()
    latest = store.pm_latest_ts(client.address)
    since = None if latest is None else int(latest - OVERLAP)
    progress('Downloading Polymarket activity')
    rows = [normalize_activity(r) for r in client.activity(start=since)]
    store.replace_pm_activity(client.address, since, rows)

    known = store.pm_markets()
    conditions = sorted({r['condition_id'] for r in store.pm_activity(client.address) if r['condition_id']})
    needed = [c for c in conditions if not (known.get(c) or {}).get('resolved')]
    progress(f'Looking up {len(needed)} Polymarket markets' if needed else 'Polymarket markets up to date')
    found = []
    for i in range(0, len(needed), 20):
        try:
            found += [normalize_market(m) for m in client.markets(needed[i:i + 20])]
        except PolymarketError as exc:
            if exc.status == 0:
                raise
    if found:
        store.upsert_pm_markets(found)
    for m in found:
        if m['series_slug']:
            store.upsert_series('pm:' + m['series_slug'], m['series_title'], 'polymarket')
    try:
        store.set_meta('pm_value', client.value())
    except PolymarketError as exc:
        if exc.status == 0:
            raise
    store.set_meta('pm_last_sync', time.time())
    return {'new_rows': len(rows), 'seconds': round(time.time() - started, 1)}


def prices_for(client, store, trade, market):
    """Price history of the outcome token the trade held, cached once the market resolves."""
    key = 'pm:' + trade['token_id']
    fetched, data = store.candles(key)
    if data is not None and ((market or {}).get('resolved') or (fetched and time.time() - fetched < 60)):
        return data
    start = trade['open_ts'] - 3600
    end = min((trade['close_ts'] or time.time()) + 600, time.time())
    if market and market.get('start_ts'):
        start = max(start, market['start_ts'])
    if end - start > 3 * 86400:
        start = max(start, trade['open_ts'] - 86400)
    points = [{'t': p['t'], 'last': float(p['p']), 'bid': None, 'ask': None, 'mid': None}
              for p in client.prices(trade['token_id'], start, end)]
    store.save_candles(key, points)
    return points
