"""Read-only Kalshi client. It can only send GET requests."""
import base64
import json
import random
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlparse

BASE = 'https://external-api.kalshi.com/trade-api/v2'


class KalshiError(RuntimeError):
    def __init__(self, status, message=''):
        self.status = status
        super().__init__(message or f'Kalshi returned HTTP {status}')


class ReadOnlyClient:
    def __init__(self, key_id, pem, base=BASE, opener=None):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        self.key_id = key_id
        self.private_key = load_pem_private_key(pem.encode(), password=None)
        self.base = base
        self.opener = opener or urllib.request.urlopen

    def _headers(self, path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        stamp = str(int(time.time() * 1000))
        signature = self.private_key.sign(
            (stamp + 'GET' + path).encode(),
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())
        return {'KALSHI-ACCESS-KEY': self.key_id, 'KALSHI-ACCESS-TIMESTAMP': stamp,
                'KALSHI-ACCESS-SIGNATURE': base64.b64encode(signature).decode(),
                'Accept': 'application/json', 'User-Agent': 'MarketJournal/1.1'}

    def request(self, method, path, **params):
        if method != 'GET':
            raise PermissionError('The journal is read-only and never sends orders.')
        url = self.base + path + ('?' + urlencode({k: v for k, v in params.items() if v is not None}) if params else '')
        for attempt in range(4):
            req = urllib.request.Request(url, headers=self._headers(urlparse(url).path), method='GET')
            try:
                with self.opener(req, timeout=20) as response:
                    return json.loads(response.read() or b'{}')
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt + random.random())
                    continue
                detail = ''
                try:
                    detail = json.loads(exc.read() or b'{}').get('error', {}).get('message', '')
                except Exception:
                    pass
                if exc.code == 401:
                    raise KalshiError(401, 'Kalshi rejected the API key. Check the key ID and key file in Settings.') from None
                raise KalshiError(exc.code, f'Kalshi returned HTTP {exc.code}' + (f': {detail}' if detail else '')) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < 3:
                    time.sleep(2 ** attempt + random.random())
                    continue
                raise KalshiError(0, f'Could not reach Kalshi ({exc}). Check your internet connection.') from None

    def get(self, path, **params):
        return self.request('GET', path, **params)

    def pages(self, path, key, **params):
        rows, params = [], dict(params)
        for _ in range(10000):
            result = self.get(path, **params)
            rows.extend(result.get(key) or [])
            cursor = result.get('cursor')
            if not cursor:
                return rows
            params['cursor'] = cursor
        raise KalshiError(0, 'Kalshi pagination did not finish.')

    # Endpoints the journal uses -------------------------------------------------
    def balance(self):
        return self.get('/portfolio/balance')

    def fills(self, min_ts=None):
        return self.pages('/portfolio/fills', 'fills', min_ts=min_ts, limit=100)

    def historical_fills(self):
        return self.pages('/historical/fills', 'fills', limit=100)

    def settlements(self, min_ts=None):
        return self.pages('/portfolio/settlements', 'settlements', min_ts=min_ts, limit=100)

    def markets(self, tickers):
        return self.pages('/markets', 'markets', tickers=','.join(tickers), limit=1000)

    def market(self, ticker, historical=False):
        prefix = '/historical/markets/' if historical else '/markets/'
        return self.get(prefix + quote(ticker, safe='')).get('market') or {}

    def series(self, ticker):
        return self.get('/series/' + quote(ticker, safe='')).get('series') or {}

    def candles(self, series, ticker, start_ts, end_ts, historical=False):
        path = ('/historical/markets/' + quote(ticker, safe='') if historical
                else '/series/' + quote(series, safe='') + '/markets/' + quote(ticker, safe='')) + '/candlesticks'
        return self.get(path, start_ts=int(start_ts), end_ts=int(end_ts), period_interval=1).get('candlesticks') or []
