#!/usr/bin/env python3
"""뉴스·실적 자료를 페이지용으로 계산한다 (terminal/fetch_news.py 의 output/news/ → output/news-data.json).

입력
  output/news/earnings.json, calendar.json, headlines.json   fetch_news.py 결과(없으면 그 부분 없이 만든다)
  output/news/filings.json, filing_text.json               최근 90일 공시 목록과 8-K 첫 문장(없으면 공시 없이 만든다)
  output/prices_wide.csv                                    SPY 거래일 축
  output/stocks/prices_wide.csv, extra_prices_wide.csv      종목 종가(실적 반응 계산용)
  output/contrib-data.json                                  기여도(데이터 요약 문장용; 종가 기준일이 다르면 문장 없이 만든다)
  output/holdings/<날짜>.csv                                 최신 스냅샷의 ETF 별 상위 10 종목

출력 output/news-data.json
  meta   asof(종가 기준일), generated_at, news_at(헤드라인 수집 시각), sec_at, cal_at, src(출처별 상태)
  s[종목] tv  TradingView 심볼
         e   최근 실적 발표(8-K Item 2.02) 최대 EARN_KEEP 건: [발표일, 시점, 반응 수익률|null, 반응일|null]
             시점은 뉴욕 접수 시각으로 가른다: bmo 09:30 전(장 시작 전), dmh 장중, amc 16:00 이후(장 마감 후).
             반응은 발표 직전 종가에서 발표 뒤 첫 종가까지의 변화다(장 마감 후 발표면 다음 거래일, 장 시작 전·장중이면 그날).
         u   가장 최근 8-K 주소
         nx  다음 실적일 [날짜, bmo|amc|dmh|''] (Finnhub, 오늘 이후)
         h   헤드라인 최대 NEWS_KEEP 개 [unix 초, 제목, 출처, 주소] (Finnhub, 최근 7일. ETF 모드와 좁은 화면의 목록)
         w   최근 WEEKS 주(뉴욕 월요일 시작, 이번 주부터)의 주별 묶음 [[월요일, 기사 수, 묶음들, 일부만 받음 0|1], …]
             묶음 = [unix 초, 제목, 출처, 주소, 같은 이야기로 묶인 다른 기사 수, 묶음의 마지막 기사 unix 초(다른 주로 이어질 때만, 아니면 0)]
             회사 이름·티커·흔한 낱말을 뺀 제목 낱말이 SIM_STRONG 개 이상 겹치거나, SIM_MIN 개 이상이면서 짧은 쪽의
             SIM_RATIO 이상 겹치고, SIM_DAYS 일 안에 나온 기사는 한 묶음이다(앞 기사 어느 하나와 겹치면 된다).
             묶음은 첫 기사가 나온 주에 둔다. 주마다 제목에 회사 이름이나 티커가 있는 묶음을 먼저, 그 안에서는 점수(묶인 기사 수,
             제목의 실적·배당·목표가 같은 낱말, 출처 등급) 순으로 WEEK_KEEP 개까지 남긴다. Finnhub 주소는 'fh:<id>' 로 줄여 적는다.
         f   최근 90일 공시 [[접수일, 서식, 표시 이름, 주소, 8-K 첫 문장, 뉴욕 접수일], …] (최근 것부터)
             표시 이름은 '8.01 배당'·'5.02 임원 변동·보상'·'연간 보고서 · 회계연도 8/30 마감' 처럼 만든다. 8-K 는 첫 항목 번호와
             그 한국어 이름이고, 첫 문장에 배당·자사주 매입 같은 낱말이 있으면 그 이름으로 바꾼다.
         ins 같은 기간의 내부자 신고 건수 {"4": n, "144": n, "3": n, "5": n}, cik (내부자 신고 목록 링크용)
  etf[ETF] sum  데이터 요약 문장 (기여도 1개월·1주와 실적으로 만든다. 기준은 산업·테마가 상위 섹터, 나머지는 SPY)
           nx   상위 10 종목의 다가오는 실적 [[표시 심볼, 날짜, 시점]] (가까운 순)
           re   최근 30일 실적 발표 [[표시 심볼, 날짜]]; 없으면 last 에 가장 최근 발표
           nh   상위 10 종목의 헤드라인 수
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
sys.path.insert(0, os.path.join(ROOT, "breakout"))
from compute_terminal import PARENT  # noqa: E402
from fetch_stock_prices import parse_symbol  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001  (tzdata 가 없는 환경: 동부 표준시로 근사)
    NY = dt.timezone(dt.timedelta(hours=-5))

EARN_KEEP = 4
NEWS_KEEP = 6
WEEKS = 4                  # 주별 묶음 수(이번 주 포함)
WEEK_KEEP = 4              # 주마다 남기는 묶음 수(화면은 위 2개, 나머지는 펼치면)
SIM_MIN, SIM_RATIO, SIM_STRONG, SIM_DAYS = 2, 0.3, 3, 4
TITLE_MAX = 120
FILING_TEXT_MAX = 170
FH_URL = "https://finnhub.io/api/news?id="
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
NEWS_STALE_DAYS = 3        # 이보다 오래된 헤드라인 파일은 쓰지 않는다
CAL_STALE_DAYS = 7
SOON_DAYS = 14             # 요약 문장에 넣는 '곧 발표' 범위
RECENT_DAYS = 30
NON_STOCK = re.compile(r"TRS|^B\.0|^XBOX$|CASH|^\s*$", re.I)


def read_wide(path):
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


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def age_days(stamp, now):
    try:
        t = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return 9999
    return (now - t).total_seconds() / 86400


# ---------- 실적 반응 ----------
def timing(hm):
    return "bmo" if hm < "09:30" else ("amc" if hm >= "16:00" else "dmh")


def reaction(px, axis, at):
    """발표 직전 종가 → 발표 뒤 첫 종가. 아직 다음 종가가 없으면 (None, None)."""
    if not px:
        return None, None
    d, hm = at[:10], at[11:16]
    after = next((i for i, a in enumerate(axis) if a > d or (a == d and hm < "16:00")), None)
    if after is None or after == 0:
        return None, None
    a, b = px.get(axis[after - 1]), px.get(axis[after])
    if not a or not b:
        return None, axis[after]
    return round(b / a - 1, 4), axis[after]


# ---------- 문장 ----------
def josa(word, pair):
    """pair = (받침 있을 때, 없을 때). 영문은 L·M·N·R 로 끝나면 받침이 있는 것으로 읽는다(엘·엠·엔·알)."""
    w = re.sub(r"\(.*?\)$", "", word.strip())
    last = w[-1:] if w else ""
    if "가" <= last <= "힣":
        jong = (ord(last) - 0xAC00) % 28 != 0
    elif last.isdigit():
        jong = last in "013678"
    elif last.isalpha():
        jong = last.upper() in "LMNR"
    else:
        jong = False
    return pair[0] if jong else pair[1]


def pc(x, d=2):
    return ("+" if x >= 0 else "−") + f"{abs(x) * 100:.{d}f}"


def md(iso):
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def names_and(items):
    """['A(+1)', 'B(+2)'] → 'A(+1)와 B(+2)', 셋 이상은 가운뎃점으로 잇는다."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]}{josa(items[0], ('과', '와'))} {items[1]}"
    return "·".join(items)


