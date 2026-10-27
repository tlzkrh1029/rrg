#!/usr/bin/env python3
"""터미널 페이지를 생성한다.

입력
  output/terminal-data.json        compute_terminal.py 결과
  output/holdings/<date>.csv       구성종목 스냅샷(최신 두 개를 써서 변동 %p 를 계산)
  data/etf_meta.json               ETF 이름·순자산·보유 종목 수·출처 URL
  --lists DIR                      tradfi-ticker-lists 의 output 폴더(상장 점). 없으면 점을 비운다.
  data/lists/log.json              5) 상장 로그 탭의 누적 로그(terminal/listing_log.py 가 쌓음). 없으면 탭에 안내만 적는다.
  output/breakout-data.json        돌파 지도 계산 결과(종목 상세용 가격·이동평균 재료). 없으면 docs/breakout/data/ 의 직전 사본, 그것도 없으면 종목 상세 없이 만든다.
  output/contrib-data.json         구성종목 기여도(terminal/compute_contrib.py). 종가 기준일이 이번 계산과 다르거나 없으면 기여도 블록과 주도 종목 없이 만든다.
  output/etf-chart-data.json       Holdings 패널의 ETF 차트(fetch_etf_history.py). 종가 기준일이 이번 계산과 다르거나 없으면 차트 없이 만든다.
  output/news-data.json            뉴스·실적(terminal/compute_news.py). 종가 기준일이 이번 계산과 다르거나 없으면 뉴스 칼럼과 실적 표시 없이 만든다.
  terminal/template.html           토큰이 든 HTML 템플릿

출력 (docs/)
  index.html                       GitHub Pages 용 완전한 문서
  terminal.artifact.html           Claude 아티팩트 발행용(문서 골격 없이 <title>부터)
  data/terminal-data.json          이번 계산값 사본(다음 실행에서 변화 비교에 쓴다)
  data/alerts.json                 직전 빌드와 비교한 변화 목록
  data/brief.txt                   텔레그램용 주간 브리핑 본문
  data/alerts.txt                  텔레그램용 조건 알림 본문(변화가 없으면 빈 파일)
"""
import argparse
import csv
import datetime as dt
import glob
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAGE_URL = "https://tlzkrh1029.github.io/rrg/#board"   # #board 는 대문을 건너뛰고 바로 보드를 연다
# 5) 돌파 지도 탭이 가리키는 곳. Pages 본문은 같은 사이트의 breakout/ 를, 아티팩트 본문은 돌파 지도 아티팩트를 새 탭으로 연다.
BREAKOUT_PAGE = "breakout/"
BREAKOUT_ARTIFACT = "https://claude.ai/artifact/AMAB2TMoSxwXPuLPWjtjGB"
# TradingView 뉴스 위젯(종목을 열면 불러옴)을 Pages 본문에 넣을지. 아티팩트는 외부 스크립트를 쓸 수 없어 늘 끈다.
# 2026-10-28 Pages 에서 확인해 보니 위젯이 2024년 기사 같은 오래된 기사를 띄우고 기사 열기도 될 때와 안 될 때가 있어서 껐다.
# 다시 켜려면 이 값만 True 로 바꾸면 된다(헤드라인 목록과 TradingView 링크는 어느 쪽이든 그대로 남는다).
TV_WIDGET_PAGES = False

KO = {"XLK": "기술", "XLF": "금융", "XLV": "헬스케어", "XLY": "경기소비재", "XLC": "커뮤니케이션", "XLI": "산업재",
      "XLP": "필수소비재", "XLE": "에너지", "XLU": "유틸리티", "XLB": "소재", "XLRE": "부동산",
      "SMH": "반도체", "IGV": "소프트웨어", "XBI": "바이오", "GNOM": "유전체학", "ARKG": "유전체학 (액티브)", "KRE": "지역은행",
      "XHB": "주택건설", "XME": "금속·광업", "URA": "우라늄", "ITA": "항공우주·방산", "PAVE": "미국 인프라", "DRAM": "메모리반도체",
      "MAGS": "매그니피센트 7"}
SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLB", "XLRE"]
# 스타일·집중도 ETF: SPY 대비 국면과 신호는 알리되, 전환순 상위 3 같은 순위 집계에는 넣지 않는다
STYLE = ["MAGS"]
SWAP_RE = re.compile(r"TRS|^B\.0")
# DRAM 의 총수익스왑 행: 기초 종목의 상장 여부를 그대로 쓴다
SWAP_UNDERLYING = {"595112103.TRS 050427 NM": "MU", "595112103.TRS 052427 GS": "MU", "6771720.TRS 052427 GS": "KRX:005930",
                   "6450267.TRS 052427 GS": "KRX:000660", "BTMTQT8.TRS 052427 GS": "SSE:688825"}
