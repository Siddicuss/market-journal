r"""Market Journal tests. Run: .venv\Scripts\python.exe -m unittest discover -p "test_*.py"."""
import json
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stats  # noqa: E402
from kalshi_client import KalshiError, ReadOnlyClient  # noqa: E402
from server import App, serve  # noqa: E402
from store import Store  # noqa: E402
from sync import normalize_market, sync  # noqa: E402
from trades import build_trades, normalize_fill  # noqa: E402

T0 = datetime(2026, 10, 20, 14, 0).timestamp()  # a Tuesday, local time


def raw_fill(fid, ticker, book, count, yes_price, ts, fee='0.02', taker=True, **extra):
    return {'fill_id': fid, 'trade_id': fid, 'order_id': 'o' + fid, 'ticker': ticker, 'market_ticker': ticker,
            'outcome_side': 'yes' if book == 'bid' else 'no', 'book_side': book, 'count_fp': f'{count:.2f}',
            'yes_price_dollars': f'{yes_price:.4f}', 'no_price_dollars': f'{1 - yes_price:.4f}',
            'is_taker': taker, 'fee_cost': fee, 'ts': int(ts), 'subaccount_number': 0, **extra}


def fills(*raws):
    return [normalize_fill(r) for r in raws]


class FillTests(unittest.TestCase):
    def test_book_side_sets_direction(self):
        f = normalize_fill(raw_fill('1', 'KXBTC15M-A', 'ask', 3, .40, T0))
        self.assertEqual(f['yes_delta'], -3.0)
        self.assertAlmostEqual(f['no_price'], .60)
        self.assertEqual(f['ts'], T0)

    def test_legacy_side_action(self):
        r = raw_fill('1', 'X', 'bid', 2, .3, T0)
        del r['book_side']
        r.update(side='no', action='sell')  # selling NO is buying YES
        self.assertEqual(normalize_fill(r)['yes_delta'], 2.0)
        r.update(side='no', action='buy')
        self.assertEqual(normalize_fill(r)['yes_delta'], -2.0)

    def test_created_time_preferred(self):
        r = raw_fill('1', 'X', 'bid', 1, .5, T0, created_time='2026-10-20T19:00:00Z')
        self.assertEqual(normalize_fill(r)['ts'], datetime.fromisoformat('2026-10-20T19:00:00+00:00').timestamp())


class TradeTests(unittest.TestCase):
    def test_buy_then_sell_yes(self):
        t, = build_trades(fills(raw_fill('1', 'M', 'bid', 10, .60, T0, fee='.17'),
                                raw_fill('2', 'M', 'ask', 10, .70, T0 + 120, fee='.15')))
        self.assertEqual((t['side'], t['status'], t['close_reason']), ('yes', 'closed', 'sold'))
        self.assertAlmostEqual(t['entry_price'], .60)
        self.assertAlmostEqual(t['exit_price'], .70)
        self.assertAlmostEqual(t['pnl_gross'], 1.0)
        self.assertAlmostEqual(t['pnl'], 1.0 - .32)
        self.assertEqual(t['hold_seconds'], 120)

    def test_buy_no_held_to_settlement_win(self):
        markets = {'M': {'result': 'no', 'close_ts': T0 + 900, 'series': 'KXBTC15M', 'title': 'BTC up?'}}
        t, = build_trades(fills(raw_fill('1', 'M', 'ask', 5, .30, T0, fee='.10')), markets)
        self.assertEqual(t['side'], 'no')
        self.assertAlmostEqual(t['entry_price'], .70)
        self.assertEqual(t['close_reason'], 'settled')
        self.assertAlmostEqual(t['payout'], 5.0)
        self.assertAlmostEqual(t['pnl'], 5 - 3.5 - .10)
        self.assertTrue(t['side_won'])
        self.assertAlmostEqual(t['minutes_left'], 15)
        self.assertEqual(t['close_ts'], T0 + 900)

    def test_settlement_fee_is_not_counted_twice(self):
        # Kalshi's settlement fee_cost repeats the market's fill fees.
        markets = {'M': {'result': 'no', 'close_ts': T0 + 900}}
        settlements = {'M': {'result': 'no', 'settled_ts': T0 + 960, 'fee': .05}}
        t, = build_trades(fills(raw_fill('1', 'M', 'bid', 4, .80, T0, fee='.05')), markets, settlements)
        self.assertAlmostEqual(t['fees'], .05)
        self.assertAlmostEqual(t['pnl'], -3.2 - .05)
        self.assertEqual(t['close_ts'], T0 + 960)
        self.assertFalse(t['side_won'])

    def test_flip_splits_into_two_trades(self):
        out = build_trades(fills(raw_fill('1', 'M', 'bid', 2, .50, T0, fee='.04'),
                                 raw_fill('2', 'M', 'ask', 5, .60, T0 + 60, fee='.10')),
                           {'M': {'result': 'yes'}})
        self.assertEqual([t['side'] for t in out], ['yes', 'no'])
        first, second = out
        self.assertAlmostEqual(first['pnl'], 2 * .10 - .04 - .04)
        self.assertEqual(second['contracts'], 3)
        self.assertAlmostEqual(second['entry_price'], .40)
        self.assertAlmostEqual(second['fees'], .06)
        self.assertAlmostEqual(second['pnl'], -1.2 - .06)

    def test_open_position_stays_open(self):
        t, = build_trades(fills(raw_fill('1', 'M', 'bid', 1, .5, T0)))
        self.assertEqual(t['status'], 'open')
        self.assertIsNone(t['close_ts'])

    def test_scaling_in_and_partial_exits(self):
        t, = build_trades(fills(raw_fill('1', 'M', 'bid', 2, .40, T0, fee='0'),
                                raw_fill('2', 'M', 'bid', 2, .60, T0 + 10, fee='0'),
                                raw_fill('3', 'M', 'ask', 1, .70, T0 + 20, fee='0')),
                          {'M': {'result': 'yes'}})
        self.assertEqual(t['contracts'], 4)
        self.assertEqual(t['peak'], 4)
        self.assertAlmostEqual(t['entry_price'], .50)
        self.assertAlmostEqual(t['pnl'], .70 + 3 - 2.0)
        self.assertAlmostEqual(t['exit_price'], (0.70 + 3) / 4)

    def test_subaccounts_are_separate(self):
        a = raw_fill('1', 'M', 'bid', 1, .5, T0)
        b = raw_fill('2', 'M', 'ask', 1, .6, T0 + 5, subaccount_number=2)
        out = build_trades(fills(a, b))
        self.assertEqual(len(out), 2)
        self.assertTrue(all(t['status'] == 'open' for t in out))

    def test_scalar_settlement(self):
        t, = build_trades(fills(raw_fill('1', 'M', 'ask', 2, .5, T0, fee='0')), {'M': {'result': 'scalar', 'value': 30}})
        self.assertAlmostEqual(t['payout'], 2 * .70)