SHARE_FOLD = {"GOOG": "GOOGL", "FOX": "FOXA", "NWS": "NWSA", "BRK.A": "BRK.B"}   # 같은 회사의 두 번째 주식 → 첫 번째 주식


def fold_rows(rows):
    """기여도 행에서 같은 회사의 두 번째 주식을 첫 번째 주식 행에 합친다(비중·기여는 더하고 수익률은 합친 기여 ÷ 합친 비중)."""
    have = {str(x[0]).upper() for x in rows}
    out = [list(x) for x in rows if SHARE_FOLD.get(str(x[0]).upper()) not in have]
    for x in rows:
        p = SHARE_FOLD.get(str(x[0]).upper())
        if p in have:
            o = next(y for y in out if str(y[0]).upper() == p)
            o[2] = (o[2] or 0) + (x[2] or 0)
            o[4] = (o[4] or 0) + (x[4] or 0)
            o[3] = o[4] / o[2] if o[2] else o[3]
    return out


def top_by(rows, sign, k):
    xs = [x for x in rows if (x[4] > 0 if sign > 0 else x[4] < 0) and abs(x[4]) >= 0.0005]
    return sorted(xs, key=lambda x: -abs(x[4]))[:k]


def summary(t, funds, bench, earn, nxt, today):
    """데이터 요약 문장. 기여도가 없으면 빈 목록.
    earn: 표시 심볼 → 실적 목록(e), nxt: 표시 심볼 → 다음 실적일(nx), today: 'YYYY-MM-DD'."""
    F = funds.get(t)
    roll = "21D" if "21D" in (F or {}).get("P", {}) else "1M"   # 최근 1개월: 21거래일 이동 창(없는 옛 계산이면 1M)
    if not F or roll not in F.get("P", {}):
        return []
    A = dict(F["P"][roll])
    B = funds.get(bench, {}).get("P", {}).get(roll) if bench else None
    A["rows"] = fold_rows(A["rows"])
    if B:
        B = dict(B)
        B["rows"] = fold_rows(B["rows"])
    out = []
    # 1) 수익률과 기준과의 차이
    if B:
        d = A["R"] - B["R"]
        head = f"{t}{josa(t, ('은', '는'))} 최근 1개월 {pc(A['R'])}%로, 같은 기간 {pc(B['R'])}%인 {bench}"
        out.append(head + (f"{josa(bench, ('과', '와'))} 비슷했습니다." if abs(d) < 0.0005
                           else f"보다 {abs(d) * 100:.2f}%p {'앞섰습니다' if d > 0 else '뒤졌습니다'}."))
    else:
        out.append(f"{t}{josa(t, ('은', '는'))} 최근 1개월 {pc(A['R'])}% 움직였습니다.")
    # 2) 차이를 만든 종목: 기준 쪽은 같은 방향 기여의 70% 를 덮을 때까지(최대 4개)의 합, ETF 쪽은 가장 큰 둘
    sign = 1 if A["R"] >= 0 else -1
    mine = top_by(A["rows"], sign, 2)
    verb_mine = "보탰습니다" if sign > 0 else "깎았습니다"
    mine_txt = ""
    if mine:
        names = [f"{x[0]}({pc(x[4])}%p)" for x in mine]
        mine_txt = f"{t} 쪽에서는 {names_and(names)}{josa(names[-1], ('이', '가'))} 가장 많이 {verb_mine}"
    bench_txt = ""
    if B:
        bs = 1 if B["R"] >= 0 else -1
        same = sum(x[4] for x in B["rows"] if (x[4] > 0 if bs > 0 else x[4] < 0))
        picked, acc = [], 0.0
        for x in top_by(B["rows"], bs, 4):
            picked.append(x[0])
            acc += x[4]
            if same and acc / same >= 0.7:
                break
        if picked and abs(acc) >= 0.001:
            nm = "·".join(picked)
            bench_txt = f"{bench} 쪽에서는 {nm}{josa(nm, ('이', '가'))} 합계 {abs(acc) * 100:.2f}%p를 {'보탰' if bs > 0 else '깎았'}"
    if bench_txt and mine_txt:
        out.append(f"{bench_txt}고, {mine_txt}.")
    elif bench_txt:
        out.append(f"{bench_txt}습니다.")
    elif mine_txt:
        out.append(f"{mine_txt}.")
    # 3) 상위 10 밖이 결과를 좌우한 경우
    rest_w, rest_c = A["rest"][0], A["rest"][1]
    if rest_w >= 0.02 and abs(rest_c) >= 0.003 and abs(rest_c) > max([abs(x[4]) for x in A["rows"]] or [0]):
        out.append(f"상위 10 밖 종목(비중 {round(rest_w * 100)}%)이 합계 {pc(rest_c)}%p로 가장 크게 움직였습니다.")
    # 4) 이번 주(직전 주 마지막 종가부터)를 이끈 종목
    W = F["P"].get("1W")
    if W:
        W = dict(W)
        W["rows"] = fold_rows(W["rows"])
    if W and abs(W["R"]) >= 0.001:
        ws = 1 if W["R"] >= 0 else -1
        lead = top_by(W["rows"], ws, 2)
        if lead:
            names = [f"{x[0]}({pc(x[4])}%p)" for x in lead]
            out.append(f"이번 주({md(W['from'])} 종가부터) {'상승' if ws > 0 else '하락'}({pc(W['R'])}%)은 {names_and(names)}{josa(names[-1], ('이', '가'))} 이끌었습니다.")
    # 5) 기간 중 실적 발표와 그 반응(반응이 큰 순서로 셋까지)
    rec = []
    for x in A["rows"]:
        for e in earn.get(x[0].upper(), []):
            if e[3] and A["from"] < e[3] <= A["to"] and e[2] is not None:
                rec.append((abs(e[2]), x[0], e))
    rec.sort(key=lambda r: -r[0])
    if rec:
        items = [f"{s}({md(e[0])}, {'다음 거래일' if e[1] == 'amc' else '그날'} {pc(e[2], 1)}%)" for _a, s, e in rec[:3]]
        out.append(f"기간 중 실적을 발표한 종목은 {names_and(items)}입니다.")
    # 6) 곧 발표할 종목
    horizon = (dt.date.fromisoformat(today) + dt.timedelta(days=SOON_DAYS)).isoformat()
    soon = sorted((nxt[x[0].upper()][0], x[0]) for x in A["rows"] if x[0].upper() in nxt and today <= nxt[x[0].upper()][0] <= horizon)
    if len(soon) == 1:
        out.append(f"{soon[0][1]}{josa(soon[0][1], ('은', '는'))} {md(soon[0][0])}에 실적을 발표합니다.")
    elif soon:
        items = [f"{s}({md(d)})" for d, s in soon[:4]]
        more = f" 외 {len(soon) - 4}종목" if len(soon) > 4 else ""
        out.append(f"{'·'.join(items)}{more}{josa(items[-1] if not more else '목', ('이', '가'))} {SOON_DAYS}일 안에 실적을 발표합니다.")
    return out

