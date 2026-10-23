#!/usr/bin/env python3
"""돌파 지도용 구성종목 일봉 수집기.

최신 구성종목 스냅샷(output/holdings/<날짜>.csv)에서 돌파 지도 화면(data/etf_meta.json 의
screens 에 "breakout" 이 든 ETF)의 상위 10개 종목을 모아, 종목마다 조정 종가 일봉(2년)을 받아
SPY 거래일 축(output/prices_wide.csv)에 맞춰 쓴다. 표준 라이브러리만 쓰고 API 키가 없다.
가격 함수는 저장소 루트의 fetch_prices.py(ETF 수집기)의 것을 그대로 가져다 쓴다.

출력 (--out, 기본 output/stocks/)
  prices_wide.csv   date(SPY 축) + 종목 키마다 조정 종가 한 열. 비미국 종목은 현지 휴일에
                    직전 값을 그대로 둔다(forward fill). 수집에 실패한 종목은 열이 없다.
  ath.json          종목별 "2년 창 이전" 최고 조정 종가 캐시. 사상 최고 지평은 이 값과 2년 창
                    안의 종가를 합쳐 계산한다. 처음 보는 종목은 Yahoo 에서 전체 이력(range=max)을
                    한 번 받아 채우고, ATH_REFRESH_DAYS 가 지났거나 기준일 종가가 캐시와 달라지면
                    (액면분할·배당 재조정) 다시 받는다.
  summary.json      종목별 출처·구간·결측, 심볼 대응표, 실패 목록, 경고.

심볼 대응 (holdings 의 표기 → Yahoo 심볼)
  미국 티커는 그대로(BRK.B → BRK-B). "KRX: 005930" 처럼 거래소 접두어가 붙은 비미국 종목은
  YAHOO_SUFFIX 의 접미어를 붙인다. 코스닥·TWSE 상장 여부는 알 수 없으므로 .KS 와 .TW 를
  먼저 시도하고 실패하면 .KQ·.TWO 를 시도한다. 심볼이 빈 행(URA 의 Sprott Physical Uranium
  Trust 등)은 종목 칸 없이 구성종목 표에만 남는다.

종료 코드: 0 정상, 1 SPY 축을 읽지 못했거나 종목이 하나도 없음, 2 일부 종목 실패(요약에 기록).
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import fetch_prices as fp  # noqa: E402  (fetch_prices, fetch_yahoo, drop_partial_bar)

WINDOW_DAYS = 450          # Yahoo range=2y (약 500 거래일). 90일 창 + 52주 지평에 충분하다
ATH_REFRESH_DAYS = 30      # 전체 이력 ATH 를 다시 받는 주기(배당 재조정 반영)
PAUSE = 0.2                # 요청 사이 대기(초). Yahoo 429 는 http_get 이 15초 쉬고 재시도한다

# holdings 의 거래소 접두어 → Yahoo 접미어 후보(앞에서부터 시도)
YAHOO_SUFFIX = {
    "KRX": [".KS", ".KQ"], "KOSDAQ": [".KQ"], "TWSE": [".TW", ".TWO"], "TPE": [".TW", ".TWO"],
    "TSE": [".T"], "TYO": [".T"], "SSE": [".SS"], "SHA": [".SS"], "SZSE": [".SZ"], "SHE": [".SZ"],
    "HKG": [".HK"], "HKEX": [".HK"], "LON": [".L"], "LSE": [".L"], "ASX": [".AX"],
    "TSX": [".TO"], "TSXV": [".V"], "CVE": [".V"], "NEO": [".NE"], "CNSX": [".CN"],
    "XETRA": [".DE"], "ETR": [".DE"], "FRA": [".F"], "SIX": [".SW"], "SWX": [".SW"], "VTX": [".SW"],
    "EPA": [".PA"], "AMS": [".AS"], "BIT": [".MI"], "BME": [".MC"], "TLV": [".TA"], "STO": [".ST"],
    "CPH": [".CO"], "OSL": [".OL"], "HEL": [".HE"], "EBR": [".BR"], "BRU": [".BR"], "ELI": [".LS"],
    "VIE": [".VI"], "WSE": [".WA"], "NSE": [".NS"], "BOM": [".BO"], "SGX": [".SI"], "JSE": [".JO"],
    "BVMF": [".SA"], "BMV": [".MX"], "NZE": [".NZ"], "ISE": [".IR"], "IDX": [".JK"], "SET": [".BK"],
}
US_EXCHANGES = {"", "US", "NASDAQ", "NYSE", "NYSEARCA", "NYSE ARCA", "AMEX", "NYSEAMERICAN", "NYSE AMERICAN",
                "CBOE", "CBOE BZX", "BATS", "OTC", "OTCMKTS", "OTCQX", "OTCQB"}


def parse_symbol(sym):
    """'KRX: 005930' → ('KRX', '005930'); 'NVDA' → ('', 'NVDA'); '' → ('', '')."""
    s = (sym or "").strip()
    m = re.match(r"^([A-Za-z ]+?):\s*(\S.*)$", s)
    if m:
        return m.group(1).strip().upper(), m.group(2).strip()
    return "", s


def yahoo_candidates(exch, code):
    if exch in US_EXCHANGES:
        return [code.replace(".", "-")]
    sufs = YAHOO_SUFFIX.get(exch)
    if not sufs:
        return [code]  # 모르는 거래소: 접미어 없이 한 번 시도한다
    c = code
    if exch in ("HKG", "HKEX") and c.isdigit():
        c = c.zfill(4)
    return [c + s for s in sufs]


def universe(meta, holdings_rows, screen="breakout"):
    """돌파 지도 ETF 의 상위 10개 행을 모아 종목 사전을 만든다.

    종목 키는 보통 코드 그대로(NVDA, 005930)이고, 같은 코드가 다른 거래소에도 있으면 'CODE.EXCH' 로 가른다.
    돌려주는 값: (etf 목록, {key: {exch, code, name, yahoo}}, {etf: [(key 또는 None, symbol, name, weight)]})
    """
    etfs = [t for t, m in meta.items() if screen in m.get("screens", [])]
    by_etf = {}
    for r in holdings_rows:
        by_etf.setdefault(r["etf"], []).append(r)
    owner, stocks, rows_of = {}, {}, {}
    for t in etfs:
        rows = sorted(by_etf.get(t, []), key=lambda r: int(r["rank"]))[:10]
        out = []
        for r in rows:
            exch, code = parse_symbol(r["symbol"])
            if not code:
                out.append((None, r["symbol"], r["name"], r["weight_pct"]))
                continue
            key = code
            if key in owner and owner[key] != (exch, code):
                key = f"{code}.{exch or 'US'}"
            owner[key] = (exch, code)
            if key not in stocks:
                stocks[key] = {"exch": exch, "code": code, "name": r["name"], "yahoo": yahoo_candidates(exch, code)}
            out.append((key, r["symbol"], r["name"], r["weight_pct"]))
        rows_of[t] = out
    return etfs, stocks, rows_of


def load_axis(path):
    with open(path, encoding="utf-8-sig") as f:
        return [r["date"] for r in csv.DictReader(f)]


def latest_holdings(hdir):
    files = sorted(f for f in os.listdir(hdir) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.csv", f))
    if not files:
        raise SystemExit(f"no holdings snapshot in {hdir}")
    with open(os.path.join(hdir, files[-1]), encoding="utf-8-sig") as f:
        return files[-1][:-4], list(csv.DictReader(f))


def fetch_one(cands, days, state, attempts):
    """후보 심볼을 차례로 시도해 (rows, source, symbol, adj, dropped, filled) 를 돌려준다.
    접미어가 없는 미국 심볼만 Stooq 를 뒤에 둔다."""
    last_err = None
    for sym in cands:
        order = ["yahoo", "stooq"] if "." not in sym else ["yahoo"]
        try:
            rows, source, adj, att, dropped, filled = fp.fetch_prices(sym, days, order, state)
            attempts[sym] = att
            return rows, source, sym, adj, dropped, filled
        except Exception as e:  # noqa: BLE001
            attempts[sym] = repr(e)
            last_err = e
    raise RuntimeError(f"all symbols failed: {last_err!r}")


def old_history_max(sym, before):
    """전체 이력(range=max)에서 날짜 < before 인 조정 종가의 최고값과 그 날짜, 이력의 첫 날짜."""
    rows, _adj, _filled = fp.fetch_yahoo(sym, 99999)
    fp.drop_partial_bar(rows)
    old = [b for d, b in rows.items() if d < before and b["adj_close"] is not None]
    if not old:
        return None, None, min(rows) if rows else None
    best = max(old, key=lambda b: b["adj_close"])
    return float(best["adj_close"]), best["date"], min(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--axis", default=os.path.join(ROOT, "output", "prices_wide.csv"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "stocks"))
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--limit", type=int, default=0, help="시험용: 앞의 N 종목만 받는다")
    ap.add_argument("--no-ath", action="store_true", help="전체 이력 ATH 를 받지 않는다(캐시만 쓴다)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    started = dt.datetime.now(dt.timezone.utc)
    today = started.strftime("%Y-%m-%d")
    try:
        axis = load_axis(args.axis)
    except Exception as e:  # noqa: BLE001
        print(f"cannot read the SPY axis from {args.axis}: {e!r}")
        return 1
    if not axis:
        print(f"the SPY axis in {args.axis} is empty")
        return 1
    hold_date, hrows = latest_holdings(args.holdings)
    meta = json.load(open(args.meta, encoding="utf-8"))
    etfs, stocks, rows_of = universe(meta, hrows)
    keys = list(stocks)
    if args.limit:
        keys = keys[: args.limit]
    if not keys:
        print("no constituents found")
        return 1
    print(f"holdings {hold_date}: {len(etfs)} ETFs, {len(stocks)} stocks; axis {axis[0]} ~ {axis[-1]} ({len(axis)} days)")

    ath_path = os.path.join(args.out, "ath.json")
    ath = json.load(open(ath_path, encoding="utf-8")) if os.path.exists(ath_path) else {}

    state = {"stooq_down": False, "stooq_failures": 0}
    series, info, failed, warnings = {}, {}, {}, []
    for i, key in enumerate(keys, 1):
        st = stocks[key]
        attempts = {}
        try:
            rows, source, sym, adj, dropped, filled = fetch_one(st["yahoo"], args.days, state, attempts)
        except Exception as e:  # noqa: BLE001
            failed[key] = {"attempts": attempts, "error": repr(e)}
            print(f"{i:>3}/{len(keys)} {key:<10} FAILED  {e}")
            time.sleep(PAUSE)
            continue
        # SPY 축에 맞춘다: 현지 휴일(축에는 있고 종목에는 없는 날)은 직전 값을 쓰고, 상장 전 구간은 비운다
        dates = sorted(rows)
        col, last_val, filled_days, j = {}, None, 0, 0
        for d in axis:
            while j < len(dates) and dates[j] <= d:
                last_val = rows[dates[j]]["adj_close"]
                j += 1
            if last_val is None:
                continue
            if d not in rows:
                filled_days += 1
            col[d] = float(last_val)
        if not col:
            failed[key] = {"attempts": attempts, "error": "no bars on the SPY axis"}
            print(f"{i:>3}/{len(keys)} {key:<10} FAILED  no bars on the axis")
            continue
        series[key] = col
        first = min(col)
        # ---- 2년 창 이전의 최고 종가 캐시
        entry = ath.get(sym)
        reason = None
        if entry is None:
            reason = "new"
        else:
            ref_d, ref_c = entry.get("ref_date"), entry.get("ref_close")
            if ref_d and ref_c and ref_d in col and abs(col[ref_d] / ref_c - 1) > 0.02:
                reason = "readjusted"      # 액면분할·큰 배당으로 과거 종가가 다시 조정됨
            elif (dt.date.fromisoformat(today) - dt.date.fromisoformat(entry.get("fetched") or "2000-01-01")).days > ATH_REFRESH_DAYS:
                reason = "stale"
            elif entry.get("before") and entry["before"] > first:
                reason = "window moved back"   # 캐시 경계가 창 안에 들어옴(거의 없음)
        if reason and not args.no_ath:
            try:
                v, when, hist_first = old_history_max(sym, first)
                ref_d = axis[len(axis) // 2] if axis[len(axis) // 2] in col else first
                entry = {"ath_old": v, "ath_old_date": when, "before": first, "history_from": hist_first,
                         "ref_date": ref_d, "ref_close": col[ref_d], "fetched": today}
                time.sleep(PAUSE)
            except Exception as e:  # noqa: BLE001
                warnings.append(f"{key}: full history not fetched ({e!r}); the ATH uses the 2y window only" + (" (cache kept)" if entry else ""))
                if entry is None:
                    entry = {"ath_old": None, "ath_old_date": None, "before": first, "history_from": None,
                             "ref_date": None, "ref_close": None, "fetched": None}
        elif reason and args.no_ath and entry is None:
            entry = {"ath_old": None, "ath_old_date": None, "before": first, "history_from": None,
                     "ref_date": None, "ref_close": None, "fetched": None}
        ath[sym] = entry
        info[key] = {"symbol": sym, "source": source, "exch": st["exch"], "code": st["code"], "name": st["name"],
                     "first": first, "last": max(col), "days": len(col), "filled_days": filled_days,
                     "adj_close_available": adj, "dropped_partial": dropped, "filled_from_quote": filled,
                     "ath_old": entry["ath_old"], "ath_old_date": entry["ath_old_date"], "ath_refreshed": reason}
        print(f"{i:>3}/{len(keys)} {key:<10} {sym:<12} {source:<5} {first} ~ {max(col)} ({len(col)} days, ffill {filled_days})"
              + (f"  ATH cache: {reason}" if reason else ""))
        time.sleep(PAUSE)

    cols = [k for k in keys if k in series]
    with open(os.path.join(args.out, "prices_wide.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + cols)
        for d in axis:
            w.writerow([d] + [("" if d not in series[k] else f"{series[k][d]:.4f}") for k in cols])
    with open(ath_path, "w", encoding="utf-8") as f:
        json.dump(ath, f, ensure_ascii=False, indent=1, sort_keys=True)
    summary = {
        "generated_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "holdings_snapshot": hold_date, "axis_first": axis[0], "axis_last": axis[-1], "axis_days": len(axis),
        "etfs": etfs, "stocks": len(keys), "fetched": len(cols),
        "last_date_differs": {k: v["last"] for k, v in info.items() if v["last"] != axis[-1]},
        "failed": failed, "tickers": info, "warnings": warnings, "stooq_down": state["stooq_down"],
    }
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(cols)} series to {args.out}/prices_wide.csv; failed {len(failed)}; warnings {len(warnings)}")
    for k, v in failed.items():
        print(f"  failed {k}: {v['error']}")
    return 2 if failed or warnings else 0


if __name__ == "__main__":
    sys.exit(main())