EXCH_MAP = {"KRX": "KRX", "HKG": "HKEX", "TSX": "TSX", "LON": "LSE", "ASX": "ASX", "SSE": "SSE", "TSE": "TSE"}
CODE_ALIAS = {"005930": "KRX:005930", "000660": "KRX:000660", "1548": "HKEX:1548"}

SKELETON_HEAD = ('<!doctype html><html><head><meta charset=utf8><meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">'
                 '<style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
                 'html{scroll-padding-top:env(safe-area-inset-top,0px)}body{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,sans-serif;background:#faf9f5;color:#141413}'
                 'img{max-width:100%}[hidden]:not([hidden=until-found i]){display:none!important}</style></head><body>\n')
SKELETON_TAIL = "\n</body></html>\n"


def norm_sym(s):
    return re.sub(r"^[A-Z]+:\s*", "", s.strip())


def exch_of(s):
    m = re.match(r"^([A-Z]+):\s*", s.strip())
    return m.group(1) if m else ""


def long_date(iso):
    d = dt.date.fromisoformat(iso)
    return d.strftime("%b %-d, %Y") if os.name != "nt" else d.strftime("%b %d, %Y")


def html_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def built_at_kst():
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
    return now.strftime("%Y-%m-%d %H:%M KST")


def phase(r, m):
    return ("Leading" if m >= 100 else "Weakening") if r >= 100 else ("Improving" if m >= 100 else "Lagging")


# ---------- 구성종목 ----------
def load_snapshot(path):
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        out.setdefault(r["etf"], []).append(r)
    return out


def build_hold(snapshots, meta):
    """snapshots: [(date, rows_by_etf)] 날짜 오름차순. 마지막이 최신."""
    cur_date, cur = snapshots[-1]
    prev_date, prev = snapshots[-2] if len(snapshots) > 1 else (None, {})
    hold = {}
    for t, m in meta.items():
        if "terminal" not in m.get("screens", ["terminal"]):   # 돌파 지도 전용 ETF(GDX 등)는 이 화면에 없다
            continue
        rows = cur.get(t, [])
        # 이전 스냅샷에 이 ETF 가 통째로 없으면(새로 추가한 ETF) 변동 %p 와 진입·이탈을 계산하지 않는다.
        # 출처가 바뀐 경우(스왑·국채 행이 따로 있는 stockanalysis 표 ↔ 스왑 노출을 합산한 Roundhill 표)도 비중의 뜻이 달라 비교하지 않는다.
        raw_prev = any(SWAP_RE.search(norm_sym(r["symbol"])) for r in prev.get(t, []))
        raw_cur = any(SWAP_RE.search(norm_sym(r["symbol"])) for r in rows)
        source_changed = prev_date is not None and t in prev and bool(rows) and raw_prev != raw_cur
        has_prev = prev_date is not None and t in prev and not source_changed
        oldw = {norm_sym(r["symbol"]): float(r["weight_pct"]) for r in prev.get(t, [])} if has_prev else {}
        out = []
        for r in rows:
            sym = norm_sym(r["symbol"])
            w = float(r["weight_pct"])
            ch = None if (not has_prev or sym not in oldw) else round(w - oldw[sym], 2)
            out.append([sym, r["name"], f"{w:.2f}%", ch, exch_of(r["symbol"])])
        newset = {norm_sym(r["symbol"]) for r in rows}
        hold[t] = {
            "name": m["name"], "aum": m["aum"], "aumAsof": m["aum_asof"][5:], "n": m["n"],
            "top10": f"{sum(float(r['weight_pct']) for r in rows):.2f}%",
            "asof": long_date(cur_date), "prev": long_date(prev_date) if prev_date else "없음", "url": m["url"], "rows": out,
            "prevMissing": bool(prev_date) and not has_prev,
            "sourceChanged": source_changed,
            "entered": [norm_sym(r["symbol"]) for r in rows if has_prev and norm_sym(r["symbol"]) not in oldw],
            "left": [s for s in oldw if s not in newset],
        }
    return hold, cur_date, prev_date


# ---------- 상장 점 ----------
def norm_listing(s):
    s = (s or "").strip()
    if not s:
        return None
    m = re.match(r"^([A-Za-z ]+):\s*(.+)$", s)
    if not m:
        return s.upper()
    ex, code = m.group(1).strip().upper(), m.group(2).strip().upper()
    if ex in ("US", "NASDAQ", "NYSE", "NYSE ARCA", "CBOE BZX", "NYSE AMERICAN", "CBOE"):
        return code
    return f"{ {'SSE STAR': 'SSE'}.get(ex, ex) }:{code}".replace(" ", "")