def trade(pnl, ts, side='yes', entry=.6, contracts=10, tags=(), series='KXBTC15M', won=None, reason='settled'):
    return {'id': f'{ts}-{pnl}', 'ticker': series + '-X', 'title': 'BTC', 'series': series, 'side': side, 'status': 'closed',
            'close_reason': reason, 'open_ts': ts - 300, 'close_ts': ts, 'contracts': contracts, 'entry_price': entry,
            'exit_price': 1, 'cost': entry * contracts, 'fees': .1, 'pnl_gross': pnl + .1, 'pnl': pnl, 'return_pct': 0,
            'hold_seconds': 300, 'minutes_left': 9, 'market_result': None, 'side_won': won, 'taker_fills': 1,
            'maker_fills': 0, 'tags': list(tags), 'note': ''}


class StatsTests(unittest.TestCase):
    def setUp(self):
        self.trades = [trade(2, T0), trade(-1, T0 + 3600), trade(3, T0 + 86400), trade(-4, T0 + 2 * 86400),
                       trade(1, T0 + 2 * 86400 + 60)]

    def test_summary(self):
        s = stats.summary(self.trades)
        self.assertEqual((s['trades'], s['wins'], s['losses']), (5, 3, 2))
        self.assertAlmostEqual(s['net_pnl'], 1)
        self.assertAlmostEqual(s['win_rate'], 60)
        self.assertAlmostEqual(s['profit_factor'], 6 / 5)
        self.assertAlmostEqual(s['avg_loss'], -2.5)
        self.assertAlmostEqual(s['max_drawdown'], 4)  # peak 4 -> 0
        self.assertEqual(s['streak'], 1)
        self.assertEqual(s['longest_loss_streak'], 1)
        self.assertAlmostEqual(s['per_contract'], 1 / 50)

    def test_summary_empty(self):
        s = stats.summary([])
        self.assertEqual(s['trades'], 0)
        self.assertIsNone(s['win_rate'])

    def test_breakdowns(self):
        tags = [{'id': 1, 'name': 'Plan', 'kind': 'tag'}]
        ts = [trade(1, T0, side='yes', tags=tags), trade(-2, T0 + 10, side='no')]
        rows = {r['label']: r for r in stats.breakdown(ts, 'side')}
        self.assertEqual(rows['NO']['net_pnl'], -2)
        rows = {r['label']: r for r in stats.breakdown(ts, 'tag')}
        self.assertEqual(set(rows), {'Plan', '(no tag)'})
        for group in stats.GROUPS:
            stats.breakdown(ts, group)

    def test_calibration(self):
        ts = [trade(1, T0, entry=.62, won=True), trade(-1, T0, entry=.65, won=False), trade(1, T0, entry=.9, won=None)]
        row, = stats.calibration(ts)
        self.assertEqual(row['label'], '60-70¢')
        self.assertAlmostEqual(row['implied'], 63.5)
        self.assertAlmostEqual(row['actual'], 50)

    def test_week_report(self):
        week = stats.week_start(stats.day_key(T0)).isoformat()
        r = stats.week_report(self.trades + [trade(5, T0 - 7 * 86400)], week, {'worked': 'x'})
        self.assertEqual(r['week'], week)
        self.assertEqual(r['summary']['trades'], 5)
        self.assertEqual(r['previous']['trades'], 1)
        self.assertEqual(len(r['days']), 7)
        self.assertEqual(r['best'][0]['pnl'], 3)
        self.assertEqual(r['worst'][0]['pnl'], -4)
        weeks = stats.week_list(self.trades, {week: {'worked': 'x'}})
        self.assertTrue(weeks[0]['reviewed'])

    def test_calendar_and_filters(self):
        d = datetime.fromtimestamp(T0)
        cal = stats.calendar_month(self.trades, d.year, d.month, {})
        day = next(c for c in cal['days'] if c['day'] == stats.day_key(T0))
        self.assertAlmostEqual(day['pnl'], 1)
        self.assertEqual(day['trades'], 2)
        only = stats.filter_trades(self.trades, {'outcome': 'loss'})
        self.assertEqual(len(only), 2)
        start = stats.day_key(T0 + 86400)
        self.assertEqual(len(stats.filter_trades(self.trades, {'start': start})), 3)

    def test_dates_filter_by_close_day_and_search_covers_tags(self):
        overnight = trade(1, T0 + 86400)
        overnight['open_ts'] = T0  # opened the day before it closed
        tagged = trade(2, T0, tags=[{'id': 3, 'name': 'Breakout', 'kind': 'tag'}])
        self.assertEqual(stats.filter_trades([overnight], {'start': stats.day_key(T0 + 86400)}), [overnight])
        self.assertEqual(stats.filter_trades([overnight, tagged], {'search': 'breakout'}), [tagged])
        self.assertEqual(stats.breakdown([trade(1, T0, contracts=1)], 'size')[0]['label'], '1 contract')


