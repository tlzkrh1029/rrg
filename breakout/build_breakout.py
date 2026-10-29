#!/usr/bin/env python3
"""돌파 지도 페이지를 생성한다.

입력
  output/breakout-data.json        compute_breakout.py 결과
  breakout/template.html           토큰이 든 HTML 템플릿
  docs/breakout/data/breakout-data.json   직전 빌드의 사본(새 돌파·새 이탈을 가르는 데 쓴다)

출력 (docs/breakout/)
  index.html                       GitHub Pages 용 완전한 문서 (사이트 줄의 매트릭스 링크는 ../#board)
  breakout.artifact.html           Claude 아티팩트 발행용(문서 골격 없이 <title>부터; 매트릭스 링크는 매트릭스 아티팩트)
  data/breakout-data.json          이번 계산값 사본(판정 포함; 다음 실행에서 비교에 쓴다)
  data/alerts.json                 오늘의 새 돌파·새 이탈·실패·블록 알림(USD 기준)
  data/alerts.txt                  텔레그램용 조건 알림 본문(알릴 것이 없으면 빈 파일)
  data/brief.txt                   텔레그램용 주간 브리핑 본문

페이지의 DATA 에는 judge/stats 를 넣지 않는다(페이지가 같은 규칙으로 직접 판정한다).
"""

import argparse
import datetime as dt
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "terminal"))
from build_terminal import SKELETON_HEAD, SKELETON_TAIL, built_at_kst, html_escape  # noqa: E402

PAGE_URL = "https://tlzkrh1029.github.io/rrg/breakout/"
TERMINAL_PAGE = "../"                                                   # Pages: 같은 사이트의 터미널
TERMINAL_ARTIFACT = "https://claude.ai/artifact/6591K5Cr3HAFUAKMXav4in"  # 아티팩트: 매트릭스 아티팩트
HS = ("20", "55", "52w", "ath")
HLAB = {"20": "20일", "55": "55일", "52w": "52주", "ath": "사상 최고"}
MLAB = {"usd": "USD", "qqq": "QQQ 대비", "spy": "SPY 대비"}
BLOCK_ON, BLOCK_DOWN, BLOCK_FAIL = 0.5, 0.5, 0.3   # 페이지의 알림 타일과 같은 초안 임계값


def etfs_of(data):
    out = {}
    for e in data["etfs"]:
        for k, _w in e["rows"]:
            out.setdefault(k, []).append(e["t"])
    return out


def top_horizon(jd, mode, key, want=("lit",)):
    for H in ("ath", "52w", "55", "20"):
        if jd[mode][H].get(key, [None])[0] in want:
            return H
    return None


def make_alerts(cur, prev):
    """USD 기준. 항목: 새 돌파(1일째)·새 이탈(1일째)·오늘 실패·블록 알림. prev 와 asof 가 같으면 빈 목록."""
    if prev and prev.get("asof") == cur.get("asof"):
        return []
    jd, eo = cur["judge"], etfs_of(cur)
    alerts = []
    seen = set()
    # 새 돌파: 1일째인 칸을 가장 높은 지평으로 한 번만
    for H in ("ath", "52w", "55", "20"):
        for k, (st, days, over) in jd["usd"][H].items():
            if st == "lit" and days == 1 and k not in seen:
                seen.add(k)
                alerts.append({"type": "up", "H": H, "key": k, "name": cur["stocks"][k]["n"], "etfs": eo.get(k, []), "over": over,
                               "spy": jd["spy"][H][k][0], "qqq": jd["qqq"][H][k][0]})
    seen = set()
    for H in ("55", "20"):
        for k, (st, days, over) in jd["usd"][H].items():
            if st == "down" and days == 1 and k not in seen:
                seen.add(k)
                alerts.append({"type": "down", "H": H, "key": k, "name": cur["stocks"][k]["n"], "etfs": eo.get(k, []), "over": over})
    # 오늘 실패: 어제까지 켜져 있던 칸이 오늘 실패로 바뀐 것(직전 빌드와 비교)
    if prev and "judge" in prev:
        for H in ("ath", "52w", "55", "20"):
            for k, (st, days, over) in jd["usd"][H].items():
                if st == "failed" and prev["judge"]["usd"][H].get(k, [None])[0] == "lit":
                    alerts.append({"type": "failed", "H": H, "key": k, "name": cur["stocks"][k]["n"], "etfs": eo.get(k, []), "over": over})
    # 블록 알림(20일 USD): 켜진 비율 50% 이상, 이탈 50% 이상, 실패 30% 이상 — 직전 빌드에서 아니었던 블록만
    per = cur["stats"]["blocks_lit"]["usd"]["20"]
    pper = (prev or {}).get("stats", {}).get("blocks_lit", {}).get("usd", {}).get("20", {}) if prev else {}
    for t, (l, d, f, n) in per.items():
        if not n:
            continue
        pl, pd, pf, pn = pper.get(t, [0, 0, 0, n])
        if l / n >= BLOCK_ON and not (pn and pl / pn >= BLOCK_ON):
            alerts.append({"type": "block_on", "etf": t, "lit": l, "n": n})
        if d / n >= BLOCK_DOWN and not (pn and pd / pn >= BLOCK_DOWN):
            alerts.append({"type": "block_down", "etf": t, "down": d, "n": n})
        if f / n >= BLOCK_FAIL and not (pn and pf / pn >= BLOCK_FAIL):
            alerts.append({"type": "block_fail", "etf": t, "failed": f, "n": n})
    return alerts


