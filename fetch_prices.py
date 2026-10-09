#!/usr/bin/env python3
"""Fetch daily bars for the US sector / industry ETFs on the weekly
relative-strength board, aligned to SPY's trading days.

Standard library only. Writes to --out (default output/):
  prices.csv              long: date, ticker, open, high, low, close, adj_close, volume
  prices_wide.csv         wide: date (SPY axis) + one adj_close column per ticker
  prices_summary.json     per-ticker source and coverage, base axis, checks, warnings
  holdings/<run date>.csv top-10 holdings of every ETF (stockanalysis.com)

This script only collects raw prices. The relative-return percentiles, the
ETF / SPY and ETF / QQQ ratio lines and the RS-Ratio / RS-Momentum of the
two-tier RRG (sector / SPY, industry / parent sector) are computed elsewhere
from prices_wide.csv, which is why every ticker is written on one date axis.

Sources (no API key): Stooq daily CSV first, Yahoo Finance chart second.
Stooq serves a single price series with no separate adjusted column, so a
ticker that comes from Stooq gets close copied into adj_close and
adj_close_available = false in the summary. Yahoo's adjclose is dividend
and split adjusted.
"""

import argparse
import csv
import datetime as dt
import html.parser
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# ------------------------------------------------------------------ universe
# Keep the three lists in board order: the CSV columns and rows follow it.

BENCHMARKS = ["SPY", "QQQ"]
SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLB", "XLRE"]
INDUSTRIES = ["SMH", "IGV", "XBI", "GNOM", "ARKG", "KRE", "XHB", "XME", "URA", "ITA", "PAVE", "DRAM"]

# Second-tier RRG benchmark: industry / theme ETF -> the sector ETF it sits in.
PARENT_SECTOR = {
    "SMH": "XLK", "IGV": "XLK", "DRAM": "XLK",
    "XBI": "XLV", "GNOM": "XLV", "ARKG": "XLV",
    "KRE": "XLF",
    "XHB": "XLY",
    "XME": "XLB",
    "ITA": "XLI", "PAVE": "XLI",
    "URA": "XLE",
}

TICKERS = BENCHMARKS + SECTORS + INDUSTRIES
BASE_TICKER = "SPY"          # its trading days are the date axis
CORE = BENCHMARKS + SECTORS  # expected to be gap-free on the axis
MAX_CORE_MISSING = 5

# ------------------------------------------------------------------- sources

STOOQ_URL = "https://stooq.com/q/d/l/?s={sym}.us&i=d"
YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]
YAHOO_URL = "{host}/v8/finance/chart/{sym}?range={rng}&interval=1d"
HOLDINGS_URL = "https://stockanalysis.com/etf/{sym}/holdings/"
HOLDINGS_TOP = 10
HOLDINGS_FIELDS = ["etf", "rank", "symbol", "name", "weight_pct"]
PRICE_FIELDS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
SOURCE_ORDER = {"auto": ["stooq", "yahoo"], "stooq": ["stooq"], "yahoo": ["yahoo"]}

UA = {"User-Agent": "Mozilla/5.0 (rrg-price-fetcher)"}
# stockanalysis.com answers 403 unless the request looks like a browser
# (the Accept headers matter more than the User-Agent).
BROWSER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

RETRYABLE = (urllib.error.URLError, http.client.HTTPException, TimeoutError,
             ConnectionError, json.JSONDecodeError)


def http_get(url, headers=UA, retries=4, timeout=60):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            # 429 and 5xx are worth another try; other 4xx are final.
            if (e.code != 429 and e.code < 500) or attempt == retries - 1:
                raise
            time.sleep(15 if e.code == 429 else 2 ** attempt)
        except RETRYABLE:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # no tzdata on this machine
        return None


NY = zone("America/New_York")


def bar(date, o, h, l, c, adj, v):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "adj_close": adj, "volume": v}


