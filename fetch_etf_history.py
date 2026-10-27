#!/usr/bin/env python3
"""ETF 차트 자료: 매트릭스의 Holdings 패널과 돌파 지도의 ETF 패널이 함께 쓰는 90일 차트와 사상 최고 수준.

두 가지를 한다.
1. 사상 최고 캐시 (output/etf_ath.json)
   ETF 마다 Yahoo 전체 이력(range=max)을 받아, 받는 날의 90일 창 첫날(before) 이전의 최고 조정 종가와
   SPY·QQQ·상위 섹터로 나눈 비율의 최고값을 적어 둔다. 캐시가 없거나 REFRESH_DAYS(30)일이 지난 ETF 만
   다시 받는다(대개 월 1회 전체). 받지 못한 ETF 는 이전 값을 그대로 둔다.
   창은 앞으로만 움직이므로 before 이전의 값은 나중에도 창 이전이다. before 부터 지금 창 첫날까지는 2년 가격 파일이 덮는다.
   경계 직전 거래일(ref)의 조정 종가·비율을 함께 적어 두고, 쓸 때는 지금 가격 파일의 같은 날 값과의 비로 캐시 값을
   늘이거나 줄인다. 받은 뒤에 생긴 배당·분할 재조정이 이렇게 반영된다.
2. 차트 자료 (output/etf-chart-data.json)
   output/prices_wide.csv(SPY 거래일 축, 2년)에서 ETF 마다 90일 종가(c)와 이동평균용 창 앞 160거래일 종가(cx)를 내고,
   USD 와 세 비율(SPY·QQQ·상위 섹터 대비)마다 창 이전 52주 고점과 사상 최고를 적는다.
   사상 최고 = max(캐시의 before 이전 최고, 2년 파일 안의 창 이전 최고, 52주 고점).
   돌파 지도의 종목 칸(breakout/compute_breakout.py)과 창·규칙이 같다. 다만 종목의 비율 사상 최고는 2년 창 안에서만
   잡고, ETF 는 비율의 사상 최고도 전체 이력에서 잡는다.

출력 형식 (etf-chart-data.json)
  asof first days     90일 창의 마지막 날·첫날·길이
  dates               90일 창의 날짜
  etfs[T]             c 90일 종가, cx 창 앞 종가(최대 160), par 상위 섹터(산업·테마만)
                      lv[m] = [52주 고점, 사상 최고, 사상 최고를 찾은 이력의 첫날, 사상 최고 날짜]  (m = usd · spy · qqq · par)
                      stale 마지막 종가가 빈 날(직전 값으로 채움), ipo 창 안에서 상장한 날(앞은 첫 종가로 채움)
                      SPY·QQQ 는 c·cx 만 있다(비율의 분모)
  warnings            캐시를 쓰지 못한 ETF 와 그 이유

표준 라이브러리만 쓰고 API 키가 없다. 가격 함수는 fetch_prices.py 의 것을 쓴다.
종료 코드: 0 정상, 2 일부 ETF 의 전체 이력을 받지 못함(이전 캐시 값이나 2년 파일로 대신함), 1 가격 파일을 읽지 못함.
"""

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_prices as fp  # noqa: E402

NARR, PRE_MA = 90, 160            # breakout/compute_breakout.py 의 NARR·PRE_MA
PRE52 = 252 + 30 - NARR           # 52주 고점에 쓰는 창 이전 구간(판정 창 30일의 첫날 기준으로 252일)
MIN_DAYS = 45                     # 이보다 짧은 이력은 차트를 만들지 않는다
REFRESH_DAYS = 30
PAUSE = 0.3
BENCH = ["SPY", "QQQ"]
PARENT = {t: p for t, p in fp.PARENT_SECTOR.items() if p not in BENCH}   # 산업·테마 → 상위 섹터 (MAGS 는 SPY 라 뺀다)


def read_wide(path):
    """날짜 축과 {열: {날짜: 값}}. 빈 칸은 없는 값."""
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        head = next(rd)
        axis, data = [], {c: {} for c in head[1:]}
        for row in rd:
            axis.append(row[0])
            for c, v in zip(head[1:], row[1:]):
                if v not in ("", None):
                    data[c][row[0]] = float(v)
    return axis, data


def sig(x, n=7):
    return float(f"{x:.{n}g}")


# ---------- 1. 사상 최고 캐시 ----------
def history(sym):
    """전체 이력의 {날짜: 조정 종가}."""
    rows, _adj, _filled = fp.fetch_yahoo(sym, 99999)
    fp.drop_partial_bar(rows)
    return {d: float(b["adj_close"]) for d, b in rows.items() if b.get("adj_close") is not None}


