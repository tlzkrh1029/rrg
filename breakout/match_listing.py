#!/usr/bin/env python3
"""세 거래소 상장 목록(tradfi-ticker-lists 의 output)을 돌파 지도의 종목·ETF 에 대조한다.

터미널(terminal/build_terminal.py)은 "bph" 같은 글자만 쓰지만, 돌파 지도는 종목 상세 패널에
거래소 원본 심볼(NVDABUSDT, NVDAUSDT, xyz:NVDA)과 확인 시각을 적으므로 심볼 목록까지 모은다.

대조 규칙(터미널과 같다)
  미국 종목은 티커 그대로(BRK.B 는 그대로), 비미국 종목은 "거래소:코드" 로 맞춘다.
  거래소 접두어는 holdings 표기(KRX, HKG, SSE, TSE, TSX, LON, ASX …)를 목록 표기(KRX, HKEX, SSE, TSE …)로 바꾸고,
  목록 쪽은 build_terminal.norm_listing 으로 같은 꼴("KRX:005930", "HKEX:0100")로 만든다.
  미국 밖 거래소에만 표기된 종목이 미국에도 상장되어 있으면 DUAL 로 미국 티커를 덧붙여 찾는다(TSX:ABX → B).

결과(json): {asof, counts: {b, p, h}, stocks: {key: {b: [...], p: [...], h: [...]}}, swaps: {}, etfs: {ETF: {...}}}
  stocks 에는 세 목록 중 하나라도 있는 종목만 들어간다. swaps 는 스왑 행이 남아 있는 스냅샷을 위한 자리다.
"""

import argparse
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "terminal"))
sys.path.insert(0, HERE)
from build_terminal import EXCH_MAP, SWAP_RE, SWAP_UNDERLYING, norm_listing  # noqa: E402
from fetch_stock_prices import latest_holdings, universe  # noqa: E402

# holdings 의 접두어 → 목록 접두어. EXCH_MAP 에 없는 것은 그대로 쓴다(목록에 그 거래소가 없으면 어차피 빈다).
EXCH = dict(EXCH_MAP, TYO="TSE", TPE="TWSE", SHA="SSE", KOSDAQ="KRX")
# 미국 밖 표기 → 미국 상장 티커(ADR·이중 상장). 목록은 미국 티커로 적혀 있다.
DUAL = {
    "TSX:ABX": "B", "TSX:AEM": "AEM", "TSX:WPM": "WPM", "TSX:FNV": "FNV", "TSX:K": "KGC", "TSX:PAAS": "PAAS",
    "TSX:AGI": "AGI", "TSX:OR": "OR", "TSX:ELD": "EGO", "TSX:BTO": "BTG", "TSX:IMG": "IAG", "TSX:SSRM": "SSRM",
    "TSX:NXE": "NXE", "TSX:CCO": "CCJ", "TSX:DML": "DNN", "TSX:EFR": "UUUU", "TSX:CDE": "CDE", "TSX:AC": "ACDVF",
    "TSX:SHOP": "SHOP", "TSX:TECK": "TECK", "TSX:FM": "FQVLF", "TSX:IVN": "IVPAF",
    "JSE:GFI": "GFI", "JSE:HAR": "HMY", "JSE:ANG": "AU", "JSE:SSW": "SBSW", "JSE:DRD": "DRD",
    "LON:KAP": "KAP", "LON:NG": "NGG", "LON:SHEL": "SHEL", "LON:BP": "BP", "LON:RIO": "RIO", "LON:AAL": "NGLOY",
    "ASX:NST": "NST", "ASX:PDN": "PALAF", "ASX:BHP": "BHP", "ASX:EVN": "CAHPF",
    "SWX:ABBN": "ABBNY", "EPA:SU": "SBGSY", "ETR:EOAN": "EONGY", "BIT:PRY": "PRYMY", "BME:SLR": "SLR",
    "TLV:ENLT": "ENLT", "TLV:DORL": "DORL", "HKG:3800": "GCPEF", "TYO:8001": "ITOCY",
    "NEO:CCO": "CCJ", "NEO:EFR": "UUUU", "NEO:CDE": "CDE",
}


def list_key(exch, code):
    """우리 유니버스의 (거래소, 코드) → 목록 대조 키."""
    if not exch:
        return code.upper()
    ex = EXCH.get(exch, exch)
    c = code.upper()
    if ex == "HKEX" and c.isdigit():
        c = c.zfill(4)
    return f"{ex}:{c}"