# ---------- 헤드라인: 주별 묶음 ----------
STOP = set("""a an the and or but of to in on for at by with from as is are was were be been being it its this that these those
after before over under into out up down about amid than then so if not no new more most less very just still also why how what
when where who which will would could should can may might must says said say report reports reported stock stocks share shares
inc corp co ltd company companies plc llc today week weekly month year years day days now here buy sell hold investor investors
market markets wall street price prices nyse nasdaq your you our we they their his her he she them there get got here's what's
usd target targets percent pct analyst analysts rating ratings update news""".split())
SUFFIX = re.compile(r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|llc|l\.?p|n\.?v|s\.?a|ag|se|holdings?|group|"
                    r"class [abc]|common stock|ordinary shares|ads|adr|gdr|the)\b\.?", re.I)
GENERIC = {"american", "united", "general", "first", "national", "international", "global", "new", "southern", "northern",
           "western", "eastern", "royal", "bank", "energy", "health", "advanced", "applied", "texas", "pacific", "atlantic",
           "digital", "philip", "eli", "public", "service", "capital", "financial", "street", "state"}
ALIASES = {"GOOGL": ["Google"], "GOOG": ["Google"], "META": ["Facebook"], "PG": ["P&G"], "KO": ["Coke"], "TSM": ["TSMC"],
           "XOM": ["ExxonMobil"], "LLY": ["Lilly"], "CL": ["Colgate"], "PEP": ["Pepsi"], "BRK.B": ["Berkshire"]}
