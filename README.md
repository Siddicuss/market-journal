# Market Journal

A trade journal for your Kalshi. It only reads your history and never places or cancels orders.

## Install

**[⬇ Download MarketJournal-Setup.exe](https://github.com/Siddicuss/market-journal/releases/latest/download/MarketJournal-Setup.exe)**
(newest version). The green **Code** button downloads only the source code,
not the app.

1. Download the installer with the link above, or open **Releases** on the
   right and click **MarketJournal-Setup.exe** under **Assets**.
2. If your browser warns the file isn't commonly downloaded, click **⋯ → Keep
   → Keep anyway**.
3. Open **MarketJournal-Setup.exe** from your Downloads folder.
4. If Windows says "Windows protected your PC", click **More info → Run
   anyway**. This appears because the installer isn't code-signed yet.
5. Click **Install**. No admin rights are needed. It installs to
   `%LOCALAPPDATA%\Programs\Market Journal` and adds Start-menu and Desktop
   shortcuts.

- **Updating:** run a newer Setup over the top. Your journal and key are kept.
  If market Journal is installed, Setup removes it and moves its data over.
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
- **Weekly review:** the week's numbers against the previous week, a day-by-day
  chart, best and worst trades, top tags and mistakes, a written review
  (worked / didn't / focus), and past weeks.

## Data

Everything is stored in `%LOCALAPPDATA%\MarketJournal\` (`journal.db` and a
log). A trade opens when a position leaves zero and closes when it returns to
zero or the market settles. P&L includes fees.

- **Kalshi:** fees come from each fill.

## Updates

Settings → Updates → **Check for updates** looks for a newer release on
GitHub. **Update now** downloads it, closes the app, installs it and reopens
it. Your journal and saved key are kept.