def value_at(h, d):
    """h(날짜 → 값)에서 d 또는 그 앞의 마지막 값."""
    ks = [k for k in h if k <= d]
    return h[max(ks)] if ks else None


def max_before(h, before):
    best = None
    for d, v in h.items():
        if d < before and (best is None or v > best[0]):
            best = [sig(v), d]
    return best


def pairs_of(t):
    return BENCH + ([PARENT[t]] if t in PARENT else [])


def usable(entry, today, before, axis_set):
    """캐시 항목을 그대로 둘 수 있는가: 받은 지 REFRESH_DAYS 이내, 경계가 지금 창 첫날 이전, 기준일이 2년 축 안."""
    if not entry or not entry.get("fetched") or not entry.get("ref"):
        return False
    if (today - dt.date.fromisoformat(entry["fetched"])).days > REFRESH_DAYS:
        return False
    return entry.get("before", "9999") <= before and entry["ref"][0] in axis_set


def refresh(cache, etfs, axis, today, force):
    """낡았거나 없는 ETF 의 전체 이력을 받아 캐시를 고친다. (받은 ETF 목록, 실패 사전)"""
    i0 = len(axis) - NARR
    before, ref = axis[i0], axis[i0 - 1]
    axis_set = set(axis)
    T, P = cache.setdefault("tickers", {}), cache.setdefault("pairs", {})
    need = [t for t in etfs if force or not usable(T.get(t), today, before, axis_set)
            or any(not usable(P.get(f"{t}/{b}"), today, before, axis_set) for b in pairs_of(t))]
    if not need:
        return [], {}
    syms = []
    for s in BENCH + sorted({PARENT[t] for t in need if t in PARENT}) + need:
        if s not in syms:
            syms.append(s)
    hist, failed = {}, {}
    for s in syms:
        try:
            h = history(s)
            if not h:
                raise ValueError("empty history")
            hist[s] = h
            print(f"history {s:<5} {min(h)} ~ {max(h)} ({len(h)} days)")
        except Exception as e:  # noqa: BLE001
            failed[s] = repr(e)
            print(f"history {s:<5} FAILED  {e}")
        time.sleep(PAUSE)
    stamp = today.isoformat()
    done = []
    for t in need:
        ht = hist.get(t)
        if not ht:
            continue
        rt = value_at(ht, ref)
        T[t] = {"fetched": stamp, "before": before, "from": min(ht), "ref": [ref, sig(rt) if rt else None], "max": max_before(ht, before)}
        for b in pairs_of(t):
            hb = hist.get(b)
            if not hb:
                failed[f"{t}/{b}"] = f"{b} history missing"
                continue
            hr = {d: v / hb[d] for d, v in ht.items() if d in hb and hb[d]}
            rb = value_at(hb, ref)
            P[f"{t}/{b}"] = {"fetched": stamp, "before": before, "from": min(hr) if hr else None,
                             "ref": [ref, sig(rt / rb) if rt and rb else None], "max": max_before(hr, before)}
        done.append(t)
    return done, failed


# ---------- 2. 차트 자료 ----------
def fill(col, axis):
    """축 위의 값 목록: 앞쪽 결측(상장 전)은 첫 종가로, 중간·끝 결측은 직전 값으로 채운다. (목록, 첫 인덱스, 마지막 인덱스)"""
    full = [col.get(d) for d in axis]
    have = [i for i, v in enumerate(full) if v is not None]
    if not have:
        return None, None, None
    seq, last = [], None
    for v in full:
        if v is None:
            v = last if last is not None else full[have[0]]
        seq.append(v)
        last = v
    return seq, have[0], have[-1]


def levels(vals, fi, i0, axis, entry, now_ref):
    """[52주 고점, 사상 최고, 사상 최고를 찾은 이력의 첫날, 사상 최고 날짜]. 캐시를 쓰지 못하면 2년 파일 안에서만 찾는다."""
    rng = range(max(fi, i0 - PRE52), i0)
    if len(rng):
        j = max(rng, key=lambda i: vals[i])
        h52, best = vals[j], (vals[j], axis[j])
    else:
        h52, best = vals[i0], (vals[i0], axis[i0])
    if fi < i0:
        j = max(range(fi, i0), key=lambda i: vals[i])
        if vals[j] > best[0]:
            best = (vals[j], axis[j])
    frm, note = axis[fi], None
    if not entry or not entry.get("max") or not entry.get("ref") or not entry["ref"][1]:
        note = "no cache"
    elif entry.get("before", "9999") > axis[i0]:
        note = f"cache boundary {entry.get('before')} is inside the window"
    elif now_ref is None:
        note = f"reference day {entry['ref'][0]} is not in the price file"
    else:
        scale = now_ref / entry["ref"][1]
        v = entry["max"][0] * scale
        frm = entry.get("from") or frm
        if v > best[0]:
            best = (v, entry["max"][1])
        if abs(scale - 1) > 0.1:
            note = f"price file differs from the cached history by {scale - 1:+.1%} on {entry['ref'][0]} (rescaled)"
    return [sig(h52), sig(best[0]), frm, best[1]], note