def load_lists(lists_dir):
    B, F, H = set(), set(), set()
    counts = {"b": 0, "f": 0, "h": 0}
    ts = None
    try:
        for r in csv.DictReader(open(os.path.join(lists_dir, "binance_spot_bstocks.csv"), encoding="utf-8-sig")):
            counts["b"] += 1
            if r.get("status") == "TRADING":
                B.add(r["underlying"].upper())
        for r in csv.DictReader(open(os.path.join(lists_dir, "binance_futures_tradfi.csv"), encoding="utf-8-sig")):
            counts["f"] += 1
            if r.get("status") in ("TRADING", "PENDING_TRADING"):
                k = norm_listing(r.get("listing"))
                if k:
                    F.add(k)
        for r in csv.DictReader(open(os.path.join(lists_dir, "hyperliquid_hip3_stocks.csv"), encoding="utf-8-sig")):
            counts["h"] += 1
            if r.get("delisted") != "True":
                k = norm_listing(r.get("reference_listing"))
                if k:
                    H.add(k)
        sp = os.path.join(lists_dir, "summary.json")
        if os.path.exists(sp):
            ts = json.load(open(sp, encoding="utf-8")).get("generated_at")
    except FileNotFoundError as e:
        print("listing lists not found:", e)
        return None, None, None, counts, ts
    return B, F, H, counts, ts


def flags(key, B, F, H):
    if B is None:
        return ""
    return ("b" if key in B else "") + ("p" if key in F else "") + ("h" if key in H else "")


# ---------- 알림·브리핑 ----------
def shift_score(e):
    p = e["pct"]
    if p[0] is None or p[1] is None:
        return None
    base = p[4] if p[4] is not None else p[3]
    if base is None:
        return None
    return (p[0] + p[1]) / 2 - base


def phases(data):
    out = {}
    for t, e in data["etfs"].items():
        out[t] = {
            "bench": phase(*e["rrgPath"][-1]) if e["rrgPath"] else None,
            "spy": phase(*e["rrgSpy"][-1]) if e["rrgSpy"] else None,
        }
    return out


def pct_strip(p):
    return "/".join("n/a" if v is None else str(v) for v in p)


def make_alerts(prev, cur):
    """prev 가 없으면 빈 목록. 조건: RRG 국면 변화, 새로 켜진 신호, 전환순 상위 3 진입.

    항목마다 etf, type, text(ETF 접두어 포함), detail(접두어 없는 본문), pct(1W~12M 백분위), phase(벤치마크 대비 국면)를 담고,
    1W 백분위가 높은 ETF 부터(같으면 보드 순서) 정렬한다. 텔레그램 본문과 Changes 패널이 이 순서를 그대로 쓴다.
    """
    alerts = []
    if not prev:
        return alerts
    pp, pc = phases(prev), phases(cur)
    order = list(cur["etfs"])

    def add(t, kind, detail):
        e = cur["etfs"][t]
        alerts.append({"etf": t, "type": kind, "text": f"{t} {KO[t]}: {detail}", "detail": detail,
                       "pct": e["pct"], "phase": pc[t]["bench"], "bench": e["parent"] or "SPY"})

    for t in cur["etfs"]:
        e = cur["etfs"][t]
        bench = e["parent"] or "SPY"
        if pp.get(t, {}).get("bench") and pc[t]["bench"] and pp[t]["bench"] != pc[t]["bench"]:
            add(t, "rrg", f"RRG {pp[t]['bench']} → {pc[t]['bench']} (vs {bench})")
        if e["parent"] and pp.get(t, {}).get("spy") and pc[t]["spy"] and pp[t]["spy"] != pc[t]["spy"]:
            add(t, "rrg", f"RRG {pp[t]['spy']} → {pc[t]['spy']} (vs SPY)")
        old_sig = {s[1] for s in prev["etfs"].get(t, {}).get("sig", [])}
        for s in e["sig"]:
            if s[1] not in old_sig:
                add(t, "signal", f"{s[0]} {s[1]}")
    top_prev = [t for t, _ in sorted(((t, shift_score(e)) for t, e in prev["etfs"].items() if t not in STYLE and shift_score(e) is not None), key=lambda x: -x[1])[:3]]
    top_cur = [t for t, _ in sorted(((t, shift_score(e)) for t, e in cur["etfs"].items() if t not in STYLE and shift_score(e) is not None), key=lambda x: -x[1])[:3]]
    for t in top_cur:
        if t not in top_prev:
            add(t, "shift", f"전환순 상위 3 진입 (점수 {shift_score(cur['etfs'][t]):.0f})")
    # 1W 백분위 내림차순, 같으면 보드 순서. 백분위가 없는 ETF 는 맨 뒤.
    alerts.sort(key=lambda a: (-(a["pct"][0] if a["pct"][0] is not None else -1), order.index(a["etf"])))
    return alerts


