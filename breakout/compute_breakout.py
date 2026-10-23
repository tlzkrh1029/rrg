#!/usr/bin/env python3
"""돌파 지도의 데이터를 계산한다: 종목별 90일 종가 창, 지평별 기준 수준, 세 기준(USD·QQQ 대비·SPY 대비)의 판정.

입력
  output/prices_wide.csv           SPY 축 + SPY·QQQ·ETF 조정 종가 (fetch_prices.py)
  output/stocks/prices_wide.csv    같은 축 위의 구성종목 조정 종가 (breakout/fetch_stock_prices.py)
  output/stocks/ath.json           종목별 2년 창 이전 최고 종가 캐시
  output/holdings/<date>.csv       구성종목 스냅샷(최신 것)
  data/etf_meta.json               ETF 메타. screens 에 "breakout" 이 든 ETF 가 블록이 된다
  --lists DIR                      tradfi-ticker-lists 의 output 폴더(상장 점). 없으면 점을 비운다

출력  output/breakout-data.json
  dates/spy/qqq        마지막 NARR(90) 거래일과 그날의 SPY·QQQ 종가
  stocks[key]          n 이름, x 거래소 접두어, sym Yahoo 심볼, src 출처, c 90일 종가,
                       h52 창 이전 52주 고점, ath 창 이전 사상 최고, rspy/rqqq [52주 고점, 최고] (비율),
                       cx 창 앞 PRE_MA(160)거래일 종가(종목 상세의 50 SMA·89 HMA·144 HMA 계산용; 상장이 짧으면 그만큼만),
                       ipo 상장일(90일 창을 다 못 채운 종목만; 앞을 첫 종가로 채웠다)
  etfs[]               t ko cat tag name aum n top10 asof url rows[[key, w]] rows_all[[sym, name, w, key|null, note]] c(ETF 종가 90일|null)
  listing              match_listing 결과
  judge[mode][H][key]  [state, days, over|dist] 파이썬 판정(알림·브리핑용). 페이지의 JS 판정과 같은 규칙이다
  stats                breadth 와 블록별 켜진 칸 수 (알림용 요약)

판정 규칙(페이지의 JS 와 같다)
  선반: 지평 N일(20·55) 최고 종가에서 최근 GAP(5)일을 뺀 값. 52주·사상 최고는 창 이전 수준 h52/ath 와 창 안의 종가를 함께 본다.
  돌파: 직전 5일 종가가 모두 선반×1.005 이하였다가 오늘 종가가 그 위로 처음 올라선 날(경과 1일).
  켜짐: 돌파 후 MAXAGE(10)일 이내이고 종가가 선반×0.99 위. 실패: 돌파 후 FAILWIN(5)일 안에 선반×0.99 아래로 되돌아와 그 뒤 5일 동안.
  이탈: 거울. 바닥(최근 5일을 뺀 N일 최저; 52주·사상 최고 지평은 55일)÷1.005 아래로 처음 내려선 날부터 10일 이내.
  직전: 돌파도 이탈도 아니지만 천장(지평 고점)의 1% 안. 유지: 돌파 후 10일이 지났지만 아직 선반 위.
"""

import argparse
import csv
import datetime as dt
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from fetch_stock_prices import latest_holdings, universe  # noqa: E402
from match_listing import match  # noqa: E402

TOL, MAXAGE, FAILWIN, MARGIN, GAP, W, NEAR = 0.99, 10, 5, 1.005, 5, 30, 0.01
NARR = 90                    # 페이지에 보내는 종가 창(판정 창 30일 + 55일 선반 + 간격)
PRE52 = 252 + W - NARR       # 52주 고점에 쓰는 창 이전 구간(거래일). 판정 창의 첫날 기준으로 252일이 된다
MIN_DAYS = W + GAP + 10      # 이보다 짧은 이력은 칸을 만들지 않는다
HS = ("20", "55", "52w", "ath")
PRE_MA = 160                      # 창 앞에 더 내보내는 종가 수: HMA 144 는 144 + 12 - 1 = 155 봉이 먼저 있어야 창 첫날부터 그려진다
MODES = ("usd", "qqq", "spy")