def load_lists(lists_dir):
    """세 목록을 {대조 키: [거래소 심볼]} 사전 셋으로 읽는다. 없으면 (None, None, None, counts, None)."""
    B, P, H = {}, {}, {}
    counts = {"b": 0, "p": 0, "h": 0}
    ts = None
    try:
        for r in csv.DictReader(open(os.path.join(lists_dir, "binance_spot_bstocks.csv"), encoding="utf-8-sig")):
            if r.get("status") != "TRADING":
                continue
            counts["b"] += 1
            B.setdefault((r.get("us_listing") or r.get("underlying") or "").strip().upper(), []).append(r["symbol"])
        for r in csv.DictReader(open(os.path.join(lists_dir, "binance_futures_tradfi.csv"), encoding="utf-8-sig")):
            if r.get("status") not in ("TRADING", "PENDING_TRADING"):
                continue
            counts["p"] += 1
            k = norm_listing(r.get("listing")) or (r.get("underlying") or "").strip().upper()
            if k:
                P.setdefault(k, []).append(r["symbol"])
        for r in csv.DictReader(open(os.path.join(lists_dir, "hyperliquid_hip3_stocks.csv"), encoding="utf-8-sig")):
            if r.get("delisted") == "True":
                continue
            counts["h"] += 1
            k = norm_listing(r.get("reference_listing")) or (r.get("ticker") or r["coin"].split(":")[-1]).strip().upper()
            if k:
                H.setdefault(k, []).append(r["coin"])
        sp = os.path.join(lists_dir, "summary.json")
        if os.path.exists(sp):
            ts = json.load(open(sp, encoding="utf-8")).get("generated_at")
    except FileNotFoundError as e:
        print("listing lists not found:", e)
        return None, None, None, counts, None
    return B, P, H, counts, ts


def found(keys, B, P, H):
    f = {"b": [], "p": [], "h": []}
    for k in keys:
        for v in B.get(k, []):
            if v not in f["b"]:
                f["b"].append(v)
        for v in P.get(k, []):
            if v not in f["p"]:
                f["p"].append(v)
        for v in H.get(k, []):
            if v not in f["h"]:
                f["h"].append(v)
    return f if any(f.values()) else None


def match(stocks, rows_of, etfs, lists_dir):
    """stocks/rows_of/etfs 는 fetch_stock_prices.universe 의 결과."""
    B, P, H, counts, ts = load_lists(lists_dir) if lists_dir else (None, None, None, {"b": 0, "p": 0, "h": 0}, None)
    out = {"asof": ts or "", "counts": counts, "stocks": {}, "swaps": {}, "etfs": {}}
    if B is None:
        return out
    for key, st in stocks.items():
        lk = list_key(st["exch"], st["code"])
        cands = [lk]
        if lk in DUAL:
            cands.append(DUAL[lk])
        if st["exch"] == "" and st["code"] == "ABX":    # 바릭: NYSE 티커가 GOLD → B 로 바뀌었다
            cands += ["B", "GOLD"]
        f = found(cands, B, P, H)
        if f:
            out["stocks"][key] = f
    for t, rows in rows_of.items():
        for key, sym, _name, _w in rows:
            if key is None and SWAP_RE.search(sym or ""):
                u = SWAP_UNDERLYING.get(sym)
                if u:
                    f = found([u], B, P, H)
                    if f:
                        out["swaps"][sym] = f
    for t in etfs:
        f = found([t], B, P, H)
        if f:
            out["etfs"][t] = f
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lists", default=os.path.join(ROOT, "data", "lists"))
    ap.add_argument("--holdings", default=os.path.join(ROOT, "output", "holdings"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "data", "etf_meta.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "output", "stocks", "listing.json"))
    args = ap.parse_args()
    _date, hrows = latest_holdings(args.holdings)
    meta = json.load(open(args.meta, encoding="utf-8"))
    etfs, stocks, rows_of = universe(meta, hrows)
    res = match(stocks, rows_of, etfs, args.lists)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"lists {res['asof'] or '(none)'} counts {res['counts']}: stocks listed {len(res['stocks'])}/{len(stocks)}, ETFs {sorted(res['etfs'])}")
    for t in etfs:
        b = p = h = 0
        for key, *_ in rows_of[t]:
            f = res["stocks"].get(key) if key else None
            if f:
                b += bool(f["b"]); p += bool(f["p"]); h += bool(f["h"])
        print(f"  {t:<5} b{b} p{p} h{h} / {len(rows_of[t])}")
    miss = [k for k, s in stocks.items() if s["exch"] and k not in res["stocks"]]
    print("non-US keys without a listing:", ", ".join(miss) or "none")


if __name__ == "__main__":
    main()
