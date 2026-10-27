#!/usr/bin/env python3
"""매트릭스의 뉴스·실적 자료를 받는다 (Holdings 패널과 뉴스 칼럼).

대상: 최신 구성종목 스냅샷에서 터미널 ETF 의 상위 10 종목.
1. TradingView 심볼: 미국 상장 종목은 NASDAQ Trader 상장 목록(nasdaqlisted.txt·otherlisted.txt)으로 거래소를 찾고
   (NASDAQ·NYSE·AMEX·CBOE; 목록에 없으면 SEC 제출 목록의 거래소), 해외 상장 종목은 구성종목 표의 거래소 접두어를
   TV_EXCH 표로 바꾼다(KRX:005930, TSE:8035, HKEX:1548 …).
2. 실적 (SEC EDGAR): 티커 → CIK(company_tickers.json), CIK 마다 제출 목록(submissions)에서 최근 EARN_DAYS 일의
   8-K 가운데 Item 2.02(Results of Operations and Financial Condition)가 든 것. 접수 시각(acceptanceDateTime, UTC)을
   뉴욕 시각으로 바꿔 적는다. SEC 는 연락처가 든 User-Agent 를 요구하므로 SEC_USER_AGENT 환경 변수가 없으면 건너뛰고
   이전 파일을 그대로 둔다. 요청은 초당 10건 한도보다 느리게(SEC_PAUSE 간격) 보낸다.
3. 헤드라인 (Finnhub): 미국 상장 종목마다 최근 NEWS_DAYS 일의 company-news. FINNHUB_TOKEN 환경 변수가 없으면 건너뛴다.
   무료 등급의 분당 60건 한도를 지키려고 FH_PAUSE 간격으로 보내고, 429 를 받으면 60초 쉬고 다시 시도한다.
   다가오는 실적일은 earnings calendar(오늘부터 CAL_DAYS 일)를 한 번에 받아 대상 종목만 남긴다.

출력 (output/news/)
  earnings.json     종목별 tv(TradingView 심볼), cik, e[[뉴욕 접수 시각 'YYYY-MM-DDTHH:MM', 8-K 주소]] (최근 것부터)
  calendar.json     종목별 다음 실적일 [날짜, bmo|amc|dmh|'']
  headlines.json    종목별 h[[unix 초, 제목, 출처, 주소]] (최근 것부터 KEEP_NEWS 개). 저장소에는 올리지 않는다(.gitignore)
  각 파일의 meta: generated_at, status(ok·partial·skipped·failed), failed(실패한 종목과 사유)
받지 못한 부분은 이전 파일을 그대로 두므로, 계산(compute_news.py)은 그날 받은 것과 이전 것을 함께 쓴다.

비밀 값(SEC_USER_AGENT, FINNHUB_TOKEN)은 출력·로그·파일 어디에도 남기지 않는다. Finnhub 토큰은 X-Finnhub-Token 헤더로만 보낸다.
표준 라이브러리만 쓴다. 종료 코드: 0 정상, 2 일부 실패 또는 비밀 값 없음(받은 만큼 씀), 1 스냅샷을 읽지 못함.
"""

import argparse
import csv
import datetime as dt
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "breakout"))
from fetch_stock_prices import US_EXCHANGES, parse_symbol  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001  (tzdata 가 없는 환경: 동부 표준시로 근사)
    NY = dt.timezone(dt.timedelta(hours=-5))

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
FINNHUB = "https://finnhub.io/api/v1"
NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
UA = {"User-Agent": "Mozilla/5.0 (rrg-news-fetcher)"}

NEWS_DAYS = 7
EARN_DAYS = 400
CAL_DAYS = 35
KEEP_NEWS = 8
SEC_PAUSE = 0.15
FH_PAUSE = 1.05
NON_STOCK = re.compile(r"TRS|^B\.0|^XBOX$|CASH|^\s*$", re.I)
# 구성종목 표의 거래소 접두어 → TradingView 거래소
TV_EXCH = {"KRX": "KRX", "KOSDAQ": "KRX", "TSE": "TSE", "TYO": "TSE", "TWSE": "TWSE", "TPE": "TWSE", "HKG": "HKEX", "HKEX": "HKEX",
           "LON": "LSE", "LSE": "LSE", "ASX": "ASX", "TSX": "TSX", "TSXV": "TSXV", "CVE": "TSXV", "JSE": "JSE", "SSE": "SSE",
           "SHA": "SSE", "SZSE": "SZSE", "SHE": "SZSE", "NEO": "NEO", "XETRA": "XETR", "ETR": "XETR", "EPA": "EURONEXT",
           "AMS": "EURONEXT", "BIT": "MIL", "SIX": "SIX", "SWX": "SIX", "NSE": "NSE", "BOM": "BSE"}
