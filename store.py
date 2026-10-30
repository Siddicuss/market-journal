"""SQLite storage for synced Kalshi and Polymarket data and the user's journal entries."""
import json
import sqlite3
import threading
import time
from pathlib import Path

from market_names import friendly_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, order_id TEXT, subaccount INTEGER,
    yes_delta REAL, yes_price REAL, no_price REAL, fee REAL, is_taker INTEGER, ts REAL, raw TEXT);
CREATE INDEX IF NOT EXISTS fills_ticker ON fills(ticker);
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY, title TEXT, subtitle TEXT, series TEXT, event_ticker TEXT,
    open_ts REAL, close_ts REAL, settled_ts REAL, result TEXT, value REAL, status TEXT, raw TEXT);
CREATE TABLE IF NOT EXISTS settlements (
    ticker TEXT PRIMARY KEY, result TEXT, settled_ts REAL, fee REAL, revenue REAL, value REAL, raw TEXT);
CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, open_ts REAL, close_ts REAL, data TEXT);
CREATE TABLE IF NOT EXISTS trade_notes (trade_id TEXT PRIMARY KEY, note TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'tag',
    UNIQUE(name, kind));
CREATE TABLE IF NOT EXISTS trade_tags (
    trade_id TEXT NOT NULL, tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (trade_id, tag_id));