class FakeKalshi:
    """Stands in for ReadOnlyClient with fixed responses."""

    def __init__(self):
        self.calls = []
        self.recent = [raw_fill('f1', 'KXBTC15M-26OCT201415-15', 'bid', 10, .60, T0, fee='.17'),
                       raw_fill('f2', 'KXBTC15M-26OCT201430-30', 'ask', 4, .25, T0 + 900, fee='.05')]
        self.archived = [raw_fill('h1', 'KXETH15M-26SEP011200-00', 'bid', 3, .55, T0 - 50 * 86400, fee='.04'),
                         raw_fill('h2', 'KXETH15M-26SEP011200-00', 'ask', 3, .65, T0 - 50 * 86400 + 60, fee='.04')]

    def get(self, path, **params):
        self.calls.append(path)
        if path == '/historical/cutoff':
            return {'trades_created_ts': '2026-10-01T00:00:00Z'}
        raise KalshiError(404)

    def pages(self, path, key, **params):
        self.calls.append(path)
        return list(self.archived) if path == '/historical/fills' else []

    def fills(self, min_ts=None):
        self.calls.append(('fills', min_ts))
        return [f for f in self.recent if min_ts is None or f['ts'] >= min_ts]

    def settlements(self, min_ts=None):
        return [{'ticker': 'KXBTC15M-26OCT201415-15', 'market_result': 'yes', 'settled_time': '2026-10-20T19:16:00Z',
                 'fee_cost': '0', 'revenue': 1000, 'yes_count_fp': '10.00', 'no_count_fp': '0.00'}]

    def markets(self, tickers):
        base = {'KXBTC15M-26OCT201415-15': ('yes', T0 + 900), 'KXBTC15M-26OCT201430-30': ('', T0 + 1800)}
        return [{'ticker': t, 'event_ticker': t.rsplit('-', 1)[0], 'title': 'Bitcoin up in 15 min?', 'yes_sub_title': 'Up',
                 'open_time': datetime.fromtimestamp(base[t][1] - 900).astimezone().isoformat(),
                 'close_time': datetime.fromtimestamp(base[t][1]).astimezone().isoformat(), 'result': base[t][0],
                 'status': 'finalized' if base[t][0] else 'active'} for t in tickers if t in base]

    def market(self, ticker, historical=False):
        if historical and ticker.startswith('KXETH'):
            return {'ticker': ticker, 'event_ticker': ticker.rsplit('-', 1)[0], 'title': 'Ethereum up?', 'result': 'no'}
        raise KalshiError(404)

    def balance(self):
        return {'balance': 12345, 'portfolio_value': 500}

    def series(self, ticker):
        self.calls.append(('series', ticker))
        return {'KXBTC15M': {'title': 'Bitcoin price up down', 'category': 'Crypto'},
                'KXETH15M': {'title': 'ETH 15M price up down', 'category': 'Crypto'}}.get(ticker, {})

    def candles(self, series, ticker, start, end, historical=False):
        return [{'end_period_ts': int(start) + 60, 'yes_bid': {'close_dollars': '0.58'}, 'yes_ask': {'close_dollars': '0.62'},
                 'price': {'close_dollars': '0.60'}}]


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / 'j.db')

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_full_then_incremental_sync(self):
        client = FakeKalshi()
        result = sync(client, self.store)
        self.assertEqual(result['new_fills'], 4)
        trades = {t['ticker']: t for t in self.store.trades()}
        btc = trades['KXBTC15M-26OCT201415-15']
        self.assertEqual(btc['close_reason'], 'settled')
        self.assertAlmostEqual(btc['pnl'], 10 - 6 - .17)
        self.assertEqual(btc['series'], 'KXBTC15M')
        self.assertEqual(trades['KXBTC15M-26OCT201430-30']['status'], 'open')
        eth = trades['KXETH15M-26SEP011200-00']
        self.assertEqual(eth['title'], 'Ethereum up?')
        self.assertAlmostEqual(eth['pnl'], .30 - .08)
        self.assertEqual(self.store.get_meta('balance'), {'cash': 123.45, 'portfolio': 5.0})
        self.assertIn('/historical/fills', client.calls)

        client2 = FakeKalshi()
        sync(client2, self.store)
        self.assertNotIn('/historical/fills', client2.calls)
        since = next(c[1] for c in client2.calls if isinstance(c, tuple))
        self.assertEqual(since, int(T0 + 900 - 86400))
        self.assertEqual(len(self.store.trades()), 3)

    def test_notes_and_tags_survive_resync(self):
        sync(FakeKalshi(), self.store)
        tid = self.store.trades()[0]['id']
        self.store.set_trade_note(tid, 'patient entry')
        tag = self.store.create_tag('  Followed   plan ')
        self.assertEqual(tag['name'], 'Followed plan')
        self.store.set_trade_tags(tid, [tag['id']])
        sync(FakeKalshi(), self.store)
        t = self.store.trade(tid)
        self.assertEqual(t['note'], 'patient entry')
        self.assertEqual([x['name'] for x in t['tags']], ['Followed plan'])
        self.store.delete_tag(tag['id'])
        self.assertEqual(self.store.trade(tid)['tags'], [])

    def test_market_names(self):
        client = FakeKalshi()
        sync(client, self.store)
        names = {t['series']: t['series_name'] for t in self.store.trades()}
        self.assertEqual(names, {'KXBTC15M': 'Bitcoin 15-min up/down', 'KXETH15M': 'Ethereum 15-min up/down'})
        self.assertEqual(len([c for c in client.calls if c[0] == 'series']), 2)
        self.store.set_series_name('KXBTC15M', '  BTC   quick ')
        self.assertEqual(self.store.trades()[-1]['series_name'], 'BTC quick')
        rows = {r['label'] for r in stats.breakdown(self.store.trades(), 'series')}
        self.assertIn('BTC quick', rows)
        self.store.set_series_name('KXBTC15M', '')
        client2 = FakeKalshi()
        sync(client2, self.store)  # titles are cached, so no repeat lookups
        self.assertFalse([c for c in client2.calls if c[0] == 'series'])
        self.assertIn('Bitcoin 15-min up/down', {t['series_name'] for t in self.store.trades()})

    def test_friendly_name_rules(self):
        from market_names import friendly_name
        self.assertEqual(friendly_name('KXSOL15M', 'whatever'), 'Solana 15-min up/down')
        self.assertEqual(friendly_name('KXEARNINGSMENTIONGOOGL', 'earnings mention'), 'GOOGL earnings call mentions')
        self.assertEqual(friendly_name('KXMVECROSSCATEGORY', 'MVE Cross Category'), 'Combos (multi-leg)')
        self.assertEqual(friendly_name('KXRANKLISTSONGTHRILLER', 'WILL THRILLER MAKE THE TOP 10'), 'Will thriller make the top 10')
        self.assertEqual(friendly_name('KXTOP10TAYLORSWIFT', 'Taylor swift holds top ten spots?'), 'Taylor swift holds top ten spots')
        self.assertEqual(friendly_name('KXLOLGAME', 'League of Legends Game'), 'League of Legends Game')
        self.assertEqual(friendly_name('KXNEW', None), 'KXNEW')
        self.assertEqual(friendly_name('KXBTC15M', 'x', custom='Mine'), 'Mine')

    def test_normalize_market(self):
        m = normalize_market({'ticker': 'KXBTC15M-A-B', 'event_ticker': 'KXBTC15M-A', 'result': '',
                              'settlement_value_dollars': '1.0000', 'close_time': '2026-10-20T19:15:00Z'})
        self.assertIsNone(m['result'])
        self.assertEqual(m['series'], 'KXBTC15M')
        self.assertEqual(m['value'], 100)


