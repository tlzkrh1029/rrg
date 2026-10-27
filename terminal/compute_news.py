#!/usr/bin/env python3
"""뉴스·실적 자료를 페이지용으로 계산한다 (terminal/fetch_news.py 의 output/news/ → output/news-data.json).

입력
  output/news/earnings.json, calendar.json, headlines.json   fetch_news.py 결과(없으면 그 부분 없이 만든다)
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
         h   헤드라인 최대 NEWS_KEEP 개 [unix 초, 제목, 출처, 주소] (Finnhub, 최근 7일)
  etf[ETF] sum  데이터 요약 문장 (기여도 1개월·1주와 실적으로 만든다. 기준은 산업·테마가 상위 섹터, 나머지는 SPY)
           nx   상위 10 종목의 다가오는 실적 [[표시 심볼, 날짜, 시점]] (가까운 순)
           re   최근 30일 실적 발표 [[표시 심볼, 날짜]]; 없으면 last 에 가장 최근 발표
           nh   상위 10 종목의 헤드라인 수
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

EARN_KEEP = 4
NEWS_KEEP = 6
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


def top_by(rows, sign, k):
    xs = [x for x in rows if (x[4] > 0 if sign > 0 else x[4] < 0) and abs(x[4]) >= 0.0005]
    return sorted(xs, key=lambda x: -abs(x[4]))[:k]


def summary(t, funds, bench, earn, nxt, today):
    """데이터 요약 문장. 기여도가 없으면 빈 목록.
    earn: 표시 심볼 → 실적 목록(e), nxt: 표시 심볼 → 다음 실적일(nx), today: 'YYYY-MM-DD'."""
    F = funds.get(t)
    if not F or "1M" not in F.get("P", {}):
        return []
    A = F["P"]["1M"]
    B = funds.get(bench, {}).get("P", {}).get("1M") if bench else None
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
    # 4) 최근 1주를 이끈 종목
    W = F["P"].get("1W")
    if W and abs(W["R"]) >= 0.001:
        ws = 1 if W["R"] >= 0 else -1
        lead = top_by(W["rows"], ws, 2)
        if lead:
            names = [f"{x[0]}({pc(x[4])}%p)" for x in lead]
            out.append(f"최근 1주 {'상승' if ws > 0 else '하락'}({pc(W['R'])}%)은 {names_and(names)}{josa(names[-1], ('이', '가'))} 이끌었습니다.")
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
    cal_ok = age_days((C.get("meta") or {}).get("generated_at"), now) <= CAL_STALE_DAYS
    news_ok = age_days((H.get("meta") or {}).get("generated_at"), now) <= NEWS_STALE_DAYS
    today = now.date().isoformat()

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
            s["h"] = h[:NEWS_KEEP]
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

    em, hm, cm = E.get("meta") or {}, H.get("meta") or {}, C.get("meta") or {}
    data = {"meta": {"asof": axis[-1], "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "news_at": hm.get("generated_at") if news_ok else None, "sec_at": em.get("sec_generated_at"),
                     "cal_at": cm.get("generated_at") if cal_ok else None,
                     "src": {"sec": em.get("status", "missing"), "news": hm.get("status", "missing") if news_ok else "missing",
                             "calendar": cm.get("status", "missing") if cal_ok else "missing"}},
            "s": stocks, "etf": etf}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    n_e = sum(1 for s in stocks.values() if s.get("e"))
    n_h = sum(len(s.get("h", [])) for s in stocks.values())
    n_sum = sum(1 for v in etf.values() if v["sum"])
    print(f"news-data: {len(stocks)} names ({n_e} with 8-K earnings, {n_h} headlines), {len(etf)} ETFs ({n_sum} summaries) -> "
          f"{os.path.relpath(args.out, ROOT)} ({os.path.getsize(args.out) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