def alerts_text(cur, alerts):
    """텔레그램 조건 알림 본문: ETF 단위로 묶고, 머리글에 1W~12M 백분위와 RRG 국면을 적는다."""
    lines = [f"🔔 섹터 전환 터미널 · {cur['asof']} 종가 · 변화 {len(alerts)}건",
             "1W 백분위 높은 순 · [1W/1M/3M/6M/12M 백분위] · RRG 는 벤치마크 대비 국면", ""]
    seen = []
    for a in alerts:
        if a["etf"] not in seen:
            seen.append(a["etf"])
            lines.append(f"{a['etf']} {KO[a['etf']]}  [{pct_strip(a['pct'])}]  {a['phase'] or 'N/A'} vs {a['bench']}")
        lines.append(f"  · {a['detail']}")
    lines += ["", PAGE_URL]
    return "\n".join(lines)


def drivers_lines(contrib):
    """주간 브리핑의 '주도 종목' 두 줄. 종목마다 가장 크게 움직인 ETF 하나와 그 기여(%p)를 적는다."""
    d = (((contrib or {}).get("drivers") or {}).get("1W") or {}).get("usd")
    if not d:
        return []
    fmt = lambda v: f"{v * 100:+.2f}".replace("-", "−")

    def part(items, sign):
        out = []
        for b in items[:3]:
            es = [e for e in b["e"] if (e[1] > 0 if sign > 0 else e[1] < 0)]
            if es:
                out.append(f"{b['s']} {es[0][0]} {fmt(es[0][1])}")
        return " · ".join(out) or "없음"
    return ["주도 종목 (1W · 가장 크게 움직인 ETF 기여 %p)", "  ▲ " + part(d["up"], 1), "  ▼ " + part(d["dn"], -1), ""]


def brief_html(brief):
    """주간 브리핑 탭의 본문(<pre>). 텔레그램과 같은 글이고, '주도 종목' 묶음(머리줄과 들여 쓴 ▲·▼ 줄)만 강조용 span 으로 감싼다."""
    lines, out, i = brief.split("\n"), [], 0
    while i < len(lines):
        if lines[i].startswith("주도 종목"):
            j = i + 1
            while j < len(lines) and lines[j].startswith("  "):
                j += 1
            out.append('<span class="drvhl">' + "\n".join(html_escape(x) for x in lines[i:j]) + "</span>")
            i = j
        else:
            out.append(html_escape(lines[i]))
            i += 1
    return "\n".join(out)


def make_brief(cur, hold, alerts, prev_asof, contrib=None):
    E = cur["etfs"]
    ph = phases(cur)
    lines = [f"📊 섹터 전환 터미널 · {cur['asof']} 종가 기준", ""]
    strip = pct_strip
    ranked = sorted(((t, shift_score(e)) for t, e in E.items() if t not in STYLE and shift_score(e) is not None), key=lambda x: -x[1])
    lines.append("전환순 상위 3 (1W·1M 평균 − 12M 백분위)")
    for t, s in ranked[:3]:
        lines.append(f"  {t} {KO[t]}: {s:+.0f}  [{strip(E[t]['pct'])}]")
    lines.append("")
    lines += drivers_lines(contrib)
    by = {}
    for t in SECTORS:
        by.setdefault(ph[t]["bench"], []).append(t)
    lines.append("1단 RRG (섹터 ÷ SPY)")
    for k in ("Leading", "Improving", "Weakening", "Lagging"):
        if by.get(k):
            lines.append(f"  {k}: {' '.join(by[k])}")
    styled = [t for t in STYLE if t in E]
    if styled:
        lines.append("스타일·집중도 (÷ SPY · 섹터 집계 제외)")
        for t in styled:
            last = E[t]["rrgPath"][-1] if E[t]["rrgPath"] else None
            coords = f" (RS-Ratio {last[0]:.1f}, RS-Mom {last[1]:.1f})" if last else ""
            lines.append(f"  {t} {KO[t]}: {ph[t]['bench'] or 'N/A'}{coords}  [{strip(E[t]['pct'])}]")
    lines.append("")
    lines.append("2단 RRG (하위 ETF ÷ 상위 섹터)")
    for s in SECTORS:
        kids = [t for t, e in E.items() if e["parent"] == s]
        if not kids:
            continue
        parts = []
        for t in kids:
            parts.append(f"{t} {ph[t]['bench'] or 'N/A'}")
        lines.append(f"  {s}: " + " · ".join(parts))
    lines.append("")
    fired = [(t, s) for t, e in E.items() for s in e["sig"]]
    lines.append("신호")
    if fired:
        for t, s in fired:
            lines.append(f"  {s[0]} {t}: {s[1]}")
    else:
        lines.append("  없음")
    lines.append("")
    lines.append(f"직전 빌드({prev_asof or '없음'}) 대비 변화 (1W 백분위 높은 순, [1W/1M/3M/6M/12M])")
    if alerts:
        seen = []
        for a in alerts:
            if a["etf"] not in seen:
                seen.append(a["etf"])
                lines.append(f"  {a['etf']} {KO[a['etf']]}  [{strip(a['pct'])}]  {a['phase'] or 'N/A'} vs {a['bench']}")
            lines.append(f"    · {a['detail']}")
    else:
        lines.append("  없음")
    changed = [t for t, h in hold.items() if h["entered"] or h["left"]]
    if changed:
        lines.append("")
        lines.append("구성종목 상위 10 변화: " + ", ".join(f"{t}(+{len(hold[t]['entered'])}/−{len(hold[t]['left'])})" for t in changed))
    lines.append("")
    lines.append(PAGE_URL)
    return "\n".join(lines)