class ClientTests(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()).decode()

    def test_refuses_writes(self):
        client = ReadOnlyClient('id', self.pem, opener=lambda *a, **k: self.fail('should not send'))
        for method in ('POST', 'DELETE', 'PUT'):
            with self.assertRaises(PermissionError):
                client.request(method, '/portfolio/orders')

    def test_signs_and_paginates(self):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        import base64
        seen = []

        class Resp:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(self.body).encode()

        def opener(req, timeout):
            seen.append(req)
            h = {k.lower(): v for k, v in req.header_items()}
            path = urllib.parse.urlparse(req.full_url).path
            self.key.public_key().verify(base64.b64decode(h['kalshi-access-signature']),
                                         (h['kalshi-access-timestamp'] + 'GET' + path).encode(),
                                         padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                                         hashes.SHA256())
            return Resp({'fills': [1], 'cursor': ''} if 'cursor=' in req.full_url else {'fills': [0], 'cursor': 'abc'})

        client = ReadOnlyClient('id', self.pem, opener=opener)
        self.assertEqual(client.fills(), [0, 1])
        self.assertTrue(all(r.get_method() == 'GET' for r in seen))
        self.assertIn('/trade-api/v2/portfolio/fills', seen[0].full_url)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        import os
        import credentials
        self.credentials = credentials
        self.dir = tempfile.TemporaryDirectory()
        self.old = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = self.dir.name

    def tearDown(self):
        import os
        os.environ['LOCALAPPDATA'] = self.old
        self.dir.cleanup()

    def test_starts_empty_even_when_the_bot_has_a_key(self):
        bot = Path(self.dir.name) / 'KalshiLocalTrader' / 'profile.json'
        bot.parent.mkdir(parents=True)
        bot.write_text(json.dumps({'credentials': {'production': 'anything'}}), encoding='utf-8')
        self.assertIsNone(self.credentials.load())

    def test_entered_key_is_saved_encrypted_and_can_be_removed(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()).decode()
        self.credentials.save(' key-id ', pem)
        stored = self.credentials.journal_credentials_path().read_text(encoding='utf-8')
        self.assertNotIn('key-id', stored)
        self.assertNotIn('PRIVATE KEY', stored)
        self.assertEqual(self.credentials.load(), ('key-id', pem, 'journal'))
        self.credentials.forget()
        self.assertIsNone(self.credentials.load())


# ---------------------------------------------------------------- Polymarket
COND, UP, DOWN = '0xc1', 'tok-up-0000000001', 'tok-down-000000002'


def pm_row(ts, kind, side='', size=10, usdc=0.0, price=0.0, asset=UP, outcome='Up', tx=None, condition=COND):
    return {'timestamp': int(ts), 'type': kind, 'condition_id': condition, 'token_id': asset, 'side': side,
            'size': size, 'usdc_size': usdc, 'price': price, 'outcome': outcome, 'slug': 'btc-updown-5m-1',
            'title': 'Bitcoin Up or Down - 2:00PM-2:05PM ET', 'event_slug': 'btc-updown-5m-1',
            'transaction_hash': tx or f'0x{kind}{int(ts)}{side}'}


def pm_market(prices=('1', '0'), closed=True, condition=COND):
    return {'conditionId': condition, 'slug': 'btc-updown-5m-1', 'question': 'Bitcoin Up or Down - 2:00PM-2:05PM ET',
            'outcomes': '["Up", "Down"]', 'outcomePrices': json.dumps(list(prices)), 'clobTokenIds': json.dumps([UP, DOWN]),
            'closed': closed, 'closedTime': '2026-10-20 19:06:00+00', 'endDate': '2026-10-20T19:05:00Z',
            'startDate': '2026-10-19T19:00:00Z',
            'events': [{'slug': 'btc-updown-5m-1', 'series': [{'slug': 'btc-up-or-down-5m', 'title': 'BTC Up or Down 5m'}]}]}