def breadth_line(cur, mode):
    b = cur["stats"]["breadth"][mode]
    cells = b["20"]["cells"]
    return " · ".join(f"{HLAB[H]} {b[H]['lit']}" + (f"/{cells} ({b[H]['lit'] / cells * 100:.0f}%)" if H == "20" else "") for H in HS) \
        + f" · 이탈 {b['20']['down']} · 실패 {b['20']['failed']}"


def alerts_text(cur, alerts):
    if not alerts:
        return ""
    lines = [f"돌파 지도 {cur['asof']} (USD 기준, 직전 종가)", f"켜진 칸 {breadth_line(cur, 'usd')}", ""]
    ups = [a for a in alerts if a["type"] == "up"]
    if ups:
        lines.append("새 돌파 (1일째)")
        for H in ("ath", "52w", "55", "20"):
            xs = [a for a in ups if a["H"] == H]
            if xs:
                lines.append(f"  {HLAB[H]}: " + ", ".join(f"{a['key']}({'·'.join(a['etfs'])})" + ("*" if a["spy"] == "lit" else "") for a in xs))
        lines.append("  * SPY 대비로도 켜진 칸")
    dns = [a for a in alerts if a["type"] == "down"]
    if dns:
        lines.append("새 이탈 (1일째)")
        for H in ("55", "20"):
            xs = [a for a in dns if a["H"] == H]
            if xs:
                lines.append(f"  {HLAB[H]} 바닥: " + ", ".join(f"{a['key']}({'·'.join(a['etfs'])})" for a in xs))
    fl = [a for a in alerts if a["type"] == "failed"]
    if fl:
        lines.append("오늘 실패한 돌파: " + ", ".join(f"{a['key']}({'·'.join(a['etfs'])}, {HLAB[a['H']]})" for a in fl))
    bl = [a for a in alerts if a["type"].startswith("block")]
    if bl:
        lines.append("블록 알림 (20일): " + " · ".join(
            f"{a['etf']} {a['lit']}/{a['n']} 켜짐" if a["type"] == "block_on" else
            f"{a['etf']} ▼{a['down']}/{a['n']} 이탈" if a["type"] == "block_down" else
            f"{a['etf']} ×{a['failed']}/{a['n']} 실패" for a in bl))
    lines += ["", PAGE_URL]
    return "\n".join(lines)


