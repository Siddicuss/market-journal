"""Turn raw Kalshi fills into round-trip trades.

Kalshi nets YES and NO on the same market: buying NO is the same as selling
YES. Each fill is tracked as a signed YES position change. A trade opens when
the position leaves zero and closes when it returns to zero (sold out) or the
market settles. A fill that flips the position closes one trade and opens the
next with the remainder.
"""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal as D

ZERO = D(0)


def _d(value, default='0'):
    if value in (None, ''):
        value = default
    return D(str(value))


def _ts(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()


def normalize_fill(raw):
    """Reduce an API fill to the fields the journal needs."""
    count = _d(raw.get('count_fp', raw.get('count')))
    book = raw.get('book_side')
    if book not in ('bid', 'ask'):
        side, action = raw.get('side'), raw.get('action')
        buys_yes = (side == 'yes') == (action == 'buy')
        book = 'bid' if buys_yes else 'ask'
    yes_price = _d(raw.get('yes_price_dollars'), None) if raw.get('yes_price_dollars') not in (None, '') else None
    no_price = _d(raw.get('no_price_dollars'), None) if raw.get('no_price_dollars') not in (None, '') else None
    if yes_price is None and no_price is not None:
        yes_price = 1 - no_price
    if no_price is None and yes_price is not None:
        no_price = 1 - yes_price
    ts = _ts(raw.get('created_time')) or _ts(raw.get('ts'))
    return {
        'fill_id': str(raw.get('fill_id') or raw.get('trade_id')),
        'order_id': raw.get('order_id'),
        'ticker': raw.get('ticker') or raw.get('market_ticker'),
        'subaccount': int(raw.get('subaccount_number') or 0),
        'yes_delta': float(count if book == 'bid' else -count),
        'yes_price': float(yes_price or 0),
        'no_price': float(no_price or 0),
        'fee': float(_d(raw.get('fee_cost'))),
        'is_taker': 1 if raw.get('is_taker') else 0,
        'ts': ts,
    }


def _settlement_payout(direction, result, value):
    """Payout per contract held in `direction` ('yes' or 'no')."""
    if result in ('yes', 'no'):
        return D(1) if result == direction else ZERO
    if result == 'scalar' and value is not None:
        yes = D(str(value)) / 100
        return yes if direction == 'yes' else 1 - yes
    return None


class _Trade:
    def __init__(self, fill, direction):
        self.id = fill['fill_id']
        self.ticker = fill['ticker']
        self.subaccount = fill['subaccount']
        self.side = direction
        self.open_ts = fill['ts']
        self.close_ts = None
        self.bought = ZERO  # contracts opened in trade direction
        self.cost = ZERO
        self.sold = ZERO
        self.proceeds = ZERO
        self.settled = ZERO
        self.payout = ZERO
        self.fees = ZERO
        self.peak = ZERO
        self.fills = []
        self.close_reason = None
        self.taker = 0
        self.maker = 0

    def price(self, fill):
        return D(str(fill['yes_price'] if self.side == 'yes' else fill['no_price']))

    def add(self, fill, qty, opening, fee):
        price = self.price(fill)
        if opening:
            self.bought += qty
            self.cost += qty * price
        else:
            self.sold += qty
            self.proceeds += qty * price
        self.fees += fee
        if fill['is_taker']:
            self.taker += 1
        else:
            self.maker += 1
        self.fills.append({'fill_id': fill['fill_id'], 'ts': fill['ts'], 'action': 'buy' if opening else 'sell',
                           'side': self.side, 'qty': float(qty), 'price': float(price), 'fee': float(fee),
                           'is_taker': fill['is_taker']})

    def result(self, market):
        pnl_gross = self.proceeds + self.payout - self.cost
        pnl = pnl_gross - self.fees
        entry = self.cost / self.bought if self.bought else ZERO
        exit_qty = self.sold + self.settled
        exit_value = self.proceeds + self.payout
        close_ts = market.get('close_ts')
        side_won = None
        if market.get('result') in ('yes', 'no'):
            side_won = market['result'] == self.side
        return {
            'id': self.id, 'platform': 'kalshi', 'ticker': self.ticker, 'subaccount': self.subaccount,
            'side': self.side, 'status': 'closed' if self.close_reason else 'open',
            'close_reason': self.close_reason, 'open_ts': self.open_ts, 'close_ts': self.close_ts,
            'contracts': float(self.bought), 'peak': float(self.peak), 'entry_price': float(round(entry, 6)),
            'exit_price': float(round(exit_value / exit_qty, 6)) if exit_qty else None,
            'cost': float(self.cost), 'proceeds': float(self.proceeds), 'payout': float(self.payout),
            'sold_qty': float(self.sold), 'settled_qty': float(self.settled),
            'fees': float(self.fees), 'pnl_gross': float(pnl_gross), 'pnl': float(pnl),
            'return_pct': float(pnl / self.cost * 100) if self.cost else 0.0,
            'hold_seconds': (self.close_ts - self.open_ts) if self.close_ts and self.open_ts else None,
            'minutes_left': (close_ts - self.open_ts) / 60 if close_ts and self.open_ts else None,
            'series': market.get('series') or self.ticker.split('-')[0],
            'title': market.get('title') or self.ticker,
            'market_result': market.get('result'), 'side_won': side_won,
            'taker_fills': self.taker, 'maker_fills': self.maker,
            'fills': self.fills,
        }


def build_trades(fills, markets=None, settlements=None):
    """Group normalized fills into trades.

    markets: {ticker: {'result', 'close_ts', 'settled_ts', 'series', 'title', 'value'}}
    settlements: {ticker: {'result', 'settled_ts', 'fee', 'value'}}
    """
    markets, settlements = markets or {}, settlements or {}
    groups = defaultdict(list)
    for fill in fills:
        groups[(fill['subaccount'], fill['ticker'])].append(fill)
    out = []
    for (_, ticker), rows in groups.items():
        rows.sort(key=lambda f: (f['ts'] or 0, f['fill_id']))
        market = dict(markets.get(ticker) or {})
        settlement = settlements.get(ticker) or {}
        position, current, trades = ZERO, None, []
        for fill in rows:
            delta = D(str(fill['yes_delta']))
            fee = D(str(fill['fee']))
            if delta == 0:
                continue
            if current is None:
                current = _Trade(fill, 'yes' if delta > 0 else 'no')
            sign = 1 if current.side == 'yes' else -1
            if (delta > 0) == (sign > 0):
                current.add(fill, abs(delta), True, fee)
                position += delta
            else:
                closing = min(abs(delta), abs(position))
                remainder = abs(delta) - closing
                close_fee = fee * closing / abs(delta)
                current.add(fill, closing, False, close_fee)
                position += -sign * closing
                if remainder:
                    current.close_ts, current.close_reason = fill['ts'], 'sold'
                    trades.append(current)
                    current = _Trade(fill, 'no' if current.side == 'yes' else 'yes')
                    current.add(fill, remainder, True, fee - close_fee)
                    position = remainder if current.side == 'yes' else -remainder
            current.peak = max(current.peak, abs(position))
            if position == 0:
                current.close_ts, current.close_reason = fill['ts'], 'sold'
                trades.append(current)
                current = None
        if current is not None:
            result = settlement.get('result') or market.get('result')
            value = settlement.get('value', market.get('value'))
            payout = _settlement_payout(current.side, result, value)
            if payout is not None:
                qty = abs(position)
                # A settlement's fee_cost totals the fill fees already counted, so it is not added again.
                current.settled, current.payout = qty, qty * payout
                current.close_ts = settlement.get('settled_ts') or market.get('settled_ts') or market.get('close_ts') or rows[-1]['ts']
                current.close_reason = 'settled'
            if result and not market.get('result'):
                market['result'] = result
            trades.append(current)
        out.extend(t.result(market) for t in trades)
    out.sort(key=lambda t: t['open_ts'] or 0)
    return out
