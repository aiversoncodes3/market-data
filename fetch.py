"""Pull market data from Yahoo Finance (via yfinance) for the Market Narrative dashboard.

Writes JSON files to data/ in the exact shape the dashboard reads:
  data/prices.json  daily closes: {ticker, group, lag, date, close}
                    lag 0 = last COMPLETED session; lags 0-21 and 63 are kept
  data/live.json    latest price incl. pre-market / overnight futures:
                    {ticker, name, kind, price, prev_close, chg, as_of, session}
  data/yields.json  daily Treasury yields (%), last 6 months: {date, y3m, y5, y10, y30}
  data/oil.json     daily WTI and Brent (US$/bbl), last 6 months: {date, wti, brent}
  data/meta.json    when it ran and anything that failed

To change what's tracked, edit the lists below.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------- what to track
HOLDINGS = ["GOOG", "MSFT", "NVDA", "TSM", "SMH"]
WATCHLIST = ["MU", "CRM", "AAPL", "AVGO", "VST", "BE"]
INDEX_ETFS = ["SPY", "QQQ", "DIA", "IWM", "XLK", "RSP", "QQEW"]
FUTURES = {"ES=F": "S&P 500 futures", "NQ=F": "Nasdaq-100 futures",
           "YM=F": "Dow futures", "RTY=F": "Russell 2000 futures"}
YIELDS = {"y3m": "^IRX", "y5": "^FVX", "y10": "^TNX", "y30": "^TYX"}
OIL = {"wti": "CL=F", "brent": "BZ=F"}
# --------------------------------------------------------------------------------

ET = ZoneInfo("America/New_York")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
KEEP_LAGS = set(range(22)) | {63}
NOW = datetime.now(ET)
START = time.time()
BUDGET_SECONDS = 360  # stop calling Yahoo after 6 minutes so the job can still save what it has
errors = []


def retry(fn, what, tries=3):
    last = None
    for i in range(tries):
        if time.time() - START > BUDGET_SECONDS:
            errors.append(f"{what}: skipped, time budget used up")
            return None
        try:
            out = fn()
            if out is not None and not (hasattr(out, "empty") and out.empty):
                return out
            raise ValueError("empty result")
        except Exception as e:  # noqa: BLE001 - Yahoo fails in many ways
            last = e
            time.sleep(3 * (i + 1))
    errors.append(f"{what}: {type(last).__name__}: {last}")
    return None


def daily_closes(symbols, period):
    """One download for many symbols -> {symbol: Series of closes indexed by date}."""
    df = retry(lambda: yf.download(symbols, period=period, interval="1d", auto_adjust=False,
                                   group_by="ticker", progress=False, threads=True),
               f"daily download {','.join(symbols)}")
    out = {}
    if df is None:
        return out
    for s in symbols:
        try:
            col = df[s]["Close"] if isinstance(df.columns, pd.MultiIndex) else df["Close"]
            col = col.dropna()
            if col.empty:
                errors.append(f"{s}: no daily closes")
                continue
            col.index = pd.to_datetime(col.index).date
            out[s] = col
        except KeyError:
            errors.append(f"{s}: missing from download")
    return out


def write(name, data):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(data, f, separators=(",", ":"))


os.makedirs(OUT, exist_ok=True)

# ---- prices.json (completed sessions only) ----
groups = [("Holding", HOLDINGS), ("Watchlist", WATCHLIST), ("Index", INDEX_ETFS)]
all_syms = [s for _, g in groups for s in g]
closes = daily_closes(all_syms, "6mo")
market_closed_today = NOW.time() >= datetime.strptime("16:15", "%H:%M").time()
prices = []
for group, syms in groups:
    for s in syms:
        c = closes.get(s)
        if c is None:
            continue
        if not market_closed_today:  # drop today's still-forming bar
            c = c[c.index < NOW.date()]
        c = c.sort_index(ascending=False)
        for lag, (d, v) in enumerate(c.items()):
            if lag in KEEP_LAGS:
                prices.append({"ticker": s, "group": group, "lag": lag,
                               "date": d.isoformat(), "close": round(float(v), 4)})
            if lag > max(KEEP_LAGS):
                break

# ---- live.json (pre-market for stocks/ETFs, overnight for futures) ----
def session_of(ts):
    if ts.date() < NOW.date():
        return "closed"
    t = ts.time()
    if t < datetime.strptime("09:30", "%H:%M").time():
        return "pre"
    if t < datetime.strptime("16:00", "%H:%M").time():
        return "regular"
    return "post"


live = []
targets = [(s, s, "future", FUTURES[s]) for s in FUTURES] + \
          [(s, s, "stock", s) for s in HOLDINGS + WATCHLIST + INDEX_ETFS]
for sym, ticker, kind, name in targets:
    t = yf.Ticker(sym)
    intr = retry(lambda: t.history(period="5d", interval="5m", prepost=True, auto_adjust=False),
                 f"{sym} intraday")
    day = retry(lambda: t.history(period="10d", interval="1d", auto_adjust=False), f"{sym} daily")
    if intr is None or day is None:
        continue
    intr = intr.dropna(subset=["Close"])
    if intr.empty:
        errors.append(f"{sym}: no intraday bars")
        continue
    ts = intr.index[-1].tz_convert(ET)
    price = float(intr["Close"].iloc[-1])
    dclose = day["Close"].dropna()
    ddates = pd.to_datetime(dclose.index).date
    prior = dclose[ddates < ts.date()]
    if prior.empty:
        errors.append(f"{sym}: no prior close")
        continue
    prev = float(prior.iloc[-1])
    live.append({"ticker": ticker, "name": name, "kind": kind,
                 "price": round(price, 4), "prev_close": round(prev, 4),
                 "chg": round(price / prev - 1, 6),
                 "as_of": ts.isoformat(timespec="minutes"),
                 "session": "futures" if kind == "future" else session_of(ts)})
    time.sleep(0.3)

# ---- yields.json ----
ycl = daily_closes(list(YIELDS.values()), "6mo")
ycols = {}
for key, sym in YIELDS.items():
    s = ycl.get(sym)
    if s is None:
        continue
    if s.median() > 20:  # older Yahoo convention quoted yield x10
        s = s / 10
    ycols[key] = s
yields = []
if ycols:
    ydf = pd.DataFrame(ycols).sort_index()
    for d, r in ydf.iterrows():
        yields.append({"date": d.isoformat(),
                       **{k: (None if pd.isna(r.get(k)) else round(float(r[k]), 3)) for k in YIELDS}})

# ---- oil.json ----
ocl = daily_closes(list(OIL.values()), "6mo")
ocols = {k: ocl[s] for k, s in OIL.items() if s in ocl}
oil = []
if ocols:
    odf = pd.DataFrame(ocols).sort_index()
    for d, r in odf.iterrows():
        oil.append({"date": d.isoformat(),
                    **{k: (None if pd.isna(r.get(k)) else round(float(r[k]), 2)) for k in OIL}})

# ---- write ----
if prices:
    write("prices.json", prices)
if live:
    write("live.json", live)
if yields:
    write("yields.json", yields)
if oil:
    write("oil.json", oil)
meta = {"generated_at": NOW.isoformat(timespec="seconds"),
        "rows": {"prices": len(prices), "live": len(live), "yields": len(yields), "oil": len(oil)},
        "tickers_missing": sorted(set(all_syms) - {p["ticker"] for p in prices}),
        "errors": errors[:50]}
write("meta.json", meta)
print(json.dumps(meta, indent=2))
sys.exit(0 if prices else 1)