def make_brief(cur, listing):
    jd, eo = cur["judge"], etfs_of(cur)
    ko = {e["t"]: e["ko"] for e in cur["etfs"]}
    lines = [f"돌파 지도 주간 브리핑 · 종가 {cur['asof']} · 구성종목 {cur['holdings_asof']}", ""]
    lines.append("켜진 칸 (지평별)")
    for m in ("usd", "qqq", "spy"):
        lines.append(f"  {MLAB[m]}: {breadth_line(cur, m)}")
    lines.append("")
    per = cur["stats"]["blocks_lit"]["usd"]["20"]
    ranked = sorted(((t, v) for t, v in per.items() if v[3]), key=lambda x: (-x[1][0] / x[1][3], -x[1][0]))
    lines.append("켜진 비율이 높은 블록 (USD 20일)")
    for t, (l, d, f, n) in ranked[:6]:
        if not l:
            break
        spy_l = cur["stats"]["blocks_lit"]["spy"]["20"][t][0]
        lines.append(f"  {t} {ko.get(t, '')} {l}/{n}" + (f" ▼{d}" if d else "") + (f" ×{f}" if f else "") + f" · SPY 대비 {spy_l}/{n}")
    dn = sorted(((t, v) for t, v in per.items() if v[3] and v[1]), key=lambda x: (-x[1][1] / x[1][3], -x[1][1]))[:4]
    if dn:
        lines.append("이탈이 몰린 블록 (USD 20일): " + " · ".join(f"{t} ▼{v[1]}/{v[3]}" for t, v in dn))
    lines.append("")
    lines.append("켜진 칸 (USD, 가장 높은 지평; * 는 SPY 대비로도 켜짐)")
    lit = []
    for k in cur["stocks"]:
        H = top_horizon(jd, "usd", k)
        if H:
            st, days, over = jd["usd"][H][k]
            lit.append((["ath", "52w", "55", "20"].index(H), days, -over, k, H))
    lit.sort()
    by_h = {}
    for _o, days, _ng, k, H in lit:
        by_h.setdefault(H, []).append(f"{k}({'·'.join(eo.get(k, []))}) {days}일" + ("*" if jd["spy"][H][k][0] == "lit" else ""))
    for H in ("ath", "52w", "55", "20"):
        if by_h.get(H):
            lines.append(f"  {HLAB[H]} {len(by_h[H])}: " + ", ".join(by_h[H]))
    if not lit:
        lines.append("  없음")
    weak = []
    for k in cur["stocks"]:
        st20 = jd["usd"]["20"][k]
        st55 = jd["usd"]["55"][k]
        if st55[0] == "down":
            weak.append(f"{k}({'·'.join(eo.get(k, []))}) 55일 이탈 {st55[1]}일")
        elif st20[0] == "down":
            weak.append(f"{k}({'·'.join(eo.get(k, []))}) 20일 이탈 {st20[1]}일")
        elif st20[0] == "failed" or st55[0] == "failed":
            weak.append(f"{k}({'·'.join(eo.get(k, []))}) 실패")
    lines.append("")
    lines.append(f"약세 신호 {len(weak)}: " + (", ".join(weak) if weak else "없음"))
    if listing.get("asof"):
        lit_keys = {k for *_x, k, _H in lit}
        anyl = sum(1 for k in lit_keys if k in listing["stocks"])
        lines.append("")
        lines.append(f"켜진 종목 {len(lit_keys)} 중 크립토 거래소 상장 {anyl} (목록 {listing['asof'][:10]})")
    lines += ["", PAGE_URL]
    return "\n".join(lines)


