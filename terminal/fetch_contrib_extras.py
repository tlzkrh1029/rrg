#!/usr/bin/env python3
"""기여도 계산(terminal/compute_contrib.py)에 필요한 보조 가격을 받는다.

1. 보조 종목: 최근 LOOKBACK_DAYS 일 동안의 구성종목 스냅샷에서 터미널 ETF 와 SPY·QQQ 의 상위 10개에
   들었지만 돌파 지도 수집(output/stocks/prices_wide.csv)에 없는 종목. 지금은 상위 10 밖으로 밀려났어도
   기간 시작 구성으로 기여도를 다시 계산할 때 필요하다. 돌파 지도와 같은 방식(Yahoo 조정 종가, SPY 거래일 축,
   현지 휴일은 직전 값)으로 받는다.
2. 환율: 해외 상장 종목(두 수집 결과 모두)의 통화를 Yahoo 차트의 currency 로 확인하고, 통화마다
   Yahoo '<통화>=X'(1달러당 통화 수량) 일봉을 받는다. GBp·ZAc·ILA 처럼 보조 단위로 매겨지는 종목은
   단위를 기록해 두고 계산 단계에서 100 으로 나눈다.

출력 (--out, 기본 output/stocks/)
  extra_prices_wide.csv   date(SPY 축) + 보조 종목 키마다 조정 종가(현지 통화)
  fx_wide.csv             date(SPY 축) + 통화마다 1달러당 통화 수량
  extra_summary.json      보조 종목 목록과 출처, 종목별 통화(main·extra 모두), 실패 목록

표준 라이브러리만 쓰고 API 키가 없다. 가격 함수는 fetch_prices.py 와 breakout/fetch_stock_prices.py 의 것을 쓴다.
종료 코드: 0 정상, 2 일부 실패(요약에 기록), 1 축이나 스냅샷을 읽지 못함.
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "breakout"))
import fetch_prices as fp  # noqa: E402
import fetch_stock_prices as fsp  # noqa: E402

LOOKBACK_DAYS = 45          # 1개월(21거래일) 기간의 시작 구성을 덮을 만큼
WINDOW_DAYS = 450           # 돌파 지도와 같은 창(Yahoo range=2y)
PAUSE = 0.2
BENCH = ["SPY", "QQQ"]
NON_STOCK = re.compile(r"TRS|^B\.0|^XBOX$|CASH", re.I)
SUBUNIT = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ZAC": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def snapshots(hdir):
    out = []
    for f in sorted(os.listdir(hdir)):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.csv", f):
            out.append((f[:-4], list(csv.DictReader(open(os.path.join(hdir, f), encoding="utf-8-sig")))))
    return out


def candidates(exch, code):
    """breakout 의 후보에 더해, 점이 든 코드는 점을 그대로 둔 것과 '-' 로 바꾼 것을 함께 시도한다(U.UN → U-UN.TO, CCO.NE)."""
    out = list(fsp.yahoo_candidates(exch, code))
    if "." in code:
        if exch in fsp.US_EXCHANGES:
            out.append(code)
        else:
            out += [code.replace(".", "-") + s for s in fsp.YAHOO_SUFFIX.get(exch, [])]
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def yahoo_currency(sym):
    """Yahoo 차트 메타의 통화 코드(예: KRW, GBp). 실패하면 None."""
    for host in fp.YAHOO_HOSTS:
        url = fp.YAHOO_URL.format(host=host, sym=urllib.parse.quote(sym), rng="5d")
        try:
            d = json.loads(fp.http_get(url, retries=2))
            res = ((d.get("chart") or {}).get("result") or [None])[0] or {}
            cur = (res.get("meta") or {}).get("currency")
            if cur:
                return cur
        except Exception:  # noqa: BLE001
            continue
    return None


def align(rows, axis, field="adj_close"):
    """{date: bar} 을 SPY 축에 맞춘다. 축에는 있고 자료에는 없는 날은 직전 값, 첫 자료 이전은 비운다."""
    dates = sorted(rows)
    col, last, j = {}, None, 0
    for d in axis:
        while j < len(dates) and dates[j] <= d:
            v = rows[dates[j]].get(field)
            if v is None:
                v = rows[dates[j]].get("close")
            if v is not None:
                last = float(v)
            j += 1
        if last is not None:
            col[d] = last
    return col


def write_wide(path, axis, series, fmt="{:.6g}"):
    cols = sorted(series)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + cols)
        for d in axis:
            w.writerow([d] + [("" if d not in series[k] else fmt.format(series[k][d])) for k in cols])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--axis", default=os.path.join(ROOT, "output", "prices_wide.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "stocks"))
    ap.add_argument("--lookback", type=int, default=LOOKBACK_DAYS)
    args = ap.parse_args()

    started = dt.datetime.now(dt.timezone.utc)
    try:
        axis = fsp.load_axis(args.axis)
        snaps = snapshots(args.holdings)
    except Exception as e:  # noqa: BLE001
        print(f"cannot read the axis or the holdings: {e!r}")
        return 1
    if not axis or not snaps:
        print("empty axis or no holdings snapshot")
        return 1
    meta = json.load(open(args.meta, encoding="utf-8"))
    funds = [t for t, m in meta.items() if "terminal" in m.get("screens", [])] + BENCH
    latest = dt.date.fromisoformat(snaps[-1][0])
    recent = [(d, rows) for d, rows in snaps if (latest - dt.date.fromisoformat(d)).days <= args.lookback]

    main_summary_path = os.path.join(args.out, "summary.json")
    main_info = (json.load(open(main_summary_path, encoding="utf-8")).get("tickers") or {}) if os.path.exists(main_summary_path) else {}
    main_pairs = {(v.get("exch") or "", v.get("code") or k): k for k, v in main_info.items()}
    used_keys = set(main_info)

    # ---- 보조 종목: 최근 스냅샷의 상위 10 가운데 돌파 지도 수집에 없는 종목.
    # 같은 종목이 스냅샷에 따라 거래소 접두어 없이 적히기도 하므로('KRX: 000660' ↔ '000660'), 접두어가 없는 코드는
    # 같은 코드가 이미 있으면(수집 종목이든 접두어가 붙은 보조 종목이든) 같은 종목으로 본다.
    main_codes = {code for (_ex, code) in main_pairs}
    pairs = {}
    for d, rows in recent:
        for r in rows:
            if r.get("etf") not in funds:
                continue
            try:
                if int(r.get("rank") or 99) > 10:
                    continue
            except ValueError:
                continue
            exch, code = fsp.parse_symbol(r.get("symbol"))
            if not code or NON_STOCK.search(code) or (exch, code) in main_pairs:
                continue
            if (exch, code) not in pairs:
                pairs[(exch, code)] = {"name": r.get("name") or "", "seen": d, "etf": r.get("etf")}
    prefixed = {code for (ex, code) in pairs if ex}
    extras = {}
    for (exch, code), v in sorted(pairs.items(), key=lambda kv: (kv[0][0] == "", kv[0])):
        if not exch and (code in main_codes or code in prefixed):
            continue
        key = code if code not in used_keys else f"{code}.{exch or 'US'}"
        used_keys.add(key)
        extras[key] = {"exch": exch, "code": code, "name": v["name"], "yahoo": candidates(exch, code), "seen": v["seen"], "etf": v["etf"]}
    print(f"snapshots {recent[0][0]} ~ {recent[-1][0]} ({len(recent)}): {len(extras)} extra names for {len(funds)} funds")

    state = {"stooq_down": False, "stooq_failures": 0}
    series, info, failed = {}, {}, {}
    for i, (key, st) in enumerate(sorted(extras.items()), 1):
        attempts = {}
        try:
            rows, source, sym, adj, dropped, filled = fsp.fetch_one(st["yahoo"], WINDOW_DAYS, state, attempts)
        except Exception as e:  # noqa: BLE001
            failed[key] = {"attempts": attempts, "error": repr(e), "etf": st["etf"], "seen": st["seen"]}
            print(f"{i:>3}/{len(extras)} {key:<10} FAILED  {e}")
            time.sleep(PAUSE)
            continue
        col = align(rows, axis)
        if not col:
            failed[key] = {"attempts": attempts, "error": "no bars on the SPY axis", "etf": st["etf"], "seen": st["seen"]}
            continue
        series[key] = col
        info[key] = {"symbol": sym, "source": source, "exch": st["exch"], "code": st["code"], "name": st["name"],
                     "etf": st["etf"], "seen": st["seen"], "first": min(col), "last": max(col)}
        print(f"{i:>3}/{len(extras)} {key:<10} {sym:<12} {min(col)} ~ {max(col)}")
        time.sleep(PAUSE)

    # ---- 통화: 미국 밖 거래소 종목만 확인한다(미국 상장은 USD)
    currency, cur_failed = {}, {}
    def note_currency(key, exch, sym):
        if exch in fsp.US_EXCHANGES:
            return
        c = yahoo_currency(sym)
        time.sleep(PAUSE)
        if not c:
            cur_failed[key] = sym
            return
        base, div = SUBUNIT.get(c, (c.upper(), 1.0))
        currency[key] = {"cur": base, "div": div, "symbol": sym}
    for key, v in main_info.items():
        note_currency(key, v.get("exch") or "", v.get("symbol") or key)
    for key, v in info.items():
        note_currency(key, v["exch"], v["symbol"])

    fx, fx_failed = {}, {}
    for cur in sorted({v["cur"] for v in currency.values() if v["cur"] != "USD"}):
        sym = f"{cur}=X"
        try:
            rows, _adj, _filled = fp.fetch_yahoo(sym, WINDOW_DAYS)
            fp.drop_partial_bar(rows)
            col = align(rows, axis, field="close")
            if not col:
                raise ValueError("no bars on the SPY axis")
            fx[cur] = col
            print(f"fx {sym:<8} {min(col)} ~ {max(col)}")
        except Exception as e:  # noqa: BLE001
            fx_failed[cur] = repr(e)
            print(f"fx {sym:<8} FAILED  {e}")
        time.sleep(PAUSE)

    os.makedirs(args.out, exist_ok=True)
    write_wide(os.path.join(args.out, "extra_prices_wide.csv"), axis, series, "{:.4f}")
    write_wide(os.path.join(args.out, "fx_wide.csv"), axis, fx, "{:.6f}")
    summary = {
        "generated_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lookback_days": args.lookback, "snapshots": [d for d, _ in recent], "funds": funds,
        "extras": info, "failed": failed, "currency": currency, "currency_failed": cur_failed,
        "fx": sorted(fx), "fx_failed": fx_failed,
    }
    with open(os.path.join(args.out, "extra_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"wrote {len(series)} extra series, {len(fx)} fx series; failed {len(failed)} names, {len(cur_failed)} currencies, {len(fx_failed)} fx")
    return 2 if (failed or cur_failed or fx_failed) else 0


if __name__ == "__main__":
    sys.exit(main())
