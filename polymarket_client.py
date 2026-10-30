"""Read-only Polymarket client. Polymarket's history is public, so it needs a wallet address, not a key."""
import json
import random
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

DATA = 'https://data-api.polymarket.com'
GAMMA = 'https://gamma-api.polymarket.com'
CLOB = 'https://clob.polymarket.com'
ADDRESS = re.compile(r'^0x[0-9a-fA-F]{40}$')


class PolymarketError(RuntimeError):
    def __init__(self, status, message=''):
        self.status = status
        super().__init__(message or f'Polymarket returned HTTP {status}')


def validate_address(address):
    address = (address or '').strip()
    if not ADDRESS.match(address):
        raise ValueError('Enter your Polymarket wallet address: 0x followed by 40 letters and numbers. '
                         'Find it on polymarket.com under your profile.')
    return address.lower()


class PublicClient:
    def __init__(self, address, opener=None):
        self.address = validate_address(address)
        self.opener = opener or urllib.request.urlopen

    def get(self, base, path, params=()):
        url = base + path + ('?' + urlencode([(k, v) for k, v in params if v is not None]) if params else '')
        for attempt in range(4):
            req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'MarketJournal/1.1'})
            try:
                with self.opener(req, timeout=20) as response:
                    return json.loads(response.read() or b'null')
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt + random.random())
                    continue
                raise PolymarketError(exc.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt + random.random())
                    continue
                raise PolymarketError(0, f'Could not reach Polymarket ({exc}). Check your internet connection.') from None

    def activity(self, start=None):
        """Every activity row for the wallet since `start` (epoch seconds), oldest first."""
        rows, cursor = [], None
        for _ in range(10000):
            page = self.get(DATA, '/v2/activity', [('user', self.address), ('limit', 500), ('start', start),
                                                   ('sortDirection', 'ASC'), ('cursor', cursor)])
            rows.extend(page.get('data') or [])
            cursor = (page.get('pagination') or {}).get('next_cursor')
            if not cursor:
                return rows
        raise PolymarketError(0, 'Polymarket pagination did not finish.')

    def markets(self, condition_ids):
        """Market details for up to ~20 condition IDs. Gamma lists open and closed markets separately."""
        ids = [('condition_ids', c) for c in condition_ids]
        out = {}
        for closed in ('false', 'true'):
            for m in self.get(GAMMA, '/markets', ids + [('closed', closed), ('limit', len(ids))]) or []:
                out[m.get('conditionId')] = m
        return list(out.values())

    def value(self):
        rows = self.get(DATA, '/value', [('user', self.address)]) or []
        return float(rows[0].get('value') or 0) if rows else 0.0

    def prices(self, token_id, start_ts, end_ts):
        data = self.get(CLOB, '/prices-history', [('market', token_id), ('startTs', int(start_ts)),
                                                  ('endTs', int(end_ts)), ('fidelity', 1)]) or {}
        return data.get('history') or []