def load_stocks(args, hold):
    """종목 상세용 가격. 돌파 지도 계산 결과에서 이 보드의 구성종목(상위 10개에 든 종목)만 추린다.

    후보는 output/breakout-data.json(그날 계산)과 docs/breakout/data/breakout-data.json(직전 빌드의 사본)이고, 종가 기준일(asof)이 새 것을 쓴다.
    그날 돌파 지도 단계가 실패하면 자연히 전날 것이 쓰이고, 페이지는 종가 기준일이 다르다는 표시를 한다. 아무것도 없으면 None 을 넣고 행을 눌러도 펼쳐지지 않는다.
    """
    cands = [args.stocks] if args.stocks else [os.path.join(ROOT, "output", "breakout-data.json"), os.path.join(args.docs, "breakout", "data", "breakout-data.json")]
    best, best_path = None, None
    for p in cands:
        if not p or not os.path.exists(p):
            continue
        try:
            bd = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(bd, dict) or "stocks" not in bd or "spy" not in bd or "dates" not in bd:
            continue
        if best is None or str(bd.get("asof", "")) > str(best.get("asof", "")):
            best, best_path = bd, p
    if best is None:
        return None, None
    keys = {str(r[0]).upper() for rows in hold.values() for r in rows.get("rows", []) if r[0] and str(r[0]).upper() in best["stocks"]}
    fields = ("n", "x", "sym", "c", "cx", "h52", "ath", "rspy", "rqqq", "ipo", "athFrom")
    out = {"asof": best["asof"], "dates": best["dates"], "spy": best["spy"], "qqq": best["qqq"],
           "spyx": best.get("spyx", []), "qqqx": best.get("qqqx", []),
           "stocks": {k: {f: best["stocks"][k][f] for f in fields if f in best["stocks"][k]} for k in sorted(keys)},
           "listing": {k: v for k, v in (best.get("listing", {}).get("stocks", {})).items() if k in keys}}
    return out, best_path


def load_contrib(path, asof):
    """기여도 계산 결과. 종가 기준일이 이번 계산과 같을 때만 쓴다(계산이 실패해 전날 파일이 남은 경우를 거른다)."""
    try:
        c = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        print(f"contribution data not found ({os.path.relpath(path, ROOT)}): built without the contribution block")
        return None
    if c.get("asof") != asof:
        print(f"contribution data is for {c.get('asof')}, not {asof}: built without the contribution block")
        return None
    return {k: v for k, v in c.items() if k not in ("generated_at", "warnings")}


def load_etf_chart(path, asof, tickers):
    """Holdings 패널의 ETF 차트 자료. 종가 기준일이 이번 계산과 같을 때만 쓰고, 이 보드의 ETF 와 SPY·QQQ 만 남긴다."""
    try:
        c = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        print(f"ETF chart data not found ({os.path.relpath(path, ROOT)}): built without the ETF chart")
        return None
    if c.get("asof") != asof:
        print(f"ETF chart data is for {c.get('asof')}, not {asof}: built without the ETF chart")
        return None
    return {"asof": c["asof"], "dates": c["dates"], "etfs": {t: e for t, e in c.get("etfs", {}).items() if t in tickers}}


def load_news(path, asof):
    """뉴스·실적. 종가 기준일이 이번 계산과 같을 때만 쓴다."""
    try:
        n = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        print(f"news data not found ({os.path.relpath(path, ROOT)}): built without news and earnings")
        return None
    if (n.get("meta") or {}).get("asof") != asof:
        print(f"news data is for {(n.get('meta') or {}).get('asof')}, not {asof}: built without news and earnings")
        return None
    return n


def hold_key(sym, ex):
    """구성종목 행 → 목록 대조 키. 상장 점과 같은 규칙(미국은 티커, 그 밖은 "거래소:코드", 홍콩 코드는 네 자리)."""
    key = f"{EXCH_MAP.get(ex, ex)}:{sym}" if ex else CODE_ALIAS.get(sym, sym.upper())
    m = re.match(r"^HKEX:(\d+)$", key)
    return f"HKEX:{m.group(1).zfill(4)}" if m else key


