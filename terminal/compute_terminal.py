#!/usr/bin/env python3
"""output/prices_wide.csv 에서 섹터 전환 터미널의 수치를 계산해 output/terminal-data.json 으로 쓴다.

표준 라이브러리만 사용한다.

계산 규칙
- 상대수익률: ETF 수익률 − SPY 수익률. 기간은 1주 5, 1개월 21, 3개월 63, 6개월 126, 12개월 252 거래일.
- 백분위: 기간마다 값이 있는 ETF 안에서 등수를 매겨 1등 100, 꼴찌 0. 순위를 매기는 대상은 섹터 11개와 산업·테마 12개(RANKED)뿐이다.
  스타일·집중도 ETF(STYLE, MAGS)는 그 23개 사이에 끼워 넣었을 때의 위치를 백분위로 적고, 다른 ETF 의 백분위에는 영향을 주지 않는다.
- 비율선: 주봉(그 주 마지막 거래일) 종가 기준 ETF ÷ SPY, ETF ÷ QQQ 의 최근 12주.
- RRG: 주봉 RS = 100 × ETF ÷ 벤치마크.
    RS-Ratio    = 100 × SMA(RS, 10) ÷ SMA(RS, 30)
    RS-Momentum = 100 × RS-Ratio ÷ SMA(RS-Ratio, 9)
  1단은 섹터 11개 ÷ SPY, 2단은 산업·테마 12개 ÷ 상위 섹터(PARENT). 산업·테마의 SPY 대비도 따로 계산한다.
  스타일·집중도 ETF 는 SPY 로 나누어 1단과 같은 축에 놓되, 섹터 11개를 세는 집계에는 넣지 않는다.
  최근 5주 좌표를 궤적으로 남긴다. 주봉이 38주 미만이면 좌표가 비어 있다.
- 신호: 52주 상대 신고가(ETF÷SPY 일봉 비율), 비율선의 50·200일선 돌파·이탈(최근 5거래일 안),
  백분위 ±30 급변(6개월 → 1개월), 1주 반등 미확인(1주 ≥ 70, 1개월 ≤ 40).
"""
import argparse
import csv
import datetime as dt
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLB", "XLRE"]
INDUSTRY = ["SMH", "IGV", "XBI", "GNOM", "ARKG", "KRE", "XHB", "XME", "URA", "ITA", "PAVE", "DRAM"]
PARENT = {"SMH": "XLK", "IGV": "XLK", "DRAM": "XLK", "XBI": "XLV", "GNOM": "XLV", "ARKG": "XLV",
          "KRE": "XLF", "XHB": "XLY", "XME": "XLB", "ITA": "XLI", "PAVE": "XLI", "URA": "XLE"}
# 스타일·집중도: 동일가중 Mag 7 은 XLK·XLC·XLY 세 섹터에 걸쳐 있어 상위 섹터를 하나 고를 수 없으므로 SPY 로 나눈다.
STYLE = ["MAGS"]
RANKED = SECTORS + INDUSTRY   # 백분위 순위를 매기는 23개
ETFS = RANKED + STYLE
GROUP = {**{t: "sector" for t in SECTORS}, **{t: "industry" for t in INDUSTRY}, **{t: "style" for t in STYLE}}
WINDOWS = [5, 21, 63, 126, 252]
WINDOW_NAMES = ["1W", "1M", "3M", "6M", "12M"]


