#!/usr/bin/env python3
"""장후·장전 가격(시간외 가격)을 받아 output/xh.json 에 쓴다.

아침 빌드(미국 장 마감 뒤)와 저녁 실행(미국 장전 시간)이 함께 쓴다. 종목마다 Yahoo chart 의 5분봉 두 거래일치를
장전·장후 봉까지(includePrePost) 받아 다음 값을 읽는다.
  종가   meta.regularMarketPrice (마지막 정규장. 그 날짜는 meta.regularMarketTime 의 뉴욕 날짜)
  장후   그 날짜 16:00~20:00 ET 의 마지막 봉
  장전   그다음 거래일 04:00~09:30 ET 의 마지막 봉 (장전 시간 전이면 없음)
가격은 봉의 종가이고, 시각은 그 값이 마지막으로 바뀔 수 있었던 때(봉 시작 + 5분, 지금보다 늦으면 지금)다.
미국 밖 상장 종목(hasPrePostMarketData 가 false)과 받지 못한 종목은 넣지 않는다.

대상: ETF 는 output/prices_summary.json 의 groups 전체, 종목은 output/breakout-data.json(없으면
docs/breakout/data/ 의 사본)의 종목 가운데 미국 상장 종목. 키는 페이지가 쓰는 키(구성종목 표의 표기, 예 BRK.B)다.

같은 마지막 정규장 날짜의 output/xh.json 이 이미 있으면(아침 빌드가 쓴 것) 그 값을 바탕에 두고 새 값으로 덮는다.
저녁 실행은 그사이 끝난 장후 거래의 마지막 가격으로 장후 값을 고치고 장전 값을 더한다.

--mode
  auto     (기본) 뉴욕 시각으로 정한다: 04:00~09:30 이면 pm, 09:30~16:00 이면 정규장이라 아무것도 쓰지 않고 3 으로 끝난다
  ah · pm  강제로 정한다(시험용). 정규장 시간에도 쓴다

output/xh.json
  date      마지막 정규장 날짜(뉴욕). 페이지는 이 날짜가 빌드의 종가 기준일과 같을 때만 쓴다
  next      그다음 거래일(장전 봉이 있으면 그 날짜, 없으면 다음 평일). 장 시작 전 실적을 고르는 데 쓴다
  close_ts  date 의 16:00 ET (unix 초). 장 마감 뒤 기사를 고르는 기준
  ah_at     장후 값을 마지막으로 받은 시각(UTC ISO) · pm_at 장전 값을 받은 시각(없으면 null)
  s         {키: [종가, 장후 가격, 장후 시각 'HH:MM', 장전 가격, 장전 시각 'HH:MM']} (없는 값은 null)

종료 코드: 0 정상, 1 받은 값이 하나도 없음, 2 일부 실패, 3 정규장 시간이라 쓰지 않음.
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import fetch_prices as fp  # noqa: E402  (http_get, YAHOO_HOSTS, NY)

URL = "{host}/v8/finance/chart/{sym}?range=2d&interval=5m&includePrePost=true"
WORKERS = 6
NY = fp.NY or dt.timezone(dt.timedelta(hours=-4))


def et(ts):
    return dt.datetime.fromtimestamp(ts, NY)


def hm(ts, now):
    """봉 시작 + 5분(지금보다 늦으면 지금)의 뉴욕 시각 'HH:MM'."""
    return et(min(ts + 300, now)).strftime("%H:%M")


def parse(d, now):
    """chart 응답 하나에서 [종가, 장후, 장후 시각, 장전, 장전 시각]과 정규장 날짜·장전 날짜를 읽는다."""
    res = ((d or {}).get("chart") or {}).get("result") or []
    if not res:
        return None
    r = res[0]
    m = r.get("meta") or {}
    if not m.get("hasPrePostMarketData") or not m.get("regularMarketPrice") or not m.get("regularMarketTime"):
        return None
    day = et(m["regularMarketTime"]).date()
    ts = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    cl = q.get("close") or []
    ah = pm = None
    pm_day = None
    for t, c in zip(ts, cl):
        if c is None:
            continue
        x = et(t)
        mins = x.hour * 60 + x.minute
        if x.date() == day and 16 * 60 <= mins < 20 * 60:
            ah = (round(c, 4), hm(t, now))
        elif x.date() > day and 4 * 60 <= mins < 9 * 60 + 30:
            pm = (round(c, 4), hm(t, now))
            pm_day = x.date()
    row = [round(m["regularMarketPrice"], 4), ah[0] if ah else None, ah[1] if ah else None, pm[0] if pm else None, pm[1] if pm else None]
    return row, day, pm_day


def fetch(sym, now):
    err = None
    for host in fp.YAHOO_HOSTS:
        try:
            d = json.loads(fp.http_get(URL.format(host=host, sym=urllib.parse.quote(sym)), retries=3, timeout=30))
            return parse(d, now), None
        except Exception as e:  # noqa: BLE001  (다음 호스트로)
            err = f"{type(e).__name__}: {e}"[:160]
    return None, err


def universe(out):
    """(키, Yahoo 심볼) 목록: ETF 전체와 미국 상장 종목."""
    pairs = []
    ps = os.path.join(out, "prices_summary.json")
    if os.path.exists(ps):
        groups = json.load(open(ps, encoding="utf-8")).get("groups") or {}
        for g in groups.values():
            pairs += [(t, t) for t in g]
    else:
        pairs += [(t, t) for t in fp.TICKERS]
    for p in (os.path.join(out, "breakout-data.json"), os.path.join(ROOT, "docs", "breakout", "data", "breakout-data.json")):
        if os.path.exists(p):
            for k, v in (json.load(open(p, encoding="utf-8")).get("stocks") or {}).items():
                sym = v.get("sym") or k
                if "." in sym:          # Yahoo 의 거래소 접미어(.KS, .TO …)가 붙은 미국 밖 종목
                    continue
                pairs.append((k.upper(), sym))
            break
    seen, out_pairs = set(), []
    for k, s in pairs:
        if k not in seen:
            seen.add(k)
            out_pairs.append((k, s))
    return out_pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "output"))
    ap.add_argument("--mode", choices=["auto", "ah", "pm"], default="auto")
    args = ap.parse_args()

    now_dt = dt.datetime.now(dt.timezone.utc)
    now = int(now_dt.timestamp())
    n = et(now)
    mins = n.hour * 60 + n.minute
    mode = args.mode
    if mode == "auto":
        if n.weekday() < 5 and 9 * 60 + 30 <= mins < 16 * 60:
            print(f"regular session in New York ({n:%a %H:%M} ET): xh.json not written")
            return 3
        mode = "pm" if n.weekday() < 5 and 4 * 60 <= mins < 9 * 60 + 30 else "ah"

    pairs = universe(args.out)
    rows, days, pm_days, failed = {}, {}, {}, []
    with cf.ThreadPoolExecutor(WORKERS) as ex:
        futs = {ex.submit(fetch, s, now): k for k, s in pairs}
        for f in cf.as_completed(futs):
            k = futs[f]
            got, err = f.result()
            if got is None:
                if err:
                    failed.append(k)
                continue
            row, day, pm_day = got
            rows[k], days[k] = row, day
            if pm_day:
                pm_days[k] = pm_day
    if not rows:
        print(f"xh: nothing fetched ({len(failed)} failed of {len(pairs)})")
        return 1

    # 마지막 정규장 날짜: 가장 많은 종목이 가리키는 날짜. 다른 날짜의 종목(거래 정지 등)은 뺀다
    day = max(set(days.values()), key=list(days.values()).count)
    rows = {k: v for k, v in rows.items() if days[k] == day}
    if mode == "ah":
        for v in rows.values():
            v[3] = v[4] = None
    pm_day = max(set(pm_days.values()), key=list(pm_days.values()).count) if pm_days and mode == "pm" else None
    nxt = pm_day or (day + dt.timedelta(days=3 if day.weekday() == 4 else 1))

    path = os.path.join(args.out, "xh.json")
    old = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    s = {}
    if old.get("date") == day.isoformat():
        s = {k: list(v) for k, v in (old.get("s") or {}).items()}
    for k, v in rows.items():
        base = s.get(k) or [None] * 5
        s[k] = [v[0], v[1] if v[1] is not None else base[1], v[2] if v[1] is not None else base[2],
                v[3] if v[3] is not None else (base[3] if mode == "pm" else None), v[4] if v[3] is not None else (base[4] if mode == "pm" else None)]
    close_ts = int(dt.datetime.combine(day, dt.time(16, 0), NY).timestamp())
    at = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    same = old.get("date") == day.isoformat()
    got_ah = any(v[1] is not None for v in rows.values())
    got_pm = mode == "pm" and any(v[3] is not None for v in rows.values())
    doc = {
        "date": day.isoformat(),
        "next": nxt.isoformat(),
        "close_ts": close_ts,
        "ah_at": at if got_ah else (old.get("ah_at") if same else None),
        "pm_at": at if got_pm else (old.get("pm_at") if same and mode == "pm" else None),
        "mode": mode,
        "failed": sorted(failed),
        "s": dict(sorted(s.items())),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    nah = sum(1 for v in s.values() if v[1] is not None)
    npm = sum(1 for v in s.values() if v[3] is not None)
    print(f"xh {mode} for {day} ({n:%a %H:%M} ET): {len(s)} symbols · after-hours {nah} · pre-market {npm} · failed {len(failed)} of {len(pairs)}")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