class PolymarketTradeTests(unittest.TestCase):
    def build(self, rows, market=None):
        import polymarket
        markets = {COND: polymarket.normalize_market(market)} if market else {}
        return polymarket.build_trades([polymarket.normalize_activity(r) for r in rows], markets)

    def test_buy_then_sell(self):
        t, = self.build([pm_row(T0, 'TRADE', 'BUY', 10, 6.0, .60), pm_row(T0 + 60, 'TRADE', 'SELL', 10, 7.0, .70)])
        self.assertEqual((t['platform'], t['side'], t['status'], t['close_reason']), ('polymarket', 'Up', 'closed', 'sold'))
        self.assertAlmostEqual(t['pnl'], 1.0)
        self.assertAlmostEqual(t['entry_price'], .60)
        self.assertEqual(t['hold_seconds'], 60)
        self.assertEqual(t['series'], 'pm:btc-updown-5m-1')  # no market details yet

    def test_held_to_resolution_win_and_loss(self):
        rows = [pm_row(T0, 'TRADE', 'BUY', 10, 4.0, .40), pm_row(T0 + 5, 'TRADE', 'BUY', 5, 3.0, .60, asset=DOWN, outcome='Down'),
                pm_row(T0 + 900, 'REDEEM', size=10, usdc=10.0, asset='')]
        trades = {t['side']: t for t in self.build(rows, pm_market(('1', '0')))}
        self.assertEqual(trades['Up']['close_reason'], 'settled')
        self.assertAlmostEqual(trades['Up']['pnl'], 6.0)
        self.assertTrue(trades['Up']['side_won'])
        self.assertAlmostEqual(trades['Down']['pnl'], -3.0)
        self.assertFalse(trades['Down']['side_won'])
        self.assertEqual(trades['Up']['close_ts'], T0 + 900)
        self.assertEqual(trades['Up']['series'], 'pm:btc-up-or-down-5m')
        self.assertEqual(trades['Up']['market_result'], 'Up')

    def test_unredeemed_loser_settles_when_market_resolves(self):
        t, = self.build([pm_row(T0, 'TRADE', 'BUY', 10, 4.0, .40)], pm_market(('0', '1')))
        self.assertEqual(t['close_reason'], 'settled')
        self.assertAlmostEqual(t['pnl'], -4.0)
        self.assertEqual(t['close_ts'], datetime.fromisoformat('2026-10-20T19:06:00+00:00').timestamp())

    def test_open_until_resolved(self):
        t, = self.build([pm_row(T0, 'TRADE', 'BUY', 10, 4.0, .40)], pm_market(('0.4', '0.6'), closed=False))
        self.assertEqual(t['status'], 'open')

    def test_fees_come_from_the_cash_that_moved(self):
        t, = self.build([pm_row(T0, 'TRADE', 'BUY', 10, 5.1, .50), pm_row(T0 + 60, 'TRADE', 'SELL', 10, 5.9, .60)])
        self.assertAlmostEqual(t['fees'], .2)
        self.assertAlmostEqual(t['pnl'], .8)
        self.assertAlmostEqual(t['pnl_gross'], 1.0)

    def test_split_and_merge(self):
        rows = [pm_row(T0, 'SPLIT', size=10, usdc=10.0, asset='', outcome=''),
                pm_row(T0 + 10, 'TRADE', 'SELL', 10, 3.0, .30, asset=DOWN, outcome='Down'),
                pm_row(T0 + 20, 'TRADE', 'BUY', 4, 2.8, .70, asset=DOWN, outcome='Down'),
                pm_row(T0 + 30, 'MERGE', size=4, usdc=4.0, asset='', outcome='')]
        trades = sorted(self.build(rows, pm_market(('0.5', '0.5'), closed=False)), key=lambda t: t['open_ts'])
        down_split, up, down_again = trades[0], trades[1], trades[2]
        self.assertEqual({t['side'] for t in trades[:2]}, {'Up', 'Down'})
        down_split = next(t for t in trades[:2] if t['side'] == 'Down')
        up = next(t for t in trades[:2] if t['side'] == 'Up')
        self.assertAlmostEqual(down_split['pnl'], 3.0 - 5.0)
        self.assertEqual(up['status'], 'open')  # 4 of 10 merged back
        self.assertAlmostEqual(up['proceeds'], 2.0)
        self.assertEqual((down_again['status'], down_again['close_reason']), ('closed', 'sold'))
        self.assertAlmostEqual(down_again['pnl'], 2.0 - 2.8)

    def test_sell_without_tracked_buy_is_ignored(self):
        self.assertEqual(self.build([pm_row(T0, 'TRADE', 'SELL', 10, 5.0, .5)]), [])

    def test_yes_no_outcomes_use_yes_no_sides(self):
        t, = self.build([pm_row(T0, 'TRADE', 'BUY', 1, .3, .3, outcome='No')])
        self.assertEqual(t['side'], 'no')

    def test_address_validation(self):
        from polymarket_client import validate_address
        self.assertEqual(validate_address(' 0xABCDEF0123456789abcdef0123456789ABCDEF01 '), '0xabcdef0123456789abcdef0123456789abcdef01')
        for bad in ('', '0x123', 'abcdef0123456789abcdef0123456789abcdef0123'):
            with self.assertRaises(ValueError):
                validate_address(bad)

    def test_friendly_names(self):
        from market_names import friendly_name
        self.assertEqual(friendly_name('pm:btc-up-or-down-5m', 'BTC Up or Down 5m'), 'Bitcoin 5-min up/down')
        self.assertEqual(friendly_name('pm:eth-up-or-down-1h'), 'Ethereum 1-hour up/down')
        self.assertEqual(friendly_name('pm:nhl-2026', 'NHL 2026'), 'NHL 2026')


class FakePolymarket:
    def __init__(self, address='0x' + 'a' * 40, rows=None):
        self.address = address
        self.rows = rows if rows is not None else [pm_row(T0, 'TRADE', 'BUY', 10, 4.0, .40),
                                                   pm_row(T0 + 60, 'TRADE', 'SELL', 10, 5.0, .50)]
        self.calls = []

    def activity(self, start=None):
        self.calls.append(('activity', start))
        return [r for r in self.rows if start is None or r['timestamp'] >= start]

    def markets(self, ids):
        self.calls.append(('markets', tuple(ids)))
        return [pm_market(('0.5', '0.5'), closed=False)] if COND in ids else []

    def value(self):
        return 12.5

    def prices(self, token, start, end):
        return [{'t': int(start) + 60, 'p': 0.45}]


class PolymarketSyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / 'j.db')

    def tearDown(self):
        self.store.close()
        self.dir.cleanup()

    def test_sync_is_incremental_and_never_duplicates(self):
        import polymarket
        from sync import rebuild
        client = FakePolymarket()
        polymarket.sync(client, self.store)
        sync(FakeKalshi(), self.store)  # both platforms in one journal
        trades = self.store.trades()
        self.assertEqual({t['platform'] for t in trades}, {'kalshi', 'polymarket'})
        pm, = [t for t in trades if t['platform'] == 'polymarket']
        self.assertAlmostEqual(pm['pnl'], 1.0)
        self.assertEqual(pm['series_name'], 'Bitcoin 5-min up/down')
        self.assertEqual(self.store.get_meta('pm_value'), 12.5)
        self.store.set_trade_note(pm['id'], 'kept')
        client2 = FakePolymarket()
        polymarket.sync(client2, self.store)
        self.assertEqual(client2.calls[0], ('activity', int(T0 + 60 - 86400)))
        rebuild(self.store)
        self.assertEqual(len(self.store.pm_activity()), 2)
        self.assertEqual(self.store.trade(pm['id'])['note'], 'kept')
        self.assertEqual(len(stats.filter_trades(self.store.trades(), {'platform': 'polymarket'})), 1)
        rows = {r['label'] for r in stats.breakdown(self.store.trades(), 'platform')}
        self.assertEqual(rows, {'Kalshi', 'Polymarket'})