def sma(seq, n):
    out = []
    for i in range(len(seq)):
        win = seq[max(0, i - n + 1):i + 1]
        if len(win) < n or any(v is None for v in win):
            out.append(None)
        else:
            out.append(sum(win) / n)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", default=os.path.join(ROOT, "output", "prices_wide.csv"))
    ap.add_argument("--summary", default=os.path.join(ROOT, "output", "prices_summary.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "terminal-data.json"))
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.prices, encoding="utf-8-sig")))
    dates = [r["date"] for r in rows]
    n_days = len(dates)
    tickers = ["SPY", "QQQ"] + ETFS
    px = {}
    for t in tickers:
        px[t] = [float(r[t]) if r.get(t) not in ("", None) else None for r in rows]
    summary = {}
    if os.path.exists(args.summary):
        summary = json.load(open(args.summary, encoding="utf-8"))

    # 출처 지연으로 마지막 봉이 비어 있는 티커는 최근 3거래일 안의 마지막 값을 끝값으로 쓰고 stale 에 그 날짜를 적는다.
    # (n/a 로 비우면 그 ETF 가 백분위 풀에서 빠져 다른 ETF 의 순위까지 흔들린다.)
    END = {}
    for t in tickers:
        idx = n_days - 1
        while idx > max(0, n_days - 3) and px[t][idx] is None:
            idx -= 1
        END[t] = idx
    stale = {t: (dates[END[t]] if END[t] != n_days - 1 and px[t][END[t]] is not None else None) for t in tickers}

    def ret(t, w):
        iend = END[t]
        i = iend - w
        if i < 0:
            return None
        a, b = px[t][iend], px[t][i]
        if a is None or b is None:
            return None
        return a / b - 1

    # ---- 백분위 ----
    pct = {t: [None] * 5 for t in ETFS}
    rel = {t: [None] * 5 for t in ETFS}
    for i, w in enumerate(WINDOWS):
        rs = ret("SPY", w)
        vals = {}
        for t in ETFS:
            r = ret(t, w)
            if r is None or rs is None:
                continue
            vals[t] = r - rs
            rel[t][i] = round((r - rs) * 100, 2)
        order = sorted((t for t in RANKED if t in vals), key=lambda k: vals[k], reverse=True)
        n = len(order)
        for rank, t in enumerate(order, 1):
            pct[t][i] = round((n - rank) / (n - 1) * 100) if n > 1 else 100
        # 스타일 ETF: 23개 사이에 끼워 넣었을 때의 위치(동률은 위로). 다른 ETF 의 백분위는 바꾸지 않는다.
        pool = [vals[t] for t in order]
        for t in STYLE:
            if t in vals and n > 0:
                rank = 1 + sum(1 for v in pool if v > vals[t])
                pct[t][i] = round((n + 1 - rank) / n * 100)

    # ---- 주봉: 각 ISO 주의 마지막 거래일. 그 날 값이 비어 있으면(출처 지연) 그 주 안의 마지막 값을 쓴다 ----
    week_idx, week_members = [], []
    for i in range(n_days):
        d = dt.date.fromisoformat(dates[i])
        nxt = dt.date.fromisoformat(dates[i + 1]) if i + 1 < n_days else None
        if not week_members or dt.date.fromisoformat(dates[week_members[-1][-1]]).isocalendar()[:2] != d.isocalendar()[:2]:
            week_members.append([i])
        else:
            week_members[-1].append(i)
        if nxt is None or nxt.isocalendar()[:2] != d.isocalendar()[:2]:
            week_idx.append(i)

    def weekly(t):
        out = []
        for members in week_members:
            v = None
            for i in reversed(members):
                if px[t][i] is not None:
                    v = px[t][i]
                    break
            out.append(v)
        return out

    def ratio_series(t, bench, weeks=12):
        a, b = weekly(t), weekly(bench)
        out = [None if x is None or y is None else round(x / y, 5) for x, y in zip(a, b)]
        return out[-weeks:]

    def rrg_path(t, bench, weeks=5):
        a, b = weekly(t), weekly(bench)
        rs = [None if x is None or y is None else 100 * x / y for x, y in zip(a, b)]
        s10, s30 = sma(rs, 10), sma(rs, 30)
        ratio = [None if p is None or q in (None, 0) else 100 * p / q for p, q in zip(s10, s30)]
        s9 = sma(ratio, 9)
        mom = [None if p is None or q in (None, 0) else 100 * p / q for p, q in zip(ratio, s9)]
        path = [[round(r, 2), round(m, 2)] for r, m in zip(ratio, mom) if r is not None and m is not None]
        return path[-weeks:]

    def signals(t):
        sig = []
        iend = END[t]
        rr = [None if px[t][i] is None or px["SPY"][i] is None else px[t][i] / px["SPY"][i] for i in range(iend + 1)]
        valid = [v for v in rr if v is not None]
        if len(valid) >= 20 and rr[-1] is not None:
            hi52 = max(v for v in rr[-252:] if v is not None)
            if rr[-1] >= hi52 * 0.999:
                sig.append(["★", "SPY 대비 비율 52주 신고가", "hot"])
            for n, name in ((200, "200일선"), (50, "50일선")):
                if len(valid) < n + 6:
                    continue
                ma = sma(rr, n)
                d = [None if rr[i] is None or ma[i] is None else rr[i] - ma[i] for i in range(len(rr))]
                if d[-1] is None:
                    continue
                recent = [x for x in d[-6:-1] if x is not None]
                if d[-1] > 0 and any(x <= 0 for x in recent):
                    sig.append(["↗", f"SPY 비율 {name} 상향 돌파", "hot"])
                elif d[-1] < 0 and any(x >= 0 for x in recent):
                    sig.append(["↘", f"SPY 비율 {name} 하향 이탈", "cold"])
        p1, p6 = pct[t][1], pct[t][3]
        if p1 is not None and p6 is not None:
            if p1 - p6 >= 30:
                sig.append(["↑", f"백분위 {p1 - p6}점 상승 (6개월 → 1개월)", "hot"])
            elif p6 - p1 >= 30:
                sig.append(["↓", f"백분위 {p6 - p1}점 하락 (6개월 → 1개월)", "cold"])
        p0 = pct[t][0]
        if p0 is not None and p1 is not None and p0 >= 70 and p1 <= 40:
            sig.append(["≈", "1주 반등, 1개월 이상에서는 미확인", ""])
        return sig[:3]

    out = {
        "asof": dates[-1], "first_date": dates[0], "days": n_days, "weeks": len(week_idx),
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "price_generated_at": summary.get("generated_at"),
        "source": sorted({(summary.get("tickers") or {}).get(t, {}).get("source", "?") for t in tickers}),
        "windows": dict(zip(WINDOW_NAMES, WINDOWS)),
        "parent": PARENT,
        "groups": {"sectors": SECTORS, "industries": INDUSTRY, "style": STYLE},
        "etfs": {},
    }
    for t in ETFS:
        bench = PARENT.get(t, "SPY")
        wk = weekly(t)
        out["etfs"][t] = {
            "parent": PARENT.get(t),
            "group": GROUP[t],
            "pct": pct[t], "rel": rel[t],
            "S": ratio_series(t, "SPY"), "Q": ratio_series(t, "QQQ"),
            "rrgPath": rrg_path(t, bench), "rrgSpy": rrg_path(t, "SPY"),
            "sig": signals(t),
            "weeks": sum(1 for v in wk if v is not None),
            "lastClose": px[t][END[t]],
            "stale": stale[t],
        }
    out["stale"] = {t: d for t, d in stale.items() if d}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    missing = [t for t in ETFS if not out["etfs"][t]["rrgPath"]]
    print(f"terminal-data: asof {out['asof']}, {n_days} days, {len(week_idx)} weeks, RRG missing: {missing or 'none'} -> {args.out}")


if __name__ == "__main__":
    main()