def same_stock_marks(L):
    """같은 거래소에 같은 종목(대조 키 lk)이 다른 심볼로 이미 있던 때의 사건을 찾는다. {id(사건): 다른 심볼 문자열}.

    신규는 그 시점에 같은 lk 의 다른 심볼이 상장 중이었으면 '추가'(하이퍼리퀴드의 다른 dex, 바이낸스의 같은 종목 두 번째 계약),
    이탈은 그 뒤에도 같은 lk 의 다른 심볼이 남아 있으면 '심볼 이탈'이다. 로그의 사건을 거래소별로 시간순으로 다시 걸으며 판단한다.
    시각은 선물은 상장일(d), 나머지는 수집 시각(t1)을 쓰고, 같은 시각이면 신규를 먼저·심볼 이름순으로 본다.
    사건이 하나도 없는 심볼(처음 반영할 때 이미 있던 것)과 첫 사건이 이탈인 심볼은 처음부터 상장 중이었던 것으로 본다.
    """
    out = {}
    events, state = L.get("events", []), L.get("state", {})
    for v in ("b", "p", "h"):
        mine = sorted((e for e in events if e.get("v") == v),
                      key=lambda e: (e["d"] if v == "p" else (e.get("t1") or ""), 0 if e["e"] == "add" else 1, e["s"]))
        lk = {sym: info.get("lk") for sym, info in (state.get(v) or {}).items()}
        first = {}
        for e in mine:
            lk.setdefault(e["s"], e.get("lk"))
            first.setdefault(e["s"], e["e"])
        present = {sym for sym in lk if first.get(sym, "del") == "del"}
        for e in mine:
            sym, key = e["s"], e.get("lk")
            if e["e"] == "add":
                others = sorted(o for o in present if o != sym and lk.get(o) == key)
                present.add(sym)
            else:
                present.discard(sym)
                others = sorted(o for o in present if lk.get(o) == key)
            if key and others:
                out[id(e)] = ", ".join(others)
    return out