CREATE TABLE IF NOT EXISTS day_notes (day TEXT PRIMARY KEY, note TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS week_reviews (
    week TEXT PRIMARY KEY, worked TEXT, didnt TEXT, focus TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS candles (ticker TEXT PRIMARY KEY, fetched REAL, data TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS series (ticker TEXT PRIMARY KEY, title TEXT, category TEXT, custom_name TEXT);
CREATE TABLE IF NOT EXISTS pm_activity (
    wallet TEXT NOT NULL, ts REAL NOT NULL, type TEXT, condition_id TEXT, asset TEXT, side TEXT, size REAL,
    usdc REAL, price REAL, outcome TEXT, slug TEXT, title TEXT, event_slug TEXT, tx TEXT);
CREATE INDEX IF NOT EXISTS pm_activity_wallet_ts ON pm_activity(wallet, ts);
CREATE TABLE IF NOT EXISTS pm_markets (condition_id TEXT PRIMARY KEY, data TEXT);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys = ON')
        self.db.execute('PRAGMA journal_mode = WAL')
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def _all(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql, args=()):
        with self.lock:
            row = self.db.execute(sql, args).fetchone()
            return dict(row) if row else None

    def _write(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur

    # meta ----------------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self._one('SELECT value FROM meta WHERE key=?', (key,))
        return json.loads(row['value']) if row else default

    def set_meta(self, key, value):
        self._write('INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)', (key, json.dumps(value)))

    # synced data ---------------------------------------------------------
    def upsert_fills(self, fills, raws):
        with self.lock:
            self.db.executemany(
                'INSERT OR REPLACE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                [(f['fill_id'], f['ticker'], f['order_id'], f['subaccount'], f['yes_delta'], f['yes_price'],
                  f['no_price'], f['fee'], f['is_taker'], f['ts'], json.dumps(r)) for f, r in zip(fills, raws)])
            self.db.commit()

    def fills(self):
        return self._all('SELECT fill_id, ticker, order_id, subaccount, yes_delta, yes_price, no_price, fee, '
                         'is_taker, ts FROM fills ORDER BY ts')

    def latest_fill_ts(self):
        row = self._one('SELECT MAX(ts) AS ts FROM fills')
        return row['ts'] if row else None

    def upsert_markets(self, markets):
        with self.lock:
            self.db.executemany(
                'INSERT OR REPLACE INTO markets VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                [(m['ticker'], m.get('title'), m.get('subtitle'), m.get('series'), m.get('event_ticker'),
                  m.get('open_ts'), m.get('close_ts'), m.get('settled_ts'), m.get('result'), m.get('value'),
                  m.get('status'), json.dumps(m.get('raw') or {})) for m in markets])
            self.db.commit()

    def markets(self):
        return {r['ticker']: r for r in self._all('SELECT ticker, title, subtitle, series, event_ticker, open_ts, '
                                                  'close_ts, settled_ts, result, value, status FROM markets')}

    def upsert_settlements(self, rows):
        with self.lock:
            self.db.executemany(
                'INSERT OR REPLACE INTO settlements VALUES (?,?,?,?,?,?,?)',
                [(s['ticker'], s.get('result'), s.get('settled_ts'), s.get('fee'), s.get('revenue'),
                  s.get('value'), json.dumps(s.get('raw') or {})) for s in rows])
            self.db.commit()

    def settlements(self):
        return {r['ticker']: r for r in self._all('SELECT ticker, result, settled_ts, fee, revenue, value FROM settlements')}

    def replace_trades(self, trades):
        with self.lock:
            self.db.execute('DELETE FROM trades')
            self.db.executemany('INSERT INTO trades VALUES (?,?,?,?)',
                                [(t['id'], t['open_ts'], t['close_ts'], json.dumps(t)) for t in trades])
            self.db.commit()

    def trades(self):
        rows = self._all('SELECT data FROM trades ORDER BY open_ts')
        trades = [json.loads(r['data']) for r in rows]
        notes = {r['trade_id']: r['note'] for r in self._all('SELECT trade_id, note FROM trade_notes')}
        tags = {}
        for r in self._all('SELECT tt.trade_id, t.id, t.name, t.kind FROM trade_tags tt JOIN tags t ON t.id = tt.tag_id'):
            tags.setdefault(r['trade_id'], []).append({'id': r['id'], 'name': r['name'], 'kind': r['kind']})
        names = self.series_names()
        for t in trades:
            t.setdefault('platform', 'kalshi')
            t['note'] = notes.get(t['id'], '')
            t['tags'] = sorted(tags.get(t['id'], []), key=lambda x: (x['kind'], x['name'].lower()))
            t['series_name'] = names.get(t['series']) or friendly_name(t['series'])
        return trades

    # polymarket ------------------------------------------------------------
    PM_COLS = ('ts', 'type', 'condition_id', 'asset', 'side', 'size', 'usdc', 'price', 'outcome', 'slug', 'title',
               'event_slug', 'tx')

    def replace_pm_activity(self, wallet, since, rows):
        """Replace the wallet's activity from `since` on (all of it when None) with freshly downloaded rows."""
        with self.lock:
            self.db.execute('DELETE FROM pm_activity WHERE wallet=? AND ts>=?', (wallet, since or 0))
            self.db.executemany(f'INSERT INTO pm_activity(wallet, {", ".join(self.PM_COLS)}) VALUES (?{",?" * len(self.PM_COLS)})',
                                [(wallet, *(r[c] for c in self.PM_COLS)) for r in rows if (since is None or r['ts'] >= since)])
            self.db.commit()

    def pm_activity(self, wallet=None):
        sql = f'SELECT {", ".join(self.PM_COLS)} FROM pm_activity'
        return self._all(sql + (' WHERE wallet=? ORDER BY ts' if wallet else ' ORDER BY ts'), (wallet,) if wallet else ())

    def pm_latest_ts(self, wallet):
        row = self._one('SELECT MAX(ts) AS ts FROM pm_activity WHERE wallet=?', (wallet,))
        return row['ts'] if row else None

    def clear_pm_activity(self, keep_wallet=None):
        self._write('DELETE FROM pm_activity WHERE wallet IS NOT ?', (keep_wallet,))

    def upsert_pm_markets(self, markets):
        with self.lock:
            self.db.executemany('INSERT OR REPLACE INTO pm_markets VALUES (?, ?)',
                                [(m['condition_id'], json.dumps(m)) for m in markets if m.get('condition_id')])
            self.db.commit()

    def pm_markets(self):
        return {r['condition_id']: json.loads(r['data']) for r in self._all('SELECT condition_id, data FROM pm_markets')}

    # market names ----------------------------------------------------------
    def upsert_series(self, ticker, title, category):
        self._write('INSERT INTO series(ticker, title, category) VALUES (?, ?, ?) '
                    'ON CONFLICT(ticker) DO UPDATE SET title=excluded.title, category=excluded.category',
                    (ticker, title, category))

    def series(self):
        """{ticker: {'title', 'category', 'custom_name', 'name'}} for every series fetched."""
        return {r['ticker']: {**r, 'name': friendly_name(r['ticker'], r['title'], r['custom_name'])}
                for r in self._all('SELECT ticker, title, category, custom_name FROM series')}

    def series_names(self):
        return {k: v['name'] for k, v in self.series().items()}

    def set_series_name(self, ticker, name):
        name = ' '.join(str(name or '').split())[:80] or None
        self._write('INSERT INTO series(ticker, custom_name) VALUES (?, ?) '
                    'ON CONFLICT(ticker) DO UPDATE SET custom_name=excluded.custom_name', (ticker, name))

    def trade(self, trade_id):
        for t in self.trades():
            if t['id'] == trade_id:
                return t
        return None

    def candles(self, ticker):
        row = self._one('SELECT fetched, data FROM candles WHERE ticker=?', (ticker,))
        return (row['fetched'], json.loads(row['data'])) if row else (None, None)

    def save_candles(self, ticker, data):
        self._write('INSERT OR REPLACE INTO candles VALUES (?,?,?)', (ticker, time.time(), json.dumps(data)))

    # journal -------------------------------------------------------------
    def set_trade_note(self, trade_id, note):
        self._write('INSERT OR REPLACE INTO trade_notes VALUES (?,?,?)', (trade_id, note, time.time()))

    def tags(self):
        return self._all('SELECT t.id, t.name, t.kind, COUNT(tt.trade_id) AS uses FROM tags t '
                         'LEFT JOIN trade_tags tt ON tt.tag_id = t.id GROUP BY t.id ORDER BY t.kind, lower(t.name)')

    def create_tag(self, name, kind='tag'):
        name = ' '.join(str(name).split())[:60]
        if not name:
            raise ValueError('Tag name is empty.')
        if kind not in ('tag', 'mistake'):
            raise ValueError('Unknown tag kind.')
        with self.lock:
            self.db.execute('INSERT OR IGNORE INTO tags(name, kind) VALUES (?, ?)', (name, kind))
            self.db.commit()
            return dict(self.db.execute('SELECT id, name, kind FROM tags WHERE name=? AND kind=?', (name, kind)).fetchone())

    def rename_tag(self, tag_id, name):
        name = ' '.join(str(name).split())[:60]
        if not name:
            raise ValueError('Tag name is empty.')
        self._write('UPDATE tags SET name=? WHERE id=?', (name, tag_id))

    def delete_tag(self, tag_id):
        self._write('DELETE FROM tags WHERE id=?', (tag_id,))

    def set_trade_tags(self, trade_id, tag_ids):
        with self.lock:
            self.db.execute('DELETE FROM trade_tags WHERE trade_id=?', (trade_id,))
            self.db.executemany('INSERT OR IGNORE INTO trade_tags VALUES (?, ?)', [(trade_id, int(i)) for i in tag_ids])
            self.db.commit()

    def day_note(self, day):
        row = self._one('SELECT note FROM day_notes WHERE day=?', (day,))
        return row['note'] if row else ''

    def day_notes(self):
        return {r['day']: r['note'] for r in self._all('SELECT day, note FROM day_notes') if r['note']}

    def set_day_note(self, day, note):
        self._write('INSERT OR REPLACE INTO day_notes VALUES (?,?,?)', (day, note, time.time()))

    def week_review(self, week):
        return self._one('SELECT worked, didnt, focus, updated FROM week_reviews WHERE week=?', (week,)) or \
            {'worked': '', 'didnt': '', 'focus': '', 'updated': None}

    def week_reviews(self):
        return {r['week']: r for r in self._all('SELECT week, worked, didnt, focus, updated FROM week_reviews')}

    def set_week_review(self, week, worked, didnt, focus):
        self._write('INSERT OR REPLACE INTO week_reviews VALUES (?,?,?,?,?)', (week, worked, didnt, focus, time.time()))