class FakeCreds:
    def __init__(self):
        self.saved = None

    def load(self):
        return ('id', 'pem', 'journal') if self.saved else None

    def validate(self, key, pem):
        if not key:
            raise ValueError('Enter your API key ID')

    def save(self, key, pem):
        self.saved = (key, pem)

    def forget(self):
        self.saved = None


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.dir.name) / 'j.db')
        sync(FakeKalshi(), self.store)
        self.creds = FakeCreds()
        self.app = App(self.store, Path(__file__).parent / 'web', client_factory=lambda k, p: FakeKalshi(), creds=self.creds,
                       pm_factory=lambda address: FakePolymarket(address), polymarket_enabled=True)
        self.httpd = serve(self.app)
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}'

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.store.close()
        self.dir.cleanup()

    def call(self, path, body=None, token=True):
        headers = {'X-Token': self.app.token} if token else {}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method='POST' if data is not None else 'GET')
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if r.headers.get_content_type() == 'application/json' else raw)
        except urllib.error.HTTPError as e:
            with e:
                raw = e.read() or b'{}'
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw

    def test_donate_opens_paypal_in_the_browser(self):
        opened = []
        self.app.open_url = opened.append
        self.assertEqual(self.call('/api/donate', {})[0], 200)
        self.assertEqual(opened, ['https://www.paypal.com/donate/?business=DCQT2RXMBW7B2&no_recurring=0&currency_code=USD'])
        self.assertEqual(self.call('/api/donate', {}, token=False)[0], 403)

    def test_update_check_and_install(self):
        events = []

        class FakeUpdates:
            available = True

            def installed(self):
                return True

            def check(self):
                return {'current': '1.2.0', 'latest': '1.3.0', 'available': self.available,
                        'release': {'version': '1.3.0', 'notes': 'Faster sync', 'url': 'https://github.com/x', 'size': 3}}

            def download(self, release, progress):
                progress(3, 3)
                events.append(('download', release['version']))
                return Path('setup.exe')

            def launch_installer(self, setup):
                events.append(('launch', setup.name))

        fake = FakeUpdates()
        self.app.updates = fake
        self.app.quit = lambda: events.append(('quit',))
        status, body = self.call('/api/update')
        self.assertEqual((status, body['available'], body['latest'], body['notes']), (200, True, '1.3.0', 'Faster sync'))
        self.assertEqual(self.call('/api/update', {}, token=False)[0], 403)
        self.assertEqual(self.call('/api/update', {}), (200, {'started': True}))
        deadline = time.time() + 5
        while ('quit',) not in events and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(events, [('download', '1.3.0'), ('launch', 'setup.exe'), ('quit',)])
        self.assertEqual(self.call('/api/status')[1]['version'], __import__('version').VERSION)
        fake.available = False
        self.app.update_state['running'] = False
        status, body = self.call('/api/update', {})
        self.assertEqual(status, 400)
        self.assertIn('newest version', body['error'])

    def test_update_failure_is_reported(self):
        import updater

        class Broken:
            def installed(self):
                return True

            def check(self):
                return {'current': '1.0.0', 'latest': '2.0.0', 'available': True, 'release': {'version': '2.0.0'}}

            def download(self, release, progress):
                raise updater.UpdateError('The download was incomplete. Try again.')

        self.app.updates = Broken()
        self.assertEqual(self.call('/api/update', {})[0], 200)
        deadline = time.time() + 5
        while self.app.update_state['running'] and time.time() < deadline:
            time.sleep(0.05)
        state = self.call('/api/status')[1]['update']
        self.assertEqual((state['running'], state['error']), (False, 'The download was incomplete. Try again.'))

    def test_update_needs_installed_app(self):
        self.assertEqual(self.call('/api/update', {})[0], 400)  # tests run from source

    def test_polymarket_coming_soon(self):
        import polymarket
        from sync import rebuild
        wallet = '0x' + 'a' * 40
        self.assertEqual(self.call('/api/polymarket', {'address': wallet})[0], 200)
        polymarket.sync(FakePolymarket(wallet), self.store)
        rebuild(self.store)
        self.assertTrue(any(t['platform'] == 'polymarket' for t in self.store.trades()))
        off = App(self.store, Path(__file__).parent / 'web', creds=self.creds,
                  pm_factory=lambda address: FakePolymarket(address), polymarket_enabled=False)
        self.assertIsNone(off.pm)  # the saved wallet is ignored and never synced
        self.assertFalse(any(t['platform'] == 'polymarket' for t in self.store.trades()))
        self.assertFalse(off.get('/api/status', {})['polymarket_enabled'])
        with self.assertRaises(ValueError):
            off.post('/api/polymarket', {'address': wallet})
        self.assertEqual(self.store.get_meta('pm_wallet'), wallet)  # kept for when it comes back

    def test_requires_token(self):
        self.assertEqual(self.call('/api/status', token=False)[0], 403)
        self.assertEqual(self.call('/api/sync', {}, token=False)[0], 403)

    def test_static_files_and_traversal(self):
        status, body = self.call('/', token=False)
        self.assertEqual(status, 200)
        self.assertIn(b'Market Journal', body)
        self.assertEqual(self.call('/../store.py', token=False)[0], 404)

    def test_read_routes(self):
        for path in ('/api/status', '/api/dashboard', '/api/trades?outcome=win', '/api/tags', '/api/summary',
                     '/api/reports?group=entry_price', '/api/calendar?year=2026&month=10', '/api/day?day=2026-10-20',
                     '/api/weeks', '/api/week?week=2026-10-21'):
            status, body = self.call(path)
            self.assertEqual(status, 200, (path, body))
        status, body = self.call('/api/week?week=2026-10-21')
        self.assertEqual(body['week'], '2026-10-19')
        self.assertEqual(self.call('/api/reports?group=bogus')[0], 400)

    def test_journal_writes_and_csv(self):
        tid = self.store.trades()[0]['id']
        self.assertEqual(self.call('/api/trade/note', {'id': tid, 'note': 'hello'})[0], 200)
        _, tag = self.call('/api/tags', {'name': 'FOMO', 'kind': 'mistake'})
        self.call('/api/trade/tags', {'id': tid, 'tag_ids': [tag['tag']['id']]})
        self.call('/api/day/note', {'day': '2026-10-20', 'note': 'calm day'})
        self.call('/api/week/review', {'week': '2026-10-22', 'worked': 'a', 'didnt': 'b', 'focus': 'c'})
        _, t = self.call('/api/trade?id=' + urllib.parse.quote(tid))
        self.assertEqual(t['trade']['note'], 'hello')
        self.assertEqual(t['trade']['tags'][0]['name'], 'FOMO')
        self.assertEqual(self.call('/api/day?day=2026-10-20')[1]['note'], 'calm day')
        self.assertEqual(self.call('/api/week?week=2026-10-19')[1]['review']['focus'], 'c')
        status, csv = self.call('/api/export.csv')
        self.assertEqual(status, 200)
        self.assertIn('FOMO', csv.decode('utf-8-sig'))
        self.assertIn('hello', csv.decode('utf-8-sig'))

    def test_credentials_and_sync(self):
        status = self.call('/api/status')[1]
        self.assertFalse(status['configured'])
        self.assertEqual(status['trades'], 3)  # saved trades stay viewable without a key
        self.assertEqual(self.call('/api/sync', {})[0], 400)
        self.assertEqual(self.call('/api/credentials', {'key': '', 'pem': 'x'})[0], 400)
        status, body = self.call('/api/credentials', {'key': 'abc', 'pem': 'x'})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.call('/api/status')[1]['configured'])
        self.assertTrue(self.call('/api/sync', {})[1]['started'])
        for _ in range(100):
            if not self.app.sync_state['running']:
                break
            time.sleep(.05)
        self.assertIsNone(self.app.sync_state['error'])
        tid = self.store.trades()[0]['id']
        status, body = self.call('/api/candles?id=' + urllib.parse.quote(tid))
        self.assertEqual(status, 200)
        self.assertAlmostEqual(body['points'][0]['mid'], .60)
        self.call('/api/credentials/forget', {})
        self.assertFalse(self.call('/api/status')[1]['configured'])

    def wait_for_sync(self):
        for _ in range(100):
            if not self.app.sync_state['running']:
                break
            time.sleep(.05)

    def test_polymarket_connect_sync_and_disconnect(self):
        self.assertEqual(self.call('/api/polymarket', {'address': 'nope'})[0], 400)
        status, body = self.call('/api/polymarket', {'address': '0x' + 'B' * 40})
        self.assertEqual((status, body['address']), (200, '0x' + 'b' * 40))
        status = self.call('/api/status')[1]
        self.assertTrue(status['configured'])
        self.assertFalse(status['kalshi'])
        self.assertTrue(self.call('/api/sync', {})[1]['started'])
        self.wait_for_sync()
        self.assertIsNone(self.app.sync_state['error'])
        status = self.call('/api/status')[1]
        self.assertEqual([p['id'] for p in status['platforms']], ['kalshi', 'polymarket'])
        self.assertEqual(status['pm_value'], 12.5)
        self.assertIn('Polymarket', {x['platform'] for x in status['series']})
        _, body = self.call('/api/trades?platform=polymarket')
        t, = body['trades']
        _, detail = self.call('/api/trade?id=' + urllib.parse.quote(t['id']))
        self.assertEqual(detail['market']['close_ts'], datetime.fromisoformat('2026-10-20T19:05:00+00:00').timestamp())
        _, candles = self.call('/api/candles?id=' + urllib.parse.quote(t['id']))
        self.assertTrue(candles['direct'])
        self.assertAlmostEqual(candles['points'][0]['last'], .45)
        csv = self.call('/api/export.csv?platform=polymarket')[1].decode('utf-8-sig')
        self.assertIn('Polymarket,Bitcoin 5-min up/down', csv)
        self.assertEqual(self.call('/api/reports?group=platform')[0], 200)
        self.call('/api/polymarket/forget', {})
        status = self.call('/api/status')[1]
        self.assertFalse(status['configured'])
        self.assertEqual([p['id'] for p in status['platforms']], ['kalshi'])

    def test_one_platform_failing_does_not_stop_the_other(self):
        from polymarket_client import PolymarketError

        class Broken(FakePolymarket):
            def activity(self, start=None):
                raise PolymarketError(503)
        self.app.pm_factory = lambda address: Broken(address)
        self.call('/api/credentials', {'key': 'abc', 'pem': 'x'})
        self.call('/api/polymarket', {'address': '0x' + 'c' * 40})
        self.call('/api/sync', {})
        self.wait_for_sync()
        self.assertIn('Polymarket', self.app.sync_state['error'])
        self.assertEqual(self.app.sync_state['message'], 'Synced Kalshi (with errors)')


