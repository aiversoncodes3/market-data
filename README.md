# market-data

Market data for the **Market Narrative** dashboard, pulled from Yahoo Finance by a GitHub Actions job every weekday morning.

## How it works

1. `.github/workflows/fetch-market-data.yml` runs on a schedule (weekdays, about 6:15 AM Toronto time, plus a second run an hour later) and whenever you press **Run workflow** on the Actions tab.
2. It runs `fetch.py`, which writes the files in `data/`:
   - `prices.json`: daily closes for holdings, watchlist and index ETFs (last completed session first)
   - `live.json`: index futures overnight, and pre-market prices for stocks
   - `yields.json`: 3-month, 5-, 10- and 30-year Treasury yields
   - `oil.json`: WTI and Brent
   - `meta.json`: when it last ran and anything that failed
3. Claude's 7:07 AM refresh reads `data/` and updates the dashboard.

## Common changes

- **Add or remove tickers:** edit the lists at the top of `fetch.py` (`HOLDINGS`, `WATCHLIST`, ...). Committing the change runs the job straight away. The dashboard shows new tickers on its next refresh.
- **Change the schedule:** edit the `cron:` lines in the workflow file. Times are in UTC (Toronto is UTC-4 in summer, UTC-5 in winter).
- **Run it now:** Actions tab > *Fetch market data* > **Run workflow**.
- **See what happened:** Actions tab > click a run > *fetch* > expand *Pull data from Yahoo Finance*.

Data comes from Yahoo Finance through the unofficial `yfinance` library, for personal use.