# ---------- 판정 (JS judge 의 이식) ----------
def shelf_at(c, t, H, h52, ath):
    if H in ("20", "55"):
        N = int(H)
        m = -math.inf
        for i in range(max(0, t - N), t - GAP):
            if c[i] > m:
                m = c[i]
        return m
    m = h52 if H == "52w" else ath
    for i in range(0, t - GAP):
        if c[i] > m:
            m = c[i]
    return m


def floor_at(c, t, H):
    N = 20 if H == "20" else 55
    m = math.inf
    for i in range(max(0, t - N), t - GAP):
        if c[i] < m:
            m = c[i]
    return m


def ceiling_at(c, t, H, h52, ath):
    if H in ("20", "55"):
        N = int(H)
        return max(c[max(0, t - N):t]) if t > 0 else c[0]
    m = h52 if H == "52w" else ath
    return max([m] + c[:t]) if t > 0 else m


def near_or_none(c, last, ceiling):
    d = ceiling / c[last] - 1
    if 0 < d <= NEAR:
        return {"st": "near", "dist": d, "ceiling": ceiling}
    return {"st": "none", "dist": d, "ceiling": ceiling}


def judge(c, H, h52, ath):
    last = len(c) - 1
    up, dn = [], []
    for t in range(last - W + 1, last + 1):
        sh = shelf_at(c, t, H, h52, ath) * MARGIN
        fl = floor_at(c, t, H) / MARGIN
        prev = c[t - GAP:t]
        pm, pn = max(prev), min(prev)
        if c[t] > sh and pm <= sh:
            up.append((t, sh / MARGIN))
        if c[t] < fl and pn >= fl:
            dn.append((t, fl * MARGIN))
    ceiling = ceiling_at(c, last, H, h52, ath)
    last_up = up[-1][0] if up else -1
    last_dn = dn[-1][0] if dn else -1
    if last_dn > last_up:
        le, lvl = dn[-1]
        days = last - le + 1
        back = next((d for d in range(le + 1, last + 1) if c[d] > lvl / TOL), None)
        if back is None and days <= MAXAGE:
            return {"st": "down", "le": le, "lvl": lvl, "days": days, "over": c[last] / lvl - 1, "ceiling": ceiling}
        return near_or_none(c, last, ceiling)
    if last_up >= 0:
        le, lvl = up[-1]
        days = last - le + 1
        fail = next((d for d in range(le + 1, last + 1) if c[d] < lvl * TOL), None)
        if fail is None:
            return {"st": "lit" if days <= MAXAGE else "quiet", "le": le, "lvl": lvl, "days": days, "over": c[last] / lvl - 1, "ceiling": ceiling}
        if fail - le <= FAILWIN and last - fail < FAILWIN:
            return {"st": "failed", "le": le, "lvl": lvl, "days": days, "fail": fail, "over": c[last] / lvl - 1, "ceiling": ceiling}
    return near_or_none(c, last, ceiling)


# ---------- 입력 ----------
def read_wide(path):
    """{col: {date: float}} 와 날짜 축. 빈 칸은 없는 값."""
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        header = next(rd)
        cols = header[1:]
        axis, data = [], {c: {} for c in cols}
        for row in rd:
            d = row[0]
            axis.append(d)
            for c, v in zip(cols, row[1:]):
                if v not in ("", None):
                    data[c][d] = float(v)
    return axis, data