class InstallerTests(unittest.TestCase):
    """Install/uninstall into temp folders; shortcuts and the registry are stubbed out."""

    def setUp(self):
        import installer
        import zipfile
        self.installer = installer
        self.dir = tempfile.TemporaryDirectory()
        root = Path(self.dir.name)
        self.zip = root / 'payload.zip'
        with zipfile.ZipFile(self.zip, 'w') as z:
            z.writestr(installer.EXE, b'exe')
            z.writestr('_internal/lib.dll', b'dll')
        self.data = root / 'data'
        self.data.mkdir()
        (self.data / 'journal.db').write_bytes(b'db')
        self.links = []
        self.saved = {name: getattr(installer, name) for name in
                      ('payload', 'make_shortcut', 'register', 'unregister', 'registered_dir', 'local_appdata',
                       'data_dir', 'start_menu_link', 'desktop_link')}
        self.unregistered = []
        installer.payload = lambda: self.zip
        installer.make_shortcut = lambda link, target: self.links.append((link.name, target))
        installer.register = lambda d: None
        installer.unregister = lambda key=installer.UNINSTALL_KEY: self.unregistered.append(key)
        installer.registered_dir = lambda key=None: None
        installer.local_appdata = lambda: root
        installer.data_dir = lambda name='MarketJournal': self.data if name == 'MarketJournal' else root / name
        installer.start_menu_link = lambda app=installer.APP: root / f'start-{app}.lnk'
        installer.desktop_link = lambda app=installer.APP: root / f'desktop-{app}.lnk'

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.installer, name, value)
        self.dir.cleanup()

    def test_install_upgrade_and_uninstall_keep_data(self):
        target = Path(self.dir.name) / 'Programs' / 'Market Journal'
        exe = self.installer.install(target)
        self.assertTrue(exe.is_file())
        self.assertTrue((target / 'uninstall.exe').is_file())
        self.assertEqual([n for n, _ in self.links], ['start-Market Journal.lnk', 'desktop-Market Journal.lnk'])
        (target / 'old-file.txt').write_text('from v0')
        self.installer.install(target, desktop=False)  # upgrade replaces the program files only
        self.assertFalse((target / 'old-file.txt').exists())
        self.installer.uninstall(target)
        self.assertFalse(target.exists())
        self.assertTrue((self.data / 'journal.db').exists())
        self.installer.uninstall(target, delete_data=True)
        self.assertFalse(self.data.exists())

    def test_never_replaces_or_deletes_an_unrelated_folder(self):
        folder = Path(self.dir.name) / 'Documents'
        folder.mkdir()
        (folder / 'taxes.xlsx').write_text('keep me')
        with self.assertRaises(RuntimeError):
            self.installer.install(folder)
        self.installer.uninstall(folder)
        self.assertTrue((folder / 'taxes.xlsx').exists())

    def test_reinstalls_over_a_leftover_uninstaller(self):
        target = Path(self.dir.name) / 'Market Journal'
        target.mkdir()
        (target / 'uninstall.exe').write_bytes(b'left behind')
        self.assertTrue(self.installer.install(target).is_file())

    def test_replaces_the_old_kalshi_journal_install_and_moves_its_data(self):
        root = Path(self.dir.name)
        old = root / 'Programs' / 'Kalshi Journal'
        (old / '_internal').mkdir(parents=True)
        (old / 'KalshiJournal.exe').write_bytes(b'old')
        (root / 'start-Kalshi Journal.lnk').write_bytes(b'link')
        old_data = root / 'KalshiJournal'
        old_data.mkdir()
        (old_data / 'journal.db').write_bytes(b'my notes')
        self.data = root / 'MarketJournal'  # not created yet: the first install under the new name
        self.installer.install(root / 'Programs' / 'Market Journal')
        self.assertFalse(old.exists())
        self.assertFalse((root / 'start-Kalshi Journal.lnk').exists())
        self.assertIn(self.installer.OLD_UNINSTALL_KEY, self.unregistered)
        self.assertEqual((self.data / 'journal.db').read_bytes(), b'my notes')
        self.assertFalse(old_data.exists())

    def test_old_name_cleanup_leaves_unrelated_folders(self):
        root = Path(self.dir.name)
        other = root / 'Programs' / 'Kalshi Journal'
        other.mkdir(parents=True)
        (other / 'mine.txt').write_text('keep')
        self.installer.remove_old_name()
        self.assertTrue((other / 'mine.txt').exists())


