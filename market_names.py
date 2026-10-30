"""Readable names for market series (Kalshi KXBTC15M or Polymarket pm:btc-up-or-down-5m -> "Bitcoin 15-min up/down")."""
import re

COINS = {
    'BTC': 'Bitcoin', 'ETH': 'Ethereum', 'SOL': 'Solana', 'XRP': 'XRP', 'DOGE': 'Dogecoin',
    'ADA': 'Cardano', 'BNB': 'BNB', 'BCH': 'Bitcoin Cash', 'LTC': 'Litecoin', 'AVAX': 'Avalanche',
    'LINK': 'Chainlink', 'HYPE': 'Hyperliquid', 'SHIBA': 'Shiba Inu', 'DOT': 'Polkadot', 'SUI': 'Sui',
}

RULES = [
    (re.compile(r'^KX(?P<coin>[A-Z]+)15M$'), lambda m: COINS.get(m['coin']) and f"{COINS[m['coin']]} 15-min up/down"),
    (re.compile(r'^KXEARNINGSMENTION(?P<co>[A-Z]+)$'), lambda m: f"{m['co']} earnings call mentions"),
    (re.compile(r'^KXMVE'), lambda m: 'Combos (multi-leg)'),
    (re.compile(r'^pm:(?P<coin>[a-z]+)-up-or-down-(?P<n>\d+)(?P<unit>[mh])$'),
     lambda m: COINS.get(m['coin'].upper()) and f"{COINS[m['coin'].upper()]} {m['n']}-{'min' if m['unit'] == 'm' else 'hour'} up/down"),
]


def tidy(title):
    """Clean up Kalshi's series title: fix ALL CAPS / all lowercase and drop a trailing '?'."""
    title = ' '.join((title or '').split()).rstrip('?').strip()
    if not title:
        return ''
    shouting = title.isupper() and any(len(w) > 4 for w in re.findall(r'[A-Za-z]+', title))  # keep "NHL 2026"
    if shouting or title.islower():
        title = title.lower()
        title = title[0].upper() + title[1:]
    return title


def friendly_name(ticker, kalshi_title=None, custom=None):
    if custom and custom.strip():
        return custom.strip()
    for pattern, make in RULES:
        match = pattern.match(ticker or '')
        if match:
            name = make(match)
            if name:
                return name
    return tidy(kalshi_title) or ticker
