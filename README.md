# Market Journal

A trade journal for your Kalshi and Polymarket accounts, in the style of
Tradervue. It only reads your history and never places or cancels orders.
(It was called Kalshi Journal before version 1.1.)

## Install

Run **`MarketJournal-Setup.exe`**. Setup asks for no admin rights. It installs
to `%LOCALAPPDATA%\Programs\Market Journal` and adds a Start-menu shortcut,
plus a Desktop shortcut unless you untick it.

- **Windows warning:** Windows may say "Windows protected your PC" because the
  installer isn't code-signed. Click **More info → Run anyway**.
- **Updating:** run a newer Setup over the top. Your journal and key are kept.
  If Kalshi Journal is installed, Setup removes it and moves its data over.
- **Uninstalling:** go to **Settings → Apps → Installed apps → Market Journal →
  Uninstall**. It asks whether to also delete your journal data and saved key;
  the default is to keep them.

## Open it

Open **Market Journal** from the Start menu or Desktop. It runs in its own
window, syncs right away, then syncs every 5 minutes while open. Closing the
window quits it.

It starts with no accounts connected. Open **Settings** and connect one or
both:

- **Kalshi:** enter your key ID and private key file (kalshi.com → Account &
  security → API keys). The key is tested, then saved encrypted with your
  Windows login. **Remove saved key** clears it.
- **Polymarket:** enter your wallet address (polymarket.com → your profile →
  the 0x… address). Polymarket trade history is public, so no key is needed.
  This covers polymarket.com accounts. Polymarket US accounts aren't
  supported yet.

DraftKings Predictions has no public API, so it can't be connected.

## Features

- **Dashboard:** net P&L, win rate, profit factor, expectancy, average win and
  loss, fees, max drawdown, equity curve, daily P&L, results by market, open
  positions.
- **Trades:** filter by platform, market, side, result or tag; sort, search
  and export to CSV.
- **Trade page:** price chart with your buys, sells and settlement marked, all
  fills, notes, tags and mistakes.
- **Calendar:** each day colored by P&L, with a daily journal entry and weekly
  totals.
- **Reports:** breakdowns by platform, market, side, tag, mistake, entry
  price, time left at entry, hold time, size, hour, weekday, exit type and
  maker/taker. Also price vs. outcome, and an A/B compare.
- **Donate:** a PayPal button at the bottom of the sidebar opens PayPal in
  your web browser.
- **Weekly review:** the week's numbers against the previous week, a day-by-day
  chart, best and worst trades, top tags and mistakes, a written review
  (worked / didn't / focus), and past weeks.

## Data

Everything is stored in `%LOCALAPPDATA%\MarketJournal\` (`journal.db` and a
log). A trade opens when a position leaves zero and closes when it returns to
zero or the market settles. P&L includes fees.

- **Kalshi:** fees come from each fill.
- **Polymarket:** P&L uses the USDC that actually moved. The fee shown is the
  difference between that and size × price. Rewards and rebates aren't counted.

## Rebuild after code changes

```
build_journal.cmd
```

The first run creates `.venv` (Python 3.14) and installs `requirements.txt`.

The script:

1. runs the tests
2. builds the app (`dist\MarketJournal\`)
3. builds the one-file installer, **`dist\MarketJournal-Setup.exe`**, which is
   the file to share

Tests only:

```
.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
```