def level_pre(vals, pre):
    """창 이전 구간(vals: 오래된 순)의 최고값. pre 가 비었으면 None."""
    return max(pre) if pre else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--etf-prices", default=os.path.join(ROOT, "output", "prices_wide.csv"))
    ap.add_argument("--stock-prices", default=os.path.join(ROOT, "output", "stocks", "prices_wide.csv"))
    ap.add_argument("--ath", default=os.path.join(ROOT, "output", "stocks", "ath.json"))
    ap.add_argument("--stock-summary", default=os.path.join(ROOT, "output", "stocks", "summary.json"))
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--lists", default=None, help="tradfi-ticker-lists 의 output 폴더")
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "breakout-data.json"))
    args = ap.parse_args()

    axis, etfp = read_wide(args.etf_prices)
    saxis, stp = read_wide(args.stock_prices)
    if saxis != axis:
        # 종목 파일은 ETF 파일의 축으로 만들지만, 두 수집 사이에 축이 바뀌었을 수 있다: 공통 구간만 쓴다
        common = [d for d in axis if d in set(saxis)]
        if len(common) < NARR + PRE52:
            raise SystemExit(f"date axes differ too much: etf {len(axis)} days, stocks {len(saxis)} days, common {len(common)}")
        print(f"warning: axes differ (etf {axis[0]}~{axis[-1]}, stocks {saxis[0]}~{saxis[-1]}); using {len(common)} common days")
        axis = common
    if "SPY" not in etfp or "QQQ" not in etfp:
        raise SystemExit("SPY/QQQ missing in the ETF price file")
    spy_full = [etfp["SPY"].get(d) for d in axis]
    qqq_full = [etfp["QQQ"].get(d) for d in axis]
    if any(v is None for v in spy_full[-NARR:]) or any(v is None for v in qqq_full[-NARR:]):
        raise SystemExit("SPY/QQQ have gaps inside the 90-day window")
    n = len(axis)
    i0 = n - NARR                     # 90일 창의 시작 인덱스
    dates = axis[i0:]
    ath_cache = json.load(open(args.ath, encoding="utf-8")) if os.path.exists(args.ath) else {}
    ssum = json.load(open(args.stock_summary, encoding="utf-8")) if os.path.exists(args.stock_summary) else {}
    tinfo = ssum.get("tickers", {})

    hold_date, hrows = latest_holdings(args.holdings)
    meta = json.load(open(args.meta, encoding="utf-8"))
    etfs, uni, rows_of = universe(meta, hrows)
    listing = match(uni, rows_of, etfs, args.lists)

    stocks, skipped = {}, {}
    for key, st in uni.items():
        col = stp.get(key)
        if not col:
            skipped[key] = "no price data"
            continue
        full = [col.get(d) for d in axis]
        have = [i for i, v in enumerate(full) if v is not None]
        if not have:
            skipped[key] = "no bars on the axis"
            continue
        first_i = have[0]
        if n - first_i < MIN_DAYS:
            skipped[key] = f"only {n - first_i} days since {axis[first_i]}"
            continue
        # 앞쪽 결측(상장 전)은 첫 종가로, 중간 결측은 직전 값으로 채운다(수집 단계에서 이미 채워져 있지만 안전하게)
        seq, lastv = [], None
        for v in full:
            if v is None:
                v = lastv if lastv is not None else full[first_i]
            seq.append(v)
            lastv = v
        c = seq[i0:]
        pre = seq[max(first_i, i0 - PRE52):i0] if first_i < i0 else []
        pre_all = seq[first_i:i0] if first_i < i0 else []
        h52 = level_pre(seq, pre)
        if h52 is None:
            h52 = c[0]
        sym = tinfo.get(key, {}).get("symbol") or st["yahoo"][0]
        cache = ath_cache.get(sym) or {}
        ath_old = cache.get("ath_old")
        cands = [v for v in (ath_old, level_pre(seq, pre_all), h52) if v is not None]
        ath = max(cands) if cands else c[0]
        rec = {"n": st["name"], "x": st["exch"], "sym": sym, "src": tinfo.get(key, {}).get("source") or "",
               "c": [round(v, 4) for v in c], "h52": round(h52, 4), "ath": round(ath, 4),
               "athFrom": cache.get("history_from") or (axis[first_i] if first_i >= 0 else None)}
        rec["cx"] = [round(v, 2) for v in seq[max(first_i, i0 - PRE_MA):i0]]   # 이동평균용이라 소수 둘째 자리면 충분하다
        if first_i > 0 and first_i > i0 - PRE52:
            rec["ipo"] = axis[first_i]
        for bname, bfull in (("spy", spy_full), ("qqq", qqq_full)):
            ratio = [seq[i] / bfull[i] if bfull[i] else None for i in range(n)]
            r_pre = [v for v in ratio[max(first_i, i0 - PRE52):i0] if v is not None]
            r_all = [v for v in ratio[first_i:i0] if v is not None]
            r0 = ratio[i0]
            rh52 = max(r_pre) if r_pre else r0
            rath = max(r_all + [rh52]) if r_all else rh52
            rec["r" + bname] = [round(rh52, 6), round(rath, 6)]
        stocks[key] = rec

    # 블록
    out_etfs = []
    for t in etfs:
        m = meta[t]
        rows, rows_all = [], []
        for key, sym, name, w in rows_of[t]:
            wv = float(w) if w not in ("", None) else 0.0
            if key is None:
                note = "swap" if (sym and ("TRS" in sym or sym.startswith("B.0"))) else "nosym"
                rows_all.append([sym or "", name, f"{wv:.2f}%", None, note])
                continue
            if key in stocks:
                rows.append([key, f"{wv:.2f}%"])
                rows_all.append([sym, name, f"{wv:.2f}%", key, ""])
            else:
                rows_all.append([sym, name, f"{wv:.2f}%", None, "nodata"])
        ec = [etfp[t].get(d) for d in dates] if t in etfp else None
        if ec and any(v is None for v in ec):
            ec = None
        asof_long = dt.date.fromisoformat(hold_date).strftime("%b %-d, %Y")
        out_etfs.append({"t": t, "ko": m.get("ko", t), "cat": m.get("cat", "ind"), "tag": m.get("tag", "산업"),
                         "name": m["name"], "aum": m["aum"], "n": m["n"],
                         "top10": f"{sum(float(w) for _k, _s, _n, w in rows_of[t] if w not in ('', None)):.2f}%",
                         "asof": asof_long, "url": m["url"], "rows": rows, "rows_all": rows_all,
                         "c": [round(v, 4) for v in ec] if ec else None})

    # 판정(알림·브리핑용) 과 요약
    spy, qqq = spy_full[i0:], qqq_full[i0:]
    series = {}
    for key, s in stocks.items():
        series[key] = {"usd": (s["c"], s["h52"], s["ath"]),
                       "qqq": ([v / qqq[i] for i, v in enumerate(s["c"])], s["rqqq"][0], s["rqqq"][1]),
                       "spy": ([v / spy[i] for i, v in enumerate(s["c"])], s["rspy"][0], s["rspy"][1])}
    jd = {m: {H: {} for H in HS} for m in MODES}
    for key, sv in series.items():
        for m in MODES:
            c, h52, ath = sv[m]
            for H in HS:
                r = judge(c, H, h52, ath)
                jd[m][H][key] = [r["st"], r.get("days"), round(r.get("over", r.get("dist", 0.0)) or 0.0, 5)]
    cells = sum(len(e["rows"]) for e in out_etfs)
    stats = {"blocks": len(out_etfs), "cells": cells, "stocks": len(stocks), "skipped": skipped, "breadth": {}, "blocks_lit": {}}
    for m in MODES:
        stats["breadth"][m] = {}
        for H in HS:
            lit = down = failed = 0
            per = {}
            for e in out_etfs:
                l = d = f = 0
                for key, _w in e["rows"]:
                    st = jd[m][H][key][0]
                    if st == "lit":
                        l += 1
                    elif st == "down":
                        d += 1
                    elif st == "failed":
                        f += 1
                lit += l; down += d; failed += f
                per[e["t"]] = [l, d, f, len(e["rows"])]
            stats["breadth"][m][H] = {"lit": lit, "down": down, "failed": failed, "cells": cells}
            stats["blocks_lit"].setdefault(m, {})[H] = per

    out = {
        "asof": dates[-1], "first": dates[0], "days": NARR, "window": W, "axis_first": axis[0], "axis_days": n,
        "holdings_asof": hold_date, "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stock_prices_at": ssum.get("generated_at"), "sources": sorted({v["src"] for v in stocks.values() if v["src"]}),
        "dates": dates, "spy": [round(v, 4) for v in spy], "qqq": [round(v, 4) for v in qqq],
        "spyx": [round(v, 2) for v in spy_full[max(0, i0 - PRE_MA):i0]], "qqqx": [round(v, 2) for v in qqq_full[max(0, i0 - PRE_MA):i0]],
        "stocks": stocks, "etfs": out_etfs, "listing": listing, "judge": jd, "stats": stats,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    b = stats["breadth"]["usd"]
    print(f"asof {out['asof']} ({dates[0]} ~ {dates[-1]}), holdings {hold_date}: {len(out_etfs)} blocks, {cells} cells, {len(stocks)} stocks"
          + (f", skipped {len(skipped)}: " + ", ".join(f"{k} ({v})" for k, v in skipped.items()) if skipped else ""))
    print("USD lit/down/failed: " + " · ".join(f"{H} {b[H]['lit']}/{b[H]['down']}/{b[H]['failed']}" for H in HS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