def chart(axis, px, cache, etfs):
    n = len(axis)
    i0 = n - NARR
    T, P = cache.get("tickers", {}), cache.get("pairs", {})
    out, seqs, warnings = {}, {}, []
    for t in BENCH + etfs:
        col = px.get(t)
        seq, fi, li = fill(col, axis) if col else (None, None, None)
        if seq is None:
            warnings.append(f"{t}: no prices")
            continue
        if n - fi < MIN_DAYS:
            warnings.append(f"{t}: only {n - fi} days since {axis[fi]}")
            continue
        seqs[t] = (seq, fi)
        rec = {"c": [round(v, 4) for v in seq[i0:]], "cx": [round(v, 2) for v in seq[max(fi, i0 - PRE_MA):i0]]}
        if li < n - 1:
            rec["stale"] = axis[li]
        if fi > i0:
            rec["ipo"] = axis[fi]
        out[t] = rec
    for t in etfs:
        if t not in seqs:
            continue
        seq, fi = seqs[t]
        lv, nocache = {}, []
        e = T.get(t)
        ref_d = e["ref"][0] if e and e.get("ref") else None
        lv["usd"], note = levels(seq, fi, i0, axis, e, px[t].get(ref_d) if ref_d else None)
        if note == "no cache":
            nocache.append("usd")
        elif note:
            warnings.append(f"{t} usd: {note}")
        for m, b in [("spy", "SPY"), ("qqq", "QQQ")] + ([("par", PARENT[t])] if t in PARENT else []):
            if b not in seqs:
                continue
            sb, fb = seqs[b]
            r = [seq[i] / sb[i] for i in range(n)]
            pe = P.get(f"{t}/{b}")
            rd = pe["ref"][0] if pe and pe.get("ref") else None
            now = px[t][rd] / px[b][rd] if rd and px[t].get(rd) and px[b].get(rd) else None
            lv[m], note = levels(r, max(fi, fb), i0, axis, pe, now)
            if note == "no cache":
                nocache.append(m)
            elif note:
                warnings.append(f"{t}/{b}: {note}")
        out[t]["lv"] = lv
        if t in PARENT:
            out[t]["par"] = PARENT[t]
        if nocache:
            warnings.append(f"{t}: no full-history cache for {', '.join(nocache)}; the all-time high uses the 2-year file only")
    return {"asof": axis[-1], "first": axis[i0], "days": NARR, "dates": axis[i0:], "etfs": out, "warnings": warnings}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", default=os.path.join(HERE, "output", "prices_wide.csv"))
    ap.add_argument("--meta", default=os.path.join(HERE, "data", "etf_meta.json"))
    ap.add_argument("--cache", default=os.path.join(HERE, "output", "etf_ath.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "output", "etf-chart-data.json"))
    ap.add_argument("--no-fetch", action="store_true", help="전체 이력을 받지 않고 지금 캐시만 쓴다")
    ap.add_argument("--refresh", action="store_true", help="캐시가 새것이어도 모두 다시 받는다")
    args = ap.parse_args()

    started = dt.datetime.now(dt.timezone.utc)
    try:
        axis, px = read_wide(args.prices)
    except Exception as e:  # noqa: BLE001
        print(f"cannot read {args.prices}: {e!r}")
        return 1
    if len(axis) < NARR + MIN_DAYS or "SPY" not in px:
        print(f"{args.prices} is too short ({len(axis)} days) or has no SPY")
        return 1
    meta = json.load(open(args.meta, encoding="utf-8"))
    etfs = [t for t in meta if t in px]
    cache = json.load(open(args.cache, encoding="utf-8")) if os.path.exists(args.cache) else {}

    failed = {}
    if not args.no_fetch:
        done, failed = refresh(cache, etfs, axis, started.date(), args.refresh)
        if done or failed:
            cache["last_refresh"] = {"at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "etfs": done, "failed": failed}
            os.makedirs(os.path.dirname(args.cache), exist_ok=True)
            with open(args.cache, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)
            print(f"history cache: refreshed {len(done)} ETFs, failed {len(failed)}")
        else:
            print("history cache is fresh")

    data = chart(axis, px, cache, etfs)
    data["generated_at"] = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {os.path.relpath(args.out, HERE)}: {data['first']} ~ {data['asof']}, {len(data['etfs'])} series, {len(data['warnings'])} warnings")
    for w in data["warnings"]:
        print("  " + w)
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
