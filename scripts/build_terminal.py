#!/usr/bin/env python3
"""터미널 페이지를 생성한다.

입력
  output/terminal-data.json        compute_terminal.py 결과
  output/holdings/<date>.csv       구성종목 스냅샷(최신 두 개를 써서 변동 %p 를 계산)
  data/etf_meta.json               ETF 이름·순자산·보유 종목 수·출처 URL
  --lists DIR                      tradfi-ticker-lists 의 output 폴더(상장 점). 없으면 점을 비운다.
  template/terminal.html           토큰이 든 HTML 템플릿

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
PAGE_URL = "https://tlzkrh1029.github.io/rrg/"

KO = {"XLK": "기술", "XLF": "금융", "XLV": "헬스케어", "XLY": "경기소비재", "XLC": "커뮤니케이션", "XLI": "산업재",
      "XLP": "필수소비재", "XLE": "에너지", "XLU": "유틸리티", "XLB": "소재", "XLRE": "부동산",
      "SMH": "반도체", "IGV": "소프트웨어", "XBI": "바이오", "GNOM": "유전체학", "ARKG": "유전체학 (액티브)", "KRE": "지역은행",
      "XHB": "주택건설", "XME": "금속·광업", "URA": "우라늄", "ITA": "항공우주·방산", "PAVE": "미국 인프라", "DRAM": "메모리반도체"}
SECTORS = ["XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE", "XLU", "XLB", "XLRE"]
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
        rows = cur.get(t, [])
        oldw = {norm_sym(r["symbol"]): float(r["weight_pct"]) for r in prev.get(t, [])}
        out = []
        for r in rows:
            sym = norm_sym(r["symbol"])
            w = float(r["weight_pct"])
            ch = None if (prev_date is None or sym not in oldw) else round(w - oldw[sym], 2)
            out.append([sym, r["name"], f"{w:.2f}%", ch, exch_of(r["symbol"])])
        newset = {norm_sym(r["symbol"]) for r in rows}
        hold[t] = {
            "name": m["name"], "aum": m["aum"], "aumAsof": m["aum_asof"][5:], "n": m["n"],
            "top10": f"{sum(float(r['weight_pct']) for r in rows):.2f}%",
            "asof": long_date(cur_date), "prev": long_date(prev_date) if prev_date else "없음", "url": m["url"], "rows": out,
            "entered": [norm_sym(r["symbol"]) for r in rows if prev_date and norm_sym(r["symbol"]) not in oldw],
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


def make_alerts(prev, cur):
    """prev 가 없으면 빈 목록. 조건: RRG 국면 변화, 새로 켜진 신호, 전환순 상위 3 진입."""
    alerts = []
    if not prev:
        return alerts
    pp, pc = phases(prev), phases(cur)
    for t in cur["etfs"]:
        e = cur["etfs"][t]
        bench = e["parent"] or "SPY"
        if pp.get(t, {}).get("bench") and pc[t]["bench"] and pp[t]["bench"] != pc[t]["bench"]:
            alerts.append({"etf": t, "type": "rrg", "text": f"{t} {KO[t]}: RRG {pp[t]['bench']} → {pc[t]['bench']} (vs {bench})"})
        if e["parent"] and pp.get(t, {}).get("spy") and pc[t]["spy"] and pp[t]["spy"] != pc[t]["spy"]:
            alerts.append({"etf": t, "type": "rrg", "text": f"{t} {KO[t]}: RRG {pp[t]['spy']} → {pc[t]['spy']} (vs SPY)"})
        old_sig = {s[1] for s in prev["etfs"].get(t, {}).get("sig", [])}
        for s in e["sig"]:
            if s[1] not in old_sig:
                alerts.append({"etf": t, "type": "signal", "text": f"{t} {KO[t]}: {s[0]} {s[1]}"})
    top_prev = [t for t, _ in sorted(((t, shift_score(e)) for t, e in prev["etfs"].items() if shift_score(e) is not None), key=lambda x: -x[1])[:3]]
    top_cur = [t for t, _ in sorted(((t, shift_score(e)) for t, e in cur["etfs"].items() if shift_score(e) is not None), key=lambda x: -x[1])[:3]]
    for t in top_cur:
        if t not in top_prev:
            alerts.append({"etf": t, "type": "shift", "text": f"{t} {KO[t]}: 전환순 상위 3 진입 (점수 {shift_score(cur['etfs'][t]):.0f})"})
    return alerts


def make_brief(cur, hold, alerts, prev_asof):
    E = cur["etfs"]
    ph = phases(cur)
    lines = [f"📊 섹터 전환 터미널 · {cur['asof']} 종가 기준", ""]
    ranked = sorted(((t, shift_score(e)) for t, e in E.items() if shift_score(e) is not None), key=lambda x: -x[1])
    lines.append("전환순 상위 3 (1W·1M 평균 − 12M 백분위)")
    for t, s in ranked[:3]:
        p = E[t]["pct"]
        lines.append(f"  {t} {KO[t]}: {s:+.0f}  [{'/'.join('n/a' if v is None else str(v) for v in p)}]")
    lines.append("")
    by = {}
    for t in SECTORS:
        by.setdefault(ph[t]["bench"], []).append(t)
    lines.append("1단 RRG (섹터 ÷ SPY)")
    for k in ("Leading", "Improving", "Weakening", "Lagging"):
        if by.get(k):
            lines.append(f"  {k}: {' '.join(by[k])}")
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
    lines.append(f"직전 빌드({prev_asof or '없음'}) 대비 변화")
    if alerts:
        for a in alerts:
            lines.append(f"  · {a['text']}")
    else:
        lines.append("  없음")
    changed = [t for t, h in hold.items() if h["entered"] or h["left"]]
    if changed:
        lines.append("")
        lines.append("구성종목 상위 10 변화: " + ", ".join(f"{t}(+{len(hold[t]['entered'])}/−{len(hold[t]['left'])})" for t in changed))
    lines.append("")
    lines.append(PAGE_URL)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(ROOT, "output", "terminal-data.json"))
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--lists", default=None, help="tradfi-ticker-lists 의 output 폴더")
    ap.add_argument("--template", default=os.path.join(ROOT, "template", "terminal.html"))
    ap.add_argument("--docs", default=os.path.join(ROOT, "docs"))
    args = ap.parse_args()

    cur = json.load(open(args.data, encoding="utf-8"))
    meta = json.load(open(args.meta, encoding="utf-8"))
    os.makedirs(os.path.join(args.docs, "data"), exist_ok=True)
    prev_path = os.path.join(args.docs, "data", "terminal-data.json")
    prev = json.load(open(prev_path, encoding="utf-8")) if os.path.exists(prev_path) else None
    if prev and prev.get("asof") == cur.get("asof"):
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
    brief = make_brief(cur, hold, alerts, prev_for_alerts.get("asof") if prev_for_alerts else None)

    data_slim = {t: {k: v for k, v in e.items() if k in ("pct", "rel", "S", "Q", "rrgPath", "rrgSpy", "sig", "weeks")} for t, e in cur["etfs"].items()}
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
    }
    page = open(args.template, encoding="utf-8").read()
    for k, v in tokens.items():
        if k not in page:
            raise SystemExit(f"template token missing: {k}")
        page = page.replace(k, v)
    leftover = re.findall(r"__[A-Z_]+__", page)
    if leftover:
        raise SystemExit(f"unreplaced tokens: {sorted(set(leftover))}")

    with open(os.path.join(args.docs, "terminal.artifact.html"), "w", encoding="utf-8") as f:
        f.write(page)
    with open(os.path.join(args.docs, "index.html"), "w", encoding="utf-8") as f:
        f.write(SKELETON_HEAD + page + SKELETON_TAIL)
    with open(prev_path, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.docs, "data", "alerts.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": cur["asof"], "prev_asof": prev_for_alerts.get("asof") if prev_for_alerts else None, "alerts": alerts}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.docs, "data", "brief.txt"), "w", encoding="utf-8") as f:
        f.write(brief)
    with open(os.path.join(args.docs, "data", "alerts.txt"), "w", encoding="utf-8") as f:
        if alerts:
            f.write(f"🔔 섹터 전환 터미널 · {cur['asof']} 종가 · 변화 {len(alerts)}건\n" + "\n".join("· " + a["text"] for a in alerts) + f"\n{PAGE_URL}")
    open(os.path.join(args.docs, ".nojekyll"), "a").close()
    print(f"built docs/ for {cur['asof']} · holdings {hold_asof} (prev {hold_prev}) · lists {list_asof} ({counts['b']}/{counts['f']}/{counts['h']}) · alerts {len(alerts)}")


if __name__ == "__main__":
    main()
