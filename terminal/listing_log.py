#!/usr/bin/env python3
"""세 거래소 상장 목록(data/lists)을 직전 상태와 비교해 신규·이탈을 누적 로그(data/lists/log.json)에 쌓는다.

tradfi-ticker-lists 가 매일 받는 세 목록 가운데 바이낸스 TradFi 선물에는 계약마다 상장일(onboard_date)이 있지만,
바이낸스 bStocks 와 하이퍼리퀴드 HIP-3 목록에는 날짜가 없다. 그래서 목록을 받을 때마다 직전 상태와 비교해
처음 보인 것(신규)과 사라진 것(이탈)을 그 사이의 두 수집 시각과 함께 적는다. 선물의 신규는 상장일을 그대로 쓴다.
매트릭스의 5) 상장 로그 탭(terminal/build_terminal.py)이 이 파일을 읽는다.

입력
  --lists DIR   binance_spot_bstocks.csv · binance_futures_tradfi.csv · hyperliquid_hip3_stocks.csv · summary.json (기본 data/lists)
출력
  --log FILE    누적 로그(기본 data/lists/log.json). 한 줄에 한 항목씩 적어 git 의 변경 내역을 읽기 쉽게 한다.
    {"version": 1, "updated": 마지막으로 반영한 목록의 수집 시각(UTC), "prev": 그 앞 수집 시각, "counts": {b, p, h},
     "state": {"b": {심볼: 정보}, "p": {...}, "h": {...}},   마지막으로 확인한 상장 목록
     "events": [{v, e, s, u, lk, n, c, m, d, x, t0, t1}, ...]}  날짜 내림차순
    v 거래소(b bStocks 현물 · p 바이낸스 TradFi 선물 · h 하이퍼리퀴드 HIP-3), e add(신규)/del(이탈), s 거래소 심볼,
    u 표시 티커, lk 대조 키(상장 점과 같은 꼴: 미국은 티커, 그 밖은 "KRX:005930"), n 이름, c stock/etf/unknown,
    m 시장(US·KR·HK·CN·JP·TW, 모르면 빈칸), d 날짜(KST), x 1 이면 d 가 거래소가 밝힌 상장일, 0 이면 t0~t1 사이에 목록에서 확인한 날,
    t0·t1 직전 수집과 이번 수집 시각(UTC). 처음 반영하는 거래소의 목록은 상태로만 잡고(선물은 상장일이 있으므로 신규로 적는다),
    그 뒤로 달라진 것만 적는다.

안전 규칙
  목록의 수집 시각(summary.json 의 generated_at)이 로그의 updated 와 같거나 이르면 아무것도 하지 않는다
  (목록 수집이 실패해 직전 사본이나 예전 사본을 쓴 날). 한 거래소의 목록이 비었거나 수집 오류가 적혀 있으면 그 거래소는 건너뛴다.
  상장 수가 직전보다 20% 넘게(4종 이상) 줄면 이탈을 적지 않고 상태에 남겨 두며, 다음 정상 수집에서 다시 판단한다.
  하이퍼리퀴드는 수집 오류가 난 dex 의 코인을 이탈로 보지 않는다.

종료 코드: 0 = 반영했거나 바뀐 것이 없음, 1 = 목록을 읽지 못함(로그는 그대로 둔다).
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
from build_terminal import norm_listing  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
FILES = {"b": "binance_spot_bstocks.csv", "p": "binance_futures_tradfi.csv", "h": "hyperliquid_hip3_stocks.csv"}
SUMKEY = {"b": "binance_spot_bstocks", "p": "binance_futures_tradfi", "h": "hyperliquid_hip3_stocks"}
US_EX = {"US", "NASDAQ", "NYSE", "NYSE ARCA", "NYSE AMERICAN", "CBOE", "CBOE BZX", "AMEX"}
MARKET = {"KRX": "KR", "KOSDAQ": "KR", "HKEX": "HK", "SSE": "CN", "SSE STAR": "CN", "SZSE": "CN", "TSE": "JP", "TWSE": "TW", "TPEX": "TW"}
DROP_RATIO, DROP_MIN = 0.8, 4
BSTOCKS_TAG = re.compile(r"\s*[(（]\s*bStocks\s*[)）]\s*$", re.I)   # 바이낸스 이름 끝의 "(bStocks)" 꼬리표
# 나스닥 심볼 디렉터리 이름 끝의 증권 종류("- Class A Common Stock", "Ordinary Shares", "American Depositary Shares" …)
SEC_TAIL = re.compile(r"\s*(?:[-–,]\s*)?(?:\((?:NEW|DE)\)\s*)*(?:(?:Class|Series)\s+[A-Z0-9]+\s+)?"
                      r"(?:Common\s+Stock|Common\s+Shares|Ordinary\s+Shares?|American\s+Deposit[ao]ry\s+Shares?|Deposit[ao]ry\s+Shares?|New\s+York\s+Registry\s+Shares?|Registered\s+Shares?|Sponsored\s+ADR|ADRs?|ADSs?)\b.*$", re.I)


def clean_name(n):
    n = BSTOCKS_TAG.sub("", (n or "").strip())
    n = SEC_TAIL.sub("", n).strip()
    n = re.sub(r"\s*\((?:NEW|DE)\)\s*$", "", n).strip()
    return n.rstrip(" -–,").strip()


def market_of(ref):
    m = re.match(r"^([A-Za-z ]+):", (ref or "").strip())
    if not m:
        return ""
    ex = m.group(1).strip().upper()
    return "US" if ex in US_EX else MARKET.get(ex, "")


def kst_date(iso):
    t = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return t.astimezone(KST).date().isoformat()


def read_lists(lists_dir):
    """{v: {심볼: 정보}} (상장 중인 것만), 수집 시각, 거래소별 상태 {v: (ok, 오류 dex 집합, 사유)}."""
    summ = json.load(open(os.path.join(lists_dir, "summary.json"), encoding="utf-8"))
    ts = summ.get("generated_at")
    if not ts:
        raise ValueError("summary.json 에 generated_at 이 없음")
    cur, health = {}, {}
    for v, fn in FILES.items():
        s = summ.get(SUMKEY[v]) or {}
        rows = list(csv.DictReader(open(os.path.join(lists_dir, fn), encoding="utf-8-sig")))
        out = {}
        for r in rows:
            if v == "b":
                if r.get("status") != "TRADING":
                    continue
                u = (r.get("underlying") or r.get("us_listing") or r.get("base_asset") or "").strip().upper()
                info = {"u": u, "lk": (r.get("us_listing") or u).strip().upper(), "n": clean_name(r.get("binance_name") or r.get("us_security_name")),
                        "c": r.get("asset_class") or "unknown", "m": "US"}
            elif v == "p":
                if r.get("status") not in ("TRADING", "PENDING_TRADING"):
                    continue
                u = (r.get("underlying") or r.get("base_asset") or "").strip().upper()
                lk = norm_listing(r.get("listing")) or u
                if ":" in u:   # 홍콩 계약은 기초 자산이 "HKEX:0700" 꼴이다. 표시 티커는 계약 이름(TENCENT)으로 둔다
                    u = (r.get("base_asset") or u).strip().upper()
                info = {"u": u, "lk": lk, "n": clean_name(r.get("security_name")),
                        "c": r.get("asset_class") or "unknown", "m": (r.get("market") or "").strip(), "od": (r.get("onboard_date") or "").strip()}
            else:
                if r.get("delisted") == "True":
                    continue
                u = (r.get("ticker") or r["coin"].split(":")[-1]).strip().upper()
                info = {"u": u, "lk": norm_listing(r.get("reference_listing")) or u,
                        "n": clean_name(r.get("security_name") or r.get("display_name")),
                        "c": r.get("asset_class") or "unknown", "m": market_of(r.get("reference_listing")), "dex": r.get("dex") or ""}
            sym = (r.get("symbol") or r.get("coin") or "").strip()
            if sym:
                out[sym] = info
        why = s.get("error") or ("목록이 비었음" if not out else "")
        bad = set((s.get("dex_errors") or {}).keys()) if v == "h" else set()
        cur[v], health[v] = out, (not why, bad, why)
    return cur, ts, health


def event(v, e, sym, info, t0, t1):
    exact = v == "p" and e == "add" and bool(info.get("od"))
    ev = {"v": v, "e": e, "s": sym, "u": info.get("u", ""), "lk": info.get("lk", ""), "n": info.get("n", ""),
          "c": info.get("c", ""), "m": info.get("m", ""), "d": info["od"] if exact else kst_date(t1), "x": 1 if exact else 0,
          "t0": t0, "t1": t1}
    if info.get("dex"):
        ev["dex"] = info["dex"]
    return ev


def ev_sort_key(e):
    return (e["d"], e.get("t1") or "", {"add": 1, "del": 0}[e["e"]], e["v"], e["u"], e["s"])


def update(log, lists_dir):
    """로그(dict 또는 None)에 목록을 반영한 새 로그와, 새로 생긴 사건 목록, 요약 문장을 돌려준다."""
    cur, ts, health = read_lists(lists_dir)
    if log and log.get("updated") and ts <= log["updated"]:
        return log, [], f"목록 수집 시각 {ts} 이 로그의 {log['updated']} 와 같거나 일러서 건너뜀"
    t0 = log.get("updated") if log else None
    state = (log or {}).get("state") or {}
    new_state, new_events, notes = {}, [], []
    for v in FILES:
        ok, bad, why = health[v]
        old = state.get(v) or {}
        if not ok:
            new_state[v] = old
            notes.append(f"{v}: 건너뜀({why})")
            continue
        now = cur[v]
        if not old:
            # 처음 반영하는 거래소: 상태만 잡는다. 선물은 상장일이 있으므로 모두 신규로 적는다
            new_state[v] = now
            if v == "p":
                new_events += [event(v, "add", k, now[k], None, ts) for k in now]
            notes.append(f"{v}: 처음 반영 {len(now)}종")
            continue
        added = [k for k in now if k not in old]
        removed = [k for k in old if k not in now]
        keep = {}
        if bad:
            held = [k for k in removed if k.split(":")[0] in bad]
            keep.update({k: old[k] for k in held})
            removed = [k for k in removed if k not in keep]
            if held:
                notes.append(f"{v}: 수집 오류 dex({','.join(sorted(bad))})의 {len(held)}종은 이탈로 보지 않음")
        if len(now) < len(old) * DROP_RATIO and len(old) - len(now) >= DROP_MIN:
            keep.update({k: old[k] for k in removed})
            notes.append(f"{v}: 상장 수가 {len(old)} → {len(now)} 로 크게 줄어 이탈 {len(removed)}종을 보류")
            removed = []
        new_events += [event(v, "add", k, now[k], t0, ts) for k in added]
        new_events += [event(v, "del", k, old[k], t0, ts) for k in removed]
        new_state[v] = {**now, **keep}
        notes.append(f"{v}: 신규 {len(added)} · 이탈 {len(removed)} · 현재 {len(new_state[v])}")
    events = sorted(((log or {}).get("events") or []) + new_events, key=ev_sort_key, reverse=True)
    out = {"version": 1, "updated": ts, "prev": t0, "counts": {v: len(new_state[v]) for v in FILES},
           "state": {v: dict(sorted(new_state[v].items())) for v in FILES}, "events": events}
    return out, new_events, " · ".join(notes)


def dump(log, path):
    """한 줄에 한 항목: 상태의 종목 하나, 사건 하나가 각각 한 줄이 되게 쓴다(유효한 JSON)."""
    j = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    lines = ["{" + f'"version":{log["version"]},"updated":{j(log["updated"])},"prev":{j(log["prev"])},"counts":{j(log["counts"])},', '"state":{']
    for i, v in enumerate(FILES):
        items = list(log["state"][v].items())
        lines.append(f'"{v}":{{')
        lines += [f"{j(k)}:{j(info)}" + ("," if n < len(items) - 1 else "") for n, (k, info) in enumerate(items)]
        lines.append("}" + ("," if i < len(FILES) - 1 else ""))
    lines.append("},")
    lines.append('"events":[')
    ev = log["events"]
    lines += [j(e) + ("," if n < len(ev) - 1 else "") for n, e in enumerate(ev)]
    lines.append("]}")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    json.load(open(tmp, encoding="utf-8"))   # 쓴 것이 JSON 으로 다시 읽히는지 확인한 뒤 바꾼다
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lists", default=os.path.join(ROOT, "data", "lists"))
    ap.add_argument("--log", default=os.path.join(ROOT, "data", "lists", "log.json"))
    args = ap.parse_args()
    log = None
    if os.path.exists(args.log):
        try:
            log = json.load(open(args.log, encoding="utf-8"))
        except ValueError as e:
            print(f"listing log unreadable ({e}); keeping the file and stopping", file=sys.stderr)
            return 1
    try:
        new, events, note = update(log, args.lists)
    except (OSError, ValueError, KeyError) as e:
        print(f"listing lists unreadable: {e!r}", file=sys.stderr)
        return 1
    if new is not log:
        dump(new, args.log)
    adds = [e for e in events if e["e"] == "add"]
    dels = [e for e in events if e["e"] == "del"]
    print(f"listing log: {note}")
    for tag, group in (("+", adds), ("-", dels)):
        if group:
            print(f"  {tag} " + ", ".join(f"{e['v']}:{e['u']}" for e in group[:40]) + (" …" if len(group) > 40 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