def load_xh(path, asof):
    """장후·장전 가격(terminal/fetch_xh.py 의 output/xh.json). 마지막 정규장 날짜가 종가 기준일과 같을 때만 쓴다."""
    try:
        x = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not x or x.get("date") != asof or not x.get("s"):
        return None
    return {k: x.get(k) for k in ("date", "next", "close_ts", "ah_at", "pm_at", "s")}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(ROOT, "output", "breakout-data.json"))
    ap.add_argument("--template", default=os.path.join(HERE, "template.html"))
    ap.add_argument("--docs", default=os.path.join(ROOT, "docs", "breakout"))
    ap.add_argument("--terminal-artifact", default=TERMINAL_ARTIFACT, help="아티팩트 본문의 매트릭스 링크가 열 주소")
    ap.add_argument("--xh", default=os.path.join(ROOT, "output", "xh.json"), help="장후·장전 가격(terminal/fetch_xh.py)")
    args = ap.parse_args()

    cur = json.load(open(args.data, encoding="utf-8"))
    os.makedirs(os.path.join(args.docs, "data"), exist_ok=True)
    prev_path = os.path.join(args.docs, "data", "breakout-data.json")
    prev = json.load(open(prev_path, encoding="utf-8")) if os.path.exists(prev_path) else None

    alerts = make_alerts(cur, prev)
    prev_asof = prev.get("asof") if prev else None
    kept = None
    if prev and prev.get("asof") == cur.get("asof"):
        try:
            old = json.load(open(os.path.join(args.docs, "data", "alerts.json"), encoding="utf-8"))
        except (OSError, ValueError):
            old = None
        if old and old.get("asof") == cur["asof"]:
            kept = old   # 같은 종가로 다시 만든 빌드(저녁 실행 등)는 그 종가의 첫 빌드가 찾은 변화를 그대로 둔다
            alerts, prev_asof = old.get("alerts") or [], old.get("prev_asof")
    brief = make_brief(cur, cur["listing"])
    page_data = {k: v for k, v in cur.items() if k not in ("judge", "stats", "listing")}
    listing = cur["listing"]
    src = "·".join(cur.get("sources") or ["yahoo"])
    src_ko = {"yahoo": "야후 파이낸스", "stooq": "Stooq", "yahoo·stooq": "야후 파이낸스·Stooq", "stooq·yahoo": "Stooq·야후 파이낸스"}.get(src, src)
    list_ts = listing.get("asof") or ""
    tokens = {
        "__DATA__": json.dumps(page_data, ensure_ascii=False, separators=(",", ":")),
        "__LISTING__": json.dumps(listing, ensure_ascii=False, separators=(",", ":")),
        "__PRICE_ASOF__": cur["asof"], "__PRICE_FIRST__": cur["first"], "__PRICE_DAYS__": str(cur["days"]), "__PRICE_SOURCE__": src_ko,
        "__HOLD_ASOF__": cur["holdings_asof"],
        "__LIST_ASOF__": list_ts[:10] or "없음", "__LIST_TS__": list_ts.replace("T", " ").replace("Z", " UTC") or "수집 자료 없음",
        "__LIST_B__": str(listing["counts"]["b"]), "__LIST_F__": str(listing["counts"]["p"]), "__LIST_H__": str(listing["counts"]["h"]),
        "__BUILT_AT__": built_at_kst(),
        # 장후·장전 가격(마지막 정규장 날짜가 종가 기준일과 같을 때만). 없으면 null
        "__XH__": json.dumps(load_xh(args.xh, cur["asof"]), ensure_ascii=False, separators=(",", ":")),
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

    page_site = fill({"__TERMINAL_URL__": TERMINAL_PAGE, "__NAV_TARGET__": "_self"})
    page_artifact = fill({"__TERMINAL_URL__": args.terminal_artifact, "__NAV_TARGET__": "_blank"})
    with open(os.path.join(args.docs, "breakout.artifact.html"), "w", encoding="utf-8") as f:
        f.write(page_artifact)
    with open(os.path.join(args.docs, "index.html"), "w", encoding="utf-8") as f:
        f.write(SKELETON_HEAD + page_site + SKELETON_TAIL)
    with open(prev_path, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(args.docs, "data", "alerts.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": cur["asof"], "prev_asof": prev_asof, "alerts": alerts}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.docs, "data", "alerts.txt"), "w", encoding="utf-8") as f:
        f.write(alerts_text(cur, alerts))
    with open(os.path.join(args.docs, "data", "brief.txt"), "w", encoding="utf-8") as f:
        f.write(brief)
    print(f"built {args.docs}: asof {cur['asof']}, {cur['stats']['blocks']} blocks, {cur['stats']['cells']} cells, "
          f"alerts {len(alerts)}" + ((" (kept from the first build of this close)" if kept else " (same asof as the previous build: no alerts)") if prev and prev.get("asof") == cur.get("asof") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