KW = re.compile(r"\b(earnings|results|revenue|sales|guidance|outlook|forecast|profit|loss|dividend|buyback|repurchase|upgrade|"
                r"downgrade|price target|target|initiat\w*|coverage|rating|acquir\w*|acquisition|merger|deal|contract|agreement|"
                r"lawsuit|sues|probe|investigation|sec|fda|approval|recall|ceo|cfo|resign\w*|appoint\w*|layoffs?|job cuts|"
                r"strike|tariffs?|beats?|miss(es)?|record|plunge\w*|soar\w*|surge\w*|tumble\w*|slump\w*|ipo|split)\b", re.I)
TIER = [(re.compile(r"globenewswire|business ?wire|pr ?newswire|accesswire|press release", re.I), 2.0),
        (re.compile(r"reuters|bloomberg|cnbc|wsj|wall street journal|financial times|barron|marketwatch|associated press|\bap\b|"
                    r"the information|axios|nikkei", re.I), 1.5),
        (re.compile(r"yahoo|seeking ?alpha|benzinga|motley|fool|zacks|investorplace|tipranks|nasdaq|thestreet|investopedia|"
                    r"24/7|247wallst|forbes|fortune|business insider|insider", re.I), 0.5)]


def tier_of(src):
    return next((v for rx, v in TIER if rx.search(src or "")), 0.0)


