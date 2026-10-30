# Market Journal — Claude Code notes

A Tradervue-style trade journal for the user's **live** Kalshi account and,
optionally, a Polymarket wallet (no paper or demo). It was called Kalshi
Journal until v1.1 (Oct 28, 2026). It is a separate project from the trading
bot in `..\kalshi-local-trader` and does not import its code.

## Rules

- **Read-only:** `kalshi_client.ReadOnlyClient` refuses anything but GET, and
  `polymarket_client.PublicClient` only sends GETs to public endpoints. Never
  add order, cancel or transfer calls.
- **Keys:** never open, print or copy key files or the DPAPI files
  (`%LOCALAPPDATA%\MarketJournal\credentials.json`,
  `%LOCALAPPDATA%\KalshiLocalTrader\profile.json`).
  - The app starts with no key. The user enters one in Settings and it is
    saved, DPAPI-encrypted, to `credentials.json` only.
  - It never reads the bot's profile.
  - The Polymarket wallet address is public. It is stored in `journal.db` meta
    (`pm_wallet`).
- **Git:** don't commit or push unless asked.
- **The real journal:** test writes (notes, tags, reviews) on a copy of
  `journal.db`, not the real one.

## Layout

- `main.py` starts a local `http.server` (`server.py`) on 127.0.0.1.
  - It shows the server in the app's own native window, using pywebview with
    WebView2 (`gui='edgechromium'`, through pythonnet).
  - The app quits when its window closes. Launching it a second time brings
    the existing window to the front.
  - On first run it renames `%LOCALAPPDATA%\KalshiJournal` to `MarketJournal`
    (`credentials.migrate_old_data`).
- **Kalshi:**
  - `sync.py` downloads fills (`/portfolio/fills`, plus `/historical/fills`
    before the cutoff), settlements and market details.
  - `trades.py` groups fills into trades as a signed YES position. A
    settlement's `fee_cost` repeats the fill fees, so it is not added again.
- **Polymarket:**
  - `polymarket.py` downloads the wallet's `data-api /v2/activity` (cursor
    paged). Each sync replaces the rows from one day before the latest one.
  - Market details come from `gamma-api /markets`, which needs `closed=true`
    for resolved markets. Price charts come from `clob /prices-history`.
  - Trades are tracked per outcome token: buy/split opens, sell/merge closes,
    and resolution settles. Amounts are the USDC that moved, and
    fee = |usdc − size × price|.
  - Trade ids are `pm:<tx>:<token tail>`, so notes survive resyncs. Series
    keys are `pm:<series slug>`.
- **Sync:** `server.App.start_sync` runs each connected platform and keeps
  going if one fails, then `sync.rebuild` merges both into the `trades` table.
  Each trade has `platform`.
- `stats.py` computes the dashboard, reports, calendar and weekly review.
  `store.py` is the SQLite storage. `market_names.py` makes the friendly
  series names.
- `web/` is the UI: plain JS with SVG charts and no external libraries.

## Not supported

- **Polymarket US** (api.polymarket.us) needs an Ed25519 key. Its trade
  objects have no side or fee field, so it needs a real account's responses
  before building.
- **DraftKings Predictions** has no public API.

## Commands

```
.venv\Scripts\python.exe -m unittest discover -p "test_*.py"   # tests
build_journal.cmd                                               # tests + app + installer
dist\MarketJournal-Setup.exe /S [/NODESKTOP]                    # silent install
"%LOCALAPPDATA%\Programs\Market Journal\uninstall.exe" /S       # silent uninstall, keeps data
```

- **Build output:**
  - the app: `dist\MarketJournal\` (PyInstaller, one folder)
  - the installer: `dist\MarketJournal-Setup.exe` (`installer.py`, one file,
    with the app folder zipped inside)
- **The installer (`installer.py`):**
  - It installs per user to `%LOCALAPPDATA%\Programs\Market Journal`, with no
    admin rights.
  - It creates the shortcuts and registers under HKCU `...\Uninstall\MarketJournal`.
  - It removes an old Kalshi Journal install (shortcuts, registry, folder) and
    moves its data (`remove_old_name`).
  - It only replaces or deletes a folder that holds our app (`is_our_install`).
  - The uninstaller runs from a temp copy so it can remove its own folder.
  - Tests stub `unregister`, `registered_dir` and the paths, so they never
    touch the real registry.
- **Rebuilding:** close the running app first, because its `.exe` is locked
  while it runs. Bump `installer.VERSION` for each release.

## Verified Oct 28, 2026

- **Kalshi:** per-market realized P&L and fees matched Kalshi's
  `/portfolio/positions` + `/historical/positions` on all 318 closed markets.
- **Polymarket:** P&L on 4 public wallets matched Polymarket's
  `/closed-positions` `realizedPnl` within 2¢ per position.
