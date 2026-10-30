"""Local HTTP server for the Market Journal window. Binds to 127.0.0.1 only."""
import csv
import io
import json
import logging
import mimetypes
import secrets
import threading
import time
import traceback
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import credentials
import polymarket
import stats
import updater
from kalshi_client import KalshiError, ReadOnlyClient
from polymarket_client import PolymarketError, PublicClient, validate_address
from sync import candles_for, rebuild, sync
from updater import UpdateError
from version import VERSION

log = logging.getLogger('journal')
FILTER_KEYS = ('start', 'end', 'platform', 'series', 'side', 'outcome', 'status', 'tag', 'search')
PLATFORMS = {'kalshi': 'Kalshi', 'polymarket': 'Polymarket'}
DONATE_URL = 'https://www.paypal.com/donate/?business=DCQT2RXMBW7B2&no_recurring=0&currency_code=USD'
AUTO_SYNC_SECONDS = 300
POLYMARKET_ENABLED = False  # shown as "Coming soon" in Settings; the code is kept for later


class App:
    """Shared state behind the HTTP handlers."""

    def __init__(self, store, web_dir, client_factory=ReadOnlyClient, creds=credentials, pm_factory=PublicClient,
                 open_url=webbrowser.open, updates=updater, quit=lambda: None,
                 polymarket_enabled=POLYMARKET_ENABLED):
        self.store = store
        self.web_dir = Path(web_dir)
        self.token = secrets.token_urlsafe(24)
        self.client_factory = client_factory
        self.creds = creds
        self.pm_factory = pm_factory
        self.open_url = open_url
        self.polymarket_enabled = polymarket_enabled
        self.updates = updates
        self.quit = quit  # closes the window so the installer can replace the app
        self.update_state = {'running': False, 'message': '', 'error': None}
        self.pm = None
        self.client = None
        self.source = None
        self.sync_lock = threading.Lock()
        self.sync_state = {'running': False, 'message': '', 'error': None, 'result': None}
        self.last_seen = time.time()
        self._connect()
        if not polymarket_enabled and any(t['platform'] == 'polymarket' for t in store.trades()):
            rebuild(store, False)  # drop trades from a wallet connected before Polymarket was switched off

    @property
    def configured(self):
        return bool(self.client or self.pm)

    def _connect(self):
        wallet = self.store.get_meta('pm_wallet') if self.polymarket_enabled else None
        self.pm = self.pm_factory(wallet) if wallet else None
        found = self.creds.load()
        if not found:
            self.client, self.source = None, None
            return
        key, pem, self.source = found
        try:
            self.client = self.client_factory(key, pem)
        except Exception:
            self.client, self.source = None, None

    # sync ----------------------------------------------------------------
    def start_sync(self):
        if not self.configured:
            raise ValueError('Connect Kalshi in Settings first.' if not self.polymarket_enabled else
                             'Connect Kalshi or Polymarket in Settings first.')
        if not self.sync_lock.acquire(blocking=False):
            return False
        self.sync_state.update(running=True, message='Starting sync', error=None)

        def run():
            progress = lambda m: self.sync_state.update(message=m)
            errors, done = [], []
            jobs = [('Kalshi', self.client, lambda c: sync(c, self.store, progress)),
                    ('Polymarket', self.pm, lambda c: polymarket.sync(c, self.store, progress))]
            try:
                for name, client, job in jobs:
                    if not client:
                        continue
                    try:
                        job(client)
                        done.append(name)
                    except (KalshiError, PolymarketError) as exc:
                        errors.append(f'{name}: {exc}')
                    except Exception as exc:
                        log.error('%s sync failed\n%s', name, traceback.format_exc())
                        errors.append(f'{name} sync failed: {exc}')
                trades = rebuild(self.store, self.polymarket_enabled)
                self.store.set_meta('last_sync', time.time())
                self.sync_state.update(result={'trades': len(trades)}, error=' · '.join(errors) or None,
                                       message='Sync failed' if errors and not done else
                                       f"Synced {' and '.join(done)}" + (' (with errors)' if errors else ''))
            except Exception as exc:
                log.error('Sync failed\n%s', traceback.format_exc())
                self.sync_state.update(error=f'Sync failed: {exc}', message='Sync failed')
            finally:
                self.sync_state['running'] = False
                self.sync_lock.release()
        threading.Thread(target=run, daemon=True, name='sync').start()
        return True

    # updates -------------------------------------------------------------
    def start_update(self):
        if not self.updates.installed():
            raise ValueError('Updates install from the installed app, not from source.')
        if self.update_state['running']:
            return False
        info = self.updates.check()
        if not info['available']:
            raise ValueError(f'Market Journal {VERSION} is the newest version.')
        self.update_state.update(running=True, message='Downloading…', error=None)

        def progress(done, total):
            if total:
                self.update_state['message'] = f'Downloading… {done * 100 // total}%'

        def run():
            try:
                setup = self.updates.download(info['release'], progress)
                self.update_state['message'] = 'Installing. Market Journal will reopen in a moment.'
                self.updates.launch_installer(setup)
                time.sleep(1.5)  # let the page show the message
                self.quit()
            except Exception as exc:
                log.error('Update failed: %s', exc)
                self.update_state.update(running=False, message='',
                                         error=str(exc) if isinstance(exc, UpdateError) else f'The update failed: {exc}')

        threading.Thread(target=run, daemon=True, name='update').start()
        return True

    def auto_sync_loop(self):
        while True:
            time.sleep(30)
            last = self.store.get_meta('last_sync') or 0
            if self.configured and time.time() - last > AUTO_SYNC_SECONDS and not self.sync_state['running'] \
                    and not self.sync_state['error']:
                try:
                    self.start_sync()
                except Exception:
                    pass

    # helpers -------------------------------------------------------------
    def filtered(self, q):
        return stats.filter_trades(self.store.trades(), {k: q.get(k) for k in FILTER_KEYS if q.get(k)})

    def market_for(self, t):
        if t['platform'] != 'polymarket':
            return self.store.markets().get(t['ticker'])
        m = self.store.pm_markets().get(t['condition_id'])
        return m and {'subtitle': '', 'close_ts': m.get('end_ts'), 'result': m.get('winner'), 'title': m.get('question')}

    @staticmethod
    def slim(t):
        return {k: v for k, v in t.items() if k != 'fills'}

    # routes --------------------------------------------------------------
    def get(self, path, q):
        s = self.store
        if path == '/api/status':
            trades = s.trades()
            info = s.series()
            series = {t['series']: t['series_name'] for t in trades}
            platform = {t['series']: t['platform'] for t in trades}
            return {'version': VERSION, 'update': self.update_state, 'configured': self.configured, 'polymarket_enabled': self.polymarket_enabled, 'kalshi': bool(self.client), 'source': self.source,
                    'polymarket': self.pm.address if self.pm else None,
                    'pm_value': s.get_meta('pm_value') if self.pm else None, 'last_sync': s.get_meta('last_sync'),
                    'balance': s.get_meta('balance') if self.client else None, 'sync': self.sync_state, 'trades': len(trades),
                    'platforms': [{'id': k, 'name': v, 'trades': sum(t['platform'] == k for t in trades)}
                                  for k, v in PLATFORMS.items() if any(t['platform'] == k for t in trades)],
                    'series': [{'ticker': k, 'name': v, 'platform': PLATFORMS[platform[k]],
                                'kalshi_title': (info.get(k) or {}).get('title') or '',
                                'custom': bool((info.get(k) or {}).get('custom_name')),
                                'trades': sum(t['series'] == k for t in trades)}
                               for k, v in sorted(series.items(), key=lambda kv: kv[1].lower())]}
        if path == '/api/dashboard':
            trades = self.filtered(q)
            open_trades = [self.slim(t) for t in trades if t['status'] == 'open']
            recent = sorted(stats.closed(trades), key=lambda t: -t['close_ts'])[:8]
            days = stats.by_day(trades)
            return {'summary': stats.summary(trades), 'equity': stats.equity_curve(trades),
                    'open': open_trades, 'recent': [self.slim(t) for t in recent],
                    'days': [{'day': d, **v} for d, v in sorted(days.items())][-30:],
                    'by_series': stats.breakdown(trades, 'series')}
        if path == '/api/trades':
            trades = self.filtered(q)
            return {'trades': [self.slim(t) for t in reversed(trades)], 'summary': stats.summary(trades)}
        if path == '/api/trade':
            t = s.trade(q.get('id'))
            if not t:
                raise LookupError('Trade not found.')
            return {'trade': t, 'market': self.market_for(t)}
        if path == '/api/update':
            info = self.updates.check()
            return {k: info[k] for k in ('current', 'latest', 'available')} | {'notes': info['release']['notes']}
        if path == '/api/candles':
            t = s.trade(q.get('id'))
            if not t:
                raise LookupError('Trade not found.')
            if t['platform'] == 'polymarket':
                if not self.pm:
                    return {'points': s.candles('pm:' + t['token_id'])[1] or [], 'direct': True}
                return {'points': polymarket.prices_for(self.pm, s, t, s.pm_markets().get(t['condition_id'])), 'direct': True}
            if not self.client:
                return {'points': s.candles(t['ticker'])[1] or []}
            return {'points': candles_for(self.client, s, t, s.markets().get(t['ticker']))}
        if path == '/api/tags':
            return {'tags': s.tags()}
        if path == '/api/summary':
            return {'summary': stats.summary(self.filtered(q))}
        if path == '/api/reports':
            trades = self.filtered(q)
            group = q.get('group') or 'series'
            if group not in stats.GROUPS:
                raise ValueError('Unknown report.')
            return {'rows': stats.breakdown(trades, group), 'summary': stats.summary(trades),
                    'calibration': stats.calibration(trades)}
        if path == '/api/calendar':
            today = date.today()
            year, month = int(q.get('year') or today.year), int(q.get('month') or today.month)
            return stats.calendar_month(s.trades(), year, month, s.day_notes())
        if path == '/api/day':
            day = q.get('day') or date.today().isoformat()
            trades = [t for t in stats.closed(s.trades()) if stats.day_key(t['close_ts']) == day]
            return {'day': day, 'note': s.day_note(day), 'trades': [self.slim(t) for t in trades],
                    'summary': stats.summary(trades)}
        if path == '/api/weeks':
            return {'weeks': stats.week_list(s.trades(), s.week_reviews())}
        if path == '/api/week':
            week = stats.week_start(q.get('week') or date.today().isoformat()).isoformat()
            return stats.week_report(s.trades(), week, s.week_review(week))
        raise LookupError('Not found.')

    def post(self, path, body):
        s = self.store
        if path == '/api/sync':
            return {'started': self.start_sync()}
        if path == '/api/update':
            return {'started': self.start_update()}
        if path == '/api/donate':
            self.open_url(DONATE_URL)  # the user's browser, so the journal window stays put
            return {'ok': True}
        if path == '/api/credentials':
            key, pem = body.get('key', ''), body.get('pem', '')
            self.creds.validate(key, pem)
            client = self.client_factory(key.strip(), pem)
            client.balance()  # prove the key works before saving it
            self.creds.save(key, pem)
            self._connect()
            self.sync_state['error'] = None
            return {'ok': True, 'source': self.source}
        if path == '/api/polymarket':
            if not self.polymarket_enabled:
                raise ValueError('Polymarket is coming soon.')
            address = validate_address(body.get('address'))
            client = self.pm_factory(address)
            client.value()  # make sure Polymarket answers before saving
            if address != s.get_meta('pm_wallet'):
                s.clear_pm_activity(keep_wallet=address)
            s.set_meta('pm_wallet', address)
            self._connect()
            self.sync_state['error'] = None
            return {'ok': True, 'address': address}
        if path == '/api/polymarket/forget':
            s.set_meta('pm_wallet', None)
            s.clear_pm_activity()
            self._connect()
            rebuild(s, self.polymarket_enabled)
            return {'ok': True, 'configured': self.configured}
        if path == '/api/credentials/forget':
            self.creds.forget()
            self._connect()
            return {'ok': True, 'configured': self.configured, 'source': self.source}
        if path == '/api/trade/note':
            s.set_trade_note(body['id'], str(body.get('note', ''))[:20000])
            return {'ok': True}
        if path == '/api/trade/tags':
            s.set_trade_tags(body['id'], body.get('tag_ids') or [])
            return {'ok': True}
        if path == '/api/tags':
            return {'tag': s.create_tag(body.get('name', ''), body.get('kind', 'tag'))}
        if path == '/api/tags/rename':
            s.rename_tag(int(body['id']), body.get('name', ''))
            return {'ok': True}
        if path == '/api/tags/delete':
            s.delete_tag(int(body['id']))
            return {'ok': True}
        if path == '/api/day/note':
            s.set_day_note(body['day'], str(body.get('note', ''))[:20000])
            return {'ok': True}
        if path == '/api/week/review':
            week = stats.week_start(body['week']).isoformat()
            s.set_week_review(week, *(str(body.get(k, ''))[:20000] for k in ('worked', 'didnt', 'focus')))
            return {'ok': True}
        if path == '/api/series/name':
            s.set_series_name(str(body['ticker']), body.get('name', ''))
            return {'ok': True, 'name': s.series_names().get(str(body['ticker']))}
        if path == '/api/rebuild':
            return {'trades': len(rebuild(s, self.polymarket_enabled))}
        raise LookupError('Not found.')

    def export_csv(self, q):
        out = io.StringIO()
        cols = ['open_time', 'close_time', 'platform', 'market', 'ticker', 'title', 'side', 'status', 'close_reason', 'contracts',
                'entry_price', 'exit_price', 'cost', 'fees', 'pnl_gross', 'pnl', 'return_pct', 'hold_minutes',
                'minutes_left', 'market_result', 'tags', 'mistakes', 'note']
        writer = csv.writer(out)
        writer.writerow(cols)
        fmt = lambda ts: stats.local(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else ''
        for t in self.filtered(q):
            writer.writerow([fmt(t['open_ts']), fmt(t['close_ts']), PLATFORMS[t['platform']], t['series_name'], t['ticker'], t['title'], t['side'].upper(),
                             t['status'], t['close_reason'] or '', t['contracts'], t['entry_price'],
                             t['exit_price'] if t['exit_price'] is not None else '', round(t['cost'], 4),
                             round(t['fees'], 4), round(t['pnl_gross'], 4), round(t['pnl'], 4),
                             round(t['return_pct'], 2),
                             round(t['hold_seconds'] / 60, 2) if t['hold_seconds'] is not None else '',
                             round(t['minutes_left'], 2) if t['minutes_left'] is not None else '',
                             t['market_result'] or '',
                             '; '.join(x['name'] for x in t['tags'] if x['kind'] == 'tag'),
                             '; '.join(x['name'] for x in t['tags'] if x['kind'] == 'mistake'),
                             t.get('note', '')])
        return out.getvalue().encode('utf-8-sig')


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'MarketJournal'

        def log_message(self, *args):
            pass

        def _send(self, status, body, ctype='application/json; charset=utf-8', extra=None):
            self.send_response(status)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, value):
            self._send(status, json.dumps(value).encode())

        def _authorized(self, q):
            return secrets.compare_digest(self.headers.get('X-Token') or q.get('t') or '', app.token)

        def _query(self):
            parsed = urlparse(self.path)
            return parsed.path, {k: v[-1] for k, v in parse_qs(parsed.query).items()}

        def _guard(self, fn):
            try:
                fn()
            except (LookupError, KeyError) as exc:
                self._json(404, {'error': str(exc).strip("'") or 'Not found.'})
            except (ValueError, PermissionError) as exc:
                self._json(400, {'error': str(exc)})
            except (KalshiError, PolymarketError, UpdateError) as exc:
                self._json(502, {'error': str(exc)})
            except Exception as exc:
                log.error('Request failed: %s\n%s', self.path, traceback.format_exc())
                self._json(500, {'error': f'Something went wrong: {exc}'})

        def do_GET(self):
            path, q = self._query()
            app.last_seen = time.time()
            if path.startswith('/api/'):
                if not self._authorized(q):
                    return self._json(403, {'error': 'Forbidden'})
                if path == '/api/export.csv':
                    stamp = date.today().isoformat()
                    return self._guard(lambda: self._send(200, app.export_csv(q), 'text/csv; charset=utf-8',
                                                          {'Content-Disposition': f'attachment; filename="market-journal-trades-{stamp}.csv"'}))
                return self._guard(lambda: self._json(200, app.get(path, q)))
            name = 'index.html' if path in ('/', '') else path.lstrip('/')
            file = (app.web_dir / name).resolve()
            if app.web_dir.resolve() not in file.parents or not file.is_file():
                return self._send(404, b'Not found', 'text/plain')
            ctype = mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype in ('application/javascript',):
                ctype += '; charset=utf-8'
            self._send(200, file.read_bytes(), ctype)

        def do_POST(self):
            path, q = self._query()
            app.last_seen = time.time()
            if not self._authorized(q):
                return self._json(403, {'error': 'Forbidden'})
            if path == '/api/heartbeat':
                return self._json(200, {'ok': True})
            length = int(self.headers.get('Content-Length') or 0)
            if length > 2_000_000:
                return self._json(413, {'error': 'Request too large.'})
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except ValueError:
                return self._json(400, {'error': 'Bad request.'})
            self._guard(lambda: self._json(200, app.post(path, body)))

    return Handler


def serve(app, port=0):
    httpd = ThreadingHTTPServer(('127.0.0.1', port), make_handler(app))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name='http').start()
    return httpd