def short_names(name, ticker):
    """제목에서 회사를 알아볼 이름들. 'Costco Wholesale Corporation' → ['Costco Wholesale', 'Costco']."""
    words = [w for w in SUFFIX.sub(" ", re.sub(r"[,()]", " ", name or "")).split() if w]
    out = []
    if words:
        if len(words) >= 3 and words[1] == "&":
            out.append(" ".join(words[:3]))
        else:
            out.append(" ".join([w for w in words if w != "&"][:2]))
        if len(words[0]) >= 4 and words[0].lower() not in GENERIC:
            out.append(words[0])
    out += ALIASES.get(ticker, [])
    return [x for i, x in enumerate(out) if x and x not in out[:i]]


def named_rx(name, ticker):
    """제목에 회사 이름이나 티커가 있는지 보는 정규식. 두 글자 이하 티커는 (KO)·$KO·NYSE:KO 처럼 적힌 것만 센다."""
    parts = [r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])" for n in short_names(name, ticker)]
    t = re.escape(ticker.replace(".", "-")) + "|" + re.escape(ticker)
    parts.append(r"(?:\((?:" + t + r")\)|\$(?:" + t + r")\b|(?:NYSE|NASDAQ|AMEX):\s?(?:" + t + r")\b)")
    rx_ci = re.compile("|".join(parts[:-1]), re.I) if len(parts) > 1 else None
    rx_tk = re.compile(parts[-1] + (r"|(?<![A-Za-z$])(?:" + t + r")(?![A-Za-z])" if len(ticker) >= 3 else ""))
    return lambda title: bool((rx_ci and rx_ci.search(title)) or rx_tk.search(title))


def tokens(title, drop):
    t = (title or "").lower().replace("’", "'")
    out = set()
    for w in re.findall(r"\$?\d[\d,.]*%?|[a-z][a-z'&-]*", t):
        w = w.rstrip(".,'").replace("'s", "")
        if not w or w in STOP or w in drop or (not w[0].isdigit() and w[0] != "$" and len(w) < 3):
            continue
        if w[0].isalpha() and len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]   # dividends → dividend, sales → sale (가벼운 복수형 맞추기)
        out.add(w)
    return out


def ny_day(ts):
    return dt.datetime.fromtimestamp(ts, NY).date()