def load_listing_log(path, hold, etfs):
    """5) 상장 로그 탭의 자료. terminal/listing_log.py 가 쌓는 data/lists/log.json 을 페이지에 넣을 만큼 줄인다. 없으면 None.

    사건마다 [날짜, 거래소, 신규 1·이탈 0, 거래소 심볼, 티커, 이름, 종류, 시장, 상장일 여부, 직전 수집, 이번 수집,
    이 보드의 구성종목 심볼, 이 보드의 ETF 본체] 를 적는다. 구성종목 심볼은 상장 점과 같은 대조로 찾은 HOLD 행의 심볼(대문자)이고,
    ETF 본체는 그 상품이 이 보드의 ETF 자체(예: 하이퍼리퀴드의 SMH)일 때의 티커다. 맞는 것이 없으면 None.
    """
    try:
        L = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        print(f"listing log not found ({os.path.relpath(path, ROOT)}): built without the listing log")
        return None
    keymap = {}
    for h in hold.values():
        for sym, _name, _w, _ch, ex in h["rows"]:
            if not SWAP_RE.search(sym):
                keymap.setdefault(hold_key(sym, ex), sym.upper())
    marks = same_stock_marks(L)
    # 이름·종류·시장이 비어 있는 심볼(참조 상장이 없는 하이퍼리퀴드 dex 등)은 같은 거래소의 같은 종목 다른 심볼에서 빌려 온다
    known = {}
    for v, st in (L.get("state") or {}).items():
        for info in st.values():
            if info.get("n"):
                known.setdefault((v, info.get("lk")), info)
    ev = []
    for e in L.get("events", []):
        lk = e.get("lk") or ""
        m = re.match(r"^HKEX:(\d+)$", lk)
        lk = f"HKEX:{m.group(1).zfill(4)}" if m else lk
        k = known.get((e["v"], e.get("lk"))) if not e.get("n") else None
        u = e.get("u", "")
        if ":" in u and e["v"] == "p":   # 홍콩 계약은 기초 자산이 "HKEX:0700" 꼴이라 계약 이름(TENCENT)을 티커로 보인다
            u = re.sub(r"(USDT|USDC|USD1|USD)$", "", e["s"]) or u
        n, c, mk = (k["n"], k.get("c", ""), k.get("m", "")) if k else (e.get("n") or "", e.get("c", ""), e.get("m", ""))
        ev.append([e["d"], e["v"], 1 if e["e"] == "add" else 0, e["s"], u, n[:80], c, mk,
                   e.get("x", 0), e.get("t0"), e.get("t1"), keymap.get(lk), lk if lk in etfs else None, marks.get(id(e))])
    return {"updated": L.get("updated"), "prev": L.get("prev"), "counts": L.get("counts", {}), "ev": ev}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(ROOT, "output", "terminal-data.json"))
    ap.add_argument("--contrib", default=os.path.join(ROOT, "output", "contrib-data.json"))
    ap.add_argument("--etf-chart", default=os.path.join(ROOT, "output", "etf-chart-data.json"))
    ap.add_argument("--news", default=os.path.join(ROOT, "output", "news-data.json"))
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--lists", default=None, help="tradfi-ticker-lists 의 output 폴더")
    ap.add_argument("--listing-log", default=os.path.join(ROOT, "data", "lists", "log.json"), help="5) 상장 로그 탭의 누적 로그(terminal/listing_log.py)")
    ap.add_argument("--template", default=os.path.join(HERE, "template.html"))
    ap.add_argument("--docs", default=os.path.join(ROOT, "docs"))
    ap.add_argument("--breakout-artifact", default=BREAKOUT_ARTIFACT, help="아티팩트 본문의 3) 돌파 지도 탭이 열 주소")
    ap.add_argument("--stocks", default=None, help="종목 상세용 돌파 지도 계산 결과(breakout-data.json). 기본은 output/breakout-data.json 과 docs/breakout/data/breakout-data.json 가운데 종가 기준일이 새 것")
    args = ap.parse_args()

    cur = json.load(open(args.data, encoding="utf-8"))
    meta = json.load(open(args.meta, encoding="utf-8"))
    os.makedirs(os.path.join(args.docs, "data"), exist_ok=True)
    prev_path = os.path.join(args.docs, "data", "terminal-data.json")
    prev = json.load(open(prev_path, encoding="utf-8")) if os.path.exists(prev_path) else None
    same_asof = bool(prev and prev.get("asof") == cur.get("asof"))
    if same_asof:
        prev_for_alerts = None   # 같은 종가로 다시 빌드한 경우에는 변화가 없다
    else:
        prev_for_alerts = prev

    snaps = sorted(glob.glob(os.path.join(args.holdings, "*.csv")))
    if not snaps:
        raise SystemExit("holdings snapshot not found")
    snapshots = [(os.path.splitext(os.path.basename(p))[0], load_snapshot(p)) for p in snaps[-2:]]
    hold, hold_asof, hold_prev = build_hold(snapshots, meta)

    B, F, H, counts, list_ts = load_lists(args.lists) if args.lists else (None, None, None, {"b": 0, "f": 0, "h": 0}, None)
    elst, lst, swaps = {}, {}, {}
    for t in cur["etfs"]:
        v = flags(t, B, F, H)
        if v:
            elst[t] = v
    for t, h in hold.items():
        for sym, name, w, ch, ex in h["rows"]:
            if SWAP_RE.search(sym):
                u = SWAP_UNDERLYING.get(sym)
                v = flags(u, B, F, H) if u else ""
                if v:
                    swaps[sym] = v
                continue
            key = f"{EXCH_MAP.get(ex, ex)}:{sym}" if ex else CODE_ALIAS.get(sym, sym.upper())
            v = flags(key, B, F, H)
            if v:
                lst[sym] = v
    list_asof = (list_ts or "")[:10] or "없음"
    list_ts_txt = (list_ts or "").replace("T", " ").replace("Z", " UTC") or "수집 자료 없음"

    alerts = make_alerts(prev_for_alerts, cur)
    contrib = load_contrib(args.contrib, cur["asof"])
    brief = make_brief(cur, hold, alerts, prev_for_alerts.get("asof") if prev_for_alerts else None, contrib)

    data_slim = {t: {k: v for k, v in e.items() if k in ("pct", "rel", "S", "Q", "rrgPath", "rrgSpy", "sig", "weeks", "stale")} for t, e in cur["etfs"].items()}
    stocks_tok, stocks_src = load_stocks(args, hold)
    etfx = load_etf_chart(args.etf_chart, cur["asof"], set(cur["etfs"]) | {"SPY", "QQQ"})
    news = load_news(args.news, cur["asof"])
    listlog = load_listing_log(args.listing_log, hold, set(cur["etfs"]))
    src = "·".join(cur.get("source") or ["yahoo"])
    src_ko = {"yahoo": "야후 파이낸스", "stooq": "Stooq"}.get(src, src)
    tokens = {
        "__HOLD__": json.dumps(hold, ensure_ascii=False, separators=(",", ":")),
        "__DATA__": json.dumps(data_slim, ensure_ascii=False, separators=(",", ":")),
        "__PRICE_ASOF__": cur["asof"], "__PRICE_FIRST__": cur["first_date"],
        "__PRICE_DAYS__": str(cur["days"]), "__PRICE_WEEKS__": str(cur["weeks"]), "__PRICE_SOURCE__": src_ko,
        "__HOLD_ASOF__": hold_asof, "__HOLD_PREV__": hold_prev or "없음",
        "__LIST_ASOF__": list_asof, "__LIST_TS__": list_ts_txt,
        "__LIST_B__": str(counts["b"]), "__LIST_F__": str(counts["f"]), "__LIST_H__": str(counts["h"]),
        "__ELST__": json.dumps(elst, ensure_ascii=False, separators=(",", ":")),
        "__LST__": json.dumps(lst, ensure_ascii=False, separators=(",", ":")),
        "__SWAPS__": json.dumps(swaps, ensure_ascii=False, separators=(",", ":")),
        "__ALERTS__": json.dumps({"asof": cur["asof"], "prev_asof": prev_for_alerts.get("asof") if prev_for_alerts else None, "same_asof": same_asof, "alerts": alerts}, ensure_ascii=False, separators=(",", ":")),
        "__STOCKS__": json.dumps(stocks_tok, ensure_ascii=False, separators=(",", ":")),
        "__CONTRIB__": json.dumps(contrib, ensure_ascii=False, separators=(",", ":")),
        "__ETFX__": json.dumps(etfx, ensure_ascii=False, separators=(",", ":")),
        # 헤드라인은 바깥 글이라 <script> 를 닫지 못하게 '<' 를 \u003c 로 적는다(JSON 문자열 밖에는 '<' 가 없다)
        "__NEWS__": json.dumps(news, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
        # 상장 로그의 이름도 바깥 목록에서 온 글이라 같은 방식으로 적는다
        "__LISTLOG__": json.dumps(listlog, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"),
        "__BRIEF__": brief_html(brief),
        "__BUILT_AT__": built_at_kst(),
    }
    tmpl = open(args.template, encoding="utf-8").read()
    for k in tokens:
        if k not in tmpl:
            raise SystemExit(f"template token missing: {k}")

    def fill(extra):
        page = tmpl
        for k, v in {**tokens, **extra}.items():
            page = page.replace(k, v)
        leftover = re.findall(r"__[A-Z_]+__", page)
        if leftover:
            raise SystemExit(f"unreplaced tokens: {sorted(set(leftover))}")
        return page

    # 두 본문은 5) 돌파 지도 탭의 주소만 다르다
    page_site = fill({"__BREAKOUT_URL__": BREAKOUT_PAGE, "__NAV_TARGET__": "_self", "__TV_WIDGET__": "true" if TV_WIDGET_PAGES else "false"})
    page_artifact = fill({"__BREAKOUT_URL__": args.breakout_artifact, "__NAV_TARGET__": "_blank", "__TV_WIDGET__": "false"})
    with open(os.path.join(args.docs, "terminal.artifact.html"), "w", encoding="utf-8") as f:
        f.write(page_artifact)
    with open(os.path.join(args.docs, "index.html"), "w", encoding="utf-8") as f:
        f.write(SKELETON_HEAD + page_site + SKELETON_TAIL)
    with open(prev_path, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.docs, "data", "alerts.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": cur["asof"], "prev_asof": prev_for_alerts.get("asof") if prev_for_alerts else None, "alerts": alerts}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.docs, "data", "brief.txt"), "w", encoding="utf-8") as f:
        f.write(brief)
    with open(os.path.join(args.docs, "data", "alerts.txt"), "w", encoding="utf-8") as f:
        if alerts:
            f.write(alerts_text(cur, alerts))
    open(os.path.join(args.docs, ".nojekyll"), "a").close()
    print(f"built docs/ for {cur['asof']} · holdings {hold_asof} (prev {hold_prev}) · lists {list_asof} ({counts['b']}/{counts['f']}/{counts['h']}) · alerts {len(alerts)}"
          + (f" · stock details {len(stocks_tok['stocks'])} (asof {stocks_tok['asof']}, {os.path.relpath(stocks_src, ROOT)})" if stocks_tok else " · stock details none")
          + (f" · contribution {len(contrib['funds'])} funds" if contrib else " · contribution none")
          + (f" · ETF charts {sum(1 for t in etfx['etfs'] if t not in ('SPY', 'QQQ'))}" if etfx else " · ETF charts none")
          + (f" · news {sum(len(v.get('h', [])) for v in news['s'].values())} headlines, {sum(1 for v in news['s'].values() if v.get('e'))} with 8-K earnings" if news else " · news none")
          + (f" · listing log {len(listlog['ev'])} events ({sum(1 for e in listlog['ev'] if e[11] or e[12])} on this board, {sum(1 for e in listlog['ev'] if e[13])} same-stock)" if listlog else " · listing log none"))


if __name__ == "__main__":
    main()
