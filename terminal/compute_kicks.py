#!/usr/bin/env python3
"""킥: 주마다 '그 주를 결정한 하루'를 종가로 정하고, 규칙으로 찾을 수 있는 원인을 붙인다 (→ output/kicks.json).

정하는 규칙
  1. 그 주 수익률을 비교 대상과 견준다. 종목은 그 ETF, ETF 는 기준(산업·테마는 상위 섹터 ETF, 나머지는 SPY)이다.
     차이가 TH_WEEK(1%p) 안이면 '같이'로 두고 비교 대상의 킥을 따른다. 다만 하루 차이가 TH_DAY(1%p) 를 넘은 실적일이 있으면 그 실적이 킥이다.
  2. 차이가 TH_WEEK 를 넘으면, 같은 방향으로 가장 크게 벌어진 날(TH_DAY 이상)을 그 주를 결정한 하루로 고른다.
     그런 날이 없으면 '여러 날에 나뉨'이다.
  3. ETF 한 주에 붙이는 자기 킥(위의 날과 실적)은 영향(비중 × |주간 차이|)이 큰 CAP(3) 종목까지다. 나머지는 '영향이 작아 붙이지 않음'.
     같은 회사의 두 번째 주식(GOOG 등, SHARE_FOLD)은 첫 번째 주식에 비중을 더해 한 종목으로 고르고, top·over 에 따로 넣지 않는다.
  해외 거래소 종목은 장 시간이 미국 ETF 와 달라 하루 차이를 비교하지 않고, 같은 방향으로 가장 크게 움직인 날(TH_DAY 이상)을 쓴다.
  주도 종목(기여도 1주·1개월)은 가장 크게 기여한 ETF 와 견준다. 종목 수익률과 주간 차이의 방향이 다르면(같은 방향으로 덜 움직임) '같이'다.

원인
  규칙: 그날이 실적 반응일이면 '실적 발표'(8-K Item 2.02). 그날(앞 거래일 16:00 ~ 그날 16:00, 뉴욕) Finnhub 제목에
        같은 방향의 목표가·투자의견 변경이 있으면 '<회사> 목표가 상향' 처럼 적는다. 실적이 있으면 실적을 먼저 쓴다.
  Claude: 나머지는 terminal/claude_notes.py 가 웹 검색으로 찾아 output/kick-notes.json 에 쌓고, build_terminal.py 가 이 파일과 합친다.

입력  output/prices_wide.csv(ETF), output/stocks/prices_wide.csv·extra_prices_wide.csv(종목), output/holdings/<최신>.csv,
      output/news-data.json(실적·공시), output/news/headlines.json(Finnhub 4주 제목, 같은 실행에서만 있음),
      output/contrib-data.json(주도 종목), data/etf_meta.json
출력  output/kicks.json
  asof · weeks(이번 주부터 월요일 WEEKS 개) · th · cap · claude(이 빌드에 Claude 단계가 켜졌는지)
  etf[ETF]     {"b": 기준, "w": [주마다 {"r", "rb", "d", "dr", "db", "s": {종목: [r, 종류, 날, 그날 r, 그날 ETF r]}, "top": [...], "over": [...]}]}
               r·rb 는 그 주 ETF·기준 수익률, d 는 ETF 의 결정한 날(없으면 null), dr·db 는 그날 ETF·기준 수익률.
               s 의 종류는 day(그 날) · earn(실적일) · spread(여러 날에 나뉨). '같이'인 종목은 [r] 만 적는다.
               top 은 자기 킥을 붙이는 종목(영향 순), over 는 상한 밖 종목. 해외 종목의 '그날 ETF r' 은 null.
  drivers[1W|1M][종목] = [ETF, 기간 r, ETF 기간 r, 종류, 날, 그날 r, 그날 ETF r]   (종류 with 는 앞 셋만)
  n[id]        규칙으로 찾은 원인 {"k": 킥, "ev": 근거, "src": [{"t", "u"}], "by": "rule", "earn": 1?}
               id = 'S:<종목>:<날>'(종목의 그날, ETF 와 상관없이 하나) · 'E:<ETF>:<날>'(ETF 의 그날)
  ask          Claude 에 맡길 순서(규칙으로 원인을 못 찾은 것 먼저, 실적 규칙만 있는 것은 뒤로)
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "breakout"))
from compute_terminal import PARENT  # noqa: E402
from fetch_stock_prices import parse_symbol  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001  (tzdata 가 없는 환경: 동부 표준시로 근사)
    NY = dt.timezone(dt.timedelta(hours=-5))

TH_WEEK = 0.01
TH_DAY = 0.01
CAP = 3
WEEKS = 4
US_EX = {"", "NYSE", "NASDAQ", "AMEX", "NYSEARCA", "NYSE ARCA", "ARCA", "BATS", "CBOE", "NYSEAMERICAN", "NYSE AMERICAN", "US"}
NON_STOCK = re.compile(r"TRS|^B\.0|^XBOX$|CASH|^\s*$", re.I)
TIMING_KO = {"bmo": "장 시작 전", "dmh": "장중", "amc": "장 마감 후"}
SHARE_FOLD = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA", "BRK.A": "BRK.B"}   # 같은 회사의 두 번째 주식 → 첫 번째 주식(페이지의 뉴스 칼럼과 같다)


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_wide(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        head = next(rd)
        data = {c: {} for c in head[1:]}
        for row in rd:
            for c, v in zip(head[1:], row[1:]):
                if v not in ("", None):
                    data[c][row[0]] = float(v)
    return data


def r4(x):
    return None if x is None else round(x, 5)


# ---------- 가격 ----------
class Series:
    """한 종목·ETF 의 종가. 날짜 오름차순."""

    def __init__(self, closes):
        self.d = sorted(closes)
        self.c = closes

    def week(self, lo, hi):
        """[lo, hi] 안 마지막 종가 / lo 앞 마지막 종가 − 1."""
        ins = [d for d in self.d if lo <= d <= hi]
        bef = [d for d in self.d if d < lo]
        if not ins or not bef or not self.c[bef[-1]]:
            return None
        return self.c[ins[-1]] / self.c[bef[-1]] - 1

    def days(self, lo, hi):
        """{날: 하루 수익률} (lo~hi 안의 날, 바로 앞 종가 대비)."""
        out = {}
        for a, b in zip(self.d, self.d[1:]):
            if lo <= b <= hi and self.c[a]:
                out[b] = self.c[b] / self.c[a] - 1
        return out

    def prev(self, d):
        bef = [x for x in self.d if x < d]
        return bef[-1] if bef else None


def classify(st, et, lo, hi, earn_days=(), foreign=False, driver=False):
    """(종류, r, R, 날, 그날 r, 그날 비교 r). 종류: with · earn · day · spread. 자료가 모자라면 None."""
    r, R = st.week(lo, hi), et.week(lo, hi)
    if r is None or R is None:
        return None
    x = r - R
    sd, ed = st.days(lo, hi), et.days(lo, hi)
    if foreign:
        rel = dict(sd)
    else:
        rel = {d: v - ed[d] for d, v in sd.items() if d in ed}
    # 주도 종목은 그 종목이 기간 동안 움직인 방향으로 그날도 TH_DAY 넘게 움직인 날만 본다(ETF 보다 덜 내린 날 같은 것은 빼고)
    own_ok = (lambda d: abs(sd[d]) >= TH_DAY and (sd[d] > 0) == (r > 0)) if driver else (lambda d: True)
    earn = [d for d in rel if d in earn_days and own_ok(d)]
    if abs(x) < TH_WEEK or (driver and (r > 0) != (x > 0)):
        e = [d for d in earn if abs(rel[d]) >= TH_DAY] if abs(x) < TH_WEEK else []
        if e:
            d = max(e, key=lambda d: abs(rel[d]))
            return ("earn", r, R, d, sd[d], None if foreign else ed.get(d))
        return ("with", r, R, None, None, None)
    cand = [d for d, v in rel.items() if abs(v) >= TH_DAY and (v > 0) == (x > 0) and own_ok(d)]
    if not cand:
        return ("spread", r, R, None, None, None)
    d = max(cand, key=lambda d: abs(rel[d]))
    return ("earn" if d in earn_days else "day", r, R, d, sd[d], None if foreign else ed.get(d))


def week_starts(asof):
    d = dt.date.fromisoformat(asof)
    mon = d - dt.timedelta(days=d.weekday())
    return [(mon - dt.timedelta(days=7 * i)).isoformat() for i in range(WEEKS)]   # 이번 주부터


def sunday(mon):
    return (dt.date.fromisoformat(mon) + dt.timedelta(days=6)).isoformat()


# ---------- 규칙 원인 ----------
FIRMS = [  # (정규식, 표시 이름)
    (r"J\.?\s?P\.?\s?Morgan|JPMorgan", "JP Morgan"), (r"Goldman Sachs", "Goldman Sachs"), (r"Morgan Stanley", "Morgan Stanley"),
    (r"Bank of America|BofA|BoA Securities", "BofA"), (r"Citi(group)?\b", "Citi"), (r"Wells Fargo", "Wells Fargo"),
    (r"Barclays", "Barclays"), (r"UBS", "UBS"), (r"Deutsche Bank", "Deutsche Bank"), (r"Jefferies", "Jefferies"),
    (r"Evercore( ISI)?", "Evercore"), (r"Piper Sandler", "Piper Sandler"), (r"Raymond James", "Raymond James"),
    (r"RBC( Capital)?", "RBC"), (r"TD Cowen|Cowen", "TD Cowen"), (r"Bernstein", "Bernstein"), (r"Mizuho", "Mizuho"),
    (r"KeyBanc", "KeyBanc"), (r"Truist", "Truist"), (r"Stifel", "Stifel"), (r"Needham", "Needham"), (r"Oppenheimer", "Oppenheimer"),
    (r"BMO( Capital)?", "BMO"), (r"Wedbush", "Wedbush"), (r"Loop Capital", "Loop Capital"), (r"Guggenheim", "Guggenheim"),
    (r"HSBC", "HSBC"), (r"Macquarie", "Macquarie"), (r"Redburn", "Redburn"), (r"Argus( Research)?", "Argus"),
    (r"SocGen|Soci[ée]t[ée] G[ée]n[ée]rale", "Société Générale"), (r"Canaccord( Genuity)?", "Canaccord"), (r"Baird", "Baird"),
    (r"William Blair", "William Blair"), (r"Benchmark", "Benchmark"), (r"Rosenblatt", "Rosenblatt"), (r"Susquehanna", "Susquehanna"),
    (r"CFRA", "CFRA"), (r"Morningstar", "Morningstar"), (r"Erste", "Erste"), (r"DZ Bank", "DZ Bank"), (r"Daiwa", "Daiwa"),
    (r"Nomura", "Nomura"), (r"Freedom (Broker|Capital)", "Freedom Broker"), (r"Seaport", "Seaport"), (r"Melius", "Melius"),
]
FIRM_RX = [(re.compile(r"\b(?:" + p + r")\b", re.I), n) for p, n in FIRMS]
UP_PT = re.compile(r"\b(raises?|raised|boosts?|boosted|lifts?|lifted|increases?|increased|hikes?|hiked|bumps? up)\b[^.]*?\b(price target|target price|PT|target)\b", re.I)
DN_PT = re.compile(r"\b(lowers?|lowered|cuts?|reduces?|reduced|trims?|trimmed|slashes?|slashed)\b[^.]*?\b(price target|target price|PT|target)\b", re.I)
UP_RT = re.compile(r"\b(upgrades?|upgraded)\b", re.I)
DN_RT = re.compile(r"\b(downgrades?|downgraded)\b", re.I)
PT_TO = re.compile(r"\bto \$\s?([\d,]+(?:\.\d+)?)", re.I)


def josa_ga(word):
    """이/가. 영문은 L·M·N 으로 끝나면 받침이 있는 것으로 읽는다(Intel·IBM·Morgan). -er 처럼 R 로 끝나면 받침이 없다(Sandler)."""
    last = word.strip()[-1:] if word.strip() else ""
    if "가" <= last <= "힣":
        return "이" if (ord(last) - 0xAC00) % 28 else "가"
    if last.isdigit():
        return "이" if last in "013678" else "가"
    return "이" if last.upper() in "LMN" else "가"


def analyst_note(title, up):
    """같은 방향의 목표가·투자의견 변경이면 (킥, 근거). 아니면 None."""
    firm = next((n for rx, n in FIRM_RX if rx.search(title)), None)
    if not firm:
        return None
    pt = PT_TO.search(title)
    pt_txt = f"{pt.group(1)}달러로 " if pt else ""
    if up and UP_RT.search(title):
        return f"{firm} 투자의견 상향", f"{firm}{josa_ga(firm)} 투자의견을 올림" + (f", 목표가 {pt.group(1)}달러" if pt else "")
    if (not up) and DN_RT.search(title):
        return f"{firm} 투자의견 하향", f"{firm}{josa_ga(firm)} 투자의견을 내림" + (f", 목표가 {pt.group(1)}달러" if pt else "")
    if up and UP_PT.search(title):
        return f"{firm} 목표가 상향", f"{firm}{josa_ga(firm)} 목표가를 {pt_txt}올림"
    if (not up) and DN_PT.search(title):
        return f"{firm} 목표가 하향", f"{firm}{josa_ga(firm)} 목표가를 {pt_txt}내림"
    return None


def ny_ts(d, hm):
    return int(dt.datetime.fromisoformat(f"{d}T{hm}").replace(tzinfo=NY).timestamp())


def titles_for_day(heads, prev_day, day):
    """앞 거래일 16:00 ~ 그날 16:00(뉴욕)에 나온 제목 [unix 초, 제목, 출처, 주소]."""
    lo = ny_ts(prev_day, "16:00") if prev_day else ny_ts(day, "00:00") - 86400
    hi = ny_ts(day, "16:00")
    return [x for x in heads if lo < x[0] <= hi]


def earn_url(s, day):
    for f in s.get("f") or []:
        if len(f) > 5 and f[1].startswith("8-K") and str(f[2]).startswith("2.02") and f[5] == day:
            return f[3]
    e = (s.get("e") or [[None]])[0]
    return s.get("u") if e and e[0] == day else None


def rule_note(key, day, move, s, heads, prev_day):
    """종목의 그날 원인을 규칙으로. 실적 반응일이면 실적, 아니면 같은 방향의 목표가·투자의견 변경 제목."""
    for e in s.get("e") or []:
        if len(e) > 3 and e[3] == day:
            u = earn_url(s, e[0])
            ev = f"{int(e[0][5:7])}/{int(e[0][8:])} {TIMING_KO.get(e[1], '')} 실적 발표(8-K Item 2.02)".replace("  ", " ")
            return {"k": "실적 발표", "ev": ev, "src": [{"t": "SEC 8-K", "u": u}] if u else [], "by": "rule", "earn": 1}
    if move is None or not heads:
        return None
    for x in sorted(titles_for_day(heads, prev_day, day), key=lambda x: -x[0]):
        hit = analyst_note(x[1], move > 0)
        if hit:
            return {"k": hit[0], "ev": hit[1], "src": [{"t": x[2] or "Finnhub", "u": x[3]}], "by": "rule", "title": x[1][:160]}
    return None


# ---------- 계산 ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--etf-prices", default=os.path.join(ROOT, "output", "prices_wide.csv"))
    ap.add_argument("--stocks", default=os.path.join(ROOT, "output", "stocks"))
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--news", default=os.path.join(ROOT, "output", "news-data.json"))
    ap.add_argument("--headlines", default=os.path.join(ROOT, "output", "news", "headlines.json"))
    ap.add_argument("--contrib", default=os.path.join(ROOT, "output", "contrib-data.json"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "kicks.json"))
    args = ap.parse_args()

    E = {k: Series(v) for k, v in read_wide(args.etf_prices).items() if v}
    if "SPY" not in E:
        raise SystemExit("ETF prices not found")
    asof = E["SPY"].d[-1]
    SP = {}
    for name in ("prices_wide.csv", "extra_prices_wide.csv"):
        for k, v in read_wide(os.path.join(args.stocks, name)).items():
            if v:
                SP.setdefault(k.upper(), Series(v))
    news = load(args.news)
    S = (news.get("s") or {}) if (news.get("meta") or {}).get("asof") == asof else {}
    H = (load(args.headlines).get("stocks") or {})
    contrib = load(args.contrib)
    meta = load(args.meta)
    terminal = [t for t, m in meta.items() if "terminal" in m.get("screens", [])]

    files = sorted(f for f in os.listdir(args.holdings) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.csv", f))
    rows = list(csv.DictReader(open(os.path.join(args.holdings, files[-1]), encoding="utf-8-sig")))
    top = {t: [] for t in terminal}
    for r in sorted(rows, key=lambda r: (r["etf"], int(r.get("rank") or 99))):
        if r["etf"] in top and int(r.get("rank") or 99) <= 10:
            ex, code = parse_symbol(r.get("symbol"))
            if code and not NON_STOCK.search(code):
                top[r["etf"]].append((code.upper(), ex not in US_EX, float(r.get("weight_pct") or 0)))

    FOREIGN = {k: f for tt in top.values() for k, f, _w in tt}

    def earn_days(k):
        return {e[3] for e in (S.get(k) or {}).get("e") or [] if len(e) > 3 and e[3]}

    weeks = week_starts(asof)
    notes, need = {}, []   # need: (우선순위 묶음, 정렬 값, id)

    def note_for(k, d, move, foreign):
        sid = f"S:{k}:{d}"
        if sid in notes:
            return sid
        s = S.get(k) or {}
        st = SP.get(k)
        n = rule_note(k, d, move, s, [] if foreign else (H.get(k) or []), st.prev(d) if st else None)
        if n:
            notes[sid] = n
        return sid

    etf_out = {}
    for t in terminal:
        if t not in E:
            continue
        b = PARENT.get(t) or "SPY"
        weeks_out = []
        for i, mon in enumerate(weeks):
            lo, hi = mon, sunday(mon)
            c = classify(E[t], E[b], lo, hi) if b in E else None
            w = {"r": r4(c[1]) if c else r4(E[t].week(lo, hi)), "rb": r4(c[2]) if c else None,
                 "d": c[3] if c and c[0] in ("day",) else None, "dr": r4(c[4]) if c else None, "db": r4(c[5]) if c else None,
                 "k": c[0] if c else None, "s": {}, "top": [], "over": []}
            if c and c[0] == "day":
                need.append((0 if i == 0 else 3, (i, -abs(c[1] - c[2])), f"E:{t}:{c[3]}"))
            own = []
            held = {k for k, _f, _w in top[t]}
            fold_w = {}
            for k, _f, wt in top[t]:
                if SHARE_FOLD.get(k) in held:
                    fold_w[SHARE_FOLD[k]] = fold_w.get(SHARE_FOLD[k], 0.0) + wt
            for k, foreign, wt in top[t]:
                st = SP.get(k)
                cc = classify(st, E[t], lo, hi, earn_days(k), foreign) if st else None
                if not cc:
                    continue
                kind, r, R, d, dr, de = cc
                if kind == "with":
                    w["s"][k] = [r4(r)]
                elif kind == "spread":
                    w["s"][k] = [r4(r), "spread"]
                else:
                    w["s"][k] = [r4(r), kind, d, r4(dr), r4(de)]
                    if SHARE_FOLD.get(k) not in held:
                        own.append(((wt + fold_w.get(k, 0.0)) * abs(r - R), k, d, dr, foreign))
            own.sort(key=lambda x: -x[0])
            w["top"] = [k for _imp, k, *_ in own[:CAP]]
            w["over"] = [k for _imp, k, *_ in own[CAP:]]
            for imp, k, d, dr, foreign in own[:CAP]:
                sid = note_for(k, d, dr, foreign)
                n = notes.get(sid)
                if not n:
                    need.append((2 if i == 0 else 4, (i, -imp), sid))
                elif n.get("earn"):
                    need.append((5, (i, -imp), sid))
            weeks_out.append(w)
        etf_out[t] = {"b": b, "w": weeks_out}

    # 주도 종목: 가장 크게 기여한 ETF 와 견준다
    drv_out = {}
    D = contrib.get("drivers") or {}
    funds = contrib.get("funds") or {}
    if contrib.get("asof") == asof:
        for per in ("1W", "1M"):
            P = ((funds.get("SPY") or next(iter(funds.values()), {})).get("P") or {}).get(per)
            if not P:
                continue
            lo = (dt.date.fromisoformat(P["from"]) + dt.timedelta(days=1)).isoformat()
            hi = P["to"]
            out = {}
            for basis in ("usd", "spy"):
                for side in ("up", "dn"):
                    for x in ((D.get(per) or {}).get(basis) or {}).get(side) or []:
                        sym = str(x.get("s") or "")
                        if sym in out or not x.get("e"):
                            continue
                        key = sym.split("·")[0].strip().upper()
                        e_t = max(x["e"], key=lambda e: abs(e[1]))[0]
                        st, et = SP.get(key), E.get(e_t)
                        if not st or not et:
                            continue
                        foreign = FOREIGN.get(key, False)
                        cc = classify(st, et, lo, hi, earn_days(key), foreign, driver=True)
                        if not cc:
                            continue
                        kind, r, R, d, dr, de = cc
                        if kind in ("with", "spread"):
                            out[sym] = [e_t, r4(r), r4(R), kind]
                        else:
                            out[sym] = [e_t, r4(r), r4(R), kind, d, r4(dr), r4(de)]
                            sid = note_for(key, d, dr, foreign)
                            n = notes.get(sid)
                            if not n:
                                need.append((1, (0 if per == "1W" else 1, -abs(x.get("r") or 0)), sid))
                            elif n.get("earn"):
                                need.append((5, (0, 0), sid))
            drv_out[per] = out

    ask, seen = [], set()
    for _g, _o, i in sorted(need, key=lambda z: (z[0], z[1])):
        if i not in seen:
            seen.add(i)
            ask.append(i)
    off = (os.environ.get("CLAUDE_NOTES") or "").strip().lower() in ("off", "false", "0", "no") \
        or (os.environ.get("CLAUDE_KICKS") or "").strip().lower() in ("off", "false", "0", "no")
    claude = os.environ.get("HAS_TOKEN") == "true" and not off
    out = {"asof": asof, "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "weeks": weeks, "th": {"week": TH_WEEK, "day": TH_DAY}, "cap": CAP, "claude": claude,
           "etf": etf_out, "drivers": drv_out, "n": notes, "ask": ask}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    n_own = sum(len(w["top"]) for e in etf_out.values() for w in e["w"])
    n_etf = sum(1 for e in etf_out.values() for w in e["w"] if w["d"])
    print(f"kicks for {asof}: {len(etf_out)} ETFs × {len(weeks)} weeks · ETF days {n_etf} · stock kicks {n_own} "
          f"· drivers {sum(len(v) for v in drv_out.values())} · rule causes {len(notes)} ({sum(1 for n in notes.values() if n.get('earn'))} earnings) "
          f"· to ask {len(ask)} · Claude {'on' if claude else 'off'} -> {os.path.relpath(args.out, ROOT)} ({os.path.getsize(args.out) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