def week_blocks(items, name, ticker, start, cut=None):
    """[[unix 초, 제목, 출처, 주소], …] → s.w (모듈 설명 참고). start: 창의 첫 월요일(뉴욕), cut: 창 앞이 빈 종목의 가장 오래된 unix 초."""
    is_named = named_rx(name, ticker)
    drop = {w.lower() for n in short_names(name, ticker) + [name or ""] for w in re.findall(r"[A-Za-z][A-Za-z'&-]*", n)}
    drop |= {ticker.lower(), ticker.lower().replace(".", "-")}
    arts = []
    for x in items:
        d = ny_day(x[0])
        if d < start:
            continue
        arts.append({"ts": x[0], "t": x[1], "src": x[2], "u": x[3], "day": d, "tok": tokens(x[1], drop), "named": is_named(x[1])})
    arts.sort(key=lambda a: a["ts"])
    clusters = []
    for a in arts:
        home = None
        for c in clusters:
            if a["ts"] - c["last"] > SIM_DAYS * 86400:
                continue
            for b in c["m"]:
                common = len(a["tok"] & b["tok"])
                small = min(len(a["tok"]), len(b["tok"])) or 1
                if common >= SIM_STRONG or (common >= SIM_MIN and common / small >= SIM_RATIO):
                    home = c
                    break
            if home:
                break
        if home:
            home["m"].append(a)
            home["last"] = a["ts"]
        else:
            clusters.append({"m": [a], "last": a["ts"]})
    weeks = [start + dt.timedelta(days=7 * i) for i in range(WEEKS)][::-1]   # 이번 주부터
    picked = {w: [] for w in weeks}
    for c in clusters:
        m = c["m"]
        rep = max(m, key=lambda a: (tier_of(a["src"]), a["named"], -a["ts"]))
        size = len(m)
        score = (3 * math.log2(1 + size) + (2 if KW.search(rep["t"]) else 0) + max(tier_of(a["src"]) for a in m)
                 + (1.5 if any(a["named"] for a in m) else -4))
        wk = m[0]["day"] - dt.timedelta(days=m[0]["day"].weekday())
        if wk not in picked:
            continue
        last_wk = m[-1]["day"] - dt.timedelta(days=m[-1]["day"].weekday())
        u = rep["u"][len(FH_URL):] if rep["u"].startswith(FH_URL) else rep["u"]
        named = any(a["named"] for a in m)
        picked[wk].append((named, score, rep["ts"], [rep["ts"], rep["t"][:TITLE_MAX], rep["src"], ("fh:" + u) if rep["u"].startswith(FH_URL) else u,
                                              size - 1, m[-1]["ts"] if last_wk != wk else 0]))
    out = []
    for w in weeks:
        n = sum(1 for a in arts if w <= a["day"] < w + dt.timedelta(days=7))
        top = [x[3] for x in sorted(picked[w], key=lambda x: (not x[0], -x[1], -x[2]))[:WEEK_KEEP]]
        part = 1 if cut and ny_day(cut) > w else 0
        out.append([w.isoformat(), n, top, part])
    return out if any(b[1] for b in out) else []


# ---------- 공시 ----------
ITEM_KO = {"1.01": "중요 계약", "1.02": "계약 해지", "1.03": "파산", "1.05": "사이버 보안 사고", "2.01": "인수·매각 완료",
           "2.02": "실적", "2.03": "채무 발생", "2.04": "채무 조기 상환 사유", "2.05": "구조조정 비용", "2.06": "자산 손상",
           "3.01": "상장 유지 기준·이전", "3.02": "미등록 증권 발행", "3.03": "주주 권리 변경", "4.01": "회계법인 교체",
           "4.02": "재무제표 신뢰 철회", "5.01": "지배권 변경", "5.02": "임원 변동·보상", "5.03": "정관 변경",
           "5.05": "윤리 강령", "5.07": "주주총회 결과", "5.08": "주주 추천 이사", "7.01": "공정 공시", "8.01": "기타 중요 사항"}
FORM_KO = {"10-K": "연간 보고서", "10-K/A": "연간 보고서 정정", "10-Q": "분기 보고서", "10-Q/A": "분기 보고서 정정",
           "6-K": "해외 발행사 수시 보고", "20-F": "해외 발행사 연간 보고서", "40-F": "캐나다 발행사 연간 보고서"}
LABEL_RULES = [(re.compile(p, re.I), ko) for p, ko in [
    (r"\bdividends?\b", "배당"), (r"\b(repurchase|buyback|buy-back)", "자사주 매입"), (r"\b(annual|special) meeting\b", "주주총회"),
    (r"\b(merger|acquisition|acquire[sd]?|to be acquired)\b", "인수·합병"),
    (r"\b(senior notes|notes due|debentures|indenture|offering of)\b", "채권 발행"),
    (r"\b(credit agreement|credit facility|term loan|revolving credit)\b", "신용 계약"),
    (r"\b(bonus(es)?|compensation|incentive|equity awards?|severance)\b", "임원 보상"),
    (r"\b(resign\w*|retire\w*|step(ping)? down|depart\w*)\b", "임원 사임·퇴임"),
    (r"\b(appoint\w*|elected|will join|hired)\b", "임원 선임"),
    (r"\b(lawsuit|litigation|complaint|settlement)\b", "소송")]]