def fetch_stooq(ticker):
    """{date: bar}. Stooq has no adjusted column: adj_close = close."""
    text = http_get(STOOQ_URL.format(sym=ticker.lower()), retries=2, timeout=30).decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].lower().startswith("date,open,high,low,close"):
        # "Exceeded the daily hits limit", "No data", or an HTML page
        raise ValueError("unexpected response: " + (lines[0][:80] if lines else "empty body"))
    rows = {}
    for line in lines[1:]:
        f = line.split(",")
        if len(f) < 5:
            continue
        try:
            o, h, l, c = (float(x) for x in f[1:5])
        except ValueError:
            continue
        v = int(float(f[5])) if len(f) > 5 and f[5] else None
        rows[f[0]] = bar(f[0], o, h, l, c, c, v)
    if not rows:
        raise ValueError("no price rows")
    return rows, False


def yahoo_range(days):
    return "2y" if days <= 450 else "5y" if days <= 1150 else "10y" if days <= 2400 else "max"


def fetch_yahoo(ticker, days):
    """{date: bar} with Yahoo's dividend/split adjusted close in adj_close."""
    errors = []
    for host in YAHOO_HOSTS:
        url = YAHOO_URL.format(host=host, sym=urllib.parse.quote(ticker), rng=yahoo_range(days))
        try:
            d = json.loads(http_get(url))
            break
        except Exception as e:
            errors.append(f"{host}: {e!r}")
    else:
        raise RuntimeError("; ".join(errors))
    chart = d.get("chart") or {}
    if chart.get("error"):
        raise RuntimeError(str(chart["error"]))
    res = (chart.get("result") or [None])[0]
    if not res or not res.get("timestamp"):
        raise ValueError("empty result")
    meta = res.get("meta") or {}
    tz = zone(meta.get("exchangeTimezoneName") or "") or dt.timezone(dt.timedelta(seconds=meta.get("gmtoffset") or 0))
    ts = res["timestamp"]
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")
    col = lambda k: q.get(k) or [None] * len(ts)
    rows = {}
    for i, t in enumerate(ts):
        c = col("close")[i]
        if c is None:
            continue
        date = dt.datetime.fromtimestamp(t, tz).strftime("%Y-%m-%d")
        a = adj[i] if adj and i < len(adj) and adj[i] is not None else c
        rows[date] = bar(date, col("open")[i], col("high")[i], col("low")[i], c, a, col("volume")[i])
    if not rows:
        raise ValueError("no price rows")
    return rows, adj is not None


def drop_partial_bar(rows):
    """Both sources include today's bar while the session is still running.
    Drop it so a run during US market hours never stores an intraday close."""
    if NY is None:
        return None
    now = dt.datetime.now(NY)
    last = max(rows)
    if last == now.strftime("%Y-%m-%d") and now.time() < dt.time(16, 15):
        del rows[last]
        return last
    return None


def fetch_prices(ticker, days, order, state):
    """Try the sources in order. Returns (rows, source, adj_available, attempts, dropped)."""
    attempts = {}
    for source in order:
        if source == "stooq" and state["stooq_down"]:
            attempts[source] = "skipped: unreachable for the previous tickers"
            continue
        try:
            rows, adj = fetch_stooq(ticker) if source == "stooq" else fetch_yahoo(ticker, days)
        except Exception as e:
            attempts[source] = repr(e)
            if source == "stooq":
                state["stooq_failures"] += 1
                # Three connection-level failures in a row = the host is not
                # reachable from here; do not burn a timeout on every ticker.
                if state["stooq_failures"] >= 3 and not isinstance(e, ValueError):
                    state["stooq_down"] = True
            continue
        if source == "stooq":
            state["stooq_failures"] = 0
        dropped = drop_partial_bar(rows)
        if not rows:
            attempts[source] = "only today's partial bar"
            continue
        return rows, source, adj, attempts, dropped
    raise RuntimeError("; ".join(f"{s}: {e}" for s, e in attempts.items()))


# ------------------------------------------------------------------ holdings