# NASDAQ Trader otherlisted.txt 의 거래소 기호 → TradingView 거래소 (NYSE Arca 도 TradingView 에서는 AMEX)
OTHER_EXCH = {"N": "NYSE", "A": "AMEX", "P": "AMEX", "Z": "CBOE"}
# SEC 제출 목록의 exchanges 값 → TradingView 거래소
SEC_EXCH = {"NASDAQ": "NASDAQ", "NYSE": "NYSE", "NYSE AMERICAN": "AMEX", "NYSE MKT": "AMEX", "NYSE ARCA": "AMEX", "CBOE": "CBOE", "BATS": "CBOE"}


class FetchError(Exception):
    """요청 실패. 메시지에는 상태 코드나 예외 이름만 담는다(주소·헤더는 담지 않는다)."""


def get(url, headers, retries=3, timeout=40, wait429=60):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            code = e.code
            if code == 429 and attempt < retries - 1:
                time.sleep(wait429)
                continue
            if code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise FetchError(f"HTTP {code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException, OSError) as e:
            if attempt == retries - 1:
                raise FetchError(type(e).__name__) from None
            time.sleep(2 ** attempt)
    raise FetchError("no response")


def norm(sym):
    """BRK.B · BRK-B · BRK/B 를 같은 값으로 본다."""
    return re.sub(r"[^A-Z0-9]", "", (sym or "").upper())


def load_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


# ---------- 대상 ----------
def universe(hdir, meta):
    files = sorted(f for f in os.listdir(hdir) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.csv", f))
    if not files:
        raise SystemExit(f"no holdings snapshot in {hdir}")
    rows = list(csv.DictReader(open(os.path.join(hdir, files[-1]), encoding="utf-8-sig")))
    funds = {t for t, m in meta.items() if "terminal" in m.get("screens", [])}
    out = {}
    for r in rows:
        if r.get("etf") not in funds:
            continue
        try:
            if int(r.get("rank") or 99) > 10:
                continue
        except ValueError:
            continue
        exch, code = parse_symbol(r.get("symbol"))
        if not code or NON_STOCK.search(code):
            continue
        key = code.upper()
        st = out.setdefault(key, {"code": code, "exch": exch, "name": r.get("name") or "", "etfs": [], "us": exch in US_EXCHANGES})
        if r["etf"] not in st["etfs"]:
            st["etfs"].append(r["etf"])
    return files[-1][:-4], out


# ---------- TradingView 심볼 ----------
def nasdaq_listing():
    """{정규화 심볼: TradingView 거래소}. 시험 종목은 뺀다."""
    out = {}
    txt = get(NASDAQ_LISTED, UA).decode("utf-8", "replace").splitlines()
    for line in txt[1:]:
        f = line.split("|")
        if len(f) < 4 or line.startswith("File Creation") or f[3].strip() == "Y":
            continue
        out[norm(f[0])] = "NASDAQ"
    time.sleep(0.3)
    txt = get(OTHER_LISTED, UA).decode("utf-8", "replace").splitlines()
    for line in txt[1:]:
        f = line.split("|")
        if len(f) < 8 or line.startswith("File Creation") or f[6].strip() == "Y":
            continue
        ex = OTHER_EXCH.get(f[2].strip())
        if not ex:
            continue
        for s in (f[0], f[3], f[7]):
            if s.strip():
                out.setdefault(norm(s), ex)
    return out


def tv_symbol(st, listing, sec_exch=None):
    code = st["code"]
    if st["us"]:
        ex = listing.get(norm(code)) or sec_exch
        return f"{ex}:{code}" if ex else code
    ex = TV_EXCH.get(st["exch"])
    if not ex:
        return None
    if ex == "HKEX":
        code = code.lstrip("0") or code
    return f"{ex}:{code}"


# ---------- SEC 8-K Item 2.02 ----------
def sec_earnings(stocks, ua, today, failed):
    """{key: {cik, e, exch}}. 8-K 접수 시각은 UTC 로 오므로 뉴욕 시각으로 바꾼다."""
    headers = {"User-Agent": ua, "Accept": "application/json"}
    tick = json.loads(get(SEC_TICKERS, headers))
    cik_of = {norm(v.get("ticker")): int(v["cik_str"]) for v in tick.values() if v.get("ticker") and v.get("cik_str")}
    by_cik = {}
    for key, st in stocks.items():
        if not st["us"]:
            continue
        cik = cik_of.get(norm(st["code"]))
        if cik:
            by_cik.setdefault(cik, []).append(key)
        else:
            failed[key] = "no CIK (not an SEC filer under this ticker)"
    out = {}
    for n, (cik, keys) in enumerate(sorted(by_cik.items()), 1):
        time.sleep(SEC_PAUSE)
        try:
            sub = json.loads(get(SEC_SUBMISSIONS.format(cik=cik), headers))
        except (FetchError, ValueError) as e:
            for k in keys:
                failed[k] = f"submissions: {e}"
            print(f"sec {n:>3}/{len(by_cik)} CIK {cik:<8} {','.join(keys):<12} FAILED  {e}")
            continue
        rec = (sub.get("filings") or {}).get("recent") or {}
        col = lambda name: rec.get(name) or []  # noqa: E731
        forms, items, accepted, accs, docs = col("form"), col("items"), col("acceptanceDateTime"), col("accessionNumber"), col("primaryDocument")
        events, seen = [], set()
        for i, form in enumerate(forms):
            if form != "8-K" or i >= len(items) or i >= len(accepted) or i >= len(accs):
                continue
            if "2.02" not in [x.strip() for x in (items[i] or "").split(",")]:
                continue
            try:
                t = dt.datetime.strptime(accepted[i][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).astimezone(NY)
            except ValueError:
                continue
            if (today - t.date()).days > EARN_DAYS or t.date() in seen:
                continue
            seen.add(t.date())
            doc = docs[i] if i < len(docs) else ""
            url = SEC_ARCHIVE.format(cik=cik, acc=accs[i].replace("-", ""), doc=doc) if doc else \
                f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K"
            events.append([t.strftime("%Y-%m-%dT%H:%M"), url])
        events.sort(reverse=True)
        tickers = [norm(x) for x in (sub.get("tickers") or [])]
        exchanges = sub.get("exchanges") or []
        for k in keys:
            ex = None
            if norm(stocks[k]["code"]) in tickers:
                j = tickers.index(norm(stocks[k]["code"]))
                ex = SEC_EXCH.get(str(exchanges[j] if j < len(exchanges) else "").upper())
            out[k] = {"cik": cik, "e": events, "secExch": ex}
        print(f"sec {n:>3}/{len(by_cik)} CIK {cik:<8} {','.join(keys):<12} 8-K 2.02 x{len(events)}" + (f" last {events[0][0]}" if events else ""))
    return out


# ---------- Finnhub ----------
def finnhub(path, params, token):
    url = f"{FINNHUB}{path}?{urllib.parse.urlencode(params)}"
    return json.loads(get(url, {"X-Finnhub-Token": token, **UA}, retries=3, wait429=60))


def clean_news(items, since_ts):
    out, seen = [], set()
    for it in sorted(items or [], key=lambda x: -(x.get("datetime") or 0)):
        h = re.sub(r"\s+", " ", str(it.get("headline") or "")).strip()
        u = str(it.get("url") or "")
        ts = int(it.get("datetime") or 0)
        if not h or not u.startswith(("https://", "http://")) or ts < since_ts:
            continue
        k = re.sub(r"[^a-z0-9]", "", h.lower())[:80]
        if k in seen:
            continue
        seen.add(k)
        out.append([ts, h[:220], str(it.get("source") or "")[:40], u])
        if len(out) >= KEEP_NEWS:
            break
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "news"))
    ap.add_argument("--limit", type=int, default=0, help="시험용: 앞의 N 종목만")
    args = ap.parse_args()

    started = dt.datetime.now(dt.timezone.utc)
    stamp = started.strftime("%Y-%m-%dT%H:%M:%SZ")
    today = started.astimezone(NY).date()
    try:
        meta = json.load(open(args.meta, encoding="utf-8"))
        snap, stocks = universe(args.holdings, meta)
    except (OSError, ValueError, SystemExit) as e:
        print(f"cannot read the holdings: {e}")
        return 1
    if args.limit:
        stocks = dict(list(stocks.items())[: args.limit])
    us = [k for k, s in stocks.items() if s["us"]]
    print(f"holdings {snap}: {len(stocks)} names ({len(us)} US-listed)")
    partial = False

    # ---- 1. TradingView 거래소
    try:
        listing = nasdaq_listing()
        print(f"nasdaqtrader: {len(listing)} symbols")
    except FetchError as e:
        listing = {}
        partial = True
        print(f"nasdaqtrader FAILED ({e}); falling back to SEC exchanges")

    # ---- 2. SEC 실적
    ep = os.path.join(args.out, "earnings.json")
    prev_file = load_json(ep)
    prev, prev_meta = prev_file.get("stocks", {}), prev_file.get("meta", {})
    ua = os.environ.get("SEC_USER_AGENT", "").strip()
    sec_failed, sec = {}, {}
    if not ua:
        sec_status = "skipped"
        partial = True
        print("SEC_USER_AGENT is not set: SEC step skipped (previous earnings file kept)")
    else:
        try:
            sec = sec_earnings(stocks, ua, today, sec_failed)
            sec_status = "partial" if sec_failed else "ok"
        except (FetchError, ValueError) as e:
            sec_status = "failed"
            sec_failed["*"] = f"company_tickers: {e}"
            print(f"SEC FAILED ({e}); previous earnings file kept")
        partial = partial or sec_status != "ok"
    earn = {}
    for k, st in stocks.items():
        got = sec.get(k)
        old = prev.get(k, {})
        rec = {"tv": tv_symbol(st, listing, (got or {}).get("secExch") or old.get("secExch")), "name": st["name"], "etfs": st["etfs"]}
        if got:
            rec.update({"cik": got["cik"], "e": got["e"], "secExch": got["secExch"]})
        elif old.get("e") is not None:
            rec.update({k2: old[k2] for k2 in ("cik", "e", "secExch") if k2 in old})
        earn[k] = rec
    write_json(ep, {"meta": {"generated_at": stamp, "snapshot": snap, "status": sec_status, "failed": sec_failed,
                             "sec_generated_at": stamp if sec else prev_meta.get("sec_generated_at")},
                    "stocks": earn})

    # ---- 3. Finnhub 헤드라인·실적 일정
    token = os.environ.get("FINNHUB_TOKEN", "").strip()
    cp, hp = os.path.join(args.out, "calendar.json"), os.path.join(args.out, "headlines.json")
    if not token:
        partial = True
        print("FINNHUB_TOKEN is not set: headlines and the earnings calendar skipped")
        return 2 if partial else 0
    since = started - dt.timedelta(days=NEWS_DAYS)
    cal, cal_status, cal_failed = {}, "ok", {}
    try:
        res = finnhub("/calendar/earnings", {"from": today.isoformat(), "to": (today + dt.timedelta(days=CAL_DAYS)).isoformat()}, token)
        want = {norm(stocks[k]["code"]): k for k in us}
        for row in sorted(res.get("earningsCalendar") or [], key=lambda r: str(r.get("date") or "")):
            k = want.get(norm(row.get("symbol")))
            if k and k not in cal and str(row.get("date") or "") >= today.isoformat():
                cal[k] = [row["date"], str(row.get("hour") or "")]
        print(f"finnhub calendar: {len(cal)} of {len(us)} names report within {CAL_DAYS} days")
    except (FetchError, ValueError, KeyError) as e:
        cal_status, cal_failed["*"] = "failed", str(e)
        partial = True
        print(f"finnhub calendar FAILED ({e})")
    if cal_status == "ok":
        write_json(cp, {"meta": {"generated_at": stamp, "from": today.isoformat(), "days": CAL_DAYS, "status": cal_status}, "stocks": cal})
    news, news_failed, stop, denied = {}, {}, None, 0
    for i, k in enumerate(us, 1):
        if stop:
            news_failed[k] = stop
            continue
        time.sleep(FH_PAUSE)
        try:
            items = finnhub("/company-news", {"symbol": stocks[k]["code"], "from": since.date().isoformat(), "to": today.isoformat()}, token)
            news[k] = clean_news(items if isinstance(items, list) else [], int(since.timestamp()))
            denied = 0
        except (FetchError, ValueError) as e:
            news_failed[k] = str(e)
            denied = denied + 1 if str(e) in ("HTTP 401", "HTTP 403") else 0
            if str(e) == "HTTP 401" or denied >= 3:
                stop = f"stopped after {e}"   # 토큰이 틀렸거나 권한이 없으면 나머지 종목도 같다
            print(f"news {i:>3}/{len(us)} {k:<8} FAILED  {e}")
            continue
        if i % 25 == 0 or i == len(us):
            print(f"news {i:>3}/{len(us)} done ({sum(len(v) for v in news.values())} headlines)")
    if news:
        write_json(hp, {"meta": {"generated_at": stamp, "days": NEWS_DAYS, "status": "partial" if news_failed else "ok", "failed": news_failed}, "stocks": news})
    partial = partial or bool(news_failed)
    print(f"done: earnings {sec_status}, calendar {cal_status}, headlines {len(news)}/{len(us)} names")
    return 2 if partial else 0


if __name__ == "__main__":
    sys.exit(main())
