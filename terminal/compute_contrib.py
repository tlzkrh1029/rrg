#!/usr/bin/env python3
"""구성종목 기여도를 계산해 output/contrib-data.json 으로 쓴다.

표준 라이브러리만 쓴다. 입력은 수집 단계가 남긴 파일이다.
  output/prices_wide.csv                 ETF·SPY·QQQ 조정 종가(SPY 거래일 축)
  output/stocks/prices_wide.csv          돌파 지도 수집 종목의 조정 종가(현지 통화)
  output/stocks/extra_prices_wide.csv    상위 10 밖으로 밀려난 종목 등 보조 종목(terminal/fetch_contrib_extras.py)
  output/stocks/fx_wide.csv              1달러당 통화 수량
  output/stocks/summary.json, extra_summary.json   종목별 거래소·통화
  output/holdings/<KST 날짜>.csv         구성종목 스냅샷
  data/etf_meta.json, output/prices_summary.json  화면 구분, 구성종목 출처(Roundhill 여부)

계산 규칙
- 기여 = 기간 시작 비중 × 기간 수익률(USD). 기간의 시작은 1D 가 전 거래일 종가, 1W 가 직전 주(이번 주 월요일 앞)의 마지막 거래일 종가,
  1M 이 직전 달(이번 달 1일 앞)의 마지막 거래일 종가다(주간·월간 누적). 페이지의 주간·월간 변동률과 같은 정의다.
- 21D 는 최근 21거래일(약 1개월)의 이동 창이다. 화면의 기여도 블록에는 나오지 않고, '왜 움직였나' 데이터 요약(compute_news.py)과
  Claude 이유 문장의 ETF 고르기(claude_notes.py)가 '최근 1개월'로 쓴다. 월초에도 한 달 치를 보게 하려는 것이다.
- 기간 시작 비중: 스냅샷 비중을 그 스냅샷의 기준 종가부터 기간 시작 종가까지 가격 변화로 옮긴다(매수 후 보유).
    w(t) = w(스냅샷) × (종목 가격 t ÷ 종목 가격 기준일) ÷ (ETF 가격 t ÷ ETF 가격 기준일)
- 스냅샷 기준일: 스냅샷 날짜(KST) 이전의 마지막 미국 종가에서 ETF 마다 lag 거래일 앞이다. lag 는 연속한 스냅샷 사이의
  비중 변화와 가격 변화가 가장 잘 맞는 값(0~3)을 고른다. 비교할 짝이 3개 미만이면 Roundhill 0, 나머지 1 을 쓴다.
- 해외 종목은 현지 종가를 같은 날 환율로 나눠 USD 로 바꾼다. 행에는 현지 수익률과 환율 수익률을 함께 남긴다.
  해외 종목은 미국 장보다 먼저 마감하므로 하루(1D) 기여는 미국 장의 움직임을 아직 담지 못한다(시차 표시).
- 나머지 = ETF 수익률 − 표에 있는 종목 기여의 합. 표의 비중 합이 98% 이상이면 다른 종목이 남지 않으므로
  화면에서는 '미설명분'(현금·보수·시차·스왑 등)으로 적고 평균 수익률을 계산하지 않는다.
- 기간 시작 구성: 기간 시작에 가장 가까운 스냅샷의 상위 10으로 다시 계산한 값. 균등가중 ETF 처럼 상위 10이 자주 바뀌면
  지금 구성 기준 목록에는 기간 중 오른 종목만 남기 때문에 함께 둔다. 스냅샷 출처가 다르면(스왑 행을 따로 적은 표 ↔ 합산한 표) 만들지 않는다.
- 주간 히트맵: 최근 8주(ISO 주의 마지막 거래일 기준) 동안 지금 구성 종목의 주간 기여.
- 주도 종목: 터미널 ETF 의 상위 10 종목 기여를 종목(같은 회사의 여러 주식은 하나로)별로 모아, 한 ETF 에 준 가장 큰 기여로 순위를 매긴다.
  기준은 USD 기여와 SPY 대비 기여(비중 × (종목 수익률 − SPY 수익률)) 두 가지다.
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from compute_terminal import SECTORS, INDUSTRY, STYLE, PARENT  # noqa: E402

TERMINAL = SECTORS + INDUSTRY + STYLE
BENCH = ["SPY", "QQQ"]
FUNDS = TERMINAL + BENCH
PERIODS = [("1D", "d"), ("1W", "w"), ("1M", "m")]   # 기간 시작: d 전 거래일 · w 직전 주 마지막 거래일 · m 직전 달 마지막 거래일
ROLL = ("21D", 21)   # 최근 1개월 이동 창(요약 문장과 Claude 이유 문장용)
HEAT_WEEKS = 8
EQUAL_WEIGHT = {"XHB", "KRE", "XBI", "XME", "XOP"}   # S&P Select Industry 계열(수정 균등가중)
LAG_MAX = 3
US_EXCH = {"", "US", "NASDAQ", "NYSE", "NYSEARCA", "NYSE ARCA", "AMEX", "NYSEAMERICAN", "NYSE AMERICAN",
           "CBOE", "CBOE BZX", "BATS", "OTC", "OTCMKTS", "OTCQX", "OTCQB"}
NON_STOCK = re.compile(r"TRS|^B\.0|^XBOX$|CASH", re.I)


def read_wide(path):
    if not os.path.exists(path):
        return [], {}
    with open(path, encoding="utf-8-sig") as f:
        r = csv.reader(f)
        h = next(r, None)
        if not h:
            return [], {}
        dates, cols = [], {c: [] for c in h[1:]}
        for row in r:
            dates.append(row[0])
            for c, v in zip(h[1:], row[1:]):
                cols[c].append(float(v) if v not in ("", None) else None)
    return dates, cols


def reindex(dates, cols, axis):
    pos = {d: i for i, d in enumerate(dates)}
    return {c: [v[pos[d]] if d in pos else None for d in axis] for c, v in cols.items()}


def parse_symbol(sym):
    s = (sym or "").strip()
    m = re.match(r"^([A-Za-z ]+?):\s*(\S.*)$", s)
    if m:
        return m.group(1).strip().upper(), m.group(2).strip()
    return "", s


def r6(x, n=6):
    return None if x is None else round(x, n)


class Data:
    def __init__(self, root):
        out = os.path.join(root, "output")
        self.D, self.etf = read_wide(os.path.join(out, "prices_wide.csv"))
        sd, scols = read_wide(os.path.join(out, "stocks", "prices_wide.csv"))
        xd, xcols = read_wide(os.path.join(out, "stocks", "extra_prices_wide.csv"))
        fd, fcols = read_wide(os.path.join(out, "stocks", "fx_wide.csv"))
        self.stk = {**reindex(xd, xcols, self.D), **reindex(sd, scols, self.D)}
        self.fx = reindex(fd, fcols, self.D)
        main_sum = self._json(os.path.join(out, "stocks", "summary.json"))
        extra_sum = self._json(os.path.join(out, "stocks", "extra_summary.json"))
        self.exch = {k: (v.get("exch") or "") for k, v in (main_sum.get("tickers") or {}).items()}
        self.exch.update({k: (v.get("exch") or "") for k, v in (extra_sum.get("extras") or {}).items()})
        self.currency = extra_sum.get("currency") or {}
        self.extra_failed = sorted((extra_sum.get("failed") or {}).keys())
        psum = self._json(os.path.join(out, "prices_summary.json"))
        self.hsource = ((psum.get("holdings") or {}).get("sources") or {})
        self.snaps = []
        hdir = os.path.join(out, "holdings")
        for f in sorted(os.listdir(hdir)):
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.csv", f):
                continue
            by = {}
            for r in csv.DictReader(open(os.path.join(hdir, f), encoding="utf-8-sig")):
                try:
                    if int(r.get("rank") or 99) > 10:
                        continue
                    w = float(r["weight_pct"]) / 100
                except (ValueError, KeyError):
                    continue
                by.setdefault(r["etf"], []).append((r["symbol"], r.get("name") or "", w))
            self.snaps.append((f[:-4], by))
        self._usd = {}

    @staticmethod
    def _json(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def key_of(self, symbol):
        exch, code = parse_symbol(symbol)
        if not code or NON_STOCK.search(code):
            return None
        for k in (code, f"{code}.{exch or 'US'}"):
            if k in self.stk:
                return k
        return None

    def foreign(self, k):
        return (self.exch.get(k) or "") not in US_EXCH

    def usd(self, k):
        """(USD 가격 열, 통화, 환율 없음 여부). 미국 종목은 그대로."""
        if k in self._usd:
            return self._usd[k]
        P = self.stk.get(k)
        c = self.currency.get(k)
        res = (P, None, False)
        if P is not None and c and c.get("cur") and c["cur"] != "USD":
            f = self.fx.get(c["cur"])
            div = float(c.get("div") or 1)
            if f is None:
                res = ([None if p is None else p / div for p in P], c["cur"], True)
            else:
                res = ([None if p is None or x in (None, 0) else p / div / x for p, x in zip(P, f)], c["cur"], False)
        elif P is not None and c and c.get("div", 1) != 1:
            res = ([None if p is None else p / float(c["div"]) for p in P], None, False)
        elif P is not None and not c and self.foreign(k):
            res = (P, self.exch.get(k) or "?", True)    # 통화를 확인하지 못한 해외 종목: 현지 가격 그대로, '환율 없음' 표시
        self._usd[k] = res
        return res


def ucb(D, kst):
    """KST 날짜 kst 이전의 마지막 미국 거래일 인덱스."""
    i = len(D) - 1
    while i >= 0 and D[i] >= kst:
        i -= 1
    return i


def style_of(rows):
    return "raw" if any(NON_STOCK.search(parse_symbol(s)[1] or "") for s, _n, _w in rows) else "folded"


def fit_lag(X, E):
    snaps = [(k, s[E]) for k, s in X.snaps if E in s]
    tot, n = {L: 0.0 for L in range(LAG_MAX + 1)}, 0
    for (ka, A), (kb, B) in zip(snaps, snaps[1:]):
        if style_of(A) != style_of(B):
            continue
        wa = {s: w for s, _n, w in A if w > 0}
        wb = {s: w for s, _n, w in B if w > 0}
        common = [s for s in wa if s in wb and X.key_of(s)]
        if len(common) < 4 or all(abs(wa[s] - wb[s]) < 1e-9 for s in common):
            continue
        ia, ib = ucb(X.D, ka), ucb(X.D, kb)
        if ia - LAG_MAX < 0:
            continue
        ys = [math.log(wb[s] / wa[s]) for s in common]
        my = sum(ys) / len(ys)
        ys = [y - my for y in ys]
        errs = {}
        for L in tot:
            xs = []
            for s in common:
                U = X.usd(X.key_of(s))[0]
                a, b = U[ia - L], U[ib - L]
                xs.append(math.log(b / a) if a and b else 0.0)
            mx = sum(xs) / len(xs)
            errs[L] = sum((y - (x - mx)) ** 2 for x, y in zip(xs, ys))
        n += 1
        for L in tot:
            tot[L] += errs[L]
    if n >= 3:
        return min(tot, key=tot.get), True, n
    return (0 if str(X.hsource.get(E, "")).startswith("roundhill") else 1), False, n


def end_index(X, E):
    P = X.etf.get(E) or []
    i = len(X.D) - 1
    while i > len(X.D) - 4 and (i >= len(P) or P[i] is None):
        i -= 1
    return i


def period_start(D, e, kind):
    """기간 시작 인덱스. d 는 전 거래일, w 는 D[e] 가 속한 주의 월요일 앞 마지막 거래일, m 은 그 달 1일 앞 마지막 거래일."""
    if kind == "d":
        return e - 1
    end = dt.date.fromisoformat(D[e])
    cut = (end - dt.timedelta(days=end.weekday()) if kind == "w" else end.replace(day=1)).isoformat()
    s = e
    while s > 0 and D[s] >= cut:
        s -= 1
    return s


def period(X, E, rows, a, s, e):
    """rows: [(symbol, name, w)] 스냅샷(기준일 a)의 상위 10. 기간 [s, e] 의 기여."""
    PE = X.etf[E]
    if a < 0 or PE[a] is None or PE[s] is None or PE[e] is None:
        return None
    R = PE[e] / PE[s] - 1
    out_rows, missing, base = [], [], []
    for sym, name, w in rows:
        k = X.key_of(sym)
        disp = parse_symbol(sym)[1]
        if k is None:
            if not NON_STOCK.search(disp or ""):
                missing.append(disp)
            continue
        U, cur, nofx = X.usd(k)
        if U is None or None in (U[a], U[s], U[e]):
            missing.append(disp)
            continue
        ws = w * (U[s] / U[a]) / (PE[s] / PE[a])
        r = U[e] / U[s] - 1
        row = [disp, k, r6(ws), r6(r, 5), r6(ws * r), 1 if X.foreign(k) else 0]
        if cur:
            L = X.stk[k]
            f = X.fx.get(cur)
            rl = L[e] / L[s] - 1 if L[s] and L[e] else None
            rf = (f[s] / f[e] - 1) if (f and f[s] and f[e]) else None
            row += [cur, r6(rl, 5), r6(rf, 5), 1 if nofx else 0]
        out_rows.append(row)
        base.append((disp, w, U))
    cov = sum(x[2] for x in out_rows)
    expl = sum(x[4] for x in out_rows)
    rest_w = 1 - cov
    rest = [r6(rest_w), r6(R - expl), r6((R - expl) / rest_w, 5) if abs(rest_w) > 0.02 else None]
    days = []
    for t in range(s + 1, e + 1):
        if PE[t] is None or PE[t - 1] is None:
            continue
        cs = []
        for disp, w, U in base:
            if U[t] is None or U[t - 1] is None:
                continue
            wt = w * (U[t - 1] / U[a]) / (PE[t - 1] / PE[a])
            cs.append((disp, wt * (U[t] / U[t - 1] - 1)))
        if not cs:
            continue
        cs.sort(key=lambda z: -z[1])
        days.append([X.D[t], r6(PE[t] / PE[t - 1] - 1, 5), cs[0][0], r6(cs[0][1]), cs[-1][0], r6(cs[-1][1])])
    return {"R": r6(R, 6), "rows": out_rows, "rest": rest, "days": days, "missing": missing}


def week_ends(D):
    out = []
    for i, d in enumerate(D):
        nxt = dt.date.fromisoformat(D[i + 1]).isocalendar()[:2] if i + 1 < len(D) else None
        if nxt != dt.date.fromisoformat(d).isocalendar()[:2]:
            out.append(i)
    return out


def heat(X, E, rows, a, e):
    ends = [i for i in week_ends(X.D) if i <= e][-(HEAT_WEEKS + 1):]
    if ends and ends[-1] != e:
        ends = ends[1:] + [e] if len(ends) > HEAT_WEEKS else ends + [e]
    PE = X.etf[E]
    names = [parse_symbol(s)[1] for s, _n, _w in rows if X.key_of(s)]
    H = {"weeks": [], "partial": dt.date.fromisoformat(X.D[e]).weekday() < 4, "rows": {n: [] for n in names}, "rest": [], "R": []}
    for p, q in zip(ends, ends[1:]):
        dd = dt.date.fromisoformat(X.D[q])
        H["weeks"].append(f"{dd.month}/{dd.day}")
        if PE[p] is None or PE[q] is None or PE[a] is None:
            H["R"].append(None)
            H["rest"].append(None)
            for n in names:
                H["rows"][n].append(None)
            continue
        R = PE[q] / PE[p] - 1
        tot = 0.0
        for sym, _n, w in rows:
            k = X.key_of(sym)
            if not k:
                continue
            disp = parse_symbol(sym)[1]
            U = X.usd(k)[0]
            if U is None or None in (U[a], U[p], U[q]):
                H["rows"][disp].append(None)
                continue
            wp = w * (U[p] / U[a]) / (PE[p] / PE[a])
            c = wp * (U[q] / U[p] - 1)
            H["rows"][disp].append(r6(c))
            tot += c
        H["R"].append(r6(R))
        H["rest"].append(r6(R - tot))
    return H


def company(name):
    n = re.sub(r"\b(class [a-z]|cl [a-z])\b", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]", "", n)


def drivers(per_fund, spy_R):
    """per_fund: {E: {'1W': period, '1M': period}}. 종목(회사)별로 ETF 기여를 모아 순위를 매긴다."""
    out = {}
    for p in ("1W", "1M"):
        out[p] = {}
        for basis in ("usd", "spy"):
            groups, order = {}, []
            for E in TERMINAL:
                X = per_fund.get(E, {}).get(p)
                if not X:
                    continue
                for row in X["rows"]:
                    disp, k, w, r, c = row[:5]
                    name = X.get("names", {}).get(disp, "")
                    g = company(name) or disp
                    if g not in groups:
                        groups[g] = {"syms": [], "n": name, "r": r, "e": {}}
                        order.append(g)
                    G = groups[g]
                    if disp not in G["syms"]:
                        G["syms"].append(disp)
                    v = c if basis == "usd" else w * (r - (spy_R.get(p) or 0))
                    G["e"][E] = G["e"].get(E, 0.0) + v
            items = []
            for g in order:
                G = groups[g]
                e = sorted(G["e"].items(), key=lambda kv: -abs(kv[1]))
                items.append({"s": "·".join(G["syms"]), "n": G["n"], "r": G["r"], "e": [[E, r6(v)] for E, v in e],
                              "max": max(v for _E, v in e), "min": min(v for _E, v in e)})
            up = sorted(items, key=lambda x: -x["max"])[:8]
            dn = sorted(items, key=lambda x: x["min"])[:8]
            for x in up + dn:
                x.pop("max", None)
                x.pop("min", None)
            out[p][basis] = {"up": up, "dn": dn}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "contrib-data.json"))
    args = ap.parse_args()

    X = Data(args.root)
    if not X.D or not X.snaps:
        raise SystemExit("no prices or no holdings snapshot")
    latest_kst, latest = X.snaps[-1]
    funds_out, per_fund, warn = {}, {}, []
    spy_R = {}
    for E in FUNDS:
        if E not in X.etf or E not in latest:
            warn.append(f"{E}: no price column or not in the latest snapshot")
            continue
        rows = latest[E]
        lag, fitted, npairs = fit_lag(X, E)
        e = end_index(X, E)
        a = ucb(X.D, latest_kst) - lag
        names = {parse_symbol(s)[1]: n for s, n, _w in rows}
        snap_cov = sum(w for s, _n, w in rows if X.key_of(s))
        src = str(X.hsource.get(E, ""))
        F = {"snap": latest_kst, "asof": X.D[a] if a >= 0 else None, "lag": lag, "lagFit": fitted, "pairs": npairs,
             "src": "roundhill" if src.startswith("roundhill") else "stockanalysis",
             "eqw": E in EQUAL_WEIGHT, "cov": r6(snap_cov, 4), "end": X.D[e], "P": {}}
        per_fund[E] = {}
        for p, kind in PERIODS:
            s = period_start(X.D, e, kind)
            res = period(X, E, rows, a, s, e)
            if res is None:
                continue
            res["names"] = names
            per_fund[E][p] = res
            if E == "SPY":
                spy_R[p] = res["R"]
            # 기간 시작 구성: 기준일이 기간 시작에 가장 가까운(같으면 앞선) 스냅샷
            best = None
            for kst, by in X.snaps:
                if E not in by or style_of(by[E]) != style_of(rows):
                    continue
                a2 = ucb(X.D, kst) - lag
                if a2 < 0:
                    continue
                score = (abs(a2 - s), a2 > s)
                if best is None or score < best[0]:
                    best = (score, kst, by[E], a2)
            start = None
            if best and best[1] != latest_kst:
                same = sorted(parse_symbol(x[0])[1] for x in best[2]) == sorted(parse_symbol(x[0])[1] for x in rows)
                if not same:
                    r2 = period(X, E, best[2], best[3], s, e)
                    if r2:
                        start = {"snap": best[1], "asof": X.D[best[3]], "rows": r2["rows"], "rest": r2["rest"], "missing": r2["missing"]}
            out_p = {k: res[k] for k in ("R", "rows", "rest", "days", "missing")}
            out_p["from"], out_p["to"] = X.D[s], X.D[e]
            if start:
                out_p["start"] = start
            F["P"][p] = out_p
        s = e - ROLL[1]
        res = period(X, E, rows, a, s, e) if s >= 0 else None
        if res:
            F["P"][ROLL[0]] = {k: res[k] for k in ("R", "rows", "rest", "missing")}
            F["P"][ROLL[0]]["from"], F["P"][ROLL[0]]["to"] = X.D[s], X.D[e]
        F["heat"] = heat(X, E, rows, a, e)
        F["fx"] = any(len(r) > 6 for P in F["P"].values() for r in P["rows"])
        F["tz"] = any(r[5] for P in F["P"].values() for r in P["rows"])
        funds_out[E] = F
    out = {
        "asof": X.D[-1], "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "periods": {p: k for p, k in PERIODS}, "parent": PARENT, "bench": BENCH,
        "funds": funds_out, "drivers": drivers(per_fund, spy_R),
        "fx": sorted(X.fx), "extraFailed": X.extra_failed, "warnings": warn,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    lags = " ".join(f"{E}:{F['lag']}{'' if F['lagFit'] else '*'}" for E, F in funds_out.items())
    print(f"contrib-data: asof {out['asof']}, funds {len(funds_out)}, fx {out['fx'] or 'none'}, lags {lags} -> {args.out} ({os.path.getsize(args.out) // 1024} KB)")
    for w in warn:
        print("  warning:", w)


if __name__ == "__main__":
    main()