class FirstTable(html.parser.HTMLParser):
    """Cell texts of the first <table> on the page, one list per row."""

    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell, self._depth, self.done = [], None, None, 0, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            self._depth += 1
        elif self._depth and tag == "tr":
            self._row = []
        elif self._depth and tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
            self.done = self._depth == 0

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def fetch_holdings(ticker):
    page = http_get(HOLDINGS_URL.format(sym=ticker.lower()), headers=BROWSER, retries=2).decode("utf-8", "replace")
    p = FirstTable()
    p.feed(page)
    if not p.rows:
        raise ValueError("no holdings table on the page")
    header = [h.lower() for h in p.rows[0]]

    def find(*words):
        for i, h in enumerate(header):
            if any(w in h for w in words):
                return i
        raise ValueError(f"column {words[0]!r} not in table header {p.rows[0]}")

    i_sym, i_name, i_w = find("symbol"), find("name"), find("weight")
    out = []
    for row in p.rows[1:]:
        if len(row) <= max(i_sym, i_name, i_w):
            continue
        w = row[i_w].replace("%", "").replace(",", "").strip()
        try:
            weight = round(float(w), 4)
        except ValueError:
            weight = ""
        sym = row[i_sym].strip()
        out.append({"etf": ticker, "rank": len(out) + 1, "symbol": "" if sym.lower() in ("n/a", "-", "—") else sym,
                    "name": row[i_name].strip(), "weight_pct": weight})
        if len(out) == HOLDINGS_TOP:
            break
    if not out:
        raise ValueError("holdings table is empty")
    return out


# --------------------------------------------------------------------- output