def filing_rows(rec, cache):
    """filings.json 의 한 종목 → s.f (모듈 설명 참고)."""
    out = []
    for f in rec.get("f", []):
        fdate, form, items, acc, doc, at, rdate = (list(f) + [""] * 7)[:7]
        its = [x.strip() for x in (items or "").split(",") if x.strip() and x.strip() != "9.01"]
        url = SEC_ARCHIVE.format(cik=rec.get("cik"), acc=acc.replace("-", ""), doc=doc) if doc else \
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={rec.get('cik')}&type={form}"
        sent = ""
        if form in ("8-K", "8-K/A"):
            item = its[0] if its else ""
            sent = ((cache.get(acc) or ["", ""])[1] or "")
            kw = next((ko for rx, ko in LABEL_RULES if rx.search(sent)), None) if item != "2.02" else None
            disp = f"{item} {kw or ITEM_KO.get(item, '기타')}" if item else (kw or "수시 보고")
            if form == "8-K/A":
                disp += " · 정정"
        else:
            disp = FORM_KO.get(form, form)
            if form.startswith(("10-K", "20-F", "40-F")) and rdate:
                disp += f" · 회계연도 {md(rdate)} 마감"
            elif form.startswith("10-Q") and rdate:
                disp += f" · {md(rdate)} 분기 말"
        if len(sent) > FILING_TEXT_MAX:
            sent = sent[:FILING_TEXT_MAX].rsplit(" ", 1)[0] + "…"
        out.append([fdate, form, disp, url, sent, (at or fdate)[:10]])
    return out