class UpdaterTests(unittest.TestCase):
    def release(self, tag='v1.10.0', **asset):
        return {'tag_name': tag, 'body': ' Notes ', 'assets': [
            {'name': 'notes.txt', 'browser_download_url': 'https://github.com/n'},
            {'name': 'MarketJournal-Setup.exe', 'size': 5,
             'browser_download_url': 'https://github.com/Siddicuss/market-journal/releases/download/v1.10.0/MarketJournal-Setup.exe',
             **asset}]}

    def test_compares_versions_numerically(self):
        import updater
        self.assertGreater(updater.parse_version('v1.10.0'), updater.parse_version('1.9.3'))
        info = updater.check(lambda url: self.release())
        self.assertTrue(info['available'])
        self.assertEqual((info['latest'], info['release']['notes'], info['release']['size']), ('1.10.0', 'Notes', 5))
        self.assertFalse(updater.check(lambda url: self.release('v' + updater.VERSION))['available'])
        self.assertFalse(updater.check(lambda url: self.release('v0.9.0'))['available'])
        with self.assertRaises(updater.UpdateError):
            updater.check(lambda url: {'tag_name': 'v9.0.0', 'assets': []})
        with self.assertRaises(updater.UpdateError):
            updater.check(lambda url: self.release('nightly'))

    def test_reads_github_checksum(self):
        import updater
        digest = 'sha256:' + 'ab' * 32
        self.assertEqual(updater.latest(lambda url: self.release(digest=digest))['sha256'], 'ab' * 32)
        self.assertIsNone(updater.latest(lambda url: self.release())['sha256'])

    def download(self, payload, **release):
        import hashlib
        import io
        import updater
        rel = {'version': '1.10.0', 'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(),
               'url': 'https://github.com/Siddicuss/market-journal/releases/download/v1.10.0/MarketJournal-Setup.exe'} | release

        class Response(io.BytesIO):
            headers = {'Content-Length': str(len(payload))}

        original = updater._open
        updater._open = lambda url, timeout=20: Response(payload)
        folder = tempfile.mkdtemp()
        try:
            return updater.download(rel, folder=folder), folder
        finally:
            updater._open = original

    def test_download_checks_size_and_checksum(self):
        import updater
        path, folder = self.download(b'setup')
        self.assertEqual(path.read_bytes(), b'setup')
        with self.assertRaises(updater.UpdateError):
            self.download(b'setup', sha256='0' * 64)
        with self.assertRaises(updater.UpdateError):
            self.download(b'setup', size=99)
        with self.assertRaises(updater.UpdateError):
            self.download(b'setup', url='https://example.com/MarketJournal-Setup.exe')

    def test_installer_wait_returns_for_closed_app(self):
        import installer
        import subprocess
        p = subprocess.Popen([sys.executable, '-c', 'pass'])
        p.wait()
        self.assertTrue(installer.wait_for_exit(p.pid, timeout=1))
        p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        try:
            self.assertFalse(installer.wait_for_exit(p.pid, timeout=0.2))
        finally:
            p.kill()
            p.wait()


if __name__ == '__main__':
    unittest.main()