def write_csv(path, fields, rows):
    # Always rewrite, so an empty result never leaves an older file behind.
    # utf-8-sig lets Excel detect the encoding.
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def num(x):
    if x is None or x == "":
        return ""
    if isinstance(x, int):
        return x
    return f"{x:.4f}".rstrip("0").rstrip(".")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "output"))
    ap.add_argument("--no-holdings", action="store_true", help="prices only, skip the holdings snapshot")
    ap.add_argument("--days", type=int, default=500, help="trading days kept on the SPY axis (default 500)")
    ap.add_argument("--source", choices=sorted(SOURCE_ORDER), default="auto",
                    help="auto = Stooq then Yahoo per ticker; stooq / yahoo = that source only")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    order = SOURCE_ORDER[args.source]

    summary = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_order": order,
        "tickers": {},
        "base_axis": None,
        "failed_tickers": [],
        "parent_sector": PARENT_SECTOR,
        "holdings": None,
        "holdings_failed": {},
        "checks": {},
        "warnings": [],
    }
    warn = summary["warnings"].append
    if NY is None:
        warn("zoneinfo has no America/New_York: the partial-bar check was skipped")

    def finish(code):
        with open(os.path.join(args.out, "prices_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        for w in summary["warnings"]:
            print("warning:", w, file=sys.stderr)
        print(f"exit {code}")
        sys.exit(code)

    # ---- prices
    data, state = {}, {"stooq_failures": 0, "stooq_down": False}
    for t in TICKERS:
        try:
            rows, source, adj, attempts, dropped = fetch_prices(t, args.days, order, state)
        except Exception as e:
            summary["failed_tickers"].append(t)
            summary["tickers"][t] = {"source": None, "error": str(e)}
            print(f"{t:<5} FAILED  {e}")
            continue
        data[t] = rows
        summary["tickers"][t] = {"source": source, "adj_close_available": adj, "attempts": attempts,
                                 "partial_bar_dropped": dropped}
        print(f"{t:<5} {source:<6} {len(rows):>5} rows  {min(rows)}..{max(rows)}"
              + (f"  (dropped today's partial bar {dropped})" if dropped else ""))

    if BASE_TICKER not in data:
        # No axis, nothing can be aligned: blank the CSVs so no older run passes for this one.
        write_csv(os.path.join(args.out, "prices.csv"), PRICE_FIELDS, [])
        write_csv(os.path.join(args.out, "prices_wide.csv"), ["date"] + TICKERS, [])
        warn(f"{BASE_TICKER} could not be fetched, so no date axis and no price files were written")
        finish(1)

    # ---- align every ticker to the SPY axis
    axis = sorted(data[BASE_TICKER])[-args.days:]
    axis_set, axis_start = set(axis), axis[0]
    summary["base_axis"] = {"ticker": BASE_TICKER, "first_date": axis[0], "last_date": axis[-1], "days": len(axis)}
    aligned = {}
    for t, rows in data.items():
        kept = {d: r for d, r in rows.items() if d in axis_set}
        off_axis = sorted(d for d in rows if d not in axis_set and d >= axis_start)
        missing = [d for d in axis if d not in kept]
        info = summary["tickers"][t]
        if kept:
            info["first_date"], info["last_date"] = min(kept), max(kept)
            info["gap_days"] = sum(1 for d in missing if info["first_date"] < d < info["last_date"])
        else:
            info["first_date"] = info["last_date"] = None
            info["gap_days"] = 0
        info["rows"] = len(kept)
        info["missing_days"] = len(missing)          # blank cells on the axis, listing gap included
        info["off_axis_dates"] = off_axis            # had data on a day SPY did not trade: dropped
        info["rows_before_axis"] = sum(1 for d in rows if d < axis_start)
        aligned[t] = kept
        if off_axis:
            warn(f"{t}: {len(off_axis)} date(s) not on the {BASE_TICKER} axis were dropped: {', '.join(off_axis[:5])}"
                 + (" ..." if len(off_axis) > 5 else ""))

    long_rows = [dict(aligned[t][d], ticker=t) for d in axis for t in TICKERS if t in aligned and d in aligned[t]]
    write_csv(os.path.join(args.out, "prices.csv"), PRICE_FIELDS,
              [{k: (r[k] if k in ("date", "ticker") else num(r[k])) for k in PRICE_FIELDS} for r in long_rows])
    wide_rows = [{"date": d, **{t: num(aligned[t][d]["adj_close"]) if t in aligned and d in aligned[t] else ""
                                for t in TICKERS}} for d in axis]
    write_csv(os.path.join(args.out, "prices_wide.csv"), ["date"] + TICKERS, wide_rows)

    # ---- checks
    checks = summary["checks"]
    checks["wide_rows_equal_base_axis_days"] = len(wide_rows) == len(axis)
    if not checks["wide_rows_equal_base_axis_days"]:
        warn(f"prices_wide.csv has {len(wide_rows)} rows but the axis has {len(axis)} days")
    over = {t: summary["tickers"][t]["missing_days"] for t in CORE
            if t in aligned and summary["tickers"][t]["missing_days"] > MAX_CORE_MISSING}
    checks["core_missing_days_over_limit"] = over
    for t, n in over.items():
        warn(f"{t}: {n} missing days on the axis (limit {MAX_CORE_MISSING})")
    stale = {t: summary["tickers"][t]["last_date"] for t in aligned
             if summary["tickers"][t]["last_date"] != axis[-1]}
    checks["last_date_differs_from_base"] = stale
    for t, d in stale.items():
        warn(f"{t}: last date {d} differs from {BASE_TICKER} {axis[-1]}")
    unadjusted = sorted(t for t in aligned if not summary["tickers"][t]["adj_close_available"])
    checks["tickers_without_adj_close"] = unadjusted
    if unadjusted and len(unadjusted) < len(aligned):
        warn("adj_close mixes dividend-adjusted and unadjusted series; close was copied for: "
             + ", ".join(unadjusted) + " (rerun with --source yahoo for one adjusted series)")
    elif unadjusted:
        warn("no source provided an adjusted close; adj_close is a copy of close for every ticker")
    if summary["failed_tickers"]:
        warn("price fetch failed for: " + ", ".join(summary["failed_tickers"]))

    # ---- holdings
    if not args.no_holdings:
        hdir = os.path.join(args.out, "holdings")
        os.makedirs(hdir, exist_ok=True)
        hfile = os.path.join(hdir, dt.date.today().isoformat() + ".csv")
        rows = []
        for t in TICKERS:
            try:
                got = fetch_holdings(t)
            except Exception as e:
                summary["holdings_failed"][t] = str(e)
                print(f"holdings {t:<5} FAILED  {e}")
            else:
                rows.extend(got)
                print(f"holdings {t:<5} {len(got):>2} rows  top: {got[0]['symbol']} {got[0]['weight_pct']}%")
            time.sleep(1)  # one page per second is polite
        write_csv(hfile, HOLDINGS_FIELDS, rows)
        summary["holdings"] = {"file": os.path.relpath(hfile, args.out), "source": "stockanalysis.com",
                               "etfs": len(TICKERS) - len(summary["holdings_failed"]), "rows": len(rows)}
        if summary["holdings_failed"]:
            warn("holdings failed for: " + ", ".join(summary["holdings_failed"]))

    finish(1 if summary["failed_tickers"] else 2 if summary["holdings_failed"] else 0)


if __name__ == "__main__":
    main()