def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--news", default=os.path.join(ROOT, "output", "news"))
    ap.add_argument("--axis", default=os.path.join(ROOT, "output", "prices_wide.csv"))
    ap.add_argument("--stocks", default=os.path.join(ROOT, "output", "stocks"))
    ap.add_argument("--contrib", default=os.path.join(ROOT, "output", "contrib-data.json"))
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "news-data.json"))
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    axis, _etfp = read_wide(args.axis)
    px = {}
    for name in ("prices_wide.csv", "extra_prices_wide.csv"):
        p = os.path.join(args.stocks, name)
        if os.path.exists(p):
            _ax, d = read_wide(p)
            for k, v in d.items():
                px.setdefault(k, v)
    E = load(os.path.join(args.news, "earnings.json"))
    C = load(os.path.join(args.news, "calendar.json"))
    H = load(os.path.join(args.news, "headlines.json"))
    FL = load(os.path.join(args.news, "filings.json"))
    TX = (load(os.path.join(args.news, "filing_text.json")).get("acc") or {})
    cal_ok = age_days((C.get("meta") or {}).get("generated_at"), now) <= CAL_STALE_DAYS
    news_ok = age_days((H.get("meta") or {}).get("generated_at"), now) <= NEWS_STALE_DAYS
    today = now.date().isoformat()
    ny_today = now.astimezone(NY).date()
    start = ny_today - dt.timedelta(days=ny_today.weekday() + 7 * (WEEKS - 1))   # 주별 묶음의 첫 월요일(뉴욕)
    week_ago = int(now.timestamp()) - 7 * 86400
    cuts = (H.get("meta") or {}).get("cut") or {}
    fil = FL.get("stocks") or {}

    stocks = {}
    for key, rec in (E.get("stocks") or {}).items():
        code = key
        series = px.get(key) or px.get(code) or px.get(f"{code}.US")
        ev = []
        for at, url in (rec.get("e") or [])[:EARN_KEEP]:
            r, rday = reaction(series, axis, at)
            ev.append([at[:10], timing(at[11:16]), r, rday])
        s = {"tv": rec.get("tv")}
        if ev:
            s["e"] = ev
            s["u"] = rec["e"][0][1]
        nx = (C.get("stocks") or {}).get(key) if cal_ok else None
        if nx and nx[0] >= today:
            s["nx"] = nx
        h = (H.get("stocks") or {}).get(key) if news_ok else None
        if h:
            recent = [x for x in h if x[0] >= week_ago]
            if recent:
                s["h"] = recent[:NEWS_KEEP]
            w = week_blocks(h, rec.get("name") or key, key, start, cuts.get(key))
            if w:
                s["w"] = w
        fr = fil.get(key)
        if fr:
            f = filing_rows(fr, TX)
            if f:
                s["f"] = f
            if fr.get("ins"):
                s["ins"] = fr["ins"]
                s["cik"] = fr.get("cik")
        stocks[key] = s

    # ETF 별 상위 10 (스냅샷 순서)
    files = sorted(f for f in os.listdir(args.holdings) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.csv", f))
    rows = list(csv.DictReader(open(os.path.join(args.holdings, files[-1]), encoding="utf-8-sig")))
    meta = json.load(open(args.meta, encoding="utf-8"))
    terminal = [t for t, m in meta.items() if "terminal" in m.get("screens", [])]
    top = {t: [] for t in terminal}
    for r in sorted(rows, key=lambda r: (r["etf"], int(r.get("rank") or 99))):
        if r["etf"] in top and int(r.get("rank") or 99) <= 10:
            _ex, code = parse_symbol(r.get("symbol"))
            if code and not NON_STOCK.search(code):
                top[r["etf"]].append(code.upper())

    contrib = load(args.contrib)
    funds = contrib.get("funds", {}) if contrib.get("asof") == axis[-1] else {}
    if contrib and not funds:
        print(f"contribution data is for {contrib.get('asof')}, not {axis[-1]}: summaries skipped")
    # 기여도 행의 표시 심볼(GOOGL·005930)은 종목 키와 같다(대문자)
    earn = {k: v.get("e", []) for k, v in stocks.items()}
    nxt = {k: v["nx"] for k, v in stocks.items() if v.get("nx")}
    etf = {}
    for t, keys in top.items():
        keys = [k for k in keys if SHARE_FOLD.get(k) not in keys]   # 같은 회사의 두 번째 주식은 첫 번째 주식에 합쳐 보인다
        nxs = sorted([[k, stocks[k]["nx"][0], stocks[k]["nx"][1]] for k in keys if k in stocks and stocks[k].get("nx")], key=lambda x: x[1])
        cut = (now.date() - dt.timedelta(days=RECENT_DAYS)).isoformat()
        rec = [[k, e[0]] for k in keys if k in stocks for e in stocks[k].get("e", []) if e[0] >= cut]
        out = {"nx": nxs, "re": sorted(rec, key=lambda x: x[1], reverse=True),
               "nh": sum(len(stocks.get(k, {}).get("h", [])) for k in keys)}
        if not rec:
            last = [[k, stocks[k]["e"][0][0]] for k in keys if k in stocks and stocks[k].get("e")]
            if last:
                latest = max(x[1] for x in last)
                out["last"] = [x for x in last if x[1] == latest]
        bench = PARENT.get(t) or "SPY"
        out["sum"] = summary(t, funds, bench, earn, nxt, today)
        etf[t] = out

    em, hm, cm, fm = E.get("meta") or {}, H.get("meta") or {}, C.get("meta") or {}, FL.get("meta") or {}
    data = {"meta": {"asof": axis[-1], "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "news_at": hm.get("generated_at") if news_ok else None, "sec_at": em.get("sec_generated_at"),
                     "cal_at": cm.get("generated_at") if cal_ok else None, "fil_at": fm.get("generated_at"),
                     "from": start.isoformat(), "weeks": WEEKS, "fil_days": fm.get("days"),
                     "src": {"sec": em.get("status", "missing"), "news": hm.get("status", "missing") if news_ok else "missing",
                             "calendar": cm.get("status", "missing") if cal_ok else "missing",
                             "filings": fm.get("status", "missing")}},
            "s": stocks, "etf": etf}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    n_e = sum(1 for s in stocks.values() if s.get("e"))
    n_h = sum(len(s.get("h", [])) for s in stocks.values())
    n_w = sum(1 for s in stocks.values() if s.get("w"))
    n_f = sum(len(s.get("f", [])) for s in stocks.values())
    n_sum = sum(1 for v in etf.values() if v["sum"])
    print(f"news-data: {len(stocks)} names ({n_e} with 8-K earnings, {n_h} headlines in 7 days, {n_w} with {WEEKS}-week blocks, "
          f"{n_f} filings), {len(etf)} ETFs ({n_sum} summaries) -> {os.path.relpath(args.out, ROOT)} ({os.path.getsize(args.out) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
